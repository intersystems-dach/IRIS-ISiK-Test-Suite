#!/bin/bash
# =============================================================================
# healthcheck.sh - the IRIS container's Docker healthcheck
# =============================================================================
# Referenced by docker-compose.yml. Its contract, stated there:
#
#   Healthy means: bootstrap finished AND both FHIR endpoints answer a
#   CapabilityStatement request, over HTTP through the Web Gateway, as the API
#   user the test suite will use.
#
# That is a deliberately strict definition. `depends_on: condition:
# service_healthy` gates the test suite on it, and the alternative - reporting
# healthy as soon as IRIS itself is up - would start a run against an endpoint
# that does not exist yet and produce all 1401 of the round's test cases as
# failures that look like a total conformance collapse.
#
# "Through the Web Gateway" makes this check span two containers, which is unusual
# for a healthcheck and is the point: IRIS serves no HTTP of its own (the official
# images ship no web server), so the gateway is not infrastructure around the SUT,
# it is the SUT's HTTP surface. Probing anything else would report healthy for a
# server the test suite cannot reach.
#
# Exit codes are the Docker healthcheck convention:
#   0  healthy
#   1  unhealthy
# There is no "starting" code: Docker treats a container as `starting` for the
# duration of `start_period` regardless of what the check returns, which is why
# start_period is set to 45m in compose - the package import genuinely takes
# tens of minutes.
#
# Output goes to the check's log (`docker inspect --format '{{json .State.Health}}'`),
# so every line here is written on the assumption that someone is reading it to
# find out why a container never came up.
# =============================================================================

set -uo pipefail

STATE_DIR="${ISIK_STATE_DIR:-/opt/isik/state}"
STATUS_FILE="${STATE_DIR}/bootstrap.status"
DONE_FILE="${STATE_DIR}/bootstrap.done"

# --- 1. Has the bootstrap reached a terminal state? --------------------------
# The status file is written by bootstrap.sh at every transition. Reading it
# rather than probing the endpoints first means a still-installing container
# reports *what* it is doing, not just that it is not ready.
if [[ ! -f "$STATUS_FILE" ]]; then
    echo "bootstrap has not started yet (no ${STATUS_FILE})"
    exit 1
fi

status="$(cat "$STATUS_FILE" 2>/dev/null || true)"

case "$status" in
    failed:*)
        # Terminal failure. Reporting unhealthy is all this check can do; the
        # reason is in run/iris-state/bootstrap.log on the host, which is why the
        # message points at it rather than trying to summarise.
        echo "bootstrap FAILED: ${status#failed: }"
        echo "see run/iris-state/bootstrap.log for the full installation log"
        exit 1
        ;;
    running:*)
        echo "bootstrap in progress: ${status#running: }"
        exit 1
        ;;
    done:*)
        : # fall through to the endpoint probes
        ;;
    *)
        echo "unrecognised bootstrap status: '${status}'"
        exit 1
        ;;
esac

if [[ ! -f "$DONE_FILE" ]]; then
    # status says done but the marker is absent: the two are written together, so
    # this means someone deleted one of them, or the state directory is not the
    # one bootstrap.sh wrote to.
    echo "bootstrap.status says done but ${DONE_FILE} is missing - inconsistent state"
    exit 1
fi

# --- 2. Do both endpoints answer, as the test suite's user? -----------------
# Re-checked on every interval, not just once: this is a liveness check as much
# as a readiness check. If the FHIR service, the web application or the
# authentication path breaks mid-round - an expired password, a stopped
# production, a filled-up database - the container must stop reporting healthy,
# because from that moment on every remaining scenario fails for a reason that
# has nothing to do with ISiK conformance.
#
# The probe goes to the Web Gateway, not to localhost. IRIS itself serves no HTTP
# whatsoever - the official images ship no web server - so this is the only place
# the SUT's HTTP surface exists, and it is exactly the URL the test suite will
# use. That deliberately makes the gateway part of what "the SUT is healthy"
# means: a running IRIS behind a broken gateway serves nothing.
#
# No deadlock: the webgateway service waits on `condition: service_started` for
# IRIS, not service_healthy, so it comes up while IRIS is still installing and is
# already answering by the time this check first runs for real.
for var in ISIK_FHIR_BASE_STUFE3 ISIK_FHIR_BASE_STUFE5 ISIK_SUT_USER ISIK_SUT_PASSWORD \
           ISIK_WEBGATEWAY_HOST; do
    if [[ -z "${!var:-}" ]]; then
        echo "environment variable ${var} is not set - cannot probe the endpoints"
        exit 1
    fi
done

# Shared with bootstrap.sh so the healthcheck and the bootstrap cannot disagree
# about what "the endpoint answers" means. It also bounds each request (see
# HTTP_PROBE_TIMEOUT): the compose healthcheck timeout would kill a hung client
# anyway, but bounding it here means the failure message says which URL timed out
# instead of the check being reported unhealthy with no output at all.
. "$(dirname "${BASH_SOURCE[0]}")/http_probe.sh"

# Where the SUT's HTTP surface actually is. Injected as ISIK_WEBGATEWAY_HOST /
# ISIK_WEBGATEWAY_PORT by docker-compose.yml from WEBGATEWAY_IP and
# WEBGATEWAY_PORT_INTERNAL, so this script never has to know the subnet layout.
# Addressed by IP rather than by the `webgateway` service name for the same reason
# every other address in this rig is fixed: a name resolved through Docker's
# embedded DNS is one more moving part in a check whose failure output has to be
# unambiguous.
GATEWAY="${ISIK_WEBGATEWAY_HOST}:${ISIK_WEBGATEWAY_PORT:-80}"

probe() {
    local base="$1"
    local url="http://${GATEWAY}${base}/metadata"
    local code

    code="$(http_probe "$url")"

    if [[ "$code" != 200 ]]; then
        # The diagnoses below are the ones this rig actually produced during
        # bring-up, each mapped to its real cause rather than to the generic
        # meaning of the status code. They are worth the space: this text is all an
        # operator gets when a container never turns healthy.
        case "$code" in
            000) echo "  ${base}/metadata: no response from the Web Gateway at ${GATEWAY} - the webgateway container is not up, or not on this subnet" ;;
            401) echo "  ${base}/metadata: HTTP 401 - the API user ${ISIK_SUT_USER} cannot authenticate. If IRIS's audit log shows 'ERROR #5915: Unable to allocate a license', the cause is the licence, not the password" ;;
            403) echo "  ${base}/metadata: HTTP 403 - ${ISIK_SUT_USER} authenticated but lacks the endpoint resource" ;;
            404) echo "  ${base}/metadata: HTTP 404 - nothing serving this path, OR the request arrived unauthenticated: these endpoints answer an anonymous request with 404 rather than 401, so a credential that never reached IRIS looks exactly like a missing path. Check, in this order: that the Authorization header is being sent at all (http_probe.sh needs wget's --auth-no-challenge, because no challenge ever arrives to trigger it); that the IRIS web application exists; that the gateway vhost has a 'CSP On' Location block for this path (run/webgateway/isik-vhost.conf)" ;;
            500) echo "  ${base}/metadata: HTTP 500 - the gateway reached IRIS but its own service connection was refused. Check /opt/webgateway/bin/CSP.ini in the webgateway container for 'Connection Validation Failed ... Access Denied': the CSPSystem password there must match the one IRIS was given" ;;
            503) echo "  ${base}/metadata: HTTP 503 - the gateway routed the request but IRIS would not serve it. Usually a missing 'CGIPassAuth On', which strips the Authorization header and makes IRIS try to render a login page ('CSPServer Error displaying login page' in messages.log)" ;;
            *)   echo "  ${base}/metadata: HTTP ${code} (expected 200)" ;;
        esac
        return 1
    fi
    return 0
}

failed=0
probe "$ISIK_FHIR_BASE_STUFE3" || failed=1
probe "$ISIK_FHIR_BASE_STUFE5" || failed=1

if (( failed )); then
    echo "bootstrap completed but an endpoint is not serving CapabilityStatements"
    exit 1
fi

echo "healthy: both FHIR endpoints serve /metadata as ${ISIK_SUT_USER} via ${GATEWAY}"
exit 0
