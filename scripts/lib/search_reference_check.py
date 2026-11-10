#!/usr/bin/env python3
"""Check that the seeded corpus contains the references search scenarios demand.

Why this is a separate check
----------------------------
`assertion_check.py` deliberately attributes an assertion to a resource only when
an instance-level *read* made that resource Tiger's current response body. A
search hit never does - the current body is the Bundle - so search assertions are
excluded there by design, and correctly so: `entry.resource.count() > 0` is not a
requirement on any one Patient.

But a subset of them *is* a requirement on the seed data, and it is invisible to
every other tool in this repository. Two step shapes:

    And element "subject" in all bundle resources references resource
        with ID "Patient/${data.medication-patient-id}"

    And FHIR current response body evaluates the FHIRPath
        'entry.resource.all(serviceProvider.reference
            .replaceMatches("/_history/.+","")
            .matches("\\b${data.encounter-read-in-progress-service-provider}$"))'

Both sit inside a scenario that has just searched *by that very reference*, so
`all()` over the result set is vacuous when the set is empty - and the preceding
`entry.resource.count() > 0` is what fails instead. The real requirement is
therefore: at least one seeded resource of the searched type must carry that
reference, spelled so that the search finds it. Nothing else checks this. An
unmet one surfaces in the report as "no search results were found", which reads
exactly like an IRIS search defect, and that is the one misattribution this
project must not make.

What it checks, and how strictly
--------------------------------
The regex is Tiger's own: `\\b<value>$` applied to the reference string, after
`/_history/…` is stripped. So it is reproduced verbatim rather than approximated
with `endswith()` - an earlier throwaway version of this check used
`r.endswith("/" + value)`, which double-counts the type prefix for the contract
values that already carry one (`encounter-read-in-progress-service-provider` is
`Organization/Organization-Read-Example`, not a bare id) and reported 23 false
positives in Stufe 3 alone.

The element path is resolved with `assertion_check.resolve`, so FHIR choice
elements work: `medication` finds `medicationReference`, which is how
`MedicationRequest.medication[x]` is actually spelled in the JSON, and a path
walker without that rule reports every Medication* resource as missing its
reference when all of them carry it.

Coverage is per (level, resource type, element path, required value): the check
is satisfied by *one* seeded resource of that type, because that is what makes
the search non-empty. It does not verify that every resource returned satisfies
it - that is what `all()` says, and evaluating it would require knowing which
resources the SUT returns for a given search, which is the thing under test.

Scope
-----
Only scenarios the run will actually execute: the level's tag expression must
select them (default `@Stufe<n> and (not @Optional)`), and `@Disabled` features
are skipped. Out-of-scope requirements are counted and listed under `--all`
rather than silently dropped, since a future tag expression can bring them into
scope.

Which levels are checked by default is the caller's decision, passed as
`--levels`: suite release 3.0.0 removed ISiK Level 3, and defaulting to both here
would report `no rendered resources under build/fhir/Stufe3` and exit 2 on a
round that is correct.

Usage
-----
    scripts/lib/search_reference_check.py --level Stufe5 --data-root build/fhir
    scripts/lib/search_reference_check.py --level Stufe5 -v      # list satisfied too
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from assertion_check import resolve                      # noqa: E402
from feature_parser import (                             # noqa: E402
    RE_DATA_KEY,
    effective_tags,
    load_features,
    parse_tag_expression,
)

REPO = Path(__file__).resolve().parents[2]

# element "X" in all bundle resources references resource with ID "Y"
#
# Y is sometimes a bare id (`${data.patient-read-id}`) and sometimes type-prefixed
# (`Patient/${data.medication-patient-id}`). Both are kept verbatim: Tiger anchors
# the match with `\b`, so a bare id matches "Patient/Patient-Read-Example" while a
# prefixed one additionally requires the prefix to be spelled that way.
RE_BUNDLE_REF = re.compile(
    r'element "([^"]+)" in all bundle resources references resource with ID "([^"]+)"')

# entry.resource.all(<path>.replaceMatches("/_history/.+","").matches("\b<value>$"))
#
# Quoting varies across the corpus - the outer FHIRPath literal may be single- or
# double-quoted and the inner arguments take whichever the outer does not - so both
# are accepted on both. The `\\b` in the feature text is an escaped backslash inside
# a Gherkin string; by the time the parser hands it over it is a literal `\b`, which
# is why the pattern matches `\\\\?b` rather than assuming one spelling.
#
# Groups are named throughout, not numbered: the quote back-references are groups
# too, so `group(3)` here is the closing quote character rather than the value, and
# reading it positionally reported every one of these assertions as unsatisfied
# against a required value of `"` - eight bogus findings that each pointed at a
# resource whose own "seeded:" line showed the correct reference.
RE_ALL_MATCHES = re.compile(
    r"entry\.resource\.all\(\s*"
    r"(?P<element>[A-Za-z_.<>]+)\.reference"
    r"\.replaceMatches\((?P<q1>['\"])/_history/\.\+(?P=q1)\s*,\s*(?P=q1)(?P=q1)\)"
    r"\.matches\((?P<q2>['\"])\\\\?b(?P<value>.+?)\$(?P=q2)\)\s*\)")

# The type being searched, from the step that precedes the assertion. `all()` is
# over the bundle's entries, so the resources that must carry the reference are of
# the type the search targets - not of the type the reference points at.
#
# Three URL shapes are a search of a collection, and the suite writes all three:
# `/Encounter/?date=...` (383 occurrences in 3.0.9), `/Encounter?_count=10` (12,
# introduced by the Connect module) and `/DocumentReference` with no query at all
# (11). A read is `/Encounter/${id}` and must not match, so the type may be
# followed only by the query or by the end of the URL. `metadata` is excluded
# because the CapabilityStatement step has that shape and is not a search; left in,
# it would satisfy the "preceding search" test and so could mask a step shape this
# check really does not model.
RE_SEARCH_URL = re.compile(
    r'(?:resource at|request to) "http://fhirserver/(?!metadata")([A-Za-z]+)/?(?=\?|")')

# RE_DATA_KEY is imported from feature_parser rather than written again here: it
# captures the *qualified* key (`data.patient-read-id` under 2.x, which loads
# every file under one baseKey, and e.g. `medikation.medication-patient-id` under
# 3.x, which gives each module its own). requirements.json keys its dataKeys by
# exactly that qualified string, so one pattern keeps this check and the seeder
# resolving a placeholder through the same name. A second copy of the pattern is
# what made this check report 0 requirements against 3.0.9 while reporting
# success.
RE_OUTLINE = re.compile(r"<([^<>]+)>")

# Some steps write a `$` *after* the closing brace of the placeholder in the
# plain-string form of the assertion. Three in 2.7.0:
#
#   Stufe3/Medikation/MedicationRequest-Search.feature:71
#       ... references resource with ID "Encounter/${data.medication-encounter-id}$"
#   Stufe3/Medikation/MedicationStatement-Search.feature:83
#   Stufe5/Medikation/MedicationStatement-Search.feature:57
#       ... references resource with ID "Patient/${data.medication-patient-id}$"
#
# One survives in 3.0.9 - the two Stufe 3 sites went with the level, the Stufe 5
# one was carried over unchanged apart from the new namespace:
#
#   Stufe5/Medikation/MedicationStatement-Search.feature:58
#       ... references resource with ID "Patient/${medikation.medication-patient-id}$"
#
# In the FHIRPath form the `$` belongs there - it anchors the `matches()` regex. In
# this form the value is compared as a string, so the anchor is a literal character
# in the expected id and no resource can carry it. The suite's own authors hit this:
# Stufe3/Basis/Encounter-Read-In-Progress.feature:44 is the same typo *commented
# out* and replaced on line 45 by the identical step without the `$`.
#
# The anchor is stripped so the underlying data requirement is still checked - the
# reference genuinely has to be there, and it is, in all three cases - and the fact
# is recorded on the entry so that a run failing these three scenarios is
# attributed to the test suite and not to IRIS. Reported as an upstream defect in
# docs/en/05-testdata.md rather than silently healed.
STRAY_ANCHOR = "documented at scripts/lib/search_reference_check.py:STRAY_ANCHOR"


def expand(text: str, row: dict[str, str]) -> str:
    """Substitute one Examples row's values into an outline step."""
    return RE_OUTLINE.sub(lambda m: row.get(m.group(1), m.group(0)), text)


def contract(level: str, req: dict) -> dict[str, str]:
    """key -> ID, from the suite's own test-data contract.

    Keyed by the qualified `<namespace>.<key>`, which is the string the feature
    files write and the string requirements.json records.

    Read out of requirements.json rather than the YAML so that this check and the
    seed data resolve `${<ns>.*}` through the same path; a discrepancy between the
    two would make the check pass on values the suite never sends.
    """
    out: dict[str, str] = {}
    for f in req["features"]:
        if f["level"] != level:
            continue
        for key, info in f["dataKeys"].items():
            if info["value"] not in (None, ""):
                out[key] = str(info["value"])
    return out


def resolve_keys(text: str, values: dict[str, str]) -> tuple[str, set[str]]:
    """Replace `${<ns>.<key>}` with the contract value; report keys with none."""
    missing: set[str] = set()

    def repl(m: re.Match) -> str:
        key = m.group(1)
        if key not in values:
            missing.add(key)
            return m.group(0)
        return values[key]

    return RE_DATA_KEY.sub(repl, text), missing


def requirements(features_root: Path, level: str, predicate,
                 values: dict[str, str], include_out_of_scope: bool) -> list[dict]:
    """One entry per (type, path, value) a search scenario requires of the corpus."""
    out: list[dict] = []
    for feature in load_features(features_root):
        if feature.level != level:
            continue
        disabled = any(t.lower() == "@disabled" for t in feature.tags)

        for scenario in feature.scenarios:
            if scenario.is_precondition:
                continue
            # An outline is expanded per row so that `<entrytype>` and
            # `<searchParameter>` become real element paths. Rows carry their own
            # tags via the Examples block, so scope is decided per row - the suite
            # has Examples blocks that are @Optional under a mandatory scenario.
            if scenario.is_outline:
                variants = [
                    (dict(zip(ex.header, row)), effective_tags(feature, scenario, ex))
                    for ex in scenario.examples for row in ex.rows
                ]
            else:
                variants = [({}, effective_tags(feature, scenario))]

            for row, tags in variants:
                in_scope = predicate(tags) and not disabled
                if not in_scope and not include_out_of_scope:
                    continue

                searched: str | None = None
                for step in scenario.steps:
                    text = expand(step.full, row)

                    url = RE_SEARCH_URL.search(text)
                    if url:
                        searched = url.group(1)
                        continue

                    found: list[tuple[str, str]] = []
                    for element, target in RE_BUNDLE_REF.findall(text):
                        found.append((element, target))
                    for m in RE_ALL_MATCHES.finditer(text):
                        found.append((m.group("element"), m.group("value")))
                    if not found:
                        continue
                    if searched is None:
                        # An assertion over bundle entries with no preceding search
                        # in the same scenario. Reported rather than skipped: it
                        # would mean a step shape this check does not model.
                        out.append({"level": level, "type": None, "element": None,
                                    "value": None, "inScope": in_scope,
                                    "feature": feature.rel, "scenario": scenario.name,
                                    "line": step.line, "unmodelled": text})
                        continue
                    for element, target in found:
                        value, missing = resolve_keys(target, values)
                        # A stray `$` after the placeholder - see STRAY_ANCHOR.
                        stray = value.endswith("$")
                        out.append({
                            "level": level, "type": searched, "element": element,
                            "value": value.rstrip("$") if stray else value,
                            "strayAnchor": stray, "inScope": in_scope,
                            "feature": feature.rel, "scenario": scenario.name,
                            "line": step.line,
                            "unresolvedKeys": sorted(missing),
                        })
    return out


def corpus(data_root: Path, level: str) -> dict[str, list[dict]]:
    """resourceType -> the rendered bodies of that type."""
    out: dict[str, list[dict]] = {}
    root = data_root / level
    if not root.is_dir():
        return out
    for path in sorted(root.glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        rtype = doc.get("resourceType")
        if rtype:
            doc.setdefault("_file", path.name)
            out.setdefault(rtype, []).append(doc)
    return out


def references(docs: list[dict], element: str) -> list[tuple[str, str]]:
    """(file, reference string) for every `<element>.reference` in the corpus.

    `resolve` handles the choice-element spelling, so `medication` reaches
    `medicationReference`. Both `<element>.reference` and a bare string at
    `<element>` are collected, the latter because `entry.resource.all(x.reference…)`
    and `element "x" … references resource with ID` name the same thing and only
    the first spells `.reference` out.
    """
    out: list[tuple[str, str]] = []
    for doc in docs:
        for node in resolve(doc, element):
            if isinstance(node, dict) and isinstance(node.get("reference"), str):
                out.append((doc["_file"], node["reference"]))
            elif isinstance(node, str):
                out.append((doc["_file"], node))
    return out


def check(reqs: list[dict], data: dict[str, list[dict]]) -> list[dict]:
    """Add a verdict to each requirement, deduplicated by (type, element, value)."""
    seen: dict[tuple, dict] = {}
    for r in reqs:
        if r.get("unmodelled"):
            seen[("unmodelled", r["feature"], r["line"])] = {**r, "satisfied": False}
            continue
        key = (r["type"], r["element"], r["value"])
        entry = seen.setdefault(key, {**r, "sites": [], "straySites": []})
        site = f"{r['feature']}:{r['line']}"
        entry["sites"].append(site)
        entry["inScope"] = entry["inScope"] or r["inScope"]
        # Per site, not per entry: stripping the anchor makes a defective step
        # dedup onto the same (type, element, value) as its correct siblings -
        # MedicationStatement.subject is asserted twice in one feature, once each
        # way - and flagging the whole entry would misreport the correct site as
        # unpassable.
        if r["strayAnchor"]:
            entry["straySites"].append(site)

    for entry in seen.values():
        if entry.get("unmodelled") or entry["type"] is None:
            continue
        found = references(data.get(entry["type"], []), entry["element"])
        # Tiger's own test, reproduced: `\b<value>$` against the reference with any
        # `/_history/…` suffix removed.
        pattern = re.compile(r"\b" + re.escape(entry["value"]) + r"$")
        hits = [(f, re.sub(r"/_history/.+", "", ref)) for f, ref in found]
        entry["seeded"] = sorted({f"{f}: {ref}" for f, ref in hits})
        entry["satisfied"] = any(pattern.search(ref) for _, ref in hits)
    return list(seen.values())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--level", choices=("Stufe3", "Stufe5"), action="append",
                    help="check only this level (repeatable; default: --levels)")
    ap.add_argument("--levels", default="Stufe3 Stufe5",
                    help="the levels the pinned suite carries, space-separated "
                         "(default: both, i.e. a 2.x suite)")
    ap.add_argument("--features", type=Path,
                    default=REPO / "vendor" / "upstream" / "features")
    ap.add_argument("--data-root", type=Path, default=REPO / "build" / "fhir")
    ap.add_argument("--requirements", type=Path,
                    default=REPO / "vendor" / "requirements.json")
    ap.add_argument("--tags-stufe3", default="@Stufe3 and (not @Optional)")
    ap.add_argument("--tags-stufe5", default="@Stufe5 and (not @Optional)")
    ap.add_argument("--all", action="store_true",
                    help="include scenarios the tag expression excludes")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="list satisfied requirements as well")
    ap.add_argument("--json-report", type=Path)
    args = ap.parse_args()

    if not args.requirements.is_file():
        print(f"{args.requirements} not found - run: make packages", file=sys.stderr)
        return 2
    req = json.loads(args.requirements.read_text(encoding="utf-8"))
    exprs = {"Stufe3": args.tags_stufe3, "Stufe5": args.tags_stufe5}

    wanted = args.levels.split()
    unknown = [lv for lv in wanted if lv not in exprs]
    if unknown:
        print(f"--levels: unknown level(s): {', '.join(unknown)}; "
              f"known: {', '.join(sorted(exprs))}", file=sys.stderr)
        return 2
    selected = args.level or wanted
    outside = [lv for lv in selected if lv not in wanted]
    if outside:
        print(f"--level names {', '.join(outside)}, which --levels says the "
              f"pinned suite does not carry", file=sys.stderr)
        return 2

    rc = 0
    report: list[dict] = []
    for level in selected:
        values = contract(level, req)
        predicate = parse_tag_expression(exprs[level])
        reqs = requirements(args.features, level, predicate, values, args.all)
        data = corpus(args.data_root, level)
        if not data:
            print(f"{level}: no rendered resources under {args.data_root / level} "
                  f"- run: scripts/lib/build_testdata.py", file=sys.stderr)
            return 2

        results = check(reqs, data)
        report.extend(results)

        in_scope = [r for r in results if r["inScope"] and not r.get("unmodelled")]
        bad = [r for r in in_scope if not r["satisfied"]]
        unmodelled = [r for r in results if r.get("unmodelled")]
        stray = [r for r in in_scope if r["straySites"]]

        print(f"{level}: {len(in_scope)} search-reference requirement(s), "
              f"{len(in_scope) - len(bad)} satisfied, {len(bad)} unsatisfied")

        for r in sorted(bad, key=lambda x: (x["type"], x["element"])):
            print(f"  UNSATISFIED {r['type']}.{r['element']}.reference "
                  f"must match \\b{r['value']}$")
            print(f"              required by {', '.join(r['sites'][:3])}"
                  + (f" (+{len(r['sites']) - 3} more)" if len(r["sites"]) > 3 else ""))
            print(f"              seeded: "
                  + ("; ".join(r["seeded"][:4]) if r["seeded"] else "(none)"))
            if r["unresolvedKeys"]:
                print(f"              unresolved contract key(s): "
                      f"{', '.join(r['unresolvedKeys'])}")
            rc = 1

        if args.verbose:
            for r in sorted((x for x in in_scope if x["satisfied"]),
                            key=lambda x: (x["type"], x["element"])):
                print(f"  ok          {r['type']}.{r['element']}.reference "
                      f"-> \\b{r['value']}$")

        # Not a failure of the seed data, and not silent either: these three
        # scenarios cannot pass against any conformant server, so a reader of the
        # run's results needs to know before attributing them to IRIS.
        for r in sorted(stray, key=lambda x: (x["type"], x["element"])):
            print(f"  upstream    {r['type']}.{r['element']} expected id is written "
                  f"\"{r['value']}$\" - a regex anchor in a plain-string comparison, "
                  f"so this step fails regardless of the data")
            print(f"              {', '.join(r['straySites'])}; the underlying data "
                  f"requirement is "
                  + ("satisfied" if r["satisfied"] else "NOT satisfied"))

        for r in unmodelled:
            print(f"  UNMODELLED  {r['feature']}:{r['line']}: bundle assertion with "
                  f"no preceding search in the same scenario")
            print(f"              {r['unmodelled'][:100]}")
            rc = 1

    if args.json_report:
        args.json_report.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
