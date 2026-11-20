#!/usr/bin/env python3
"""Turn the test suite's own feature files into the test-data specification.

What problem this solves
------------------------
The ISiK test suite seeds nothing. Every scenario reads resources that must
already exist in the system under test, addressed by IDs supplied through
``${data.<key>}`` configuration keys. The suite ships the *keys* (11 YAML files,
287 of them across both levels) but the *content* those resources must have is
stated only as German or English prose inside each feature file's
``@Precondition`` / ``@vorbedingung`` scenario - 217 such blocks. That prose is
the authoritative field-level specification for our fabricated test data:

    * Status: active
    * Prefix: Prof.
    * Last name: Graf von und zu Mustermann
    * Statutory health insurance number: X485231029

Reading 217 prose blocks by hand and hoping nothing was missed is not a method
that survives an audit. This script produces, mechanically and repeatably:

  requirements.json          machine-readable, consumed by 50-seed.sh (which
                             refuses to seed an unknown key) and by the gap
                             report (which cites the precondition prose when a
                             scenario fails)
  REQUIREMENTS-Stufe3.md     per-module tables for a human author/reviewer,
  REQUIREMENTS-Stufe5.md     with the prose quoted verbatim
  RESOURCE-INVENTORY.md      the fabrication work list: every resource that must
                             exist, its ID, its FHIR type, the profile it must
                             conform to, and which features need it

Deliberately not a prose-to-JSON translator
-------------------------------------------
The tempting next step - parsing "* Gender: male" into
``{"gender": "male"}`` - is not taken. The prose is irregular by nature: it mixes
two languages, nests three levels deep with two bullet characters, and expresses
requirements as sentences ("Referenced medication: an ISiKMedication resource with
ATC code V03AB23") as often as as key/value pairs. A parser would be right about
80% of it and quietly wrong about the rest, and a quietly wrong test datum shows
up as an IRIS conformance failure - the one error this whole exercise must not
make. So the prose is carried through *verbatim* and the machine only extracts
what it can extract exactly:

  - which ``${data.*}`` keys a feature uses, and what value the suite's own YAML
    gives them (that is a fact, not an interpretation);
  - what FHIR resource type each key denotes, inferred only from step shapes that
    state the type explicitly - ``/Patient/${data.x}``,
    ``references resource with ID "Patient/${data.x}"``. Nothing is guessed from
    the key's name, because names lie: ``account-read-patient-id`` is used only
    in prose and never in a typed step, so it is reported as untyped rather than
    assumed to be a Patient;
  - which profile canonicals the feature asserts, and via which validation module
    (that is the link to the supplying FHIR package);
  - which other test cases the precondition says must run first.

The judgement calls stay with the author, who reads the quoted prose. This script
guarantees the author sees all of it, and that the inventory is complete.
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from feature_parser import (  # noqa: E402
    DATA_KEY_PATTERN,
    RE_DATA_KEY,
    RE_PROFILE_FULL,
    RE_PROFILE_SHORT,
    count_asserting_cases,
    count_executed_cases,
    data_keys_in,
    load_features,
    parse_tag_expression,
    runtime_keys_in,
)

# A test datum, captured as its QUALIFIED name `<namespace>.<key>` - `data.x` in
# suite 2.x, which has one namespace, and `basis.x`, `icuminimal.x` and so on in
# 3.x, which replaced it with one namespace per ISiK module. Qualification is not
# cosmetic there: observation-encounter-id, observation-patient-id and
# observation-practitioner-id are each defined by several modules with DIFFERENT
# values, so a bare key does not identify a resource.
#
# The namespace is matched by shape rather than against a fixed list, so that a
# namespace this rig has not been told about still yields a key instead of
# unrecognised text that seeds nothing. Which namespaces are legitimate is then
# checked separately, against the baseKeys of the Tiger configuration itself (see
# load_basekeys and the namespace check in analyse). Measured on both corpora:
# every `${...}` occurrence in the feature files and fixtures of 2.7.0 (2107) and
# 3.0.9 (2323) is a test-data reference, so nothing else can match this shape.
#
# Defined once, in feature_parser, so that the gap-analysis index and this
# extractor cannot disagree about what a key is.
KEY = DATA_KEY_PATTERN

# -----------------------------------------------------------------------------
# Resource-type inference
# -----------------------------------------------------------------------------
# Only step shapes that name the FHIR type in the same breath as the key. Each
# was read off the corpus rather than imagined; the count in the comment is how
# often the shape occurs in 2.7.0 and is there so that a future version bump which
# drops a shape entirely shows up as a suspicious zero rather than as silence.
TYPE_RULES = (
    # Get FHIR resource at "http://fhirserver/Patient/${data.patient-read-id}"
    # Negative lookahead for "=": a search URL such as "/Patient/?_id=${...}"
    # names the type too, but for the *search target*, which for chained or
    # reverse-chained parameters is not the type of the key's resource.
    ("instance-url", re.compile(r"http://fhirserver/([A-Za-z]+)/" + KEY + r"(?![^\"']*=)")),
    # And element "subject" references resource with ID "Patient/${data.x}"
    ("typed-reference", re.compile(r'references resource with ID "([A-Za-z]+)/' + KEY)),
    # And referenced Patient resource with id "${data.x}" conforms to ...
    # And referenced "Encounter" resource with id "${data.x}" conforms to ...
    ("referenced-typed", re.compile(r'referenced "?([A-Za-z]+)"? resource with id "' + KEY)),
    # And bundle does not contain resource "Encounter" with ID "${data.x}"
    ("bundle-excludes", re.compile(r'bundle does not contain resource "([A-Za-z]+)" with ID "' + KEY)),
    # TGR send PUT request to "http://fhirserver/Patient/${data.x}"
    ("write-url", re.compile(r'(?:PUT|POST|DELETE|GET) request to "http://fhirserver/([A-Za-z]+)/' + KEY)),
    # "/Condition/?_id=${data.x}" - `_id` searches a resource's own id, so the
    # type in the path is the key's type. This is the one search form that is a
    # type statement, which is why it is listed separately from the general
    # "?param=" case the instance-url rule excludes.
    ("search-by-id", re.compile(r"http://fhirserver/([A-Za-z]+)/\?_id=" + KEY)),
    # And reason for medication is given either by code or by reference to a
    # Condition resource with ID "${data.medication-condition-id}"
    ("reference-to-typed", re.compile(r"reference to a[n]? ([A-Za-z]+) resource with ID \"" + KEY)),
    # A reference-typed search parameter carries the type of the *target*, which is
    # the key's own type: "?patient=Patient/${data.account-read-patient-id}" and
    # "?partof=Organization/${data.organization-read-parent-id}". Distinct from the
    # bare "?_id=${data.x}" form, which names the type of the resource being
    # searched *for* and says nothing about the key - that form is deliberately
    # excluded by the negative lookahead in the instance-url rule above.
    ("search-target", re.compile(r"[?&][a-zA-Z_.:-]+=([A-Za-z]+)/" + KEY)),
    # The same statement inside a FHIRPath assertion:
    # "partOf.where(reference = 'Organization/${data.organization-read-parent-id}')"
    ("fhirpath-reference", re.compile(r"[=~]\s*'([A-Za-z]+)/" + KEY + r"'")),
)

# The same statement inside a fixture body - `"reference": "Slot/${data.x}"`. The
# step text never names the type of a resource a fixture merely references, so
# without this rule those resources are missing from the inventory entirely.
#
# Kept out of TYPE_RULES because it must be applied to a different corpus: these
# patterns are matched against the JSON bodies the suite POSTs/PUTs, not against
# the feature text. Two rules rather than one because the JSON form is
# double-quoted (`"Slot/${data.x}"`) where the FHIRPath form inside a step is
# single-quoted, and a shared regex would match neither cleanly.
FIXTURE_TYPE_RULES = (
    # "reference": "Slot/${data.appointment-book-slot-id}"
    ("fixture-reference", re.compile(r'"([A-Za-z]+)/' + KEY + r'"')),
    # "url": "Appointment/${data.x}" in a transaction entry's request
    ("fixture-request-url", re.compile(r'"url"\s*:\s*"([A-Za-z]+)/' + KEY + r'"')),
)

# `"id": "${data.x}"` inside a fixture. The key names the ID the *fixture itself*
# will be created under, which is the suite's own payload and not seeded data - so
# these keys are barred from the inventory even when some other rule types them.
# Without the bar, `Appointment-Book-Fixture-With-Specialty` and the ten other
# fixture bodies would appear as resources we must pre-create, and `make seed`
# would create them - pre-empting the very Create the scenario is testing.
RE_FIXTURE_OWN_ID = re.compile(r'"id"\s*:\s*"' + KEY + r'"')

# Not FHIR resource types, even though they appear in the type position of a URL.
NOT_A_TYPE = {"metadata", "_search", "$book"}

# -----------------------------------------------------------------------------
# Precondition prose mining - only the two things that are stated formulaically
# -----------------------------------------------------------------------------
# "The Patient-Read test case must have been executed successfully beforehand."
# "Der Testfall Patient-Read & Patient-Read-Extended muss zuvor erfolgreich ..."
# The names are feature tags (@Patient-Read), so they resolve to real features and
# give the seed order a documented justification rather than a guessed one.
RE_PREREQ_EN = re.compile(
    r"[Tt]he ([A-Za-z0-9&, \-]+?) test\s*case[s]? must have been (?:executed|carried out|run)"
)
RE_PREREQ_DE = re.compile(
    r"Der Testfall ([A-Za-z0-9&, \-]+?) muss zuvor|Die Testfälle ([A-Za-z0-9&, \-]+?) müssen zuvor"
)

# The prose names the configuration key that must hold the resource's ID. Worth
# extracting because it is the one place the prose and the YAML contract meet: a
# key mentioned in prose but never used in a step is documentation-only, and one
# used in a step but never mentioned in prose has no stated content requirement.
RE_PROSE_KEY = re.compile(r"'([a-zA-Z][a-zA-Z0-9_.-]*-(?:id|ids|system|value|code|version|name|count|date|start|end|identifier|location|provider|ingredient))'")

# Request bodies the suite supplies itself: `!{file('src/test/resources/.../X.json')}`.
# Recorded because they mark the resources we must NOT author content for - a
# Create or Update feature sends its own payload, so the only requirement on the
# seeded resource is that it exists at the given ID with the right type. That is
# also why those resources come out of the inventory with no profile attributed:
# the suite never asserts a profile on them, it asserts the effect of its own PUT.
RE_FIXTURE = re.compile(r"file\('([^']+)'\)")


def split_prereqs(blob: str) -> list[str]:
    """"Patient-Read & Patient-Read-Extended" -> two names."""
    parts = re.split(r"\s*(?:&|,|\band\b|\bund\b)\s*", blob)
    return [p.strip() for p in parts if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", p.strip() or "")]


# -----------------------------------------------------------------------------
# Profile attribution - which profile must THIS resource satisfy
# -----------------------------------------------------------------------------
# The naive approach, matching a canonical's last segment against the resource
# type, does not work: ISiK profile names are German, so ISiKAbrechnungsfall is the
# Account profile and ISiKKontaktGesundheitseinrichtung is the Encounter one. No
# string relationship exists to exploit.
#
# The reliable route is Tiger's own semantics. `FHIR current response body is a
# valid <module> resource and conforms to profile "<canonical>"` asserts against
# the *current response* - whatever the preceding `Get FHIR resource at` step
# fetched. So walking a scenario's steps in order and remembering the last
# instance-level fetch attributes each profile assertion to exactly the resource
# the suite attributes it to. That is a fact about the run, not an inference about
# names.
RE_FETCH_INSTANCE = re.compile(
    r'(?:Get FHIR resource at|request to) "http://fhirserver/([A-Za-z]+)/' + KEY + r'(?![^"\']*=)'
)
# Any step that issues an HTTP request, whatever its shape. Tiger's "current
# response" is replaced by *every* request, not only by the instance-level ones the
# two regexes above recognise, so a request this file cannot attribute to a key must
# still end the current subject. It is the only remaining way the subject changes.
#
# Without this, `Appointment-Book` (Stufe 5) reads
# `Slot/${data.appointment-book-slot-id}`, then POSTs to `Appointment/$book` - a
# type-level operation URL with no data key in it, so neither RE_FETCH_INSTANCE nor
# RE_HTTP_INSTANCE matches - and the five assertions that follow, which describe the
# *Appointment or Parameters* the operation returns, were attributed to the Slot.
# The result was a Slot required to satisfy `($this is Appointment) implies
# id.exists()` and `status.toString().matches('^booked|pending$')`: the first only
# vacuously, the second not at all, since a Slot's status is `free`. That is
# unsatisfiable by any Slot, and an author who tried would have broken the one
# assertion the row really does carry (`status = 'free'`).
#
# Six assertions in the 2.7.0 corpus sit behind such a step - the five above plus one
# `$.responseCode`, which was already excluded as HTTP-level. No profile claim does.
RE_HTTP_ANY = re.compile(
    r'(?:Get FHIR resource at|Post FHIR search request to'
    r'|send (?:empty )?(?:GET|PUT|POST|DELETE) request to) "'
)
# `referenced "Patient" resource with id "${data.x}" conforms to a valid v5
# "ISiKPatient" profile` and its unquoted variant - key and profile in one step,
# so no ordering is involved.
RE_REF_CONFORMS = re.compile(
    r'referenced "?([A-Za-z]+)"? resource with id "' + KEY
    + r'" conforms to (?:a valid v5 )?"?(ISiK[A-Za-z0-9]+)"? profile'
)


# -----------------------------------------------------------------------------
# Assertion attribution - the requirement that actually decides pass or fail
# -----------------------------------------------------------------------------
# The precondition prose is the *stated* specification, and it is systematically
# less specific than what the scenario checks. Stufe3/Basis/Patient-Read says only
#
#     Addresse: Unter den Linden 3, 1. Etage Hinterhaus, 10117 Berlin, Deutschland
#     Stadtteil: Mitte
#     Postfach: 4711 (selbe Stadt)
#
# while the scenario requires TWO Address entries - one `type = 'both'` carrying
# iso21090-ADXP-streetName / -houseNumber / -additionalLocator extensions, one
# `type = 'postal'` carrying -postBox with the literal value "Postfach 4711" - plus
# an -precinct extension for the district. An author working from the prose alone
# writes a single address and fails four assertions, and the failure then looks
# like an IRIS gap.
#
# So the assertions are collected per resource, by the same response-state walk
# that attributes profiles: whatever `Get FHIR resource at .../${data.key}` last
# fetched is what the following assertion steps constrain.
RE_ASSERT_FHIRPATH = re.compile(r"evaluates the FHIRPath [\"'](.+?)[\"'](?:\s+with error message|$)")
RE_ASSERT_ATTRIBUTE = re.compile(r'response with attribute "([^"]+)" matches "([^"]*)"')
RE_ASSERT_REFERENCE = re.compile(r'element "([^"]+)" references resource with ID "([^"]+)"')

# Any HTTP verb aimed at an instance URL, not just the reading ones. A write has to
# be recognised in order to be *excluded*: after `TGR send PUT request to
# .../Patient/${data.x}` the response body is the payload the suite just sent, so an
# assertion on it states what the suite wrote, not what our seed data must contain.
# Attributing those to the seeded resource would put "note.text = 'Updated note'"
# into a resource whose whole purpose is to hold the note *before* the update.
RE_HTTP_INSTANCE = re.compile(
    r'send (?:empty )?(GET|PUT|POST|DELETE) request to "http://fhirserver/([A-Za-z]+)/'
    + KEY + r'(?![^"\']*=)'
)
# Response attributes that describe the HTTP exchange rather than the resource.
# `$.responseCode` is the status code and `$.header.*` the headers; both are
# requirements on IRIS, not on our JSON, and a checker fed them would look for an
# element named "responseCode" in a Patient and report a spurious failure.
RE_HTTP_LEVEL_ATTR = re.compile(r"^\$\.(responseCode|header)\b")
# `$.body` is Tiger's *parsed response body*, so `$.body.…` addresses the resource
# tree itself and not some wrapper around it. Two spellings occur, both being the
# same resource seen through Tiger's RbelPath over the parse of the negotiated
# content type:
#
#   $.body.Condition.onsetAge.unit.value   XML  - the document element is named
#                                               after the resource type, and a
#                                               primitive's text sits in `.value`
#                                               (the XML attribute of that name)
#   $.body.note.0.text.content             JSON - no type element, list indices are
#                                               path segments, and a primitive's
#                                               text sits in `.content`
#
# Which one appears follows the `with content type "xml"|"json"` of the preceding
# read, e.g. Condition-Read-Active reads as XML and RiskAssessment-Update as JSON.
# `$.body` alone (Binary-Read) is the raw, unparsed payload.
#
# These are therefore normalised to element paths and checked like any other
# attribute assertion. Treating them as an opaque envelope instead would silently
# drop 18 real content requirements - among them Condition.onsetAge and the
# `condition-related` extension, neither of which is asserted anywhere else.
RE_BODY_ATTR = re.compile(r"^\$\.body\b")
# `$.body` with nothing under it: the undecoded payload, which is not an element
# path and cannot be checked against a JSON tree.
RE_BODY_RAW = re.compile(r"^\$\.body$")


def normalise_body_path(path: str) -> str:
    """`$.body.…` -> the `$..<element path>.value` shape used by attribute assertions.

    Drops the XML document element (`Condition`), the JSON leaf accessors
    (`.content` / trailing `.value`) and numeric list indices, since the checker
    flattens lists the way FHIRPath does.
    """
    parts = path.split(".")[2:]                      # strip "$", "body"
    if parts and parts[0][:1].isupper():             # XML document element
        parts = parts[1:]
    while parts and parts[-1] in ("content", "value"):
        parts.pop()
    parts = [p for p in parts if not p.isdigit()]    # list indices
    return "$.." + ".".join(parts) + ".value"


def _assert_sig(a: dict) -> tuple:
    """Identity of an assertion, ignoring where it was found."""
    return tuple(sorted((k, v) for k, v in a.items()
                        if k not in ("line", "scenario", "feature")))


def attribute_assertions(feature, values: dict[str, object] | None = None) -> dict[str, list[dict]]:
    """key -> the assertions made about that resource's own seeded representation.

    Only instance-level *reads* count as establishing a subject, and three classes of
    assertion are deliberately excluded because attributing them would state a
    requirement the seed data does not have to meet:

    - A search response is a Bundle, so its assertions constrain the bundle and the
      search behaviour, not the resource. Attributing them would tell the author to
      put `entry.resource.count() > 0` into a Patient.
    - After a write (PUT/POST/DELETE) the current response body is the payload the
      *suite* sent. `MedicationRequest-Update-Original-Example` is read once to
      confirm its note says "Testnotiz", then updated; the post-update assertion
      says "Aktualisierte Testnotiz". Both would land on one resource, and no single
      instance can satisfy both. The write ends the read subject rather than
      continuing it.

      This holds for the *re-read* as well, which is the less obvious half. The
      update scenarios do `PUT`, then `GET` the same instance, then assert - and
      that GET makes the instance current again by the rule above, so its
      assertions would be attributed to our seed data even though they describe
      the fixture the suite just PUT. 32 such assertions exist across 9 keys
      (medicationrequest-update-id, list-update-id, ...), and every one of them
      contradicts the pre-update assertion on the same resource: `amount.numerator
      .value ~ 40` after the write versus `~ 20` before it. Authoring to the
      post-write value would make the *first* read fail, so an instance once
      written in a scenario stops being a subject for the rest of that scenario.
    - `$.responseCode` and `$.header.*` are properties of the HTTP exchange. They
      are requirements on IRIS, not on our JSON.

    ``values`` resolves `${<namespace>.<key>}` inside an expected value to the ID
    the suite's own contract assigns it, so a reference requirement reads
    `Patient-Read-Example` rather than a placeholder the author would have to look
    up by hand.

    Keyed by the QUALIFIED name, and matched with feature_parser.RE_DATA_KEY
    rather than a second pattern written here. Both halves are load-bearing: a
    pattern of its own hardcoding the 2.x namespace `data` resolves nothing in a
    3.x corpus, and a lookup by the bare key resolves nothing once ``values`` is
    qualified. Either way every reference assertion keeps its placeholder, and
    assertion_check.py then reports correct seed data as failing to carry a
    reference whose required value it was never told - measured on the 2.7.0
    corpus as 56 of 122 EXACT assertions failing, all of them spuriously.
    """
    values = values or {}

    def resolve_placeholders(text: str) -> str:
        return RE_DATA_KEY.sub(
            lambda m: str(values.get(m.group(1), m.group(0))), text)

    out: dict[str, list[dict]] = collections.defaultdict(list)
    for scenario in feature.scenarios:
        if scenario.is_precondition:
            continue
        current: str | None = None
        # Instances this scenario has written. Their content is no longer ours to
        # state, so they are barred from becoming the subject again - see the
        # docstring on why the post-PUT re-read must not be attributed.
        written: set[str] = set()
        for step in scenario.steps:
            http = RE_HTTP_INSTANCE.search(step.full)
            if http:
                key = http.group(3)
                if http.group(1) == "GET":
                    current = None if key in written else key
                else:
                    written.add(key)
                    current = None
                continue
            fetch = RE_FETCH_INSTANCE.search(step.full)
            if fetch:
                key = fetch.group(2)
                current = None if key in written else key
                continue
            if RE_HTTP_ANY.search(step.full):
                # A request whose target this file cannot tie to a key - a search, or
                # a type-level operation such as `Appointment/$book`. It still
                # replaces the response the next assertions see, so the subject ends.
                current = None
                continue
            if current is None:
                continue
            # A commented-out assertion is not a requirement. The parser drops
            # comment lines before this is reached, so nothing to do - noted
            # because 2.7.0 has three of them (RiskAssessment-Read / -Search) and
            # their absence from this output is correct, not a miss.
            for m in RE_ASSERT_FHIRPATH.finditer(step.full):
                out[current].append({"kind": "fhirpath",
                                     "expr": resolve_placeholders(m.group(1)),
                                     "line": step.line, "scenario": scenario.name})
            for path, want in RE_ASSERT_ATTRIBUTE.findall(step.full):
                if RE_HTTP_LEVEL_ATTR.match(path):
                    continue
                if RE_BODY_RAW.match(path):
                    # Binary-Read asserts on the undecoded payload; there is no
                    # element path to check, but the requirement is real and is
                    # recorded so it can be satisfied by hand.
                    out[current].append({"kind": "raw-body", "path": path,
                                         "matches": resolve_placeholders(want),
                                         "line": step.line, "scenario": scenario.name})
                    continue
                if RE_BODY_ATTR.match(path):
                    path = normalise_body_path(path)
                out[current].append({"kind": "attribute", "path": path,
                                     "matches": resolve_placeholders(want),
                                     "line": step.line, "scenario": scenario.name})
            for element, target in RE_ASSERT_REFERENCE.findall(step.full):
                out[current].append({"kind": "reference", "element": element,
                                     "target": resolve_placeholders(target),
                                     "line": step.line, "scenario": scenario.name})
    return out


def attribute_profiles(feature) -> dict[str, dict[str, set[str]]]:
    """key -> {"canonical": {...}, "byName": {...}, "fetched": bool} for one feature.

    Walks each scenario's steps in order so that a profile assertion lands on the
    resource the preceding fetch made current. Scenario boundaries reset the
    current resource, because Tiger's response state does not survive them.

    ``fetched`` records whether any step retrieves the key's resource at instance
    level. It is what distinguishes "we failed to find the profile" from "the
    suite asserts none": a resource that is only ever a *reference target* or a
    *search hit* never becomes Tiger's current response body, so no
    ``conforms to profile`` step can apply to it, and the absence of a profile in
    the inventory is then the correct answer rather than a gap in this extraction.
    """
    out: dict[str, dict[str, set[str]]] = collections.defaultdict(
        lambda: {"canonical": set(), "byName": set(), "fetched": False})

    for scenario in feature.scenarios:
        current: str | None = None
        for step in scenario.steps:
            fetch = RE_FETCH_INSTANCE.search(step.full)
            if fetch:
                current = fetch.group(2)
                out[current]["fetched"] = True
                continue
            for _rtype, key, name in RE_REF_CONFORMS.findall(step.full):
                out[key]["byName"].add(name)
            if RE_HTTP_ANY.search(step.full):
                # Same rule as in attribute_assertions: an unattributable request
                # still ends the subject. No profile claim in 2.7.0 currently sits
                # behind one, so this changes nothing today - it is here so the two
                # walks cannot drift apart on a version bump that adds one.
                current = None
                continue
            if current is None:
                continue
            m = RE_PROFILE_FULL.search(step.full)
            if m and "current response body" in step.text:
                out[current]["canonical"].add(m.group(2))
    return out


def infer_types(text: str) -> dict[str, set[str]]:
    """Data key -> the resource types a feature's own steps state for that key."""
    found: dict[str, set[str]] = collections.defaultdict(set)
    for _name, rx in TYPE_RULES:
        for rtype, key in rx.findall(text):
            if rtype in NOT_A_TYPE:
                continue
            # FHIR resource types are UpperCamelCase. Anything else in this
            # position is a path segment, not a type.
            if not rtype[:1].isupper():
                continue
            found[key].add(rtype)
    return found


FIXTURE_PREFIX = "src/test/resources/features/"


def read_fixtures(features_root: Path, rel_paths: list[str]) -> tuple[str, set[str]]:
    """The concatenated bodies a feature sends, plus the keys naming their own IDs.

    A fixture is the payload the suite POSTs or PUTs, referenced from a step as
    ``!{file('src/test/resources/features/<...>.json')}``. Its *content* is the
    suite's, not ours - which is why ``overwrittenByFixture`` rows carry no
    content requirement. But a fixture also *references* resources, and those
    references are the only place some seeded resources are named at all:

        Stufe3/Terminplanung/Appointment-Book.feature POSTs a body containing
        `"reference": "Slot/${data.appointment-book-slot-id}"`.

    That is the sole occurrence of `appointment-book-slot-id` in a typed position
    anywhere in Stufe 3 - the feature text mentions it only in German prose. So
    without reading fixture bodies, `Slot-Free-Example` is absent from the Stufe 3
    inventory, nothing seeds it, and the mandatory booking scenario fails on a
    resource we were never told to create. The same holds for
    `Schedule-Read-Example-Book` in Stufe 5.

    The returned key set is the fixtures' *own* IDs (``"id": "${data.x}"``), which
    must be excluded from typing: those name resources the suite creates by
    sending the fixture, and pre-creating them would pre-empt the Create under
    test. 11 Stufe 5 keys and 2 Stufe 3 keys are of this kind.

    A path that does not resolve is a hard error rather than a skip: the fixture
    corpus and the feature corpus come out of the same image, so a missing file
    means the extraction is reading a tree it does not understand, and the honest
    response is to stop rather than to silently under-report the inventory.
    """
    blobs: list[str] = []
    own: set[str] = set()
    for rel in rel_paths:
        if not rel.startswith(FIXTURE_PREFIX):
            raise SystemExit(
                f"fixture path {rel!r} does not start with {FIXTURE_PREFIX!r}, so it "
                "cannot be located under the extracted features tree")
        path = features_root / rel[len(FIXTURE_PREFIX):]
        if not path.is_file():
            raise SystemExit(f"fixture referenced by a feature is missing: {path}")
        text = path.read_text(encoding="utf-8")
        blobs.append(text)
        own.update(RE_FIXTURE_OWN_ID.findall(text))
    return "\n".join(blobs), own


def infer_fixture_types(body: str, own_ids: set[str]) -> dict[str, set[str]]:
    """Types stated by a fixture body, excluding the fixture's own IDs."""
    found: dict[str, set[str]] = collections.defaultdict(set)
    for _name, rx in FIXTURE_TYPE_RULES:
        for rtype, key in rx.findall(body):
            if rtype in NOT_A_TYPE or not rtype[:1].isupper() or key in own_ids:
                continue
            found[key].add(rtype)
    return found


# -----------------------------------------------------------------------------
# Which namespace a test-data file is loaded under
# -----------------------------------------------------------------------------
# Taken from the Tiger configuration rather than from a list kept here, because
# the configuration is what actually decides it: Tiger loads
# `testdata/Stufe5/icu-minimal.yaml` under `baseKey: icuminimal`, and no rule
# derives that name from the file name. A second copy of the mapping in this file
# could disagree with the configuration, and the symptom would be a key resolving
# to nothing - which downstream reads as a resource the system under test lacks.
RE_CFG_FILENAME = re.compile(r"^\s*-\s*filename:\s*(\S+)\s*$")
RE_CFG_BASEKEY = re.compile(r"^\s*baseKey:\s*(\S+)\s*$")


def load_basekeys(tiger_dir: Path) -> dict[str, str]:
    """`Stufe5/basis.yaml` -> `basis`, for every configuration in `tiger_dir`.

    The level is part of the key because file names repeat across levels
    (Stufe3/basis.yaml and Stufe5/basis.yaml in 2.x).
    """
    out: dict[str, str] = {}
    for tmpl in sorted(tiger_dir.glob("*.tmpl")):
        pending: str | None = None
        for raw in tmpl.read_text(encoding="utf-8").splitlines():
            m = RE_CFG_FILENAME.match(raw)
            if m:
                if pending is not None:
                    sys.exit(f"{tmpl}: `filename: {pending}` has no baseKey")
                pending = m.group(1)
                continue
            m = RE_CFG_BASEKEY.match(raw)
            if m and pending is not None:
                rel = pending[len("testdata/"):] if pending.startswith("testdata/") else pending
                prev = out.get(rel)
                if prev is not None and prev != m.group(1):
                    sys.exit(
                        f"{tmpl}: {rel} is loaded under baseKey {m.group(1)!r} here but "
                        f"{prev!r} in another configuration of the same generation"
                    )
                out[rel] = m.group(1)
                pending = None
        if pending is not None:
            sys.exit(f"{tmpl}: `filename: {pending}` has no baseKey")
    if not out:
        sys.exit(f"no `filename:`/`baseKey:` pairs in any *.tmpl under {tiger_dir}")
    return out


@dataclasses.dataclass(frozen=True)
class Extraction:
    """One extraction: the corpus it reads, the test data staged for it, the levels.

    These four facts are fixed for the whole extraction - the feature tree, the
    staged test data, the tag expression that puts a case in scope at each level,
    and the namespace each test-data file is loaded under - so they are stated
    once here rather than travelling together through every step that needs one
    of them.

    `basekeys` is None for suite generation 2.x, which has the single namespace
    `data` and no Tiger configuration to read one from.
    """

    features_root: Path
    testdata_root: Path
    tag_expr: dict[str, str]
    basekeys: dict[str, str] | None = None

    def staged_contract(self, level: str) -> tuple[dict[str, object], dict[str, str]]:
        """The suite's key->value contract for one level, plus which file defines each.

        Parsed with a deliberately minimal YAML reader rather than PyYAML: this module
        is imported by tooling that must run on a bare deployment host, and these
        files are flat ``key: value`` maps with comments - the one YAML shape that
        needs no library. (99-check.sh separately parses them with PyYAML, so a file
        that is *not* this shape is caught there rather than silently misread here.)

        Quoting is the one piece of real YAML syntax these files use, and it has to be
        honoured rather than passed through. Seven keys are written quoted so that YAML
        does not coerce them to another type:

            slot-read-start: "2027-01-01"           # else a YAML 1.1 date, not a string
            appointment-book-datetime-start: "2027-01-01T11:05:00Z"

        Tiger loads these with snakeyaml, which strips the quotes, so the step
        ``start.toString().contains('${data.slot-read-start}')`` is sent as
        ``contains('2027-01-01')``. Taking the raw characters instead would record the
        required literal as ``"2027-01-01"`` *with* the quote marks - a value no
        conformant instance can contain - and the seed data would then be authored to
        satisfy a requirement the suite never makes, or reported as failing one it does.
        """
        testdata_root, basekeys = self.testdata_root, self.basekeys
        values: dict[str, object] = {}
        origin: dict[str, str] = {}
        d = testdata_root / level
        if not d.is_dir():
            return values, origin
        for path in sorted(d.glob("*.yaml")):
            # The namespace this file's keys live in. Absent a configuration to read
            # it from, `data` - the single namespace of suite 2.x.
            if basekeys is None:
                ns = "data"
            else:
                ns = basekeys.get(f"{level}/{path.name}")
                if ns is None:
                    sys.exit(
                        f"{path} is staged as test data but no Tiger configuration "
                        f"loads it, so the namespace its keys live in is unknown. "
                        f"Either add it to the configuration template or remove it."
                    )
            for raw in path.read_text(encoding="utf-8").splitlines():
                line = raw.rstrip()
                if not line or line.lstrip().startswith("#") or ":" not in line:
                    continue
                if line[:1] in (" ", "\t", "-"):
                    continue
                key, _, val = line.partition(":")
                val = val.strip()
                if val.startswith("#"):
                    val = ""
                # A trailing comment after an unquoted scalar is not part of the value.
                # Checked before dequoting so that a `#` inside a quoted value survives.
                elif not val[:1] in ("'", '"') and " #" in val:
                    val = val.split(" #", 1)[0].rstrip()
                elif len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                    val = val[1:-1]
                qualified = f"{ns}.{key.strip()}"
                values[qualified] = val
                origin[qualified] = path.name
        return values, origin

    def analyse(self) -> dict:
        features_root, tag_expr, basekeys = (
            self.features_root, self.tag_expr, self.basekeys)
        features = load_features(features_root)

        preds = {lvl: parse_tag_expression(expr) for lvl, expr in tag_expr.items()}
        testdata = {lvl: self.staged_contract(lvl) for lvl in tag_expr}

        # --- Does the corpus reference a namespace nothing loads? ------------------
        # KEY matches a namespace by shape, so an unexpected one yields keys rather
        # than unrecognised text. That is the safe failure mode, but it must not pass
        # silently: a namespace no configuration loads cannot resolve, and every key
        # in it would reach the system under test as the literal string
        # "${namespace.key}" - a request that fails for a reason which has nothing to
        # do with conformance.
        if basekeys is not None:
            declared = set(basekeys.values())
            seen: dict[str, set[str]] = collections.defaultdict(set)
            for f in features:
                for key in data_keys_in(f.text):
                    ns = key.split(".", 1)[0]
                    if ns not in declared:
                        seen[ns].add(f.rel)
            if seen:
                detail = "\n".join(
                    f"    ${{{ns}.*}} in {len(files)} feature file(s), e.g. {sorted(files)[0]}"
                    for ns, files in sorted(seen.items())
                )
                sys.exit(
                    "the feature corpus references test-data namespaces that no Tiger "
                    "configuration loads:\n" + detail + "\n"
                    f"  configured namespaces: {', '.join(sorted(declared))}\n"
                    "  Every key in an unloaded namespace reaches the system under test "
                    "as a literal\n  placeholder. Add the module's file and baseKey to the "
                    "configuration template."
                )

        out: dict = {
            "featuresRoot": str(features_root),
            "tagExpressions": tag_expr,
            "features": [],
            "levels": {},
        }

        # --- Resource types are resolved per LEVEL, not per feature ---------------
        # A key denotes one resource within its level: patient-read-id is the same
        # Patient whether Patient-Read reads it directly or Coverage-Search asserts a
        # reference to it. So evidence from any feature at that level applies to all
        # of them. Doing this per feature instead would leave 34 of Stufe 3's 65
        # ID-shaped keys untyped, purely because the feature that happens to state the
        # type is not the feature being rendered - Patient-Search only ever writes
        # `?_id=${data.patient-read-id}`, which this deliberately does not read as a
        # type declaration.
        #
        # Merging across features is only safe because it is checked: a key that
        # collects two different types would mean the merge is invalid for that key,
        # and rather than silently picking one, it is refused. There are no such keys
        # in 2.7.0 - both levels come out clean - so this is a guard against a future
        # release, not a workaround for the present one.
        #
        # The evidence corpus is the feature text *plus* the bodies of the fixtures it
        # sends - see read_fixtures for why omitting the latter loses two in-scope
        # resources outright.
        level_types: dict[str, dict[str, set[str]]] = {}
        type_evidence: dict[tuple[str, str], dict[str, list[str]]] = {}

        # Per feature: the fixture bodies it sends, the keys those bodies use, and the
        # keys naming a fixture's own ID. Computed once here because both the type walk
        # below and the per-feature record further down need them.
        fixture_info: dict[str, tuple[str, set[str], set[str]]] = {}
        for f in features:
            rels = sorted({p for p in RE_FIXTURE.findall(f.text) if "<" not in p})
            body, own = read_fixtures(features_root, rels)
            fixture_info[f.rel] = (body, data_keys_in(body), own)

        for f in features:
            acc = level_types.setdefault(f.level, collections.defaultdict(set))
            for rule, rx in TYPE_RULES:
                for rtype, key in rx.findall(f.text):
                    if rtype in NOT_A_TYPE or not rtype[:1].isupper():
                        continue
                    acc[key].add(rtype)
                    where = type_evidence.setdefault((f.level, key), collections.defaultdict(list))
                    where[rtype].append(f"{rule} in {f.rel}")
            body, _keys, own = fixture_info[f.rel]
            for key, types in infer_fixture_types(body, own).items():
                acc[key].update(types)
                where = type_evidence.setdefault((f.level, key), collections.defaultdict(list))
                for rtype in types:
                    where[rtype].append(f"fixture-reference in {f.rel}")

        conflicts = {
            f"{level}:{key}": {t: sorted(set(v))[:3] for t, v in type_evidence[(level, key)].items()}
            for level, keys in level_types.items()
            for key, types in keys.items()
            if len(types) > 1
        }
        if conflicts:
            raise SystemExit(
                "these ${data.*} keys are used with more than one FHIR resource type, so "
                "one key does not denote one resource and this extractor's level-wide "
                "type merge is invalid for them. Resolve by hand before trusting the "
                "inventory:\n" + json.dumps(conflicts, indent=2)
            )

        # Feature tag -> rel path, so a prerequisite named in prose can be resolved to
        # the file that defines it. Built per level: Stufe 3 and Stufe 5 both have a
        # @Patient-Read, and they are different features.
        by_tag: dict[tuple[str, str], str] = {}
        for f in features:
            for t in f.tags:
                if t.lower() not in {"@stufe3", "@stufe5", "@mandatory", "@optional", "@disabled"} \
                   and t.lstrip("@") not in {f.module, "Basis", "Basismodul"}:
                    by_tag.setdefault((f.level, t.lower()), f.rel)

        for f in features:
            pred = preds.get(f.level)
            if pred is None:
                raise SystemExit(f"no tag expression configured for level {f.level!r} ({f.rel})")

            values, origin = testdata[f.level]
            types = level_types[f.level]
            runtime = runtime_keys_in(f.text)

            # In scope = at least one non-precondition case survives the tag filter.
            # Computed per case rather than per file, because a mandatory feature can
            # hold @Optional scenarios and vice versa.
            assertive = count_asserting_cases(f, pred)
            executed = count_executed_cases(f, pred)

            profiles: dict[str, set[str]] = collections.defaultdict(set)
            for module, canonical in RE_PROFILE_FULL.findall(f.text):
                profiles[canonical].add(module)
            short = set(RE_PROFILE_SHORT.findall(f.text))

            prereqs: set[str] = set()
            prose_blocks = []
            prose_keys: set[str] = set()
            for scenario in f.precondition_scenarios:
                # Steps in source order, so the requirement sentence
                # ("Testbeschreibung: ..." / "the Test Description: ...") precedes the
                # data prose exactly as the feature file states it. Order matters here
                # because both are quoted into the requirement tables and the gap
                # report, where the sentence is the heading the prose explains.
                for step in scenario.steps:
                    if "Test Description" in step.text or "Testbeschreibung" in step.text:
                        # The argument is the whole quoted string. Matched greedily to
                        # the final quote: several of these sentences contain quoted
                        # words themselves ("... the status 'entered-in-error' ...").
                        m = re.search(r"[\"'](.*)[\"']\s*$", step.text)
                        if m:
                            prose_blocks.append({
                                "scenario": scenario.name,
                                "line": step.line,
                                "description": m.group(1),
                            })
                    if not step.docstring:
                        continue
                    prose_blocks.append({
                        "scenario": scenario.name,
                        "line": step.docstring_line,
                        "text": step.docstring,
                    })
                    for m in RE_PREREQ_EN.finditer(step.docstring):
                        prereqs.update(split_prereqs(m.group(1)))
                    for m in RE_PREREQ_DE.finditer(step.docstring):
                        prereqs.update(split_prereqs(m.group(1) or m.group(2) or ""))
                    prose_keys.update(RE_PROSE_KEY.findall(step.docstring))

            # Tags that only match because feature_parser normalises them. Surfaced so
            # the four "@Precondition:" spellings appear in the documentation as an
            # upstream defect instead of being silently absorbed. See
            # Scenario.is_precondition for why normalising is nevertheless correct.
            tag_defects = sorted({
                t for s in f.scenarios for t in s.tags
                if t != t.rstrip(":") and t.lower().rstrip(":") in ("@precondition", "@vorbedingung")
            })

            attributed = attribute_profiles(f)
            asserted = attribute_assertions(f, values)
            fixture_body, fixture_keys, fixture_own = fixture_info[f.rel]

            # Keys a fixture body uses that the feature text never mentions. They are
            # real uses - Tiger resolves them when it renders the payload - so leaving
            # them out reports them as "defined but never used" and, worse, leaves the
            # resources they name out of the inventory. The fixtures' own IDs are
            # excluded: those are resources the suite creates, not ones we seed.
            keys = {}
            for key in sorted(data_keys_in(f.text) | (fixture_keys - fixture_own)):
                keys[key] = {
                    "assertions": asserted.get(key, []),
                    "resourceTypes": sorted(types.get(key, ())),
                    "value": values.get(key),
                    "definedIn": origin.get(key),
                    "definedButEmpty": key in values and values[key] == "",
                    "setAtRuntime": key in runtime,
                    "namedInPrecondition": key in prose_keys,
                    "usedOnlyInFixtureBody": key not in data_keys_in(f.text),
                    "mustConformTo": sorted(attributed[key]["canonical"]) if key in attributed else [],
                    "mustConformToByName": sorted(attributed[key]["byName"]) if key in attributed else [],
                    "readAtInstanceLevel": bool(attributed[key]["fetched"]) if key in attributed else False,
                }

            # Outline placeholders (`fixtures/<inputFile>`) are resolved per Examples
            # row, so the literal is not a path. Excluded rather than reported missing.
            fixtures = sorted({p for p in RE_FIXTURE.findall(f.text) if "<" not in p})

            # The URL of every write step, so the inventory can tell which resources a
            # feature overwrites with a fixture body rather than reading as seeded.
            write_targets = [
                m.group(1)
                for s in f.scenarios for st in s.steps
                for m in [re.search(r'(?:PUT|POST) request to "(http://fhirserver/[^"]+)"', st.full)]
                if m
            ]

            out["features"].append({
                "rel": f.rel,
                "level": f.level,
                "module": f.module,
                "name": f.name,
                "tags": f.tags,
                "language": f.language or "de (English keywords, German prose)",
                "casesExecuted": executed,
                "casesAssertive": assertive,
                "inScope": assertive > 0,
                "disabled": any(t.lower() == "@disabled" for t in f.tags),
                "scenarioCount": len(f.test_scenarios),
                "preconditionBlocks": prose_blocks,
                "malformedTags": tag_defects,
                "prerequisites": sorted(prereqs),
                "prerequisiteFiles": sorted(
                    {by_tag[(f.level, "@" + p.lower())] for p in prereqs
                     if (f.level, "@" + p.lower()) in by_tag}
                ),
                "dataKeys": keys,
                "profiles": {c: sorted(m) for c, m in sorted(profiles.items())},
                "profilesByName": sorted(short),
                "fixtures": fixtures,
                "fixtureOwnIdKeys": sorted(fixture_own),
                "writeTargets": write_targets,
            })

        # --- Per-level rollup -----------------------------------------------------
        for level in sorted({f["level"] for f in out["features"]}):
            feats = [f for f in out["features"] if f["level"] == level]
            values, origin = testdata[level]

            # The fabrication work list: one entry per resource that must exist.
            # Grouped by (type, id) rather than by key, because several keys point at
            # one resource - patient-read-id and medication-patient-id are both
            # Patient-Read-Example in the Stufe 5 contract, and fabricating it twice
            # would be a second, conflicting definition of the same instance.
            inventory: dict[tuple[str, str], dict] = {}
            untyped: dict[str, dict] = {}
            for f in feats:
                for key, info in f["dataKeys"].items():
                    value = info["value"]
                    if info["setAtRuntime"] or value in (None, ""):
                        continue
                    if not info["resourceTypes"]:
                        # Recorded, not dropped: an untyped key with an ID-shaped
                        # value still needs a resource, the type just has to come
                        # from the prose. Making the author supply it beats guessing.
                        if key.endswith(("-id", "-Id")):
                            entry = untyped.setdefault(key, {"value": value, "usedBy": [], "inScope": False})
                            entry["usedBy"].append(f["rel"])
                            entry["inScope"] |= f["inScope"]
                        continue
                    for rtype in info["resourceTypes"]:
                        entry = inventory.setdefault((rtype, str(value)), {
                            "resourceType": rtype,
                            "id": str(value),
                            "keys": [],
                            "usedBy": [],
                            "profiles": [],
                            "profileNames": [],
                            "inScope": False,
                            "overwrittenByFixture": False,
                            "readAtInstanceLevel": False,
                            "assertions": [],
                        })
                        entry["readAtInstanceLevel"] |= info["readAtInstanceLevel"]
                        # Deduplicated on the assertion itself, not on (feature, line):
                        # the same FHIRPath is asserted about Patient-Read-Example in
                        # several features, and the author needs the requirement once.
                        # The first occurrence keeps its citation, so every requirement
                        # still points at one real line in one real feature file.
                        seen_asserts = {_assert_sig(e) for e in entry["assertions"]}
                        for a in info["assertions"]:
                            if _assert_sig(a) in seen_asserts:
                                continue
                            seen_asserts.add(_assert_sig(a))
                            entry["assertions"].append({**a, "feature": f["rel"]})
                        # A feature that PUTs one of the suite's own fixture bodies to
                        # this resource's URL replaces its content. Only the ID and the
                        # type are then requirements on our seed data - which is worth
                        # flagging, because these rows have no profile attributed and
                        # would otherwise read as an extraction gap.
                        if f["fixtures"] and any(
                            f'/{rtype}/${{data.{key}}}' in step
                            for step in (f["writeTargets"] or [])
                        ):
                            entry["overwrittenByFixture"] = True
                        if key not in entry["keys"]:
                            entry["keys"].append(key)
                        if f["rel"] not in entry["usedBy"]:
                            entry["usedBy"].append(f["rel"])
                        entry["inScope"] |= f["inScope"]
                        # Profiles attributed to THIS key by attribute_profiles, not
                        # every profile the feature happens to mention: a feature that
                        # reads a Patient also asserts the core Bundle profile on its
                        # search results, and listing that against the Patient would
                        # send the author fabricating to the wrong specification.
                        for canonical in info["mustConformTo"]:
                            if canonical not in entry["profiles"]:
                                entry["profiles"].append(canonical)
                        for name in info["mustConformToByName"]:
                            if name not in entry["profileNames"]:
                                entry["profileNames"].append(name)

            used = set()
            for f in feats:
                used.update(f["dataKeys"])
            runtime_all = set()
            for f in feats:
                runtime_all.update(k for k, v in f["dataKeys"].items() if v["setAtRuntime"])

            # Keys that name a fixture's *own* ID. They are excluded from `dataKeys`
            # (see the per-feature `keys` construction) and so fall out of `used`,
            # which would otherwise report them as "defined but never referenced" -
            # a claim that is false in the first clause and right in the second. They
            # ARE referenced, by the fixture body the suite sends; what makes them
            # need no seed resource is that the suite creates them, not that nothing
            # mentions them. Reported separately so the distinction survives into the
            # documentation: seeding one of these would pre-empt the Create under test.
            own_id_keys = set()
            for f in feats:
                own_id_keys.update(f["fixtureOwnIdKeys"])
            own_id_keys &= set(values)

            # Why a row can legitimately carry no profile. Computed once here so that
            # the inventory explains every such row instead of leaving the reader to
            # decide whether it is an extraction gap. The three causes are distinct
            # and each has a different consequence for the author:
            #   fixture-body  the suite PUTs its own payload over this resource, so
            #                 only its ID and type are requirements on our seed data
            #   never-read    the resource is only a reference target or a search hit;
            #                 no `conforms to profile` step can ever apply to it, so
            #                 its content requirement lives solely in the prose
            #   not-asserted  it IS read at instance level, yet no profile assertion
            #                 follows. In 2.7.0 this is the three commented-out
            #                 assertions (RiskAssessment-Read / -Search) plus reads
            #                 that only check individual elements. Author to the
            #                 profile the prose names.
            for r in inventory.values():
                if r["profiles"] or r["profileNames"]:
                    r["noProfileReason"] = None
                elif r["overwrittenByFixture"]:
                    r["noProfileReason"] = "fixture-body"
                elif not r["readAtInstanceLevel"]:
                    r["noProfileReason"] = "never-read"
                else:
                    r["noProfileReason"] = "not-asserted"

            out["levels"][level] = {
                "features": len(feats),
                "featuresInScope": sum(1 for f in feats if f["inScope"]),
                "casesExecuted": sum(f["casesExecuted"] for f in feats),
                "casesAssertive": sum(f["casesAssertive"] for f in feats),
                "keysDefined": len(values),
                "keysUsed": len(used),
                "keysUnused": sorted(set(values) - used - own_id_keys),
                "keysCreatedByFixture": sorted(own_id_keys - used),
                "keysUndefined": sorted(k for k in used if k not in values and k not in runtime_all),
                "keysAtRuntime": sorted(runtime_all),
                "resources": [inventory[k] for k in sorted(inventory)],
                "untypedIdKeys": untyped,
                "malformedTags": {f["rel"]: f["malformedTags"] for f in feats if f["malformedTags"]},
                "modules": {
                    module: {
                        "features": sum(1 for f in feats if f["module"] == module),
                        "casesExecuted": sum(f["casesExecuted"] for f in feats if f["module"] == module),
                        "casesAssertive": sum(f["casesAssertive"] for f in feats if f["module"] == module),
                    }
                    for module in sorted({f["module"] for f in feats})
                },
            }

        return out


# -----------------------------------------------------------------------------
# Markdown rendering
# -----------------------------------------------------------------------------

def _indent_block(text: str, prefix: str = "> ") -> str:
    return "\n".join(prefix + line if line.strip() else prefix.rstrip()
                     for line in text.splitlines())


def render_requirements(data: dict, level: str) -> str:
    """The whole of one level's TESTDATA-REQUIREMENTS.md."""
    feats = [f for f in data["features"] if f["level"] == level]
    roll = data["levels"][level]
    lines: list[str] = []
    A = lines.append

    A(f"# ISiK {level.replace('Stufe', 'Stufe ')} - test-data requirements")
    A("")
    A("GENERATED FILE - do not edit. Produced by `scripts/lib/extract_requirements.py`")
    A("from the feature files of the pinned test suite image. Regenerate with")
    A("`make packages`.")
    A("")
    A(f"Source of every quoted line: {data.get('source', data['featuresRoot'])}")
    A("")
    A("Every scenario in the ISiK test suite reads resources that must already exist")
    A("in the system under test. This file is the specification for those resources:")
    A("for each feature, the prose the suite itself states as its precondition, the")
    A("`${data.*}` keys it addresses resources by, and the profile the response must")
    A("conform to. The prose is quoted **verbatim and untranslated** - it is the")
    A("authoritative statement of what each resource must contain, and paraphrasing it")
    A("would put an interpretation between the specification and the test data.")
    A("")
    A("The prose is necessary but **not sufficient**. It is systematically less precise")
    A("than what the scenarios check, so each feature also lists the assertions made")
    A("about its resources. `Stufe3/Basis/Patient-Read` states its address as one line")
    A("of prose, `Unter den Linden 3, 1. Etage Hinterhaus, 10117 Berlin`, while its")
    A("scenario requires two `Address` entries - `type = 'both'` carrying the ISO-21090")
    A("`streetName` / `houseNumber` / `additionalLocator` extensions, and `type = 'postal'`")
    A("carrying `postBox` - plus a `precinct` extension for the district. Author to the")
    A("prose alone and four assertions fail, and those failures then look like IRIS gaps.")
    A("")
    A(f"Tag expression in force: `{data['tagExpressions'][level]}`")
    A("")
    A("| | |")
    A("|---|---|")
    A(f"| Feature files | {roll['features']} ({roll['featuresInScope']} with at least one in-scope case) |")
    A(f"| Cases executed | {roll['casesExecuted']} |")
    A(f"| Cases making assertions about the SUT | {roll['casesAssertive']} |")
    A(f"| Test-data keys defined by the suite | {roll['keysDefined']} |")
    A(f"| Test-data keys referenced by features | {roll['keysUsed']} |")
    A(f"| Resources to fabricate | {len(roll['resources'])} |")
    A("")
    A("The two case counts differ because each feature's `@Precondition` /")
    A("`@vorbedingung` block is itself a Cucumber scenario: it executes and appears in")
    A("the report, but its steps only log prose (`IsikGlue.printDescription` /")
    A("`configureInitialState`) and assert nothing. \"Cases\" counts `Examples:` rows")
    A("individually, which is how Cucumber and the Serenity report count them.")
    A("")

    for module in sorted({f["module"] for f in feats}):
        mods = roll["modules"][module]
        A(f"## {module}")
        A("")
        A(f"{mods['features']} feature files, {mods['casesAssertive']} asserting cases "
          f"of {mods['casesExecuted']} executed.")
        A("")
        for f in sorted((x for x in feats if x["module"] == module), key=lambda x: x["rel"]):
            scope = "in scope" if f["inScope"] else "**not in scope** for this tag expression"
            if f["disabled"]:
                scope += ", `@Disabled` upstream"
            A(f"### {f['name']}")
            A("")
            A(f"- Source: `{f['rel']}` ({scope})")
            A(f"- Tags: {' '.join(f['tags']) if f['tags'] else '(none)'}")
            A(f"- Cases: {f['casesAssertive']} asserting / {f['casesExecuted']} executed")
            if f["prerequisites"]:
                A(f"- Depends on test case(s): {', '.join(f['prerequisites'])}"
                  + (f" ({', '.join(f['prerequisiteFiles'])})" if f["prerequisiteFiles"] else ""))
            if f["profiles"]:
                A("- Response must conform to:")
                for canonical, modules in f["profiles"].items():
                    A(f"    - `{canonical}` (validation module `{', '.join(modules)}`)")
            if f["profilesByName"]:
                A(f"- Referenced resources must conform to: {', '.join('`' + p + '`' for p in f['profilesByName'])}")
            if f["malformedTags"]:
                A(f"- Upstream tag defect: {', '.join('`' + t + '`' for t in f['malformedTags'])} "
                  "(stray colon; treated as the precondition tag it was meant to be)")
            A("")

            if f["dataKeys"]:
                A("| `${data.*}` key | resource type | ID / value | defined in |")
                A("|---|---|---|---|")
                for key, info in f["dataKeys"].items():
                    types = ", ".join(info["resourceTypes"]) or "-"
                    if info["setAtRuntime"]:
                        value = "*(written by the suite at run time)*"
                    elif info["definedIn"] is None:
                        value = "**UNDEFINED - see docs/en/05-testdata.md**"
                    elif info["definedButEmpty"]:
                        value = "*(defined empty upstream)*"
                    else:
                        value = f"`{info['value']}`"
                    A(f"| `{key}` | {types} | {value} | {info['definedIn'] or '-'} |")
                A("")

            # The prose is the stated requirement; the assertions are the enforced
            # one, and they are consistently stricter. Both are printed, prose first,
            # because the prose says *why* a field is there and the assertion says
            # exactly what shape it has to have.
            asserted = {k: v["assertions"] for k, v in f["dataKeys"].items()
                        if v["assertions"]}
            if asserted:
                A("**Enforced on the response** - the assertions this feature makes")
                A("about each resource's own representation, quoted verbatim:")
                A("")
                for key, items in sorted(asserted.items()):
                    A(f"- `${{data.{key}}}`:")
                    for a in items:
                        if a["kind"] == "fhirpath":
                            what = f"FHIRPath `{a['expr']}`"
                        elif a["kind"] == "attribute":
                            what = f"response attribute `{a['path']}` matches `{a['matches']}`"
                        elif a["kind"] == "raw-body":
                            what = (f"raw response payload matches `{a['matches']}`"
                                    " *(undecoded body, not an element path)*")
                        else:
                            what = f"`{a['element']}` references `{a['target']}`"
                        A(f"    - {what}  (`:{a['line']}`)")
                A("")

            for block in f["preconditionBlocks"]:
                if "description" in block:
                    A(f"**Requirement** (`{f['rel']}:{block['line']}`)")
                    A("")
                    A(_indent_block(block["description"]))
                    A("")
                else:
                    A(f"**Precondition, verbatim** (`{f['rel']}:{block['line']}`)")
                    A("")
                    A("```text")
                    A(block["text"].rstrip())
                    A("```")
                    A("")

    return "\n".join(lines) + "\n"


NO_PROFILE_REASON = {
    "fixture-body": "*none asserted - the suite PUTs its own body here*",
    "never-read": "*none asserted - referenced/searched only, never read directly*",
    "not-asserted": "*none asserted - read directly, but no profile step follows*",
}


def render_inventory(data: dict) -> str:
    """The whole of RESOURCE-INVENTORY.md, across every level."""
    lines: list[str] = []
    A = lines.append
    A("# Resource inventory - what must exist in the SUT before a run")
    A("")
    A("GENERATED FILE - do not edit. Produced by `scripts/lib/extract_requirements.py`.")
    A("")
    A(f"Source of every quoted line: {data.get('source', data['featuresRoot'])}")
    A("")
    A("One row per resource the test suite expects to find, derived by joining the")
    A("suite's own `testdata/**.yaml` key->ID contract against the step shapes that")
    A("name a FHIR resource type explicitly. Several keys can name one resource; those")
    A("are collapsed into a single row, because two rows would mean two conflicting")
    A("definitions of the same instance.")
    A("")
    A("The `type` column is inferred **only** from steps that state the type -")
    A("`/Patient/${data.x}`, `references resource with ID \"Patient/${data.x}\"`,")
    A("`referenced Encounter resource with id \"${data.x}\"`. Nothing is inferred from")
    A("the key's spelling: `account-read-patient-id` merely looks like a Patient, and")
    A("keys with no typed use are listed separately at the end of each level rather")
    A("than guessed at.")
    A("")
    A("The profile column is attributed the way Tiger itself attributes it. A step of")
    A("the form `FHIR current response body is a valid <module> resource and conforms")
    A("to profile \"<canonical>\"` asserts against whatever the preceding")
    A("`Get FHIR resource at` fetched, so each canonical lands on exactly the resource")
    A("the suite lands it on. Name matching would not work here: ISiK profile names are")
    A("German, so `ISiKAbrechnungsfall` is the Account profile and")
    A("`ISiKKontaktGesundheitseinrichtung` the Encounter one.")
    A("")
    A("A row with no profile is **not** an extraction gap - the reason is stated in the")
    A("cell. Such a resource still has a content requirement; it is stated only in the")
    A("precondition prose, which `REQUIREMENTS-Stufe{3,5}.md` quotes verbatim.")
    A("")

    for level, roll in data["levels"].items():
        A(f"## {level}")
        A("")
        in_scope = [r for r in roll["resources"] if r["inScope"]]
        A(f"{len(roll['resources'])} resources, {len(in_scope)} required by at least one")
        A("in-scope case.")
        A("")
        A("| type | id | scope | profile the response must satisfy | keys |")
        A("|---|---|---|---|---|")
        for r in roll["resources"]:
            # Canonical URLs where the suite states one; otherwise the bare profile
            # name from a `... conforms to ISiKPatient profile` step, marked so the
            # author knows it still has to be resolved to a canonical in the
            # package set.
            shown = [f"`{p}`" for p in r["profiles"]]
            shown += [f"`{n}` (by name)" for n in r["profileNames"]]
            profiles = "<br>".join(shown) or NO_PROFILE_REASON.get(
                r["noProfileReason"], "-")
            keys = ", ".join(f"`{k}`" for k in r["keys"])
            A(f"| {r['resourceType']} | `{r['id']}` | {'required' if r['inScope'] else 'optional-only'} "
              f"| {profiles} | {keys} |")
        A("")

        if roll["untypedIdKeys"]:
            A("### ID-shaped keys with no typed use")
            A("")
            A("These name a resource, but no step states its FHIR type - the type has to")
            A("be read out of the precondition prose. Listed so that they are consciously")
            A("resolved rather than silently missing from the inventory.")
            A("")
            A("| key | id | scope | used by |")
            A("|---|---|---|---|")
            for key, info in sorted(roll["untypedIdKeys"].items()):
                A(f"| `{key}` | `{info['value']}` | {'required' if info['inScope'] else 'optional-only'} "
                  f"| {', '.join('`' + u + '`' for u in info['usedBy'])} |")
            A("")

        if roll["keysUndefined"]:
            A("### Keys referenced but never defined")
            A("")
            A("Upstream defects. An undefined key renders as the literal text")
            A("`${data.<key>}` inside the request, which produces a 404 that looks exactly")
            A("like a missing resource in the SUT. See `testdata/*/zz-upstream-key-defects.yaml`")
            A("for which of these are aliased and why.")
            A("")
            for key in roll["keysUndefined"]:
                A(f"- `{key}`")
            A("")

        if roll["malformedTags"]:
            A("### Malformed precondition tags")
            A("")
            A("Upstream defect, harmless at run time and noted only for completeness:")
            A("the tag carries a stray colon, so Cucumber reads a tag name that no tag")
            A("expression mentions. The block executes either way (it is selected by the")
            A("feature-level `@Stufe5` tag, not by its own), and its steps assert nothing,")
            A("so no verdict changes. Normalised here so the prose still reaches the")
            A("requirement tables.")
            A("")
            for rel, tags in sorted(roll["malformedTags"].items()):
                A(f"- `{rel}`: {', '.join('`' + t + '`' for t in tags)}")
            A("")

        if roll["keysUnused"]:
            A("### Keys defined but never referenced")
            A("")
            A("Defined in the suite's own test data and used by no feature file in this")
            A("release. No resource needs to be fabricated for them.")
            A("")
            for key in roll["keysUnused"]:
                A(f"- `{key}`")
            A("")

        if roll["keysCreatedByFixture"]:
            A("### Keys naming a resource the suite creates itself")
            A("")
            A("Each of these appears only as the `\"id\"` of a fixture body the suite")
            A("POSTs or PUTs - so the resource comes into existence *because* the test")
            A("ran. Nothing must be fabricated for them, and fabricating one anyway")
            A("would pre-empt the Create the scenario exists to test.")
            A("")
            for key in roll["keysCreatedByFixture"]:
                A(f"- `{key}`")
            A("")

    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--features-root", required=True, type=Path,
                    help="directory holding Stufe3/ and Stufe5/ feature trees")
    ap.add_argument("--testdata-root", required=True, type=Path,
                    help="directory holding Stufe3/*.yaml and Stufe5/*.yaml")
    ap.add_argument("--out-dir", required=True, type=Path,
                    help="where the three Markdown documents are written")
    # The JSON goes somewhere else by default because it has a different
    # consumer: 50-seed.sh and the gap report read it as a build artefact, while
    # the Markdown is documentation. Keeping them apart stops a reader from
    # editing one and expecting the other to follow.
    ap.add_argument("--json-out", type=Path, default=None,
                    help="path for requirements.json (default: <out-dir>/requirements.json)")
    ap.add_argument("--tags-stufe3", default="@Stufe3 and (not @Optional)")
    ap.add_argument("--tags-stufe5", default="@Stufe5 and (not @Optional)")
    # The levels the pinned release contains - suite_levels() in scripts/lib.sh.
    # Test suite 3.0.0 removed ISiK Level 3, and a level named here but absent
    # from the corpus is an error, not an empty table: an empty requirement table
    # reads as a level that was examined and found to require nothing.
    ap.add_argument("--levels", default="Stufe3 Stufe5")
    # The generation's Tiger configuration templates. Read for one thing only:
    # which namespace each test-data file is loaded under. Omitted, every file is
    # assumed to be in `data`, which is correct for suite 2.x.
    ap.add_argument("--tiger-templates", type=Path, default=None,
                    help="directory of tiger-isik-*.yaml.tmpl, source of the "
                         "filename -> baseKey mapping")
    # What the generated documents name as their source. The local extraction path
    # is not provenance - it says nothing about which release produced these lines
    # and leaks the host's directory layout into an auditable document - so the
    # caller passes the image reference and digest instead.
    ap.add_argument("--source-label", default=None,
                    help="human-readable origin of the feature files, e.g. "
                         "'gematik1/isik-testsuite:2.7.0 (sha256:...)'")
    args = ap.parse_args()

    all_tags = {"Stufe3": args.tags_stufe3, "Stufe5": args.tags_stufe5}
    levels = args.levels.split()
    for lvl in levels:
        if lvl not in all_tags:
            sys.exit(f"--levels names {lvl!r}, which is not an ISiK level this rig knows")

    extraction = Extraction(
        features_root=args.features_root,
        testdata_root=args.testdata_root,
        tag_expr={lvl: all_tags[lvl] for lvl in levels},
        basekeys=load_basekeys(args.tiger_templates) if args.tiger_templates else None,
    )
    data = extraction.analyse()
    data["source"] = args.source_label or str(args.features_root)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    json_out = args.json_out or (args.out_dir / "requirements.json")
    json_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
        encoding="utf-8")
    for level in data["levels"]:
        (args.out_dir / f"REQUIREMENTS-{level}.md").write_text(
            render_requirements(data, level), encoding="utf-8")
    (args.out_dir / "RESOURCE-INVENTORY.md").write_text(render_inventory(data), encoding="utf-8")

    for level, roll in data["levels"].items():
        print(f"{level}: {roll['features']} features, "
              f"{roll['casesAssertive']} asserting cases, "
              f"{len(roll['resources'])} resources to fabricate, "
              f"{len(roll['untypedIdKeys'])} untyped id keys, "
              f"{len(roll['keysUndefined'])} undefined keys", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
