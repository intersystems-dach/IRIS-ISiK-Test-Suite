#!/usr/bin/env python3
"""Join two rounds case by case and name what moved between them.

Run by scripts/90-compare-rounds.sh (`make compare`). Reads the
``analysis/results.json`` of two rounds - the per-case analysis
70-collect-report.sh derives from the official archives - and writes:

    comparison-<baseline>-vs-<candidate>.json   the case-level join
    (optionally) the same as a short markdown summary

Why this exists as a tool rather than as a measurement
------------------------------------------------------
The statement a management reader actually acts on is not the pass rate of one
round; it is "nothing that worked before is broken now". That statement is a
join over two rounds, and until 2026-11-11 the program that made it lived beside
the round's documents rather than in the rig: in no make target, outside the
repository's self-check, absent from docs/, and not where whoever runs the next
round would look. The figures were right - this tool reproduces all nine of the
comparisons published that day from the archived bundles - but the one number a
release decision turns on was the only one whose generator a reader could not
reach from the repository. Hence this file, `make compare`, and the
documentation in docs/en/07-reports.md.

Like the gap analysis, it is a pure join over archived evidence: given two
bundles it prints the same answer on any machine, months later, with no
container, no network and no licence key.

``analysis/results.json`` is the source rather than the CSV beside it because it
is the only derived artefact that carries the *data* of a Scenario Outline's
Examples row, which the cross-version join below depends on. It is present in
every bundle, including the slim archives.

The five verdicts, and why PRECONDITION is one of them
------------------------------------------------------
Each joined case is classified exactly once:

    STABLE        passed in both rounds
    FIXED         failed in the baseline, passes in the candidate
    REGRESSION    passed in the baseline, fails in the candidate
    PERSISTING    failed in both rounds
    PRECONDITION  marked @vorbedingung / @Precondition in either round

Precondition blocks are separated rather than counted as tests because the
suite uses them to establish state (create the resource a Read scenario then
reads), so a precondition's verdict says nothing about conformance and would
otherwise inflate both the numerator and the denominator. The analysis they come
from already draws this line, and this tool only has to keep it.

REGRESSION is the only verdict that is a finding about the product. FIXED is the
only one that is good news. The other three are the background against which
those two are read, and all five are always reported, including the zeros: "0
regressions" is the headline of a comparison and must not be inferable only from
the absence of a key.

They are also reported so that a reader can see that the two rounds are in fact
comparable: 1 215 STABLE cases and 0 REGRESSION is a different claim from 3
STABLE cases and 0 REGRESSION.

Two join keys, chosen by measurement rather than by assertion
-------------------------------------------------------------
Within one test-suite version a case has a stable address: level, feature file,
the line the scenario is declared on, its name, and - for a Scenario Outline -
which Examples row it is. That is the strict key, and it is exact.

Across two suite versions the line numbers move, scenarios are renamed and
Examples rows are inserted, reordered and deleted. The loose key therefore drops
``featureLine`` and replaces the Examples *index* with the Examples *data*: the
row's parameter bindings, canonicalised. Joining outline rows by index across
versions is what this tool must not do, and the reason is measured rather than
imagined: between suite 2.7.0 and 3.0.9 the ``Observation-Search`` outline moved
its ``_tag`` and ``subject`` rows into an ``@Optional`` block, which under this
project's tag expression takes the executed table from 12 rows to 11 and shifts
every row below. Joining those two rounds by index reports a regression at
``combo-code-value-quantity`` and a matching fix at ``component-code`` - two
findings out of one unchanged failure. Two rows match here only if they bind the
same parameters to the same values.

The price is paid honestly: a scenario the newer suite renamed, or a row whose
parameters it changed, appears as one entry in ``onlyInBaseline`` and one in
``onlyInCandidate`` rather than as a comparison. That is the correct outcome -
the two cases are not the same case - but it means a cross-version comparison
covers fewer cases than either round contains, and those two lists are where a
reader sees how many.

``--join auto`` (the default) tries the strict key and keeps it only if it
matches every case on both sides; otherwise it falls back to the loose key. The
key that was used is recorded in the output as ``joinKey``, so no reader has to
infer it.

Levels
------
Only levels present in both rounds are compared. A round that ran Stufe 5 alone
is comparable with a round that ran both levels, but only on Stufe 5; silently
counting the baseline's 506 Stufe 3 cases as "gone" would turn a scope
difference into an apparent mass regression. The levels actually compared are
recorded in the output, and the per-level totals are reported for those levels
only - which is why ``baselineTotals`` of a Stufe-5-only comparison carries no
Stufe 3 figures even though the baseline bundle does.

Exit status is about the comparison, never about the SUT: a round full of new
regressions exits 0. ``--fail-on-regression`` is for a caller that wants the
opposite, and ``--strict`` fails when a case could not be classified at all.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

# The verdict a case carries when it is not a test but a state-establishing
# block. Kept first in the report's ordering because it is the one a reader must
# discount before reading the rest.
PRECONDITION = "PRECONDITION"
VERDICTS = (PRECONDITION, "STABLE", "FIXED", "REGRESSION", "PERSISTING")

STRICT_KEY = ("level", "feature", "featureLine", "scenario", "exampleIndex")
LOOSE_KEY = ("level", "feature", "scenario", "exampleData")

# Stands in for an Examples row whose bindings the analysis did not record. Kept
# as a sentinel rather than as "" so that the loose join can refuse it instead of
# silently treating such a row as an ordinary scenario - see example_data().
NO_EXAMPLE_DATA = "\x00no-example-data"

Case = Dict[str, Any]
Key = Tuple[str, ...]


def results_json(path: Path) -> Path:
    """Accept either the analysis file itself or the round directory holding it.

    Callers hold run directories, not file paths, and the file's location inside
    a bundle is fixed by 70-collect-report.sh. Both spellings are therefore
    allowed, and the error message names the generating command rather than the
    missing file, because "the round has not been reported yet" is the fixable
    condition.
    """
    if path.is_dir():
        candidate = path / "analysis" / "results.json"
        if not candidate.is_file():
            raise SystemExit(
                f"{path} holds no analysis/results.json.\n"
                "  The comparison joins the derived per-case analysis, not the\n"
                "  official archives directly, so that both rounds are counted\n"
                "  the same way. Run: make report RUN_ID=" + path.name)
        return candidate
    if not path.is_file():
        raise SystemExit(f"no such file or directory: {path}")
    return path


def example_data(case: Case) -> str:
    """The Examples row's parameter bindings, canonicalised for use as a key.

    Sorted keys and no whitespace, so that two rounds that recorded the same
    bindings in a different order still match. An ordinary scenario has no
    bindings and yields the empty string, which is a legitimate key value: every
    non-outline scenario of one feature file has a distinct name.

    A case that *is* example-driven but whose bindings the analysis did not
    record yields ``NO_EXAMPLE_DATA``, which the loose join rejects rather than
    keys as "". Falling back on the row's position is the one behaviour that key
    exists to remove, and doing it for a subset of rows would be worse than
    doing it for all of them: the failure would be invisible and confined to
    whichever rows the analysis happened not to carry.
    """
    ex = case.get("example")
    if not ex:
        return ""
    data = ex.get("data")
    if not data:
        return NO_EXAMPLE_DATA
    return json.dumps(data, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def flatten(doc: Dict[str, Any], source: Path) -> List[Case]:
    """One flat list of cases, with the key fields normalised to strings.

    ``featureLine`` and the Examples index are integers in results.json and
    strings in the CSV beside it; they are stringified here so that a key built
    from either artefact compares equal, and so that a missing value is "" for
    every field rather than sometimes None.
    """
    levels = doc.get("levels") or {}
    if not levels:
        raise SystemExit(
            f"{source} carries no levels.\n"
            "  Either the round recorded no execution, or the file was written\n"
            "  by a version of results_report.py older than this comparison\n"
            "  expects. Re-derive it: make report RUN_ID=<run-id>")
    out: List[Case] = []
    for level in levels.values():
        for case in level.get("cases") or []:
            ex = case.get("example") or {}
            failure = case.get("failure") or {}
            http = failure.get("http") or {}
            out.append({
                "level": str(case.get("level", "")),
                "module": str(case.get("module", "")),
                "feature": str(case.get("feature", "")),
                "featureLine": str(case.get("featureLine", "") or ""),
                "scenario": str(case.get("scenario", "")),
                "exampleIndex": str(ex.get("index", "") or ""),
                "exampleData": example_data(case),
                # A case out of certification scope asserts nothing against the
                # SUT either, so it is counted with the preconditions rather
                # than as a test whose verdict moved. `inScope` defaults to true
                # when absent: every case of every round this project has run
                # carries it, and treating a missing value as out of scope would
                # empty the comparison rather than fail it.
                "isPrecondition": bool(case.get("isPrecondition"))
                or not bool(case.get("inScope", True)),
                "result": str(case.get("result", "")),
                "verdict": str(case.get("verdict", "")),
                "failingStep": str(failure.get("step", "") or ""),
                "httpMethod": str(http.get("method", "") or ""),
                "httpPath": str(http.get("path", "") or ""),
                "httpStatus": str(http.get("status", "") or ""),
                "errorType": str(failure.get("errorType", "") or ""),
                "message": str(failure.get("message", "") or ""),
                "evidence": str((case.get("report") or {})
                                .get("outcomeJson", "") or ""),
            })
    if not out:
        raise SystemExit(f"{source} carries levels but no cases")
    return out


def key_of(case: Case, fields: Sequence[str]) -> Key:
    return tuple(case.get(f, "") for f in fields)


def strictly_joinable(base_cases: Sequence[Case], cand_cases: Sequence[Case]) -> bool:
    """Whether STRICT_KEY identifies every case of both rounds, and the same ones.

    Asked of the two corpora rather than inferred from the suite version, which
    would in any case be the wrong test: two rounds of one suite version whose
    feature corpus was edited in between are not strictly joinable either.

    A repeated key is counted here rather than refused, which is what separates
    this question from :func:`index_cases`: a corpus the loose key will handle
    must be allowed to answer "no" instead of aborting the comparison.
    """
    base = [key_of(case, STRICT_KEY) for case in base_cases]
    cand = [key_of(case, STRICT_KEY) for case in cand_cases]
    return (len(set(base)) == len(base) and len(set(cand)) == len(cand)
            and set(base) == set(cand))


def index_cases(cases: Iterable[Case], fields: Sequence[str],
                source: str) -> Dict[Key, Case]:
    """Index by key. A repeated key is fatal; it would make the join ambiguous.

    A repeated key means the chosen key does not identify a case uniquely within
    one round - two Examples rows binding identical values under the loose key,
    say. Both the silent merge and the first-wins pick would then compare one
    round's case against an arbitrary one of the other's, so the join is refused
    instead.
    """
    out: Dict[Key, Case] = {}
    for case in cases:
        key = key_of(case, fields)
        if key in out:
            raise SystemExit(
                f"{source}: two cases share the join key {key!r}, so joining on "
                f"{', '.join(fields)} would be ambiguous. This pair of rounds "
                "cannot be compared on that key; see --join.")
        out[key] = case
    return out


def totals(cases: Iterable[Case]) -> Dict[str, Dict[str, Any]]:
    """Per-level totals in the shape this project's comparisons already use.

    ``asserting`` is the conformance denominator: cases that are not
    preconditions. ``passed`` / ``failed`` count every case, preconditions
    included, so that the figures also reconcile against the official report's
    own totals, which do not know about the distinction.
    """
    per: Dict[str, Dict[str, Any]] = {}
    for case in cases:
        lv = per.setdefault(case["level"], {
            "cases": 0, "asserting": 0, "precondition": 0,
            "passed": 0, "failed": 0,
            "assertingPassed": 0, "assertingFailed": 0,
            "byResult": Counter(),
        })
        passed = case["verdict"] == "pass"
        lv["cases"] += 1
        lv["passed" if passed else "failed"] += 1
        lv["byResult"][case["result"]] += 1
        if case["isPrecondition"]:
            lv["precondition"] += 1
        else:
            lv["asserting"] += 1
            lv["assertingPassed" if passed else "assertingFailed"] += 1
    for lv in per.values():
        lv["byResult"] = dict(sorted(lv["byResult"].items(),
                                     key=lambda kv: (-kv[1], kv[0])))
    return dict(sorted(per.items()))


def sort_key(case: Case) -> Tuple[str, str, int, str, int]:
    """Deterministic output order, so two comparisons of one round diff cleanly.

    ``featureLine`` and the Examples index are sorted as numbers: lexically,
    line 100 would precede line 29.
    """
    def num(value: str) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return -1
    return (case.get("level", ""), case.get("feature", ""),
            num(case.get("featureLine", "")), case.get("scenario", ""),
            num(case.get("exampleIndex", "")))


def address(case: Case) -> Dict[str, str]:
    return {
        "level": case["level"],
        "module": case["module"],
        "scenario": case["scenario"],
        "feature": case["feature"],
        "featureLine": case["featureLine"],
        "exampleIndex": case["exampleIndex"],
    }


def unmatched(case: Case) -> Dict[str, str]:
    """A case the other round does not have, in the fields that identify it.

    Deliberately narrower than ``address()``: a reader of this list is asking
    *which scenarios differ between the two suite versions*, and a line number
    from the version that no longer exists would only invite a join that cannot
    be made. The Examples bindings are kept, because for an outline they are the
    only thing that distinguishes one entry of this list from the next.
    """
    out = {
        "level": case["level"],
        "feature": case["feature"],
        "scenario": case["scenario"],
        "exampleIndex": case["exampleIndex"],
    }
    if case["exampleData"]:
        out["exampleData"] = case["exampleData"]
    return out


def compare(base_cases: List[Case], cand_cases: List[Case],
            join: str) -> Dict[str, Any]:
    levels = sorted({c["level"] for c in base_cases}
                    & {c["level"] for c in cand_cases})
    if not levels:
        raise SystemExit(
            "the two rounds have no level in common, so there is nothing to\n"
            "  compare: baseline carries "
            f"{sorted({c['level'] for c in base_cases})}, candidate carries "
            f"{sorted({c['level'] for c in cand_cases})}")

    base_cases = [c for c in base_cases if c["level"] in levels]
    cand_cases = [c for c in cand_cases if c["level"] in levels]

    # --join auto keeps the exact key only when it is in fact exact.
    fields: Sequence[str] = STRICT_KEY
    if join == "loose":
        fields = LOOSE_KEY
    elif join == "auto" and not strictly_joinable(base_cases, cand_cases):
        fields = LOOSE_KEY

    # Under the loose key an Examples row whose bindings were not recorded could
    # only be joined by its position, which is precisely what that key exists to
    # avoid. Refused here rather than at read time, because the strict key does
    # not need those bindings and must not fail for want of them.
    if fields is LOOSE_KEY:
        for role, cases in (("baseline", base_cases), ("candidate", cand_cases)):
            for case in cases:
                if case["exampleData"] == NO_EXAMPLE_DATA:
                    raise SystemExit(
                        f"the {role} round records no row values for row "
                        f"{case['exampleIndex']} of '{case['scenario']}' in "
                        f"{case['feature']}, so it can be joined across suite "
                        "versions only by its position - which this join "
                        "refuses to do. Re-derive the analysis with "
                        "'make report RUN_ID=<run-id>', or compare these two "
                        "rounds with --join strict if they ran one suite "
                        "version.")

    base = index_cases(base_cases, fields, "baseline")
    cand = index_cases(cand_cases, fields, "candidate")

    counts: Counter = Counter()
    regressions: List[Dict[str, Any]] = []
    newly_passing: List[Dict[str, Any]] = []
    unclassified: List[Dict[str, Any]] = []

    for key in sorted(set(base) & set(cand), key=lambda k: sort_key(cand[k])):
        b, c = base[key], cand[key]
        if b["isPrecondition"] or c["isPrecondition"]:
            counts[PRECONDITION] += 1
            continue
        if b["verdict"] not in ("pass", "fail") or \
                c["verdict"] not in ("pass", "fail"):
            # Unreachable with results_report.py as it stands, which emits only
            # these two. Kept because a third value would otherwise fall into
            # the "not passed" branches below and be counted as a failure, which
            # is the one outcome worse than refusing to classify it.
            counts["UNCLASSIFIED"] += 1
            unclassified.append({**address(c),
                                 "baselineVerdict": b["verdict"],
                                 "candidateVerdict": c["verdict"]})
            continue
        bp, cp = b["verdict"] == "pass", c["verdict"] == "pass"
        if bp and cp:
            counts["STABLE"] += 1
        elif bp and not cp:
            counts["REGRESSION"] += 1
            regressions.append({
                **address(c),
                "baselineResult": b["result"],
                "candidateResult": c["result"],
                "failingStep": c["failingStep"],
                "httpMethod": c["httpMethod"],
                "httpPath": c["httpPath"],
                "httpStatus": c["httpStatus"],
                "errorType": c["errorType"],
                "message": c["message"],
                "evidence": c["evidence"],
            })
        elif cp:
            counts["FIXED"] += 1
            newly_passing.append({
                **address(c),
                "baselineResult": b["result"],
                "baselineFailingStep": b["failingStep"],
                "baselineMessage": b["message"],
            })
        else:
            counts["PERSISTING"] += 1

    only_base = [unmatched(base[k])
                 for k in sorted(set(base) - set(cand),
                                 key=lambda k: sort_key(base[k]))]
    only_cand = [unmatched(cand[k])
                 for k in sorted(set(cand) - set(base),
                                 key=lambda k: sort_key(cand[k]))]

    # Every verdict is emitted, zeros included: "0 regressions" is the sentence
    # this document exists to support, and a reader must not have to deduce it
    # from a missing key.
    out_counts = {k: counts[k] for k in VERDICTS}
    if counts["UNCLASSIFIED"]:
        out_counts["UNCLASSIFIED"] = counts["UNCLASSIFIED"]

    doc: Dict[str, Any] = {
        "joinKey": list(fields),
        "levels": levels,
        "baselineTotals": totals(base_cases),
        "candidateTotals": totals(cand_cases),
        "counts": out_counts,
        "onlyInBaseline": only_base,
        "onlyInCandidate": only_cand,
        "regressions": regressions,
        "newlyPassing": newly_passing,
    }
    if unclassified:
        doc["unclassified"] = unclassified
    return doc


def markdown(doc: Dict[str, Any], base_label: str, cand_label: str) -> str:
    c = doc["counts"]
    lines = [
        f"# {base_label} -> {cand_label}",
        "",
        f"Levels compared: {', '.join(doc['levels'])}. "
        f"Join key: `{'`, `'.join(doc['joinKey'])}`.",
        "",
        "| Verdict | Cases |",
        "|---|---|",
    ]
    for k, v in c.items():
        lines.append(f"| {k} | {v} |")
    lines += [
        "",
        f"Cases only in {base_label}: {len(doc['onlyInBaseline'])}. "
        f"Only in {cand_label}: {len(doc['onlyInCandidate'])}.",
        "",
    ]
    if doc["regressions"]:
        lines += ["## Regressions", ""]
        for r in doc["regressions"]:
            lines += [
                f"* **{r['level']} / {r['module']}** - {r['scenario']}  ",
                f"  `{r['feature']}:{r['featureLine']}`, "
                f"{r['baselineResult']} -> {r['candidateResult']}  ",
                f"  step: {r['failingStep'] or '(none recorded)'}  ",
                f"  message: {(r['message'] or '(none recorded)')[:400]}",
                "",
            ]
    else:
        lines += ["No case that passed in the baseline fails in the candidate.",
                  ""]
    if doc["newlyPassing"]:
        lines += ["## Newly passing", ""]
        for r in doc["newlyPassing"]:
            lines += [f"* **{r['level']} / {r['module']}** - {r['scenario']}  ",
                      f"  `{r['feature']}:{r['featureLine']}`, "
                      f"was {r['baselineResult']}", ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--baseline", type=Path, required=True,
                    help="the earlier round: its directory or its "
                         "analysis/results.json")
    ap.add_argument("--candidate", type=Path, required=True,
                    help="the later round, same two spellings")
    ap.add_argument("--join", choices=("auto", "strict", "loose"),
                    default="auto",
                    help="auto (default) uses the exact key when it matches "
                         "every case on both sides, else joins outline rows by "
                         "their Examples data instead of line and index")
    ap.add_argument("--out-json", type=Path,
                    help="where to write the join (default: stdout)")
    ap.add_argument("--out-md", type=Path,
                    help="also write a short markdown summary there")
    ap.add_argument("--label-baseline", default="",
                    help="how the documents name the baseline round, e.g. A")
    ap.add_argument("--label-candidate", default="")
    ap.add_argument("--fail-on-regression", action="store_true",
                    help="exit 2 when any case regressed (for CI; off by "
                         "default because a regression is a result, not an "
                         "error in the comparison)")
    ap.add_argument("--strict", action="store_true",
                    help="exit 3 when a joined case could not be classified")
    args = ap.parse_args()

    base_src = results_json(args.baseline)
    cand_src = results_json(args.candidate)
    base_doc = json.loads(base_src.read_text(encoding="utf-8"))
    cand_doc = json.loads(cand_src.read_text(encoding="utf-8"))
    doc = compare(flatten(base_doc, base_src), flatten(cand_doc, cand_src),
                  args.join)

    # The sources go in first, so the two files the join came from are the first
    # thing a reader of the JSON sees, as in the comparisons already published.
    # The generator is named for the same reason the sources are: the defect this
    # tool was written for was a published figure whose producer the next
    # operator could not find. No timestamp goes in: the document is a pure
    # function of the two inputs, and a reader must be able to regenerate it
    # byte for byte.
    out: Dict[str, Any] = {"baseline": str(base_src),
                           "candidate": str(cand_src),
                           "baselineRunId": base_doc.get("runId", ""),
                           "candidateRunId": cand_doc.get("runId", ""),
                           "generator": "scripts/lib/compare_rounds.py"}
    if args.label_baseline:
        out["baselineLabel"] = args.label_baseline
    if args.label_candidate:
        out["candidateLabel"] = args.label_candidate
    out.update(doc)

    text = json.dumps(out, indent=2, ensure_ascii=False) + "\n"
    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)

    bl = args.label_baseline or base_doc.get("runId") or base_src.parent.parent.name
    cl = args.label_candidate or cand_doc.get("runId") or cand_src.parent.parent.name
    if args.out_md:
        args.out_md.parent.mkdir(parents=True, exist_ok=True)
        args.out_md.write_text(markdown(out, bl, cl), encoding="utf-8")

    c = out["counts"]
    print(f"  {bl} -> {cl}: "
          f"{c['REGRESSION']} regression(s), "
          f"{c['FIXED']} newly passing, "
          f"{c['PERSISTING']} still failing, "
          f"{c['STABLE']} stable, "
          f"{c[PRECONDITION]} precondition block(s)", file=sys.stderr)
    if out["onlyInBaseline"] or out["onlyInCandidate"]:
        print(f"  {len(out['onlyInBaseline'])} case(s) only in {bl} and "
              f"{len(out['onlyInCandidate'])} only in {cl} are outside the "
              f"comparison (join key: {', '.join(out['joinKey'])})",
              file=sys.stderr)

    if args.strict and c.get("UNCLASSIFIED"):
        return 3
    if args.fail_on_regression and c["REGRESSION"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
