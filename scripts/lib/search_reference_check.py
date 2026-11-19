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

A third shape: the search criterion itself
------------------------------------------
The two shapes above are *assertions*, and both name a reference. A search can
also demand data through its own query string, with no assertion naming it:

    Scenario: Search for the RiskAssessment that belong to a Patient, by Risk
      Then Get FHIR resource at "http://fhirserver/RiskAssessment/?risk=
          http://terminology.hl7.org/CodeSystem/risk-probability%7Chigh&patient=..."
      And FHIR current response body evaluates the FHIRPath
          'entry.resource.count() > 0' ...

Nothing here says which element `risk` searches - `RiskAssessment.prediction.
qualitativeRisk`, per the SearchParameter definition in `hl7.fhir.r4.core` - so a
corpus can satisfy every reference requirement above and still have no resource
this search can find. Measured: it did. The seeded RiskAssessment carried no
`prediction` at all in rounds A and C, the search returned `total 0` in round C,
and the case was charged to IRIS. In round A the identical request returned two
resources and the case passed - both of them written earlier *in that same run*
by the suite's own Create and Update scenarios, which 2.7.0 happened to execute
before the searches and 3.0.9 after. So the corpus never satisfied the scenario
in either round; one round was merely lucky in the ordering.

Token criteria are therefore mined from the query string of the search that
precedes an `entry.resource.count() > 0` assertion - only there, because only
there does the suite require the result to be non-empty - resolved to an element
path through the SearchParameter definitions in the unpacked FHIR packages, and
checked against the corpus like any other requirement.

Conservative by construction, because this check gates `make all`: a criterion is
reported UNSATISFIED only when the parameter resolves to one unambiguous element
path and no resource of the searched type matches it. A `SearchParameter`
expression this resolver does not model (a `where(...)` filter, an `as` cast, a
multi-path union with no alternative on the searched type) is counted and listed
as skipped, never guessed at and never failed on. Control parameters that address
identity or paging rather than content - `_id`, `_count`, `_include`, `_sort` and
their siblings - are out of scope here; `_id` is already covered by the "response
bundle contains resource with ID" assertions.

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
import dataclasses
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, unquote_plus

sys.path.insert(0, str(Path(__file__).resolve().parent))

from assertion_check import resolve                      # noqa: E402
from feature_parser import (                             # noqa: E402
    RE_DATA_KEY,
    effective_tags,
    load_features,
    parse_tag_expression,
)
from requirements_corpus import testdata_contract        # noqa: E402

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

# The same step, captured whole, so the query string is available for the token
# criteria. Kept separate from RE_SEARCH_URL rather than widening it: that pattern
# decides what counts as a search of a collection, and it is load-bearing for the
# reference shapes above.
RE_SEARCH_STEP = re.compile(
    r'(?:resource at|request to) "http://fhirserver/(?!metadata")([A-Za-z]+)/?'
    r'(?:\?(?P<query>[^"]*))?"')

# The other way the suite sends a search: POST to `<Type>/_search` with the
# criteria in a Gherkin table instead of in the URL. `RE_SEARCH_URL` cannot match
# it - the type is followed by `/_search"`, not by `?` or `"` - and that is not a
# flaw in that pattern but the reason this one exists: 41 steps in the 2.7.0
# tree and 49 in the 3.0.9 tree take this shape (counted on both extracted
# trees), and every criterion in them was invisible to this gate. One of those
# steps is mandatory - `Observation` by `_tag` in
# Stufe5/Basis/Observation-Statuses-Search.feature, present in 3.0.9 as well and
# sent in round G, as that round's traffic log shows - so a round could charge the
# system under test for an empty result set that no seeded Observation could have
# filled.
RE_SEARCH_POST = re.compile(
    r'request to "http://fhirserver/(?!metadata")([A-Za-z]+)/_search"')

# The suite's own words for "this search must find something". Only a criterion
# guarded by this assertion is a requirement on the corpus: a search that is
# allowed to come back empty says nothing about the seed data.
COUNT_NONEMPTY = "entry.resource.count() > 0"

# Parameters that address identity, paging or presentation rather than resource
# content. `_id` is covered by the "response bundle contains resource with ID"
# assertions, which name the required id explicitly; the rest do not constrain
# the corpus at all.
CONTROL_PARAMS = {
    "_id", "_count", "_include", "_revinclude", "_sort", "_summary", "_total",
    "_format", "_elements", "_contained", "_containedType", "_has", "_type",
    "_lastUpdated", "_profile", "_security", "_text", "_content", "_query",
    "_filter", "_score", "_explain",
}

# A SearchParameter expression this resolver accepts: a plain dotted path rooted
# at the searched type. Anything else - `where(...)`, an `as` cast, `resolve()`,
# `extension(...)` - is skipped and reported, because guessing an element path
# would make this gate fail runs over its own modelling error.
RE_PLAIN_PATH = re.compile(r"^[A-Za-z][A-Za-z0-9]*(?:\.[a-z][A-Za-z0-9]*)+$")

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


def table_query(step, row: dict[str, str]) -> tuple[str, str]:
    """A POST-search step's parameter table as the query string it will send.

    Returns the query and, when the table is not the two-row shape every site in
    both pinned suite versions writes, an empty query and the reason - the same
    discipline the rest of this module follows, because a table whose extra rows
    were silently dropped would be a criterion this gate claims to have checked
    and did not.

    A cell escapes the token separator as `\\|`, `|` being the cell delimiter, so
    it is unescaped here; the result is read by `TokenCriterion.of_query` in the
    same form a URL query arrives in.
    """
    if len(step.table) != 2:
        return "", f"parameter table has {len(step.table)} row(s), not header + one"
    header = [expand(c, row).strip() for c in step.table[0]]
    pairs = []
    for name, cell in zip(header, step.table[1]):
        value = expand(cell, row).strip().replace("\\|", "|")
        if name and value:
            pairs.append(f"{name}={value}")
    return "&".join(pairs), ""


@dataclasses.dataclass(frozen=True)
class TokenParameters:
    """The token SearchParameter definitions the pinned packages ship.

    Held as one object rather than passed along because they are read once per
    run and every lookup below is against that one reading of them.
    """

    definitions: dict[tuple[str, str], dict]

    @property
    def is_empty(self) -> bool:
        """Whether no definition was read - the packages are not unpacked yet."""
        return not self.definitions

    @staticmethod
    def of(packages_root: Path) -> "TokenParameters":
        """The definitions under one root of unpacked packages.

        Indexed by (resource type, parameter code).

        Read from the unpacked FHIR packages, which is where the authority for
        "`risk` searches `RiskAssessment.prediction.qualitativeRisk`" actually lives.
        Only `type: token` is indexed - this check models no other search type.

        `hl7.fhir.r4.examples` ships copies of the core definitions; a package whose
        name ends in `.examples` is therefore skipped, so that the specification's own
        definition is the one used rather than whichever sorted last.
        """
        out: dict[tuple[str, str], dict] = {}
        if not packages_root.is_dir():
            return TokenParameters(out)
        for pkg in sorted(packages_root.iterdir()):
            if not pkg.is_dir() or pkg.name.split("#")[0].endswith(".examples"):
                continue
            for path in sorted(pkg.rglob("*.json")):
                try:
                    doc = json.loads(path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                    continue
                if (not isinstance(doc, dict)
                        or doc.get("resourceType") != "SearchParameter"):
                    continue
                if doc.get("type") != "token" or not doc.get("code"):
                    continue
                for base in doc.get("base") or []:
                    out.setdefault((base, doc["code"]), {
                        "expression": doc.get("expression") or "",
                        "url": doc.get("url") or "",
                        "package": pkg.name,
                    })
        return TokenParameters(out)

    def token_path(self, rtype: str, code: str) -> tuple[str | None, str]:
        """The element path `<rtype>.<code>` searches, or None and why not.

        Returns the path *without* the leading resource type, which is the form
        `assertion_check.resolve` takes. A union expression is accepted when exactly
        one alternative is a plain path rooted at the searched type - `Observation.code
        | Observation.component.code` is two, so it is skipped rather than halved.
        """
        spec = self.definitions.get((rtype, code))
        root = rtype
        if spec is None:
            # The parameters every resource has - `_tag`, `_security`, `_profile` -
            # are defined once with base `Resource` and an expression rooted there,
            # so a per-type lookup can never find them. `_tag` is the one of those
            # this gate must resolve: the suite searches by it and asserts the result
            # is non-empty, which is a requirement on the corpus like any other.
            spec = self.definitions.get(("Resource", code))
            root = "Resource"
        if spec is None:
            return None, "no token SearchParameter for this type in the pinned packages"
        expr = " ".join((spec["expression"] or "").split())
        if not expr:
            return None, "the SearchParameter carries no expression"
        alts = [a.strip() for a in expr.split("|")]
        plain = [a for a in alts
                 if RE_PLAIN_PATH.match(a) and a.split(".", 1)[0] == root]
        if len(plain) != 1:
            return None, f"expression not modelled by this check: {expr}"
        return plain[0].split(".", 1)[1], ""


@dataclasses.dataclass(frozen=True)
class TokenCriterion:
    """One token-shaped criterion of a search query, and what satisfies it.

    A comma-separated value list is FHIR's OR, so any one of `codes` satisfies
    the criterion; `system` is the one the `system|code` alternatives named.
    """

    parameter: str
    system: str | None
    codes: list[str]

    @property
    def value(self) -> str:
        """The criterion as the query spells it, for the report."""
        return (f"{self.system}|" if self.system else "") + ",".join(self.codes)

    @staticmethod
    def of_query(query: str) -> list["TokenCriterion"]:
        """One criterion per token-shaped `name=value` pair of a query string.

        A modifier or prefix (`identifier:of-type`, `date=gt...`)
        is left to the caller's parameter lookup to reject: a modified parameter is not
        the plain token match this check models, so the name is kept verbatim and will
        simply not resolve.
        """
        out: list["TokenCriterion"] = []
        for pair in query.split("&"):
            if "=" not in pair:
                continue
            name, _, raw = pair.partition("=")
            name = unquote_plus(name).strip()
            if not name or name in CONTROL_PARAMS:
                continue
            # Form-encoded, so `+` is a space and a literal plus arrives as `%2B`.
            # `unquote` instead of `unquote_plus` here reported
            # Stufe5/Basis/Patient-Search-Extended.feature:67 as unsatisfied, because
            # it searches `telecom=030+1234567` while the seeded Patient carries
            # `030 1234567` - and that scenario passes, the suite's own next step
            # asserting `telecom = '030 1234567'` with the space.
            value = unquote_plus(raw).strip()
            if not value or "$" in value:        # an unresolved contract placeholder
                continue
            system: str | None = None
            codes: list[str] = []
            for alternative in value.split(","):
                alternative = alternative.strip()
                if "|" in alternative:
                    sys_part, _, code_part = alternative.rpartition("|")
                    if not code_part:            # `system|` is "any code in system"
                        continue
                    system = sys_part or None
                    codes.append(code_part)
                else:
                    codes.append(alternative)
            if codes:
                out.append(TokenCriterion(parameter=name, system=system,
                                         codes=codes))
        return out

    def satisfied_by(self, node) -> bool:
        """Does one element value of a seeded resource satisfy this criterion?"""
        return any(self.matches(node, code) for code in self.codes)

    def matches(self, node, code: str) -> bool:
        """Does one element value carry this system and the given code?

        The three spellings a token parameter can land on, per the FHIR search
        specification: a CodeableConcept (match any of its codings), a Coding or an
        Identifier (match the pair), and a primitive `code` (match the code alone -
        a primitive carries no system, so a criterion that names one still matches
        here, and the system is checked wherever the data can express it).
        """
        if isinstance(node, str):
            return node == code
        if not isinstance(node, dict):
            return False
        if isinstance(node.get("coding"), list):
            return any(self.matches(c, code) for c in node["coding"])
        # Coding spells the code `code`; Identifier and ContactPoint spell it `value`.
        have = node.get("code") if "code" in node else node.get("value")
        if have != code:
            return False
        return self.system is None or node.get("system") == self.system


@dataclasses.dataclass(frozen=True)
class Level:
    """The level being checked, and everything fixed for the whole of it.

    The feature tree, the tag predicate that decides scope, the contract values a
    placeholder resolves through and the SearchParameter definitions are all
    settled before a single scenario is read, so they are held here rather than
    threaded through the walk over the features.
    """

    name: str
    features: Path
    predicate: object
    values: dict[str, str]
    params: TokenParameters

    def requirements(self) -> list[dict]:
        """One entry per (type, path, value) a search scenario requires of the corpus.

        Every scenario is described, in or out of the level's scope, and each entry
        records which it was under `inScope`. Selecting is left to the caller: no
        entry is ever merged with another, so dropping the out-of-scope ones here or
        after the fact comes to the same thing, and the predicate then has one
        meaning in this module instead of two.
        """
        features_root, level, predicate = self.features, self.name, self.predicate
        values, params = self.values, self.params
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
                        (dict(zip(ex.header, row)),
                         effective_tags(feature, scenario, ex))
                        for ex in scenario.examples for row in ex.rows
                    ]
                else:
                    variants = [({}, effective_tags(feature, scenario))]

                for row, tags in variants:
                    in_scope = predicate(tags) and not disabled

                    searched: str | None = None
                    query: str = ""
                    for step in scenario.steps:
                        text = expand(step.full, row)

                        url = RE_SEARCH_URL.search(text)
                        if url:
                            searched = url.group(1)
                            step_match = RE_SEARCH_STEP.search(text)
                            query = ((step_match.group("query") or "")
                                     if step_match else "")
                            continue

                        post = RE_SEARCH_POST.search(text)
                        if post:
                            searched = post.group(1)
                            query, why = table_query(step, row)
                            if why:
                                out.append({"kind": "reference",
                                            "level": level, "type": None,
                                            "element": None, "value": None,
                                            "inScope": in_scope,
                                            "feature": feature.rel,
                                            "scenario": scenario.name,
                                            "line": step.line,
                                            "unmodelled": f"{text} [{why}]"})
                            continue

                        # The token criteria of the search this step requires to be
                        # non-empty. Emitted here rather than at the search step so
                        # that an unguarded search - one the suite lets come back
                        # empty - produces no requirement on the corpus.
                        if (COUNT_NONEMPTY in text and searched and query
                                and not params.is_empty):
                            resolved_query, missing = resolve_keys(query, values)
                            for criterion in TokenCriterion.of_query(resolved_query):
                                path, why = params.token_path(searched,
                                                              criterion.parameter)
                                out.append({
                                    "kind": "token", "level": level, "type": searched,
                                    "element": path, "parameter": criterion.parameter,
                                    "system": criterion.system,
                                    "codes": criterion.codes,
                                    "value": criterion.value,
                                    "strayAnchor": False, "inScope": in_scope,
                                    "feature": feature.rel, "scenario": scenario.name,
                                    "line": step.line,
                                    "unresolvedKeys": sorted(missing),
                                    "skipped": None if path else why,
                                })
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
                            out.append({"kind": "reference",
                                        "level": level, "type": None,
                                        "element": None, "value": None,
                                        "inScope": in_scope,
                                        "feature": feature.rel,
                                        "scenario": scenario.name,
                                        "line": step.line, "unmodelled": text})
                            continue
                        for element, target in found:
                            value, missing = resolve_keys(target, values)
                            # A stray `$` after the placeholder - see STRAY_ANCHOR.
                            stray = value.endswith("$")
                            out.append({
                                "kind": "reference",
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
        key = (r.get("kind"), r["type"], r["element"], r["value"])
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
        if entry.get("kind") == "token":
            docs = data.get(entry["type"], [])
            criterion = TokenCriterion(parameter=entry["parameter"],
                                       system=entry["system"],
                                       codes=entry["codes"])
            if entry.get("skipped"):
                # Not modelled, so it states nothing either way. Left without a
                # verdict so that neither the count of satisfied requirements nor
                # the exit status can rest on it.
                entry["satisfied"] = None
                entry["seeded"] = []
                continue
            hits, carriers = [], []
            for doc in docs:
                for node in resolve(doc, entry["element"]):
                    if criterion.satisfied_by(node):
                        hits.append(doc["_file"])
                    carriers.append(
                        f"{doc['_file']}: "
                        + json.dumps(node, ensure_ascii=False, sort_keys=True)[:160])
            entry["seeded"] = sorted(set(carriers))
            # A type absent from the corpus is a distinct condition from a type
            # present but carrying the wrong code, and the message says which.
            entry["typeSeeded"] = bool(docs)
            entry["satisfied"] = bool(hits)
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
    ap.add_argument("--packages", type=Path, default=REPO / "vendor" / "packages",
                    help="unpacked FHIR packages, for the SearchParameter "
                         "definitions the token criteria resolve through")
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

    # Absent packages are a warning, not an error: the reference requirements are
    # the older contract of this check and do not need them, and `make packages`
    # is what populates the directory. Without it the token criteria are simply
    # not mined, and the summary says so rather than reporting zero of them.
    params = TokenParameters.of(args.packages)
    if params.is_empty:
        print(f"note: no token SearchParameter definitions under {args.packages} "
              f"- search criteria are not checked this run (run: make packages)",
              file=sys.stderr)

    rc = 0
    report: list[dict] = []
    for level in selected:
        being = Level(name=level, features=args.features,
                      predicate=parse_tag_expression(exprs[level]),
                      values=testdata_contract(level, req), params=params)
        reqs = being.requirements()
        if not args.all:
            reqs = [r for r in reqs if r["inScope"]]
        data = corpus(args.data_root, level)
        if not data:
            print(f"{level}: no rendered resources under {args.data_root / level} "
                  f"- run: scripts/lib/build_testdata.py", file=sys.stderr)
            return 2

        results = check(reqs, data)
        report.extend(results)

        in_scope = [r for r in results if r["inScope"] and not r.get("unmodelled")]
        refs = [r for r in in_scope if r.get("kind") != "token"]
        toks = [r for r in in_scope if r.get("kind") == "token"]
        skipped = [r for r in toks if r.get("skipped")]
        toks = [r for r in toks if not r.get("skipped")]
        bad = [r for r in refs if not r["satisfied"]]
        bad_toks = [r for r in toks if not r["satisfied"]]
        unmodelled = [r for r in results if r.get("unmodelled")]
        stray = [r for r in refs if r["straySites"]]

        print(f"{level}: {len(refs)} search-reference requirement(s), "
              f"{len(refs) - len(bad)} satisfied, {len(bad)} unsatisfied")
        print(f"{level}: {len(toks)} search-criterion requirement(s), "
              f"{len(toks) - len(bad_toks)} satisfied, {len(bad_toks)} unsatisfied"
              + (f", {len(skipped)} not modelled" if skipped else ""))

        for r in sorted(bad_toks, key=lambda x: (x["type"], x["parameter"])):
            print(f"  UNSATISFIED {r['type']}?{r['parameter']}={r['value']} matches "
                  f"no seeded resource at {r['type']}.{r['element']}")
            print(f"              required by {', '.join(r['sites'][:3])}"
                  + (f" (+{len(r['sites']) - 3} more)" if len(r["sites"]) > 3 else "")
                  + ", which asserts the result is not empty")
            if not r.get("typeSeeded"):
                print(f"              seeded: no {r['type']} resource at all")
            else:
                print(f"              seeded at that element: "
                      + ("; ".join(r["seeded"][:3]) if r["seeded"] else "(nothing)"))
            if r["unresolvedKeys"]:
                print(f"              unresolved contract key(s): "
                      f"{', '.join(r['unresolvedKeys'])}")
            rc = 1

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
            for r in sorted((x for x in refs if x["satisfied"]),
                            key=lambda x: (x["type"], x["element"])):
                print(f"  ok          {r['type']}.{r['element']}.reference "
                      f"-> \\b{r['value']}$")
            for r in sorted((x for x in toks if x["satisfied"]),
                            key=lambda x: (x["type"], x["parameter"])):
                print(f"  ok          {r['type']}?{r['parameter']}={r['value']} "
                      f"-> {r['type']}.{r['element']}")
            for r in sorted(skipped, key=lambda x: (x["type"], x["parameter"])):
                print(f"  not modelled {r['type']}?{r['parameter']}: {r['skipped']}")

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
