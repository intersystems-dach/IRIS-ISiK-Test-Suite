#!/usr/bin/env python3
"""Replace this round's short-lived credentials with a reference to their SHA-256.

Called by scripts/60-run-tests.sh (on the container log, once the run is over)
and by scripts/70-collect-report.sh (on the evidence bundle, before the bundle
manifest is computed, so that the manifest hashes the redacted bytes).

WHAT THIS IS FOR. The rig's own credential policy is that a round's credentials
are published on purpose: the FHIR endpoint's Basic credential and the
Management Portal password are generated per deployment, documented in
run/CREDENTIALS.md, and a human tester re-issuing a request by hand needs them.
See the CREDENTIAL HANDLING note in scripts/70-collect-report.sh.

Two credentials introduced by the `Connect` module do not fit that policy, and
this script is where the difference is implemented:

  the access tokens      Six bearer tokens, each valid for one hour. Published
                         verbatim they would be useless to a reader - expired
                         long before anyone opens the bundle - while still being
                         a credential of the kind the rig otherwise keeps at
                         0600. Replaced here by the test-data key they were
                         staged for and their SHA-256, which is strictly more
                         informative: the reader of an exchange learns WHICH
                         scope's token was presented, which is the question a
                         401 or a 403 raises.

  the realm secrets      AS_CLIENT_SECRET mints fresh tokens for as long as the
                         realm exists, and AS_ADMIN_PASSWORD opens the
                         authorization server's admin console. Neither expires
                         with the round and neither is needed to verify a
                         verdict - a reader re-mints with `make tokens`. They
                         stay in the live 0600 files the operator works from and
                         are withheld from the bundle.

WHAT MAKES A STRING A TOKEN here is the string itself, not a list of the values
this round currently holds: an access token carries `iss` and `exp` and this
realm's also carry `scope`, so access_token_claims() reads it out of the token.
Matching only against the staged values would pass a bundle re-collected after
the tokens were re-minted while its log still carried six credentials that were
live when it was written. Where the round CAN name a value, the marker cites the
test-data key it was staged for; where it cannot, the token's own scope.

WHAT IS PUBLISHED INSTEAD. The SHA-256 of each withheld value, which is enough
to prove that two files name the same credential, and that a given archive and a
given run used the same token, without disclosing either. The token record
run/tiger/TOKENS.txt, published as provenance/tiger-TOKENS.txt, carries the same
hashes together with each token's scope, issuer, audience and lifetime.

WHAT IS DELIBERATELY NOT REDACTED. `<variant>/official/` - the archives the test
suite wrote and their verbatim extraction. Those are the artefact the
Bestaetigungsverfahren is based on, their checksums are the suite's own, and the
recorded traffic in debug-report.zip contains the `Authorization` headers as
sent. Rewriting them would make the bundle's central claim - that the official
report is byte-for-byte as the suite wrote it - false. The caller passes
--exempt for that subtree, the bundle README states that it holds the headers,
and the tokens in it are expired by the time the bundle is read.

    redact_credentials.py rewrite --connect-yaml P [--env-secret NAME] ROOT...
    redact_credentials.py scan    --connect-yaml P [--env-secret NAME] ROOT...

A secret's value is never passed on a command line: --env-secret names an
environment variable and the value is read from the environment here, for the
same reason scripts/55-mint-tokens.sh hands curl a request body file.
"""

import argparse
import base64
import hashlib
import json
import os
import pathlib
import re
import sys

import yaml

# Invoked as a standalone script, so sys.path[0] is already this directory;
# the insert is for the case where a sibling imports this module instead.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tool_common import fail  # noqa: E402

PLACEHOLDER = "SET-YOUR-TOKEN-HERE"

# A JWS compact serialisation whose header begins with the usual `{"` - which is
# every token Keycloak issues. Used only to decide which test-data values are
# tokens, and in scan mode to report token-shaped strings this round cannot
# account for.
JWT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")

# Shorter than this and a literal substring replacement is more likely to shred
# the file than to protect anything: a 4-character "secret" occurs inside
# ordinary words. The rig generates both of the values above with openssl, so
# this bound is never reached in practice and is a guard against a hand-edited
# secrets/ file, not a routine case.
MIN_SECRET_LEN = 12


def digest(value):
    """The SHA-256 of a withheld value, which is what gets published instead."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def access_token_claims(token):
    """The claims of `token` if it is an access token, else None.

    A decision about the string itself, not about where it was found, and that
    is the point: matching against the values this round happens to hold at this
    moment is not enough. A bundle re-collected after the tokens were re-minted
    - the ordinary case for a bundle assembled the next day - holds tokens that
    are no longer the staged ones, and a value comparison would then pass while
    the log still carried six credentials that were live when it was written.

    An OAuth 2.0 access token in JWS compact form always carries `iss` and
    `exp`, and one of this realm's also carries `scope` or `aud`. Base64-encoded
    JSON that is NOT a token - an `Attachment.data` in the FHIR corpus, say -
    does not, which is what keeps this from rewriting evidence.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return None
    body = parts[1]
    try:
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        payload = json.loads(raw)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if "iss" not in payload or "exp" not in payload:
        return None
    if "scope" not in payload and "aud" not in payload:
        return None
    return payload


def marker(token, what):
    """What stands in for a withheld token: what it was for, and which one it was.

    The SHA-256 prefix is the whole point of the substitution: it proves that
    two files name the same credential without disclosing it. `what` is how the
    two callers below name the token, which is the only thing that differs
    between a token this round staged and one it can only read.
    """
    return f"<redacted: {what} sha256:{digest(token)[:16]}>"


def staged_token_marker(token, key):
    """What stands in for a token this round staged under a test-data key.

    The key is cited because it is more informative than the token: the reader
    of an exchange learns which scope's token was presented, which is the
    question a 401 or a 403 raises.
    """
    return marker(token, f"connect token {key}")


def unnamed_token_marker(token, payload):
    """What stands in for a token this round cannot name.

    A token from an earlier mint has no staged key to cite, so the scope out of
    the token's own claims is the next best thing - it is what a reader of the
    exchange needs.
    """
    scope = payload.get("scope")
    return marker(token, f"access token for scope {scope!r}" if scope
                  else "access token")


def collect_secrets(connect_yaml, env_names):
    """[(label, value, replacement)], longest value first.

    Longest first so that a secret which happens to be a substring of another is
    not left half-redacted: the longer replacement runs before the shorter one
    can cut it in two.
    """
    secrets = []

    if connect_yaml is not None:
        path = pathlib.Path(connect_yaml)
        if path.is_file():
            try:
                doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as exc:
                fail(f"{path}: not valid YAML: {exc}")
            if not isinstance(doc, dict):
                fail(f"{path}: expected a flat key -> value mapping")
            for key, value in doc.items():
                if not isinstance(value, str) or value == PLACEHOLDER:
                    continue
                if not JWT.fullmatch(value.strip()):
                    continue
                value = value.strip()
                secrets.append((
                    f"connect token {key}",
                    value,
                    staged_token_marker(value, key),
                ))
        # A missing staged contract is not an error here. A round with no Connect
        # module never staged one, and a round whose run/ was cleaned has nothing
        # left to match on - in both cases there is nothing to redact, and the
        # scan below is what reports a token that escaped anyway.

    for name in env_names:
        value = os.environ.get(name, "")
        if not value:
            continue
        if len(value) < MIN_SECRET_LEN:
            fail(
                f"{name} is {len(value)} characters, which is too short to replace "
                f"by substring without corrupting the files being published "
                f"(minimum {MIN_SECRET_LEN}). The rig generates this value; a short "
                f"one means it was set by hand. Regenerate it and run the round again."
            )
        secrets.append((
            f"${name}",
            value,
            f"<redacted: ${name} sha256:{digest(value)[:16]}>",
        ))

    secrets.sort(key=lambda s: len(s[1]), reverse=True)
    return secrets


def files_under(roots, exempt):
    """Every file the roots offer for rewriting or scanning.

    Symlinks and directories are not files to rewrite; a root that is itself a
    file is yielded as given. `exempt` holds substrings matched against the path
    relative to its root, which is how the verbatim upstream report is left
    untouched.
    """
    for root in roots:
        root = pathlib.Path(root)
        if root.is_file():
            yield root
            continue
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            rel = str(path.relative_to(root))
            if any(pattern in rel for pattern in exempt):
                continue
            yield path


def read_text(path):
    """The file's text, or None if it is not text this script should rewrite."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        fail(f"{path}: cannot read: {exc}")
    if b"\x00" in raw:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def replace_unnamed_tokens(text, totals):
    """Any remaining access token, named by its own claims rather than by a key.

    Runs after the named values, so a token this round can name is reported
    under its test-data key and only the rest arrive here.
    """
    found = {}

    def sub(match):
        """One token-shaped string, marked if it really is an access token."""
        token = match.group(0)
        payload = access_token_claims(token)
        if payload is None:
            return token
        found[token] = payload
        return unnamed_token_marker(token, payload)

    out = JWT.sub(sub, text)
    for token, payload in found.items():
        label = f"access token (not this round's) scope={payload.get('scope') or '?'}"
        totals[label] = totals.get(label, 0) + text.count(token)
    return out


def cmd_rewrite(args):
    """Replace every credential value in the given roots by its marker."""
    secrets = collect_secrets(args.connect_yaml, args.env_secret)

    totals = {label: 0 for label, _, _ in secrets}
    touched = 0
    seen = 0

    for path in files_under(args.roots, args.exempt):
        seen += 1
        text = read_text(path)
        if text is None:
            continue
        before = text
        for label, value, replacement in secrets:
            if value not in text:
                continue
            totals[label] += text.count(value)
            text = text.replace(value, replacement)
        # Last, and unconditionally: a token from an earlier mint is still a
        # credential that was live when the file was written, and no list of
        # values this round holds can account for it.
        text = replace_unnamed_tokens(text, totals)
        if text != before:
            # Rewritten in place, preserving the mode: the file is already part
            # of the round's output and anything holding a path to it (run.json's
            # containerLog, the provenance index) must keep pointing at it.
            path.write_text(text, encoding="utf-8")
            touched += 1

    found = {label: n for label, n in totals.items() if n}
    if not found:
        print(f"  no credential value occurred in {seen} file(s)")
        return 0

    print(f"  redacted {sum(found.values())} occurrence(s) in {touched} file(s):")
    for label in sorted(found):
        print(f"    {found[label]:3d}x  {label}")
    return 0


def cmd_scan(args):
    """Prove the claim rather than assert it: no named credential value is left."""
    secrets = collect_secrets(args.connect_yaml, args.env_secret)
    values = {value: label for label, value, _ in secrets}

    hits = []
    shaped = []
    scanned = 0

    for path in files_under(args.roots, args.exempt):
        scanned += 1
        try:
            raw = path.read_bytes()
        except OSError as exc:
            fail(f"{path}: cannot read: {exc}")
        # Decoded permissively and searched as text, so that a value stored
        # uncompressed inside an archive is still found.
        text = raw.decode("utf-8", errors="replace")
        for value, label in values.items():
            if value in text:
                hits.append((path, label, text.count(value)))
        # Sorted, not merely de-duplicated: the refusal below lists what it
        # found, and a listing that comes out in a different order on every
        # process is not something a reader can compare against the last run.
        for match in sorted(set(JWT.findall(text))):
            if match in values:
                continue  # already reported above, under its test-data key
            payload = access_token_claims(match)
            if payload is None:
                # Base64-encoded JSON that is not a token. Reported, not
                # refused: an Attachment.data in the FHIR corpus looks like
                # this, so the shape alone is a hint and not a finding.
                shaped.append((path, match[:11]))
            else:
                hits.append((path, f"an access token for scope "
                                   f"{payload.get('scope') or '?'}, from an "
                                   f"earlier mint", text.count(match)))

    if shaped:
        print(f"  note: {len(shaped)} base64 JSON string(s) that are not tokens:")
        for path, prefix in shaped[:5]:
            print(f"    {path}  ({prefix}...)")
        if len(shaped) > 5:
            print(f"    ... and {len(shaped) - 5} more")

    if hits:
        lines = [f"    {path}: {n}x {label}" for path, label, n in hits]
        fail(
            "a credential is still present in files that are about to be "
            "published:\n" + "\n".join(lines) + "\n\n  "
            "These must carry the SHA-256 reference instead. Either the rewrite "
            "pass did not cover the path, or the file was written after it ran."
        )

    print(f"  {scanned} file(s) scanned, no credential present")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    for name, handler in (("rewrite", cmd_rewrite), ("scan", cmd_scan)):
        p = sub.add_parser(name)
        p.add_argument("roots", nargs="+", help="files or directories to process")
        p.add_argument("--connect-yaml",
                       help="the staged testdata/Stufe5/connect.yaml the tokens were written into")
        p.add_argument("--env-secret", action="append", default=[], metavar="NAME",
                       help="name of an environment variable whose value is withheld")
        p.add_argument("--exempt", action="append", default=[], metavar="SUBSTRING",
                       help="skip paths containing this substring, relative to the root")
        p.set_defaults(handler=handler)

    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
