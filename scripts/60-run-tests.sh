#!/usr/bin/env bash
# =============================================================================
# 60-run-tests.sh <stufe3|stufe5> - run one conformance level and archive its report
# =============================================================================
# `make test-stufe3` / `make test-stufe5`. One invocation runs exactly one ISiK
# specification level, because the upstream image does: its entrypoint is
#
#     rm -rf $REPORT_DIR/* ; mvn clean verify -ntp -P${MAVEN_PROFILE} \
#         -DTESTS_TO_RUN="${TESTS_TO_RUN}" || true ; \
#         mv -v $APP_HOME/target/*report.zip $REPORT_DIR/
#
# Three consequences of that one line shape everything below.
#
#   1. `|| true`. Maven's exit status is discarded, and pom.xml sets
#      testFailureIgnore=false per profile. The container therefore exits 0
#      whether all scenarios passed, all of them failed, or Maven never reached a
#      single scenario, so the container's exit code says nothing about the result
#      and is recorded but never acted on.
#
#      The presence of the archives is not a sufficient success signal either, and
#      that cost a round here: `mv` runs unconditionally too, so a run that died
#      in Tiger's startup still ships a well-formed, checksummed, `unzip -t`-clean
#      test-report.zip - containing nothing but pom.xml. Section 4b therefore
#      reads what is INSIDE the archive. See its comment for why the check counts
#      Serenity outcomes rather than looking at Maven's build status: with
#      testFailureIgnore=false, `BUILD FAILURE` is the *expected* outcome of a
#      failing conformance run and cannot distinguish the two cases.
#
#   2. `rm -rf $REPORT_DIR/*` at the START of every run. The report volume holds
#      exactly one level's output at a time, so each run's archives are lifted out
#      before the next level starts. That is why `make test` serialises the two
#      levels and why this script archives before it returns.
#
#   3. The reports are assembled by Maven's post-integration-test phase. A run
#      that is killed part-way produces no archive at all - not a partial one - so
#      the timeout path stops the container gracefully and reports honestly that
#      there is nothing to archive.
#
# A FAILING CONFORMANCE RUN IS THE EXPECTED OUTCOME of this project: IRIS for
# Health is known not to be fully ISiK compliant. This script therefore exits 0
# for a run in which every scenario failed, and non-zero only for a harness error
# - a missing report archive, or one that carries no test outcomes, either of
# which means there is no evidence to analyse. `make test` must not stop before
# `make report`.
#
# Outputs, under reports/<run-id>/<stufe>/:
#   official/*.zip      the report archives, byte-for-byte as the suite wrote them
#   official/SHA256SUMS the checksums an inspector re-computes
#   run.log             the container's complete stdout/stderr, verbatim
#   run.json            what was run: images + digests, tag expression, timings,
#                       the seed record's summary, and whether the tag expression
#                       was the official certification scope
#
# 70-collect-report.sh extracts and cross-references these; nothing here
# interprets a result.
# =============================================================================

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

STUFE="${1:-}"
case "$STUFE" in
  stufe3) LEVEL=Stufe3 ;;
  stufe5) LEVEL=Stufe5 ;;
  *) die "usage: 60-run-tests.sh <stufe3|stufe5>" ;;
esac

step "Running the ISiK ${LEVEL#Stufe} conformance suite"

load_env
require_var TESTSUITE_IMAGE TESTSUITE_VERSION TESTSUITE_IP IRIS_IP \
            SUT_USER SUT_PASSWORD IRIS_MGMT_USER IRIS_MGMT_PASSWORD

need_cmd docker
need_cmd python3
# For the archive checks in section 4 (`unzip -t`, CRC integrity) and 4b
# (`unzip -Z1`, the outcome-record count). Both are verdict inputs, so a missing
# unzip must stop the round here rather than three hours later.
need_cmd unzip

PROJECT="${COMPOSE_PROJECT_NAME:-isik}"
SERVICE="testsuite-${STUFE}"
CONTAINER="${PROJECT}-testsuite-${STUFE}"
VOLUME="${PROJECT}-tiger-report"

ARCHIVE_DIR="$(run_report_dir)/${STUFE}"
OFFICIAL_DIR="${ARCHIVE_DIR}/official"
RUN_LOG="${ARCHIVE_DIR}/run.log"
RUN_META="${ARCHIVE_DIR}/run.json"

# Wall-clock ceiling for one level. Generous: Stufe 5 is the larger of the two and
# a cold Maven cache adds several minutes before the first scenario runs.
RUN_TIMEOUT="${TESTSUITE_TIMEOUT:-10800}"

# -----------------------------------------------------------------------------
# 1. Preconditions
# -----------------------------------------------------------------------------
# All checked before the container starts. A run that begins against an
# unseeded endpoint wastes an hour and then reports our own missing test data as
# a wall of IRIS conformance gaps - the one misattribution this project exists to
# avoid.

for f in "${RUN_DIR}/tiger/tiger-isik-stufe3.yaml" "${RUN_DIR}/tiger/tiger-isik-stufe5.yaml" \
         "${RUN_DIR}/testdata"; do
  [[ -e "$f" ]] || die "${f#$REPO_ROOT/} is missing - run 'make up' (scripts/30-render-config.sh renders it)"
done

if ! docker inspect "${PROJECT}-iris" --format '{{.State.Status}}' 2>/dev/null | grep -q running; then
  die "the unit under test (${PROJECT}-iris) is not running - run 'make up' first"
fi

SEED_RECORD="${RUN_DIR}/seed/${LEVEL}.json"
[[ -f "$SEED_RECORD" ]] \
  || die "run/seed/${LEVEL}.json is missing - run 'make seed' first.
  The suite seeds nothing: every scenario reads resources that must already exist
  at the IDs its testdata contract names."

# The seed record is the evidence that the corpus is complete and conformant. It
# is inspected rather than trusted, and its verdict is carried into run.json so
# the report says which corpus the run started from.
seed_summary="$(python3 - "$SEED_RECORD" <<'PY' || true
import json, sys
with open(sys.argv[1], encoding="utf-8") as fh:
    rec = json.load(fh)
c = rec["counts"]
bad = c["writeFailed"] + c["readFailed"]
print(json.dumps({
    "resources": len(rec["resources"]),
    "written": c["written"], "readBack": c["readBack"],
    "validated": c["validated"], "validationErrors": c["validationErrors"],
    "validationSkipped": c["validationSkipped"],
    "incomplete": bad,
    "endpoint": rec["baseUrl"],
    "server": rec.get("capability", {}).get("software"),
}))
PY
)"
[[ -n "$seed_summary" ]] || die "run/seed/${LEVEL}.json is not readable as a seed record - re-run 'make seed'"

seed_incomplete="$(printf '%s' "$seed_summary" | python3 -c 'import json,sys; print(json.load(sys.stdin)["incomplete"])')"
seed_invalid="$(printf '%s' "$seed_summary" | python3 -c 'import json,sys; print(json.load(sys.stdin)["validationErrors"])')"

(( seed_incomplete == 0 )) \
  || die "the ${LEVEL} corpus is incomplete: ${seed_incomplete} resource(s) were not written or not readable.
  See run/seed/SEED.txt. Running now would report our missing test data as IRIS gaps."

if (( seed_invalid > 0 )); then
  # Reachable only with SEED_VALIDATE_STRICT=0, which is an explicit operator
  # decision. Not blocked here - blocking would override that decision - but said
  # out loud, and recorded in run.json so the results documentation can qualify
  # the affected scenarios rather than counting them as IRIS gaps.
  warn "${seed_invalid} seeded resource(s) failed \$validate (SEED_VALIDATE_STRICT=0 was set).
  These are TEST DATA defects. Scenarios that read them may fail for reasons that
  are ours, not IRIS's; run.json records this so the analysis can say so."
fi

# --- tag expression ----------------------------------------------------------
# The tag expression decides the scope, and the scope decides whether the run is a
# conformance run at all. Both facts are established here, before anything starts.
case "$STUFE" in
  stufe3) TESTS_TO_RUN="${TESTS_TO_RUN_STUFE3:-@Stufe3 and (not @Optional)}" ;;
  stufe5) TESTS_TO_RUN="${TESTS_TO_RUN_STUFE5:-@Stufe5 and (not @Optional)}" ;;
esac

# A tag expression that does not name this level selects nothing, and the symptom
# is a report full of zeroes with no error anywhere - so it is a hard stop rather
# than a warning.
WANT_TAG="@${LEVEL}"
[[ "$TESTS_TO_RUN" == *"$WANT_TAG"* ]] \
  || die "TESTS_TO_RUN_${LEVEL^^} is '${TESTS_TO_RUN}' but does not mention ${WANT_TAG}.
  Maven would run the ${STUFE} profile while the tag filter selected nothing, and
  the report would be empty with no error reported anywhere."

# The official certification scope, as it appears in the upstream pom.xml profile
# defaults. Anything else is a debugging run and must not be submitted.
OFFICIAL_SCOPE="${WANT_TAG} and (not @Optional)"
if [[ "$TESTS_TO_RUN" == "$OFFICIAL_SCOPE" ]]; then
  CONFORMANCE_SCOPE=true
else
  CONFORMANCE_SCOPE=false
  warn "TESTS_TO_RUN is '${TESTS_TO_RUN}', not the official scope '${OFFICIAL_SCOPE}'.
  This run is a DEBUGGING run: its report is not a conformance result and must not
  be submitted for the Bestaetigungsverfahren. run.json records conformanceScope:
  false and the generated documentation states it."
fi

mkdir -p "$OFFICIAL_DIR"

log "run id      $(run_id)"
log "level       ISiK ${LEVEL#Stufe} (Maven profile ${STUFE})"
log "tags        ${TESTS_TO_RUN}"
log "SUT         $(sut_base_url "$STUFE")"
log "suite       ${TESTSUITE_IMAGE}:${TESTSUITE_VERSION}"
log "corpus      $(printf '%s' "$seed_summary" | python3 -c '
import json, sys
# Values into locals first: a backslash inside an f-string expression is a syntax
# error before Python 3.12, and the deployment host may be older than that.
d = json.load(sys.stdin)
print(str(d["resources"]) + " resources seeded, " + str(d["validated"]) + " validated")')"
log "archive     ${ARCHIVE_DIR#$REPO_ROOT/}"

# -----------------------------------------------------------------------------
# 2. Clear the way
# -----------------------------------------------------------------------------
# An exited container still holds its network endpoint until it is removed, and
# all three test suite services share one static address, so a container left
# behind by an earlier level or an interrupted round makes `compose up` fail with
# "Address already in use". Removing them here is safe because each level's
# archives are lifted out before this script returns.
for stale in "${PROJECT}-testsuite-stufe3" "${PROJECT}-testsuite-stufe5" \
             "${PROJECT}-testsuite-manual"; do
  if docker inspect "$stale" >/dev/null 2>&1; then
    log "removing the previous test suite container ${stale}"
    docker rm -f "$stale" >/dev/null 2>&1 || true
  fi
done

# -----------------------------------------------------------------------------
# 3. Run
# -----------------------------------------------------------------------------
# TESTS_TO_RUN is exported so compose resolves it into the service environment.
# Passing it explicitly rather than relying on the compose default keeps one
# source of truth for the value that is also written into run.json.
case "$STUFE" in
  stufe3) export TESTS_TO_RUN_STUFE3="$TESTS_TO_RUN" ;;
  stufe5) export TESTS_TO_RUN_STUFE5="$TESTS_TO_RUN" ;;
esac

STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
started_epoch="$(date +%s)"

step "Starting ${CONTAINER}"
compose --profile "$STUFE" up -d "$SERVICE" \
  || die "could not start ${CONTAINER} - see the output above.
  If compose reports the address is in use, remove leftover containers with
  'docker rm -f ${PROJECT}-testsuite-stufe3 ${PROJECT}-testsuite-stufe5'."

# The container's output is the primary human-readable record of the run and the
# only place a Tiger startup failure is explained, so it is captured in full from
# the start. Streamed to a file rather than to the terminal: this runs for up to
# three hours, usually detached, and the file is what gets archived.
docker logs -f "$CONTAINER" >"$RUN_LOG" 2>&1 &
LOG_PID=$!
# shellcheck disable=SC2064  # LOG_PID must be expanded now, not at trap time
trap "kill ${LOG_PID} 2>/dev/null || true" EXIT

log "streaming the container log to ${RUN_LOG#$REPO_ROOT/}"
log "follow it with: tail -F ${RUN_LOG#$REPO_ROOT/}"
log "Tiger WorkflowUI: http://localhost:${TESTSUITE_PORT_WORKFLOW_UI:-9010}"

step "Waiting for the run to finish (expect tens of minutes)"

TIMED_OUT=false
EXIT_CODE=''
last_report=0
POLL_INTERVAL=15
# Set once if the scenario JVM runs out of heap. Reported, not acted on - see the
# progress block below for why this warns instead of aborting.
OOM_SEEN=false
# How often to print a progress line. The wait is long and silent; without this a
# healthy run is indistinguishable from a hung one on a remote host.
REPORT_INTERVAL=120

while :; do
  now="$(date +%s)"
  elapsed=$(( now - started_epoch ))

  state="$(docker inspect "$CONTAINER" --format '{{.State.Status}}' 2>/dev/null || echo gone)"
  case "$state" in
    exited|dead)
      EXIT_CODE="$(docker inspect "$CONTAINER" --format '{{.State.ExitCode}}' 2>/dev/null || echo '?')"
      ok "the suite finished after $(( elapsed / 60 ))m $(( elapsed % 60 ))s (container exit ${EXIT_CODE})"
      break ;;
    gone)
      # Removed from underneath us. The report volume survives - it is named - so
      # the archive step can still succeed, and that is worth attempting rather
      # than failing outright.
      warn "container ${CONTAINER} disappeared; attempting to archive from the report volume anyway"
      EXIT_CODE='gone'
      break ;;
  esac

  if (( elapsed > RUN_TIMEOUT )); then
    TIMED_OUT=true
    warn "exceeded TESTSUITE_TIMEOUT=${RUN_TIMEOUT}s after $(( elapsed / 60 )) minutes"
    # `docker stop` sends SIGTERM and waits out stop_grace_period (120s in
    # compose/testsuite.yml). Maven may still write the archives if it happens to
    # be in post-integration-test; if it is not, there will be no archive at all,
    # because the zips are assembled at the end rather than incrementally.
    warn "stopping the container gracefully - the report is assembled at the END of a run, so a killed run usually yields no archive"
    docker stop -t 150 "$CONTAINER" >/dev/null 2>&1 || true
    EXIT_CODE="$(docker inspect "$CONTAINER" --format '{{.State.ExitCode}}' 2>/dev/null || echo '?')"
    break
  fi

  if (( elapsed - last_report >= REPORT_INTERVAL )); then
    last_report=$elapsed
    # The last non-empty log line: version-independent, and enough to tell a run
    # that is progressing from one that has stopped saying anything. Truncated so
    # a Maven stack trace does not swamp the progress log.
    tail_line="$(grep -v '^[[:space:]]*$' "$RUN_LOG" 2>/dev/null | tail -n1 | cut -c1-140 || true)"
    log "[$(printf '%3d' $(( elapsed / 60 )))m] $(wc -l <"$RUN_LOG" | tr -d ' ') log lines | ${tail_line}"

    # Heap exhaustion in the scenario JVM, said out loud the first time it appears.
    #
    # This is the one failure mode the progress line above cannot convey. When the
    # heap fills, Tiger's Rbel parser threads die and every subsequent scenario
    # blocks for the full 100-second parse timeout in
    # RbelMessageHistory.waitForGivenMessagesToBeParsed. The log keeps growing and
    # the tail line keeps changing, so the run looks healthy - it is simply ~25x
    # too slow to finish, and it will hit TESTSUITE_TIMEOUT, which yields no
    # archive at all. Measured on the lab host at ~600 of Stufe 5's 895 cases.
    #
    # Warn rather than abort: the operator may still prefer a partial run, and
    # aborting a conformance run on a log pattern is a decision this script should
    # not take on its own. What it must not do is stay silent for the hours it
    # takes the timeout to arrive.
    if [[ "$OOM_SEEN" == false ]] && grep -q 'OutOfMemoryError' "$RUN_LOG" 2>/dev/null; then
      OOM_SEEN=true
      warn "OutOfMemoryError in the scenario JVM. This run will not finish in time: Tiger's Rbel parser threads are dead, so every remaining scenario will burn a 100-second parse timeout and the run will hit TESTSUITE_TIMEOUT, which produces NO archive."
      warn "remedy: stop this run, raise TESTSUITE_HEAP_MAX (currently '${TESTSUITE_HEAP_MAX:-unset, i.e. the JVM default of 1/4 of host RAM}') in .env, and run this level again. See .env.example for the measurement."
    fi
  fi

  sleep "$POLL_INTERVAL"
done

kill "$LOG_PID" 2>/dev/null || true
trap - EXIT
# A short settle: `docker logs -f` exits on its own when the container stops, but
# the last few lines can still be in flight, and those lines are the ones that say
# where Maven moved the archives.
sleep 2

FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
DURATION=$(( $(date +%s) - started_epoch ))

# -----------------------------------------------------------------------------
# 4. Archive the report, verbatim
# -----------------------------------------------------------------------------
# Immediately, and before anything else runs: the next container start wipes
# $REPORT_DIR. The bytes are copied unmodified - the archives are the artefact the
# Bestaetigungsverfahren is based on, and nothing here may touch them.
step "Archiving the official report"

# Primary route: docker cp resolves the container's mount points, so it reaches
# into the named volume even though the container has exited.
if docker cp "${CONTAINER}:/app/report/." "${OFFICIAL_DIR}/" 2>/dev/null; then
  log "copied /app/report from ${CONTAINER}"
else
  # Fallback for a container that no longer exists, or a docker version that
  # declines to cp from a stopped container's volume. A throwaway container is
  # created (never started) with the report volume attached, purely as a handle
  # for docker cp. The test suite image is used because it is certainly present
  # locally - pulling anything else here could fail on an air-gapped host.
  warn "docker cp from ${CONTAINER} failed; reading the report volume directly"
  helper="$(docker create -v "${VOLUME}:/report" \
      --entrypoint /bin/true "${TESTSUITE_IMAGE}:${TESTSUITE_VERSION}" 2>/dev/null || true)"
  if [[ -n "$helper" ]]; then
    docker cp "${helper}:/report/." "${OFFICIAL_DIR}/" 2>/dev/null \
      || warn "could not read the report volume ${VOLUME} either"
    docker rm -f "$helper" >/dev/null 2>&1 || true
  else
    warn "could not create a helper container to read ${VOLUME}"
  fi
fi

# Classify what arrived. The archive names come from the upstream Maven assembly
# descriptors and carry the project version, so they are matched by substring
# rather than by an exact name that a suite release could change.
shopt -s nullglob
zips=("${OFFICIAL_DIR}"/*.zip)
shopt -u nullglob

test_zip=''
debug_zip=''
for z in "${zips[@]}"; do
  case "$(basename "$z")" in
    *test-report*.zip)  test_zip="$z" ;;
    *debug-report*.zip) debug_zip="$z" ;;
  esac
done

for z in "${zips[@]}"; do
  size="$(du -h "$z" | cut -f1)"
  # An archive Maven was still writing when the container stopped is worse than a
  # missing one: it looks like evidence and is not. unzip -t reads the central
  # directory and every entry's CRC, which is exactly the check for that.
  if unzip -t -q "$z" >/dev/null 2>&1; then
    ok "$(basename "$z")  ${size}  (archive intact)"
  else
    warn "$(basename "$z")  ${size}  ** CORRUPT - the run was probably interrupted **"
  fi
done

# Checksums over the archives as received, so the results documentation can cite
# them and an inspector can re-compute them without trusting this script.
if (( ${#zips[@]} > 0 )); then
  ( cd "$OFFICIAL_DIR" && for z in *.zip; do
      printf '%s  %s\n' "$(sha256_of "$z")" "$z"
    done ) >"${OFFICIAL_DIR}/SHA256SUMS"
fi

# -----------------------------------------------------------------------------
# 4b. Did the suite actually run anything?
# -----------------------------------------------------------------------------
# A well-formed archive is not a result. `make test-stufe3` once returned in 21
# seconds with "OK test-report.zip 4.0K (archive intact)" over an archive holding
# nothing but pom.xml: the rendered tiger-isik-stufe3.yaml was mode 0600 and the
# suite container runs as uid 100, so Tiger died reading its own configuration
# before a single scenario started. The entrypoint's `mv` is not conditional on
# Maven having succeeded, so the empty archive was produced, moved, hashed and
# accepted. THAT round is the reason this section exists.
#
# Why the check counts Serenity outcomes rather than reading Maven's verdict:
#
#   - `BUILD FAILURE` cannot be the trigger. The upstream profiles set
#     testFailureIgnore=false, so failsafe's `verify` goal fails the build as soon
#     as one scenario fails - which is the EXPECTED outcome of this project. A run
#     with 400 genuine conformance failures and a run that never started both say
#     BUILD FAILURE.
#
#   - `Tests run: 0` is a real signal but a fragile one: it is one line of one
#     plugin's console output, in a wording that belongs to failsafe rather than to
#     the certification artefact, and a `-ntp` log is not the evidence base.
#
# The archive's own contents are neither. zip-test-report.xml (upstream
# src/test/assembly/) collects exactly three things: unresolvedReports/*.json,
# serenity-summary.html and pom.xml. The first are Serenity's TestOutcome records,
# and the same files results_report.py counts every verdict from. Zero of them
# means the report documents nothing, whatever Maven printed. So the question asked
# here is the same question the analysis will ask, asked early enough to be
# actionable, and answered from the artefact rather than from a log line.
#
# A record is NOT a test case, and the difference is large enough to alarm someone
# who expects otherwise. Serenity writes one record per scenario OR per scenario
# OUTLINE, with an outline's example rows carried inside that record's dataTable and
# each row holding its own verdict. Measured on the first real Stufe 3 round:
#
#     365 plain scenarios + 27 outlines            = 392 records
#     365 plain scenarios + 141 outline example rows = 506 test cases
#
# 506 being exactly the level's expected scope. So this check deliberately tests
# only for ZERO, which is unambiguous, and does not compare the count against an
# expected case count - that comparison belongs to results_report.py, which knows
# how to descend into dataTable. Reading 392 here as "114 cases missing" would be
# wrong.
step "Verifying the archive documents a run"

outcome_count=''
if [[ -n "$test_zip" ]]; then
  # `unzip -Z1` lists the archive without extracting. Counted with grep -c over the
  # listing rather than `unzip -l | tail`, so a filename containing a space or a
  # localised summary line cannot shift the number. || true: grep -c exits 1 on
  # zero matches, which is a legitimate answer here and not an error.
  outcome_count="$(unzip -Z1 "$test_zip" 2>/dev/null \
                     | grep -c -E '(^|/)unresolvedReports/.+\.json$' || true)"
  outcome_count="${outcome_count:-0}"
  log "test-report.zip carries ${outcome_count} Serenity outcome record(s)"

  if (( outcome_count == 0 )); then
    # The two facts that name the cause, quoted from the log rather than guessed.
    # Both were plainly present in the run that motivated this check and unread.
    warn "the archive contains no test outcomes; the log lines that name the cause:"
    grep -n -E 'Tests run:|BUILD FAILURE|\[ERROR\]' "$RUN_LOG" 2>/dev/null \
      | head -n 12 | sed 's/^/    /' >&2 || true
    warn "container log tail:"
    tail -n 20 "$RUN_LOG" 2>/dev/null | sed 's/^/    /' >&2 || true
  fi
fi

# -----------------------------------------------------------------------------
# 5. Record what happened
# -----------------------------------------------------------------------------
# The provenance record for this level. Written whether or not the run produced
# an archive, because a run that produced nothing is itself a fact the round has
# to account for.
step "Recording the run"

# Every value is passed through the environment, not interpolated into the Python
# source. A tag expression contains parentheses and quotes, and a Tiger error
# message can contain anything at all; substituting those into source would be one
# stray quote away from a syntax error in the step that writes the evidence.
M_RUN_ID="$(run_id)" \
M_LEVEL="$LEVEL" M_STUFE="$STUFE" \
M_STARTED="$STARTED_AT" M_FINISHED="$FINISHED_AT" M_DURATION="$DURATION" \
M_TIMED_OUT="$TIMED_OUT" M_EXIT="$EXIT_CODE" \
M_TAGS="$TESTS_TO_RUN" M_OFFICIAL_SCOPE="$OFFICIAL_SCOPE" \
M_CONFORMANCE="$CONFORMANCE_SCOPE" \
M_SUT_URL="$(sut_base_url "$STUFE")" M_SUT_URL_HOST="$(sut_base_url_host "$STUFE")" \
M_IRIS_IMAGE="${IRIS_IMAGE}:${IRIS_VERSION}" \
M_IRIS_DIGEST="${IRIS_IMAGE_DIGEST:-}" M_IRIS_BUILD="${IRIS_BUILD_VERSION:-}" \
M_IRIS_VERSION_SOURCE="${IRIS_VERSION_SOURCE:-}" \
M_NAMESPACE="$([[ "$STUFE" == stufe3 ]] && printf '%s' "${IRIS_NS_STUFE3:-}" || printf '%s' "${IRIS_NS_STUFE5:-}")" \
M_SUITE_IMAGE="${TESTSUITE_IMAGE}:${TESTSUITE_VERSION}" \
M_SUITE_DIGEST="${TESTSUITE_IMAGE_DIGEST:-}" M_SUITE_IP="$TESTSUITE_IP" \
M_SUITE_VERSION_SOURCE="${TESTSUITE_VERSION_SOURCE:-}" \
M_SUITE_LATEST="${TESTSUITE_LATEST_AVAILABLE:-}" \
M_SUITE_HEAP="${TESTSUITE_HEAP_MAX:-}" \
M_PACKAGE_SOURCE="${PACKAGE_SOURCE:-refv}" M_MODE="$MODE" \
M_OVERRIDES="${ENV_OVERRIDES:-}" \
M_SUT_USER="$SUT_USER" M_SUT_PASSWORD="$SUT_PASSWORD" \
M_MGMT_USER="$IRIS_MGMT_USER" M_MGMT_PASSWORD="$IRIS_MGMT_PASSWORD" \
M_SEED="$seed_summary" \
M_OFFICIAL_DIR="$OFFICIAL_DIR" \
M_TEST_ZIP="$(basename "${test_zip:-}" 2>/dev/null || true)" \
M_DEBUG_ZIP="$(basename "${debug_zip:-}" 2>/dev/null || true)" \
M_OUTCOMES="${outcome_count:-}" \
python3 - "$RUN_META" <<'PY'
import json
import os
import pathlib
import sys

env = os.environ
official = pathlib.Path(env["M_OFFICIAL_DIR"])

rec = {
    "runId": env["M_RUN_ID"],
    "level": env["M_LEVEL"],
    "mavenProfile": env["M_STUFE"],
    "startedAt": env["M_STARTED"],
    "finishedAt": env["M_FINISHED"],
    "durationSeconds": int(env["M_DURATION"]),
    "timedOut": env["M_TIMED_OUT"] == "true",
    # Recorded, never acted on: the upstream entrypoint ends in `|| true`, so this
    # is 0 for a run in which every scenario failed.
    "containerExitCode": env["M_EXIT"],
    "testsToRun": env["M_TAGS"],
    "officialScope": env["M_OFFICIAL_SCOPE"],
    # false marks a narrowed, debugging run whose report is not a conformance
    # result. The generated documentation states this explicitly.
    "conformanceScope": env["M_CONFORMANCE"] == "true",
    "sut": {
        "baseUrl": env["M_SUT_URL"],
        "baseUrlFromHost": env["M_SUT_URL_HOST"],
        "image": env["M_IRIS_IMAGE"],
        "imageDigest": env["M_IRIS_DIGEST"] or None,
        "irisBuild": env["M_IRIS_BUILD"] or None,
        "namespace": env["M_NAMESPACE"] or None,
        # "pinned" = the version came verbatim from .env, so this round can be
        # re-created from that file. "resolved-latest" = it was the newest tag on
        # the track at resolve time, so the tag has probably moved since and the
        # digest above is the only exact reference to what was tested.
        "versionSource": env["M_IRIS_VERSION_SOURCE"] or None,
    },
    "testTool": {
        "image": env["M_SUITE_IMAGE"],
        "imageDigest": env["M_SUITE_DIGEST"] or None,
        "ip": env["M_SUITE_IP"],
        # -Xmx for the JVM that ran the scenarios, or null for "the JVM decided",
        # which means a quarter of the host's RAM. Recorded because it changes
        # whether a Stufe 5 run can finish at all: too little heap kills Tiger's
        # Rbel parser threads and every later scenario then burns a 100-second
        # parse timeout. A round that ended in a timeout with this null is
        # explained by it. See TESTSUITE_HEAP_MAX in .env.example.
        "scenarioHeapMax": env["M_SUITE_HEAP"] or None,
        # As sut.versionSource, but the expected value here is "pinned": the test
        # definition is not floated by default, because this repository's authored
        # test data is coupled to one release's feature files.
        "versionSource": env["M_SUITE_VERSION_SOURCE"] or None,
        # The newest published release at resolve time. Equal to the tested version
        # on a current round; higher means the pin was behind, which is a fact about
        # the round worth stating rather than a defect. null/"unknown" means GitHub
        # was unreachable, which nothing here depends on.
        "latestAvailable": env["M_SUITE_LATEST"] or None,
    },
    "packageSource": env["M_PACKAGE_SOURCE"],
    "mode": env["M_MODE"],
    # Run-behaviour settings given on the command line rather than read from the
    # configuration file, as "NAME=value" pairs, or null when there were none.
    # Recorded because otherwise this record and the file would disagree with no
    # way to tell which one the container actually got: re-running one level of a
    # finished round with more heap (TESTSUITE_HEAP_MAX=32g make test-stufe5
    # RUN_ID=<round>) is the documented response to the out-of-memory stall, and
    # the resulting archive has to say that is what happened. Only knobs that
    # describe how a level is run can appear here - never a version or an
    # endpoint, which would let a round test one thing and record another.
    "overrides": env["M_OVERRIDES"] or None,
    # Top level rather than inside "sut", so nothing that already reads "sut"
    # changes shape. Recorded in full on purpose: this is a single-round lab
    # instance, and a verdict in the results analysis has to be reproducible by
    # hand against a rebuilt one. scripts/lib/results_report.py renders this into
    # the "Access credentials" table in section 1.
    "credentials": {
        "_comment": (
            "Recorded on purpose. Single-round lab environment, destroyed after the "
            "results are verified; access is controlled physically and at the network "
            "perimeter, not by these passwords. Each deployment generates its own."
        ),
        "api": {
            "user": env["M_SUT_USER"],
            "password": env["M_SUT_PASSWORD"],
            "scheme": "HTTP Basic",
        },
        "management": {
            "user": env["M_MGMT_USER"],
            "password": env["M_MGMT_PASSWORD"],
            "purpose": "IRIS Management Portal, for the human tester",
        },
    },
    "seed": json.loads(env["M_SEED"]),
    "artefacts": {
        "reportArchives": sorted(p.name for p in official.glob("*.zip")),
        "testReport": env["M_TEST_ZIP"] or None,
        "debugReport": env["M_DEBUG_ZIP"] or None,
        "containerLog": "run.log",
        # How many Serenity TestOutcome records the certification archive carries,
        # counted from its zip listing without extracting (section 4b). Recorded
        # because it is the one number that separates "the SUT failed scenarios"
        # from "the suite never ran a scenario" - Maven's build status cannot,
        # since testFailureIgnore=false makes BUILD FAILURE the expected result of
        # a failing conformance run. null when there is no archive to count.
        "testOutcomeRecords": int(env["M_OUTCOMES"]) if env["M_OUTCOMES"] else None,
        # sha256 over the archives as received. Repeated here so run.json is a
        # self-contained provenance record: SHA256SUMS sits next to the files it
        # describes, which is convenient but not independent of them.
        "sha256": {p.name: None for p in sorted(official.glob("*.zip"))},
    },
}

# Hashed in Python rather than shelling out, so the digests in run.json and in
# SHA256SUMS are computed over the same bytes by the same read.
import hashlib
for name in rec["artefacts"]["sha256"]:
    h = hashlib.sha256()
    with (official / name).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    rec["artefacts"]["sha256"][name] = h.hexdigest()

pathlib.Path(sys.argv[1]).write_text(
    json.dumps(rec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY

log "wrote ${RUN_META#$REPO_ROOT/}"

# -----------------------------------------------------------------------------
# 6. Verdict
# -----------------------------------------------------------------------------
# A harness error is the absence of evidence, in either of its two forms: no
# certification artefact, or an artefact that documents no run. Scenario failures
# are the expected result and are not this script's business - so nothing here
# looks at whether a scenario passed, only at whether the round produced something
# to analyse. run.json is written before this point either way, because a run that
# produced nothing is itself a fact the round has to account for.
if [[ -z "$test_zip" ]]; then
  warn "container log tail:"
  tail -n 30 "$RUN_LOG" 2>/dev/null | sed 's/^/    /' >&2 || true
  if [[ "$TIMED_OUT" == true ]]; then
    die "no test-report archive was produced: the run was stopped at TESTSUITE_TIMEOUT=${RUN_TIMEOUT}s.
  The archives are assembled by Maven at the END of a run, so an interrupted run
  yields nothing. Raise TESTSUITE_TIMEOUT in .env and run again.
  The container log is kept at ${RUN_LOG#$REPO_ROOT/}."
  fi
  die "no test-report archive was produced by ${CONTAINER}.
  This is a harness error, not a conformance result: even a run in which every
  scenario fails produces test-report.zip. Read ${RUN_LOG#$REPO_ROOT/} - a Tiger
  startup failure (unreachable SUT, unparseable tiger-isik-${STUFE}.yaml,
  missing testdata file) is the usual cause and is reported there."
fi

# An archive with no outcome records in it. Fatal for the same reason a missing one
# is: there is nothing for `make report` to read, and letting it through would put
# a run that never happened into the evidence bundle as a round with zero findings.
if (( ${outcome_count:-0} == 0 )); then
  die "$(basename "$test_zip") contains no Serenity outcome records - the suite
  produced an archive but never executed a scenario. A harness error, not a
  conformance result.

  The container exits 0 regardless (its entrypoint ends in '|| true') and moves the
  archive regardless, so neither the exit code nor the archive's presence catches
  this; the WARN block above quotes the log lines that name the cause. Note that
  'BUILD FAILURE' alone is NOT the cause - the upstream profiles set
  testFailureIgnore=false, so a run with genuine conformance failures reports it
  too.

  Known causes, in the order worth checking:
    - Tiger could not read run/tiger/tiger-isik-${STUFE}.yaml. It is bind-mounted
      into a container running as uid 100, so the file must be world-readable;
      scripts/30-render-config.sh writes 0644 for exactly this reason. The log says
      'AccessDeniedException'. Re-run 'make up' to re-render it.
    - the tag expression selected nothing. TESTS_TO_RUN was '${TESTS_TO_RUN}'.
    - the SUT was unreachable from inside the subnet at $(sut_base_url "$STUFE").
    - a testdata file the contract names is missing from run/testdata.

  The full log is at ${RUN_LOG#$REPO_ROOT/}; run.json records
  artefacts.testOutcomeRecords: 0."
fi

if [[ -z "$debug_zip" ]]; then
  # Not fatal: the certification artefact is present. But the debug archive is
  # where the request/response bodies and validator messages live, so without it
  # the gap analysis can name the failing scenarios and not much else.
  warn "no debug-report archive was produced. The gap analysis needs it for the
  actual HTTP exchanges and validator output; without it, entries will state the
  expected result and the scenario, but not the response IRIS gave."
fi

ok "ISiK ${LEVEL#Stufe} run archived in ${ARCHIVE_DIR#$REPO_ROOT/} (${outcome_count} test outcome records)"
log "the official report is authoritative; 'make report' extracts and cross-references it"
step "ISiK ${LEVEL#Stufe} run complete"
