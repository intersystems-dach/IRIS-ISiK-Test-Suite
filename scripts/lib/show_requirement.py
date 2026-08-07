#!/usr/bin/env python3
"""Print everything the test suite requires of one resource, on one screen.

An authoring aid, not part of any pipeline. `RESOURCE-INVENTORY.md` says *that*
Patient/Patient-Read-Example must exist; the field-level specification for it is
spread across the `@Precondition` prose of every feature that touches it, which
for a Patient is a dozen files. This collects that scatter:

    scripts/lib/show_requirement.py Stufe5 Patient-Read-Example
    scripts/lib/show_requirement.py Stufe3 --module Vitalparameter --list

Reads vendor/requirements.json, so `make packages` (or
scripts/25-extract-requirements.sh) must have run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("level", choices=("Stufe3", "Stufe5"))
    ap.add_argument("resource_id", nargs="?", help="e.g. Patient-Read-Example")
    ap.add_argument("--module", help="restrict --list to one module")
    ap.add_argument("--list", action="store_true", help="list resources instead")
    ap.add_argument("--json", dest="json_path", type=Path,
                    default=REPO / "vendor" / "requirements.json")
    args = ap.parse_args()

    if not args.json_path.is_file():
        print(f"{args.json_path} not found - run: make packages", file=sys.stderr)
        return 2
    data = json.loads(args.json_path.read_text(encoding="utf-8"))
    level = data["levels"][args.level]
    feats = {f["rel"]: f for f in data["features"] if f["level"] == args.level}

    if args.list or not args.resource_id:
        for r in level["resources"]:
            if args.module and not any(
                    f.split("/")[1] == args.module for f in r["usedBy"]):
                continue
            flag = "" if r["inScope"] else "  (optional-only)"
            prof = r["profiles"][0].rsplit("/", 1)[-1] if r["profiles"] else \
                (r["profileNames"][0] if r["profileNames"] else f"[{r['noProfileReason']}]")
            print(f"{r['resourceType']:<24} {r['id']:<46} {prof}{flag}")
        return 0

    matches = [r for r in level["resources"] if r["id"] == args.resource_id]
    if not matches:
        print(f"no resource with id {args.resource_id!r} in {args.level}", file=sys.stderr)
        return 1

    for r in matches:
        print("=" * 78)
        print(f"{r['resourceType']}/{r['id']}    ({args.level})")
        print("=" * 78)
        print(f"keys      : {', '.join(r['keys'])}")
        print(f"scope     : {'required' if r['inScope'] else 'optional-only'}")
        for p in r["profiles"]:
            print(f"profile   : {p}")
        for p in r["profileNames"]:
            print(f"profile   : {p}  (named, not canonical)")
        if not r["profiles"] and not r["profileNames"]:
            print(f"profile   : none asserted ({r['noProfileReason']})")
        print()

        # Printed before the prose, and deliberately: these are what actually decide
        # the verdict. The prose explains the intent, but an instance that satisfies
        # every line of it can still fail a dozen of these.
        if r["assertions"]:
            print("-" * 78)
            print(f"ASSERTED ON THIS RESOURCE  ({len(r['assertions'])} distinct)")
            print("-" * 78)
            for a in r["assertions"]:
                where = f"{a['feature']}:{a['line']}"
                if a["kind"] == "fhirpath":
                    print(f"  {a['expr']}")
                elif a["kind"] == "attribute":
                    print(f"  [response] {a['path']} matches {a['matches']}")
                else:
                    print(f"  [ref] {a['element']} -> {a['target']}")
                print(f"        {where}")
            print()
        for rel in r["usedBy"]:
            f = feats[rel]
            print("-" * 78)
            print(f"{rel}   [{', '.join(f['tags'])}]")
            print("-" * 78)
            for block in f["preconditionBlocks"]:
                if "description" in block:
                    print(f"  REQUIREMENT ({rel}:{block['line']})")
                    print(f"    {block['description']}")
                else:
                    print(f"  PRECONDITION ({rel}:{block['line']})")
                    for line in block["text"].splitlines():
                        print(f"    {line}")
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
