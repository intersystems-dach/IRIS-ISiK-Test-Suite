#!/usr/bin/env bash
# =============================================================================
# 80-gap-report.sh - generate the standalone developer gap analysis
# =============================================================================
# `make gap-report`, and the second half of `make report`. Turns the run's
# analysis/results.json into reports/<run-id>/GAP-ANALYSIS.md - a work list for
# InterSystems IRIS for Health developers, in English, one entry per distinct
# defect, each stating the test step executed, the FHIR feature it tested (and
# which FHIR package supplies that feature), and the expected versus actual
# result.
#
# Why this is a separate deliverable
# ----------------------------------
# IRIS for Health is known not to be fully ISiK compliant, so failures are the
# expected outcome of this project rather than an incident. Everything else under
# reports/ is written for a German regulatory reader who cares whether the run was
# conducted correctly. This document is written for a product developer who cares
# what to fix, has full IRIS source access and strong FHIR knowledge but little
# ISiK knowledge - so it expands ISiK terms, argues in FHIR terms rather than
# quoting German spec prose, and lives at the run root rather than under docs/ so
# it cannot be mistaken for regulatory documentation.
#
# This script does not analyse anything itself; gap-analysis/gap_report.py does.
# What this script owns is the three preconditions that turn a confusing Python
# traceback into an actionable message, and the fact that the generation is a pure
# join: no container, no network, no licence key. That is what makes the document
# reproducible from an archived bundle months later, which is the property that
# lets a developer diff two rounds.
#
#   1. the run bundle exists and `make report` has been there (results.json)
#   2. gap-analysis/index.json exists and is not older than the feature corpus
#   3. python3 exists
#
# Usage:
#   make gap-report                      the current run id
#   make gap-report RUN_ID=run-...       a previous round, re-generated
#   scripts/80-gap-report.sh --strict    exit non-zero on an unbalanced analysis
#
# Any further arguments are passed through to gap_report.py, so
# `--generated-at`, `--out-md` and `--index` are available for diffing two rounds
# without the timestamp making every line differ.
#
# Exit status is about the analysis, never about the SUT. A run in which every
# scenario failed produces a large report and exit 0; a run whose failures cannot
# all be accounted for is what --strict is for.
# =============================================================================

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

step "Generating the developer gap analysis"

load_env
need_cmd python3

GAP_DIR="${REPO_ROOT}/gap-analysis"
GAP_INDEX="${GAP_DIR}/index.json"
GAP_RUN_DIR="$(run_report_dir)"
GAP_RESULTS="${GAP_RUN_DIR}/analysis/results.json"

# -----------------------------------------------------------------------------
# 1. The run bundle
# -----------------------------------------------------------------------------
# Named separately from the results file below because the two are different
# operator errors with different fixes: no bundle means the round has not been
# run, no results.json means it has but `make report` has not.
[[ -d "$GAP_RUN_DIR" ]] || die "no run directory at ${GAP_RUN_DIR#$REPO_ROOT/}
  Nothing has been archived for run id '$(run_id)'. Run 'make test' then
  'make report', or point at a previous round:
    make gap-report RUN_ID=run-20260731T101500Z
  Available runs:
$(ls -1 "$REPORTS_DIR" 2>/dev/null | sed 's/^/    /' || printf '    (none)\n')"

[[ -f "$GAP_RESULTS" ]] || die "analysis/results.json is missing from ${GAP_RUN_DIR#$REPO_ROOT/}
  The gap analysis is a join over the results analysis, not over the official
  archives directly - that is deliberate, so that both documents count the same
  cases from the same source. Run 'make report RUN_ID=$(run_id)' first, which
  writes it (and then chains back into this step by itself)."

# -----------------------------------------------------------------------------
# 2. The attribution index
# -----------------------------------------------------------------------------
# Built by `make packages`, while the feature files and the unpacked FHIR packages
# are both present. It is what supplies the second required element of every entry
# - which FHIR package provides the feature under test - and it cannot be rebuilt
# from the bundle, because the bundle holds no packages.
[[ -f "$GAP_INDEX" ]] || die "gap-analysis/index.json is missing.
  It maps each test step to the FHIR feature it asserts and to the package that
  supplies it, and is built from the pinned image's feature files plus the
  unpacked packages - neither of which is in the report bundle. Rebuild it:
    make packages          (or: gap-analysis/build_index.py)
  Without it the report could state what failed but not what feature was tested,
  which is one of the three elements every entry must carry."

# A stale index is worse than a missing one: it would attribute this round's
# failures to the previous round's packages and feature corpus, silently. Warned
# rather than failed - the index may legitimately predate a re-run of the same
# pinned versions, and refusing to produce the report would be the less useful
# outcome. The report itself prints the index's build timestamp in its header, so
# the reader can always see which corpus was used.
GAP_FEATURES="${VENDOR_DIR}/upstream/features"
if [[ -d "$GAP_FEATURES" && "$GAP_FEATURES" -nt "$GAP_INDEX" ]]; then
  warn "gap-analysis/index.json is older than ${GAP_FEATURES#$REPO_ROOT/}
  The feature corpus changed after the index was built, so step attribution may
  be joined against the wrong line numbers. Rebuild with: make packages"
fi

log "run bundle:      ${GAP_RUN_DIR#$REPO_ROOT/}"
log "attribution index: ${GAP_INDEX#$REPO_ROOT/} ($(du -h "$GAP_INDEX" | cut -f1))"

# -----------------------------------------------------------------------------
# 3. Generate
# -----------------------------------------------------------------------------
# The generator's own exit status is kept: 0 normally, and non-zero only under
# --strict when the reconciliation does not balance or a failure could not be
# categorised. It is captured rather than allowed to abort the script (set -e)
# because the summary below is worth printing either way.
gap_rc=0
"${GAP_DIR}/gap_report.py" --run-dir "$GAP_RUN_DIR" --index "$GAP_INDEX" "$@" \
  || gap_rc=$?

GAP_MD="${GAP_RUN_DIR}/GAP-ANALYSIS.md"
GAP_JSON="${GAP_RUN_DIR}/gap-analysis.json"

# --out-md / --out-json in "$@" redirect the output, in which case the defaults
# below are legitimately absent and there is nothing here to check or to hash.
if [[ "$*" == *--out-md* || "$*" == *--out-json* ]]; then
  log "output was redirected by an explicit --out-* argument"
  step "Gap analysis generated"
  exit "$gap_rc"
fi

[[ -f "$GAP_MD" ]] || die "gap_report.py did not write GAP-ANALYSIS.md - see above.
  This is a harness error, not a conformance result. The official report in
  ${GAP_RUN_DIR#$REPO_ROOT/}/<stufe>/official/ is unaffected and remains the
  authoritative artefact."

# -----------------------------------------------------------------------------
# 4. Keep the bundle manifest honest
# -----------------------------------------------------------------------------
# analysis/MANIFEST.txt is written by 70-collect-report.sh, which runs BEFORE this
# script, so the two files just generated are absent from it. An inspector who
# verifies the bundle would find two unlisted files, which looks exactly like
# tampering. Their hashes are therefore appended here, in the same
# `sha256sum`-compatible format and with the same './' prefix, so that
# `sha256sum -c analysis/MANIFEST.txt` still passes on the finished bundle.
#
# Appending is safe against re-runs: the previous entries for these two paths are
# dropped first, so re-generating the report updates its hashes rather than
# accumulating stale ones.
GAP_MANIFEST="${GAP_RUN_DIR}/analysis/MANIFEST.txt"
if [[ -f "$GAP_MANIFEST" ]]; then
  GAP_TMP="$(mktemp "${GAP_MANIFEST}.XXXXXX")"
  grep -v -E '[[:space:]]\./(GAP-ANALYSIS\.md|gap-analysis\.json)$' \
    "$GAP_MANIFEST" >"$GAP_TMP" || true
  ( cd "$GAP_RUN_DIR" \
    && for f in GAP-ANALYSIS.md gap-analysis.json; do
         [[ -f "$f" ]] && printf '%s  ./%s\n' "$(sha256_of "$f")" "$f"
       done ) >>"$GAP_TMP"
  # Re-sorted so the manifest stays in the byte order 70-collect-report.sh
  # produced (LC_ALL=C sort over the find output), which is what makes two
  # rounds' manifests diffable.
  LC_ALL=C sort -k2 "$GAP_TMP" >"$GAP_MANIFEST"
  rm -f "$GAP_TMP"
  log "recorded both files in analysis/MANIFEST.txt ($(wc -l <"$GAP_MANIFEST" | tr -d ' ') entries)"
else
  # Only reachable when this script is run by hand on a bundle that never had
  # `make report` complete. Not fatal: the gap analysis is still correct, it is
  # just not covered by the bundle's integrity check.
  warn "analysis/MANIFEST.txt does not exist, so GAP-ANALYSIS.md is outside the
  bundle's integrity check. Run 'make report RUN_ID=$(run_id)' to rebuild it."
fi

ok "wrote ${GAP_MD#$REPO_ROOT/}"
log "and ${GAP_JSON#$REPO_ROOT/} (machine-readable, for diffing two rounds)"

# What to do with it, stated in the terms of this project: the document is a work
# list, and the count that matters is the deduplicated defect count, not the
# failure count. Read out of the JSON rather than recomputed here, so this line
# and the document can never disagree.
python3 - "$GAP_JSON" <<'PY' || true
import json
import sys

try:
    doc = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception as exc:                      # pragma: no cover - defensive
    print(f"  (could not summarise: {exc})", file=sys.stderr)
    raise SystemExit(0)

r = doc.get("reconciliation") or {}
gaps = doc.get("gaps") or []
if not gaps:
    print("  No failures to attribute. Check the coverage table in the report "
          "before reading that as conformance: a run that executed few cases "
          "can pass without proving much.", file=sys.stderr)
    raise SystemExit(0)

# The same two categories gap_report.py keeps out of the gap count: a defect in
# our own test data, and a case whose precondition this deployment did not meet.
# Named here rather than imported because this is a shell wrapper's summary, and
# a stale list would overstate the work rather than crash - so it is kept short
# and checked against the JSON's own count below.
not_sut = {"TEST_DATA_DEFECT", "ENVIRONMENT_PREREQUISITE"}
sut = [g for g in gaps if g.get("category") not in not_sut]
print(f"  {r.get('irisGapCases', '?')} failing case(s) attributable to the SUT "
      f"-> {len(sut)} distinct defect(s) to fix", file=sys.stderr)
if len(sut) != r.get("irisGapEntries", len(sut)):
    print(f"  WARNING this summary counted {len(sut)} SUT finding(s) and the "
          f"report counted {r['irisGapEntries']}. The report is authoritative; "
          "this line's category list is out of date.", file=sys.stderr)
if r.get("testDataDefects"):
    print(f"  {r['testDataDefects']} failure(s) are defects in this project's own "
          f"test data, segregated into the report's appendix and excluded from "
          f"that count", file=sys.stderr)
if r.get("environmentPrerequisites"):
    print(f"  {r['environmentPrerequisites']} failure(s) failed on a precondition "
          f"this deployment did not meet (see the report's section 5.2) and are "
          f"excluded from that count too", file=sys.stderr)
if not r.get("balanced", True):
    print("  WARNING the reconciliation does not balance: not every failing case "
          "in the official report produced a finding. Treat the report as "
          "incomplete and see its section 6.", file=sys.stderr)
for w in doc.get("warnings") or []:
    print(f"  WARN {w}", file=sys.stderr)
PY

if (( gap_rc )); then
  # Only --strict can get here (the generator returns 0 otherwise), so the caller
  # asked for this. Say which contract was broken rather than just failing.
  warn "gap_report.py exited ${gap_rc} under --strict: the analysis is either
  unbalanced or has uncategorised failures. Section 6 of GAP-ANALYSIS.md says
  which. The document was still written."
  step "Gap analysis generated, with reconciliation problems"
else
  step "Gap analysis generated"
fi

# Passed through so --strict is meaningful in CI. Without --strict this is always
# 0, including when every test in the round failed: a conformance gap is the
# expected result of this project and is not a harness error.
exit "$gap_rc"
