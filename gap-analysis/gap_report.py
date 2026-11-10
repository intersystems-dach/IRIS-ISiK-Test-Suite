#!/usr/bin/env python3
"""Generate the standalone developer gap analysis for one test run.

Audience: an InterSystems IRIS for Health product developer. Assume intimate
knowledge of IRIS including source access, very good FHIR knowledge, and almost no
ISiK knowledge - so every ISiK term is expanded on first use and every requirement
is stated in FHIR terms rather than by citing German specification prose.

Each entry answers exactly the three questions asked of this report:

  1. **What test step was executed** - the resolved Gherkin step, the Serenity
     sub-step that actually failed, and the HTTP exchange, with the feature file
     and line so it can be read in context.
  2. **What FHIR feature was tested for** - the FHIR mechanism (a profile
     constraint, a search parameter, a CapabilityStatement declaration, an
     operation), the ISiK conformance resource that requires it, and the FHIR
     package that supplies it, by `name@version`.
  3. **The expected and the actual result** - the expectation in FHIR terms and
     the verbatim response or validator message.

Everything derived here is a join over two files, and nothing else: the run's own
`analysis/results.json` (what failed) and `gap-analysis/index.json` (what each step
means and which package supplies it). No network, no test suite image, no IRIS. An
archived evidence bundle regenerates an identical report.

Why the report is standalone, and separate from `analysis/results.md`
--------------------------------------------------------------------
`analysis/results.md` is the regulatory results document: every assessment cites
its location in the official Gematik report and it deliberately never says *why* a
test failed. This report is the opposite: it is interpretation, aimed at fixing the
product, and it is not part of the *Bestätigungsverfahren* submission. Keeping them
in separate files keeps the boundary between evidence and interpretation legible to
a German regulatory inspector - the official `test-report.zip` remains the only
authoritative statement of the result.

Deduplication
-------------
Around 1400 test cases share a small number of root causes: one missing search
parameter can fail a whole Scenario Outline, and one missing operation fails every
scenario in its feature. Entries are therefore collapsed by root cause, and each
entry carries the full list of affected scenarios. Without this the report would
run to hundreds of near-identical entries and be useless as a work list, which is
the one thing it has to be.

What is not the product's is segregated
---------------------------------------
Two classes of failure are not IRIS gaps, and both are moved into a clearly
marked appendix and excluded from the gap count - so the headline number cannot
be inflated by anything the product did not do:

  * a failure caused by this project's own seeded data, proven from
    `provenance/seed/<Level>.json` (the pre-run `$validate` and read-back
    records);
  * a failure whose scenario states a precondition this deployment did not meet,
    proven from the request the official report records (see PREREQUISITE_RULES
    in build_index.py). Test suite release 3.0.0 added a module - Connect - whose
    scenarios require SMART-on-FHIR access tokens that the suite itself ships as
    placeholders for the system owner to fill in.

Both exclusions require positive evidence, and both are reported in full. The
error to avoid is in either direction: charging the product for a condition the
environment never created, and quietly excusing a failure that is real.

One appendix is not about the test run at all
--------------------------------------------
§8 records what it took to load the reference validator's own package set into IRIS
in the first place. ``HS.FHIRMeta.Load.NpmLoader`` resolves every declared
dependency by exact package id - no semver range resolution - and one unresolvable
declaration aborts the whole import after the namespace has been created. Two of
the four cases are IRIS's own behaviour, hence in scope for this audience and for
nobody else; the other two are upstream and are named so no time is spent on them.

It is deliberately excluded from the §1 gap count. Counting an installation-time
limitation as a conformance failure would inflate the headline number, and it would
also bury a finding whose owner and whose fix are different.

The HTTP exchange is quoted verbatim
------------------------------------
Including request headers, and therefore including the `Authorization: Basic ...`
header Tiger sent. That is deliberate: this rig is a single-round lab environment
whose credentials are documented on purpose (see `run/CREDENTIALS.md` and section 1
of `analysis/results.md`), and a developer reading an entry has to be able to replay
the exact request that failed without reconstructing it. Nothing is filtered out of
the exchange, so what the entry shows is what went over the wire.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "gap-analysis"))

from build_index import (CATEGORIES, PREREQUISITE_RULES,               # noqa: E402
                         classify_step, classify_substep,
                         context_of_request, shape_of)

MAX_BODY = 700          # characters of a response body quoted in an entry
MAX_SCENARIOS = 12      # affected scenarios listed before "... and N more"

# The two categories that are not the product's: a defect in this project's own
# test data, and a case whose stated precondition this deployment did not meet.
# Both are reported in full, in their own appendix, and both are kept out of the
# gap count - a count that mixes them in overstates the work and, worse, attributes
# to the product something it did not do.
NOT_SUT_CATEGORIES = ("TEST_DATA_DEFECT", "ENVIRONMENT_PREREQUISITE")


# =============================================================================
# Loading
# =============================================================================

def die(msg: str) -> None:
    print(f"gap_report.py: {msg}", file=sys.stderr)
    raise SystemExit(2)


def load_json(path: Path, what: str, required: bool = True):
    if not path.is_file():
        if required:
            die(f"{what} not found at {path}")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        if required:
            die(f"{what} at {path} is not valid JSON: {exc}")
        return None


# =============================================================================
# Category resolution
# =============================================================================
# The order below is the order of increasing evidence. Each step overrides the
# previous one only when it knows strictly more:
#
#   1. the step shape          - what kind of assertion this is
#   2. the request context     - what the assertion was made about
#   3. the HTTP response       - what the SUT actually did
#   4. the seed records        - whether our own data was already broken
#
# Only 4 can turn an IRIS gap into a test-data defect, and it needs positive
# evidence from provenance/seed to do so. Absent that evidence the failure stays
# attributed to the SUT, because quietly excusing a failure is the one error this
# report must not make.

RE_ELEMENT_PATH = re.compile(r"^([A-Z][A-Za-z]*(?:\.[A-Za-z][A-Za-z0-9\[\]*]*)+)\s*:")
RE_INDEX = re.compile(r"\[\d+\]")
RE_FROM_PROFILE = re.compile(r"\(from (https?://[^\s)]+)\)")
RE_ID_IN_PATH = re.compile(r"/([A-Z][A-Za-z]+)/([A-Za-z0-9._-]+)(?:/|$|\?)")
RE_OP_IN_PATH = re.compile(r"/\$([A-Za-z][A-Za-z0-9-]*)")
RE_UUID_OR_TS = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-"
                           r"[0-9a-f]{12}\b|\b\d{4}-\d{2}-\d{2}T[\d:.]+Z?\b")


def normalise_message(msg: str) -> str:
    """A message with the run-specific parts removed, for grouping.

    Two failures with the same normalised message are the same defect: array
    indices, generated ids and timestamps differ between resources and between
    runs without the underlying cause differing.
    """
    if not msg:
        return ""
    out = RE_INDEX.sub("[*]", msg)
    out = RE_UUID_OR_TS.sub("<var>", out)
    return re.sub(r"\s+", " ", out).strip()


def resolve_steps(failure: dict) -> tuple[str, dict, str | None, dict | None]:
    """(gherkin text, gherkin classification, substep text, substep classification).

    results_report.py records the deepest failing Serenity step in `step` and its
    top-level ancestor in `gherkinStep`. Which of those is the Gherkin step depends
    on the failure: for a profile validation the leaf is a Java `@Step`
    (`validate against isik3-basismodul`) and the ancestor is the Gherkin text; for
    a Scenario Outline the leaf *is* the resolved Gherkin text and the ancestor is
    only the Examples row label. Classifying both and asking which is which is more
    robust than trusting either field's position.
    """
    leaf = (failure.get("step") or "").strip()
    ancestor = (failure.get("gherkinStep") or "").strip()

    sub = classify_substep(leaf) if leaf else None
    if sub:
        return ancestor or leaf, classify_step(ancestor or leaf), leaf, sub

    leaf_cls = classify_step(leaf) if leaf else None
    if leaf_cls and leaf_cls["kind"]:
        return leaf, leaf_cls, None, None
    anc_cls = classify_step(ancestor) if ancestor else None
    if anc_cls and anc_cls["kind"]:
        return ancestor, anc_cls, leaf or None, None
    # Neither classified. Prefer whichever text exists so the entry still quotes
    # something real, and let the category fall through to UNCLASSIFIED.
    text = leaf or ancestor
    return text, (leaf_cls or anc_cls or {"kind": None, "role": "assert",
                                          "category": "UNCLASSIFIED", "args": {}}), \
        (ancestor if leaf else None), None


def index_step(index: dict, feature: str, line: int | None,
               scenario: str | None = None, text: str | None = None) -> dict | None:
    """The indexed step for a failure, by line and then by text.

    This is what supplies the request context: the index tracked it across the
    whole feature in Gherkin order, which a single failure record cannot know. It is
    also what supplies the resource type and operation of the request an assertion
    was made about.

    The line is tried first because it is exact. It can miss - Serenity records no
    line for a nested Java `@Step`, and a Scenario Outline's step line is the
    template's, not the row's - so a text match within the same scenario, and then
    within the feature, is tried after it. Losing the join is not harmless: without
    a context the entry degrades to a vaguer FHIR feature and stops grouping with
    its siblings, so it is worth two extra passes.

    The text passes normalise through `build_index.shape_of`, the same function
    that produced every `shape` in the index. This file used to carry its own
    near-copy, and the difference cost 20 of the 21 joins that missed in the first
    full run: the copy rewrote `'arg'` to `"X"` where the index keeps `'X'`, so
    every single-quoted step - which is every FHIRPath assertion - failed to
    match, and it did not touch `<placeholder>`, so an outline template step
    could not match either. Do not reintroduce a local normaliser here.

    The last pass exists for a Gherkin DocString step. Serenity reports the step
    text with the DocString appended, while the index stores the step line alone
    (`FHIR current response body evaluates the FHIRPaths:`), so the shapes cannot
    be equal however they are normalised; the first line is matched instead. It
    is tried last and only within the named scenario, because a bare step line is
    a weaker key than a full shape.
    """
    feat = (index.get("features") or {}).get(feature)
    if not feat:
        return None
    if line is not None:
        for sc in feat["scenarios"]:
            for st in sc["steps"]:
                if st["line"] == line:
                    return st
    if not text:
        return None
    want = re.sub(r"\s+", " ", RE_KEYWORD.sub("", text.strip()))
    want_shape = shape_of(want)
    for restrict in (True, False):
        for sc in feat["scenarios"]:
            if restrict and scenario and sc["name"] != scenario:
                continue
            for st in sc["steps"]:
                if st["shape"] == want_shape or st["shape"] == want:
                    return st
    first = shape_of(RE_KEYWORD.sub("", text.strip()).splitlines()[0].strip())
    if first and scenario:
        for sc in feat["scenarios"]:
            if sc["name"] != scenario:
                continue
            for st in sc["steps"]:
                if st["shape"] == first:
                    return st
    return None


RE_KEYWORD = re.compile(r"^(?:Given|When|Then|And|But|\*)\s+")


def category_from_status(http: dict | None, cls: dict, indexed: dict | None) -> str | None:
    """The category the HTTP response implies. Mirrors index['statusRules']."""
    if not http:
        return None
    status = http.get("statusCode")
    path = http.get("path") or ""
    method = (http.get("method") or "GET").upper()
    is_op = bool(RE_OP_IN_PATH.search(path))

    if is_op and status in (400, 404, 405, 501):
        return "UNSUPPORTED_OPERATION"
    if method in ("POST", "PUT", "PATCH"):
        if status in (404, 405, 501):
            return "CAPABILITY_STATEMENT_GAP"
        if status in (400, 422):
            return "PROFILE_NONCONFORMANCE"
    if status == 404 and method == "GET":
        # A read of a resource the scenario needs. Whether this is our data or the
        # SUT is decided later, from the seed records; default to the SUT.
        return None
    if status in (406, 415):
        return "RESPONSE_FORMAT"
    if isinstance(status, int) and status >= 500:
        return "RESPONSE_FORMAT"
    return None


RE_CAPS_SEARCHPARAM = re.compile(
    r"CapabilityStatement-Definition for search parameter '(?P<param>[^']*)' of "
    r"resource '(?P<resource>[^']*)' is either missing or incorrect")

RE_ASSERTJ_VALUES = re.compile(
    r"^\s*Element value:\s*\n(?P<actual>.*?)\nExpected:\s*\n(?P<expected>.*?)\s*$",
    re.DOTALL)
RE_ATTR_OF_STEP = re.compile(r'with attribute "([^"]*)" matches "')


def response_code_verdict(failure: dict, cls: dict) -> tuple[str, str] | None:
    """(category, reason) for a `$.responseCode` assertion, from its own message.

    Implements index['responseCodeRules']. This exists because a TGR step carries
    no `restQuery`, so `failure['http']` is always None for one and
    `category_from_status` cannot fire - the status the SUT actually returned is
    only ever in the assertion message. See RESPONSE_CODE_RULES for the
    measurement.

    Returns None unless this really is a status-code assertion whose message has
    both values, so a message form this does not recognise stays UNCLASSIFIED
    rather than being classified on a guess.
    """
    attr = RE_ATTR_OF_STEP.search(failure.get("step") or "")
    if not attr or attr.group(1) != "$.responseCode":
        return None
    m = RE_ASSERTJ_VALUES.match(failure.get("message") or "")
    if not m:
        return None
    actual, expected = m.group("actual").strip(), m.group("expected").strip()
    if not actual.isdigit():
        return None
    code = int(actual)
    got = f"the SUT answered {code} where the step required `{expected}`"

    # `expected` is a regex. Which class of status it admits is what decides the
    # category, so it is tested by matching candidate codes against it rather
    # than by comparing strings.
    def admits(*codes: int) -> bool:
        return any(re.fullmatch(expected, str(c)) for c in codes)

    if code >= 500:
        return "RESPONSE_FORMAT", (f"{got}. A 5xx is an unhandled server error "
                                   "rather than a conformance verdict")
    accepted = 200 <= code < 300
    wanted_ok = admits(200, 201, 204)
    wanted_bad = admits(400, 404, 405, 422, 500)
    if accepted and wanted_bad and not wanted_ok:
        return "WRITE_VALIDATION_GAP", (
            f"{got}. The SUT accepted a body the profile forbids")
    if not accepted and wanted_ok and not wanted_bad:
        return "WRITE_VALIDATION_GAP", (
            f"{got}. The SUT rejected a body ISiK requires it to accept")
    if code in (400, 422) and wanted_bad and not wanted_ok:
        return "WRITE_VALIDATION_GAP", (
            f"{got}. Both are rejections, so the defect was detected; the "
            "status chosen for it differs")
    return None


def categorise(failure: dict, cls: dict, sub: dict | None,
               indexed: dict | None) -> tuple[str, str]:
    """(category, one-line reason for the classification)."""
    http = failure.get("http")

    if failure.get("scope") == "outcome" and not failure.get("step"):
        # No step is attributed: the scenario itself did not run to an assertion.
        etype = failure.get("errorType") or ""
        if "Connect" in etype or "Timeout" in etype or "Socket" in etype:
            return "RESPONSE_FORMAT", ("the scenario never reached an assertion - "
                                       "the SUT was unreachable, so this is an "
                                       "environment or availability failure, not a "
                                       "conformance finding")
        # index['responseBodyRules']: the suite's own validator can fail a
        # scenario outside any step, and when it does its message carries the
        # finding in full. Matched on that message, not on the absence of a step.
        m = RE_CAPS_SEARCHPARAM.search(failure.get("message") or "")
        if m:
            return "MISSING_SEARCH_PARAM", (
                f"the suite's CapabilityStatement check reports search parameter "
                f"`{m.group('param')}` of `{m.group('resource')}` as missing or "
                "incorrect. The failure carries no step, so this is read from the "
                "message the suite raised")
        return "UNCLASSIFIED", ("the failure is attributed to the scenario rather "
                                "than to a step, so no step shape is available")

    if sub:
        return sub["category"], f"the failing Serenity step is `{failure['step']}`"

    status_cat = category_from_status(http, cls, indexed)
    if status_cat:
        return status_cat, (f"the SUT answered {http.get('statusCode')} to "
                            f"{http.get('method')} {http.get('path')}")

    # After the recorded exchange, which is stronger evidence, and before the
    # shape defaults: a status-code assertion states the SUT's actual answer in
    # its own message even when no exchange was recorded.
    verdict = response_code_verdict(failure, cls)
    if verdict:
        return verdict

    # index['responseBodyRules']: the parser failed, not the assertion.
    if "HAPI-1857" in (failure.get("message") or ""):
        return "RESPONSE_FORMAT", (
            "the SUT returned no response body, so the FHIRPath had nothing to "
            "evaluate over. IRIS withholds the created resource unless the "
            "request sends `Prefer: return=representation`, which this step "
            "does not")

    if cls.get("category"):
        return cls["category"], f"the step is a `{cls['kind']}` assertion"

    if indexed and indexed.get("category"):
        return indexed["category"], (f"a `{indexed['kind']}` assertion in a "
                                     f"`{indexed['context']}` context")

    if indexed:
        return "UNCLASSIFIED", (f"a `{indexed['kind']}` assertion in a "
                                f"`{indexed['context']}` context, which neither "
                                "the step shape nor the response decides")
    return "UNCLASSIFIED", "the step shape is not in the classification table"


# =============================================================================
# FHIR feature attribution
# =============================================================================

def canonical_record(index: dict, url: str) -> tuple[str, list] | None:
    """Resolve a profile canonical, scheme-insensitively (see build_index)."""
    canon = index.get("canonicals") or {}
    if url in canon:
        return url, canon[url]
    alt = ("http://" + url[8:]) if url.startswith("https://") else \
          ("https://" + url[7:]) if url.startswith("http://") else None
    if alt and alt in canon:
        return alt, canon[alt]
    return None


def requirement_rows(index: dict, bucket: str, key: str) -> list[dict]:
    return ((index.get("conformance") or {}).get(bucket) or {}).get(key) or []


def fmt_requirements(rows: list[dict], level: str | None) -> tuple[list[str], list[str]]:
    """(prose lines, packages) for a set of CapabilityStatement requirement rows.

    Rows for the level under test come first: `Account.read` is required by both an
    ISiK 3 and an ISiK 5 conformance resource, and a developer reading a Stufe 5
    failure wants the Stufe 5 one named first.
    """
    rows = sorted(rows, key=lambda r: (r.get("isikLevel") != level,
                                       r.get("expectation") != "SHALL",
                                       r.get("capabilityStatement") or ""))
    lines, pkgs = [], []
    for r in rows:
        exp = r.get("expectation") or "unspecified expectation"
        lvl = r.get("isikLevel") or "level-independent"
        line = (f"`{exp}` per `{r.get('capabilityStatement')}` ({lvl}), "
                f"shipped by `{r.get('package')}`")
        if r.get("definition"):
            line += f"\n    definition: {r['definition']}"
        lines.append(line)
        if r.get("package") and r["package"] not in pkgs:
            pkgs.append(r["package"])
    return lines, pkgs


def attribute(index: dict, category: str, cls: dict, sub: dict | None,
              failure: dict, indexed: dict | None,
              level: str) -> dict:
    """The FHIR feature under test, and which package supplies it.

    Returns {feature, mechanism, requirements[], packages[], expected, notes[]}.
    Every field is either derived from the index or absent; nothing is invented, so
    an entry that cannot name a package says so instead of guessing.
    """
    args = dict(cls.get("args") or {})
    if sub:
        args.update(sub.get("args") or {})
    if indexed and indexed.get("args"):
        for k, v in indexed["args"].items():
            args.setdefault(k, v)
    target = (indexed or {}).get("target") or {}
    http = failure.get("http") or {}
    message = failure.get("message") or ""
    out: dict = {"feature": None, "mechanism": None, "requirements": [],
                 "packages": [], "expected": None, "notes": []}

    # A scenario-scope CapabilityStatement finding has no step and therefore no
    # step arguments, but its message names the parameter and the resource. Filling
    # them in here lets the MISSING_SEARCH_PARAM branch below do its normal
    # requirement lookup instead of reporting an unattributed finding.
    caps = RE_CAPS_SEARCHPARAM.search(message)
    if caps and category == "MISSING_SEARCH_PARAM":
        args.setdefault("name", caps.group("param"))
        args.setdefault("resourceType", caps.group("resource"))
        # The message names no parameter *type*, and the type is part of the
        # feature label and therefore of the grouping key - so without it the same
        # parameter reported by a step and by the scenario-scope check would become
        # two work items for one defect. Taken from the index only when the ISiK
        # CapabilityStatements declare exactly one type for it, so nothing is
        # invented: an ambiguous parameter keeps its type unstated.
        if not args.get("type"):
            prefix = f"{caps.group('resource')}|{caps.group('param')}|"
            declared = {k.split("|")[2] for k in
                        ((index.get("conformance") or {}).get("searchParams") or {})
                        if k.startswith(prefix)}
            if len(declared) == 1:
                args["type"] = declared.pop()
                out["notes"].append(
                    "the parameter type is not in the message the suite raised; it "
                    "is the one type the ISiK CapabilityStatements declare for this "
                    "parameter")

    # --- profile conformance -------------------------------------------------
    if category == "PROFILE_NONCONFORMANCE":
        module = args.get("validationModule")
        canonical = args.get("profile")
        if not canonical:
            m = RE_FROM_PROFILE.search(message)
            if m:
                canonical = m.group(1)
                out["notes"].append("the profile canonical is taken from the "
                                    "validator message; the step named only the "
                                    "validation module")
        out["mechanism"] = "StructureDefinition (profile) conformance"
        hit = canonical_record(index, canonical) if canonical else None
        if hit:
            declared, (pkg, _rtype, name) = hit
            out["feature"] = f"profile `{name}` ({declared})"
            out["packages"].append(pkg)
            elems = (index.get("profileElements") or {}).get(declared) or {}
            # Which element the validator complained about, and whether the profile
            # constrains it. This turns "the validator said no" into a citation.
            pm = RE_ELEMENT_PATH.match(message)
            if pm and elems:
                path = RE_INDEX.sub("", pm.group(1))
                where = elems.get("constraintSource") or "snapshot"
                req = (elems.get("required") or {}).get(path)
                if req:
                    # The element ids name the slices that impose the minimum -
                    # "the Abrechnungsnummer identifier needs a type", not "every
                    # identifier needs a type".
                    ids = [i for i in req.get("ids", []) if i != path]
                    via = f" via {', '.join(f'`{i}`' for i in ids)}" if ids else ""
                    out["requirements"].append(
                        f"`{name}` constrains `{path}` to `min={req['min']}`"
                        f"{via} in its {where} - the element is required, not "
                        "optional")
                elif path in (elems.get("mustSupport") or []):
                    out["requirements"].append(
                        f"`{name}` marks `{path}` `mustSupport` in its {where} - "
                        "a server claiming this profile must be able to return it, "
                        "though ISiK does not require the element to be populated "
                        "in every instance")
                else:
                    parent = ".".join(path.split(".")[:-1])
                    hint = ""
                    if parent and parent in (elems.get("mustSupport") or []):
                        hint = (f"; its parent `{parent}` is `mustSupport`")
                    out["requirements"].append(
                        f"`{path}` is not constrained in `{name}`'s {where}"
                        f"{hint} - the constraint comes from "
                        f"`{elems.get('baseDefinition') or 'a base profile'}` or "
                        "from base FHIR")
                if elems.get("note"):
                    out["notes"].append(elems["note"])
        elif canonical:
            out["feature"] = f"profile {canonical}"
            out["notes"].append(f"no unpacked FHIR package supplies {canonical}, "
                                "so the supplying package cannot be named")
        if module:
            mod = (index.get("validationModules") or {}).get(module) or {}
            for pkg in mod.get("measuredPackages") or []:
                if pkg not in out["packages"]:
                    out["packages"].append(pkg)
            decl = mod.get("declared") or {}
            deps = decl.get("dependencies") or []
            out["requirements"].append(
                f"asserted by the Gematik reference validator's `{module}` "
                f"validation module" +
                (f" (declared dependencies: {', '.join(deps)})" if deps else ""))
        out["expected"] = (f"the response body validates cleanly against "
                           f"{out['feature'] or 'the profile'}")
        return out

    # --- search parameters ---------------------------------------------------
    if category == "MISSING_SEARCH_PARAM":
        name = args.get("name")
        ptype = args.get("type")
        rtype = args.get("resourceType") or target.get("resourceType")
        out["mechanism"] = "SearchParameter declaration in the CapabilityStatement"
        if name and rtype:
            out["feature"] = (f"search parameter `{name}`"
                              + (f" of type `{ptype}`" if ptype else "")
                              + f" on `{rtype}`")
            rows = requirement_rows(index, "searchParams", f"{rtype}|{name}|{ptype}")
            if not rows:
                rows = [r for k, v in
                        ((index.get("conformance") or {}).get("searchParams") or {}).items()
                        if k.startswith(f"{rtype}|{name}|") for r in v]
                if rows:
                    out["notes"].append(
                        f"no ISiK CapabilityStatement declares `{name}` on "
                        f"`{rtype}` with type `{ptype}`; the rows below are the "
                        "same parameter declared with another type")
            lines, pkgs = fmt_requirements(rows, level)
            out["requirements"].extend(lines)
            out["packages"].extend(pkgs)
            defs = {r.get("definition") for r in rows if r.get("definition")}
            for d in sorted(defs):
                hit = canonical_record(index, d)
                if hit and hit[1][0] not in out["packages"]:
                    out["packages"].append(hit[1][0])
            out["expected"] = (f"`GET [base]/metadata` returns a CapabilityStatement "
                               f"whose `rest.resource.where(type='{rtype}')"
                               f".searchParam` includes `{name}`"
                               + (f" with `type = '{ptype}'`" if ptype else ""))
        return out

    # --- interactions and resource types ------------------------------------
    if category == "CAPABILITY_STATEMENT_GAP":
        code = args.get("code")
        rtype = args.get("resourceType") or target.get("resourceType")
        out["mechanism"] = "interaction declaration in the CapabilityStatement"
        if code and rtype:
            out["feature"] = f"the `{code}` interaction on `{rtype}`"
            lines, pkgs = fmt_requirements(
                requirement_rows(index, "interactions", f"{rtype}|{code}"), level)
            out["requirements"].extend(lines)
            out["packages"].extend(pkgs)
            out["expected"] = (f"`GET [base]/metadata` returns a CapabilityStatement "
                               f"whose `rest.resource.where(type='{rtype}')"
                               f".interaction` includes `{code}`")
        elif code:
            out["feature"] = f"the system-level `{code}` interaction"
            out["expected"] = (f"`GET [base]/metadata` declares `rest.interaction` "
                               f"`{code}`")
        elif http:
            out["feature"] = (f"`{http.get('method')}` on "
                              f"`{target.get('resourceType') or http.get('path')}`")
            out["expected"] = "the endpoint accepts the interaction"
        return out

    # --- operations ----------------------------------------------------------
    if category == "UNSUPPORTED_OPERATION":
        code = args.get("code") or target.get("operation")
        if not code and http.get("path"):
            m = RE_OP_IN_PATH.search(http["path"])
            code = m.group(1) if m else None
        rtype = args.get("resourceType") or target.get("resourceType")
        scope = target.get("scope")
        out["mechanism"] = "FHIR operation ($-operation)"
        if code:
            out["feature"] = (f"operation `${code}`"
                              + (f" on `{rtype}`" if rtype else "")
                              + (f" ({scope}-level)" if scope else ""))
            rows = requirement_rows(index, "operations", f"{rtype}|{code}") \
                or requirement_rows(index, "operations", f"*|{code}")
            lines, pkgs = fmt_requirements(rows, level)
            out["requirements"].extend(lines)
            out["packages"].extend(pkgs)
            # The OperationDefinition itself: its declared scopes are what a
            # developer implements against.
            for url, od in ((index.get("conformance") or {})
                            .get("operationDefinitions") or {}).items():
                if od.get("code") != code:
                    continue
                if rtype and od.get("resource") and rtype not in od["resource"]:
                    continue
                out["requirements"].append(
                    f"`OperationDefinition/{od.get('name')}` ({url}) - applies to "
                    f"{', '.join(od.get('resource') or ['any resource'])} at "
                    f"{', '.join(od.get('scopes') or ['unspecified'])} level, "
                    f"shipped by `{od.get('package')}`")
                if od.get("package") not in out["packages"]:
                    out["packages"].append(od["package"])
            out["expected"] = (f"the endpoint implements `${code}` and the "
                              "invocation returns the operation's declared "
                              "response")
        return out

    # --- search semantics ----------------------------------------------------
    if category == "SEARCH_SEMANTICS":
        rtype = target.get("resourceType")
        query = target.get("query")
        out["mechanism"] = "FHIR search"
        out["feature"] = (f"search on `{rtype}`" if rtype else "FHIR search") + \
                         (f" with `{query}`" if query else "")
        if rtype and query:
            for part in query.split("&"):
                pname = part.split("=", 1)[0].split(":", 1)[0]
                if not pname or pname.startswith("_"):
                    continue
                rows = [r for k, v in ((index.get("conformance") or {})
                                       .get("searchParams") or {}).items()
                        if k.startswith(f"{rtype}|{pname}|") for r in v]
                lines, pkgs = fmt_requirements(rows, level)
                if lines:
                    out["requirements"].append(f"parameter `{pname}`: " + lines[0])
                out["packages"].extend(p for p in pkgs if p not in out["packages"])
        expr = args.get("expr")
        out["expected"] = (f"the search result Bundle satisfies `{expr}`" if expr
                           else "the search returns exactly the expected result set")
        return out

    # --- reference integrity -------------------------------------------------
    if category == "REFERENCE_INTEGRITY":
        elem = args.get("element")
        rid = args.get("id")
        out["mechanism"] = "Reference.reference resolution"
        out["feature"] = (f"reference element `{elem}`" if elem
                          else "resource references")
        out["expected"] = (f"`{elem}` resolves to `{rid}`" if elem and rid
                           else "every reference resolves to the expected resource")
        rtype = target.get("resourceType")
        if rtype and elem:
            # Which ISiK profile constrains this reference element. Several
            # profiles can constrain the same resource type, so the candidates are
            # ordered - the level under test first, then by canonical - rather than
            # taking whichever the dict happened to yield first, so the citation is
            # the same on every run.
            base = elem.split(".")[0]
            path = f"{rtype}.{base}"
            cands = sorted(
                ((c, v) for c, v in (index.get("profileElements") or {}).items()
                 if v.get("type") == rtype
                 and ((v.get("required") or {}).get(path)
                      or path in (v.get("mustSupport") or []))),
                key=lambda cv: (cv[1].get("isikLevel") != level, cv[0]))
            for _canonical, elems in cands[:2]:
                req = (elems.get("required") or {}).get(path)
                out["requirements"].append(
                    f"`{elems.get('name')}` constrains `{path}` "
                    f"({f'''min={req['min']}''' if req else 'mustSupport'}), "
                    f"shipped by `{elems.get('package')}`")
                if elems.get("package") not in out["packages"]:
                    out["packages"].append(elems["package"])
            if len(cands) > 2:
                out["notes"].append(f"{len(cands) - 2} further ISiK profile(s) "
                                    f"constrain `{path}`; the two most relevant to "
                                    f"{level} are cited")
        return out

    # --- write-path validation ----------------------------------------------
    if category == "WRITE_VALIDATION_GAP":
        out["mechanism"] = "validation performed on create/update"
        # Deliberately not named after a resource type. The group is keyed on the
        # status pair, so it spans whatever writes hit the same gap - measured: one
        # group covers Appointment and Composition posts across both levels - and a
        # title naming the first occurrence's type would misdescribe the rest. The
        # per-occurrence table below names each one.
        out["feature"] = "write validation (create/update)"
        m = RE_ASSERTJ_VALUES.match(message)
        if m:
            actual, expected = m.group("actual").strip(), m.group("expected").strip()
            out["expected"] = (f"the SUT answers `{expected}` to this write; it "
                               f"answered `{actual}`")
            out["requirements"].append(
                "the status is the assertion: the scenario posts a body that is "
                "deliberately conformant or deliberately not, and the status code "
                "is how ISiK requires the SUT to say which it took it for")
        # No `restQuery` is recorded for a TGR step, so say where the status came
        # from rather than leaving the reader to assume an exchange was captured.
        out["notes"].append(
            "the status codes are read from the assertion message: Tiger issues "
            "this request through its own proxy, so the Serenity report carries "
            "no `restQuery` for the exchange")
        return out

    # --- response format ----------------------------------------------------
    if category == "RESPONSE_FORMAT":
        attr = args.get("attribute")
        out["mechanism"] = "FHIR RESTful API - response envelope"
        if "HAPI-1857" in message:
            rtype = args.get("resourceType") or target.get("resourceType")
            out["feature"] = ("response body of a create"
                              + (f" on `{rtype}`" if rtype else ""))
            out["expected"] = ("the create returns the stored resource, or a "
                               "`Parameters` wrapping it, so the following "
                               "FHIRPath assertions have something to read")
            out["requirements"].append(
                "the scenario asserts `($this is <Type>) or ($this is Parameters)` "
                "on the create response and sends no `Prefer` header, so it "
                "requires the body unconditionally")
            out["notes"].append(
                "measured on this SUT: the same POST returns the resource when "
                "`Prefer: return=representation` is sent and an empty body with "
                "`Location` set when it is not")
            return out
        if args.get("id"):
            out["feature"] = "logical id echoed by a read"
            out["expected"] = (f"a read of `{http.get('path') or 'the resource'}` "
                               f"returns a resource with `id = "
                               f"'{args['id']}'`")
        elif attr:
            out["feature"] = f"response attribute `{attr}`"
            out["expected"] = (f"`{attr}` matches `{args.get('matches')}`"
                               if args.get("matches") else f"`{attr}` is set")
        elif failure.get("scope") == "outcome":
            out["feature"] = "SUT reachability"
            out["expected"] = "the SUT answers the request"
            out["notes"].append("this is an environment failure, not a conformance "
                                "finding: no assertion was reached. Fix the "
                                "environment and re-run before reading any other "
                                "entry in this report.")
        else:
            out["feature"] = "response format"
            out["expected"] = "the declared content type and status code"
        ctype = http.get("contentType")
        if ctype:
            out["requirements"].append(
                "FHIR R4 requires the response `Content-Type` to match the "
                f"requested format; the SUT returned `{ctype}`")
        return out

    # --- test data -----------------------------------------------------------
    if category == "TEST_DATA_DEFECT":
        out["mechanism"] = "this project's seeded test data"
        out["feature"] = "test fixture, not a SUT feature"
        out["expected"] = "the resource the scenario needs exists and is conformant"
        return out

    out["mechanism"] = "unclassified"
    out["expected"] = "see the quoted step"
    return out


# =============================================================================
# Test data segregation
# =============================================================================

def load_seed(run_dir: Path) -> dict[str, dict]:
    """level -> {ids: {(Type,id): record}, counts, present: bool}.

    Read defensively: the seed record's per-resource rows are written by
    scripts/lib/seed_client.py and carry `id`, `validate.errors` and `read.ok`, but
    an older or partial bundle may hold fewer fields. A missing field means "no
    evidence", never "no defect".
    """
    out: dict[str, dict] = {}
    seed_dir = run_dir / "provenance" / "seed"
    for level in ("Stufe3", "Stufe5"):
        rec = load_json(seed_dir / f"{level}.json", f"seed record for {level}",
                        required=False)
        entry: dict = {"present": rec is not None, "ids": {}, "counts": {},
                       "assertionFailures": {}}
        if rec:
            entry["counts"] = rec.get("counts") or {}
            for res in rec.get("resources") or []:
                rtype, rid = res.get("resourceType"), res.get("id")
                if rtype and rid:
                    entry["ids"][(rtype, rid)] = res
        arec = load_json(seed_dir / f"assertions-{level}.json",
                         f"assertion check for {level}", required=False)
        if arec:
            for res in arec.get("resources") or []:
                rtype, rid = res.get("resourceType"), res.get("id")
                bad = [r for r in (res.get("results") or []) if r.get("ok") is False]
                if rtype and rid and bad:
                    entry["assertionFailures"][(rtype, rid)] = bad
        out[level] = entry
    return out


def test_data_evidence(seed: dict, level: str, http: dict | None) -> dict | None:
    """Positive evidence that the resource this failure touched was already broken.

    Only the SUT-facing artefacts of the seed step count: a write that failed, a
    read-back that failed, a pre-run `$validate` error, or a pre-run assertion check
    that found the required field missing. Anything less and the failure stays with
    the SUT.
    """
    if not http or not http.get("path"):
        return None
    entry = seed.get(level) or {}
    if not entry.get("present"):
        return None
    m = RE_ID_IN_PATH.search(http["path"])
    if not m:
        return None
    key = (m.group(1), m.group(2))
    reasons = []
    res = (entry.get("ids") or {}).get(key)
    if res:
        write = res.get("write") or {}
        read = res.get("read") or {}
        val = res.get("validate") or {}
        if write.get("status") and not (200 <= int(write["status"]) < 300):
            reasons.append(f"the seed write returned HTTP {write['status']}")
        if read.get("ok") is False:
            reasons.append(f"the pre-run read-back failed "
                           f"(HTTP {read.get('status')}: {read.get('detail') or '-'})")
        for err in (val.get("errors") or [])[:4]:
            reasons.append(f"pre-run `$validate` reported: {err}")
    for row in (entry.get("assertionFailures") or {}).get(key, [])[:4]:
        reasons.append(f"the pre-run assertion check failed at {row.get('cite')}: "
                       f"{row.get('detail')}")
    if not reasons:
        return None
    return {"resourceType": key[0], "id": key[1], "reasons": reasons,
            "source": f"provenance/seed/{level}.json"}


# =============================================================================
# Bodies from the official archive
# =============================================================================

RQ_FIELDS = ("method", "path", "statusCode", "contentType", "responseBody",
             "content", "requestHeaders", "responseHeaders")


def load_bodies(run_dir: Path, case: dict) -> dict:
    """The HTTP exchange the failing assertion was made about.

    results.json deliberately omits response bodies - they are large and they are
    already in the official archive. This reads them back from the outcome JSON the
    case cites, so the "actual result" in an entry is the SUT's own words.

    Which exchange is the right one matters. The assertion that failed was made
    about the *most recent* request at that point, which is usually in an earlier
    step: `Get FHIR resource at ...` carries the `restQuery`, and the following
    `FHIR current response body ...` step carries only the exception. So the tree is
    walked in Serenity's own order, remembering the last exchange seen, and stops at
    the first failing step. Taking the first `restQuery` in the file instead would
    quote a passing request; taking the last would quote a request made after the
    failure by a later scenario.

    Failure to read is not fatal - the entry falls back to the message alone.
    """
    rel = ((case.get("report") or {}).get("outcomeJson") or "")
    if not rel:
        return {}
    doc = load_json(run_dir / rel, "outcome JSON", required=False)
    if not isinstance(doc, dict):
        return {}

    last: dict = {}

    def walk(steps) -> bool:
        """True once a failing step has been reached; stops the walk."""
        nonlocal last
        for st in steps or []:
            if not isinstance(st, dict):
                continue
            rq = st.get("restQuery")
            if isinstance(rq, dict):
                last = {k: rq[k] for k in RQ_FIELDS if rq.get(k) is not None}
            failed = st.get("result") in ("FAILURE", "ERROR")
            # Descend first: a failing parent's children hold the finer-grained
            # step, and one of them may carry the exchange.
            if walk(st.get("children")):
                return True
            if failed:
                return True
        return False

    walk(doc.get("testSteps"))
    return last


# The suite's own placeholder for an access token the system owner must supply,
# shipped as the value of all six token keys in the Connect module's
# testdata/Stufe5/connect.yaml inside the suite image. Matched as a literal
# because that is what it is; a suite that renames it shows up as these cases
# returning to their SUT-side categories, which is the safe direction.
UNRESOLVED_TOKEN = "SET-YOUR-TOKEN-HERE"
SMART_DISCOVERY_PATH = "/.well-known/smart-configuration"


def request_as_resolved(run_dir: Path, case: dict) -> str:
    """The request the failing assertion was made about, as the suite resolved it.

    Tiger records each step twice: the Gherkin text with `${...}` references
    intact, and - in `reportData` - the same step with every reference resolved,
    plus the equivalent cURL command. For a TGR step that resolved form is the
    only record of what actually went on the wire: Tiger issues the request
    through its own proxy, so Serenity's `restQuery` is absent (see load_bodies).

    Walked in Serenity's order and stopped at the first failing step, exactly as
    load_bodies does, so the text returned belongs to the exchange the assertion
    was about and not to a later one. Returns "" when nothing was recorded, which
    reads as "no evidence" everywhere it is used.
    """
    rel = ((case.get("report") or {}).get("outcomeJson") or "")
    if not rel:
        return ""
    doc = load_json(run_dir / rel, "outcome JSON", required=False)
    if not isinstance(doc, dict):
        return ""

    last = ""

    def walk(steps) -> bool:
        nonlocal last
        for st in steps or []:
            if not isinstance(st, dict):
                continue
            texts = [rd.get("contents") or "" for rd in (st.get("reportData") or [])
                     if isinstance(rd, dict)
                     and rd.get("title") in ("Tiger Resolved Step Description",
                                             "cURL")]
            # A request step, not an assertion step: only those carry a cURL.
            if any("curl " in t for t in texts):
                last = "\n".join(texts)
            failed = st.get("result") in ("FAILURE", "ERROR")
            if walk(st.get("children")):
                return True
            if failed:
                return True
        return False

    walk(doc.get("testSteps"))
    return last


def prerequisite_verdict(resolved: str, tokens_unresolved: bool
                         ) -> tuple[str, str, str] | None:
    """Whether this case's own precondition went unmet - see PREREQUISITE_RULES.

    Positive evidence only, and from the official report rather than from a
    module or feature name: a Connect scenario that fails with a real token in
    hand is a conformance finding and has to stay one.
    """
    if UNRESOLVED_TOKEN in resolved:
        return "ENVIRONMENT_PREREQUISITE", (
            f"the request carries `Authorization: Bearer {UNRESOLVED_TOKEN}` - the "
            "suite's own placeholder, resolved verbatim in the official report. "
            "The scenario's precondition is a valid Bearer token for a named "
            "scope; none was issued, so no scope was presented to the SUT"
        ), "no-access-token-issued"
    if tokens_unresolved and SMART_DISCOVERY_PATH in resolved:
        return "ENVIRONMENT_PREREQUISITE", (
            f"the request is for `{SMART_DISCOVERY_PATH}`, the SMART-on-FHIR "
            "authorization discovery document, and this run's unresolved token "
            "placeholders are its own evidence that no authorization server was "
            "configured for the endpoint to describe"
        ), "no-authorization-server-configured"
    return None


def truncate(text: str, limit: int = MAX_BODY) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"\n… [truncated, {len(text)} characters total]"


# =============================================================================
# Grouping
# =============================================================================

def root_cause_key(category: str, attribution: dict, failure: dict,
                   cls: dict, sub: dict | None, prereq_rule: str | None = None
                   ) -> tuple:
    """The identity of a defect, independent of which scenario tripped over it.

    Levels are deliberately *not* part of the key: a search parameter missing on
    `Account` is one IRIS defect whether ISiK 3 or ISiK 5 asked for it, and the
    entry lists both levels. Splitting by level would double the work list and
    misrepresent the amount of work.
    """
    args = dict(cls.get("args") or {})
    if sub:
        args.update(sub.get("args") or {})
    feature = attribution.get("feature") or ""
    if prereq_rule:
        # One entry per unmet precondition, not per scenario that tripped over
        # it: the finding is the prerequisite, and the scenarios are its
        # occurrence list. The step shapes differ across these cases, so the
        # generic key below would split one environmental fact into four.
        return (category, prereq_rule)
    if category in ("MISSING_SEARCH_PARAM", "CAPABILITY_STATEMENT_GAP",
                    "UNSUPPORTED_OPERATION"):
        return (category, feature)
    if category == "PROFILE_NONCONFORMANCE":
        return (category, feature, normalise_message(failure.get("message") or ""))
    if category == "WRITE_VALIDATION_GAP":
        # Keyed on the actual/expected pair rather than on the message, so that
        # every scenario where the SUT accepted a body ISiK forbids is one work
        # item however many feature files it occurs in. The generic key below
        # would split them by module, because the message quotes the status only.
        m = RE_ASSERTJ_VALUES.match(failure.get("message") or "")
        pair = f"{m.group('actual').strip()}->{m.group('expected').strip()}" \
            if m else normalise_message(failure.get("message") or "")
        return (category, pair)
    if category == "TEST_DATA_DEFECT":
        return (category, feature, normalise_message(failure.get("message") or ""))
    return (category, cls.get("kind") or "-", failure.get("errorType") or "-",
            normalise_message(failure.get("message") or ""))


# =============================================================================
# Analysis
# =============================================================================

def analyse(results: dict, index: dict, seed: dict, run_dir: Path,
            reqs: dict | None) -> dict:
    groups: dict[tuple, dict] = {}
    per_level: dict[str, dict] = {}
    warnings: list[str] = []
    unjoined: list[str] = []

    feature_names = {}
    for feat in (reqs or {}).get("features", []):
        feature_names[feat["rel"]] = feat.get("name")

    # One pass for the requests, before any classification, because the second
    # prerequisite rule is about the run and not about a single case: whether this
    # round supplied the access tokens the Connect module asks for is decided by
    # all of its failures together. Read once per case and kept, so the outcome
    # JSONs are not parsed twice.
    resolved_requests: dict[str, str] = {}
    for level, L in (results.get("levels") or {}).items():
        for case in L.get("cases") or []:
            if not case.get("failure") or case.get("verdict") != "fail":
                continue
            rel = ((case.get("report") or {}).get("outcomeJson") or "")
            if rel and rel not in resolved_requests:
                resolved_requests[rel] = request_as_resolved(run_dir, case)
    tokens_unresolved = any(UNRESOLVED_TOKEN in text
                            for text in resolved_requests.values())

    for level, L in (results.get("levels") or {}).items():
        totals = L.get("totals") or {}
        per_level[level] = {
            "level": level,
            "directory": L.get("directory"),
            "totals": totals,
            "expected": L.get("expected"),
            "official": L.get("official"),
            "notExecuted": len(L.get("notExecuted") or []),
            "failuresSeen": 0,
            "byCategory": {},
            "byModule": {},
        }
        for case in L.get("cases") or []:
            failure = case.get("failure")
            if not failure or case.get("verdict") != "fail":
                continue
            per_level[level]["failuresSeen"] += 1

            gherkin, cls, substep, sub = resolve_steps(failure)
            indexed = index_step(index, case.get("feature"),
                                 failure.get("stepLine"),
                                 case.get("scenario"), gherkin)
            # A scenario-scope failure names no step, so there is nothing to join and
            # nothing to warn about; only a step that exists and did not match the
            # corpus is a real symptom of index drift.
            if indexed is None and gherkin:
                # `stepLine` where Serenity gave one, and the scenario's own
                # declaration line otherwise. Which of the two it is has to be
                # said: the fallback used to be printed bare, as though it were a
                # step line, which sent the first investigation of this warning
                # looking for a step at a `Scenario:` line.
                where = (f"step line {failure['stepLine']}"
                         if failure.get("stepLine") is not None
                         else f"scenario at line {case.get('featureLine')}, "
                              "no step line recorded")
                indexed_feature = case.get("feature") in (index.get("features") or {})
                unjoined.append(f"{case.get('feature')} ({where}) "
                                f"({case.get('scenario')})"
                                + ("" if indexed_feature else " [feature not indexed]"))
            category, reason = categorise(failure, cls, sub, indexed)

            # Precondition override, on positive evidence only, and ahead of the
            # test-data override: a case whose own precondition was never met
            # states nothing about either the SUT or our seeded data, so neither
            # of them is the thing to look at.
            prereq = prerequisite_verdict(
                resolved_requests.get(
                    (case.get("report") or {}).get("outcomeJson") or "", ""),
                tokens_unresolved)
            if prereq:
                category, reason, prereq_rule = prereq
            else:
                prereq_rule = None
                # Test-data override, on positive evidence only.
                evidence = test_data_evidence(seed, level, failure.get("http"))
                if evidence and category != "RESPONSE_FORMAT":
                    category, reason = "TEST_DATA_DEFECT", (
                        f"the seed step already recorded a defect on "
                        f"{evidence['resourceType']}/{evidence['id']}")
                elif category == "TEST_DATA_DEFECT" and not evidence:
                    warnings.append(
                        f"{case.get('feature')}:{case.get('featureLine')} was "
                        "categorised as a test-data defect without seed evidence; "
                        "re-attributed to the SUT")
                    category = "UNCLASSIFIED"

            attribution = attribute(index, category, cls, sub, failure, indexed,
                                    level)
            key = root_cause_key(category, attribution, failure, cls, sub,
                                 prereq_rule)

            grp = groups.get(key)
            if grp is None:
                grp = groups[key] = {
                    "key": [str(k) for k in key],
                    "category": category,
                    "reason": reason,
                    "mechanism": attribution["mechanism"],
                    "fhirFeature": attribution["feature"],
                    "requirements": attribution["requirements"],
                    "packages": attribution["packages"],
                    "expected": attribution["expected"],
                    "notes": attribution["notes"],
                    "irisLocus": (CATEGORIES.get(category) or {}).get("irisLocus"),
                    "gherkinStep": gherkin,
                    "substep": substep,
                    "stepKind": cls.get("kind"),
                    "requestContext": (indexed or {}).get("context"),
                    "errorType": failure.get("errorType"),
                    "message": failure.get("message"),
                    "http": failure.get("http"),
                    "testDataEvidence": evidence,
                    "levels": [],
                    "modules": [],
                    "occurrences": [],
                    "downstreamOf": (indexed or {}).get("inheritedFromScenario"),
                }
                bodies = load_bodies(run_dir, case)
                if bodies:
                    grp["exchange"] = bodies
            grp["occurrences"].append({
                "level": level,
                "module": case.get("module"),
                "feature": case.get("feature"),
                "featureName": feature_names.get(case.get("feature")),
                "featureLine": case.get("featureLine"),
                "stepLine": failure.get("stepLine"),
                "scenario": case.get("scenario"),
                "example": (case.get("example") or {}).get("label"),
                "exampleData": (case.get("example") or {}).get("data"),
                "result": case.get("result"),
                "reportJson": (case.get("report") or {}).get("outcomeJson"),
                "reportHtml": (case.get("report") or {}).get("outcomeHtml"),
                "message": failure.get("message"),
            })
            if level not in grp["levels"]:
                grp["levels"].append(level)
            if case.get("module") and case["module"] not in grp["modules"]:
                grp["modules"].append(case["module"])

            pl = per_level[level]
            pl["byCategory"][category] = pl["byCategory"].get(category, 0) + 1
            mod = case.get("module") or "(unknown)"
            pl["byModule"].setdefault(mod, {})
            pl["byModule"][mod][category] = pl["byModule"][mod].get(category, 0) + 1

    # Order the work list by impact: most-affected first, test data last.
    order = {c: i for i, c in enumerate(
        ["UNSUPPORTED_OPERATION", "CAPABILITY_STATEMENT_GAP", "MISSING_SEARCH_PARAM",
         "PROFILE_NONCONFORMANCE", "WRITE_VALIDATION_GAP", "SEARCH_SEMANTICS",
         "REFERENCE_INTEGRITY", "AUTHORIZATION_SCOPE", "RESPONSE_FORMAT",
         "UNCLASSIFIED", "TEST_DATA_DEFECT", "ENVIRONMENT_PREREQUISITE"])}
    entries = sorted(groups.values(),
                     key=lambda g: (order.get(g["category"], 99),
                                    -len(g["occurrences"]),
                                    g["fhirFeature"] or ""))
    for i, g in enumerate(entries, 1):
        g["id"] = f"GAP-{i:03d}"
        g["affectedCases"] = len(g["occurrences"])

    # Reconciliation: the plan requires the category counts to sum to the official
    # failure total, with test-data items visibly segregated. Computed, printed and
    # written - not asserted - so a mismatch is a finding rather than a crash.
    official_failed = sum((L.get("totals") or {}).get("failed", 0)
                          for L in (results.get("levels") or {}).values())
    seen = sum(pl["failuresSeen"] for pl in per_level.values())
    by_cat: dict[str, int] = {}
    for pl in per_level.values():
        for c, n in pl["byCategory"].items():
            by_cat[c] = by_cat.get(c, 0) + n
    data_defects = by_cat.get("TEST_DATA_DEFECT", 0)
    prerequisites = by_cat.get("ENVIRONMENT_PREREQUISITE", 0)
    recon = {
        "officialFailedCases": official_failed,
        "failureRecordsAnalysed": seen,
        "categorised": sum(by_cat.values()),
        "byCategory": by_cat,
        "testDataDefects": data_defects,
        "environmentPrerequisites": prerequisites,
        "irisGapCases": seen - data_defects - prerequisites,
        "entries": len(entries),
        "irisGapEntries": sum(1 for e in entries
                              if e["category"] not in NOT_SUT_CATEGORIES),
        "dedupRatio": (round(seen / len(entries), 1) if entries else None),
        "balanced": official_failed == seen == sum(by_cat.values()),
    }
    if not recon["balanced"]:
        warnings.append(
            f"the category counts ({sum(by_cat.values())}) and the failure records "
            f"analysed ({seen}) do not both equal the official failed-case total "
            f"({official_failed}). Every failing case must produce exactly one "
            "categorised finding; investigate before using this report.")
    if unjoined:
        shown = ", ".join(unjoined[:5])
        more = f" (and {len(unjoined) - 5} more)" if len(unjoined) > 5 else ""
        # Do not name a cause this code has not established. The earlier text
        # asserted a version mismatch and told the operator to rebuild the index;
        # in the one run where the warning fired that diagnosis was wrong - the
        # index was the pinned 2.7.0 with all 220 features present - and it sent
        # the investigation in the wrong direction. `source` and `[feature not
        # indexed]` are what actually distinguish the two possible causes.
        src = ((index.get("source") or {}).get("suite")
               if isinstance(index.get("source"), dict) else None)
        warnings.append(
            f"{len(unjoined)} failing step(s) could not be matched to the indexed "
            f"feature corpus, so their FHIR-feature attribution is based on the "
            f"step text alone and they may not group with related findings: "
            f"{shown}{more}. The index was built from "
            f"{src or 'an unrecorded source'} and holds "
            f"{len(index.get('features') or {})} feature file(s). If the entries "
            "above are marked `[feature not indexed]`, the index is from a "
            "different test suite version and `make packages` will rebuild it; if "
            "they are not, the feature is indexed and the step text itself did not "
            "match any shape in it, which is a defect in `index_step` and not "
            "something the operator can fix by rebuilding.")
    unclassified = by_cat.get("UNCLASSIFIED", 0)
    if unclassified:
        warnings.append(
            f"{unclassified} failure(s) could not be categorised. Check "
            "gap-analysis/index.json's `unclassifiedShapes` and `shapeDrift`: the "
            "test suite version probably introduced a step shape the "
            "classification table does not know.")

    return {"entries": entries, "levels": per_level, "reconciliation": recon,
            "warnings": warnings, "unjoinedSteps": unjoined}


# =============================================================================
# Rendering
# =============================================================================

CAT_TITLE = {
    "UNSUPPORTED_OPERATION": "Unsupported FHIR operation",
    "PROFILE_NONCONFORMANCE": "Profile non-conformance",
    "WRITE_VALIDATION_GAP": "Write-path validation gap",
    "MISSING_SEARCH_PARAM": "Missing search parameter",
    "CAPABILITY_STATEMENT_GAP": "CapabilityStatement gap",
    "SEARCH_SEMANTICS": "Search semantics",
    "RESPONSE_FORMAT": "Response format / REST envelope",
    "REFERENCE_INTEGRITY": "Reference integrity",
    "AUTHORIZATION_SCOPE": "SMART-on-FHIR scope enforcement",
    "TEST_DATA_DEFECT": "Test data defect (ours, not IRIS)",
    "ENVIRONMENT_PREREQUISITE": "Precondition not met by this environment "
                                "(neither IRIS nor our test data)",
    "UNCLASSIFIED": "Unclassified",
}

# Categories for which no FHIR package can be named, because the behaviour they
# describe is base FHIR REST rather than anything a package declares. Listed so
# the entry says that instead of reporting a failed package lookup.
NO_SUPPLYING_PACKAGE = {"WRITE_VALIDATION_GAP", "RESPONSE_FORMAT"}

# Who owns each class of package-loader finding in §8. Deliberately blunter than
# the wording in analysis/results.md: that document has to be even-handed for an
# inspector, this one is a work list, and a developer needs to know in one glance
# whether a row is theirs to fix.
LOADER_OWNERS = {
    "wildcard": "**IRIS** — the loader; the package is correct as published",
    "absent-version": "upstream IG — declares a version never published",
    "excluded-from-sut": "this project — the excluded example corpus",
}


def md_escape(text: str) -> str:
    return (text or "").replace("|", "\\|")


def render(results: dict, index: dict, analysis: dict, run_dir: Path,
           generated_at: str) -> str:
    L = analysis["levels"]
    recon = analysis["reconciliation"]
    entries = analysis["entries"]
    iris_entries = [e for e in entries
                    if e["category"] not in NOT_SUT_CATEGORIES]
    data_entries = [e for e in entries if e["category"] == "TEST_DATA_DEFECT"]
    prereq_entries = [e for e in entries
                      if e["category"] == "ENVIRONMENT_PREREQUISITE"]
    src = results.get("source") or {}
    out: list[str] = []
    w = out.append

    # --- header --------------------------------------------------------------
    w(f"# ISiK conformance gap analysis — {results.get('runId')}")
    w("")
    w("Audience: InterSystems IRIS for Health developers. This is a work list, not "
      "a conformance statement.")
    w("")
    w("**ISiK** (*Informationstechnische Systeme im Krankenhaus*) is Gematik's "
      "mandatory FHIR interoperability specification for German hospital "
      "information systems. It is a set of FHIR R4 implementation guides plus a "
      "conformance test suite; *Stufe 3* and *Stufe 5* are two successive stages "
      "of it, each with its own package set, tested independently. Conformance is "
      "certified through Gematik's *Bestätigungsverfahren*, for which the "
      "authoritative artefact is the test suite's own `test-report.zip` — not this "
      "document.")
    w("")
    w("| | |")
    w("|---|---|")
    w(f"| Run | `{results.get('runId')}` |")
    w(f"| Generated | {generated_at} |")
    w(f"| Generated by | `gap-analysis/gap_report.py` |")
    for level, pl in sorted(L.items()):
        run = (results.get('levels') or {}).get(level, {}).get('run') or {}
        sut = run.get("sut") or {}
        tool = run.get("testTool") or {}
        w(f"| {level} SUT | `{sut.get('image', '?')}` "
          f"({sut.get('irisBuild', 'build unknown')}), namespace "
          f"`{sut.get('namespace', '?')}`, endpoint `{sut.get('baseUrl', '?')}` |")
        w(f"| {level} SUT image digest | `{sut.get('imageDigest', 'unrecorded')}` |")
        w(f"| {level} test suite | `{tool.get('image', '?')}` "
          f"(`{tool.get('imageDigest', 'digest unrecorded')}`) |")
    w(f"| Test suite feature corpus | {(index.get('source') or {}).get('suite') or 'unrecorded'} |")
    w(f"| Attribution index | `gap-analysis/index.json`, built "
      f"{index.get('generatedAt')} |")
    w("")
    w("Every claim below is a join over two files and nothing else: this run's "
      "`analysis/results.json` (what failed) and `gap-analysis/index.json` (what "
      "each step asserts and which FHIR package supplies it). Re-running "
      "`make gap-report` on an archived bundle reproduces this document exactly.")
    w("")

    # --- headline ------------------------------------------------------------
    w("## 1 Summary")
    w("")
    if not entries:
        w("**No failures.** Every executed test case passed, so there is no gap to "
          "report. Check the coverage table below before concluding that ISiK "
          "conformance is demonstrated: a run that executed few cases can pass "
          "without proving much.")
        w("")
    else:
        w(f"**{recon['irisGapCases']} failing test case(s) attributable to the SUT, "
          f"collapsing to {len(iris_entries)} distinct defect(s).** "
          f"{recon['testDataDefects']} further failure(s) are defects in this "
          f"project's own test data and "
          f"{recon['environmentPrerequisites']} failed on a precondition this "
          "deployment did not meet; both are excluded from that count and "
          "reported in §5.")
        w("")
        if recon["dedupRatio"]:
            w(f"Deduplication factor {recon['dedupRatio']}× — most scenarios fail "
              "for a reason shared with other scenarios, so fix count is far below "
              "failure count.")
            w("")
        w("| Category | Distinct defects | Failing cases | What it means | IRIS locus |")
        w("|---|---:|---:|---|---|")
        for cat, _n in sorted(recon["byCategory"].items(),
                             key=lambda kv: -kv[1]):
            cat_entries = [e for e in entries if e["category"] == cat]
            cases = sum(e["affectedCases"] for e in cat_entries)
            meta = CATEGORIES.get(cat) or {}
            w(f"| **{cat}** | {len(cat_entries)} | {cases} | "
              f"{md_escape(meta.get('signal', ''))} | "
              f"{md_escape(meta.get('irisLocus', ''))} |")
        w("")

    # --- coverage ------------------------------------------------------------
    w("## 2 Coverage of this run")
    w("")
    w("A gap analysis is only as complete as the run behind it. This table states "
      "what was actually executed, so an incomplete run cannot be mistaken for a "
      "clean one.")
    w("")
    w("| Level | Cases executed | Expected in scope | Passed | Failed | "
      "Features not executed |")
    w("|---|---:|---:|---:|---:|---:|")
    for level, pl in sorted(L.items()):
        t = pl["totals"]
        exp = pl.get("expected") or {}
        w(f"| {level} | {t.get('cases', 0)} | "
          f"{exp.get('casesExecuted', '?')} | {t.get('passed', 0)} | "
          f"{t.get('failed', 0)} | {pl['notExecuted']} |")
    w("")
    for level, pl in sorted(L.items()):
        t = pl["totals"]
        exp = pl.get("expected") or {}
        want = exp.get("casesExecuted")
        if isinstance(want, int) and t.get("cases", 0) < want:
            w(f"> **{level} is incomplete.** {t.get('cases', 0)} of {want} expected "
              f"cases executed and {pl['notExecuted']} feature file(s) produced no "
              "result at all. Defects present only in the unexecuted features are "
              "absent from this report. See `analysis/results.md` §7 for the "
              "anomalies the results analysis recorded.")
            w("")

    if any(pl["byModule"] for pl in L.values()):
        w("### 2.1 Failures by module")
        w("")
        cats = sorted({c for pl in L.values() for m in pl["byModule"].values()
                       for c in m})
        w("| Level | Module | " + " | ".join(cats) + " | Total |")
        w("|---|---|" + "---:|" * (len(cats) + 1))
        for level, pl in sorted(L.items()):
            for mod, counts in sorted(pl["byModule"].items()):
                row = [str(counts.get(c, 0)) for c in cats]
                w(f"| {level} | {mod} | " + " | ".join(row) + " | "
                  f"{sum(counts.values())} |")
        w("")

    # --- how to read ---------------------------------------------------------
    w("## 3 How each entry was derived")
    w("")
    w("The category is decided from four sources, in order of increasing evidence: "
      "the Gherkin step's shape, the request the assertion was made about, the "
      "HTTP status the SUT returned, and — only for test data defects — the "
      "pre-run seed records. Each entry states which one decided it.")
    w("")
    w("The supplying FHIR package is derived, not annotated. Profile assertions "
      "resolve through the canonical URL to the package that declares that "
      "`StructureDefinition`; interactions, search parameters and operations "
      "resolve to the ISiK `CapabilityStatement` that declares them, with its "
      "`capabilitystatement-expectation` code (`SHALL` / `SHOULD` / `MAY`) quoted "
      "so the strength of the requirement is visible.")
    w("")
    w(f"The **IRIS locus** column is different in kind: {index.get('irisLociProvenance')}")
    w("")

    # --- entries -------------------------------------------------------------
    w("## 4 Gaps")
    w("")
    if not iris_entries:
        w("No SUT-attributable failures in this run.")
        w("")
    for e in iris_entries:
        render_entry(w, e, index)

    # --- not-the-product appendix --------------------------------------------
    w("## 5 Appendix — failures not attributable to the SUT")
    w("")
    w("### 5.1 Test data defects (ours, not IRIS)")
    w("")
    if not data_entries:
        w("None. No failure in this run is attributable to this project's test "
          "data: the seed step's pre-run `$validate` and read-back records show no "
          "defect on any resource a failing scenario touched.")
        w("")
    else:
        w("These failures were caused by this project's own seeded test data. Each "
          "is proven from the seed step's records, which were written *before* the "
          "test run. They are excluded from the gap count in §1 and are this "
          "project's work, not the product's.")
        w("")
        for e in data_entries:
            render_entry(w, e, index)
    w("### 5.2 Preconditions this deployment did not meet")
    w("")
    if not prereq_entries:
        w("None. Every failing case in this run was made under the conditions its "
          "scenario states.")
        w("")
    else:
        w("These cases failed on a condition the scenario itself requires and this "
          "deployment did not provide. They are reported in full, and excluded "
          "from the gap count in §1, because a case whose precondition was not met "
          "measures the environment rather than the SUT — in either direction: it "
          "can neither convict the product nor acquit it. The rule that decided "
          "each one, and the evidence it needs, is "
          "`index.json`'s `prerequisiteRules`.")
        w("")
        for e in prereq_entries:
            render_entry(w, e, index)

    # --- reconciliation ------------------------------------------------------
    w("## 6 Reconciliation")
    w("")
    w("Every failing case in the official report must produce exactly one "
      "categorised finding here. If these numbers disagree, this report is "
      "incomplete and must not be used as a work list.")
    w("")
    w("| | |")
    w("|---|---:|")
    w(f"| Failing cases in the official report | {recon['officialFailedCases']} |")
    w(f"| Failure records analysed | {recon['failureRecordsAnalysed']} |")
    w(f"| Categorised findings | {recon['categorised']} |")
    w(f"| — of which test data defects | {recon['testDataDefects']} |")
    w(f"| — of which preconditions not met by this deployment | "
      f"{recon['environmentPrerequisites']} |")
    w(f"| SUT-attributable failing cases | {recon['irisGapCases']} |")
    w(f"| Distinct defects after deduplication | {recon['entries']} |")
    w(f"| Balanced | {'yes' if recon['balanced'] else '**NO**'} |")
    w("")
    if analysis["warnings"]:
        w("### 6.1 Warnings")
        w("")
        for warn in analysis["warnings"]:
            w(f"- {warn}")
        w("")
    drift = index.get("shapeDrift") or []
    unk = index.get("unclassifiedShapes") or []
    if drift or unk:
        w("### 6.2 Attribution index health")
        w("")
        w("The index was written against a specific test suite version. These "
          "differences mean the classification table and the feature corpus have "
          "diverged, so some categories may be wrong or empty.")
        w("")
        for d in drift:
            w(f"- step kind `{d['kind']}`: expected {d['expected']} occurrences, "
              f"found {d['found']}")
        for s in unk:
            w(f"- unrecognised step shape: `{s}`")
        w("")
    notes = index.get("notes") or []
    if notes:
        w("### 6.3 Notes recorded while building the attribution index")
        w("")
        for n in notes:
            w(f"- {n}")
        w("")

    # --- provenance ----------------------------------------------------------
    w("## 7 Sources")
    w("")
    w("| Artefact | Path in this bundle | Role |")
    w("|---|---|---|")
    w("| Official Gematik test report | "
      + ", ".join(f"`{(((results.get('levels') or {}).get(lv) or {}).get('official') or {}).get('testReport', {}).get('archive', '?')}`"
                  for lv in sorted(L))
      + " | authoritative result; the *Bestätigungsverfahren* artefact |")
    w("| Official debug report | "
      + ", ".join(f"`{(((results.get('levels') or {}).get(lv) or {}).get('official') or {}).get('debugReport', {}).get('archive', '?')}`"
                  for lv in sorted(L))
      + " | request/response bodies and validator output |")
    w("| Results analysis | `analysis/results.json`, `analysis/results.md` | "
      "which cases failed, each citing its location in the official report |")
    w("| Attribution index | `gap-analysis/index.json` (repository, not bundle) | "
      "step shape → FHIR feature → supplying package |")
    w("| Requirements corpus | `provenance/requirements.json` | the feature files' "
      "own statement of what each scenario requires |")
    w("| Seed records | `provenance/seed/*.json` | pre-run proof of test data "
      "state, used to segregate §5 |")
    if (results.get("packagePlanDeviations") or {}).get("source"):
        w("| Package plan | "
          f"`{results['packagePlanDeviations']['source']}` | which packages each "
          "endpoint was loaded with, and every dependency declaration that had to "
          "be rewritten for the loader (§8) |")
    w("")
    w("This document is derived and interpretive. The official report is "
      "authoritative on results; where the two appear to differ, the official "
      "report is correct and this generator has a bug.")
    w("")

    # --- loader appendix -----------------------------------------------------
    # Numbered last rather than folded into §4, because it is not a conformance
    # failure and must not be counted as one - but it is a product finding, and
    # this report's audience is the one team that can act on it.
    render_loader_appendix(w, results)
    return "\n".join(out) + "\n"


def render_loader_appendix(w, results: dict) -> None:
    """§8 - the FHIR package loader findings, which are not conformance failures.

    Written for this report's audience specifically. Nothing here came out of the
    test run: it came out of getting the validator's own package set *into* IRIS at
    all, which is a prerequisite for the run rather than part of it. Two of the four
    cases are IRIS's own behaviour and are actionable by the reader; the other two
    are upstream and are named so nobody spends time on them.

    Kept out of §1's gap count on purpose. Inflating a conformance figure with an
    installation-time limitation would be exactly the kind of error that makes a gap
    report untrustworthy - and it would also bury a finding that has a different
    owner and a different fix.
    """
    dev = results.get("packagePlanDeviations") or {}
    rewrites = dev.get("dependencyRewrites") or []
    promoted = dev.get("promoted") or []
    if not rewrites and not promoted:
        return

    w("## 8 Appendix — FHIR package loader, and why the package tree was modified")
    w("")
    w("**Not conformance failures, and deliberately not counted in §1.** None of "
      "this came out of the test run. It came out of loading the reference "
      "validator's own package set into IRIS, which happens before any test "
      "executes. It is here because two of the findings are IRIS's own behaviour "
      "and this report's audience is the one that can act on them.")
    w("")
    w("`HS.FHIRMeta.Load.NpmLoader.coreVersion()` walks the `dependencies` of every "
      "package being imported and resolves each entry with "
      "`HS.FHIRMeta.Storage.Package.GetById(name @ version)` — `%OpenId` followed by "
      "`$$$ThrowOnError`. Consequences, both verified on the build under test rather "
      "than read from documentation:")
    w("")
    w("1. **No semantic-version resolution.** A dependency declared as a range — "
      "`de.gematik.isik-basismodul: 3.1.x` — is looked up as the literal package id "
      "`de.gematik.isik-basismodul@3.1.x` and is never found. A patch-level wildcard "
      "is valid under the FHIR NPM package specification, so the packages declaring "
      "it are correct as published.")
    w("2. **One unresolvable declaration aborts the whole import**, with "
      "`ERROR #5809: Object to Load not found` — and it aborts *after* "
      "`InstallNamespace()` has created the namespace, so the failure presents as a "
      "broken installation rather than as one bad declaration. There is no partial "
      "import and no diagnostic naming the dependent package; the error names only "
      "the id that could not be opened.")
    w("")
    w("A related sharp edge in the same method, hit while diagnosing the above: a "
      "stored package with no core-type marker, no resolvable dependency edge and no "
      "readable `package.json` via `loadedFrom` raises "
      "`ERROR #5001: Package '<id>' has no dependencies or other data that identify "
      "a core package`. A metadata-only `HS.FHIRMeta.Storage.Package` row is "
      "therefore not a usable way to satisfy a dangling declaration.")
    w("")
    if rewrites:
        w("### 8.1 Declarations rewritten to make the import possible")
        w("")
        w("Rewritten in the *unpacked* `package.json` files, which is what the loader "
          "reads. The `.tgz` payloads are untouched, so the package checksums in "
          "`analysis/results.md` §6 still attest the published bytes.")
        w("")
        w("| Level | Dependent package | Declared | Rewritten to | Owner |")
        w("|---|---|---|---|---|")
        for r in rewrites:
            to = f"`{md_escape(str(r.get('to')))}`" if r.get("to") else "*edge removed*"
            w(f"| {md_escape(r.get('level', '?'))} | "
              f"`{md_escape(r.get('package', '?'))}` | "
              f"`{md_escape(r.get('from', '?'))}` | {to} | "
              f"{md_escape(LOADER_OWNERS.get(r.get('kind'), r.get('kind') or '?'))} |")
        w("")
        w("Reading the owner column: a `wildcard` row is an IRIS limitation with a "
          "correct package on the other side of it. An `absent-version` row is an "
          "upstream implementation-guide defect — `hl7.fhir.r4.core: 4.0.0` was never "
          "published; R4 is 4.0.1 — and nothing in IRIS would help. An "
          "`excluded-from-sut` row is this project's decision not to load the "
          "`hl7.fhir.r4.examples` corpus into the endpoint.")
        w("")
    if promoted:
        w("### 8.2 A package loaded only because the loader demanded it")
        w("")
        w("| Package | Level(s) | Declared by | Effect |")
        w("|---|---|---|---|")
        for p in promoted:
            w(f"| `{md_escape(p.get('id', '?'))}` | "
              f"{md_escape(', '.join(p.get('levels') or []))} | "
              f"{md_escape(', '.join(p.get('declaredSnapshotOnlyBy') or []))} | "
              f"{md_escape(p.get('deviation') or '')} |")
        w("")
        w("The Gematik reference validator lists these under `snapshotDependencies`: "
          "it uses them to build snapshots and ships no payload for them. A package "
          "the endpoint *does* load declares one in its own `dependencies`, so the "
          "exact-id resolution above forces it into the SUT too. That the validator "
          "pins it only for snapshot generation while a real dependency names it is "
          "an inconsistency in the ISiK validator configuration, not in IRIS and not "
          "in the SUT.")
        w("")
    w("What a fix would look like, since this report is a work list: `coreVersion()` "
      "resolving a declared dependency through a version-range match against the "
      "already-stored packages — rather than through `GetById` on the literal "
      "string — would remove the `wildcard` class entirely, and reporting the "
      "*dependent* package alongside the unresolvable id would make the remaining "
      "cases diagnosable in one step instead of by dumping the method.")
    w("")


def render_entry(w, e: dict, index: dict) -> None:
    occ = e["occurrences"]
    first = occ[0]
    title = CAT_TITLE.get(e["category"], e["category"])
    w(f"### {e['id']} · {title} · {e['fhirFeature'] or e['stepKind'] or 'unknown'}")
    w("")
    w(f"**Affects {e['affectedCases']} test case(s)** in "
      f"{', '.join(e['levels'])} / {', '.join(e['modules']) or 'unknown module'}. "
      f"Category `{e['category']}` — {e['reason']}.")
    w("")

    # 1. what was executed
    w("**Test step executed**")
    w("")
    w("```gherkin")
    w(f"# {first['feature']}:{first.get('stepLine') or first.get('featureLine')}"
      f"  ({first.get('scenario')})")
    w(e["gherkinStep"] or "(the failure is attributed to the scenario, not a step)")
    w("```")
    w("")
    if e.get("substep"):
        w(f"Failing sub-step: `{e['substep']}`")
        w("")
    if first.get("exampleData"):
        w("Scenario Outline row: "
          + ", ".join(f"`{k}` = `{v}`" for k, v in first["exampleData"].items())
          + ".")
        w("")
    ex = e.get("exchange") or e.get("http") or {}
    if ex.get("path"):
        w("```http")
        w(f"{ex.get('method', 'GET')} {ex.get('path')}")
        if ex.get("requestHeaders"):
            for hk, hv in list((ex["requestHeaders"] or {}).items())[:4]:
                w(f"{hk}: {hv}")
        w("")
        w(f"HTTP {ex.get('statusCode', '?')}   "
          f"Content-Type: {ex.get('contentType', 'unrecorded')}")
        w("```")
        w("")
    if e.get("downstreamOf"):
        d = e["downstreamOf"]
        w(f"> This assertion runs against a response obtained in an earlier "
          f"scenario of the same feature (`{d.get('scenario')}`, line "
          f"{d.get('line')}). Tiger's *current response* is feature-scoped state. "
          "If that earlier scenario also failed, fix it first — this finding may "
          "be a consequence of it.")
        w("")

    # 2. what FHIR feature was tested
    w("**FHIR feature under test**")
    w("")
    w(f"- Mechanism: {e['mechanism']}")
    if e["fhirFeature"]:
        w(f"- Feature: {e['fhirFeature']}")
    for req in e["requirements"]:
        w(f"- Required: {req}")
    if e["packages"]:
        w(f"- Supplied by: " + ", ".join(f"`{p}`" for p in e["packages"]))
    elif e["category"] == "ENVIRONMENT_PREREQUISITE":
        # Nothing was tested, so there is no feature to attribute and nothing for
        # a package to supply. Saying so is the point of the entry.
        w("- Supplied by: not applicable — the case did not reach a statement "
          "about any FHIR feature, which is why it is in this appendix.")
    elif e["category"] in NO_SUPPLYING_PACKAGE:
        # Not a failed lookup. These categories are about the REST envelope and the
        # write path, which no FHIR package declares, so "not resolvable" would
        # read as a defect in this report where it is a property of the category.
        w("- Supplied by: no FHIR package — this is base FHIR RESTful behaviour, "
          "not a profile or search-parameter declaration.")
    else:
        w("- Supplied by: **not resolvable** — no unpacked FHIR package in this "
          "run declares this feature. Treat the attribution above as incomplete.")
    if e["irisLocus"]:
        w(f"- IRIS locus: {e['irisLocus']}")
    for note in e["notes"]:
        w(f"- Note: {note}")
    w("")

    # 3. expected vs actual
    w("**Expected**")
    w("")
    w(f"{e['expected'] or 'see the step above'}")
    w("")
    w("**Actual**")
    w("")
    if e.get("errorType"):
        w(f"`{e['errorType']}`")
        w("")
    if e.get("message"):
        w("```")
        w(truncate(e["message"]))
        w("```")
        w("")
    body = (e.get("exchange") or {}).get("responseBody")
    if body:
        w("Response body as the official report recorded it:")
        w("")
        w("```")
        w(truncate(str(body)))
        w("```")
        w("")
    if e.get("testDataEvidence"):
        ev = e["testDataEvidence"]
        w(f"Pre-run evidence on `{ev['resourceType']}/{ev['id']}` "
          f"(from `{ev['source']}`):")
        w("")
        for r in ev["reasons"]:
            w(f"- {r}")
        w("")

    # occurrences
    w("<details><summary>Affected scenarios and their locations in the official "
      f"report ({e['affectedCases']})</summary>")
    w("")
    w("| Level | Feature | Line | Scenario | Example | Official report |")
    w("|---|---|---:|---|---|---|")
    for o in occ[:MAX_SCENARIOS]:
        w(f"| {o['level']} | `{o['feature']}` | {o.get('featureLine') or ''} | "
          f"{md_escape(o.get('scenario') or '')} | "
          f"{md_escape(o.get('example') or '')} | "
          f"`{o.get('reportJson') or ''}` |")
    if len(occ) > MAX_SCENARIOS:
        w(f"| … | … and {len(occ) - MAX_SCENARIOS} more (all listed in "
          f"`gap-analysis.json`) | | | | |")
    w("")
    w("</details>")
    w("")


# =============================================================================
# Main
# =============================================================================

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run-dir", type=Path, required=True,
                    help="the run's report bundle, i.e. reports/<run-id>")
    ap.add_argument("--index", type=Path,
                    default=REPO / "gap-analysis" / "index.json")
    ap.add_argument("--out-md", type=Path,
                    help="default: <run-dir>/GAP-ANALYSIS.md")
    ap.add_argument("--out-json", type=Path,
                    help="default: <run-dir>/gap-analysis.json")
    ap.add_argument("--generated-at",
                    help="ISO-8601 timestamp; defaults to now. Set it to make the "
                         "output byte-identical across re-runs.")
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero if the reconciliation does not balance or "
                         "any failure is unclassified")
    args = ap.parse_args()

    run_dir = args.run_dir
    if not run_dir.is_dir():
        die(f"{run_dir} is not a directory. Run `make report` first.")
    results = load_json(run_dir / "analysis" / "results.json",
                        "the results analysis")
    index = load_json(args.index, "the attribution index")
    if not index.get("features"):
        die(f"{args.index} holds no indexed features. Run `make packages` to "
            "rebuild it.")
    reqs = load_json(run_dir / "provenance" / "requirements.json",
                     "the requirements corpus", required=False)
    seed = load_seed(run_dir)

    generated_at = args.generated_at or \
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    analysis = analyse(results, index, seed, run_dir, reqs)

    out_md = args.out_md or (run_dir / "GAP-ANALYSIS.md")
    out_json = args.out_json or (run_dir / "gap-analysis.json")
    out_md.write_text(render(results, index, analysis, run_dir, generated_at),
                      encoding="utf-8")
    out_json.write_text(json.dumps({
        "runId": results.get("runId"),
        "generatedAt": generated_at,
        "generator": "gap-analysis/gap_report.py",
        "audience": "InterSystems IRIS for Health developers",
        "authoritativeSource": results.get("authoritativeSource"),
        "index": {"generatedAt": index.get("generatedAt"),
                  "source": index.get("source"),
                  "shapeDrift": index.get("shapeDrift"),
                  "unclassifiedShapes": index.get("unclassifiedShapes")},
        "categories": CATEGORIES,
        "irisLociProvenance": index.get("irisLociProvenance"),
        "reconciliation": analysis["reconciliation"],
        "levels": analysis["levels"],
        "warnings": analysis["warnings"],
        "unjoinedSteps": analysis["unjoinedSteps"],
        # Carried through verbatim from results.json rather than re-derived, and kept
        # out of "gaps" on purpose: these are installation-time loader findings, not
        # conformance failures, and a consumer counting gaps must not pick them up.
        "packageLoaderFindings": results.get("packagePlanDeviations") or {},
        "gaps": analysis["entries"],
    }, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    recon = analysis["reconciliation"]
    rel_md = out_md.relative_to(REPO) if out_md.is_relative_to(REPO) else out_md
    print(f"  {recon['failureRecordsAnalysed']} failing case(s) -> "
          f"{recon['entries']} distinct finding(s) "
          f"({recon['irisGapEntries']} SUT, "
          f"{recon['entries'] - recon['irisGapEntries']} not the SUT's: "
          f"{recon['testDataDefects']} test-data and "
          f"{recon['environmentPrerequisites']} unmet-precondition case(s))",
          file=sys.stderr)
    for cat, n in sorted(recon["byCategory"].items(), key=lambda kv: -kv[1]):
        print(f"    {n:5d}  {cat}", file=sys.stderr)
    print(f"  wrote {rel_md} and {out_json.name}", file=sys.stderr)
    for warn in analysis["warnings"]:
        print(f"  WARN {warn}", file=sys.stderr)

    if args.strict and (not recon["balanced"]
                        or recon["byCategory"].get("UNCLASSIFIED")):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
