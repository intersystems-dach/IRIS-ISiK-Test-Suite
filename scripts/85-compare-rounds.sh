#!/usr/bin/env bash
# =============================================================================
# 85-compare-rounds.sh - join two archived rounds and report what moved
# =============================================================================
# `make compare BASELINE=<run-id-or-path> CANDIDATE=<run-id-or-path>`.
#
# Writes reports/<candidate>/comparison-vs-<baseline>.json (and, with
# COMPARE_MD=1, a short markdown summary beside it): one entry per case that
# changed verdict between the two rounds, plus the counts a reader needs to see
# that the two rounds are comparable at all.
#
# Why a round-to-round join is a deliverable of its own
# ----------------------------------------------------
# A single round answers "is the product conformant" (it is not, and that is the
# expected result of this project). What a release decision turns on is the
# other question: has anything that used to work stopped working. That is not
# visible in either round's documents - it is a join over both - and it is the
# one statement in the management reporting that no single archive substantiates.
# So it gets a generator, an output artefact and a place in the deliverables
# index, exactly like the gap analysis.
#
# Like `make gap-report`, this is a pure join over already-archived evidence: no
# container, no network, no licence key. That is what lets anybody re-derive the
# comparison from two slim archives months later and get the same answer -
# which is the only reason a figure in a board document is worth anything.
#
# Both arguments may be given as a run id (resolved under reports/), as a path
# to a round directory, or as a path to its analysis/results.json, so that a
# round published under this repository and a round unpacked from an archive
# somewhere else compare the same way.
#
#   make compare BASELINE=run-20260807T121756Z CANDIDATE=run-20261111T121705Z
#   make compare BASELINE=~/bundles/round-A CANDIDATE=run-20261111T105824Z
#
# Any further argument is passed through to compare_rounds.py, so --join,
# --label-baseline, --label-candidate, --fail-on-regression and --strict are
# available.
#
# Exit status is about the comparison, never about the SUT: a round that
# regressed in fifty places exits 0 and says so in the output. Only
# --fail-on-regression changes that, for a caller that wants a gate.
# =============================================================================

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

step "Comparing two rounds"

need_cmd python3

: "${BASELINE:?make compare needs BASELINE=<run-id or path>
  The earlier round, the one whose verdicts are the reference. Example:
    make compare BASELINE=run-20260807T121756Z CANDIDATE=$(run_id)
  Available rounds:
$(ls -1 "$REPORTS_DIR" 2>/dev/null | sed 's/^/    /' || printf '    (none)\n')}"

: "${CANDIDATE:=$(run_id)}"

# A bare run id means a round published in this repository; anything containing a
# slash is taken as a path, so a bundle unpacked elsewhere can be compared
# without being copied in first.
resolve_round() {
  local ref="$1" role="$2" path
  case "$ref" in
    */*) path="$ref" ;;
    *)   path="${REPORTS_DIR}/${ref}" ;;
  esac
  [[ -e "$path" ]] || die "the ${role} round is not at ${path}
  Give a run id published under reports/, or a path to a round directory or to
  its analysis/results.json. Available run ids:
$(ls -1 "$REPORTS_DIR" 2>/dev/null | sed 's/^/    /' || printf '    (none)\n')"
  printf '%s' "$path"
}

CMP_BASE="$(resolve_round "$BASELINE" baseline)"
CMP_CAND="$(resolve_round "$CANDIDATE" candidate)"

# The output belongs to the candidate's bundle: it is a statement about that
# round, and keeping it there means archiving the round archives the comparison
# with it. Named after the baseline, so a round compared against three
# predecessors carries three files rather than overwriting one.
CMP_OUT_DIR="$CMP_CAND"
[[ -d "$CMP_OUT_DIR" ]] || CMP_OUT_DIR="$(dirname "$(dirname "$CMP_CAND")")"
CMP_JSON="${CMP_OUT_DIR}/comparison-vs-$(basename "$BASELINE").json"

log "baseline:  ${CMP_BASE#$REPO_ROOT/}"
log "candidate: ${CMP_CAND#$REPO_ROOT/}"

cmp_args=(--baseline "$CMP_BASE" --candidate "$CMP_CAND" --out-json "$CMP_JSON")
if [[ "${COMPARE_MD:-0}" == "1" ]]; then
  cmp_args+=(--out-md "${CMP_JSON%.json}.md")
fi

cmp_rc=0
"${REPO_ROOT}/scripts/lib/compare_rounds.py" "${cmp_args[@]}" "$@" || cmp_rc=$?

[[ -f "$CMP_JSON" ]] || die "compare_rounds.py wrote nothing - see above.
  This is a harness error, not a conformance result. Both rounds' official
  reports are untouched and remain the authoritative artefacts."

ok "wrote ${CMP_JSON#$REPO_ROOT/}"
if [[ "${COMPARE_MD:-0}" == "1" ]]; then
  log "and ${CMP_JSON%.json}.md (the same, for a reader rather than a diff)"
fi

# Written after analysis/MANIFEST.txt, like GAP-ANALYSIS.md, so the hash is
# appended here for the same reason: an inspector who verifies the bundle must
# not find an unlisted file. Re-running replaces the previous entry rather than
# accumulating one.
CMP_MANIFEST="${CMP_OUT_DIR}/analysis/MANIFEST.txt"
if [[ -f "$CMP_MANIFEST" ]]; then
  CMP_REL="$(basename "$CMP_JSON")"
  CMP_TMP="$(mktemp "${CMP_MANIFEST}.XXXXXX")"
  grep -v -F "  ./${CMP_REL}" "$CMP_MANIFEST" >"$CMP_TMP" || true
  ( cd "$CMP_OUT_DIR" && printf '%s  ./%s\n' "$(sha256_of "$CMP_REL")" "$CMP_REL" ) \
    >>"$CMP_TMP"
  LC_ALL=C sort -k2 "$CMP_TMP" >"$CMP_MANIFEST"
  rm -f "$CMP_TMP"
  log "recorded it in analysis/MANIFEST.txt"
else
  warn "analysis/MANIFEST.txt does not exist, so the comparison is outside the
  bundle's integrity check. Run 'make report RUN_ID=$(basename "$CMP_OUT_DIR")'."
fi

step "Comparison generated"
exit "$cmp_rc"
