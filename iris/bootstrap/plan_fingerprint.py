#!/usr/bin/env python3
"""The fingerprint of what a package plan installs.

One number that answers the question a completion marker cannot: is the
installation sitting in this instance right now the one this round asks for?

It lives here, under iris/bootstrap/, because both sides of that question need
it and this directory is the one the IRIS container mounts. bootstrap.sh
compares it against the marker it left at the end of the previous install;
scripts/40-up.sh refuses to reuse a running instance installed from another
plan, and scripts/70-collect-report.sh refuses to publish a round that measured
one. A single implementation, so the two sides cannot disagree about what
"the same installation" means.

Deliberately not a digest of the plan file. Regenerating the plan can reword its
comment or reorder a mapping without changing a single package, and a digest
would then force a ~30 minute reinstall for nothing. The projection below covers
what actually reaches the SUT - the suite image the plan was derived from, and
for each endpoint its namespace, its base URL, the ordered packages imported
into its namespace and the ordered packages assigned to the endpoint itself -
and nothing else.

Usage: plan_fingerprint.py <package-plan.json>    # sha256 hex digest on stdout
"""
import hashlib
import json
import sys


def fingerprint(plan):
    """The sha256 of a plan's canonical projection."""
    endpoints = plan.get("endpoints") or {}
    # plan_packages.py writes a mapping keyed by level. A list is tolerated
    # because the report-time check reads plans archived by earlier rounds.
    items = (list(endpoints.items()) if isinstance(endpoints, dict)
             else [(e.get("level") or e.get("namespace") or "", e)
                   for e in endpoints])
    canonical = {
        "sourceImage": str(plan.get("sourceImage")),
        "endpoints": sorted(
            [str(key), str(ep.get("namespace")), str(ep.get("endpoint")),
             [str(p) for p in (ep.get("loadOrder") or [])],
             [str(p) for p in (ep.get("endpointPackageOrder") or [])]]
            for key, ep in items),
    }
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def main(argv):
    if len(argv) != 2:
        print("usage: plan_fingerprint.py <package-plan.json>", file=sys.stderr)
        return 2
    try:
        with open(argv[1], encoding="utf-8") as handle:
            plan = json.load(handle)
    except (OSError, ValueError) as exc:
        print(f"cannot read a package plan from {argv[1]}: {exc}", file=sys.stderr)
        return 1
    print(fingerprint(plan))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
