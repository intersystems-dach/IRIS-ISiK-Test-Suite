#!/usr/bin/env bash
# =============================================================================
# 81-case-evidence.sh - generate the per-case evidence annex of the gap analysis
# =============================================================================
# `make case-evidence`. Writes reports/<run-id>/CASE-EVIDENCE.md and
# case-evidence.json: one clause per failing test case, each stating the exact
# input data the case used, the output it expected, the output it received, and
# how to reproduce it.
#
# Why this is a separate deliverable from GAP-ANALYSIS.md
# -------------------------------------------------------
# GAP-ANALYSIS.md is deduplicated on purpose: 41 failing cases collapse to 17
# entries, because a developer should not read the same CapabilityStatement defect
# fourteen times. That is the right document for deciding what to fix, and the
# wrong one for fixing it - it lists the affected scenarios without showing them,
# and it shows no request body, no response and no way to replay a case.
#
# This annex is the other half. It is one clause per case, not per defect, and it
# carries the material a developer needs at the keyboard: the Scenario Outline
# row, the fixture the step read, the request as the endpoint received it with its
# body verbatim, the response with its body decoded, the FHIRPath the suite
# evaluated and the value it got, and a curl command that replays the exchange
# against the round's own endpoint.
#
# Like the gap analysis, the generation is a pure join over the archived bundle:
# no container, no network, no licence key, no SUT. That is what lets this document
# be produced for the 2026-08-07 reference round as well, months after that
# deployment was torn down - which is the point. Two rounds' CASE-EVIDENCE.md diff
# case by case, so what a release moved and what it did not is a `diff` rather than
# an argument.
#
# Preconditions, each a different operator error with a different fix:
#
#   1. the run bundle exists and `make report` has been there (results.json)
#   2. `make gap-report` has been there (gap-analysis.json), else the entries
#      carry no gap id and no expectation in FHIR terms - warned, not fatal
#   3. the official archives are present, or an extraction of them
#   4. python3 exists
#
# Usage:
#   make case-evidence                        the current run id
#   make case-evidence RUN_ID=run-...         a previous round, re-generated
#   scripts/81-case-evidence.sh --strict      exit non-zero if a failing case
#                                             has no recorded exchange
#
# Any further arguments are passed through to case_evidence.py, so
# `--generated-at`, `--out-md`, `--out-json` and `--max-body` are available. Use
# `--generated-at` when diffing two rounds, so the timestamp is not the first
# difference the diff reports.
#
# Exit status is about the annex, never about the SUT. A round in which every case
# failed produces a large document and exit 0.
# =============================================================================

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

step "Generating the per-case evidence annex"

load_env
need_cmd python3

CE_DIR="${REPO_ROOT}/gap-analysis"
CE_RUN_DIR="$(run_report_dir)"
CE_RESULTS="${CE_RUN_DIR}/analysis/results.json"
CE_GAP_JSON="${CE_RUN_DIR}/gap-analysis.json"

# -----------------------------------------------------------------------------
# 1. The run bundle
# -----------------------------------------------------------------------------
[[ -d "$CE_RUN_DIR" ]] || die "no run directory at ${CE_RUN_DIR#$REPO_ROOT/}
  Nothing has been archived for run id '$(run_id)'. Run 'make test' then
  'make report', or point at a previous round:
    make case-evidence RUN_ID=run-20260807T121756Z
  Available runs:
$(ls -1 "$REPORTS_DIR" 2>/dev/null | sed 's/^/    /' || printf '    (none)\n')"

[[ -f "$CE_RESULTS" ]] || die "analysis/results.json is missing from ${CE_RUN_DIR#$REPO_ROOT/}
  This annex documents the cases that document records as failing, so that it and
  GAP-ANALYSIS.md can never disagree about which cases exist. Run
  'make report RUN_ID=$(run_id)' first."

# -----------------------------------------------------------------------------
# 2. The attribution
# -----------------------------------------------------------------------------
# Warned rather than failed: without it every clause still states the input, the
# expectation and the received response - which is the bulk of the document - but
# none of them says which defect the case belongs to, so the annex cannot be read
# beside the gap analysis. That is a degraded document, not a wrong one.
if [[ ! -f "$CE_GAP_JSON" ]]; then
  warn "gap-analysis.json is missing from ${CE_RUN_DIR#$REPO_ROOT/}
  Every clause will carry the input, the expectation and the received response,
  but no gap id and no expectation in FHIR terms, and section 6 will report every
  case as unattributed. Run 'make gap-report RUN_ID=$(run_id)' first."
fi

# -----------------------------------------------------------------------------
# 3. The official archives
# -----------------------------------------------------------------------------
# The exchange is read out of the suite's own archives. Both forms are accepted -
# the extracted tree `make report` leaves behind, and the zips alone - so this
# step is checked by counting what is addressable either way rather than by
# looking for one of them. What it cannot replace is the debug archive itself:
# a slim bundle carries official/test-report.zip only, and over one of those the
# generator documents every case and quotes no exchange at all. Hence the warning
# below, per archive, rather than a silent document.
#
# Per ARCHIVE and not per level, because a case's exchange is in the archive that
# measured it: `connect/` is a second measurement of one Stufe 5 module and its
# verdicts supersede the complete archive's, so the cases documented here cite
# traffic logs under connect/ that exist in no other archive. The list is the same
# one 70-collect-report.sh extracts (ARCHIVE_DIRS there), named rather than
# globbed for the same reason.
ce_archives=0
ce_sources=0
for ce_arch in $(suite_levels_lc) connect; do
  ce_arch="${CE_RUN_DIR}/${ce_arch}"
  [[ -d "$ce_arch" ]] || continue
  ce_archives=$((ce_archives + 1))
  if [[ -d "${ce_arch}/official/extracted/debug-report" ]]; then
    ce_sources=$((ce_sources + 1))
    log "${ce_arch##*/}: reading the extracted debug report"
  elif [[ -f "${ce_arch}/official/debug-report.zip" ]]; then
    ce_sources=$((ce_sources + 1))
    log "${ce_arch##*/}: reading official/debug-report.zip directly"
  else
    warn "${ce_arch##*/} carries neither official/debug-report.zip nor an
  extraction of it. Its cases will be documented from the official test report
  alone, which states the steps and the assertion but not the exchange, so they
  will appear in section 5 as having no recorded request and response."
  fi
done

(( ce_archives )) || die "no archive directory (stufe3/, stufe5/, connect/) in ${CE_RUN_DIR#$REPO_ROOT/}
  There is nothing to read the exchanges out of. The bundle is incomplete."

log "run bundle:      ${CE_RUN_DIR#$REPO_ROOT/}"
log "archives:        ${ce_archives}, of which ${ce_sources} carry a traffic source"

# -----------------------------------------------------------------------------
# 4. Generate
# -----------------------------------------------------------------------------
# Exit status kept rather than allowed to abort (set -e), because the summary
# below is worth printing either way. Non-zero only under --strict.
ce_rc=0
"${CE_DIR}/case_evidence.py" --run-dir "$CE_RUN_DIR" "$@" || ce_rc=$?

CE_MD="${CE_RUN_DIR}/CASE-EVIDENCE.md"
CE_JSON="${CE_RUN_DIR}/case-evidence.json"

if generator_output_redirected "$@"; then
  log "output was redirected by an explicit --out-* argument"
  step "Case evidence annex generated"
  exit "$ce_rc"
fi

[[ -f "$CE_MD" ]] || die "case_evidence.py did not write CASE-EVIDENCE.md - see above.
  This is a harness error, not a conformance result. The official report in
  ${CE_RUN_DIR#$REPO_ROOT/}/<variant>/official/ is unaffected and remains the
  authoritative artefact."

# Checked before the manifest records it, as in 80-gap-report.sh. This document
# matters most of the three: it quotes the exchange, it is the one that gets
# forwarded to a product organisation, and in a Connect round the test suite
# resolves the bearer token into the step description of even a passing step.
# case_evidence.py withholds both credentials where it reads a step; this is the
# check that the document it produced actually carries none.
assert_no_published_credentials "$CE_MD" "$CE_JSON" \
  || die "CASE-EVIDENCE.md or case-evidence.json carries a credential this round
  generated. Both are still on disk and neither is recorded in the bundle
  manifest; see the paths above and redact() in gap-analysis/case_evidence.py."

# -----------------------------------------------------------------------------
# 5. Keep the bundle manifest honest
# -----------------------------------------------------------------------------
# Same reasoning as in 80-gap-report.sh, clause 4, and the same helper:
# amend_bundle_manifest() in lib.sh.
amend_bundle_manifest "$CE_RUN_DIR" CASE-EVIDENCE.md case-evidence.json \
  || warn "analysis/MANIFEST.txt does not exist, so CASE-EVIDENCE.md is outside the
  bundle's integrity check. Run 'make report RUN_ID=$(run_id)' to rebuild it."

ok "wrote ${CE_MD#$REPO_ROOT/} ($(du -h "$CE_MD" | cut -f1))"
log "and ${CE_JSON#$REPO_ROOT/} (machine-readable, for diffing two rounds)"

# What to do with it, in this project's terms: the annex is for working through
# cases one at a time, and the figure that matters when reading it is how many
# cases carry a replayable exchange. Read out of the JSON the generator just
# wrote, so this line and the document can never disagree.
python3 - "$CE_JSON" <<'PY' || true
import json
import sys

try:
    doc = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception as exc:                      # pragma: no cover - defensive
    print(f"  (could not summarise: {exc})", file=sys.stderr)
    raise SystemExit(0)

entries = doc.get("entries") or []
if not entries:
    print("  No failing cases to document. Check the coverage table in the "
          "annex before reading that as conformance: a run that executed few "
          "cases can pass without proving much.", file=sys.stderr)
    raise SystemExit(0)

with_rsp = [e for e in entries if (e.get("received") or {}).get("response")]
with_body = [e for e in entries if ((e.get("input") or {}).get("request") or {}).get("body")]
sut = [e for e in entries if e.get("attributedTo") == "the system under test"]
unattributed = [e for e in entries if not e.get("gap")]

print(f"  {len(entries)} failing case(s) documented, {len(with_rsp)} with a "
      f"recorded request and response, {len(with_body)} with a request body",
      file=sys.stderr)
print(f"  {len(sut)} attributed to the SUT; each clause names the gap id "
      f"GAP-ANALYSIS.md uses for it", file=sys.stderr)
if unattributed:
    print(f"  WARNING {len(unattributed)} case(s) carry no gap id, so the annex "
          f"cannot be read beside GAP-ANALYSIS.md for them. Re-generate the gap "
          f"analysis for this round.", file=sys.stderr)
if len(with_rsp) != len(entries):
    print(f"  {len(entries) - len(with_rsp)} case(s) have no recorded exchange "
          f"and are in the annex's section 5. That is expected for an assertion "
          f"made about a response another scenario of the same feature obtained; "
          f"section 5 names each one.", file=sys.stderr)
PY

if (( ce_rc )); then
  # Only --strict can get here (the generator returns 0 otherwise), so the caller
  # asked for this. Say which contract was broken rather than just failing.
  warn "case_evidence.py exited ${ce_rc} under --strict: at least one failing
  case has no recorded request and response. Section 5 of CASE-EVIDENCE.md names
  them and states why. The document was still written."
  step "Case evidence annex generated, with cases lacking an exchange"
else
  step "Case evidence annex generated"
fi

# Passed through so --strict is meaningful in CI. Without --strict this is always
# 0, including when every case in the round failed: a conformance gap is the
# expected result of this project and is not a harness error.
exit "$ce_rc"
