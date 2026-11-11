#!/usr/bin/env python3
"""Build the attribution index the developer gap analysis joins against.

Run by `make packages`, with no arguments, at the one moment when everything it
needs is on disk: the feature files and the unpacked FHIR packages of the pinned
test suite version. Writing it then is what keeps `make gap-report` a pure join -
no network, no test suite image, no IRIS - so a gap report can be regenerated
from an archived evidence bundle months later and come out identical.

What problem this solves
------------------------
`analysis/results.json` (scripts/lib/results_report.py) says *what* failed: the
scenario, the step, the exception, the HTTP exchange. It deliberately says nothing
about *why*, because the why needs three lookups that belong here:

  1. **What FHIR feature was under test.** The step shape says it. There are 53
     distinct shapes across the 220 feature files and each one is a different
     assertion about the SUT - a FHIRPath over a read response, a
     CapabilityStatement search-parameter declaration, a profile validation, a
     custom operation invocation. Classifying the shape is what turns "an
     AssertionError on line 20" into "the CapabilityStatement must declare
     SearchParameter `patient` of type `reference` on Account".

  2. **Which FHIR package supplies it.** Two hops, both mechanical. The feature
     files name a *validation module* (`isik5`, `isik3-basismodul`, ...) and a
     profile canonical; requirements.json records that pairing per feature; and
     every canonical in the unpacked package set resolves to the `name@version`
     that ships it. For interactions, search parameters and operations the
     attribution is even more direct: the ISiK packages ship the
     CapabilityStatements that *state the requirement*, so the index can name the
     exact conformance resource, its `SHALL`/`SHOULD`/`MAY` expectation, and the
     package it came from.

  3. **Which IRIS subsystem a developer would open.** This is the one thing that
     cannot be derived: no artefact in this repository maps a FHIR feature to an
     IRIS class. That table is authored (see IRIS_LOCI) and marked as authored in
     the index, so a reader can tell derived facts from asserted ones.

Deliberately not in the index
-----------------------------
Anything already in `vendor/requirements.json`. That file is copied into every
evidence bundle at `provenance/requirements.json`, the gap report reads it
directly, and duplicating its 1.5 MB here would create two records of the same
corpus that could disagree. The index holds only what requirements.json lacks:
the step classification, the package attribution, the conformance-requirement
index and the profile element index.

Degradation, on purpose
-----------------------
`vendor/refv-plugins/` (the validator plugin descriptors) is written by
20-fetch-packages.sh only on the `refv` package route. On the `simplifier` route
it does not exist. The index therefore derives validation-module -> package from
the *observed* profile canonicals - a measurement that works on both routes - and
treats the plugin descriptors as a cross-check when they are present. A missing
descriptor set narrows the index; it never stops `make packages`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))

import feature_parser as fp                                    # noqa: E402
from profile_constraints import load_index as load_canonicals   # noqa: E402

try:
    import yaml
except ImportError:                                            # pragma: no cover
    yaml = None


# =============================================================================
# Categories
# =============================================================================
# The closed set from the project plan. A category exists to answer one question:
# which part of IRIS would a developer open. Nothing is added to this list without
# that answer, and nothing is force-fitted into it either - a step shape the
# classifier cannot place is recorded as unclassified rather than filed under the
# nearest-looking heading, because a wrong category sends a developer to the wrong
# source file, which is worse than sending them nowhere.

CATEGORIES: dict[str, dict[str, str]] = {
    "UNSUPPORTED_OPERATION": {
        "signal": "a FHIR operation ($book, $update-metadata) is invoked or "
                  "declared and the SUT does not implement it",
        "irisLocus": "the endpoint's OperationHandlerClass. Built-in operations "
                     "resolve to FHIR<Scope>Op<Name> methods on "
                     "HS.FHIRServer.Storage.BuiltInOperations; a custom operation "
                     "needs a subclass of it plus an InteractionsStrategy "
                     "subclass, installed before the endpoint is created.",
    },
    "PROFILE_NONCONFORMANCE": {
        "signal": "the response body does not satisfy an ISiK StructureDefinition "
                  "- either a profile validation step or a FHIRPath assertion "
                  "over an element the profile marks mustSupport",
        "irisLocus": "resource content, not IRIS code, in most cases: the SUT "
                     "returns what was stored. Read the quoted validator message "
                     "first. IRIS code is implicated when the element was stored "
                     "and is missing from the response (JSON/XML export in "
                     "HS.FHIRServer.Util.* or the storage strategy's ToJSON path).",
    },
    "MISSING_SEARCH_PARAM": {
        "signal": "a search parameter the ISiK CapabilityStatement requires is "
                  "absent from the SUT's CapabilityStatement, or a search using "
                  "it does not filter",
        "irisLocus": "search-parameter indexing. The endpoint's SearchParameter "
                     "set comes from the packages loaded by "
                     "HS.FHIRMeta.Load.NpmLoader and selected in the instance's "
                     "package list; an unindexed parameter is not advertised.",
    },
    "CAPABILITY_STATEMENT_GAP": {
        "signal": "the SUT's generated CapabilityStatement omits an interaction, "
                  "resource type or operation the ISiK conformance resource "
                  "declares as SHALL",
        "irisLocus": "CapabilityStatement generation. IRIS builds /metadata from "
                     "the endpoint's package set and strategy capabilities; a "
                     "missing entry means the resource type or interaction is not "
                     "enabled on the endpoint rather than merely undeclared.",
    },
    "SEARCH_SEMANTICS": {
        "signal": "a search executes but returns the wrong set - a modifier, "
                  "prefix, chain, _include or paging behaviour is not honoured",
        "irisLocus": "the search layer: modifier and prefix handling in the "
                     "strategy's search implementation, and the "
                     "SearchQueryProcessor for chained and reverse-chained "
                     "parameters.",
    },
    "RESPONSE_FORMAT": {
        "signal": "the REST envelope is wrong rather than the resource: content "
                  "type, charset, _format, HTTP status, or the identity of what a "
                  "read returned",
        "irisLocus": "the REST layer - content negotiation and the read path in "
                     "HS.FHIRServer.Service / the interactions strategy. Note "
                     "this category deliberately also covers `resource has ID` "
                     "assertions: they check that a read of /Type/{id} echoes "
                     "that logical id, which is the same code path.",
    },
    "REFERENCE_INTEGRITY": {
        "signal": "a Reference.reference does not resolve to the expected "
                  "resource, or a referenced resource is missing from a bundle "
                  "the SUT assembled",
        "irisLocus": "reference storage and rewriting: how the strategy persists "
                     "Reference.reference and what _include / revinclude "
                     "assembles into a search bundle.",
    },
    "WRITE_VALIDATION_GAP": {
        "signal": "a write the ISiK profile forbids was accepted, or one it "
                  "requires was refused - the status code disagrees with the "
                  "profile, whatever the stored resource then contains",
        "irisLocus": "the write path's validation depth: the interactions "
                     "strategy's Add/Update path and what it does with the "
                     "endpoint's configured ResourceValidation setting. "
                     "$validate is a separate code path and a result there does "
                     "not settle this one. Which schema this build's write path "
                     "actually enforces is a measurement on a running SUT, so "
                     "this index - written at `make packages` time, with no SUT "
                     "reachable - does not state it: take the two-request probe "
                     "in docs/en/07-reports.md against the round's own endpoint "
                     "and record the result beside the round.",
    },
    "AUTHORIZATION_SCOPE": {
        "signal": "a request carrying a SMART-on-FHIR access token was answered "
                  "with resources outside the scope that token grants, or the "
                  "SMART discovery document is absent or incomplete - the Connect "
                  "module, added by test suite release 3.0.0",
        "irisLocus": "the endpoint's OAuth 2.0 configuration and the authorization "
                     "it delegates to. An IRIS FHIR endpoint answers "
                     "/.well-known/smart-configuration with "
                     "<HSFHIRErr>NoOAuth2Handling until an OAuth 2.0 client is "
                     "configured on it, so scope enforcement is a deployment "
                     "property of the endpoint and not of the FHIR layer alone.",
    },
    "ENVIRONMENT_PREREQUISITE": {
        "signal": "the scenario's own precondition was not met by this test "
                  "environment, so the case states nothing about the SUT. "
                  "Established from positive evidence in the official report "
                  "only - see index['prerequisiteRules']",
        "irisLocus": "none. This is a property of the deployment this round used, "
                     "not of the product. Excluded from the gap count for the "
                     "same reason TEST_DATA_DEFECT is: a count that mixes in "
                     "cases whose precondition failed overstates the work.",
    },
    "UPSTREAM_SUITE_CONFLICT": {
        "signal": "the case failed on the suite rather than on the response, in "
                  "one of two ways, each established from positive evidence "
                  "only. Either two steps require incompatible things of the "
                  "same body, so no response can satisfy both - the rejected "
                  "content is content the suite's own feature files require, see "
                  "index['suiteRequiredCrossVersionExtensions'] - or the step "
                  "raised a NullPointerException inside the suite's own "
                  "evaluation, naming the suite library method whose return "
                  "value it dereferenced, so it never compared an expectation "
                  "against the response at all",
        "irisLocus": "none. This is a defect in the test suite, not in the "
                     "product and not in this project's test data. Each entry "
                     "states which of the two kinds it is; where it is a crash, "
                     "read the exchange anyway, because a response the suite "
                     "could not evaluate is not thereby a correct one. The "
                     "first measured instance of the incompatible-requirements "
                     "kind is the R5 cross-version extension set: a Stufe 5 "
                     "feature asserts "
                     "`extension.where(url = 'http://hl7.org/fhir/5.0/"
                     "StructureDefinition/extension-Encounter.plannedStartDate')"
                     ".exists()` and the ISiK package defines that extension, "
                     "while a later step validates the enclosing Bundle against "
                     "core FHIR R4, which rejects any 5.0 extension URL as an "
                     "invalid version. Excluded from the gap count for the same "
                     "reason the two categories below are.",
    },
    "TEST_DATA_DEFECT": {
        "signal": "the seeded resource was absent, unreadable or already flagged "
                  "by $validate before the run, or the content the reference "
                  "validator rejected is the content this project staged - so "
                  "the failure is ours, not the SUT's",
        "irisLocus": "none. This is a defect in this project's test data or "
                     "seeding, proven from provenance/seed/*.json or from the "
                     "staged instances in provenance/corpus/, and is reported in "
                     "a separate appendix so it cannot inflate the IRIS gap count.",
    },
    "UNCLASSIFIED": {
        "signal": "the failing step's shape is not in this index's classification "
                  "table - almost always a test suite version that grew a new "
                  "step shape",
        "irisLocus": "unknown. The entry quotes the step and the response "
                     "verbatim so it is still actionable, and build_index.py "
                     "reports the unclassified shape so the table can be extended.",
    },
}

# Authored, not derived - see the module docstring. Kept as a separate mapping
# from CATEGORIES[*]["irisLocus"] would allow, so that the index can state which
# of its facts are measurements and which are this project's assertions.
IRIS_LOCI_PROVENANCE = (
    "Authored by this project, not derived from any artefact. No file in this "
    "repository maps a FHIR feature to an IRIS class; these are the subsystems an "
    "IRIS for Health developer would open, stated so the gap report can point "
    "somewhere. Verify against the product source before acting on one."
)


# =============================================================================
# Step classification
# =============================================================================
# One entry per distinct step shape the suite uses. `expected` is how often the
# shape occurs in the pinned version (2.7.0), recorded so that a version bump
# which drops or renames a shape shows up as a mismatch rather than as silence -
# the same technique scripts/lib/extract_requirements.py uses for its own shape
# table.
#
# `role` says what the step does to the conversation:
#   request  - issues HTTP; sets the context that following assertions inherit
#   assert   - asserts something about the current response
#   setup    - configures the client (headers, failure messages, variables)
#   doc      - records prose; asserts nothing (the Test Description / Preconditions
#              blocks). A doc step can still fail if the scenario aborts, which is
#              why it is classified rather than skipped.
#
# `category` is the default. It is refined by gap_report.py from the actual HTTP
# response (see STATUS_RULES) and from the seed records, so `None` here means
# "the shape alone does not decide it" rather than "unknown".

RE_ARGS = re.compile(r'"([^"]*)"')
RE_ARGS_SQ = re.compile(r"'([^']*)'")


def _q(text: str) -> list[str]:
    """Every double-quoted argument in a step, in order."""
    return RE_ARGS.findall(text)


STEP_KINDS: list[dict] = [
    # --- documentation blocks ------------------------------------------------
    {"kind": "doc-description", "role": "doc", "category": None, "expected": 221,
     "re": re.compile(r'^(?:the Test Description:|Testbeschreibung:)\s*"'),
     "what": "the scenario's own statement of what it tests"},
    {"kind": "doc-preconditions", "role": "doc", "category": None, "expected": 221,
     "re": re.compile(r"^(?:the Preconditions:|Mit den Vorbedingungen:)"),
     "what": "the prose specification of the test data this scenario needs"},

    # --- requests ------------------------------------------------------------
    {"kind": "get-resource", "role": "request", "category": None, "expected": 849,
     "re": re.compile(r'^Get FHIR resource at "([^"]*)" with content type "([^"]*)"'),
     "args": ("url", "contentType"),
     "what": "a FHIR read or search over HTTP GET, with an explicit Accept format"},
    {"kind": "search-post", "role": "request", "category": None, "expected": 41,
     "re": re.compile(r'^Post FHIR search request to "([^"]*)" with content type '
                      r'"([^"]*)" and parameters:'),
     "args": ("url", "contentType"),
     "what": "a POSTed search (_search) with the parameters in a data table"},
    {"kind": "tgr-send-body", "role": "request", "category": None, "expected": 47,
     "re": re.compile(r'^TGR send (POST|PUT) request to "([^"]*)" with body "([^"]*)"'),
     "args": ("method", "url", "body"),
     "what": "a create, update or operation invocation with a fixture body"},
    {"kind": "tgr-send-empty", "role": "request", "category": None, "expected": 20,
     "re": re.compile(r'^TGR send empty (GET|DELETE|POST|PUT) request to "([^"]*)"'),
     "args": ("method", "url"),
     "what": "a bodyless request - a read, a delete, or a parameterless operation"},
    {"kind": "tgr-find-last-request", "role": "setup", "category": None, "expected": 74,
     "re": re.compile(r"^TGR find the last request"),
     "what": "selects the last proxied exchange as the current response"},

    # --- CapabilityStatement conformance -------------------------------------
    {"kind": "caps-interaction", "role": "assert",
     "category": "CAPABILITY_STATEMENT_GAP", "expected": 171,
     "re": re.compile(r'^CapabilityStatement contains interaction "([^"]*)" for '
                      r'resource "([^"]*)"'),
     "args": ("code", "resourceType"),
     "what": "the SUT must declare this interaction on this resource type"},
    {"kind": "caps-system-interaction", "role": "assert",
     "category": "CAPABILITY_STATEMENT_GAP", "expected": 1,
     "re": re.compile(r'^CapabilityStatement contains system interaction "([^"]*)"'),
     "args": ("code",),
     "what": "the SUT must declare this system-level interaction"},
    {"kind": "caps-searchparam", "role": "assert",
     "category": "MISSING_SEARCH_PARAM", "expected": 50,
     "re": re.compile(r'^CapabilityStatement contains definition of search parameter '
                      r'"([^"]*)" of type "([^"]*)" for resource "([^"]*)"'),
     "args": ("name", "type", "resourceType"),
     "what": "the SUT must declare this search parameter, with this type"},
    {"kind": "caps-operation", "role": "assert",
     "category": "UNSUPPORTED_OPERATION", "expected": 2,
     "re": re.compile(r'^CapabilityStatement contains operation "([^"]*)" for '
                      r'resource "([^"]*)"'),
     "args": ("code", "resourceType"),
     "what": "the SUT must declare this FHIR operation on this resource type"},

    # --- profile validation --------------------------------------------------
    # These are the steps that hand the response to the Gematik reference
    # validator. The validation module id is the join key to the supplying package.
    {"kind": "profile-conformance", "role": "assert",
     "category": "PROFILE_NONCONFORMANCE", "expected": 175,
     "re": re.compile(r'^FHIR current response body is a valid ([A-Za-z0-9-]+) '
                      r'resource and conforms to profile "([^"]*)"'),
     "args": ("validationModule", "profile"),
     "what": "the whole response must validate against this profile"},
    {"kind": "profile-conformance-named", "role": "assert",
     "category": "PROFILE_NONCONFORMANCE", "expected": 46,
     "re": re.compile(r'^Check if current response of resource "([^"]*)" is valid '
                      r'([A-Za-z0-9-]+) resource and conforms to profile "([^"]*)"'),
     "args": ("resourceType", "validationModule", "profile"),
     "what": "the named resource inside the response must validate against this "
             "profile"},
    {"kind": "profile-conformance-ref-v5", "role": "assert",
     "category": "PROFILE_NONCONFORMANCE", "expected": 28,
     "re": re.compile(r'^referenced "([^"]*)" resource with id "([^"]*)" conforms to '
                      r'a valid v5 "([^"]*)" profile'),
     "args": ("resourceType", "id", "profileName"),
     "what": "a referenced resource must be resolvable and validate against the "
             "named ISiK profile"},
    {"kind": "profile-conformance-ref", "role": "assert",
     "category": "PROFILE_NONCONFORMANCE", "expected": 35,
     "re": re.compile(r'^referenced ([A-Za-z]+) resource with id "([^"]*)" conforms '
                      r'to ([A-Za-z0-9]+) profile'),
     "args": ("resourceType", "id", "profileName"),
     "what": "a referenced resource must be resolvable and validate against the "
             "named ISiK profile"},

    # --- content assertions --------------------------------------------------
    {"kind": "fhirpath-assert", "role": "assert", "category": None, "expected": 1590,
     "re": re.compile(r"^FHIR current response body evaluates the FHIRPath\s+"
                      r"""(?:"([^"]*)"|'([^']*)')"""),
     "args": ("expr", "exprAlt"),
     "what": "a FHIRPath over the response must evaluate true - the suite's "
             "primary way of asserting a mustSupport element is present and "
             "correct"},
    {"kind": "fhirpath-assert-table", "role": "assert", "category": None,
     "expected": 38,
     "re": re.compile(r"^FHIR current response body evaluates the FHIRPaths:"),
     "what": "several FHIRPath assertions in one data table"},
    {"kind": "fhirpath-store", "role": "setup", "category": None, "expected": 1,
     "re": re.compile(r'^FHIR evaluate FHIRPath "([^"]*)" on current response body '
                      r'and store first element as primitive value in variable '
                      r'"([^"]*)"'),
     "args": ("expr", "variable"),
     "what": "captures a value from the response for a later step"},
    {"kind": "tgr-attribute", "role": "assert", "category": None, "expected": 233,
     "re": re.compile(r'^TGR current response with attribute "([^"]*)" matches "([^"]*)"'),
     "args": ("attribute", "matches"),
     "what": "a regex assertion on one attribute of the raw exchange - the status "
             "code, a header, or an element value"},
    # The negation of `tgr-attribute`, written with Gherkin's `But`. Absent from
    # 2.7.0 (hence expected 0); release 3.0.0 introduced it in the Connect module,
    # which asserts that the SMART discovery document does *not* offer the `plain`
    # PKCE method. Classified so a failure on it reports its shape rather than
    # falling through to UNCLASSIFIED.
    {"kind": "tgr-attribute-not", "role": "assert", "category": None, "expected": 0,
     "re": re.compile(r'^TGR current response with attribute "([^"]*)" does not '
                      r'match "([^"]*)"'),
     "args": ("attribute", "matches"),
     "what": "a regex assertion that one attribute of the raw exchange does NOT "
             "match - the suite forbidding a value rather than requiring one"},
    {"kind": "tgr-node", "role": "assert", "category": None, "expected": 3,
     "re": re.compile(r'^TGR current response contains node "([^"]*)"'),
     "args": ("node",),
     "what": "the response must contain this node"},

    # --- SMART-on-FHIR authorization (Connect, added at release 3.0.0) -------
    # Also absent from 2.7.0. The step passes on either of two answers - a refusal
    # or an empty result set - so what it asserts is that the token's scope was
    # enforced, whichever way the SUT chooses to enforce it.
    {"kind": "search-forbidden-or-empty", "role": "assert",
     "category": "AUTHORIZATION_SCOPE", "expected": 0,
     "re": re.compile(r"^FHIR search response is forbidden or contains no "
                      r"resources"),
     "what": "a search outside the granted scope must be refused or return "
             "nothing - it must not disclose resources, included ones included"},
    {"kind": "resource-has-id", "role": "assert", "category": "RESPONSE_FORMAT",
     "expected": 108,
     "re": re.compile(r'^resource has ID "([^"]*)"'),
     "args": ("id",),
     "what": "a read of /Type/{id} must return a resource carrying that logical id"},

    # --- bundles and references ---------------------------------------------
    {"kind": "bundle-contains", "role": "assert", "category": "SEARCH_SEMANTICS",
     "expected": 161,
     "re": re.compile(r'^response bundle contains resource with ID "([^"]*)"'),
     "args": ("id",),
     "what": "the search result set must contain this resource - the assertion "
             "that a search actually found what it was supposed to"},
    {"kind": "bundle-excludes", "role": "assert", "category": "SEARCH_SEMANTICS",
     "expected": 33,
     "re": re.compile(r'^bundle does not contain resource "([^"]*)" with ID "([^"]*)"'),
     "args": ("resourceType", "id"),
     "what": "the search result set must NOT contain this resource - a filter "
             "that must actually filter"},
    {"kind": "reference-element", "role": "assert", "category": "REFERENCE_INTEGRITY",
     "expected": 168,
     "re": re.compile(r'^element "([^"]*)" references resource with ID "([^"]*)"'),
     "args": ("element", "id"),
     "what": "this element's Reference must resolve to that resource"},
    {"kind": "reference-element-all", "role": "assert",
     "category": "REFERENCE_INTEGRITY", "expected": 98,
     "re": re.compile(r'^element "([^"]*)" in all bundle resources references '
                      r'resource with ID "([^"]*)"'),
     "args": ("element", "id"),
     "what": "every entry in the bundle must reference that resource - i.e. the "
             "search filtered by it"},
    {"kind": "medication-reason", "role": "assert",
     "category": "PROFILE_NONCONFORMANCE", "expected": 2,
     "re": re.compile(r'^reason for medication is given either by code or by '
                      r'reference to a Condition resource with ID "([^"]*)"'),
     "args": ("id",),
     "what": "ISiK's either/or requirement on the medication reason"},

    # --- client setup --------------------------------------------------------
    {"kind": "tgr-set-header", "role": "setup", "category": None, "expected": 50,
     "re": re.compile(r'^TGR set default header "([^"]*)" to "([^"]*)"'),
     "args": ("header", "value"),
     "what": "sets a request header for the following requests (Accept, "
             "Authorization)"},
    {"kind": "tgr-failure-message", "role": "setup", "category": None, "expected": 8,
     "re": re.compile(r"^TGR (?:the custom failure message is set to|clear the "
                      r"custom failure message)"),
     "what": "sets or clears the message the next assertion reports on failure"},
]

# Response-driven refinement. The step shape narrows a failure to a family; the
# status code decides which member. Applied by gap_report.py, stated here so the
# rules live next to the classification they refine and land in the index for a
# reader to check.
STATUS_RULES = [
    {"when": "the request path contains /$ and the status is 404 or 400",
     "category": "UNSUPPORTED_OPERATION",
     "why": "IRIS answers an unknown operation with 404 and an OperationOutcome; "
            "400 is the same gap reported as a bad request"},
    {"when": "the status is 404 on a plain /Type/{id} read",
     "category": "TEST_DATA_DEFECT",
     "why": "the resource the scenario needs is not in the SUT. Confirmed against "
            "provenance/seed/<Level>.json before being reported as ours"},
    {"when": "the status is 404 or 405 on a write to /Type",
     "category": "CAPABILITY_STATEMENT_GAP",
     "why": "the endpoint does not accept that interaction on that resource type"},
    {"when": "the status is 400 or 422 on a write",
     "category": "PROFILE_NONCONFORMANCE",
     "why": "the SUT rejected the body; the OperationOutcome names the constraint"},
    {"when": "the assertion targets $.header.* or a contentType",
     "category": "RESPONSE_FORMAT",
     "why": "content negotiation, independent of the resource content"},
    {"when": "the assertion targets $.responseCode and no other rule applied",
     "category": "RESPONSE_FORMAT",
     "why": "the status line is the REST envelope"},
]

# A TGR step's exchange is invisible to Serenity: Tiger issues the request through
# its own proxy, so `restQuery` - the only place results.json can read a method,
# path and status from - is absent on every one of them. Measured on the pinned
# run: `restQuery` occurs nowhere in the report of a TGR-only scenario, so
# STATUS_RULES above cannot fire for a `tgr-attribute` failure at all, and all 17
# `$.responseCode` failures reached the report uncategorised.
#
# The assertion's own message carries what STATUS_RULES needed, in AssertJ's
# fixed two-line form ("Element value:\n<actual>\nExpected:\n<regex>"). Parsing it
# is exact for all 17: 400-vs-20\d 6x, 400-vs-422 5x, 201-vs-422 4x, 201-vs-4\d\d
# and 500-vs-4\d\d once each. `expected` is a regex, not a literal, so a rule
# matches on the class of status the suite demanded rather than on one code.
RESPONSE_CODE_RULES = [
    {"when": "the suite demanded 4xx or 422 and the SUT answered 2xx",
     "category": "WRITE_VALIDATION_GAP",
     "why": "the write was accepted although the profile the suite validated "
            "against forbids the body. What the write path did validate against "
            "is not established by this case - see the category's irisLocus"},
    {"when": "the suite demanded 2xx and the SUT answered 400",
     "category": "WRITE_VALIDATION_GAP",
     "why": "the mirror image: a body ISiK requires the SUT to accept was "
            "rejected. Same code path, opposite direction, so the same locus"},
    {"when": "the suite demanded 422 and the SUT answered 400",
     "category": "WRITE_VALIDATION_GAP",
     "why": "both are rejections, so the SUT did detect the defect; FHIR "
            "reserves 422 for a body that is well-formed but fails validation "
            "and 400 for one that is malformed. The distinction is in the REST "
            "layer's error mapping, but the decision that produced it is the "
            "write path's, which is why this is not RESPONSE_FORMAT"},
    {"when": "the SUT answered 5xx to any expected status",
     "category": "RESPONSE_FORMAT",
     "why": "an unhandled server error is not a conformance verdict; the "
            "quoted body and the SUT's own log are the evidence"},
]

# The parser failed rather than the assertion: there was no body to evaluate. The
# HAPI message is the whole evidence, so it is matched literally.
#
# Measured on the pinned SUT: `POST /isik5/fhir/r4/RiskAssessment` with no Prefer
# header is answered `201 Created`, `CONTENT-LENGTH: 0`, with the new id in
# `Location`; the identical POST with `Prefer: return=representation` returns the
# resource (158 bytes). So the body is withheld by default, and the six affected
# scenarios - all *-Create in Stufe5/Medikation - send no Prefer header and assert
# `($this is X) or ($this is Parameters)` on the response.
# When a failing case states nothing about the SUT because its own precondition
# was not met by this deployment. Applied by gap_report.py on positive evidence
# from the official report only, never from a module or feature name: a Connect
# scenario that fails with a token in hand is a conformance finding and must stay
# one.
#
# The evidence is the suite's own. `testdata/Stufe5/connect.yaml` *inside the
# suite image* ships six access tokens as the literal SET-YOUR-TOKEN-HERE, which
# is the suite telling the system owner to supply them from their authorization
# server. Where that literal reaches the SUT in an Authorization header - and the
# official report records the resolved header verbatim, in each step's
# `reportData` - the scope under test was never issued, so the SUT cannot have
# enforced it and the case measures the channel instead.
#
# What the channel is, and why it answers at all, is this rig's own documented
# deviation: the Tiger proxy route carries the API user's Basic credential so that
# the official feature files can be used byte-identically against an endpoint that
# requires authentication (see docs/en/04-testtool.md). Measured on the pinned SUT
# at the endpoint itself: an unauthenticated request and one carrying
# `Bearer SET-YOUR-TOKEN-HERE` are both answered 404, the API user's Basic
# credential is answered 200. So the 200 the suite observed is the proxy's
# credential, not the placeholder token.
PREREQUISITE_RULES = [
    {"when": "the request the failing assertion was made about carries an "
             "Authorization header whose value is still the suite's own "
             "SET-YOUR-TOKEN-HERE placeholder",
     "category": "ENVIRONMENT_PREREQUISITE",
     "why": "the scenario's stated precondition is a valid Bearer token for a "
            "named scope. No token was issued, so no scope was presented, so "
            "nothing was asserted about the SUT's scope enforcement"},
    {"when": "the request is for /.well-known/smart-configuration AND the same "
             "run shows unresolved token placeholders elsewhere",
     "category": "ENVIRONMENT_PREREQUISITE",
     "why": "that path is the SMART-on-FHIR authorization discovery document, "
            "which describes an authorization server. The placeholders are this "
            "run's own evidence that none was configured. The second condition is "
            "what keeps the rule from excusing a genuine discovery defect in a "
            "round that did supply tokens"},
]

RESPONSE_BODY_RULES = [
    {"when": "the failure is scenario-scope and its message is the suite's own "
             "\"CapabilityStatement-Definition for search parameter 'p' of "
             "resource 'R' is either missing or incorrect\"",
     "category": "MISSING_SEARCH_PARAM",
     "why": "the suite's FhirPathValidation raised this instead of a step "
            "assertion, so no step shape is available - but the message names "
            "the parameter and the resource, which is the whole finding. "
            "Measured: 1 occurrence, combo-code-value-quantity on Observation"},
    {"when": "a FHIRPath assertion failed with HAPI-1857 (no content to parse) "
             "after a write",
     "category": "RESPONSE_FORMAT",
     "why": "the SUT returned no body on create, so there was nothing to "
            "assert over. IRIS withholds the created resource unless the "
            "request carries Prefer: return=representation; the suite sends no "
            "Prefer header and ISiK expects the resource or a Parameters. The "
            "locus is the interactions strategy's create path and its default "
            "return preference - there is no endpoint configuration for it "
            "(HS.FHIRServer.API.ConfigData carries DefaultPreferHandling, which "
            "governs unknown *search parameters*, and no return-preference "
            "property), so the default is decided in code"},
]


# Serenity records a step's text with its Gherkin keyword attached
# ("Then FHIR current response body is ..."), while feature_parser splits the two.
# classify_step accepts either form so gap_report.py can classify a report's
# `gherkinStep` directly when the (feature, line) join misses.
RE_KEYWORD = re.compile(r"^(?:Given|When|Then|And|But|\*)\s+")


def classify_step(text: str) -> dict:
    """Which kind a step is, and its captured arguments.

    Order matters in STEP_KINDS: `caps-operation` must be tried before the
    generic shapes, and `profile-conformance-ref-v5` before
    `profile-conformance-ref`, whose regex is the looser of the two. The table is
    written in that order and matching stops at the first hit.
    """
    text = RE_KEYWORD.sub("", text.strip())
    for spec in STEP_KINDS:
        m = spec["re"].match(text)
        if not m:
            continue
        args: dict[str, str] = {}
        names = spec.get("args") or ()
        for name, value in zip(names, m.groups()):
            if value is not None and value != "":
                args[name] = value
        # `fhirpath-assert` has two alternative capture groups for the two quoting
        # styles the suite mixes; collapse them so consumers see one field.
        if "exprAlt" in args:
            args.setdefault("expr", args.pop("exprAlt"))
        else:
            args.pop("exprAlt", None)
        return {"kind": spec["kind"], "role": spec["role"],
                "category": spec["category"], "args": args}
    return {"kind": None, "role": "assert", "category": "UNCLASSIFIED", "args": {}}


# Serenity nests Java `@Step` methods under the Gherkin step, and it is usually a
# nested one that actually carries the failure. Those names are not Gherkin, so
# they are not in STEP_KINDS - but they are more precise than their parent, so they
# are worth recognising. `validate against <module>` is the important one: it names
# the validation module directly, which is the join key to the supplying package,
# and it is what a profile validation failure is reported against.
SUBSTEP_KINDS = [
    {"kind": "profile-conformance", "category": "PROFILE_NONCONFORMANCE",
     "re": re.compile(r"^validate against ([A-Za-z0-9-]+)$"),
     "args": ("validationModule",),
     "what": "the Gematik reference validator running one validation module over "
             "the response body"},
]


def classify_substep(text: str) -> dict | None:
    """Classify a Serenity `@Step` name, or None if it is not a known one."""
    for spec in SUBSTEP_KINDS:
        m = spec["re"].match(text.strip())
        if m:
            return {"kind": spec["kind"], "role": "assert",
                    "category": spec["category"],
                    "args": dict(zip(spec["args"], m.groups()))}
    return None


# =============================================================================
# Request context
# =============================================================================
# An assertion means something different depending on what was requested. The
# same `FHIR current response body evaluates the FHIRPath "..."` step is a profile
# conformance statement after a read and a search-semantics statement after a
# search, and it names a CapabilityStatement requirement after /metadata. The
# context is therefore tracked in Gherkin order and attached to every assertion.
#
# The tracking runs across the whole feature file, not per scenario, because the
# suite genuinely works that way: Tiger's "current response" is per-feature state,
# and 61 scenarios exploit it. Account-Search.feature is the clearest case - one
# scenario reads /metadata, and the *next* scenario, a Scenario Outline, opens with
# `And CapabilityStatement contains definition of search parameter ...` against
# that response. A scenario whose first step is `And` is the tell. Resetting per
# scenario would leave those 61 with no context and mis-categorise every one.
#
# This coupling is also a fact the gap report needs: if the /metadata scenario
# failed, the Outline's failures are downstream of it, not independent findings.
# Steps that inherit from an earlier scenario are marked so it can say that.

# A path segment naming a FHIR operation. `\$(?!\{)` is load-bearing: the suite
# writes its data placeholders as `${data.key}`, so a bare `startswith("$")` reads
# `Slot/${data.appointment-book-slot-id}` as an invocation of an operation called
# `{data.appointment-book-slot-id}` and files every following assertion under
# UNSUPPORTED_OPERATION. Measured: 21 of 916 request steps.
RE_OPERATION_SEG = re.compile(r"^\$(?!\{)([A-Za-z][A-Za-z0-9-]*)$")


def context_of_request(kind: str, args: dict) -> tuple[str, dict]:
    """(context, details) for a request step."""
    url = args.get("url", "")
    path = url.split("://", 1)[-1]
    path = path.split("/", 1)[1] if "/" in path else ""
    query = ""
    if "?" in path:
        path, query = path.split("?", 1)
    segments = [s for s in path.split("/") if s]
    detail = {"url": url}
    if query:
        detail["query"] = query

    if path.startswith("metadata") or (segments and segments[0] == "metadata"):
        return "capability", detail
    op = next((m.group(1) for m in map(RE_OPERATION_SEG.match, segments) if m), None)
    if op:
        detail["operation"] = op
        # /Type/$op is type-level, /Type/{id}/$op is instance-level. The operation
        # is always the last segment in this suite, so its position says which.
        detail["scope"] = "instance" if len(segments) >= 3 else "type"
        if segments:
            detail["resourceType"] = segments[0]
        return "operation", detail
    if segments:
        detail["resourceType"] = segments[0]
    if kind == "search-post":
        return "search", detail
    if kind in ("tgr-send-body", "tgr-send-empty"):
        method = args.get("method", "")
        if method in ("POST", "PUT", "DELETE"):
            return "write", detail
    # A GET at /Type is a search; at /Type/{id} it is a read. `_search` and a
    # query string are both explicit searches.
    if query or (segments and segments[-1] == "_search"):
        return "search", detail
    if len(segments) >= 2:
        return "read", detail
    if len(segments) == 1:
        return "search", detail
    return "unknown", detail


# The default category for an assertion, given its kind and the context it runs
# in. `None` means the shape and context together still do not decide it, and
# gap_report.py must consult the HTTP response (STATUS_RULES).
CONTEXT_CATEGORY = {
    "capability": {
        "fhirpath-assert": "CAPABILITY_STATEMENT_GAP",
        "fhirpath-assert-table": "CAPABILITY_STATEMENT_GAP",
        "tgr-attribute": "CAPABILITY_STATEMENT_GAP",
        "tgr-node": "CAPABILITY_STATEMENT_GAP",
        "profile-conformance": "CAPABILITY_STATEMENT_GAP",
        "profile-conformance-named": "CAPABILITY_STATEMENT_GAP",
    },
    "read": {
        "fhirpath-assert": "PROFILE_NONCONFORMANCE",
        "fhirpath-assert-table": "PROFILE_NONCONFORMANCE",
        "tgr-attribute": None,
        "tgr-node": "PROFILE_NONCONFORMANCE",
    },
    "search": {
        "fhirpath-assert": "SEARCH_SEMANTICS",
        "fhirpath-assert-table": "SEARCH_SEMANTICS",
        "tgr-attribute": None,
        "tgr-node": "SEARCH_SEMANTICS",
    },
    "write": {
        "fhirpath-assert": None,
        "fhirpath-assert-table": None,
        "tgr-attribute": None,
        "tgr-node": None,
    },
    "operation": {
        "fhirpath-assert": "UNSUPPORTED_OPERATION",
        "fhirpath-assert-table": "UNSUPPORTED_OPERATION",
        "tgr-attribute": "UNSUPPORTED_OPERATION",
        "tgr-node": "UNSUPPORTED_OPERATION",
        "profile-conformance": "UNSUPPORTED_OPERATION",
        "profile-conformance-named": "UNSUPPORTED_OPERATION",
        "resource-has-id": "UNSUPPORTED_OPERATION",
    },
}

RE_ATTR_HEADER = re.compile(r"^\$\.header\b")
RE_ATTR_STATUS = re.compile(r"^\$\.responseCode$")


def category_for(kind: str | None, base: str | None, context: str,
                 args: dict) -> str | None:
    """The default category of one classified step."""
    if kind is None:
        return "UNCLASSIFIED"
    # An attribute assertion is decided by what it targets, before context: a
    # content-type check is a RESPONSE_FORMAT statement wherever it appears.
    if kind == "tgr-attribute":
        attr = args.get("attribute", "")
        if RE_ATTR_HEADER.match(attr) or "content-type" in attr.lower():
            return "RESPONSE_FORMAT"
        if RE_ATTR_STATUS.match(attr) and context in ("capability", "operation"):
            return CONTEXT_CATEGORY[context].get(kind)
        if RE_ATTR_STATUS.match(attr):
            return None          # decided by the status code, see STATUS_RULES
    if base is not None:
        # A shape with its own category keeps it, except where the context is
        # strictly more specific: an assertion after an operation invocation is
        # about the operation, whatever it asserts.
        if context == "operation" and kind in CONTEXT_CATEGORY["operation"]:
            return CONTEXT_CATEGORY["operation"][kind]
        return base
    return CONTEXT_CATEGORY.get(context, {}).get(kind)


# =============================================================================
# The feature side
# =============================================================================

RE_DQ = re.compile(r'"[^"]*"')
RE_SQ = re.compile(r"'[^']*'")
RE_ANGLE = re.compile(r"<[^<>]*>")


def shape_of(text: str) -> str:
    """The step text with its arguments replaced, so two steps that differ only
    in their arguments share one shape.

    Module-level and exported on purpose: this is the *definition* of the key
    every `steps[].shape` in index.json is stored under, so gap_report.py must
    normalise a failing step's text through this same function to join against
    it. A second, nearly-identical implementation there is what caused the join
    to miss - it collapsed `'X'` to `"X"`, so a single-quoted argument (the
    FHIRPath steps) never matched, and it left `<placeholder>` untouched, so a
    Scenario Outline template step never matched either.
    """
    t = RE_DQ.sub('"X"', text)
    t = RE_SQ.sub("'X'", t)
    t = RE_ANGLE.sub("X", t)
    return re.sub(r"\s+", " ", t).strip()


def index_features(root: Path) -> tuple[dict, dict, list[str]]:
    """features[rel] -> {scenarios: [...]}, shape census, unclassified shapes."""
    features: dict[str, dict] = {}
    census: dict[str, dict] = {}
    unclassified: dict[str, int] = {}

    for feat in fp.load_features(root):
        scenarios = []
        # Feature-scoped, deliberately: see the CONTEXT_CATEGORY comment above.
        context = "unknown"
        context_detail: dict = {}
        context_origin: dict | None = None
        for sc in feat.scenarios:
            steps = []
            for st in sc.steps:
                cls = classify_step(st.text)
                shape = shape_of(st.text)
                row = census.setdefault(shape, {"count": 0, "kind": cls["kind"]})
                row["count"] += 1
                if cls["kind"] is None:
                    unclassified[shape] = unclassified.get(shape, 0) + 1

                if cls["role"] == "request":
                    context, context_detail = context_of_request(cls["kind"],
                                                                 cls["args"])
                    context_origin = {"scenario": sc.name, "line": st.line}

                entry: dict = {
                    "line": st.line,
                    "keyword": st.keyword.strip(),
                    "shape": shape,
                    "kind": cls["kind"],
                    "role": cls["role"],
                    "context": context,
                }
                if cls["args"]:
                    entry["args"] = cls["args"]
                # `target` is on every step, not just requests: an assertion is
                # *about* a request, and the resource type, operation and query
                # string of that request are what name the FHIR feature under test.
                # Without this an assertion on `POST /Appointment/$book` can only be
                # reported as "operation $book" instead of "operation $book on
                # Appointment (type-level)", and the CapabilityStatement requirement
                # lookup, which is keyed by resource type, finds nothing.
                if context_detail:
                    entry["target"] = context_detail
                    if cls["role"] != "request":
                        entry["inherited"] = True
                        # Crossing a scenario boundary makes the failure downstream
                        # of another scenario's request; the gap report says so
                        # rather than reporting it as an independent finding.
                        if context_origin and context_origin["scenario"] != sc.name:
                            entry["inheritedFromScenario"] = context_origin
                cat = category_for(cls["kind"], cls["category"], context, cls["args"])
                if cat:
                    entry["category"] = cat
                # The data keys a step references are what links a failure to the
                # seeded resources, which is how a TEST_DATA_DEFECT is proven
                # rather than guessed.
                keys = sorted(fp.data_keys_in(st.full))
                if st.docstring:
                    keys = sorted(set(keys) | fp.data_keys_in(st.docstring))
                for trow in st.table:
                    for cellv in trow:
                        keys = sorted(set(keys) | fp.data_keys_in(cellv))
                if keys:
                    entry["dataKeys"] = keys
                # A FHIRPath table's rows are individually assertive; keep them so
                # a failure inside one can be quoted.
                if cls["kind"] == "fhirpath-assert-table" and st.table:
                    entry["tableRows"] = [r[0] for r in st.table if r]
                steps.append(entry)

            scenarios.append({
                "name": sc.name,
                "keyword": sc.keyword,
                "line": sc.line,
                "tags": sc.tags,
                "isPrecondition": sc.is_precondition,
                "isOutline": sc.is_outline,
                "examples": [{"line": ex.line, "name": ex.name, "tags": ex.tags,
                              "header": ex.header,
                              "rows": [{"line": ex.line + 2 + i, "values": r}
                                       for i, r in enumerate(ex.rows)]}
                             for ex in sc.examples],
                "steps": steps,
            })

        features[feat.rel] = {
            "level": feat.level,
            "module": feat.module,
            "name": feat.name,
            "line": feat.line,
            "tags": feat.tags,
            "scenarios": scenarios,
        }

    return features, census, sorted(unclassified)


# =============================================================================
# The package side
# =============================================================================

EXPECTATION_EXT = "http://hl7.org/fhir/StructureDefinition/capabilitystatement-expectation"

# Which ISiK level a package belongs to. Derived from the package name, which is
# the only signal there is, and checked: an ISiK package that matches neither
# pattern is reported rather than silently filed under one of them.
def index_cross_version_extensions(root: Path, packages_dir: Path) -> dict:
    """Cross-version extension URLs the suite requires, and who defines them.

    One class of failure in this rig belongs to neither side of the usual line.
    A Stufe 5 feature asserts an extension whose URL carries another FHIR
    version - `http://hl7.org/fhir/5.0/StructureDefinition/extension-Encounter.
    plannedStartDate` - and the ISiK package itself ships the StructureDefinition
    for it, so a body that carries it conforms to the ISiK profile. The same
    body, inside a search Bundle, is then validated against core FHIR R4 by a
    later step, and the core validator rejects any `/5.0/` extension URL as an
    invalid version. No response can satisfy both steps: the conflict is inside
    the test suite.

    Charging that to the product would be wrong, and so would charging it to this
    project's test data - which is why it is recorded here, from the two sources
    that settle it, rather than being recognised from the message text alone:

      `requiredBy`  the feature files that assert the URL, with line numbers.
      `definedBy`   the packages whose StructureDefinition declares that URL.

    gap_report.py attributes a rejection to UPSTREAM_SUITE_CONFLICT only when
    both lists are non-empty, so a URL that merely looks cross-version, or one
    this round's packages do not define, stays with the SUT.
    """
    rx = re.compile(r"http://hl7\.org/fhir/\d+\.\d+/StructureDefinition/"
                    r"extension-[A-Za-z0-9_.-]+")
    out: dict[str, dict] = {}
    for feat in fp.load_features(root):
        for sc in feat.scenarios:
            for st in sc.steps:
                text = st.full + (st.docstring or "")
                for url in rx.findall(text):
                    rec = out.setdefault(url, {"requiredBy": [], "definedBy": []})
                    rec["requiredBy"].append({"feature": feat.rel, "line": st.line,
                                              "level": feat.level})
    if not out or not packages_dir.is_dir():
        return out
    for sd in sorted(packages_dir.glob("*/*.json")):
        name = sd.name
        if not name.startswith("StructureDefinition-"):
            continue
        try:
            doc = json.loads(sd.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        url = doc.get("url")
        if isinstance(url, str) and url in out:
            out[url]["definedBy"].append(sd.parent.name)
    return out


def level_of_package(name: str) -> str | None:
    if name == "de.gematik.isik" or name.startswith("de.gematik.isik5"):
        return "Stufe5"
    if name.startswith("de.gematik.isik-") or name.startswith("de.gematik.isik3-"):
        return "Stufe3"
    return None


def expectation_of(node: dict) -> str | None:
    """The capabilitystatement-expectation code (SHALL / SHOULD / MAY) on a node.

    This is what makes a CapabilityStatement assertion citable as a *requirement*
    rather than as a preference: the suite only asserts SHALL entries, and quoting
    the expectation lets a developer see that for themselves.
    """
    for ext in node.get("extension") or []:
        if ext.get("url") == EXPECTATION_EXT:
            return ext.get("valueCode")
    return None


def index_packages(packages_dir: Path) -> dict:
    """The conformance-requirement index, from the packages the validator uses."""
    out = {
        "canonicals": {},              # url -> [package, resourceType, name]
        "interactions": {},            # "Type|code" -> [requirement, ...]
        "searchParams": {},            # "Type|name|type" -> [requirement, ...]
        "operations": {},              # "Type|code" -> [requirement, ...]
        "operationDefinitions": {},    # url -> {code, package, resource, scopes}
        "profileElements": {},         # canonical -> {mustSupport, required, ...}
        "capabilityStatements": {},    # url -> {name, package, level}
        "packages": [],
        "unassignedIsikPackages": [],
    }

    if not packages_dir.is_dir():
        return out

    canon = load_canonicals(packages_dir)
    for url, (path, label) in canon.items():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out["canonicals"][url] = [label, doc.get("resourceType"), doc.get("name")]
        if doc.get("resourceType") == "OperationDefinition":
            out["operationDefinitions"][url] = {
                "code": doc.get("code"),
                "name": doc.get("name"),
                "package": label,
                "resource": doc.get("resource") or [],
                "scopes": [s for s, on in (("system", doc.get("system")),
                                           ("type", doc.get("type")),
                                           ("instance", doc.get("instance"))) if on],
            }

    # Directories only. 20-fetch-packages.sh writes MANIFEST.txt and PARITY.txt into
    # this same directory, and counting a file as a package would put a
    # "MANIFEST.txt@?" row in the index and inflate every package count derived from
    # it by two.
    for pkg_dir in sorted(p for p in packages_dir.iterdir() if p.is_dir()):
        root = pkg_dir / "package"
        if not root.is_dir():
            root = pkg_dir
        meta = root / "package.json"
        name, version = pkg_dir.name, "?"
        if meta.is_file():
            try:
                m = json.loads(meta.read_text(encoding="utf-8"))
                name, version = m.get("name", pkg_dir.name), m.get("version", "?")
            except (OSError, ValueError):
                pass
        label = f"{name}@{version}"
        level = level_of_package(name)
        out["packages"].append({"package": label, "name": name, "version": version,
                                "isikLevel": level})
        if name.startswith("de.gematik.isik") and level is None:
            out["unassignedIsikPackages"].append(label)

        # Only the ISiK packages' conformance resources are indexed. The base and
        # terminology packages ship CapabilityStatements too (hl7.fhir.r4.core has
        # eight examples), and treating those as requirements would attribute an
        # ISiK failure to an HL7 example resource.
        if not name.startswith("de.gematik.isik"):
            continue

        for path in sorted(root.glob("CapabilityStatement-*.json")):
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if doc.get("resourceType") != "CapabilityStatement":
                continue
            cs_url = doc.get("url") or path.name
            cs_name = doc.get("name") or "?"
            out["capabilityStatements"][cs_url] = {
                "name": cs_name, "package": label, "isikLevel": level,
                "file": path.name,
            }

            def record(bucket: str, key: str, node: dict, extra: dict | None = None):
                rec = {"capabilityStatement": cs_name, "canonical": cs_url,
                       "expectation": expectation_of(node), "package": label,
                       "isikLevel": level}
                if extra:
                    rec.update(extra)
                out[bucket].setdefault(key, []).append(rec)

            for rest in doc.get("rest") or []:
                for op in rest.get("operation") or []:
                    record("operations", f"*|{op.get('name')}", op,
                           {"definition": op.get("definition"), "scope": "system"})
                for res in rest.get("resource") or []:
                    rtype = res.get("type")
                    for node in res.get("interaction") or []:
                        record("interactions", f"{rtype}|{node.get('code')}", node)
                    for node in res.get("searchParam") or []:
                        record("searchParams",
                               f"{rtype}|{node.get('name')}|{node.get('type')}", node,
                               {"definition": node.get("definition")})
                    for node in res.get("operation") or []:
                        record("operations", f"{rtype}|{node.get('name')}", node,
                               {"definition": node.get("definition"),
                                "scope": "resource"})

        # The element-level constraints of this package's own profiles, from the
        # **snapshot**, not the differential. The differential looks like the
        # cheaper choice and gives wrong answers: ISiKAbrechnungsfall's
        # differential says nothing about `Account.identifier.type`'s cardinality
        # (it only marks it mustSupport), while its snapshot carries `min=1` on the
        # `Abrechnungsnummer` slice. The validator's most common complaint on that
        # profile is exactly "Account.identifier[0].type: minimum required = 1",
        # so a differential-only index would report that constraint as merely
        # mustSupport and the entry would understate the requirement.
        #
        # Element ids, not just paths, are kept for the required set: the id names
        # the slice (`Account.identifier:Abrechnungsnummer.type`), which is the
        # difference between "every identifier needs a type" and "the billing-number
        # identifier needs a type". A developer needs the second reading.
        for path in sorted(root.glob("StructureDefinition-*.json")):
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if doc.get("resourceType") != "StructureDefinition" or not doc.get("url"):
                continue
            els = (doc.get("snapshot") or {}).get("element") or []
            fallback = False
            if not els:
                # A profile shipped without a snapshot: the differential is all
                # there is. Recorded so the report can say the attribution is
                # narrower for this profile rather than silently offering less.
                els = (doc.get("differential") or {}).get("element") or []
                fallback = True
            must, need = [], {}
            for e in els:
                p = e.get("path")
                if not p:
                    continue
                if e.get("mustSupport") and p not in must:
                    must.append(p)
                if isinstance(e.get("min"), int) and e["min"] > 0:
                    # Several slices can constrain one path; keep the tightest
                    # minimum and every id that imposes it.
                    row = need.setdefault(p, {"min": 0, "ids": []})
                    row["min"] = max(row["min"], e["min"])
                    eid = e.get("id") or p
                    if eid not in row["ids"]:
                        row["ids"].append(eid)
            rec = {
                "package": label, "name": doc.get("name"), "type": doc.get("type"),
                "baseDefinition": doc.get("baseDefinition"),
                "isikLevel": level,
                "mustSupport": must, "required": need,
            }
            if fallback:
                rec["constraintSource"] = "differential"
                rec["note"] = ("this profile ships no snapshot, so inherited and "
                               "sliced constraints are not represented")
            out["profileElements"][doc["url"]] = rec

    return out


# =============================================================================
# Validation modules
# =============================================================================

def resolve_canonical(canonicals: dict[str, list], url: str) -> tuple[str, list] | None:
    """(canonical as the package declares it, record) for a URL a feature asserts.

    The scheme is normalised because the two sides genuinely disagree: the feature
    files assert ``https://hl7.org/fhir/StructureDefinition/Bundle`` (38 times,
    every `CORE` bundle validation), while ``hl7.fhir.r4.core#4.0.1`` declares that
    profile at ``http://``. FHIR canonicals are opaque identifiers, so strictly
    those are two different profiles and the validator's tolerance here is a
    kindness. Matching on the scheme-insensitive form is what lets the gap report
    attribute a CORE bundle failure to a package instead of reporting an
    unresolvable profile 38 times over.
    """
    hit = canonicals.get(url)
    if hit:
        return url, hit
    if url.startswith("https://"):
        alt = "http://" + url[len("https://"):]
    elif url.startswith("http://"):
        alt = "https://" + url[len("http://"):]
    else:
        return None
    hit = canonicals.get(alt)
    return (alt, hit) if hit else None


def index_validation_modules(reqs: dict, canonicals: dict[str, list],
                             plugins_dir: Path) -> tuple[dict, list[str]]:
    """module id -> {level, profiles, packages, plugin}.

    Two independent derivations, deliberately kept apart:

    * **measured** - the profile canonicals the feature files hand to that module,
      resolved through the unpacked package set. Works on both package routes.
    * **declared** - the module's own refv plugin ``config.yaml``. Authoritative
      when present, absent on the ``simplifier`` route.

    Both are recorded. Where they disagree, the disagreement is the finding.
    """
    notes: list[str] = []
    modules: dict[str, dict] = {}
    # Aggregated rather than per-occurrence: `https://…/Bundle` alone would emit
    # 38 identical notes and bury everything else.
    rescheme: dict[str, str] = {}
    unresolved: dict[str, set[str]] = {}

    for feat in reqs.get("features", []):
        for canonical, module_ids in (feat.get("profiles") or {}).items():
            for mid in module_ids:
                m = modules.setdefault(mid, {
                    "profiles": {}, "features": set(), "levels": set(),
                    "measuredPackages": {},
                })
                m["profiles"].setdefault(canonical, 0)
                m["profiles"][canonical] += 1
                m["features"].add(feat["rel"])
                m["levels"].add(feat["level"])
                found = resolve_canonical(canonicals, canonical)
                if found:
                    declared, hit = found
                    m["measuredPackages"][hit[0]] = \
                        m["measuredPackages"].get(hit[0], 0) + 1
                    if declared != canonical:
                        rescheme[canonical] = f"{declared} ({hit[0]})"
                else:
                    unresolved.setdefault(canonical, set()).add(mid)

    for asserted, declared in sorted(rescheme.items()):
        notes.append(f"the feature files assert profile {asserted}, but the "
                     f"packages declare it as {declared}; matched on the "
                     "scheme-insensitive canonical")
    for canonical, mids in sorted(unresolved.items()):
        notes.append(f"validation module(s) {', '.join(sorted(mids))} assert "
                     f"profile {canonical}, which no unpacked package supplies - "
                     "a failure on it cannot name a supplying package")

    plugins: dict[str, dict] = {}
    if plugins_dir.is_dir() and yaml is not None:
        sys.path.insert(0, str(REPO / "scripts" / "lib"))
        from plan_packages import load_plugin        # noqa: PLC0415  (optional dep)
        for entry in sorted(plugins_dir.iterdir()):
            cfg = entry / "config.yaml"
            if cfg.is_file():
                try:
                    plugins[entry.name] = load_plugin(str(cfg))
                except SystemExit:
                    # load_plugin exits on a malformed descriptor. The index must
                    # not take the whole `make packages` down with it: the measured
                    # derivation still stands and the defect is reported.
                    notes.append(f"refv plugin descriptor {cfg} is malformed and "
                                 "was skipped")
    elif not plugins_dir.is_dir():
        notes.append("vendor/refv-plugins/ is absent (PACKAGE_SOURCE=simplifier, "
                     "or `make packages` has not run) - validation-module "
                     "attribution is derived from the observed profile canonicals "
                     "only, with no declared dependency list to check it against")
    elif yaml is None:
        notes.append("PyYAML is unavailable, so the refv plugin descriptors were "
                     "not read; attribution falls back to the measured canonicals")

    out: dict[str, dict] = {}
    for mid, m in sorted(modules.items()):
        plugin = plugins.get(mid)
        rec = {
            "levels": sorted(m["levels"]),
            "features": len(m["features"]),
            "profiles": sorted(m["profiles"]),
            "measuredPackages": [p for p, _ in sorted(m["measuredPackages"].items(),
                                                      key=lambda kv: (-kv[1], kv[0]))],
        }
        if plugin:
            rec["declared"] = {
                "primaryPackages": plugin["primaryPackages"],
                "dependencies": plugin["dependencies"],
                "snapshotDependencies": plugin["snapshotDependencies"],
                "ignoredCodeSystems": plugin["ignoredCodeSystems"],
                "ignoredValueSets": plugin["ignoredValueSets"],
                "specUrl": plugin["specUrl"],
                "configFile": plugin["configFile"],
            }
            declared = {p.replace("#", "@") for p in plugin["primaryPackages"]}
            unexpected = [p for p in rec["measuredPackages"] if p not in declared]
            if unexpected and declared:
                # Not an error: `CORE` asserts hl7.fhir.r4.core profiles and every
                # ISiK module inherits from de.basisprofil.r4, so a measured
                # package outside the declared primaries is normal. Recorded so
                # the gap report can name the primary package first.
                rec["measuredBeyondPrimary"] = unexpected
        else:
            rec["declared"] = None
        out[mid] = rec

    # `CORE` is the validator's built-in module for base FHIR resources; it has no
    # plugin descriptor and never will. Say so, rather than leaving a reader to
    # wonder why one module has no declared dependency list.
    if "CORE" in out and out["CORE"]["declared"] is None:
        out["CORE"]["note"] = ("the reference validator's built-in module for base "
                               "FHIR R4 resources; it ships no refv plugin, so it "
                               "has no declared dependency list by design")

    return out, notes


# =============================================================================
# Main
# =============================================================================

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--features-root", type=Path,
                    default=REPO / "vendor" / "upstream" / "features")
    ap.add_argument("--requirements", type=Path,
                    default=REPO / "vendor" / "requirements.json")
    ap.add_argument("--packages", type=Path, default=REPO / "vendor" / "packages")
    ap.add_argument("--plugins", type=Path, default=REPO / "vendor" / "refv-plugins")
    ap.add_argument("--out", type=Path, default=REPO / "gap-analysis" / "index.json")
    ap.add_argument("--verify", action="store_true",
                    help="exit non-zero if any step shape is unclassified or any "
                         "shape's occurrence count differs from the pinned "
                         "expectation")
    args = ap.parse_args()

    if not args.features_root.is_dir():
        print(f"build_index.py: {args.features_root} does not exist.\n"
              "  The feature files are extracted from the pinned test suite image "
              "by scripts/20-fetch-packages.sh.\n"
              "  Run: make packages", file=sys.stderr)
        return 2
    if not args.requirements.is_file():
        print(f"build_index.py: {args.requirements} does not exist.\n"
              "  It is written by scripts/25-extract-requirements.sh, which "
              "`make packages` runs immediately before this step.", file=sys.stderr)
        return 2

    reqs = json.loads(args.requirements.read_text(encoding="utf-8"))

    try:
        features, census, unclassified = index_features(args.features_root)
    except fp.FeatureParseError as exc:
        print(f"build_index.py: cannot read the feature files: {exc}", file=sys.stderr)
        return 2

    notes: list[str] = []
    pkg = index_packages(args.packages)
    if not pkg["packages"]:
        notes.append(f"{args.packages} holds no unpacked FHIR packages, so no "
                     "failure can be attributed to a supplying package. Run "
                     "`make packages` before `make gap-report`.")
    for label in pkg["unassignedIsikPackages"]:
        notes.append(f"ISiK package {label} matches neither the Stufe 3 nor the "
                     "Stufe 5 naming pattern; its conformance resources are "
                     "indexed without a level")

    modules, module_notes = index_validation_modules(reqs, pkg["canonicals"],
                                                     args.plugins)
    notes.extend(module_notes)

    xver = index_cross_version_extensions(args.features_root, args.packages)
    for url, rec in sorted(xver.items()):
        if not rec["definedBy"]:
            notes.append(f"the suite's feature files require the cross-version "
                         f"extension {url}, which no package loaded by this round "
                         f"defines; a rejection of it will be attributed to the "
                         f"SUT rather than to the suite")

    # --- shape census, against the pinned expectations -----------------------
    # Recorded rather than asserted so that the index itself carries the evidence
    # for the classification's completeness, and so a suite version bump produces
    # a diff here instead of a quietly emptier gap report.
    by_kind: dict[str, int] = {}
    for shape, row in census.items():
        by_kind[row["kind"] or "(unclassified)"] = \
            by_kind.get(row["kind"] or "(unclassified)", 0) + row["count"]
    drift = []
    for spec in STEP_KINDS:
        got = by_kind.get(spec["kind"], 0)
        if got != spec["expected"]:
            drift.append({"kind": spec["kind"], "expected": spec["expected"],
                          "found": got})
    for shape in unclassified:
        notes.append(f"step shape not in the classification table, so a failure on "
                     f"it lands in UNCLASSIFIED: {shape!r}")
    for d in drift:
        notes.append(f"step kind {d['kind']} occurs {d['found']} times, but this "
                     f"index was written against {d['expected']}. The test suite "
                     "version changed; check the classification table.")

    index = {
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generator": "gap-analysis/build_index.py",
        "purpose": "attribution index for the developer gap analysis: step shape "
                   "-> FHIR feature -> supplying FHIR package -> IRIS subsystem",
        "source": {
            "suite": reqs.get("source"),
            "featuresRoot": str(args.features_root),
            "requirements": str(args.requirements),
            "packages": str(args.packages),
            "plugins": str(args.plugins) if args.plugins.is_dir() else None,
            "tagExpressions": reqs.get("tagExpressions"),
        },
        "categories": CATEGORIES,
        "irisLociProvenance": IRIS_LOCI_PROVENANCE,
        "statusRules": STATUS_RULES,
        "responseCodeRules": RESPONSE_CODE_RULES,
        "responseBodyRules": RESPONSE_BODY_RULES,
        "prerequisiteRules": PREREQUISITE_RULES,
        "stepKinds": [
            {"kind": s["kind"], "role": s["role"], "category": s["category"],
             "what": s["what"], "expectedOccurrences": s["expected"],
             "foundOccurrences": by_kind.get(s["kind"], 0)}
            for s in STEP_KINDS
        ],
        "subStepKinds": [
            {"kind": s["kind"], "category": s["category"], "what": s["what"],
             "pattern": s["re"].pattern}
            for s in SUBSTEP_KINDS
        ],
        "stepShapes": [
            {"shape": shape, "count": row["count"], "kind": row["kind"]}
            for shape, row in sorted(census.items(), key=lambda kv: -kv[1]["count"])
        ],
        "unclassifiedShapes": unclassified,
        "shapeDrift": drift,
        "validationModules": modules,
        "conformance": {
            "capabilityStatements": pkg["capabilityStatements"],
            "interactions": pkg["interactions"],
            "searchParams": pkg["searchParams"],
            "operations": pkg["operations"],
            "operationDefinitions": pkg["operationDefinitions"],
        },
        "suiteRequiredCrossVersionExtensions": xver,
        "profileElements": pkg["profileElements"],
        "canonicals": pkg["canonicals"],
        "packages": pkg["packages"],
        "features": features,
        "notes": notes,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(index, indent=1, ensure_ascii=False) + "\n",
                        encoding="utf-8")

    n_steps = sum(len(sc["steps"]) for f in features.values()
                  for sc in f["scenarios"])
    n_scen = sum(len(f["scenarios"]) for f in features.values())
    size_kb = args.out.stat().st_size // 1024
    print(f"  {len(features)} features, {n_scen} scenarios, {n_steps} steps "
          f"classified into {len(census)} shapes / "
          f"{len([s for s in STEP_KINDS])} kinds", file=sys.stderr)
    print(f"  {len(pkg['canonicals'])} canonicals over {len(pkg['packages'])} "
          f"packages; {len(pkg['interactions'])} interaction, "
          f"{len(pkg['searchParams'])} search-parameter and "
          f"{len(pkg['operations'])} operation requirements; "
          f"{len(pkg['profileElements'])} ISiK profiles", file=sys.stderr)
    print(f"  {len(modules)} validation modules "
          f"({sum(1 for m in modules.values() if m['declared'])} with a plugin "
          f"descriptor)", file=sys.stderr)
    print(f"  wrote {args.out.relative_to(REPO) if args.out.is_relative_to(REPO) else args.out}"
          f" ({size_kb} KiB)", file=sys.stderr)
    for note in notes:
        print(f"  NOTE {note}", file=sys.stderr)

    if args.verify and (unclassified or drift):
        print("build_index.py: --verify failed - the classification table does not "
              "match this test suite version", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
