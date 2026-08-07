#!/usr/bin/env python3
"""Read the must-support / cardinality / binding constraints out of a profile.

An authoring aid for the test data, and later the lookup that tells the gap
report which FHIR package supplies a given canonical.

Why this is needed
------------------
The suite validates responses with the Gematik reference validator against real
StructureDefinitions, so "author to profile" means author to *those files*, not to
a recollection of what ISiK requires. Two constraint classes decide whether an
instance validates at all:

  - min cardinality > 0        the element must be present
  - fixed / pattern values     the element must carry exactly that value
  - required bindings          the code must come from that ValueSet

and a third decides whether the *test* passes even when validation would:

  - mustSupport                ISiK's own conformance statement. A server has to
                               be able to return it, so the suite's FHIRPath
                               assertions target these elements.

`--differential` is usually what an author wants: the snapshot of an ISiK profile
carries every inherited element from Patient, which is several hundred rows of
noise around the dozen the profile actually constrains.

Usage
-----
    scripts/lib/profile_constraints.py --packages /tmp/pkg/unpacked \\
        https://gematik.de/fhir/isik/StructureDefinition/ISiKPatient

    scripts/lib/profile_constraints.py --packages ... --list ISiK      # search
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load_index(packages_dir: Path) -> dict[str, tuple[Path, str]]:
    """canonical URL -> (file, "name@version" of the supplying package).

    Later packages do not overwrite earlier ones: several versions of
    hl7.terminology.r4 sit side by side in the same plugin set, and silently
    preferring whichever sorted last would attribute a constraint to a version the
    validator may not be using. First one wins, in sorted order, and the caller
    can disambiguate by pointing --packages at a narrower directory.
    """
    index: dict[str, tuple[Path, str]] = {}
    # Directories only: the same directory also holds MANIFEST.txt and PARITY.txt.
    for pkg_dir in sorted(p for p in packages_dir.iterdir() if p.is_dir()):
        root = pkg_dir / "package"
        if not root.is_dir():
            root = pkg_dir
        meta = root / "package.json"
        label = pkg_dir.name
        if meta.is_file():
            try:
                m = json.loads(meta.read_text(encoding="utf-8"))
                label = f"{m.get('name', pkg_dir.name)}@{m.get('version', '?')}"
            except (json.JSONDecodeError, OSError):
                pass
        for path in sorted(root.glob("*.json")):
            if path.name in ("package.json", ".index.json"):
                continue
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not isinstance(doc, dict):
                continue
            url = doc.get("url")
            if url and doc.get("resourceType") in (
                    "StructureDefinition", "ValueSet", "CodeSystem",
                    "SearchParameter", "OperationDefinition"):
                index.setdefault(url, (path, label))
    return index


def _short(value) -> str:
    """A fixed/pattern value as one readable line."""
    if isinstance(value, dict):
        if "coding" in value:
            return " | ".join(
                f"{c.get('system', '')}#{c.get('code', '')}" for c in value["coding"])
        if "system" in value and "code" in value:
            return f"{value['system']}#{value['code']}"
        if "system" in value and "value" in value:
            return f"{value['system']}|{value['value']}"
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def describe(doc: dict, differential: bool, must_support_only: bool) -> list[str]:
    out: list[str] = []
    kind = "differential" if differential else "snapshot"
    elements = (doc.get(kind) or {}).get("element") or []
    if not elements and differential:
        elements = (doc.get("snapshot") or {}).get("element") or []
        kind = "snapshot (no differential in this profile)"

    out.append(f"# {doc.get('name')}  ({doc.get('type')})")
    out.append(f"# url    : {doc.get('url')}")
    out.append(f"# baseOn : {doc.get('baseDefinition')}")
    out.append(f"# view   : {kind}, {len(elements)} elements")
    out.append("")

    for el in elements:
        path = el.get("path", "?")
        ms = bool(el.get("mustSupport"))
        lo, hi = el.get("min"), el.get("max")
        fixed = next((v for k, v in el.items()
                      if k.startswith("fixed") or k.startswith("pattern")), None)
        binding = el.get("binding") or {}
        strength = binding.get("strength")
        vs = binding.get("valueSet")
        slicing = el.get("slicing")

        interesting = ms or (isinstance(lo, int) and lo > 0) or fixed is not None \
            or strength == "required" or slicing
        if must_support_only and not ms:
            continue
        if not interesting:
            continue

        marks = []
        if isinstance(lo, int) or hi:
            marks.append(f"{lo if lo is not None else '?'}..{hi or '?'}")
        if ms:
            marks.append("MS")
        if slicing:
            disc = ", ".join(
                f"{d.get('type')}:{d.get('path')}" for d in slicing.get("discriminator", []))
            marks.append(f"sliced[{disc}]")
        out.append(f"{path:<58} {' '.join(marks)}")
        if el.get("sliceName"):
            out.append(f"{'':<58}   slice: {el['sliceName']}")
        if fixed is not None:
            out.append(f"{'':<58}   fixed: {_short(fixed)}")
        if strength:
            out.append(f"{'':<58}   binding[{strength}]: {vs}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("canonical", nargs="?", help="profile canonical URL, or a name")
    ap.add_argument("--packages", type=Path, required=True,
                    help="directory of unpacked FHIR packages (vendor/packages)")
    ap.add_argument("--list", metavar="SUBSTRING", help="list matching canonicals")
    ap.add_argument("--snapshot", action="store_true",
                    help="show the full snapshot instead of the differential")
    ap.add_argument("--must-support", action="store_true",
                    help="only mustSupport elements")
    args = ap.parse_args()

    index = load_index(args.packages)

    if args.list is not None:
        for url, (path, pkg) in sorted(index.items()):
            if args.list.lower() in url.lower():
                print(f"{url}\n    {pkg}  {path.name}")
        return 0

    if not args.canonical:
        ap.error("give a canonical URL or --list")

    hit = index.get(args.canonical)
    if hit is None:
        # Accept a bare profile name too - the features name profiles both ways.
        cands = [u for u in index if u.rsplit("/", 1)[-1] == args.canonical]
        if len(cands) == 1:
            hit = index[cands[0]]
        elif cands:
            print(f"{args.canonical!r} is ambiguous:", file=sys.stderr)
            for c in cands:
                print(f"  {c}", file=sys.stderr)
            return 1
        else:
            print(f"not found: {args.canonical}", file=sys.stderr)
            return 1

    path, pkg = hit
    doc = json.loads(path.read_text(encoding="utf-8"))
    print(f"# package: {pkg}")
    print(f"# file   : {path}")
    print("\n".join(describe(doc, not args.snapshot, args.must_support)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
