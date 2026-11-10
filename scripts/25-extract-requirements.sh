#!/usr/bin/env bash
# =============================================================================
# 25-extract-requirements.sh - derive the test-data specification from the suite
# =============================================================================
# The ISiK test suite seeds nothing: every scenario reads resources that must
# already exist in the SUT. The suite ships the *keys* (testdata/**.yaml) but
# states the required *content* only as prose inside each feature file's
# @Precondition / @vorbedingung scenario - 221 blocks across 220 files. That prose
# is the authoritative field-level specification for the data we fabricate, so it
# has to be in front of the author in full, and it has to be citable.
#
# This step turns it into three documents plus one machine-readable artefact.
# Everything is derived; nothing here is authored by hand:
#
#   docs/en/generated/REQUIREMENTS-Stufe3.md   per-module requirement tables with
#   docs/en/generated/REQUIREMENTS-Stufe5.md   the precondition prose quoted
#                                              verbatim and untranslated
#   docs/en/generated/RESOURCE-INVENTORY.md    the fabrication work list: every
#                                              resource that must exist, its ID,
#                                              FHIR type, required profile
#   vendor/requirements.json                   consumed by 50-seed.sh (which
#                                              refuses to seed an unknown key)
#                                              and by the gap report (which cites
#                                              the precondition prose on failure)
#
# Why it reads vendor/upstream/features rather than a downloaded tarball
# ---------------------------------------------------------------------
# 20-fetch-packages.sh extracts the feature files from the *pinned image* that
# will actually execute them. Quoting from anywhere else - a GitHub tarball of the
# same tag, say - would mean the requirement tables document a corpus that is only
# probably the one that ran. For a Bestätigungsverfahren submission that
# distinction is the whole difference between evidence and assertion, so this step
# hard-fails rather than falling back to a download.
#
# Why the tag expressions are passed in
# -------------------------------------
# Scope depends on them: a feature is "in scope" when at least one of its cases
# survives TESTS_TO_RUN. Reading them from .env.resolved rather than defaulting
# inside the extractor keeps one source of truth - the same strings the test
# containers receive - so a narrowed debugging run cannot produce documentation
# that claims the full certification scope.
# =============================================================================

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

step "Extracting test-data requirements from the feature files"

load_env
require_var TESTSUITE_IMAGE TESTSUITE_VERSION

FEATURES_DIR="${VENDOR_DIR}/upstream/features"
OUT_DIR="${REPO_ROOT}/docs/en/generated"
JSON_OUT="${VENDOR_DIR}/requirements.json"

[[ -d "$FEATURES_DIR" ]] || die "vendor/upstream/features is missing.
  It is extracted from ${TESTSUITE_IMAGE}:${TESTSUITE_VERSION} by
  scripts/20-fetch-packages.sh, which must run first (or just: make packages).
  This step deliberately does not download the feature files itself - the
  requirement tables must quote the corpus the pinned image executes."

nfeat="$(find "$FEATURES_DIR" -name '*.feature' | wc -l | tr -d ' ')"
[[ "$nfeat" -gt 0 ]] || die "no .feature files under ${FEATURES_DIR#$REPO_ROOT/}"

# Provenance line printed into every generated document. The image digest is what
# an auditor re-pulls to reproduce the quotes; the local extraction path says
# nothing and would leak this host's directory layout into the deliverable.
SOURCE_LABEL="${TESTSUITE_IMAGE}:${TESTSUITE_VERSION}"
if [[ -n "${TESTSUITE_IMAGE_DIGEST:-}" ]]; then
  SOURCE_LABEL+=" (${TESTSUITE_IMAGE_DIGEST}), /app/src/test/resources/features"
else
  # Only reachable when this is run by hand before `make resolve`. The documents
  # stay valid but their provenance is weaker, so say so in the document itself
  # rather than only in this log.
  warn "TESTSUITE_IMAGE_DIGEST is not set - provenance will name the tag only"
  SOURCE_LABEL+=" (digest not recorded), /app/src/test/resources/features"
fi

log "reading ${nfeat} feature files from ${FEATURES_DIR#$REPO_ROOT/}"

mkdir -p "$OUT_DIR"
python3 "${SCRIPTS_DIR}/lib/extract_requirements.py" \
    --features-root "$FEATURES_DIR" \
    --testdata-root "$(suite_testdata_root)" \
    --tiger-templates "$(suite_tiger_dir)" \
    --levels "$(suite_levels)" \
    --out-dir "$OUT_DIR" \
    --json-out "$JSON_OUT" \
    --source-label "$SOURCE_LABEL" \
    --tags-stufe5 "${TESTS_TO_RUN_STUFE5:?TESTS_TO_RUN_STUFE5 must be set in .env}" \
    ${TESTS_TO_RUN_STUFE3:+--tags-stufe3 "$TESTS_TO_RUN_STUFE3"} \
  || die "requirement extraction failed - see above"

# -----------------------------------------------------------------------------
# Report what still needs a human decision
# -----------------------------------------------------------------------------
# The extractor refuses to guess (see its module docstring: a quietly wrong test
# datum surfaces as an IRIS conformance failure, the one error this exercise must
# not make). What it cannot resolve it records, and those records are surfaced
# here rather than left in the JSON - an operator who never opens the inventory
# should still learn that N keys need their type read out of the prose.
python3 - "$JSON_OUT" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
for level, roll in data["levels"].items():
    print(f"  {level}: {roll['features']} features "
          f"({roll['featuresInScope']} with in-scope cases), "
          f"{roll['casesAssertive']} asserting / {roll['casesExecuted']} executed cases, "
          f"{len(roll['resources'])} resources to fabricate", file=sys.stderr)
    for label, items in (
        ("ID-shaped keys whose FHIR type is stated only in prose", roll["untypedIdKeys"]),
        ("keys referenced by a feature but defined nowhere", roll["keysUndefined"]),
        ("features with a malformed precondition tag", roll["malformedTags"]),
    ):
        if items:
            print(f"    {len(items)} {label}: {', '.join(sorted(items))}", file=sys.stderr)
PY

ok "wrote docs/en/generated/{REQUIREMENTS-Stufe3,REQUIREMENTS-Stufe5,RESOURCE-INVENTORY}.md"
ok "wrote vendor/requirements.json"
step "Test-data requirements extracted"
