#!/usr/bin/env python3
"""Resolve authored test-data sources into the per-level resources that get seeded.

Why there is a build step at all
--------------------------------
40 of the 84 distinct (type, id) pairs are required by *both* Stufe 3 and Stufe 5,
and for 37 of those the two levels assert the same content. What differs is the
profile: ISiK 3 canonicals live under
`https://gematik.de/fhir/isik/v3/<Modul>/StructureDefinition/`, ISiK 5 under
`https://gematik.de/fhir/isik/StructureDefinition/`. Authoring each of those
resources twice would mean 40 pairs of files kept in sync by hand, and the first
time they drifted the difference would surface as a conformance failure in one
level only - which reads exactly like an IRIS gap.

So a resource is authored once in `common/`, and this script renders it per level:

  meta.profile      stamped from `vendor/requirements.json`, i.e. from the profile
                    the feature files actually assert on that resource in that
                    level. Never hand-typed. A wrong profile claim would make the
                    reference validator check the instance against the wrong
                    StructureDefinition, and the resulting failure would be ours
                    while looking like the SUT's.

  {{data.<key>}}    replaced with the ID that level's `testdata/**.yaml` contract
                    assigns to `<key>`. References are written this way rather than
                    as literal IDs so that a shared body stays correct even where
                    the two levels assign different IDs to the same role - and an
                    unresolvable key is a hard error, not a placeholder shipped
                    into a request.

  {{profile.<Name>}}  resolved to the canonical URL that *this level's* package set
                    defines for that profile name. Needed for the 9 inventory rows
                    that no assertion covers - a resource that is only a reference
                    target, or is deleted, is never validated by the suite, so
                    requirements.json has no canonical to stamp. Writing the URL by
                    hand would hardcode one level's namespace: ISiKPatient is
                    `.../isik/v3/Basismodul/StructureDefinition/ISiKPatient` in
                    Stufe 3 and `.../isik/StructureDefinition/ISiKPatient` in
                    Stufe 5, and the v5 URL in the ISIK3 namespace names a profile
                    that endpoint has never loaded. The claim is worth making rather
                    than omitting because IRIS `$validate` runs over the seed data
                    before every round, so a profile claim turns into a real check
                    of our own authoring.

Where the levels genuinely disagree - `CodeSystem-Read-Example.name` is
"testkatalog" in Stufe 3 and "Test Catalog" in Stufe 5 - a full body in
`Stufe3/` or `Stufe5/` overrides `common/` for that level only.

Resources no data key names
---------------------------
The inventory in `vendor/requirements.json` is keyed off the suite's own test-data
contract, so it can only list a resource that some `${data.*}` key points at. A few
scenarios need a resource the contract never names, because they find it by search
rather than by ID. Stufe 5's `Appointment-Book-By-Schedule` is the case in 2.7.0:

    Get FHIR resource at ".../Slot/?schedule=${data.appointment-book-by-schedule-schedule-id}
                                  &start=${data.appointment-book-by-schedule-date}"
    ... entry.resource.ofType(Slot).where(status = 'free' and start <= @... and end >= @...)
        .count() >= 1

The *schedule* is named by a key and so is in the inventory; the free Slot inside it
is not named anywhere, and the scenario fails without it. Left unseeded, that reads
as an IRIS search defect rather than as absent test data.

Such resources are authored under `extra/`, one directory per level, and are
rendered after the inventory rows with the same placeholder substitution. The
directory is separate rather than a flag inside a normal file because these bodies
have a materially weaker provenance: every `common/` and `Stufe*/` file traces to an
inventory row derived from a feature assertion, whereas an `extra/` file traces to a
human reading a search expression. Keeping them apart means the count of inventory
rows still reconciles against the extractor, and a reviewer can see at a glance
which resources exist on our judgement rather than on the suite's contract. Each
one carries a `_provenance` comment naming the scenario that requires it; the key is
stripped before the file is written, since it is not valid FHIR.

`extra/` bodies get no `meta.profile` stamped from requirements.json - there is no
row to stamp from - so a profile claim there must be written as `{{profile.Name}}`
and is the author's own, exactly as for a `never-read` inventory row.

Layout
------
    testdata/fhir/common/<Type>-<id>.json     level-neutral body, no meta.profile
    testdata/fhir/Stufe3/<Type>-<id>.json     per-level override (complete body)
    testdata/fhir/Stufe5/<Type>-<id>.json
    testdata/fhir/extra/<level>/<Type>-<id>.json   required but named by no data key
    build/fhir/<level>/<Type>-<id>.json       rendered output (gitignored)

Usage
-----
    scripts/lib/build_testdata.py                    # both levels
    scripts/lib/build_testdata.py --level Stufe3 --check-only
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RE_PLACEHOLDER = re.compile(r"\{\{data\.([A-Za-z0-9_.-]+)\}\}")
RE_PROFILE = re.compile(r"\{\{profile\.([A-Za-z0-9_-]+)\}\}")

# Which ISiK package set backs each level. Used to resolve {{profile.Name}} to the
# canonical that level's endpoint will actually have loaded, by reading the
# StructureDefinitions themselves rather than by assembling a URL from a pattern -
# the two levels do not merely differ in prefix, they differ in whether the module
# name appears at all, and one profile has moved module between releases.
LEVEL_PACKAGES = {
    "Stufe3": ("de.gematik.isik-basismodul", "de.gematik.isik-dokumentenaustausch",
               "de.gematik.isik-medikation", "de.gematik.isik-terminplanung",
               "de.gematik.isik-vitalparameter"),
    "Stufe5": ("de.gematik.isik",),
}


def profile_index(packages_dir: Path, level: str) -> dict[str, str]:
    """Profile name -> canonical URL, restricted to this level's ISiK packages.

    Restricted deliberately: both levels' packages sit side by side under
    vendor/packages, and an unrestricted lookup of "ISiKPatient" would return
    whichever sorted first, silently stamping a Stufe 5 canonical into Stufe 3 data.
    """
    if not packages_dir.is_dir():
        return {}
    prefixes = LEVEL_PACKAGES[level]
    out: dict[str, str] = {}
    for pkg_dir in sorted(packages_dir.iterdir()):
        # Directory names are "<name>-<version>" or "<name>#<version>" depending on
        # which fetch route produced them; both are matched on the name part.
        stem = re.sub(r"[-#]\d[\w.+-]*$", "", pkg_dir.name)
        if stem not in prefixes:
            continue
        root = pkg_dir / "package" if (pkg_dir / "package").is_dir() else pkg_dir
        for path in sorted(root.glob("StructureDefinition-*.json")):
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if doc.get("resourceType") == "StructureDefinition" and doc.get("name"):
                out.setdefault(doc["name"], doc["url"])
    return out


def load_contract(level: str, req: dict) -> dict[str, str]:
    """key -> ID, from the suite's own test-data contract for this level.

    Taken out of requirements.json rather than re-read from the YAML files so that
    the IDs the seed data is built with are provably the same strings the extractor
    documented and the suite will send.
    """
    out: dict[str, str] = {}
    for f in req["features"]:
        if f["level"] != level:
            continue
        for key, info in f["dataKeys"].items():
            if info["value"] not in (None, ""):
                out[key] = str(info["value"])
    return out


def substitute(node, contract: dict[str, str], profiles: dict[str, str],
               unresolved: set[str]):
    """Replace every {{data.key}} / {{profile.Name}}, recording what did not resolve."""
    if isinstance(node, dict):
        return {k: substitute(v, contract, profiles, unresolved)
                for k, v in node.items()}
    if isinstance(node, list):
        return [substitute(v, contract, profiles, unresolved) for v in node]
    if isinstance(node, str):
        def data_repl(m):
            key = m.group(1)
            if key not in contract:
                unresolved.add(f"data.{key}")
                return m.group(0)
            return contract[key]

        def profile_repl(m):
            name = m.group(1)
            if name not in profiles:
                unresolved.add(f"profile.{name}")
                return m.group(0)
            return profiles[name]

        return RE_PROFILE.sub(profile_repl, RE_PLACEHOLDER.sub(data_repl, node))
    return node


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--level", choices=("Stufe3", "Stufe5"), action="append",
                    help="build only this level (repeatable; default: both)")
    ap.add_argument("--src", type=Path, default=REPO / "testdata" / "fhir")
    ap.add_argument("--out", type=Path, default=REPO / "build" / "fhir")
    ap.add_argument("--requirements", type=Path,
                    default=REPO / "vendor" / "requirements.json")
    ap.add_argument("--packages", type=Path, default=REPO / "vendor" / "packages",
                    help="unpacked FHIR packages, for resolving {{profile.Name}}")
    ap.add_argument("--check-only", action="store_true",
                    help="report what would be built, write nothing")
    ap.add_argument("--allow-incomplete", action="store_true",
                    help="do not fail when an inventory row has no authored source")
    args = ap.parse_args()

    if not args.requirements.is_file():
        print(f"{args.requirements} not found - run: make packages", file=sys.stderr)
        return 2
    req = json.loads(args.requirements.read_text(encoding="utf-8"))
    levels = args.level or ["Stufe3", "Stufe5"]

    rc = 0
    for level in levels:
        contract = load_contract(level, req)
        profiles = profile_index(args.packages, level)
        if not profiles:
            print(f"  note: no ISiK StructureDefinitions found under {args.packages} "
                  f"for {level} - {{{{profile.*}}}} cannot be resolved "
                  f"(run `make packages` first)")
        rows = req["levels"][level]["resources"]
        out_dir = args.out / level
        built = missing = 0
        problems: list[str] = []

        for r in rows:
            name = f"{r['resourceType']}-{r['id']}.json"
            override = args.src / level / name
            common = args.src / "common" / name
            src = override if override.is_file() else common
            if not src.is_file():
                missing += 1
                problems.append(f"  no source for {r['resourceType']}/{r['id']} "
                                f"(looked in {level}/ and common/)")
                continue

            doc = json.loads(src.read_text(encoding="utf-8"))
            if doc.get("resourceType") != r["resourceType"]:
                problems.append(f"  {src.name}: declares resourceType "
                                f"{doc.get('resourceType')!r}, inventory says "
                                f"{r['resourceType']!r}")
                rc = 1
                continue

            # The ID is stamped, not authored: it is the contract's value by
            # definition, and a hand-typed copy is one more thing that can drift
            # from the YAML the suite actually sends.
            doc["id"] = r["id"]

            unresolved: set[str] = set()
            doc = substitute(doc, contract, profiles, unresolved)
            if unresolved:
                problems.append(f"  {src.name}: unresolved placeholder(s) "
                                + ", ".join(sorted(unresolved)))
                rc = 1
                continue

            # Profiles the suite asserts on this resource in this level. An authored
            # meta.profile is not trusted over this - if a source file carries one,
            # it is replaced, because requirements.json is derived from the feature
            # files and the source file is derived from a human reading them.
            if r["profiles"]:
                doc.setdefault("meta", {})["profile"] = list(r["profiles"])
            elif (doc.get("meta") or {}).get("profile"):
                # No canonical asserted, but the author claimed one. Kept and
                # reported: for a `never-read` or `fixture-body` row the claim is
                # the author's own judgement from the prose, which is legitimate,
                # but it has not been corroborated by any assertion.
                problems.append(f"  note: {src.name} claims a profile that no "
                                f"assertion in {level} corroborates "
                                f"({r['noProfileReason']})")

            if not args.check_only:
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / name).write_text(
                    json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
            built += 1

        # Resources required by a scenario but named by no data key. Rendered with
        # the same substitution, counted separately: the inventory total must keep
        # reconciling against the extractor, and the weaker provenance of these
        # bodies (see the module docstring) should stay visible in the output.
        extra = 0
        for src in sorted((args.src / "extra" / level).glob("*.json")):
            doc = json.loads(src.read_text(encoding="utf-8"))
            why = doc.pop("_provenance", None)
            if not why:
                problems.append(f"  {src.name}: no `_provenance` naming the scenario "
                                f"that requires it - required for extra/ bodies, "
                                f"which no inventory row justifies")
                rc = 1
                continue
            unresolved = set()
            doc = substitute(doc, contract, profiles, unresolved)
            if unresolved:
                problems.append(f"  {src.name}: unresolved placeholder(s) "
                                + ", ".join(sorted(unresolved)))
                rc = 1
                continue
            if f"{doc.get('resourceType')}-{doc.get('id')}.json" != src.name:
                problems.append(f"  {src.name}: body declares "
                                f"{doc.get('resourceType')}/{doc.get('id')}, which does "
                                f"not match the filename")
                rc = 1
                continue
            if not args.check_only:
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / src.name).write_text(
                    json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
            extra += 1

        print(f"{level}: {built} resources rendered, {missing} without a source "
              f"({len(rows)} in the inventory)"
              + (f", plus {extra} named by no data key" if extra else ""))
        for p in problems:
            print(p)
        if missing and not args.allow_incomplete:
            rc = 1

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
