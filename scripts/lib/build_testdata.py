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

  {{<ns>.<key>}}    replaced with the ID that level's `testdata/**.yaml` contract
                    assigns to `<key>` in namespace `<ns>`. References are written
                    this way rather than as literal IDs so that a shared body stays
                    correct even where the two levels assign different IDs to the
                    same role - and an unresolvable key is a hard error, not a
                    placeholder shipped into a request.

                    `<ns>` is the suite's `baseKey`: `data` for every file up to
                    2.7.0, one namespace per module from 3.0.0 (`basis`,
                    `medikation`, `icuextended`, ...). Bodies authored for a 2.x
                    round therefore write `{{data.<key>}}`, and the legacy rule in
                    `substitute()` keeps them resolvable under a 3.x contract
                    without rewriting 70 files - see there for the exact rule and
                    for why an ambiguous key is a hard error rather than a
                    first-match.

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
contract, so it can only list a resource that some `${<ns>.*}` key points at. A few
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

`--src` is repeatable, and a round against a 3.x suite passes both
`testdata/fhir` and `testdata/fhir/v3` (suite_fhir_roots() in lib.sh). The second
tree holds only the resources that release added; everything the two generations
share stays in the first one and is not duplicated. Within one tier a later
`--src` wins, and the level tier still beats the common tier, so the full
precedence for one resource is

    <src_n>/<level>/  >  ...  >  <src_1>/<level>/  >  <src_n>/common/  >  ...

Usage
-----
    scripts/lib/build_testdata.py                    # both levels (a 2.x suite)
    scripts/lib/build_testdata.py --levels Stufe5 --src testdata/fhir \
                                 --src testdata/fhir/v3
    scripts/lib/build_testdata.py --level Stufe3 --check-only
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# `{{<namespace>.<key>}}`. The namespace is matched rather than fixed to `data`,
# because 3.0.0 replaced the single `data` baseKey with one per module. The shape
# is the same as feature_parser.DATA_KEY_PATTERN, which matches the `${...}` form
# the feature files use; the two are deliberately not shared, because this one
# must NOT match `{{profile.Name}}`, whose dot-separated right-hand side would
# otherwise be read as a test-data key.
RE_PLACEHOLDER = re.compile(r"\{\{(?!profile\.)([A-Za-z0-9_]+\.[A-Za-z0-9_.-]+)\}\}")
RE_PROFILE = re.compile(r"\{\{profile\.([A-Za-z0-9_-]+)\}\}")

# The prefix that marks a package as ISiK's own. Profile canonicals are resolved
# only out of these, never out of the base profiles or the terminology packages
# that sit beside them.
ISIK_PACKAGE_PREFIX = "de.gematik.isik"


def level_packages(plan_path: Path, level: str) -> tuple[str, ...]:
    """The ISiK package names loaded on this level's endpoint.

    Read from vendor/package-plan.json rather than listed here, because the plan
    is itself derived from the suite's own validation plugin - the authoritative
    statement of what each endpoint is loaded with - and because the list changed
    with the suite generation: 2.7.0's Stufe 3 endpoint carries five module
    packages, its Stufe 5 endpoint the single `de.gematik.isik`, and 3.0.0 added
    four modules.

    Measured against the 2.7.0 plan before the list was replaced by this lookup:
    it yields the five hardcoded Stufe 3 names plus
    `de.gematik.isik3-dokumentenaustausch-terminology`, which contains no
    StructureDefinition at all, so the resulting profile index is unchanged.
    """
    if not plan_path.is_file():
        return ()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    bucket = (plan.get("endpoints") or {}).get(level.lower()) or {}
    names = {re.sub(r"[#@].*$", "", p) for p in bucket.get("packages") or []}
    return tuple(sorted(n for n in names if n.startswith(ISIK_PACKAGE_PREFIX)))


def profile_index(packages_dir: Path, prefixes: tuple[str, ...]) -> dict[str, str]:
    """Profile name -> canonical URL, restricted to this level's ISiK packages.

    Restricted deliberately: both levels' packages sit side by side under
    vendor/packages, and an unrestricted lookup of "ISiKPatient" would return
    whichever sorted first, silently stamping a Stufe 5 canonical into Stufe 3 data.
    """
    if not packages_dir.is_dir() or not prefixes:
        return {}
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
    """Qualified `<ns>.<key>` -> ID, from the suite's own contract for this level.

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


LEGACY_NS = "data"


def legacy_index(contract: dict[str, str]) -> dict[str, set[str]]:
    """Bare key -> the set of distinct values the contract assigns it.

    One of the two indices behind the legacy rule in substitute(); this is the
    unscoped one, consulted when the resource's own namespaces define the key
    nowhere. A set rather than a single value, because the set's size is exactly
    the decision: one value means every namespace that defines the key agrees and
    the bare name is unambiguous; more than one means it is not.
    """
    out: dict[str, set[str]] = {}
    for qualified, value in contract.items():
        # A key without a namespace is its own bare name. Splitting on the first
        # dot unconditionally would make every such key index under the empty
        # string, collapsing the whole contract into one ambiguous bucket -
        # measured against a requirements.json produced before keys were
        # qualified, which turned all 84 bodies into "unresolved placeholder(s)".
        ns, dot, rest = qualified.partition(".")
        bare = rest if dot else ns
        out.setdefault(bare, set()).add(value)
    return out


def row_namespaces(row: dict) -> set[str]:
    """The test-data namespaces that name this resource.

    An inventory row records every qualified key that resolves to the resource,
    across modules - Observation/Body-Weight-Read-Example is named by
    `icuminimal.observation-read-body-weight-id`,
    `vitalparameter.observation-read-body-weight-id` and
    `medikation.riskassessment-observation-id`. Those namespaces are the scope in
    which a legacy `{{data.<key>}}` inside that body is resolved; see
    substitute().
    """
    return {k.partition(".")[0] for k in row.get("keys") or [] if "." in k}


def substitute(node, contract: dict[str, str], profiles: dict[str, str],
               unresolved: set[str], legacy: dict[str, set[str]] | None = None,
               ambiguous: set[str] | None = None,
               ns_scope: set[str] | None = None):
    """Replace every {{<ns>.<key>}} / {{profile.Name}}, recording what did not resolve.

    Qualified lookup first: the placeholder names a namespace and a key, and under
    the generation the body was authored for that is an exact contract key.

    Legacy rule, for `{{data.<key>}}` only. Every body in `common/` was authored
    against a 2.x contract, where the suite loaded all of its test-data files
    under the single baseKey `data`. Under a 3.x contract there is no `data`
    namespace at all, so those placeholders would all be unresolvable and 70
    bodies would have to be rewritten per generation - which is the duplication
    this build step exists to avoid. The key is resolved by its bare name
    instead, in two steps:

      1. within `ns_scope`, the namespaces that name *this* resource. A body
         reached through `vitalparameter.*` resolves `data.observation-patient-id`
         to `vitalparameter.observation-patient-id`, which is the reference the
         scenario reading it through that module expects.
      2. failing that, across every namespace, which covers a key the resource's
         own modules do not define - `basis.loinc-version` referenced from an ICU
         body, for instance.

    Either step resolves only if the candidate namespaces agree on one value.

    An ambiguous bare name is a hard error, never a first match. In 3.0.9, 45 of
    the 262 key names are defined in more than one module and three of them -
    observation-encounter-id, observation-patient-id, observation-practitioner-id
    - carry *different* ids per module (see suite_namespaces() in lib.sh). Picking
    one would seed a body referencing another module's resource, and the scenario
    that then failed would read as an IRIS defect. Measured on the 3.0.9
    inventory: step 1 is what decides 33 such occurrences across 11 Observation
    bodies, and with step 1 removed every one of them is reported ambiguous.
    A body whose ambiguity survives both steps must name its namespace
    explicitly.
    """
    if isinstance(node, dict):
        return {k: substitute(v, contract, profiles, unresolved, legacy,
                              ambiguous, ns_scope)
                for k, v in node.items()}
    if isinstance(node, list):
        return [substitute(v, contract, profiles, unresolved, legacy,
                           ambiguous, ns_scope)
                for v in node]
    if isinstance(node, str):
        def data_repl(m):
            key = m.group(1)
            if key in contract:
                return contract[key]
            ns, _, bare = key.partition(".")
            if ns == LEGACY_NS and legacy is not None:
                for values in (
                    {contract[q] for q in (f"{n}.{bare}" for n in ns_scope or ())
                     if q in contract},
                    legacy.get(bare) or set(),
                ):
                    if len(values) == 1:
                        return next(iter(values))
                    if len(values) > 1:
                        if ambiguous is not None:
                            ambiguous.add(bare)
                        return m.group(0)
            unresolved.add(key)
            return m.group(0)

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
                    help="build only this level (repeatable; default: --levels)")
    ap.add_argument("--levels", default="Stufe3 Stufe5",
                    help="the levels the pinned suite carries, space-separated "
                         "(default: both, i.e. a 2.x suite)")
    ap.add_argument("--src", type=Path, action="append",
                    help="authored source tree, repeatable; a later one wins "
                         "(default: testdata/fhir)")
    ap.add_argument("--out", type=Path, default=REPO / "build" / "fhir")
    ap.add_argument("--requirements", type=Path,
                    default=REPO / "vendor" / "requirements.json")
    ap.add_argument("--packages", type=Path, default=REPO / "vendor" / "packages",
                    help="unpacked FHIR packages, for resolving {{profile.Name}}")
    ap.add_argument("--package-plan", type=Path,
                    default=REPO / "vendor" / "package-plan.json",
                    help="which ISiK packages back each endpoint")
    ap.add_argument("--check-only", action="store_true",
                    help="report what would be built, write nothing")
    ap.add_argument("--allow-incomplete", action="store_true",
                    help="do not fail when an inventory row has no authored source")
    args = ap.parse_args()

    if not args.requirements.is_file():
        print(f"{args.requirements} not found - run: make packages", file=sys.stderr)
        return 2
    req = json.loads(args.requirements.read_text(encoding="utf-8"))

    wanted = args.levels.split()
    known = ("Stufe3", "Stufe5")
    unknown = [lv for lv in wanted if lv not in known]
    if unknown:
        print(f"--levels: unknown level(s): {', '.join(unknown)}; "
              f"known: {', '.join(known)}", file=sys.stderr)
        return 2
    levels = args.level or wanted
    outside = [lv for lv in levels if lv not in wanted]
    if outside:
        print(f"--level names {', '.join(outside)}, which --levels says the "
              f"pinned suite does not carry", file=sys.stderr)
        return 2
    for lv in levels:
        if lv not in req["levels"]:
            print(f"{args.requirements}: no inventory for {lv}; it was extracted "
                  f"for {', '.join(sorted(req['levels']))}", file=sys.stderr)
            return 2

    # Most specific last, as given on the command line; see the module docstring
    # for the resulting precedence.
    roots = args.src or [REPO / "testdata" / "fhir"]

    rc = 0
    for level in levels:
        contract = load_contract(level, req)
        legacy = legacy_index(contract)
        pkgs = level_packages(args.package_plan, level)
        profiles = profile_index(args.packages, pkgs)
        if not profiles:
            # Two distinct causes, and naming the wrong one costs an operator a
            # long detour: either the plan does not say which ISiK packages this
            # endpoint carries (absent, stale or from another suite generation),
            # or it does and none of them is unpacked. Both end in the same
            # symptom - every {{profile.*}} left unresolved - so the cause is
            # reported rather than inferred by the reader.
            if not pkgs:
                print(f"  note: {args.package_plan} names no ISiK package for "
                      f"{level} - {{{{profile.*}}}} cannot be resolved "
                      f"(run `make packages` to regenerate the plan)")
            else:
                print(f"  note: no ISiK StructureDefinitions found under "
                      f"{args.packages} for {level}'s packages "
                      f"({', '.join(pkgs)}) - {{{{profile.*}}}} cannot be "
                      f"resolved (run `make packages` first)")
        rows = req["levels"][level]["resources"]
        out_dir = args.out / level
        built = missing = 0
        problems: list[str] = []

        for r in rows:
            name = f"{r['resourceType']}-{r['id']}.json"
            # Level tier before common tier, and within each tier the last --src
            # first, so that a generation-specific tree can override a shared body
            # without the shared body having to know it exists.
            candidates = ([root / level / name for root in reversed(roots)]
                          + [root / "common" / name for root in reversed(roots)])
            src = next((c for c in candidates if c.is_file()), None)
            if src is None:
                missing += 1
                # Paths printed as given, not made relative to REPO: --src may be
                # relative already, and relative_to() raises on a path that is
                # not under the repository root.
                problems.append(f"  no source for {r['resourceType']}/{r['id']} "
                                f"(looked in {len(candidates)} place(s): "
                                + ", ".join(str(c) for c in candidates) + ")")
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
            ambiguous: set[str] = set()
            doc = substitute(doc, contract, profiles, unresolved, legacy,
                             ambiguous, row_namespaces(r))
            if ambiguous:
                problems.append(
                    f"  {src.name}: ambiguous legacy key(s) "
                    + ", ".join(f"data.{k} (defined with "
                                f"{len(legacy[k])} different values)"
                                for k in sorted(ambiguous))
                    + " - name the namespace explicitly")
                rc = 1
                continue
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
        extra_srcs = {}
        for root in roots:
            for src in sorted((root / "extra" / level).glob("*.json")):
                extra_srcs[src.name] = src      # a later root wins, as above
        for src in extra_srcs.values():
            doc = json.loads(src.read_text(encoding="utf-8"))
            why = doc.pop("_provenance", None)
            if not why:
                problems.append(f"  {src.name}: no `_provenance` naming the scenario "
                                f"that requires it - required for extra/ bodies, "
                                f"which no inventory row justifies")
                rc = 1
                continue
            unresolved = set()
            ambiguous = set()
            doc = substitute(doc, contract, profiles, unresolved, legacy, ambiguous)
            if ambiguous:
                problems.append(
                    f"  {src.name}: ambiguous legacy key(s) "
                    + ", ".join(f"data.{k}" for k in sorted(ambiguous))
                    + " - name the namespace explicitly")
                rc = 1
                continue
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
