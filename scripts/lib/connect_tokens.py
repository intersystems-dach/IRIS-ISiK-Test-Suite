#!/usr/bin/env python3
"""Install and verify the ISiK Stufe 5 `Connect` bearer tokens.

Called by scripts/55-mint-tokens.sh, which performs the HTTP token requests and
leaves one token per file in a directory; everything that inspects a token or
writes a file lives here.

Two subcommands, sharing one validator so that `make tokens` and the check run
immediately before a Connect run cannot disagree about what a usable token is:

  install   validate the freshly minted tokens, write them into the staged
            test-data contract, and write the evidence record
  verify    re-validate what is in the staged contract now

WHAT IS AND IS NOT CHECKED. The token's signature is NOT verified here. The
system under test verifies it - that is the property under test - and a
signature check in the harness would only assert that Keycloak signs with the
key it published. What is checked is everything that would otherwise turn into a
conformance finding against the SUT when the fault is ours:

  - the value is a token at all, and not the placeholder the official test-data
    file ships (`SET-YOUR-TOKEN-HERE`)
  - `scope` is exactly the one scope the feature file for that key exercises. A
    token carrying two scopes would make a refusal scenario pass or fail for the
    wrong reason.
  - `aud` contains the endpoint the suite addresses. IRIS rejects a token whose
    audience it was not configured for, with a 401 that looks exactly like a
    rejected signature.
  - `iss` is the issuer IRIS discovered. Keycloak derives the issuer from the
    request host unless pinned, so a token minted through the published port
    could carry an issuer the SUT has never heard of.
  - the token still has usable life left. An expired token produces a 401 on
    every scenario of the module, which reads as a total authorization failure
    in the system under test.
  - a `patient/...` scope carries the `patient` launch-context claim, and its
    value is the patient id the suite's own test data declares as the patient in
    context. Without it the scenarios that assert patient-context filtering
    cannot mean anything.

TOKENS ARE CREDENTIALS. No token value is printed, logged or written into the
evidence record; the record carries the SHA-256 of each token instead, which is
enough to prove that a given archive and a given run used the same token.
"""

import argparse
import base64
import datetime
import hashlib
import json
import pathlib
import re
import sys
from dataclasses import dataclass

import yaml

# Invoked as a standalone script, so sys.path[0] is already this directory;
# the insert is for the case where a sibling imports this module instead.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from tool_common import fail  # noqa: E402

PLACEHOLDER = "SET-YOUR-TOKEN-HERE"

# The key in the official test-data contract that names the patient the
# patient-context tokens must be bound to.
PATIENT_IN_CONTEXT_KEY = "read-patient-in-context-id"


def read_map(stream):
    """key|client|scope records, as scripts/lib.sh connect_token_clients() emits them."""
    entries = []
    for line in stream:
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) != 3:
            fail(f"malformed token map line (want key|client|scope): {line!r}")
        entries.append({"key": parts[0], "client": parts[1], "scope": parts[2]})
    if not entries:
        fail("the token map on stdin was empty - see connect_token_clients() in scripts/lib.sh")
    return entries


def claims(token):
    """The payload of a JWS compact serialisation, without verifying the signature."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    body = parts[1]
    try:
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        return json.loads(raw)
    except Exception:
        return None


def audiences(payload):
    aud = payload.get("aud")
    if aud is None:
        return []
    if isinstance(aud, str):
        return [aud]
    return [str(a) for a in aud]


@dataclass(frozen=True)
class StagedToken:
    """One of the module's tokens as it was found, and which entry it is for.

    `entry` names the key, the client and the single scope the token must carry;
    `value` is the JWS as staged, which `cmd_install` reads from the minting
    directory and `cmd_verify` reads back out of the contract file. Neither half
    means anything without the other, so they are one value.
    """

    entry: dict
    value: str


@dataclass(frozen=True)
class TokenExpectations:
    """What a token must say for a Connect round to be able to use it.

    All five are fixed for one invocation: the audience and issuer the SUT is
    configured for, the life a run needs, the patient the suite's own test data
    puts in context, and the clock reading every expiry is measured against.
    One clock reading for the whole check, not one per token, so that two
    tokens minted in the same second cannot be reported with different lives.
    """

    audience: str
    issuer: str
    min_remaining: int
    patient: str
    now: int

    def check(self, staged: StagedToken):
        """Returns (record, problems). record is None when the token is unusable."""
        entry, token = staged.entry, staged.value
        key = entry["key"]
        problems = []

        if not token:
            return None, [f"{key}: no value - run 'make tokens'"]
        if token == PLACEHOLDER:
            return None, [
                f"{key}: still carries the official file's placeholder "
                f"{PLACEHOLDER!r}. The suite would send it verbatim as a bearer "
                "token and every scenario "
                "of the module would fail on authorization. Run 'make tokens'."
            ]

        payload = claims(token)
        if payload is None:
            return None, [f"{key}: the value is not a decodable JWT"]

        scope = payload.get("scope", "")
        if scope.split() != [entry["scope"]]:
            problems.append(
                f"{key}: scope is {scope!r}, expected exactly {entry['scope']!r} "
                f"(client {entry['client']})"
            )

        if self.audience not in audiences(payload):
            problems.append(
                f"{key}: aud is {audiences(payload)!r} and does not contain "
                f"{self.audience!r}, which is the endpoint the suite addresses "
                "and the audience IRIS is configured to accept"
            )

        issuer = payload.get("iss", "")
        if issuer != self.issuer:
            problems.append(
                f"{key}: iss is {issuer!r}, expected {self.issuer!r} - the issuer the "
                "SUT discovered. Check KC_HOSTNAME on the authserver service."
            )

        exp = payload.get("exp")
        remaining = None
        if not isinstance(exp, (int, float)):
            problems.append(f"{key}: no usable exp claim")
        else:
            remaining = int(exp - self.now)
            if remaining < self.min_remaining:
                problems.append(
                    f"{key}: {remaining}s of life left, less than the "
                    f"{self.min_remaining}s a run needs. Re-mint with "
                    "'make tokens' immediately before the run."
                )

        patient = payload.get("patient")
        if entry["scope"].startswith("patient/"):
            if patient != self.patient:
                problems.append(
                    f"{key}: patient launch-context claim is {patient!r}, "
                    f"expected {self.patient!r} - the value "
                    f"{PATIENT_IN_CONTEXT_KEY} carries in the suite's own test "
                    "data. The realm and the test data have drifted apart."
                )

        record = {
            "key": key,
            "client": entry["client"],
            "scope": entry["scope"],
            "sha256": hashlib.sha256(token.encode("ascii", "strict")).hexdigest(),
            "iat": payload.get("iat"),
            "exp": exp,
            "remaining": remaining,
            "patient": patient,
            "issuer": issuer,
            "audiences": audiences(payload),
        }
        return record, problems


def load_yaml(path):
    if not path.is_file():
        fail(
            f"{path} does not exist. It is staged by scripts/30-render-config.sh "
            "from the official test-data contract - run 'make up' first."
        )
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        fail(f"{path}: expected a flat key -> value mapping")
    return data


def set_key(text, key, value):
    """Replace one key's value in place, preserving every other byte of the file.

    Deliberately textual rather than a yaml.safe_dump round trip: this file is
    the official contract and its parity against the released version is
    reported in run/tiger/TESTDATA-PARITY.txt. A round trip would reorder the
    keys and drop upstream's comments, which would turn a six-line difference
    into an unreadable one.
    """
    pattern = re.compile(r"^(\s*%s\s*:[ \t]*)(.*)$" % re.escape(key), re.MULTILINE)
    if not pattern.search(text):
        fail(f"no line defining {key!r} in the staged contract file")
    # A JWS compact serialisation is base64url plus two dots, so it is a safe
    # unquoted YAML scalar; the result is re-parsed below regardless.
    return pattern.sub(lambda m: m.group(1) + value, text, count=1)


def cmd_install(args, entries, now):
    token_dir = pathlib.Path(args.token_dir)
    yaml_path = pathlib.Path(args.yaml)
    data = load_yaml(yaml_path)

    want_patient = data.get(PATIENT_IN_CONTEXT_KEY)
    if not want_patient:
        fail(
            f"{yaml_path} defines no {PATIENT_IN_CONTEXT_KEY}; the patient-context "
            "tokens cannot be checked against the test data"
        )

    expected = TokenExpectations(audience=args.audience, issuer=args.issuer,
                                 min_remaining=args.min_remaining,
                                 patient=want_patient, now=now)

    tokens = {}
    records = []
    problems = []
    for entry in entries:
        path = token_dir / entry["key"]
        if not path.is_file():
            problems.append(f"{entry['key']}: no token was minted (missing {path})")
            continue
        token = path.read_text(encoding="utf-8").strip()
        record, errs = expected.check(StagedToken(entry, token))
        problems.extend(errs)
        if record is not None:
            records.append(record)
            tokens[entry["key"]] = token

    if problems:
        fail("the minted tokens are not usable:\n  " + "\n  ".join(problems))

    text = yaml_path.read_text(encoding="utf-8")
    for key, token in tokens.items():
        text = set_key(text, key, token)
    yaml_path.write_text(text, encoding="utf-8")

    # Re-read what was written: the suite reads this file, so the question is
    # what is on disk, not what was intended.
    written = load_yaml(yaml_path)
    for key, token in tokens.items():
        if written.get(key) != token:
            fail(
                f"{yaml_path}: {key} does not read back as the token that was written. "
                "Do not run the Connect module against this file."
            )

    # 0644 and not 0600, and that is forced by the consumer: this file is
    # bind-mounted into the test suite container, which runs as its own
    # unprivileged uid 100. A 0600 file owned by the host user is unreadable
    # there, and Tiger fails at startup with an AccessDeniedException. The same
    # constraint applies to the rendered Tiger configuration, which carries the
    # API password for the same reason - see render() in 30-render-config.sh.
    # run/ is gitignored and `make check` verifies that mechanically.
    yaml_path.chmod(0o644)

    write_record(pathlib.Path(args.record), records, args, now)

    if args.parity:
        update_parity(
            pathlib.Path(args.parity),
            yaml_path,
            pathlib.Path(args.upstream) if args.upstream else None,
            sorted(tokens),
        )

    print(f"  {len(records)} token(s) installed into {yaml_path}")
    for rec in records:
        print(
            "    %-56s %-22s %4dm left  sha256:%s"
            % (rec["key"], rec["scope"], (rec["remaining"] or 0) // 60, rec["sha256"][:16])
        )


# The marker that delimits this script's addition to the parity record. Written
# so the block can be replaced rather than appended to: `make tokens` runs again
# whenever an hour has passed, and a record that grew a section per minting would
# stop being a statement about the current file.
PARITY_MARKER = "# --- Parameterised keys (written by scripts/55-mint-tokens.sh) ---"


def update_parity(parity_path, staged, upstream, keys):
    """Re-state the staged contract file's row in TESTDATA-PARITY.txt.

    30-render-config.sh writes that record before any token exists, so it
    reports this file as IDENTICAL - which stops being true the moment the
    tokens are staged into it. Left alone, the round's own evidence would claim
    byte-identity for a file that differs from the release in six places.

    PARAMETERISED is that third state, and it is a CHECKED claim, not a label:
    every key of the released file must still be present with the released
    value, except exactly the keys named here, whose released value must have
    been the empty placeholder. Anything else is a modification of the official
    contract and stops the round - the same consequence MODIFIED has in
    scripts/30-render-config.sh, for the same reason.
    """
    rel = f"{staged.parent.name}/{staged.name}"
    digest = hashlib.sha256(staged.read_bytes()).hexdigest()
    allow = set(keys)

    verified = False
    detail = []
    if upstream is not None and upstream.is_file():
        staged_data = load_yaml(staged)
        up_data = load_yaml(upstream)
        problems = []
        for key in sorted(set(up_data) | set(staged_data)):
            if key not in up_data:
                problems.append(f"{key}: staged but absent from the released file")
            elif key not in staged_data:
                problems.append(f"{key}: in the released file but not staged")
            elif staged_data[key] != up_data[key] and key not in allow:
                problems.append(
                    f"{key}: the staged value differs from the released one and it is "
                    "not one of the token keys"
                )
        for key in sorted(allow):
            released = up_data.get(key)
            if released not in (None, "", PLACEHOLDER):
                problems.append(
                    f"{key}: the released file defines the value {released!r}; "
                    "parameterising it would replace official content"
                )
        if problems:
            fail(
                "the staged test-data contract no longer matches the official release "
                "except in the token keys:\n  " + "\n  ".join(problems)
            )
        verified = True
        detail = [
            "# Every other key of the released file is present with the released value;",
            "# each key below was empty or %r upstream." % PLACEHOLDER,
        ]
    else:
        detail = [
            "# NOT COMPARED key by key: the released copy of this file was not available",
            "# (vendor/upstream/testdata is written by scripts/20-fetch-packages.sh).",
            "# The status is still PARAMETERISED rather than IDENTICAL, because the file",
            "# on disk demonstrably differs from the release - this script wrote to it.",
        ]

    if not parity_path.is_file():
        # Nothing to re-state. Not fatal: the tokens are staged and usable, and the
        # absence of the record is itself reported by 30-render-config.sh.
        print(f"  no parity record at {parity_path} - the row was not re-stated")
        return

    text = parity_path.read_text(encoding="utf-8")
    text = text.split(PARITY_MARKER)[0].rstrip("\n") + "\n"

    row = re.compile(r"^  \S+(\s+)%s(\s+)\S+$" % re.escape(rel), re.MULTILINE)
    if row.search(text):
        text = row.sub("  %-13s %-46s %s" % ("PARAMETERISED", rel, digest), text, count=1)
    else:
        print(f"  {rel} has no row in {parity_path.name} - one was appended")
        text += "  %-13s %-46s %s\n" % ("PARAMETERISED", rel, digest)

    lines = [
        "",
        PARITY_MARKER,
        f"# {rel} carries this round's bearer tokens, staged at "
        + datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        + ".",
    ] + detail + [
        "#",
        "# The values are credentials and are not reproduced here; run/tiger/TOKENS.txt",
        "# carries the SHA-256 of each one.",
        "#",
    ]
    for key in sorted(allow):
        lines.append(f"  {key}")
    lines.append("")

    parity_path.write_text(text + "\n".join(lines), encoding="utf-8")
    how = "verified key by key" if verified else "NOT compared key by key"
    print(f"  {rel} re-stated as PARAMETERISED in {parity_path.name} ({how})")


def write_record(path, records, args, now):
    """run/tiger/TOKENS.txt - what was minted, without the tokens themselves."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Bearer tokens of this round's ISiK Stufe 5 `Connect` run",
        "# Written by scripts/55-mint-tokens.sh at "
        + datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "#",
        "# NO TOKEN VALUE APPEARS IN THIS FILE. A bearer token is a credential with",
        "# the full authority of its scope for as long as it lives, so this record",
        "# identifies each one by the SHA-256 of its exact serialisation instead. That",
        "# is enough to prove that an archived run and this record describe the same",
        "# token: re-compute it from the staged contract file with",
        "#   python3 -c 'import hashlib,sys,yaml;"
        " d=yaml.safe_load(open(sys.argv[1]));"
        " print(hashlib.sha256(d[sys.argv[2]].encode()).hexdigest())' \\",
        "#     run/testdata/Stufe5/connect.yaml <key>",
        "#",
        "# The token values themselves are in run/testdata/Stufe5/connect.yaml, which",
        "# is gitignored, and in the traffic recorded inside each run's",
        "# debug-report.zip. They must not be reproduced in any published document;",
        "# see scripts/70-collect-report.sh.",
        "#",
        f"# issuer      {args.issuer}",
        f"# audience    {args.audience}",
        f"# grant       client_credentials",
        "#",
        "# %-54s %-34s %-22s %s" % ("KEY", "CLIENT", "SCOPE", "SHA256"),
    ]
    for rec in records:
        lines.append(
            "  %-54s %-34s %-22s %s" % (rec["key"], rec["client"], rec["scope"], rec["sha256"])
        )
    lines += ["", "# %-54s %-21s %-21s %s" % ("KEY", "ISSUED (UTC)", "EXPIRES (UTC)", "PATIENT CONTEXT")]
    for rec in records:
        def stamp(value):
            if not isinstance(value, (int, float)):
                return "-"
            return datetime.datetime.fromtimestamp(
                value, datetime.timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ")

        lines.append(
            "  %-54s %-21s %-21s %s"
            % (rec["key"], stamp(rec["iat"]), stamp(rec["exp"]), rec["patient"] or "-")
        )
    lines += [
        "",
        "# A token's life is AS_TOKEN_LIFESPAN seconds (see .env.example). The check",
        "# that runs immediately before the Connect run refuses to start it with less",
        "# than %d seconds left, because an expired token presents as a total" % args.min_remaining,
        "# authorization failure in the system under test rather than as our mistake.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    path.chmod(0o644)


def cmd_verify(args, entries, now):
    yaml_path = pathlib.Path(args.yaml)
    data = load_yaml(yaml_path)
    want_patient = data.get(PATIENT_IN_CONTEXT_KEY)
    if not want_patient:
        fail(f"{yaml_path} defines no {PATIENT_IN_CONTEXT_KEY}")

    expected = TokenExpectations(audience=args.audience, issuer=args.issuer,
                                 min_remaining=args.min_remaining,
                                 patient=want_patient, now=now)

    problems = []
    records = []
    for entry in entries:
        staged = StagedToken(entry, str(data.get(entry["key"], "") or "").strip())
        record, errs = expected.check(staged)
        problems.extend(errs)
        if record is not None:
            records.append(record)

    if problems:
        fail(
            "the staged Connect test data is not usable for a run:\n  "
            + "\n  ".join(problems)
            + "\n\n  Re-mint with 'make tokens' and start the run straight after."
            "\n  Note that 'make up' re-renders the configuration and thereby "
            "re-stages this\n  file from the official contract, restoring the "
            "placeholders, so the order is\n  always 'make up', then 'make tokens'."
        )

    for rec in records:
        print(
            "  %-56s %-22s %4dm left"
            % (rec["key"], rec["scope"], (rec["remaining"] or 0) // 60)
        )
    print(f"  {len(records)} token(s) usable")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--yaml", required=True, help="the staged testdata/Stufe5/connect.yaml")
        p.add_argument("--audience", required=True, help="the aud claim every token must carry")
        p.add_argument("--issuer", required=True, help="the iss claim every token must carry")
        p.add_argument("--min-remaining", type=int, default=600,
                       help="seconds of life a token must still have (default 600)")

    p_install = sub.add_parser("install", help="validate minted tokens and stage them")
    common(p_install)
    p_install.add_argument("--token-dir", required=True, help="one file per key, named by key")
    p_install.add_argument("--record", required=True, help="where to write TOKENS.txt")
    p_install.add_argument("--parity", help="run/tiger/TESTDATA-PARITY.txt, to re-state this "
                                            "file's row as PARAMETERISED")
    p_install.add_argument("--upstream", help="the released copy of the same contract file, "
                                             "for the key-by-key comparison")

    p_verify = sub.add_parser("verify", help="re-validate the staged tokens")
    common(p_verify)

    args = parser.parse_args()
    entries = read_map(sys.stdin)
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()

    if args.command == "install":
        cmd_install(args, entries, now)
    else:
        cmd_verify(args, entries, now)


if __name__ == "__main__":
    main()
