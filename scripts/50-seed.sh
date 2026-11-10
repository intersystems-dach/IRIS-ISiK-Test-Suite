#!/usr/bin/env bash
# =============================================================================
# 50-seed.sh - build the test data, load it into both endpoints, prove it landed
# =============================================================================
# `make seed`. The ISiK test suite seeds nothing: every scenario reads resources
# that must already exist at exactly the IDs its testdata/**.yaml contract names.
# This step is therefore a precondition of the entire conformance run, and its
# correctness is load-bearing in a specific way - a missing or non-conformant
# seeded resource produces a failing scenario that is indistinguishable, in the
# official report, from an IRIS conformance gap. Avoiding that misattribution is
# what most of the checking below is for.
#
# Five stages, each of which can fail the step:
#
#   1. render     testdata/fhir/{common,Stufe*,extra} -> build/fhir/<level>/
#                 (scripts/lib/build_testdata.py: resolves ${<ns>.*} against the
#                 suite's own contract and stamps meta.profile from the profiles
#                 the feature files assert)
#   2. assert     every rendered resource against the assertions extracted from
#                 the feature files (scripts/lib/assertion_check.py)
#   3. refs       the search-scenario reference requirements no other check can
#                 see (scripts/lib/search_reference_check.py)
#   4. seed       PUT in dependency order, re-PUT to close the reference cycle,
#                 GET every resource back, POST $validate on every body
#                 (scripts/lib/seed_client.py)
#   5. record     run/seed/<level>.json + run/seed/SEED.txt, archived into the
#                 run's report directory by 70-collect-report.sh
#
# Stages 2 and 3 run on the host with no container involved, so a data defect
# costs seconds rather than a 45-minute bring-up followed by a misleading report.
# They are also run by `make check`, but repeated here because `make seed` may be
# invoked directly and because build/ is regenerated in stage 1 - checking the
# tree that was just rendered is the only check that covers what will be sent.
#
# Idempotent: PUT-with-ID overwrites, so re-running against a live endpoint is
# safe and is the intended way to reset the corpus after a scenario has mutated
# it. Several scenarios do mutate it (`*-Update-Original-Example`,
# `Patient-Delete-Example`), which is why `make test` depends on `seed` and why
# each level is seeded independently.
#
# Which levels those are is suite_levels(), never a literal: release 3.0.0
# removed ISiK Level 3 from the suite, and seeding a level the suite does not
# carry would write a corpus no scenario reads - harmless in itself, but it would
# appear in the seed record as data the round depended on.
# =============================================================================

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

step "Seeding the test data"

load_env
# TESTSUITE_VERSION selects which levels exist; the two FHIR_BASE_* variables
# describe the system under test, which installs both endpoints regardless.
require_var TESTSUITE_VERSION SUT_USER SUT_PASSWORD \
            FHIR_BASE_STUFE3 FHIR_BASE_STUFE5

need_cmd python3
need_cmd curl

LIB="${SCRIPTS_DIR}/lib"
BUILD_DIR="${REPO_ROOT}/build/fhir"
SEED_DIR="${RUN_DIR}/seed"
SEED_LOG="${SEED_DIR}/SEED.txt"

[[ -f "${VENDOR_DIR}/requirements.json" ]] || die "vendor/requirements.json is missing - run 'make packages' first
  The seed corpus is rendered against the suite's own test-data contract, which
  is extracted from the pinned image by scripts/25-extract-requirements.sh."

mkdir -p "$SEED_DIR"

# -----------------------------------------------------------------------------
# 1. Render
# -----------------------------------------------------------------------------
# build/ is removed first rather than overwritten: a resource dropped from the
# inventory by a test-suite version bump would otherwise stay behind and be seeded
# for a level that no longer wants it.
step "1/5 Rendering the authored test data"

rm -rf "${REPO_ROOT}/build/fhir"
# One --src per authored tree, shared tree first: a 3.x round adds
# testdata/fhir/v3 for the resources release 3.0.0 introduced, and the shared
# bodies stay where they are rather than being copied per generation.
src_args=()
for root in $(suite_fhir_roots); do src_args+=(--src "$root"); done

python3 "${LIB}/build_testdata.py" --packages "$PACKAGES_DIR" \
  --levels "$(suite_levels)" "${src_args[@]}" \
  || die "test-data rendering failed - see above.
  An unresolved \${<ns>.*} placeholder means the suite's contract does not define
  that key in that namespace at this level; an unresolved {{profile.*}} means no
  loaded ISiK package defines that profile name."

# -----------------------------------------------------------------------------
# 2. Assertion check
# -----------------------------------------------------------------------------
# Against the assertions the feature files make about each resource, at the four
# honest strength tiers assertion_check.py documents. The UNVERIFIED count is
# printed rather than suppressed: it is the measure of how much of the
# specification this static check does not cover.
step "2/5 Checking the rendered data against the suite's assertions"

assert_failed=0
for level in $(suite_levels); do
  python3 "${LIB}/assertion_check.py" --level "$level" --data-root "$BUILD_DIR" \
      --json-report "${SEED_DIR}/assertions-${level}.json" \
    || assert_failed=1
done
(( assert_failed == 0 )) || die "the rendered test data does not satisfy the suite's own assertions - see above.
  Fix the authored source under testdata/fhir/ and re-run. Seeding data that is
  known not to satisfy an assertion would produce a failing scenario that reads
  as an IRIS gap."

# -----------------------------------------------------------------------------
# 3. Search-reference check
# -----------------------------------------------------------------------------
# The requirement class assertion_check.py cannot see: a search filtered by a
# reference needs at least one seeded resource carrying that reference, or the
# scenario fails at `entry.resource.count() > 0` and reads as an IRIS search
# defect. See the module docstring for why `all()` over the result set is vacuous.
step "3/5 Checking the search-scenario reference requirements"

# TESTS_TO_RUN_STUFE3 is passed only when the suite carries Stufe 3; under a 3.x
# suite the variable may legitimately be unset, and `:?` would abort the step.
python3 "${LIB}/search_reference_check.py" --data-root "$BUILD_DIR" \
    --levels "$(suite_levels)" \
    --tags-stufe5 "${TESTS_TO_RUN_STUFE5:?}" \
    ${TESTS_TO_RUN_STUFE3:+--tags-stufe3 "$TESTS_TO_RUN_STUFE3"} \
    --json-report "${SEED_DIR}/search-references.json" \
  || die "a search scenario requires a reference no seeded resource carries - see above"

# -----------------------------------------------------------------------------
# 4. Seed
# -----------------------------------------------------------------------------
# Each level into its own endpoint. Deliberately sequential: the two endpoints
# share one IRIS instance, and a parallel seed would interleave in the log, which
# is the artefact an inspector reads to see what was loaded.
step "4/5 Writing the corpus into both endpoints"

SEED_VALIDATE="${SEED_VALIDATE:-1}"
SEED_VALIDATE_STRICT="${SEED_VALIDATE_STRICT:-1}"

# if/fi rather than `[[ ... ]] && ...`: lib.sh sets -e, under which a standalone
# `test && command` whose test is false aborts the script.
validate_args=()
if [[ "$SEED_VALIDATE" != 1 ]]; then
  validate_args+=(--no-validate)
fi
if [[ "$SEED_VALIDATE_STRICT" == 1 ]]; then
  validate_args+=(--strict)
fi

if [[ "$SEED_VALIDATE" != 1 ]]; then
  warn "SEED_VALIDATE=0: seeded resources will NOT be validated.
  A non-conformant resource then surfaces during the run as an IRIS conformance
  failure. Use only when debugging the seeder itself."
fi

seed_failed=0
for level in $(suite_levels); do
  stufe="$(printf '%s' "$level" | tr '[:upper:]' '[:lower:]')"
  base="$(sut_base_url_host "$stufe")"
  log "${level} -> ${base}"

  # The password reaches the client through the environment, never on the command
  # line - the way a production FHIR client would be configured, rather than
  # because the value is confidential here (it is documented on purpose; see
  # run/CREDENTIALS.md).
  if SUT_USER="$SUT_USER" SUT_PASSWORD="$SUT_PASSWORD" \
     python3 "${LIB}/seed_client.py" \
        --level "$level" \
        --base-url "$base" \
        --data-root "$BUILD_DIR" \
        --out "${SEED_DIR}/${level}.json" \
        "${validate_args[@]}"; then
    ok "${level} seeded"
  else
    seed_failed=1
    warn "${level} seeding reported a failure - details above and in run/seed/${level}.json"
  fi
done

# -----------------------------------------------------------------------------
# 5. Record
# -----------------------------------------------------------------------------
# A short human-readable summary alongside the JSON. The JSON is the evidence;
# this is what gets pasted into a ticket, and what `make status` and the results
# documentation quote.
step "5/5 Recording what was seeded"

{
  printf 'ISiK test-data seed record\n'
  printf 'generated: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'run id:    %s\n' "$(run_id)"
  printf 'config:    %s\n' "${ENV_LOADED_FROM#$REPO_ROOT/}"
  printf 'validate:  SEED_VALIDATE=%s SEED_VALIDATE_STRICT=%s\n\n' \
    "$SEED_VALIDATE" "$SEED_VALIDATE_STRICT"
} > "$SEED_LOG"

python3 - "$SEED_DIR" $(suite_levels) >> "$SEED_LOG" <<'PY'
import json
import pathlib
import sys

seed_dir = pathlib.Path(sys.argv[1])
for level in sys.argv[2:]:
    path = seed_dir / f"{level}.json"
    if not path.is_file():
        print(f"{level}: not seeded (no record written)\n")
        continue
    rec = json.loads(path.read_text(encoding="utf-8"))
    caps, c = rec["capability"], rec["counts"]
    print(f"{level}")
    print(f"  endpoint      {rec['baseUrl']}")
    print(f"  server        {caps.get('software') or '?'}, "
          f"FHIR {caps.get('fhirVersion') or '?'}")
    print(f"  resources     {len(rec['resources'])}")
    print(f"  written       {c['written']}  (failed: {c['writeFailed']})")
    if c["rewritten"]:
        print(f"  re-written    {c['rewritten']}  "
              f"(second pass, to close a reference cycle)")
    print(f"  read back     {c['readBack']}  (failed: {c['readFailed']})")
    print(f"  $validate     {c['validated']} validated, "
          f"{c['validationErrors']} with error-severity issues, "
          f"{c['validationSkipped']} not validated")

    # update-as-create is what PUT-with-ID depends on. Recorded per level because
    # it is the endpoint's own claim, and a false claim here explains every
    # subsequent 404 at once.
    uc = caps.get("updateCreate") or {}
    types = sorted({r["resourceType"] for r in rec["resources"]})
    without = [t for t in types if uc.get(t) is not True]
    if without:
        print(f"  updateCreate  not declared for: {', '.join(without)}")
    else:
        print(f"  updateCreate  declared for all {len(types)} seeded types")

    bad = [r for r in rec["resources"]
           if (r.get("validate") or {}).get("errors")]
    if bad:
        print(f"\n  TEST DATA DEFECTS ({len(bad)} resource(s)) - our data, not IRIS:")
        for r in bad:
            print(f"    {r['key']}  ({r['file']})")
            for issue in r["validate"]["errors"][:5]:
                where = f" at {issue['expression']}" if issue["expression"] else ""
                print(f"      {issue['severity']} {issue['code']}{where}: {issue['text']}")

    failed = [r for r in rec["resources"]
              if r.get("write", {}).get("status") not in (200, 201)
              or not r.get("read", {}).get("ok")]
    if failed:
        print(f"\n  NOT SEEDED ({len(failed)} resource(s)):")
        for r in failed:
            w = r.get("write") or {}
            rd = r.get("read") or {}
            # The detail of the pass that actually failed. Taking the write's
            # first would print its success summary for a resource that was
            # written fine and then could not be read back - which is precisely
            # the case worth reporting clearly.
            detail = (w.get("detail") if w.get("status") not in (200, 201)
                      else rd.get("detail")) or ""
            print(f"    {r['key']}  PUT {w.get('status', '-')} "
                  f"GET {rd.get('status', '-')}  {detail}")
    print()
PY

cat "$SEED_LOG" >&2

if (( seed_failed )); then
  die "seeding did not complete cleanly - see run/seed/SEED.txt.
  A conformance run against an incomplete or non-conformant corpus would report
  our own data defects as IRIS gaps, so this is a hard stop. Set
  SEED_VALIDATE_STRICT=0 in .env to proceed anyway with the defects recorded."
fi

ok "test data seeded and verified in $(suite_levels | wc -w | tr -d ' ') endpoint(s): $(suite_levels)"
log "record: run/seed/SEED.txt and one JSON per level ($(suite_levels))"
step "Seeding complete"
