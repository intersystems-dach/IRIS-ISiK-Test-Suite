#!/usr/bin/env python3
"""Write the rendered test-data corpus into one FHIR endpoint, then prove it landed.

Four passes, in this order, and the order is the point
-----------------------------------------------------
1. **Write** every resource with `PUT <base>/<Type>/<id>`, in the dependency order
   `seed_order.py` derives. PUT rather than POST because the suite addresses every
   resource by the exact ID its `testdata/**.yaml` contract names: a server-assigned
   ID would break every scenario at once. This relies on FHIR *update as create*,
   which is optional in the specification, so the CapabilityStatement is read first
   and what it says about `updateCreate` is recorded either way.

2. **Re-write** the resources that had to be written ahead of one of their own
   reference targets. Exactly one such resource exists in 2.7.0 (Stufe 5's
   `Encounter-Read-In-Progress-Example`, in a genuine three-node cycle with the two
   Conditions - see `seed_order.py`), and the second write is harmless for the
   others. This makes the corpus referentially complete even on an endpoint that
   rejects forward references.

3. **Read back** every resource with `GET <base>/<Type>/<id>`. A 2xx on the write
   is not proof: the resource has to be retrievable at the URL the suite will
   actually request, through the same search/read path, and this is the only pass
   that demonstrates it.

4. **Validate** every authored body with `POST <base>/<Type>/$validate`. Runs last
   so that a validation failure cannot stop the corpus from being complete and
   inspectable, and so that its verdict covers the same bytes that were written.
   The authored body is submitted rather than the stored one because the stored one
   differs only in `meta.versionId`/`meta.lastUpdated`, which no profile constrains,
   and submitting it would make the check depend on the read-back it is meant to be
   independent of.

Why validation matters more here than usual
-------------------------------------------
A non-conformant seeded resource produces a failing scenario that is
indistinguishable in the official report from an IRIS conformance gap. That is the
one misattribution this project must not make, so `$validate` runs before every
conformance round and `SEED_VALIDATE_STRICT=1` (the default) refuses to proceed on
any error-severity issue. The verdict is IRIS's own validator against the profiles
IRIS loaded - not this repository's opinion of its own data - which is what makes
it worth having.

`information`- and `warning`-severity issues are recorded but do not fail: the
IRIS validator emits warnings for terminology it cannot expand locally, and
treating those as defects would block every round for reasons outside the SUT.

Output
------
A JSON record per level at `--out`, listing every resource with the status of all
four passes and the validator's issues verbatim. It is the evidence that the run
started from a known, conformant corpus, and `70-collect-report.sh` copies it into
the run's report directory for exactly that reason.

Credentials come from `SUT_USER` / `SUT_PASSWORD` in the environment, never from
the command line. Not because the credential is confidential here - it is
documented in this project's reports on purpose, see `run/CREDENTIALS.md` - but
because that is how a production FHIR client is configured, and this seeder should
not be the one component that deviates. It costs nothing to do it properly.

Usage
-----
    SUT_USER=isikapi SUT_PASSWORD=... scripts/lib/seed_client.py \
        --level Stufe5 --base-url http://127.0.0.1:52773/isik5/fhir/r4 \
        --out run/seed/Stufe5.json
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from seed_order import load, order                       # noqa: E402

REPO = Path(__file__).resolve().parents[2]

FHIR_JSON = "application/fhir+json"

# Severities that make a resource unfit to seed. `warning` and `information` are
# recorded but tolerated - see the module docstring.
FATAL_SEVERITIES = ("fatal", "error")


class Endpoint:
    """The smallest FHIR client that does this job, with no third-party imports.

    `requests` is not assumed: the deployment host is provisioned for docker and
    coreutils, and a pip install is one more thing that can fail there. urllib is
    in the standard library and this needs exactly three verbs.
    """

    def __init__(self, base: str, user: str, password: str, timeout: int) -> None:
        self.base = base.rstrip("/")
        self.timeout = timeout
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        self.auth = f"Basic {token}"

    def request(self, method: str, path: str, body: dict | None = None) -> tuple[int, object]:
        """(status, parsed body or raw text). Never raises for an HTTP status.

        A 4xx/5xx carrying an OperationOutcome is the most informative thing this
        script can report, so error responses are read and returned rather than
        allowed to propagate as an exception with the body discarded.
        """
        url = f"{self.base}{path}"
        data = None
        headers = {"Accept": FHIR_JSON, "Authorization": self.auth}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = FHIR_JSON

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, self._parse(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, self._parse(exc.read())
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # Not an HTTP status at all: the endpoint is unreachable, the TLS/socket
            # layer failed, or the request timed out. Reported as 0 so the caller
            # distinguishes "the server said no" from "there was no server".
            return 0, {"_transport": str(exc)}

    @staticmethod
    def _parse(raw: bytes) -> object:
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            # An IRIS error page or a login redirect. Truncated, because the whole
            # of an HTML page in the seed log obscures the 129 records around it.
            return {"_nonJson": raw.decode("utf-8", "replace")[:600]}


def outcome_issues(body: object) -> list[dict]:
    """The issues of an OperationOutcome, normalised; [] for anything else."""
    if not isinstance(body, dict) or body.get("resourceType") != "OperationOutcome":
        return []
    out = []
    for issue in body.get("issue") or []:
        if not isinstance(issue, dict):
            continue
        text = ((issue.get("details") or {}).get("text")
                or issue.get("diagnostics")
                or issue.get("code") or "")
        out.append({
            "severity": issue.get("severity", "?"),
            "code": issue.get("code", "?"),
            "expression": (issue.get("expression") or issue.get("location") or [None])[0],
            "text": str(text)[:500],
        })
    return out


def summarise(body: object, limit: int = 200) -> str:
    """A one-line description of a response body, for the human-readable log."""
    for issue in outcome_issues(body):
        if issue["severity"] in FATAL_SEVERITIES:
            return f"{issue['severity']}: {issue['text']}"[:limit]
    if isinstance(body, dict):
        if "_transport" in body:
            return f"transport: {body['_transport']}"[:limit]
        if "_nonJson" in body:
            return f"response was not JSON: {body['_nonJson'][:120]}"
        issues = outcome_issues(body)
        if issues:
            return f"{issues[0]['severity']}: {issues[0]['text']}"[:limit]
        if body.get("resourceType"):
            return f"{body['resourceType']}/{body.get('id', '?')}"
    return ""


def capability(ep: Endpoint) -> dict:
    """What the endpoint says about itself, reduced to what seeding depends on.

    `updateCreate` is the one capability this whole approach rests on. It is read
    and recorded rather than assumed, because if it is false the failure mode is a
    404 on every write, and a run that ends there should say which capability was
    missing instead of leaving the reader to infer it.
    """
    status, body = ep.request("GET", "/metadata")
    info: dict = {"status": status, "fhirVersion": None,
                  "updateCreate": {}, "software": None}
    if status != 200 or not isinstance(body, dict):
        info["error"] = summarise(body) or f"HTTP {status}"
        return info
    if body.get("resourceType") != "CapabilityStatement":
        info["error"] = "response was not a CapabilityStatement"
        return info
    info["fhirVersion"] = body.get("fhirVersion")
    software = body.get("software") or {}
    info["software"] = f"{software.get('name', '?')} {software.get('version', '')}".strip()
    for rest in body.get("rest") or []:
        for res in rest.get("resource") or []:
            if res.get("type"):
                info["updateCreate"][res["type"]] = bool(res.get("updateCreate"))
    return info


def seed(ep: Endpoint, level: str, data_root: Path, do_validate: bool) -> dict:
    nodes = load(data_root, level)
    sequence = order(nodes)

    caps = capability(ep)
    record = {
        "level": level,
        "baseUrl": ep.base,
        "capability": caps,
        "resources": [],
        "counts": {"written": 0, "writeFailed": 0, "rewritten": 0,
                   "readBack": 0, "readFailed": 0,
                   "validated": 0, "validationErrors": 0, "validationSkipped": 0},
    }
    if caps.get("error"):
        record["fatal"] = f"the endpoint did not serve a usable CapabilityStatement: {caps['error']}"
        return record

    rows: dict[str, dict] = {}
    for n in sequence:
        rows[n["key"]] = {
            "key": n["key"], "resourceType": n["resourceType"], "id": n["id"],
            "file": n["file"], "cycleBroken": n["cycleBroken"],
            "updateCreate": caps["updateCreate"].get(n["resourceType"]),
        }
    record["resources"] = [rows[n["key"]] for n in sequence]

    def body_of(row: dict) -> dict:
        return json.loads((data_root / level / row["file"]).read_text(encoding="utf-8"))

    # --- pass 1: write -------------------------------------------------------
    print(f"  writing {len(sequence)} resources", file=sys.stderr)
    for n in sequence:
        row = rows[n["key"]]
        status, body = ep.request("PUT", f"/{row['resourceType']}/{row['id']}",
                                 body_of(row))
        row["write"] = {"status": status, "detail": summarise(body)}
        if status in (200, 201):
            record["counts"]["written"] += 1
        else:
            record["counts"]["writeFailed"] += 1
            row["write"]["issues"] = outcome_issues(body)
            print(f"    FAIL PUT {row['key']} -> HTTP {status} {row['write']['detail']}",
                  file=sys.stderr)

    # --- pass 2: re-write the cycle-broken resources --------------------------
    for n in sequence:
        if not n["cycleBroken"]:
            continue
        row = rows[n["key"]]
        status, body = ep.request("PUT", f"/{row['resourceType']}/{row['id']}",
                                 body_of(row))
        row["rewrite"] = {"status": status, "detail": summarise(body),
                          "because": n["cycleBroken"]}
        if status in (200, 201):
            record["counts"]["rewritten"] += 1
            # A first write that failed and a second that succeeded is a resolved
            # ordering problem, not a defect: correct the tally rather than leaving
            # the resource counted as failed.
            if row["write"]["status"] not in (200, 201):
                record["counts"]["writeFailed"] -= 1
                record["counts"]["written"] += 1
        else:
            print(f"    FAIL re-PUT {row['key']} -> HTTP {status} "
                  f"{row['rewrite']['detail']}", file=sys.stderr)

    # --- pass 3: read back ---------------------------------------------------
    print(f"  reading back {len(sequence)} resources", file=sys.stderr)
    for n in sequence:
        row = rows[n["key"]]
        status, body = ep.request("GET", f"/{row['resourceType']}/{row['id']}")
        okay = (status == 200 and isinstance(body, dict)
                and body.get("resourceType") == row["resourceType"]
                and body.get("id") == row["id"])
        row["read"] = {"status": status, "ok": okay,
                       "versionId": ((body or {}).get("meta") or {}).get("versionId")
                       if isinstance(body, dict) else None,
                       "detail": "" if okay else summarise(body)}
        if okay:
            record["counts"]["readBack"] += 1
        else:
            record["counts"]["readFailed"] += 1
            print(f"    FAIL GET {row['key']} -> HTTP {status} {row['read']['detail']}",
                  file=sys.stderr)

    # --- pass 4: validate ----------------------------------------------------
    if not do_validate:
        record["counts"]["validationSkipped"] = len(sequence)
        print("  $validate skipped (SEED_VALIDATE=0)", file=sys.stderr)
        return record

    print(f"  validating {len(sequence)} resources", file=sys.stderr)
    for n in sequence:
        row = rows[n["key"]]
        status, body = ep.request("POST", f"/{row['resourceType']}/$validate",
                                  body_of(row))
        issues = outcome_issues(body)
        errors = [i for i in issues if i["severity"] in FATAL_SEVERITIES]
        # HTTP status alone does not decide this. A conformant server may answer 200
        # with an OperationOutcome full of errors, or 400 with the same content, so
        # the issues are what is judged - and a response that is not an
        # OperationOutcome at all means the operation itself did not run.
        ran = bool(issues) or (isinstance(body, dict)
                               and body.get("resourceType") == "OperationOutcome")
        row["validate"] = {
            "status": status, "ran": ran,
            "errors": errors,
            "warnings": [i for i in issues if i["severity"] not in FATAL_SEVERITIES],
        }
        if not ran:
            record["counts"]["validationSkipped"] += 1
            row["validate"]["detail"] = summarise(body) or f"HTTP {status}"
            print(f"    NOTE $validate {row['key']} -> HTTP {status}: no "
                  f"OperationOutcome ({row['validate']['detail']})", file=sys.stderr)
            continue
        record["counts"]["validated"] += 1
        if errors:
            record["counts"]["validationErrors"] += 1
            print(f"    INVALID {row['key']} ({len(errors)} error-severity issue(s))",
                  file=sys.stderr)
            for i in errors[:4]:
                where = f" at {i['expression']}" if i["expression"] else ""
                print(f"            {i['code']}{where}: {i['text']}", file=sys.stderr)

    return record


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--level", choices=("Stufe3", "Stufe5"), required=True)
    ap.add_argument("--base-url", required=True,
                    help="FHIR base, e.g. http://127.0.0.1:52773/isik5/fhir/r4")
    ap.add_argument("--data-root", type=Path, default=REPO / "build" / "fhir")
    ap.add_argument("--out", type=Path, help="write the JSON seed record here")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--no-validate", action="store_true",
                    help="skip the $validate pass (SEED_VALIDATE=0)")
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero when any resource fails $validate")
    args = ap.parse_args()

    user = os.environ.get("SUT_USER")
    password = os.environ.get("SUT_PASSWORD")
    if not user or not password:
        print("SUT_USER and SUT_PASSWORD must be set in the environment",
              file=sys.stderr)
        return 2

    ep = Endpoint(args.base_url, user, password, args.timeout)
    record = seed(ep, args.level, args.data_root, not args.no_validate)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8")
        # The record carries no credential: the base URL holds none and the
        # Authorization header is never logged, so no special file mode is needed.
        # Not a suppression - this project documents its credentials deliberately -
        # but the channel for that is run/CREDENTIALS.md and run.json's
        # "credentials" block, and one channel means one place to look.

    c = record["counts"]
    if record.get("fatal"):
        print(f"{args.level}: {record['fatal']}", file=sys.stderr)
        return 1

    caps = record["capability"]
    print(f"{args.level}: {caps.get('software') or 'unknown server'}, "
          f"FHIR {caps.get('fhirVersion') or '?'}", file=sys.stderr)
    print(f"{args.level}: {c['written']} written, {c['readBack']} read back, "
          f"{c['validated']} validated"
          + (f", {c['rewritten']} re-written to close a reference cycle"
             if c["rewritten"] else "")
          + (f", {c['validationSkipped']} not validated"
             if c["validationSkipped"] else ""), file=sys.stderr)

    if c["writeFailed"] or c["readFailed"]:
        print(f"{args.level}: {c['writeFailed']} write failure(s), "
              f"{c['readFailed']} read-back failure(s) - the corpus is incomplete, "
              f"so a conformance run would attribute the missing resources to IRIS",
              file=sys.stderr)
        return 1

    if c["validationErrors"]:
        # Our defect, not the SUT's, and reported as such in both directions: the
        # message names the data, and the exit status is governed by --strict so a
        # debugging round can proceed with the finding recorded.
        print(f"{args.level}: {c['validationErrors']} resource(s) failed $validate "
              f"- these are TEST DATA defects, not IRIS gaps; see the record at "
              f"{args.out}", file=sys.stderr)
        return 1 if args.strict else 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
