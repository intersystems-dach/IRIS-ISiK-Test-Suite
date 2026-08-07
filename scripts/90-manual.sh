#!/usr/bin/env bash
# =============================================================================
# 90-manual.sh - deploy mode 3: leave the environment up for interactive testing
# =============================================================================
# `make manual`, which first runs `make seed MODE=manual` and then this. By the
# time this script starts, IRIS is up with both endpoints installed and the test
# data seeded; all that remains is to start the test suite container in its
# non-auto-running form and hand over.
#
# The difference from an unattended run is entirely in the configuration, not in
# the image: run/tiger/manual/tiger-isik-*.yaml sets `lib.runTestsOnStart: false`,
# so Tiger boots, starts the WorkflowUI and waits for the operator to select and
# start scenarios. The `manual` compose profile mounts those files instead of the
# auto-running ones, which is why the two variants are rendered separately - an
# unattended run cannot pick up the manual configuration by accident.
#
# This script returns as soon as the WorkflowUI answers. It deliberately does NOT
# block: the environment is meant to outlive the invocation. Tear it down with
# `make clean` when finished.
#
# Note on reports: the upstream entrypoint wipes /app/report at every container
# start and the report zips are only assembled by Maven's post-integration-test
# phase. A manually driven session therefore produces no report archive. Use the
# WorkflowUI and the Tiger traffic log for interactive work, and an unattended
# `make test` for anything whose result is to be reported.
# =============================================================================

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

step "Starting the test tool for manual testing"

load_env
require_var TESTSUITE_IMAGE TESTSUITE_VERSION TESTSUITE_IP SUT_USER SUT_PASSWORD

PROJECT="${COMPOSE_PROJECT_NAME:-isik}"
CONTAINER="${PROJECT}-testsuite-manual"
UI_PORT="${TESTSUITE_PORT_WORKFLOW_UI:-9010}"
PROXY_PORT="${TESTSUITE_PORT_PROXY_LOG:-9011}"
MANUAL_STUFE="${MANUAL_STUFE:-stufe5}"

case "$MANUAL_STUFE" in
  stufe3|stufe5) ;;
  *) die "MANUAL_STUFE must be 'stufe3' or 'stufe5' (got '${MANUAL_STUFE}') - it selects which level the WorkflowUI offers" ;;
esac

# -----------------------------------------------------------------------------
# 1. Preconditions
# -----------------------------------------------------------------------------
for f in "${RUN_DIR}/tiger/manual/tiger-isik-stufe3.yaml" \
         "${RUN_DIR}/tiger/manual/tiger-isik-stufe5.yaml"; do
  [[ -f "$f" ]] \
    || die "${f#$REPO_ROOT/} is missing - run 'make up' (scripts/30-render-config.sh renders it)"
done

# The manual configuration must really be the non-auto-running one. Checked
# rather than assumed: mounting the auto-running variant here would start a full
# conformance run (895 test cases at Stufe 5, 506 at Stufe 3) the operator did not
# ask for, and the first sign of it would be the endpoints being hammered.
grep -q 'runTestsOnStart:[[:space:]]*false' "${RUN_DIR}/tiger/manual/tiger-isik-${MANUAL_STUFE}.yaml" \
  || die "run/tiger/manual/tiger-isik-${MANUAL_STUFE}.yaml does not set runTestsOnStart: false
  It would start a full conformance run on container start. Re-render it with 'make up'."

# The SUT must be up: Tiger's own healthcheckUrl would otherwise fail its startup
# check and the container would exit before the UI ever appeared.
#
# Both containers, because both are the SUT. Tiger's healthcheckUrl is an HTTP URL
# and IRIS serves no HTTP, so a running IRIS behind a stopped gateway fails that
# check exactly as a stopped IRIS would - with the container exiting and no UI.
for svc in iris webgateway; do
  docker inspect "${PROJECT}-${svc}" --format '{{.State.Status}}' 2>/dev/null | grep -q running \
    || die "the ${svc} container (${PROJECT}-${svc}) is not running - run 'make manual' rather than this script directly"
done

log "level:      ISiK ${MANUAL_STUFE#stufe}"
log "SUT:        $(sut_base_url "$MANUAL_STUFE")"
log "config:     run/tiger/manual/tiger-isik-${MANUAL_STUFE}.yaml"

# -----------------------------------------------------------------------------
# 2. Start the container
# -----------------------------------------------------------------------------
# MANUAL_STUFE is exported so compose resolves ${MANUAL_STUFE} in the
# testsuite-manual service's MAVEN_PROFILE, which is what decides which
# tiger-isik-*.yaml Maven reads via TIGER_TESTENV_CFGFILE.
export MANUAL_STUFE

# The tag expression must name the same level as the Maven profile. It is easy to
# get this wrong - .env ships both settings and only one of them looks like a
# level - and the symptom is confusing rather than obvious: Maven runs the
# stufe3 profile while the tag filter selects @Stufe5 scenarios, so the UI offers
# an empty scenario list with no error anywhere.
WANT_TAG="@Stufe${MANUAL_STUFE#stufe}"
if [[ -n "${TESTS_TO_RUN_MANUAL:-}" && "$TESTS_TO_RUN_MANUAL" != *"$WANT_TAG"* ]]; then
  warn "TESTS_TO_RUN_MANUAL is '${TESTS_TO_RUN_MANUAL}' but MANUAL_STUFE=${MANUAL_STUFE} needs ${WANT_TAG}"
  warn "  overriding it with '${WANT_TAG} and (not @Optional)' - set both consistently in .env to silence this"
  TESTS_TO_RUN_MANUAL="${WANT_TAG} and (not @Optional)"
fi
export TESTS_TO_RUN_MANUAL="${TESTS_TO_RUN_MANUAL:-${WANT_TAG} and (not @Optional)}"
log "tags:       ${TESTS_TO_RUN_MANUAL}"

log "starting ${CONTAINER}"
compose --profile manual up -d testsuite-manual \
  || die "could not start the test suite container - see the output above"

# -----------------------------------------------------------------------------
# 3. Wait for the WorkflowUI
# -----------------------------------------------------------------------------
# Maven has to resolve the reactor and Tiger has to boot its proxy before the UI
# binds, which takes a minute or two on a warm Maven cache and longer on a cold
# one. Waiting here means the operator is told a URL that actually works.
step "Waiting for the Tiger WorkflowUI on port ${UI_PORT}"

UI_TIMEOUT="${MANUAL_UI_TIMEOUT:-600}"
started_at="$(date +%s)"

while :; do
  elapsed=$(( $(date +%s) - started_at ))

  state="$(docker inspect "$CONTAINER" --format '{{.State.Status}}' 2>/dev/null || echo gone)"
  if [[ "$state" != running ]]; then
    warn "last 40 lines of container output:"
    docker logs --tail 40 "$CONTAINER" 2>&1 | sed 's/^/    /' >&2 || true
    die "the test suite container is ${state} - it exited before the WorkflowUI came up.
  A Tiger startup failure is the usual cause; the output above says which server
  or configuration file it objected to."
  fi

  # Any HTTP response means the port is bound and the UI is serving. The exact
  # status is not checked: Tiger's UI redirects and its status codes vary between
  # versions, and this only needs to establish reachability.
  if curl -s -o /dev/null --max-time 5 "http://127.0.0.1:${UI_PORT}/" 2>/dev/null; then
    ok "WorkflowUI is up after $(( elapsed ))s"
    break
  fi

  if (( elapsed > UI_TIMEOUT )); then
    warn "last 40 lines of container output:"
    docker logs --tail 40 "$CONTAINER" 2>&1 | sed 's/^/    /' >&2 || true
    warn "the WorkflowUI did not answer within ${UI_TIMEOUT}s"
    warn "the container is left running; check 'docker logs -f ${CONTAINER}'"
    # Not fatal: the container is up and may simply be slow. Failing the target
    # would tear down nothing but would suggest the environment is unusable.
    break
  fi

  sleep 5
done

# -----------------------------------------------------------------------------
# 4. Hand over
# -----------------------------------------------------------------------------
cat >&2 <<EOF

$( [[ -t 2 ]] && printf '\033[1m' )Manual test environment ready$( [[ -t 2 ]] && printf '\033[0m' )

  Tiger WorkflowUI     http://localhost:${UI_PORT}
  Tiger traffic log    http://localhost:${PROXY_PORT}
  IRIS Management      http://localhost:${IRIS_PORT_WEB:-52773}/csp/sys/UtilHome.csp
                       ${IRIS_MGMT_USER:-_SYSTEM} / ${IRIS_MGMT_PASSWORD:-** unset - run make resolve **}

  FHIR endpoints, from the host:
    ISiK Stufe 3       $(sut_base_url_host stufe3)
    ISiK Stufe 5       $(sut_base_url_host stufe5)
  and from inside the isolated subnet (what the suite uses):
    ISiK Stufe 3       $(sut_base_url stufe3)
    ISiK Stufe 5       $(sut_base_url stufe5)

  All four addresses - and the Management Portal above - are served by the Web
  Gateway at ${WEBGATEWAY_IP}, not by IRIS: the IRIS images ship no web server.

  API credentials      ${SUT_USER} / ${SUT_PASSWORD}
                       (this instance's own, generated at deployment; the full
                        sheet with ready-to-paste commands is run/CREDENTIALS.md,
                        or 'make credentials')

  The WorkflowUI is serving ISiK ${MANUAL_STUFE#stufe} only, because one container
  runs one Maven profile. Switch level with:

      make clean && MANUAL_STUFE=stufe3 make manual

  Scenarios do not start on their own - select them in the WorkflowUI. Note that a
  manually driven session produces no test-report.zip: the report is assembled by
  Maven at the end of an unattended run. Use 'make test' for reportable results.

  Tear down with 'make clean'.

EOF

step "Manual environment up"
