#!/usr/bin/env python3
"""Turn the official ISiK report into a cited results document.

Run by scripts/70-collect-report.sh (`make report`). Reads the archives the test
suite produced - already extracted, verbatim, under reports/<run-id>/<stufe>/ -
and writes:

    analysis/results.md              the inspector-facing evidence index
    analysis/results.json            the same, machine-readable, consumed by
                                     gap-analysis/gap_report.py
    analysis/scenario-verdicts.csv   one row per test case, for spreadsheet use

Design constraint: the official report is authoritative, this document is
derived
--------------------------------------------------------------------------
German regulatory inspectors work with the artefacts they know and are entitled
to distrust anything a script wrote. So every statement this generator makes
carries the path of the file inside the official archive that substantiates it,
and the archives themselves are never touched. If this document and the official
report disagree, the official report wins - and saying so is not modesty, it is
the only claim that makes the derived document usable as evidence at all.

Two citation targets exist per test case, and both are recorded when present:

    <stufe>/official/extracted/test-report/unresolvedReports/<name>.json
        The machine-readable Serenity TestOutcome. Part of test-report.zip -
        i.e. part of the artefact submitted to TITUS - so an inspector holding
        only the submission can still follow the citation.
    <stufe>/official/extracted/debug-report/serenity/<name>.html
        The same outcome as the browsable Serenity page, with request and
        response bodies. Only in debug-report.zip, which is not submitted.

The two share a base name because Serenity derives both from one ReportNamer,
and the base name is a sha256 of the outcome's qualified id - opaque, and
deliberately not reconstructed here. It is read from the filename we were given,
and the .html sibling is probed rather than assumed.

Why the joins are what they are
-------------------------------
Serenity does not record the feature-file line of a scenario, and its report
filenames are hashes, so neither can be used to point an inspector at the
requirement that was tested. Three joins bridge that:

  * ``userStory.path`` is the feature path relative to the suite's own
    ``features/`` directory, which after normalisation is the ``rel`` key of
    vendor/requirements.json. That gives level, module and scope.

    Normalisation is required and is not cosmetic. Serenity 5.3.10 records the
    path **without the ``.feature`` suffix** ("Stufe3/Basis/Account-Read"),
    while ``rel`` carries it ("Stufe3/Basis/Account-Read.feature"). Joining on
    the raw value matches nothing at all, and the failure is not quiet in the
    counts - it raises unknownFeature + missingFeatureFile for every single
    case, classifies every precondition block as an ordinary test (74 and 112 of
    them, which inflates the conformance denominator), and loses every scenario
    line number the citations depend on. ``feature_rel()`` below is therefore
    the only place this key is built.
  * the scenario *name* is matched against the parsed feature file to recover
    its declaration line, its tags, and whether it is a ``@vorbedingung`` /
    ``@Precondition`` block rather than a test.
  * ``dataTable.rows[i].lineNumber`` gives the exact Examples-row line for a
    Scenario Outline, and ``testSteps[i].lineNumber`` the line of a failing step.
    Which row belongs to which step is **not** taken from the index, for the
    reasons in ``match_step_rows()``.

Case counting follows Serenity's own rule (``TestOutcome.getTestCount()``): a
data-driven outcome counts one case per Examples row, everything else counts
one. That is why the expected totals are 506 (Stufe 3) and 895 (Stufe 5) rather
than the scenario-keyword count - and why the reconciliation section can be a
straight equality check against vendor/requirements.json.

Applying that rule needs one correction, because Serenity 5.3.10 misattributes
``dataTable`` between feature files that share a basename. ISiK 2.7.0's Stufe 5
has **16** such basenames across its 124 features (``Practitioner-Search.feature``
exists in both ``Basis/`` and ``Medikation/``; ``Patient-Search.feature`` and
``Encounter-Search.feature`` exist four times each), while Stufe 3 has none at
all - which is why only Stufe 5 is affected. Taken at face value the
report claims 905 Stufe 5 cases rather than 895, and one failing case appears as
a pass. Steps are therefore attributed to Examples rows by the parameters each
step records in its own title; with that, both levels reconcile exactly.
``match_step_rows()`` carries the measurement and the failure modes.

Nothing here interprets *why* a case failed. That is the gap report's job
(gap-analysis/gap_report.py), which joins this file's ``failure`` records
against the scenario -> validation-module -> FHIR-package index.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))
import feature_parser as fp  # noqa: E402  (path set above, by design)

LEVELS = ("Stufe3", "Stufe5")
STUFE_DIR = {"Stufe3": "stufe3", "Stufe5": "stufe5"}

# net.thucydides.model.domain.TestResult, in the enum's own order. Grouped into
# three verdicts because that is the granularity a conformance statement needs;
# the raw value is kept alongside so nothing is lost by the grouping.
RESULT_ORDER = ("SUCCESS", "FAILURE", "ERROR", "COMPROMISED", "UNSUCCESSFUL",
                "PENDING", "SKIPPED", "IGNORED", "ABORTED", "UNDEFINED")
FAIL_RESULTS = {"FAILURE", "ERROR", "COMPROMISED", "UNSUCCESSFUL"}
PASS_RESULTS = {"SUCCESS"}

# Longest message quoted inline in the markdown. The full text is in results.json
# and, verbatim, in the official report; a Tiger validator message can run to
# several kilobytes of profile diagnostics and would make the table unreadable.
MSG_LIMIT = 320

# Who owns each class of dependency rewrite recorded by
# scripts/lib/order_packages.py. The `kind` and the prose reason both come from the
# plan; only this attribution is added here, because the audience of §6 is a reader
# deciding whether a package-tree modification says anything about the SUT - and
# none of these does. Kept short: it is a table cell, and the plan carries the full
# reason, which is rendered underneath.
REWRITE_OWNERS = {
    "wildcard": (
        "InterSystems IRIS — the package is correct as published; a semver range "
        "is permitted and the loader does not resolve one"
    ),
    "absent-version": (
        "Upstream implementation guide — it declares a version the package "
        "registry has never published"
    ),
    "excluded-from-sut": (
        "This project — the dependency is the example corpus we deliberately do "
        "not load; see `excludedPackages`"
    ),
}


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dedupe_anomalies(records: list[dict]) -> list[dict]:
    """Drop byte-identical anomaly records, keeping the first of each.

    Several anomalies are raised per *outcome* but describe a property of the
    *feature* - three scenarios from one misplaced feature file raise the same
    `wrongLevel` record three times. Identical records carry no extra information
    in either the markdown or the JSON, and a `notExecuted`-style aggregate is
    used wherever the per-item detail does matter.
    """
    seen: set[tuple] = set()
    out: list[dict] = []
    for r in records:
        key = (r.get("kind"), r.get("detail"), r.get("where"))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def verdict_of(result: str) -> str:
    r = (result or "").upper()
    if r in PASS_RESULTS:
        return "pass"
    if r in FAIL_RESULTS:
        return "fail"
    return "other"


# -----------------------------------------------------------------------------
# Reading the official report
# -----------------------------------------------------------------------------

def is_outcome(doc: Any) -> bool:
    """Whether a JSON document from the report directory is a TestOutcome.

    Tiger's ``generate-serenity-reports`` mojo copies *every* root ``*.json``
    into ``unresolvedReports/``, and Serenity writes more than outcomes there
    (``serenity-summary.json``, ``manifest.json`` in some configurations). A
    document is taken as an outcome only if it has the two fields every outcome
    has; anything else is counted as skipped and reported, never silently
    dropped.
    """
    return isinstance(doc, dict) and "testSteps" in doc and "name" in doc


def load_outcomes(report_dir: Path) -> tuple[list[tuple[str, dict]], list[str], list[str]]:
    """(name, outcome) pairs, non-outcome filenames, unreadable filenames."""
    outcomes: list[tuple[str, dict]] = []
    other: list[str] = []
    broken: list[str] = []
    for path in sorted(report_dir.glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            broken.append(f"{path.name}: {exc}")
            continue
        if is_outcome(doc):
            outcomes.append((path.stem, doc))
        elif isinstance(doc, list) and doc and all(is_outcome(d) for d in doc):
            # Not a shape Serenity 5.3.10 writes, accepted defensively: a future
            # release that batches outcomes into one file must not make this
            # generator report zero test cases.
            for i, d in enumerate(doc):
                outcomes.append((f"{path.stem}[{i}]", d))
        else:
            other.append(path.name)
    return outcomes, other, broken


def walk_steps(steps: Iterable[dict] | None, depth: int = 0) -> Iterator[tuple[int, dict]]:
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        yield depth, step
        yield from walk_steps(step.get("children"), depth + 1)


def failing_leaf(steps: Iterable[dict] | None) -> dict | None:
    """The deepest failing step in a subtree - the one that actually asserted.

    A Cucumber step in Serenity is a tree: the scenario's ``Then ...`` step owns
    the Tiger glue calls it expanded into, and all of them inherit FAILURE. The
    ancestor names the requirement, the leaf names the assertion, and the leaf is
    what a developer needs, so it is what is recorded. The ancestor is recovered
    separately by :func:`failing_ancestor`.
    """
    for _depth, step in walk_steps(steps):
        if (step.get("result") or "").upper() not in FAIL_RESULTS:
            continue
        kids = step.get("children") or []
        if not any((k.get("result") or "").upper() in FAIL_RESULTS
                   for k in kids if isinstance(k, dict)):
            return step
    return None


def failing_ancestor(steps: Iterable[dict] | None) -> dict | None:
    """The outermost failing step: the Gherkin step as the feature file wrote it."""
    for depth, step in walk_steps(steps):
        if depth == 0 and (step.get("result") or "").upper() in FAIL_RESULTS:
            return step
    return None


def rest_query_of(step: dict) -> dict | None:
    """The HTTP exchange Serenity recorded on a step, if any.

    ``TestStep.restQuery`` (net.serenitybdd.model.rest.RestQuery) is where the
    actual request and response live. Bodies are omitted here: they are large,
    they are in the official archive already, and results.json is a joining
    record rather than a copy of the evidence. The gap report reads the bodies
    from the extracted archive when it needs them.
    """
    rq = step.get("restQuery")
    if not isinstance(rq, dict):
        return None
    out = {
        "method": rq.get("method"),
        "path": rq.get("path"),
        "statusCode": rq.get("statusCode"),
        "contentType": rq.get("contentType"),
    }
    return out if any(v is not None for v in out.values()) else None


def failure_record(steps: Iterable[dict] | None, outcome: dict) -> dict | None:
    """What failed, where, and what the SUT answered - or None if nothing failed."""
    leaf = failing_leaf(steps)
    top = failing_ancestor(steps)
    if leaf is None and top is None:
        # A failed outcome with no failed step happens: a scenario that could not
        # start (unreachable SUT, a Tiger configuration error) fails at the
        # outcome level. The outcome-level cause is then the only evidence there
        # is, and reporting nothing here would hide it.
        cause = outcome.get("testFailureCause") or {}
        msg = outcome.get("testFailureMessage") or cause.get("message")
        if not msg and not cause:
            return None
        return {
            "step": None, "stepLine": None, "gherkinStep": None,
            "errorType": cause.get("errorType") or outcome.get("testFailureClassname"),
            "message": msg,
            "http": None,
            "scope": "outcome",
        }

    src = leaf or top or {}
    exc = src.get("exception") or outcome.get("testFailureCause") or {}
    http = None
    # The exchange may be recorded on the failing leaf or on an earlier sibling
    # (the request step succeeded, the assertion step after it failed), so the
    # whole subtree is searched, latest-first.
    for _d, step in reversed(list(walk_steps(steps))):
        http = rest_query_of(step)
        if http:
            break
    return {
        "step": src.get("description"),
        "stepLine": src.get("lineNumber") or None,
        "gherkinStep": (top or {}).get("description") if top is not src else None,
        "errorType": exc.get("errorType") or outcome.get("testFailureClassname"),
        "message": exc.get("message") or outcome.get("testFailureMessage"),
        "http": http,
        "scope": "step",
    }


# -----------------------------------------------------------------------------
# The feature-file side of the join
# -----------------------------------------------------------------------------

def feature_rel(path: str) -> str:
    """Serenity's ``userStory.path`` as the ``rel`` key of requirements.json.

    Serenity 5.3.10 strips the ``.feature`` suffix and can use either separator
    depending on the platform that produced the report; ``rel`` always carries the
    suffix and always uses ``/``. Both are normalised here rather than at the call
    sites, because a join that silently matches nothing costs the whole document:
    see the module docstring.

    Idempotent, so it is safe to apply to a value that is already a ``rel``.
    """
    rel = (path or "").replace("\\", "/").strip("/")
    if rel and not rel.endswith(".feature"):
        rel += ".feature"
    return rel


class FeatureIndex:
    """Scenario name -> declaration line, tags, precondition flag, per feature.

    Built with the same reader the requirement extractor and `make check` use, so
    "precondition block" and "in scope" mean the same thing in this document as
    in docs/en/generated/REQUIREMENTS-*.md. Two implementations of that would
    eventually disagree, and the disagreement would surface as a results document
    that contradicts the requirement tables.
    """

    def __init__(self, features_root: Path):
        self.root = features_root
        self.by_rel: dict[str, fp.Feature] = {}
        self.ambiguous: list[str] = []
        for feature in fp.load_features(features_root):
            self.by_rel[feature.rel] = feature

    def scenario(self, rel: str, name: str) -> fp.Scenario | None:
        feature = self.by_rel.get(rel)
        if feature is None:
            return None
        hits = [s for s in feature.scenarios if s.name == name]
        if not hits:
            # Serenity titles a scenario from the Gherkin name but normalises
            # whitespace; compare collapsed as a fallback before giving up.
            want = " ".join(name.split())
            hits = [s for s in feature.scenarios if " ".join(s.name.split()) == want]
        if len(hits) > 1:
            self.ambiguous.append(f"{rel}: {len(hits)} scenarios named {name!r}")
        return hits[0] if hits else None


# -----------------------------------------------------------------------------
# Case extraction
# -----------------------------------------------------------------------------

# A top-level step of a data-driven outcome, as Serenity 5.3.10 titles it in
# BaseStepListener.exampleStarted(): "<1-based index>: <scenario name> (<params>)",
# where <params> is either the word "null" or a java.util.Map#toString of the
# Examples row, e.g. "{searchParamValue=_id, searchParamType=token}".
#
# The parameters are what make this parse worth doing: they are the step's own,
# self-contained record of which Examples row it ran, and they are the only
# trustworthy one. See match_step_rows() for why the index is not usable and the
# sibling dataTable is not either.
EXAMPLE_STEP = re.compile(r"^\s*(\d+):\s+(?P<name>.*?)\s+\((?P<params>null|\{.*\})\)\s*$",
                          re.DOTALL)


def parse_example_step(description: str | None) -> dict[str, str] | None:
    """The Examples row a top-level step ran, or None if the step does not say.

    Returns the parameter map, which is empty-but-not-None only if Serenity wrote
    ``{}``. ``None`` means either the title did not parse or it carried ``null``
    in place of parameters - both of which mean the same thing to the caller:
    this step cannot be attributed to a row.

    The map is split on ``", "`` and ``"="``, which is exactly what
    ``AbstractMap.toString`` produces and is unambiguous for the values ISiK's
    Examples tables contain (search parameter names, FHIR types, LOINC codes and
    ``${data.*}`` placeholders). A value containing ``", "`` would split wrongly;
    that is accepted because the consequence is a row that fails to match, which
    is reported as an anomaly rather than being silently mis-attributed.
    """
    if not description:
        return None
    m = EXAMPLE_STEP.match(description)
    if not m:
        return None
    params = m.group("params")
    if params == "null":
        return None
    out: dict[str, str] = {}
    for part in params[1:-1].split(", "):
        key, sep, value = part.partition("=")
        if sep:
            out[key.strip()] = value
    return out or None


def match_step_rows(steps: list[dict], headers: list[str],
                    rows: list[dict]) -> list[int | None] | None:
    """For each top-level step, the index of the Examples row it ran.

    ``None`` (instead of a list) means the steps carry no parameters at all, so
    no attribution is possible. A ``None`` *element* means that step's parameters
    match no row in this outcome's table.

    Why this exists rather than ``rows[i]`` for step ``i``. Serenity 5.3.10
    attaches the wrong ``dataTable`` to an outcome when two feature files in one
    run share a basename - Stufe 5 of ISiK 2.7.0 has 16 such basenames among its
    124 features, e.g. ``Stufe5/Basis/Practitioner-Search.feature`` and
    ``Stufe5/Medikation/Practitioner-Search.feature``. Measured on a full run,
    the index alignment is wrong in three distinct ways, and all three are
    corrected by matching on the parameters instead:

    * **57 Stufe 5 outcomes are not data-driven at all** yet carry a foreign
      table: a plain ``Scenario:`` with one step, given the 9- or 10-row Examples
      table of an outline elsewhere in the file. Those are recognised here by
      having no parameters (``null``), and the caller then treats them as the
      single cases they are.
    * **1 of those 57 was reported as a false pass.** ``Stufe5/Medikation/
      Practitioner-Search`` / "Search for Practitioner resource by ID" has
      ``result: FAILURE`` on both the outcome and its step, while row 0 of the
      foreign table it was given says ``SUCCESS``. Preferring the row - which is
      correct for a genuine outline, where the row carries the per-example
      verdict - turned a failing conformance case into a passing one.
    * **2 outcomes have their steps interleaved with another feature's.**
      ``Stufe5/Basis/Observation-Statuses-Search`` gets 10 steps for its own 5
      rows, alternating its ``${data.observation-*}`` parameters with the
      ``${data.medication-observation-*}`` ones of the ``Medikation`` file of the
      same basename, each numbered 1..5. The step index is therefore not even
      unique within an outcome, which is what made Stufe 5 count 905 cases
      against the specified 895.

    Matching on parameters is unambiguous in practice: over both levels of a full
    run, every one of the 74 genuinely data-driven outcomes matched each of its
    steps to exactly one row, in order and without repetition, and the only
    unmatched steps were the 10 belonging to the other feature.
    """
    parsed = [parse_example_step(s.get("description")) for s in steps]
    if all(p is None for p in parsed):
        return None
    row_values = [[str(v) for v in (r.get("values") or [])] for r in rows]
    out: list[int | None] = []
    for params in parsed:
        if params is None:
            out.append(None)
            continue
        hits = [i for i, values in enumerate(row_values)
                if all(params.get(h) == values[j]
                       for j, h in enumerate(headers) if j < len(values))]
        out.append(hits[0] if len(hits) == 1 else None)
    return out


def cases_from_outcome(name: str, outcome: dict, index: FeatureIndex,
                       reqs_by_rel: dict[str, dict], reqs_all: dict[str, dict],
                       html_dir: Path | None,
                       level_dir: str, level_name: str) -> tuple[list[dict], list[dict]]:
    """One record per test case, plus any anomalies found while extracting them."""
    anomalies: list[dict] = []
    story = outcome.get("userStory") or {}
    # Normalised, never used raw: Serenity omits the .feature suffix that `rel`
    # carries, so the raw value joins against nothing.
    rel = feature_rel(story.get("path") or "")
    scenario_name = outcome.get("name") or outcome.get("title") or "(unnamed)"

    req = reqs_by_rel.get(rel)
    feature = index.by_rel.get(rel)
    if req is None:
        # Two very different causes, worth telling apart. A feature that exists at
        # the *other* level means the container ran the wrong Maven profile - the
        # profile selects features by folder, so a Stufe3 outcome cannot appear in
        # a Stufe5 report unless MAVEN_PROFILE and TESTS_TO_RUN disagree. That is a
        # configuration error which invalidates the whole level, not a version skew.
        other = reqs_all.get(rel)
        if other is not None:
            anomalies.append({
                "kind": "wrongLevel",
                "detail": (f"the {level_name} report contains an outcome for {rel!r}, "
                           f"which belongs to {other['level']}. The Maven profile "
                           "selects features by folder, so this means the container "
                           "ran a different profile than the tag expression implies. "
                           "This level's figures are not a result for "
                           f"{level_name}."),
                "where": f"{level_dir}/run.json",
            })
        else:
            anomalies.append({
                "kind": "unknownFeature",
                "detail": (f"the report contains an outcome for {rel!r}, which is not "
                           "in vendor/requirements.json at any level - the feature "
                           "files and the image that ran are probably different "
                           "versions"),
                "where": (f"{level_dir}/official/extracted/test-report/"
                          f"unresolvedReports/{name}.json"),
            })
        req = other
    if feature is None:
        anomalies.append({
            "kind": "missingFeatureFile",
            "detail": (f"no feature file at vendor/upstream/features/{rel} - the line "
                       "citations for its scenarios are unavailable"),
            "where": f"{level_dir}/official/extracted/test-report/unresolvedReports/{name}.json",
        })
    module = (req or {}).get("module") or (rel.split("/")[1] if rel.count("/") >= 2 else "(unknown)")
    level = (req or {}).get("level") or (rel.split("/")[0] if "/" in rel else "(unknown)")

    scen = index.scenario(rel, scenario_name) if feature else None
    if feature is not None and scen is None:
        anomalies.append({
            "kind": "unknownScenario",
            "detail": (f"{rel} has no scenario named {scenario_name!r}; the line "
                       "citation and the precondition flag are unavailable for it"),
            "where": f"{level_dir}/official/extracted/test-report/unresolvedReports/{name}.json",
        })

    # The browsable sibling. Probed, never assumed: it lives in debug-report.zip,
    # which a run may not have produced, and a citation that does not resolve is
    # worse than one that is absent.
    html_rel = None
    if html_dir is not None:
        candidate = html_dir / f"{name}.html"
        if candidate.is_file():
            html_rel = f"{level_dir}/official/extracted/debug-report/serenity/{name}.html"

    base = {
        "level": level,
        "module": module,
        "feature": rel,
        "featureLine": scen.line if scen else None,
        "scenario": scenario_name,
        "scenarioTags": (list(feature.tags) + list(scen.tags)) if (feature and scen) else [],
        "isPrecondition": bool(scen and scen.is_precondition),
        "inScope": bool((req or {}).get("inScope")),
        "report": {
            "outcomeJson": (f"{level_dir}/official/extracted/test-report/"
                            f"unresolvedReports/{name}.json"),
            "outcomeHtml": html_rel,
            "scenarioId": outcome.get("scenarioId"),
            "outcomeId": outcome.get("id"),
        },
    }

    steps = outcome.get("testSteps") or []
    table = outcome.get("dataTable")
    cases: list[dict] = []

    headers = table.get("headers") or [] if isinstance(table, dict) else []
    rows = table.get("rows") or [] if isinstance(table, dict) else []
    # Which row each step ran, from the step's own title. None means this outcome
    # is not data-driven at all, however much of a dataTable it was given.
    step_rows = match_step_rows(steps, headers, rows) if rows else None

    if step_rows is not None:
        # Data-driven: Serenity creates one top-level step per Examples row
        # (BaseStepListener.exampleStarted), and TestOutcome.getTestCount() is
        # testSteps.size(). One case per step whose parameters name a row of
        # *this* outcome's table; a step that names none belongs to another
        # feature file of the same basename and is not this scenario's case at
        # all. Both departures from plain index alignment are reported.
        for i, (step, row_ix) in enumerate(zip(steps, step_rows)):
            if row_ix is None:
                anomalies.append({
                    "kind": "foreignExampleStep",
                    "detail": (f"{rel} / {scenario_name}: top-level step {i + 1} ran "
                               f"parameters that match no Examples row of this "
                               f"scenario ({step.get('description')!r}); it belongs to "
                               "another feature file of the same basename and is NOT "
                               "counted as a case of this one. Serenity defect, not "
                               "the SUT"),
                    "where": base["report"]["outcomeJson"],
                })
                continue
            row = rows[row_ix]
            values = row.get("values") or []
            result = (row.get("result") or step.get("result")
                      or outcome.get("result") or "UNDEFINED")
            case = dict(base)
            case["example"] = {
                "index": row_ix + 1,
                "line": row.get("lineNumber") or None,
                "label": step.get("description"),
                "data": dict(zip(headers, values)) if headers else values,
            }
            case["result"] = str(result).upper()
            case["verdict"] = verdict_of(case["result"])
            case["durationMs"] = step.get("duration")
            case["failure"] = (failure_record([step], outcome)
                               if case["verdict"] == "fail" else None)
            cases.append(case)
        # A table with more rows than were run is normal and not reported: an
        # outline may carry several Examples: blocks, and a block tagged @Optional
        # is filtered out by the level's tag expression while its rows stay in the
        # dataTable. Measured across a full run, all 32 outcomes in this shape had
        # attributed steps == executed steps == the count of non-@Optional rows, so
        # reporting them said nothing except that ISiK uses @Optional.
        #
        # Fewer cases than steps is different, and is already reported per step as
        # foreignExampleStep above.
        if len(cases) != len(steps):
            anomalies.append({
                "kind": "rowStepMismatch",
                "detail": (f"{rel} / {scenario_name}: {len(rows)} Examples row(s), "
                           f"{len(steps)} top-level step(s), only {len(cases)} of them "
                           "attributed to a row; cases are counted from the attributed "
                           "steps"),
                "where": base["report"]["outcomeJson"],
            })
    else:
        if rows:
            # A single-step Scenario carrying some *other* scenario's Examples
            # table. Reported because the verdict would otherwise be read off a
            # row that has nothing to do with this case - measured as one false
            # pass on a full Stufe 5 run.
            anomalies.append({
                "kind": "foreignExampleTable",
                "detail": (f"{rel} / {scenario_name}: not data-driven ({len(steps)} "
                           f"step(s), no Examples parameters) yet carries a "
                           f"{len(rows)}-row dataTable belonging to another scenario; "
                           "the table is ignored and the outcome's own result is used. "
                           "Serenity defect, not the SUT"),
                "where": base["report"]["outcomeJson"],
            })
        case = dict(base)
        case["example"] = None
        case["result"] = str(outcome.get("result") or "UNDEFINED").upper()
        case["verdict"] = verdict_of(case["result"])
        case["durationMs"] = outcome.get("duration")
        case["failure"] = (failure_record(steps, outcome)
                           if case["verdict"] == "fail" else None)
        cases.append(case)

    return cases, anomalies


# -----------------------------------------------------------------------------
# Per-level assembly
# -----------------------------------------------------------------------------

def read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def collect_level(level: str, run_root: Path, index: FeatureIndex,
                  reqs: dict) -> dict | None:
    """Everything known about one specification level's run, or None if absent."""
    stufe = STUFE_DIR[level]
    level_root = run_root / stufe
    if not level_root.is_dir():
        return None

    anomalies: list[dict] = []
    extracted = level_root / "official" / "extracted"
    unresolved = extracted / "test-report" / "unresolvedReports"
    serenity = extracted / "debug-report" / "serenity"

    run_meta = read_json(level_root / "run.json") or {}
    if not run_meta:
        anomalies.append({
            "kind": "missingRunRecord",
            "detail": f"{stufe}/run.json is missing or unreadable; the run's own "
                      "provenance (images, digests, tag expression) is unavailable",
            "where": f"{stufe}/run.json",
        })

    # Heap exhaustion in the scenario JVM. Read from the archived container log
    # rather than inferred from the case count, because the signature is
    # unambiguous and the consequence is not visible in the counts at all: the run
    # keeps executing scenarios, just ~25x slower, so an affected level reports a
    # plausible-looking partial result if it was stopped, or nothing at all if it
    # ran into TESTSUITE_TIMEOUT. Either way the shortfall is a harness fault and
    # must not be read as IRIS failing to answer.
    run_log = level_root / "run.log"
    if run_log.is_file():
        try:
            log_text = run_log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            log_text = ""
        if "OutOfMemoryError" in log_text:
            heap = ((run_meta.get("testTool") or {}).get("scenarioHeapMax")
                    or "unset (the JVM default, a quarter of host RAM)")
            anomalies.append({
                "kind": "scenarioJvmOom",
                "detail": (
                    "the JVM running the scenarios threw OutOfMemoryError "
                    f"(scenario heap: {heap}). Tiger's RbelLogger retains a parsed tree "
                    "of every HTTP message of the run; once the heap fills, its parser "
                    "threads die and each subsequent scenario blocks for the full "
                    "100-second Rbel parse timeout. Any shortfall in this level's case "
                    "count is therefore a harness fault, NOT the SUT failing to "
                    "respond, and the cases that did run after the first "
                    "OutOfMemoryError may carry timeout-induced failures of their own. "
                    "Raise TESTSUITE_HEAP_MAX and run the level again."),
                "where": f"{stufe}/run.log",
            })

    if not unresolved.is_dir():
        anomalies.append({
            "kind": "missingTestReport",
            "detail": (f"{stufe}/official/extracted/test-report/unresolvedReports/ does "
                       "not exist - test-report.zip was not produced or not extracted. "
                       "There is no conformance evidence for this level."),
            "where": f"{stufe}/official/",
        })
        outcomes, other_json, broken = [], [], []
    else:
        outcomes, other_json, broken = load_outcomes(unresolved)

    for fname in other_json:
        anomalies.append({
            "kind": "nonOutcomeJson",
            "detail": f"unresolvedReports/{fname} is not a Serenity TestOutcome and "
                      "was not counted",
            "where": f"{stufe}/official/extracted/test-report/unresolvedReports/{fname}",
        })
    for detail in broken:
        anomalies.append({
            "kind": "unreadableJson",
            "detail": f"unresolvedReports/{detail}",
            "where": f"{stufe}/official/extracted/test-report/unresolvedReports/",
        })

    html_dir = serenity if serenity.is_dir() else None
    if html_dir is None:
        anomalies.append({
            "kind": "missingDebugReport",
            "detail": ("debug-report.zip was not produced or not extracted, so no case "
                       "can be cited to a browsable Serenity page and the gap analysis "
                       "has no request/response bodies to quote"),
            "where": f"{stufe}/official/",
        })

    reqs_by_rel = {f["rel"]: f for f in reqs["features"] if f["level"] == level}
    # Every level's features, so an outcome from the wrong level is diagnosed as
    # such rather than as an unknown feature.
    reqs_all = {f["rel"]: f for f in reqs["features"]}

    cases: list[dict] = []
    for name, outcome in outcomes:
        got, found = cases_from_outcome(name, outcome, index, reqs_by_rel, reqs_all,
                                        html_dir, stufe, level)
        cases.extend(got)
        anomalies.extend(found)

    # --- totals ---------------------------------------------------------------
    by_result = {r: 0 for r in RESULT_ORDER}
    for c in cases:
        by_result[c["result"]] = by_result.get(c["result"], 0) + 1
    asserting = [c for c in cases if not c["isPrecondition"]]
    totals = {
        "cases": len(cases),
        "asserting": len(asserting),
        "precondition": len(cases) - len(asserting),
        "passed": sum(1 for c in cases if c["verdict"] == "pass"),
        "failed": sum(1 for c in cases if c["verdict"] == "fail"),
        "other": sum(1 for c in cases if c["verdict"] == "other"),
        "assertingPassed": sum(1 for c in asserting if c["verdict"] == "pass"),
        "assertingFailed": sum(1 for c in asserting if c["verdict"] == "fail"),
        "assertingOther": sum(1 for c in asserting if c["verdict"] == "other"),
        "byResult": {k: v for k, v in by_result.items() if v},
    }

    lvl = reqs["levels"][level]
    expected = {
        "casesExecuted": lvl["casesExecuted"],
        "casesAssertive": lvl["casesAssertive"],
        "features": lvl["features"],
        "featuresInScope": lvl["featuresInScope"],
        "tagExpression": reqs["tagExpressions"].get(level),
    }

    # --- independent cross-check ---------------------------------------------
    # serenity-summary.json is generated by Serenity's own json-summary reporter
    # from the same outcomes, so agreement proves this generator counted them the
    # way Serenity does. It is in debug-report.zip only (test-report.zip carries
    # the .html summary, not the .json), which is why its absence is a note
    # rather than a failure.
    summary = read_json(serenity / "serenity-summary.json") if html_dir else None
    summary_counts = None
    # Steps discarded as belonging to another feature file of the same basename.
    # Serenity's own summary counts them, because the misattribution is Serenity's;
    # the difference between the two totals is therefore expected, and expected to
    # be exactly this number.
    foreign_steps = sum(1 for a in anomalies if a["kind"] == "foreignExampleStep")
    if isinstance(summary, dict):
        summary_counts = ((summary.get("results") or {}).get("counts")) or None
        reported = summary_counts.get("total") if summary_counts else None
        if reported is not None and reported != totals["cases"] + foreign_steps:
            anomalies.append({
                "kind": "totalsDisagree",
                "detail": (f"serenity-summary.json reports {reported} test cases, this "
                           f"analysis counted {totals['cases']} and discarded "
                           f"{foreign_steps} step(s) as belonging to another feature "
                           f"file, which does not add up. The official report is "
                           "authoritative; do not use these figures until the difference "
                           "is explained."),
                "where": f"{stufe}/official/extracted/debug-report/serenity/serenity-summary.json",
            })
        elif reported is not None and foreign_steps:
            anomalies.append({
                "kind": "totalsReconciled",
                "detail": (f"serenity-summary.json reports {reported} test cases and this "
                           f"analysis counts {totals['cases']}. The difference is exactly "
                           f"the {foreign_steps} step(s) recorded in this same table as "
                           "foreignExampleStep: Serenity counts them because the "
                           f"misattribution is its own, and {totals['cases']} is the "
                           "figure that matches the pinned suite's specified scope."),
                "where": f"{stufe}/official/extracted/debug-report/serenity/serenity-summary.json",
            })
    elif html_dir is not None:
        anomalies.append({
            "kind": "missingSummaryJson",
            "detail": "serenity-summary.json is absent from the debug archive, so the "
                      "case count has no independent cross-check",
            "where": f"{stufe}/official/extracted/debug-report/serenity/",
        })

    if totals["cases"] and totals["cases"] != expected["casesExecuted"]:
        anomalies.append({
            "kind": "scopeMismatch",
            "detail": (f"the report contains {totals['cases']} test case(s); the pinned "
                       f"suite's {expected['tagExpression']} selects "
                       f"{expected['casesExecuted']}. A narrowed tag expression, a "
                       "different suite version, or an aborted run all produce this."),
            "where": f"{stufe}/run.json",
        })

    # Features in scope that produced no outcome at all. The most consequential
    # anomaly this generator can find: a silently unexecuted feature reads as a
    # clean sheet rather than as missing evidence.
    seen_features = {c["feature"] for c in cases}
    not_executed = sorted(rel for rel, f in reqs_by_rel.items()
                          if f["inScope"] and rel not in seen_features)
    if not_executed:
        # One anomaly for the set, not one per feature: an aborted run leaves
        # dozens unexecuted and a table of 70 near-identical rows buries the other
        # anomalies. The complete list is in results.json under levels.<L>.
        shown = ", ".join(not_executed[:6])
        more = (f" and {len(not_executed) - 6} more (complete list in "
                f"analysis/results.json at levels.{level}.notExecuted)"
                if len(not_executed) > 6 else "")
        anomalies.append({
            "kind": "notExecuted",
            "detail": (f"{len(not_executed)} feature(s) in scope for "
                       f"{expected['tagExpression']} produced no outcome at all: "
                       f"{shown}{more}"),
            "where": f"{stufe}/official/extracted/test-report/unresolvedReports/",
        })

    # --- per module -----------------------------------------------------------
    modules: dict[str, dict] = {}
    for c in cases:
        m = modules.setdefault(c["module"], {
            "cases": 0, "asserting": 0, "precondition": 0,
            "passed": 0, "failed": 0, "other": 0,
        })
        m["cases"] += 1
        m["precondition" if c["isPrecondition"] else "asserting"] += 1
        m[{"pass": "passed", "fail": "failed", "other": "other"}[c["verdict"]]] += 1
    for mod, exp in (lvl.get("modules") or {}).items():
        m = modules.setdefault(mod, {"cases": 0, "asserting": 0, "precondition": 0,
                                     "passed": 0, "failed": 0, "other": 0})
        m["expectedCases"] = exp["casesExecuted"]
        m["expectedAsserting"] = exp["casesAssertive"]

    # SHA256SUMS as 60-run-tests.sh wrote it, next to the archives: the third
    # independent record of the same digests. Parsed once here so each archive can
    # be compared against both it and run.json.
    sums: dict[str, str] = {}
    sums_path = level_root / "official" / "SHA256SUMS"
    if sums_path.is_file():
        for line in sums_path.read_text(encoding="utf-8").splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2:
                sums[parts[1].strip()] = parts[0].strip()

    official = {}
    for kind, key in (("test-report", "testReport"), ("debug-report", "debugReport")):
        zips = sorted((level_root / "official").glob(f"*{kind}*.zip"))
        if not zips:
            official[key] = None
            continue
        recorded = ((run_meta.get("artefacts") or {}).get("sha256") or {}).get(zips[0].name)
        # Re-hashed here rather than quoted from run.json. The point of publishing a
        # checksum is that it was computed over the bytes being published; quoting a
        # figure recorded an hour earlier by another script would make the table a
        # claim about the past instead of a check on the present. A disagreement
        # means the archive changed after the run - reported, because it is the one
        # thing that would destroy the bundle's evidentiary value outright.
        sha = sha256_of(zips[0])
        for source, claimed in (("run.json", recorded),
                                ("official/SHA256SUMS", sums.get(zips[0].name))):
            if claimed and claimed != sha:
                anomalies.append({
                    "kind": "archiveChanged",
                    "detail": (f"{zips[0].name} hashes to {sha}, but "
                               f"{stufe}/{source} recorded {claimed} when the run "
                               "finished. The archive has been modified or replaced "
                               "since; it can no longer be treated as the report the "
                               "test suite produced."),
                    "where": f"{stufe}/official/{zips[0].name}",
                })
        if recorded is None and zips[0].name not in sums:
            anomalies.append({
                "kind": "noRecordedChecksum",
                "detail": (f"{zips[0].name} has no checksum recorded in either "
                           f"{stufe}/run.json or {stufe}/official/SHA256SUMS, so there "
                           "is nothing to compare the current digest against. The "
                           "archive cannot be shown to be unmodified since the run."),
                "where": f"{stufe}/official/",
            })
        # The extraction directory is created before unzip runs, so it exists even
        # when extraction failed on a truncated archive. Emptiness is therefore the
        # signal - and it has to be reported, because an empty directory satisfies
        # a path-exists check while substantiating nothing.
        ex_dir = level_root / "official" / "extracted" / kind
        extracted_files = (sum(1 for p in ex_dir.rglob("*") if p.is_file())
                           if ex_dir.is_dir() else 0)
        if extracted_files == 0:
            anomalies.append({
                "kind": "archiveNotExtracted",
                "detail": (f"{zips[0].name} is present ({zips[0].stat().st_size} bytes) "
                           "but nothing was extracted from it. A run interrupted while "
                           "Maven was assembling the archive leaves a truncated file; "
                           "verify it with `unzip -t`."),
                "where": f"{stufe}/official/{zips[0].name}",
            })
        official[key] = {
            "archive": f"{stufe}/official/{zips[0].name}",
            "sha256": sha,
            "sha256RecordedAtRun": recorded,
            "extractedTo": f"{stufe}/official/extracted/{kind}/" if extracted_files else None,
            "extractedFiles": extracted_files,
            "bytes": zips[0].stat().st_size,
        }

    return {
        "level": level,
        "directory": stufe,
        "run": run_meta,
        "official": official,
        "expected": expected,
        "totals": totals,
        "serenitySummaryCounts": summary_counts,
        "modules": modules,
        "notExecuted": not_executed,
        "cases": cases,
        "anomalies": dedupe_anomalies(anomalies),
    }


# -----------------------------------------------------------------------------
# Markdown rendering
# -----------------------------------------------------------------------------

def cell(value: Any, limit: int = 0) -> str:
    """One markdown table cell: no pipes, no newlines, optionally truncated."""
    if value is None:
        return "-"
    text = " ".join(str(value).split()).replace("|", "\\|")
    if limit and len(text) > limit:
        text = text[: limit - 1] + "…"
    return text or "-"


def pct(part: int, whole: int) -> str:
    return f"{100.0 * part / whole:.1f}%" if whole else "-"


def cite(case: dict) -> str:
    """The feature-file citation for one case, at the finest line available."""
    line = None
    if case.get("example") and case["example"].get("line"):
        line = case["example"]["line"]
    elif case.get("featureLine"):
        line = case["featureLine"]
    return f"{case['feature']}:{line}" if line else case["feature"]


def render(doc: dict, run_root: Path) -> str:
    out: list[str] = []
    w = out.append
    levels = [lv for lv in (doc["levels"].get(l) for l in LEVELS) if lv]

    w("# ISiK conformance results")
    w("")
    w(f"Run `{doc['runId']}` · generated {doc['generatedAt']} by "
      f"`{doc['generator']}`")
    w("")
    w("## What this document is")
    w("")
    w("**Derived, not authoritative.** The authoritative record of this test round is")
    w("the report the Gematik ISiK test suite wrote, archived unmodified under")
    w("`<stufe>/official/`. This document is a machine-generated index into it: every")
    w("verdict below carries the path of the file inside that archive which")
    w("substantiates it, so any statement here can be checked against the official")
    w("report without trusting the generator. Where the two disagree, the official")
    w("report is correct and this document is wrong.")
    w("")
    w("All paths are relative to this run directory")
    w(f"(`reports/{doc['runId']}/`). See `README.md` there for the directory layout")
    w("and the `analysis/` files' relationship to `<stufe>/official/`.")
    w("")
    w("`test-report.zip` is the artefact the *Bestätigungsverfahren* is based on.")
    w("`debug-report.zip` is not submitted; it carries the browsable Serenity report")
    w("and the request/response bodies, and is what the citations' `.html` targets and")
    w("the developer gap analysis read.")
    w("")

    # --- run identity ---------------------------------------------------------
    w("## 1. What was run")
    w("")
    if not levels:
        w("No level directory was found in this run. Nothing was archived, so there is")
        w("nothing to report. Run `make test` first.")
        w("")
        return "\n".join(out) + "\n"

    w("| | " + " | ".join(lv["level"] for lv in levels) + " |")
    w("|---|" + "---|" * len(levels))

    def row(label: str, fn) -> None:
        w(f"| {label} | " + " | ".join(cell(fn(lv)) for lv in levels) + " |")

    row("Specification level", lambda lv: f"ISiK Stufe {lv['level'][-1]}")
    row("Maven profile", lambda lv: lv["run"].get("mavenProfile"))
    row("Tag expression", lambda lv: lv["run"].get("testsToRun"))
    row("Official scope", lambda lv: "yes" if lv["run"].get("conformanceScope") else
        "**NO - debugging run, not a conformance result**")
    row("Started (UTC)", lambda lv: lv["run"].get("startedAt"))
    row("Duration", lambda lv: (f"{lv['run'].get('durationSeconds', 0) // 60}m "
                                f"{lv['run'].get('durationSeconds', 0) % 60}s")
        if lv["run"].get("durationSeconds") is not None else None)
    row("Timed out", lambda lv: "yes" if lv["run"].get("timedOut") else "no")
    row("SUT endpoint", lambda lv: (lv["run"].get("sut") or {}).get("baseUrl"))
    row("SUT image", lambda lv: (lv["run"].get("sut") or {}).get("image"))
    row("SUT image digest", lambda lv: (lv["run"].get("sut") or {}).get("imageDigest"))
    row("IRIS build", lambda lv: (lv["run"].get("sut") or {}).get("irisBuild"))
    row("IRIS namespace", lambda lv: (lv["run"].get("sut") or {}).get("namespace"))
    # How each version was arrived at, per axis. "pinned" means the round can be
    # re-created from .env; "resolved-latest" means the tag has probably moved since
    # and the digest above is the exact reference. The two axes are governed
    # separately on purpose - the SUT build may float, the test definition may not -
    # so a reader who only sees "Deploy mode: latest" would otherwise be entitled to
    # conclude that the test tool floated too, which it did not.
    row("SUT version source", lambda lv: (lv["run"].get("sut") or {}).get("versionSource"))
    row("Test suite image", lambda lv: (lv["run"].get("testTool") or {}).get("image"))
    row("Test suite digest", lambda lv: (lv["run"].get("testTool") or {}).get("imageDigest"))
    row("Test suite version source",
        lambda lv: (lv["run"].get("testTool") or {}).get("versionSource"))
    # Stated even when it equals the tested version, because "the newest release at
    # the time was the one tested" is itself a fact about the round, and its absence
    # would otherwise be indistinguishable from nobody having checked.
    row("Newest test suite release available",
        lambda lv: (lv["run"].get("testTool") or {}).get("latestAvailable"))
    row("Deploy mode", lambda lv: lv["run"].get("mode"))
    row("Package source", lambda lv: lv["run"].get("packageSource"))
    # -Xmx for the JVM that ran the scenarios. Stated because it decides whether a
    # Stufe 5 level can finish at all, and a level that timed out with a low value
    # here is explained by it rather than by the SUT.
    row("Scenario heap", lambda lv: (lv["run"].get("testTool") or {}).get("scenarioHeapMax"))
    # Present only when this level was run with a command-line override, which is
    # how a level is re-run with more heap after an out-of-memory stall. Its absence
    # means every setting came from the frozen configuration record.
    row("Command-line overrides", lambda lv: lv["run"].get("overrides"))
    row("Container exit code", lambda lv: lv["run"].get("containerExitCode"))
    row("Outcome records in the archive",
        lambda lv: (lv["run"].get("artefacts") or {}).get("testOutcomeRecords"))
    w("")

    # A round covering one level only. Legitimate in itself - the Bestätigungsverfahren
    # confirms each level separately, so a per-level round is a valid unit of work, and
    # the partialRound anomaly says exactly that. What is worth saying HERE, next to the
    # table, is the mechanical cause the anomaly list does not name: `make test-stufe<N>`
    # without RUN_ID mints a fresh round id, so two levels that were both run land in
    # two directories and each round then looks like a deliberate single-level round.
    # The reader cannot tell those two situations apart from this document alone.
    if len(levels) < len(LEVELS):
        missing = [l for l in LEVELS if l not in {lv["level"] for lv in levels}]
        w(f"> **This round covers {levels[0]['level']} only.** No archive was found for "
          + ", ".join(f"`{m}`" for m in missing)
          + ", so nothing in this document states anything about that level - neither"
          " pass nor fail. That is legitimate on its own terms, because the"
          " *Bestätigungsverfahren* confirms each specification level separately; this"
          " project's scope covers both, so the other level needs a round of its own.")
        w(">")
        w("> **If both levels were in fact run, they were split across two rounds by"
          " accident.** `make test-stufe<N>` mints a fresh run id unless `RUN_ID` names"
          " an existing round directory. Check `reports/` for a sibling directory from"
          " the same session; if one holds the other level, the fix is to move that"
          " level's directory into whichever round should hold both and re-run"
          " `make report RUN_ID=<id>` - the report targets only read the archives and"
          " re-run no test.")
        w("")
    w("Source: `<stufe>/run.json`, written by `scripts/60-run-tests.sh` at the end of")
    w("each level's run. The container exit code is recorded but says nothing about the")
    w("result: the suite's entrypoint ends in `|| true`, so it is 0 whether every")
    w("scenario passed or every scenario failed.")
    w("")
    w("The last row is the count of `unresolvedReports/*.json` entries the runner read")
    w("out of `test-report.zip`'s listing before extracting anything, and it is the")
    w("signal the exit code cannot give: the archive is written and moved whether or not")
    w("Maven ran a single scenario, so a zero there marks a level that produced an")
    w("archive without producing a run. `60-run-tests.sh` fails such a level as a harness")
    w("error, which is why it should never be 0 in a document that exists at all. It is")
    w("an independent number, taken from the zip listing rather than from the extracted")
    w("tree section 2 counts.")
    w("")
    if any((lv["run"].get("artefacts") or {}).get("testOutcomeRecords") is None
           for lv in levels):
        # `-` in that row is not a zero and must not be read as one. A zero would
        # have stopped the level as a harness error; `-` means the key is absent,
        # which only happens for a run.json written before the count existed.
        w("> A `-` in that row is *not* a zero: it means `run.json` carries no")
        w("> `artefacts.testOutcomeRecords` key, so the run predates the check. Section 2")
        w("> counts the extracted outcomes for the same level and is unaffected.")
        w("")

    if any(not lv["run"].get("conformanceScope", True) for lv in levels):
        w("> **This round is not a conformance result.** At least one level ran with a")
        w("> tag expression other than the official certification scope. Its report must")
        w("> not be submitted for the *Bestätigungsverfahren*.")
        w("")

    # --- credentials ----------------------------------------------------------
    # Stated in full, deliberately. The rig is a lab instance brought up for one
    # round and destroyed once the results are verified; access to it is controlled
    # physically and at the network perimeter rather than by these passwords, and a
    # reader has to be able to re-issue a cited request to check a verdict.
    w("### Access credentials")
    w("")
    w("| | " + " | ".join(lv["level"] for lv in levels) + " |")
    w("|---|" + "---|" * len(levels))
    creds = lambda lv: lv["run"].get("credentials") or {}  # noqa: E731
    api = lambda lv: creds(lv).get("api") or {}            # noqa: E731
    mgmt = lambda lv: creds(lv).get("management") or {}    # noqa: E731
    row("FHIR API user", lambda lv: api(lv).get("user"))
    row("FHIR API password", lambda lv: api(lv).get("password"))
    row("Authentication scheme", lambda lv: api(lv).get("scheme"))
    row("Management Portal user", lambda lv: mgmt(lv).get("user"))
    row("Management Portal password", lambda lv: mgmt(lv).get("password"))
    w("")
    w("Source: `<stufe>/run.json`, key `credentials`.")
    w("")
    if any(creds(lv) for lv in levels):
        w("These are stated in full on purpose. The environment is a lab instance brought")
        w("up for this one round of testing and destroyed once the results have been")
        w("verified; access to it is controlled physically and at the network perimeter,")
        w("not by these passwords, which are kept in place only so that the system under")
        w("test remains a faithful stand-in for a production deployment.")
        w("")
        w("They are recorded here so that any verdict in sections 4 and 5 can be")
        w("reproduced by hand: the requests quoted in the gap analysis carry this same")
        w("credential in their `Authorization` header, and section 8 shows the `curl`")
        w("form. Each deployment generates its own passwords, so nothing here grants")
        w("access to any other instance - including a rebuilt one.")
        w("")
    else:
        w("> **No credentials recorded.** This round's `run.json` carries no")
        w("> `credentials` key, which means it was produced by a version of")
        w("> `scripts/60-run-tests.sh` from before the credentials were documented.")
        w("> The values are recoverable from `provenance/env.resolved.txt`.")
        w("")

    # --- corpus ---------------------------------------------------------------
    w("### Test corpus the run started from")
    w("")
    w("| | " + " | ".join(lv["level"] for lv in levels) + " |")
    w("|---|" + "---|" * len(levels))
    seed = lambda lv: lv["run"].get("seed") or {}  # noqa: E731
    row("Resources seeded", lambda lv: seed(lv).get("resources"))
    row("Written", lambda lv: seed(lv).get("written"))
    row("Read back", lambda lv: seed(lv).get("readBack"))
    row("`$validate` clean", lambda lv: seed(lv).get("validated"))
    row("`$validate` errors", lambda lv: seed(lv).get("validationErrors"))
    row("Not validated", lambda lv: seed(lv).get("validationSkipped"))
    row("Server (self-reported)", lambda lv: seed(lv).get("server"))
    w("")
    w("Source: `provenance/seed/<Level>.json` and `provenance/seed/SEED.txt`, written by")
    w("`scripts/50-seed.sh` before the run. The ISiK test suite seeds nothing, so every")
    w("scenario reads resources this project created; a resource that was not written,")
    w("or that failed `$validate`, would produce a failing scenario that is *our*")
    w("defect and not the SUT's.")
    if any(seed(lv).get("validationErrors") for lv in levels):
        w("")
        w("> **A seeded resource failed `$validate`.** Scenarios reading it may fail for")
        w("> reasons that belong to the test data rather than to IRIS. See")
        w("> `provenance/seed/SEED.txt`, which names the resources and the issues.")
    w("")

    # --- scope reconciliation -------------------------------------------------
    w("## 2. Scope and reconciliation")
    w("")
    w("Two counts are in play and they are not interchangeable:")
    w("")
    w("* **Executed cases** - everything Cucumber ran, including each feature's")
    w("  `@vorbedingung` / `@Precondition` block. Those blocks are real scenarios and")
    w("  they do execute, but their steps only record the required test data as prose;")
    w("  they assert nothing against the SUT.")
    w("* **Asserting cases** - the executed cases that actually test the SUT. This is")
    w("  the conformance denominator.")
    w("")
    w("A Scenario Outline counts once per `Examples` row, which is how Cucumber and")
    w("Serenity count (`TestOutcome.getTestCount()` returns `testSteps.size()` for a")
    w("data-driven outcome), and therefore how this table counts.")
    w("")
    w("| Level | Executed (expected) | Executed (report) | Asserting (expected) | "
      "Asserting (report) | Precondition blocks | Independent count |")
    w("|---|---:|---:|---:|---:|---:|---:|")
    for lv in levels:
        s = lv["serenitySummaryCounts"] or {}
        w(f"| {lv['level']} | {lv['expected']['casesExecuted']} | {lv['totals']['cases']} "
          f"| {lv['expected']['casesAssertive']} | {lv['totals']['asserting']} "
          f"| {lv['totals']['precondition']} | {cell(s.get('total'))} |")
    if len(levels) > 1:
        w(f"| **Total** | {sum(lv['expected']['casesExecuted'] for lv in levels)} "
          f"| {sum(lv['totals']['cases'] for lv in levels)} "
          f"| {sum(lv['expected']['casesAssertive'] for lv in levels)} "
          f"| {sum(lv['totals']['asserting'] for lv in levels)} "
          f"| {sum(lv['totals']['precondition'] for lv in levels)} | |")
    w("")
    w("*Expected* is derived from the pinned suite's own feature files")
    w("(`vendor/requirements.json`, extracted from the test suite image by")
    w("`scripts/25-extract-requirements.sh`) evaluated against the tag expression each")
    w("level ran with. *Report* is counted from the outcomes in")
    w("`<stufe>/official/extracted/test-report/unresolvedReports/`. *Independent count*")
    w("is `results.counts.total` from `serenity-summary.json` in the debug archive,")
    w("which Serenity's own summary reporter produced from the same outcomes - it exists")
    w("to catch a counting error in this generator.")
    w("")
    if any(a["kind"] == "totalsReconciled" for lv in levels for a in lv["anomalies"]):
        w("*Independent count* is **higher than *Report* by design** where a level")
        w("contains two feature files of the same basename. Serenity 5.3.10 then counts")
        w("some steps twice, once against each file, which its own summary reproduces;")
        w("*Report* counts each step against the feature whose `Examples:` row it")
        w("actually ran. The difference is itemised, per outcome, in section 7, and it")
        w("is *Report* that agrees with *Expected*.")
        w("")

    # --- results ------------------------------------------------------------
    w("## 3. Results")
    w("")
    w("Verdicts group Serenity's `TestResult` values: **passed** is `SUCCESS`;")
    w("**failed** is `FAILURE`, `ERROR`, `COMPROMISED` or `UNSUCCESSFUL`; **other** is")
    w("`PENDING`, `SKIPPED`, `IGNORED`, `ABORTED` or `UNDEFINED`. The raw value is")
    w("carried per case in `analysis/results.json` and in")
    w("`analysis/scenario-verdicts.csv`.")
    w("")
    w("### Asserting cases only (the conformance figure)")
    w("")
    w("| Level | Asserting | Passed | Failed | Other | Pass rate |")
    w("|---|---:|---:|---:|---:|---:|")
    for lv in levels:
        t = lv["totals"]
        w(f"| {lv['level']} | {t['asserting']} | {t['assertingPassed']} | "
          f"{t['assertingFailed']} | {t['assertingOther']} | "
          f"{pct(t['assertingPassed'], t['asserting'])} |")
    if len(levels) > 1:
        a = sum(lv["totals"]["asserting"] for lv in levels)
        p = sum(lv["totals"]["assertingPassed"] for lv in levels)
        f_ = sum(lv["totals"]["assertingFailed"] for lv in levels)
        o = sum(lv["totals"]["assertingOther"] for lv in levels)
        w(f"| **Total** | {a} | {p} | {f_} | {o} | {pct(p, a)} |")
    w("")
    w("### All executed cases, by raw result")
    w("")
    w("| Level | " + " | ".join(RESULT_ORDER) + " |")
    w("|---|" + "---:|" * len(RESULT_ORDER))
    for lv in levels:
        br = lv["totals"]["byResult"]
        w(f"| {lv['level']} | " + " | ".join(str(br.get(r, 0)) for r in RESULT_ORDER) + " |")
    w("")

    for lv in levels:
        w(f"### {lv['level']} by module")
        w("")
        w("| Module | Cases (expected) | Cases | Asserting | Passed | Failed | Other | "
          "Pass rate |")
        w("|---|---:|---:|---:|---:|---:|---:|---:|")
        for mod in sorted(lv["modules"]):
            m = lv["modules"][mod]
            w(f"| {mod} | {cell(m.get('expectedCases'))} | {m['cases']} | "
              f"{m['asserting']} | {m['passed']} | {m['failed']} | {m['other']} | "
              f"{pct(m['passed'], m['cases'])} |")
        w("")

    # --- failures in detail ---------------------------------------------------
    w("## 4. Failures, with citations")
    w("")
    failures = [c for lv in levels for c in lv["cases"] if c["verdict"] == "fail"]
    total_cases = sum(lv["totals"]["cases"] for lv in levels)
    if total_cases == 0:
        # Distinct from "nothing failed", and the distinction matters: a report with
        # no test cases in it is missing evidence, not a clean sheet.
        w("**No test case was found in the report at all.** This is not a result: there")
        w("is nothing to pass or fail. Either the archive is absent (see section 7) or")
        w("the run did not reach the point of executing a scenario - a Tiger startup")
        w("failure against an unreachable SUT is the usual cause, and `<stufe>/run.log`")
        w("records it.")
        w("")
    elif not failures:
        w("No case failed. Note that this is the *unexpected* outcome for this project:")
        w("IRIS for Health is known not to be fully ISiK compliant, so a clean sheet is")
        w("more likely to mean the run did not test what it was supposed to than that")
        w("the SUT is conformant. Check the reconciliation in section 2 and the")
        w("anomalies in section 7 before reporting it.")
        w("")
    else:
        w(f"{len(failures)} failing case(s). Each entry cites, in order: the feature file")
        w("and line the requirement is written at, the Serenity outcome inside the")
        w("submitted archive, and the browsable page in the debug archive where the full")
        w("request and response can be read.")
        w("")
        w("This section states *what* failed. It deliberately does not say *why*: the")
        w("attribution of a failure to a FHIR feature, a supplying ISiK package and an")
        w("IRIS subsystem is the standalone developer gap analysis's job, at")
        w("`GAP-ANALYSIS.md` in this run directory.")
        w("")
        by_key: dict[tuple[str, str], list[dict]] = {}
        for c in failures:
            by_key.setdefault((c["level"], c["module"]), []).append(c)
        for (level, module), group in sorted(by_key.items()):
            w(f"### {level} · {module} · {len(group)} failing case(s)")
            w("")
            for c in sorted(group, key=lambda x: (x["feature"], x["scenario"],
                                                  (x.get("example") or {}).get("index") or 0)):
                f = c["failure"] or {}
                title = c["scenario"]
                if c.get("example"):
                    title += f" · example {c['example']['index']}"
                w(f"**{cell(title)}**  ")
                w(f"Requirement: `{cite(c)}`  ")
                if c.get("example") and c["example"].get("data"):
                    w(f"Example data: `{cell(json.dumps(c['example']['data'], ensure_ascii=False), 200)}`  ")
                w(f"Result: `{c['result']}`  ")
                if f.get("gherkinStep"):
                    w(f"Gherkin step: {cell(f['gherkinStep'], 400)}  ")
                if f.get("step"):
                    line = f" (`{c['feature']}:{f['stepLine']}`)" if f.get("stepLine") else ""
                    w(f"Failing step{line}: {cell(f['step'], 400)}  ")
                if f.get("http"):
                    h = f["http"]
                    w(f"HTTP: `{cell(h.get('method'))} {cell(h.get('path'), 160)}` "
                      f"-> `{cell(h.get('statusCode'))}`  ")
                if f.get("errorType"):
                    w(f"Error type: `{cell(f['errorType'])}`  ")
                if f.get("message"):
                    w(f"Message: {cell(f['message'], MSG_LIMIT)}  ")
                w(f"Official report: `{c['report']['outcomeJson']}`  ")
                if c["report"]["outcomeHtml"]:
                    w(f"Browsable: `{c['report']['outcomeHtml']}`  ")
                w("")

    # --- full index -----------------------------------------------------------
    w("## 5. Every executed case")
    w("")
    w("The complete verdict list, so that a reader can confirm the totals above by")
    w("counting rather than by trusting them. `P` marks a `@vorbedingung` /")
    w("`@Precondition` block, which asserts nothing. The same rows are in")
    w("`analysis/scenario-verdicts.csv`, which additionally carries the Serenity")
    w("outcome path for each row and sorts in a spreadsheet.")
    w("")
    for lv in levels:
        w(f"### {lv['level']}")
        w("")
        for mod in sorted(lv["modules"]):
            rows = [c for c in lv["cases"] if c["module"] == mod]
            if not rows:
                continue
            w(f"#### {mod}")
            w("")
            w("| Verdict | Result | P | Requirement (feature:line) | Scenario | "
              "Official report |")
            w("|---|---|---|---|---|---|")
            for c in sorted(rows, key=lambda x: (x["feature"], x["scenario"],
                                                 (x.get("example") or {}).get("index") or 0)):
                name = c["scenario"]
                if c.get("example"):
                    name += f" [{c['example']['index']}]"
                mark = {"pass": "PASS", "fail": "**FAIL**", "other": "-"}[c["verdict"]]
                w(f"| {mark} | {c['result']} | {'P' if c['isPrecondition'] else ''} | "
                  f"`{cite(c)}` | {cell(name, 90)} | "
                  f"`{c['report']['outcomeJson'].split('/')[-1]}` |")
            w("")
        w(f"Outcome files live in `{lv['directory']}/official/extracted/test-report/"
          "unresolvedReports/`. Serenity names them from a sha256 of the outcome's")
        w("qualified id, so the filename is opaque by design and carries no meaning.")
        w("")

    # --- provenance -----------------------------------------------------------
    w("## 6. Provenance and checksums")
    w("")
    w("### Official report archives")
    w("")
    w("| Level | Archive | Bytes | sha256 | Extracted to | Files |")
    w("|---|---|---:|---|---|---:|")
    for lv in levels:
        for key in ("testReport", "debugReport"):
            o = lv["official"].get(key)
            if not o:
                w(f"| {lv['level']} | *{key} not produced* | - | - | - | - |")
                continue
            dest = (f"`{o['extractedTo']}`" if o["extractedTo"]
                    else "**not extracted — see section 7**")
            w(f"| {lv['level']} | `{o['archive']}` | {o['bytes']} | "
              f"`{cell(o['sha256'])}` | {dest} | {o.get('extractedFiles', 0)} |")
    w("")
    w("These checksums were computed over the archives as they are now, when this")
    w("document was generated. `<stufe>/official/SHA256SUMS` and `<stufe>/run.json`")
    w("carry the checksums computed when the run finished; the three are compared")
    w("automatically and any disagreement is reported in section 7. Recompute them")
    w("yourself with `sha256sum -c SHA256SUMS` inside each `<stufe>/official/`")
    w("directory.")
    w("")
    w("The archives are byte-for-byte as the test suite wrote them:")
    w("`scripts/60-run-tests.sh` lifts them out of the container with `docker cp` and")
    w("nothing in this repository opens them for writing.")
    w("")
    if doc.get("packages"):
        w("### FHIR packages loaded into the unit under test")
        w("")
        w("| Package | Version | sha256 (tarball) |")
        w("|---|---|---|")
        for p in doc["packages"]:
            w(f"| `{p['name']}` | {p['version']} | `{p['sha256']}` |")
        w("")
        w("Verbatim from `provenance/packages/MANIFEST.txt`. The package *list* is not")
        w("chosen by this project: it comes from the test suite image's own")
        w("`refv-plugins/*/config.yaml`, so the profiles the SUT validates against are")
        w("the ones the Gematik reference validator validates against. See")
        w("`provenance/package-plan.json` for the per-endpoint assignment and")
        w("`provenance/iris-provenance.json` for what the SUT reports it actually")
        w("loaded.")
        w("")

    plan_dev = doc.get("packagePlanDeviations") or {}
    if plan_dev.get("dependencyRewrites") or plan_dev.get("promoted"):
        # Reported here rather than only in the plan file because it is the one
        # place where this project modified something inside the FHIR package tree
        # the SUT was loaded from. A reader auditing the round is entitled to see
        # that stated next to the checksums, not buried in a generated JSON file.
        w("### Package-tree modifications the IRIS loader required")
        w("")
        w("`HS.FHIRMeta.Load.NpmLoader` resolves every dependency a package declares")
        w("by **exact package id**. It performs no semantic-version range resolution,")
        w("and a single declaration it cannot resolve aborts the entire import with")
        w("`ERROR #5809: Object to Load not found` — after the namespace has been")
        w("created. Loading the package set the reference validator uses therefore")
        w("required the changes below.")
        w("")
        w("**None of these is an ISiK conformance finding against the unit under")
        w("test.** Each row names who owns it, and the owners differ: a published")
        w("package using a wildcard the FHIR NPM specification permits, an upstream")
        w("implementation guide naming a version that was never published, an ISiK")
        w("validator configuration that pins a package only for snapshot generation,")
        w("and one deliberate exclusion by this project.")
        w("")
        if plan_dev.get("dependencyRewrites"):
            w("| Level | Package whose `package.json` was changed | Declared dependency"
              " | Changed to | Kind | Owner of the finding |")
            w("|---|---|---|---|---|---|")
            for r in plan_dev["dependencyRewrites"]:
                to = f"`{cell(r['to'])}`" if r.get("to") else "*edge removed*"
                w(f"| {cell(r['level'])} | `{cell(r['package'])}` | "
                  f"`{cell(r['from'])}` | {to} | `{cell(r['kind'])}` | "
                  f"{REWRITE_OWNERS.get(r['kind'], '—')} |")
            w("")
            w("The reason recorded in the plan for each kind:")
            w("")
            for kind in sorted({r["kind"] for r in plan_dev["dependencyRewrites"]}):
                reason = next(r.get("reason", "") for r in plan_dev["dependencyRewrites"]
                              if r["kind"] == kind)
                w(f"- **`{kind}`** — {cell(reason)}")
            w("")
            w("What was modified, precisely: only the *unpacked* `package.json` files")
            w("under `vendor/packages/`, which are what IRIS reads at import time. The")
            w("`.tgz` payloads were not touched, so **every sha256 in the table above")
            w("still attests the package bytes exactly as published** and can be")
            w("checked against the registry. The full record is")
            w("`provenance/package-plan.json` → `dependencyRewrites`.")
            w("")
        if plan_dev.get("promoted"):
            w("One further consequence of the same exact-id resolution: a package the")
            w("reference validator lists only under `snapshotDependencies` — it uses it")
            w("to build snapshots and ships no payload for it — is *declared* by a")
            w("package the endpoint does load, so IRIS must be able to resolve it. It")
            w("was therefore loaded into the SUT as well:")
            w("")
            w("| Package | Level(s) | Listed as a snapshot dependency by | Why it had to be loaded |")
            w("|---|---|---|---|")
            for p in plan_dev["promoted"]:
                w(f"| `{cell(p['id'])}` | {cell(', '.join(p.get('levels') or []))} | "
                  f"{cell(', '.join(p.get('declaredSnapshotOnlyBy') or []))} | "
                  f"{cell(p.get('reason'))} |")
            w("")
            for p in plan_dev["promoted"]:
                if p.get("deviation"):
                    w(f"- `{cell(p['id'])}`: {cell(p['deviation'])}")
            w("")
            w("These packages have no bundled payload — they are absent from the test")
            w("suite image entirely — so they are fetched from the FHIR package registry")
            w("even when `PACKAGE_SOURCE=refv` selects the offline route. The header of")
            w("`provenance/packages/MANIFEST.txt` names them as the exception, so no line")
            w("of that manifest overstates where its bytes came from.")
            w("")

    if doc.get("provenanceFiles"):
        w("### Files copied into this run directory")
        w("")
        w("| File | What it is | Written by |")
        w("|---|---|---|")
        for f in doc["provenanceFiles"]:
            w(f"| `{cell(f['path'])}` | {cell(f['what'])} | `{cell(f['by'])}` |")
        w("")

    # --- anomalies ------------------------------------------------------------
    w("## 7. Anomalies")
    w("")
    all_anom = list(doc.get("anomalies") or [])
    for lv in levels:
        all_anom.extend(lv["anomalies"])
    if not all_anom:
        w("None. Every outcome in the report joined to a known feature file and a known")
        w("scenario, the case count matches both the pinned suite's own scope and")
        w("Serenity's independent summary, and every in-scope feature produced at least")
        w("one outcome.")
        w("")
    else:
        w("Conditions that affect how the figures above should be read. These are")
        w("properties of the run or of the archive, not conformance findings.")
        w("")
        w("| Kind | Detail | Where |")
        w("|---|---|---|")
        # Grouped by kind and capped. A single cause can raise the same anomaly for
        # every outcome in the report - a run that used the wrong Maven profile
        # raises `wrongLevel` hundreds of times - and a table of hundreds of near
        # identical rows hides the other anomalies instead of reporting them. The
        # complete, uncollapsed list is in analysis/results.json.
        CAP = 4
        by_kind: dict[str, list[dict]] = {}
        for a in all_anom:
            by_kind.setdefault(a["kind"], []).append(a)
        for kind, group in by_kind.items():
            for a in group[:CAP]:
                w(f"| `{kind}` | {cell(a['detail'], 400)} | `{cell(a.get('where'))}` |")
            if len(group) > CAP:
                w(f"| `{kind}` | *… and {len(group) - CAP} more of the same kind; the "
                  f"complete list is in `analysis/results.json`* | |")
        w("")
        if len(by_kind) < len(all_anom):
            w(f"{len(all_anom)} anomaly record(s) in {len(by_kind)} kind(s).")
            w("")
        if {"archiveChanged"} & set(by_kind):
            w("> **An archive no longer matches the checksum recorded when the run")
            w("> finished.** The bundle cannot be used as evidence in this state: the")
            w("> official report is supposed to be immutable from the moment")
            w("> `scripts/60-run-tests.sh` copies it out of the container. Re-run the")
            w("> round.")
            w("")
        if {"foreignExampleTable", "foreignExampleStep"} & set(by_kind):
            w("> **`foreignExampleTable` / `foreignExampleStep` are defects of the")
            w("> reporting library, not of the system under test, and are corrected in")
            w("> the figures above rather than merely noted.** Serenity 5.3.10 attaches")
            w("> the wrong `Examples:` table to a scenario when two feature files in one")
            w("> run share a basename. Stufe 5 of ISiK 2.7.0 has 16 such basenames among")
            w("> its 124 features — `Practitioner-Search.feature` exists in both `Basis/`")
            w("> and `Medikation/` — and Stufe 3 has none, so only Stufe 5 is affected.")
            w(">")
            w("> This generator therefore attributes each executed step to an `Examples:`")
            w("> row by the parameters the step itself records in its own title, not by")
            w("> its position in the table. Taken at face value instead, the report")
            w("> claims 905 Stufe 5 cases against the 895 the pinned suite specifies, and")
            w("> one genuinely **failing** case reads as a pass, because its verdict")
            w("> would be taken from a row of a table belonging to a different scenario.")
            w(">")
            w("> Every affected outcome is listed above with its own report file, so each")
            w("> correction can be checked against the archive by hand. The official")
            w("> `test-report.zip` is unmodified: the correction is in this analysis only.")
            w("")
        blocking = {"scopeMismatch", "notExecuted", "missingTestReport",
                    "totalsDisagree", "wrongLevel", "archiveNotExtracted",
                    "scenarioJvmOom"}
        if blocking & set(by_kind):
            w("> At least one anomaly above means the report does not cover the full")
            w("> certification scope. Resolve it before treating these figures as a")
            w("> conformance result.")
            w("")

    # --- how to verify --------------------------------------------------------
    w("## 8. How to verify this document")
    w("")
    w("Every claim above is reproducible from the archives in this directory alone.")
    w("From the run directory:")
    w("")
    w("```sh")
    w("# 1. the archives are the ones the suite produced")
    w("( cd stufe5/official && sha256sum -c SHA256SUMS )")
    w("")
    w("# 2. re-extract them yourself, next to ours")
    w("unzip -d /tmp/check stufe5/official/test-report.zip")
    w("")
    w("# 3. follow any citation from section 4 or 5")
    w("python3 -m json.tool \\")
    w("  stufe5/official/extracted/test-report/unresolvedReports/<name>.json | less")
    w("")
    w("# 4. read the same outcome as a page, with request and response bodies")
    w("xdg-open stufe5/official/extracted/debug-report/serenity/<name>.html")
    w("")
    w("# 5. read the whole official report as the suite renders it")
    w("xdg-open stufe5/official/extracted/debug-report/serenity/index.html")
    w("")
    w("# 6. regenerate this document and diff it")
    w("make report")
    w("```")
    w("")
    w("To go further than the archives and re-issue a request against a rebuilt")
    w("environment, use the credentials from section 1. The endpoint requires HTTP")
    w("Basic authentication, so a request the gap analysis quotes is replayed as:")
    w("")
    w("```sh")
    w("curl -u '<FHIR API user>:<FHIR API password>' \\")
    w("  -H 'Accept: application/fhir+json' \\")
    w("  '<SUT endpoint>/Patient?_count=5'")
    w("```")
    w("")
    w("The `Authorization: Basic` header shown verbatim in the gap analysis's HTTP")
    w("exchanges is that same credential, base64-encoded. Note that the SUT endpoint in")
    w("section 1 is the address inside the isolated subnet, which is what the test suite")
    w("used; from the deployment host, substitute `127.0.0.1` and the published port")
    w("(see `provenance/CREDENTIALS.md`, which lists both).")
    w("")
    w("A scenario's requirement is stated in its own feature file, in German, inside the")
    w("`@vorbedingung` / `@Precondition` block at the top. `docs/en/generated/")
    w("REQUIREMENTS-Stufe3.md` and `REQUIREMENTS-Stufe5.md` quote every one of those")
    w("blocks verbatim and untranslated, indexed by the same `feature:line` citations")
    w("used above.")
    w("")
    return "\n".join(out) + "\n"


# -----------------------------------------------------------------------------
# CSV
# -----------------------------------------------------------------------------

CSV_COLUMNS = ("level", "module", "feature", "featureLine", "scenario", "exampleIndex",
               "exampleLine", "isPrecondition", "inScope", "result", "verdict",
               "durationMs", "failingStep", "failingStepLine", "httpMethod", "httpPath",
               "httpStatus", "errorType", "message", "outcomeJson", "outcomeHtml")


def write_csv(doc: dict, path: Path) -> int:
    n = 0
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(CSV_COLUMNS)
        for level in LEVELS:
            lv = doc["levels"].get(level)
            if not lv:
                continue
            for c in lv["cases"]:
                ex = c.get("example") or {}
                f = c.get("failure") or {}
                h = f.get("http") or {}
                writer.writerow([
                    c["level"], c["module"], c["feature"], c.get("featureLine") or "",
                    c["scenario"], ex.get("index") or "", ex.get("line") or "",
                    int(c["isPrecondition"]), int(c["inScope"]),
                    c["result"], c["verdict"], c.get("durationMs") or "",
                    " ".join((f.get("step") or "").split()),
                    f.get("stepLine") or "",
                    h.get("method") or "", h.get("path") or "", h.get("statusCode") or "",
                    f.get("errorType") or "",
                    " ".join((f.get("message") or "").split()),
                    c["report"]["outcomeJson"], c["report"]["outcomeHtml"] or "",
                ])
                n += 1
    return n


# -----------------------------------------------------------------------------

def read_manifest(path: Path) -> list[dict]:
    """The FHIR package manifest as records. Format is fixed by 20-fetch-packages.sh:
    a comment header, then `  <name> <version> <sha256>` lines."""
    packages = []
    if not path.is_file():
        return packages
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split()
        if len(parts) >= 3:
            packages.append({"name": parts[0], "version": parts[1], "sha256": parts[2]})
    return packages


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run-dir", type=Path, required=True,
                    help="reports/<run-id>/ - must already hold the extracted archives")
    ap.add_argument("--requirements", type=Path, required=True,
                    help="vendor/requirements.json")
    ap.add_argument("--features-root", type=Path, required=True,
                    help="vendor/upstream/features")
    ap.add_argument("--manifest", type=Path,
                    help="the FHIR package manifest to quote (MANIFEST.txt)")
    ap.add_argument("--package-plan", type=Path,
                    help="package-plan.json, for the package-tree modifications "
                         "the IRIS loader required (§6)")
    ap.add_argument("--provenance-index", type=Path,
                    help="JSON list of {path,what,by} records for the file table")
    ap.add_argument("--run-id", default="", help="overrides the run directory's name")
    args = ap.parse_args()

    run_root: Path = args.run_dir
    if not run_root.is_dir():
        print(f"run directory does not exist: {run_root}", file=sys.stderr)
        return 2
    if not args.requirements.is_file():
        print(f"{args.requirements} not found - run 'make packages' first", file=sys.stderr)
        return 2

    reqs = json.loads(args.requirements.read_text(encoding="utf-8"))
    try:
        index = FeatureIndex(args.features_root)
    except fp.FeatureParseError as exc:
        print(f"cannot read the feature files: {exc}", file=sys.stderr)
        return 2

    doc: dict[str, Any] = {
        "runId": args.run_id or run_root.name,
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generator": "scripts/lib/results_report.py",
        "authoritativeSource": "<stufe>/official/test-report.zip",
        "source": {
            "requirements": str(args.requirements),
            "featuresRoot": str(args.features_root),
            "suite": reqs.get("source"),
            "tagExpressions": reqs.get("tagExpressions"),
        },
        "levels": {},
        "anomalies": [],
    }

    for level in LEVELS:
        collected = collect_level(level, run_root, index, reqs)
        if collected:
            doc["levels"][level] = collected

    if not doc["levels"]:
        doc["anomalies"].append({
            "kind": "noLevels",
            "detail": "neither stufe3/ nor stufe5/ exists in this run directory; "
                      "no report was archived",
            "where": ".",
        })
    elif len(doc["levels"]) == 1:
        doc["anomalies"].append({
            "kind": "partialRound",
            "detail": (f"only {next(iter(doc['levels']))} was run. A "
                       "Bestätigungsverfahren submission covers one specification level, "
                       "so this is legitimate - but this document then describes half a "
                       "round."),
            "where": ".",
        })

    for note in index.ambiguous:
        doc["anomalies"].append({
            "kind": "ambiguousScenarioName",
            "detail": f"{note} - the line citation for it may point at the wrong one",
            "where": note.split(":")[0],
        })

    if args.manifest:
        doc["packages"] = read_manifest(args.manifest)
        if not doc["packages"]:
            doc["anomalies"].append({
                "kind": "missingPackageManifest",
                "detail": f"{args.manifest.name} is absent or empty, so the results "
                          "document cannot state which FHIR packages the SUT loaded",
                "where": "provenance/packages/MANIFEST.txt",
            })
    if args.package_plan:
        # Only the two deviation records are lifted out of the plan. The plan itself
        # is copied into the bundle whole, so this document quotes rather than
        # restates it - and an absent plan is an anomaly rather than a silent gap,
        # because these records are the only statement that the package tree the SUT
        # was loaded from was modified at all.
        if args.package_plan.is_file():
            plan = json.loads(args.package_plan.read_text(encoding="utf-8"))
            doc["packagePlanDeviations"] = {
                "source": "provenance/package-plan.json",
                "dependencyRewrites": plan.get("dependencyRewrites") or [],
                "promoted": plan.get("snapshotDependenciesPromoted") or [],
            }
        else:
            doc["anomalies"].append({
                "kind": "missingPackagePlan",
                "detail": (f"{args.package_plan.name} is absent, so this document "
                           "cannot state which dependency declarations had to be "
                           "rewritten for the IRIS loader, nor which snapshot-only "
                           "package was promoted into the SUT"),
                "where": "provenance/package-plan.json",
            })
    if args.provenance_index and args.provenance_index.is_file():
        doc["provenanceFiles"] = json.loads(
            args.provenance_index.read_text(encoding="utf-8"))

    out_dir = run_root / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "results.json").write_text(
        json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out_dir / "results.md").write_text(render(doc, run_root), encoding="utf-8")
    rows = write_csv(doc, out_dir / "scenario-verdicts.csv")

    # Summary to stderr: the shell wrapper prints it, and `make report` output is
    # the only thing an operator running a remote round sees.
    for level in LEVELS:
        lv = doc["levels"].get(level)
        if not lv:
            continue
        t = lv["totals"]
        print(f"{level}: {t['cases']} cases "
              f"({t['asserting']} asserting, {t['precondition']} precondition) "
              f"-> {t['passed']} passed, {t['failed']} failed, {t['other']} other; "
              f"expected {lv['expected']['casesExecuted']} executed",
              file=sys.stderr)
    n_anom = len(doc["anomalies"]) + sum(len(lv["anomalies"]) for lv in doc["levels"].values())
    print(f"{rows} case rows, {n_anom} anomaly/anomalies", file=sys.stderr)

    # Exit status reports whether the *analysis* is sound, never whether the SUT
    # passed. A failing conformance run is the expected outcome of this project; an
    # analysis that could not read the report, or that disagrees with the official
    # report about how many cases ran, is a harness error.
    #
    # scopeMismatch and notExecuted are deliberately NOT blocking. Both are
    # legitimate for a narrowed debugging run, which 60-run-tests.sh already
    # records as conformanceScope=false; they are stated prominently in section 7
    # instead. wrongLevel is blocking: it means the container ran a profile other
    # than the one the report claims, which invalidates the level outright.
    # scenarioJvmOom is blocking for the same reason as wrongLevel: the level's
    # figures do not describe the SUT. A heap-exhausted run keeps producing
    # outcome records, so nothing else here would catch it.
    blocking = {"missingTestReport", "noLevels", "totalsDisagree", "unreadableJson",
                "wrongLevel", "archiveChanged", "scenarioJvmOom"}
    hard = [a for a in doc["anomalies"] if a["kind"] in blocking]
    for lv in doc["levels"].values():
        hard += [a for a in lv["anomalies"] if a["kind"] in blocking]
    if hard:
        kinds = sorted({a["kind"] for a in hard})
        print(f"blocking anomaly/anomalies: {', '.join(kinds)}", file=sys.stderr)
    return 1 if hard else 0


if __name__ == "__main__":
    raise SystemExit(main())
