#!/usr/bin/env bash
# =============================================================================
# 40-up.sh - start the unit under test and wait until it is genuinely ready
# =============================================================================
# Brings up the isolated network, the IRIS container and the Web Gateway that
# fronts it, then blocks until both FHIR endpoints answer a CapabilityStatement
# request as the API user. Only those two services are started: the test suite
# services are profile-gated, so `compose up` without a --profile leaves them alone.
#
# Two containers, because the unit under test needs two: the official IRIS images
# ship no web server, so the gateway is the SUT's entire HTTP surface rather than
# infrastructure around it. See docker-compose.yml and docs/en/01-architecture.md.
#
# Almost all of this file is the waiting. That is deliberate, for two reasons.
#
#   1. The wait is minutes, not seconds, and dominated by the FHIR package import.
#      Measured on the lab host (8 vCPU, 31 GiB, PACKAGE_SOURCE=refv): 4m40s for a
#      cold install of both namespaces, and 7 seconds when the namespaces already
#      exist and the bootstrap takes its reuse path. Treat those as the shape of
#      the wait rather than a guarantee - the package import is I/O bound and a
#      slower disk or a cold package cache moves it a long way. A script that
#      prints nothing for minutes is indistinguishable from one that has hung, and
#      the deployment host is remote. So progress is reported from the bootstrap's
#      own status file.
#
#   2. Docker's health status cannot be waited on naively. The compose healthcheck
#      declares `start_period: 45m`, and during a start period Docker reports the
#      container as `starting` no matter what the check returns - including for a
#      bootstrap that has already failed for good. Polling only
#      `.State.Health.Status` would therefore sit through the full 45 minute start
#      period before admitting to an error that was known in the first two - and
#      since a healthy install finishes in about five, that is nearly all wait for
#      nothing. This script
#      watches run/iris-state/bootstrap.status alongside it and aborts as soon as
#      that file reports a terminal failure.
#
# On success the endpoints are reachable, the provenance record written by the SUT
# itself is present at run/iris-state/iris-provenance.json, and `make seed` can
# run. On failure the exact reason is printed together with where to read more.
# =============================================================================

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

step "Starting the unit under test"

load_env
require_var IRIS_IMAGE IRIS_VERSION IRIS_IP SUT_USER SUT_PASSWORD \
            FHIR_BASE_STUFE3 FHIR_BASE_STUFE5 ISIK_SUBNET \
            WEBGATEWAY_IMAGE WEBGATEWAY_VERSION WEBGATEWAY_IP

need_cmd docker
need_cmd curl

IRIS_CONTAINER="${COMPOSE_PROJECT_NAME:-isik}-iris"
GATEWAY_CONTAINER="${COMPOSE_PROJECT_NAME:-isik}-webgateway"
STATE_DIR="${RUN_DIR}/iris-state"
STATUS_FILE="${STATE_DIR}/bootstrap.status"
BOOTSTRAP_LOG="${STATE_DIR}/bootstrap.log"
PROVENANCE="${STATE_DIR}/iris-provenance.json"

# Ceiling for the whole bring-up, not an expectation of it: the measured cold
# install is about five minutes (see the note at the top of this file), and this
# allows 90. The margin is deliberate. It costs nothing on a healthy run, because
# the loop below exits as soon as the endpoints answer, whereas killing a working
# install on a slow or heavily loaded host wastes far more time than waiting out a
# broken one - and the failure paths in that loop catch a genuinely broken install
# in seconds rather than leaving it to this timeout.
BOOTSTRAP_TIMEOUT="${IRIS_BOOTSTRAP_TIMEOUT:-5400}"

# -----------------------------------------------------------------------------
# 1. Preconditions
# -----------------------------------------------------------------------------
# Checked here rather than left to compose, because compose's failure for a
# missing bind-mount source is a long error about a path that means nothing to
# someone who has not read docker-compose.yml.

# Matched case-insensitively, and via IRIS_KEY_PATH when preflight has already
# resolved it, so that an operator's IRIS.key is not reported as a missing file.
# Preflight is the authority on the key (it also fixes its permissions); this is
# only a guard against reaching compose without one.
if [[ -z "${IRIS_KEY_PATH:-}" || ! -f "${IRIS_KEY_PATH}" ]]; then
  IRIS_KEY_PATH="$(find "$SECRETS_DIR" -maxdepth 1 -type f -iname 'iris.key' 2>/dev/null | sort | head -n1)"
  export IRIS_KEY_PATH
fi
[[ -n "${IRIS_KEY_PATH:-}" && -f "${IRIS_KEY_PATH}" ]] \
  || die "secrets/iris.key is missing - IRIS will not start without a licence. See secrets/README.md"

[[ -f "${VENDOR_DIR}/package-plan.json" ]] \
  || die "vendor/package-plan.json is missing - run 'make packages' first"

# -L: vendor/packages may be a symlink or bind mount to a scratch disk (the
# unpacked set is ~1 GB), and without it find returns nothing and this reads as
# "packages absent" when they are in fact there.
pkg_dirs="$(find -L "$PACKAGES_DIR" -maxdepth 2 -name package.json 2>/dev/null | wc -l | tr -d ' ')"
(( pkg_dirs > 0 )) \
  || die "no unpacked FHIR packages under vendor/packages/ - run 'make packages' first"

# One Tiger configuration per level the pinned suite carries, not two literals:
# 30-render-config.sh renders over suite_levels_lc(), so under a 3.x suite - which
# dropped ISiK Level 3 - tiger-isik-stufe3.yaml is correctly absent, and demanding
# it here fails a valid round for a file nothing would read.
tiger_expected=()
for stufe in $(suite_levels_lc); do
  tiger_expected+=("${RUN_DIR}/tiger/tiger-isik-${stufe}.yaml")
done
(( ${#tiger_expected[@]} )) || die "suite_levels() named no level for TESTSUITE_VERSION=${TESTSUITE_VERSION:-?}"
for f in "${tiger_expected[@]}" \
         "${RUN_DIR}/webgateway/CSP.ini" "${RUN_DIR}/webgateway/isik-vhost.conf"; do
  [[ -f "$f" ]] || die "${f#$REPO_ROOT/} is missing - run scripts/30-render-config.sh (or 'make up')"
done

# The state directory is a read-write bind mount and the container writes the
# bootstrap log into it. Created here because `make clean` deletes run/.
mkdir -p "$STATE_DIR"

# The official image runs as irisowner (uid 51773), not as the operator, and a
# bind mount carries the host's ownership straight into the container. A directory
# created above with the operator's default 0775 is therefore read-only to the
# container, which makes the bootstrap fail in a thoroughly misleading way: the
# status file and log cannot be written, so the real ObjectScript error never
# reaches run/iris-state/bootstrap.log and `make up` reports the generic
# "IRIS exited before the endpoints were ready" instead of the actual cause.
#
# chown is the correct fix but needs privileges the operator may not have, so it
# is attempted first and 0777 is the fallback. Being world-writable is acceptable
# for exactly this directory: it holds this round's log and provenance record, no
# credentials (those are in secrets/ at 0600), and the whole tree is destroyed with
# the round.
IRIS_UID="${IRIS_CONTAINER_UID:-51773}"
if chown "${IRIS_UID}:${IRIS_UID}" "$STATE_DIR" 2>/dev/null; then
  log "run/iris-state owned by uid ${IRIS_UID} (the image's irisowner)"
else
  chmod 0777 "$STATE_DIR"
  log "run/iris-state left mode 0777 - chown to uid ${IRIS_UID} needs root; the container must be able to write its log here"
fi

# A status file left over from a previous round would be read as this round's
# progress, and the healthcheck would act on it too - so it is cleared, but ONLY
# when this invocation will actually produce a new one.
#
# `compose up -d` on an already-running, unchanged container does nothing at all:
# it reports "Container isik-iris Running" and leaves the process untouched. Nothing
# re-runs the bootstrap, so nothing rewrites these two files - and clearing them
# unconditionally therefore erases the record of an installation that is still
# there and still serving. The healthcheck reads the absence as "bootstrap has not
# started yet" (bootstrap.status gone) and IRIS goes unhealthy; `make up` then fails
# with "the bootstrap finished but the endpoints do not serve /metadata" about a SUT
# that answers 200 on both endpoints throughout. That is what happened here on the
# second `make seed` of a live environment, and it cost the round.
#
# Both files belong to the container, which writes them at every start (and treats
# bootstrap.done as its own "already installed, reuse it" marker, bootstrap.sh:158).
# So the host's business is only to remove a *stale* pair, i.e. one from a previous
# container that is no longer running. If IRIS is up right now, its markers describe
# the instance the rest of this script is about to wait for: leave them alone.
if [[ "$(docker inspect "$IRIS_CONTAINER" --format '{{.State.Status}}' 2>/dev/null || true)" == "running" ]]; then
  log "${IRIS_CONTAINER} is already running - keeping its bootstrap markers (they describe this instance)"

  # Reusing a running instance is only sound while it holds the installation this
  # round is configured for. `compose up -d` on an unchanged, already-running
  # container does nothing whatsoever - it does not restart it, so bootstrap.sh
  # does not run and the container's own guard against a changed package plan
  # never gets the chance to fire. The host is therefore the only place this can
  # be caught, and it has to be caught before a single request is sent.
  #
  # It is a hard stop rather than an automatic rebuild: destroying a live SUT
  # installation is a decision for the operator, and a mistyped TESTSUITE_VERSION
  # should cost a corrected line in .env, not half an hour of reinstallation.
  #
  # This is the defect that let a round pinned to suite 3.0.9 measure an ISIK5
  # endpoint loaded from the 2.7.0 plan (de.gematik.isik 5.1.2 rather than 5.1.3).
  # Nothing in that round's own output said so; it had to be found by reading the
  # SUT's provenance against the plan afterwards.
  marker_fp="$(head -n 1 "${STATE_DIR}/bootstrap.done" 2>/dev/null || true)"
  plan_fp="$(python3 "${REPO_ROOT}/iris/bootstrap/plan_fingerprint.py" \
             "${VENDOR_DIR}/package-plan.json" 2>/dev/null || true)"
  if [[ -z "$plan_fp" ]]; then
    warn "the configured package plan could not be fingerprinted - this round cannot confirm that the running instance holds the packages it describes"
  elif [[ -z "$marker_fp" ]]; then
    # No marker at all, or one from before the fingerprint was recorded. The
    # container will reinstall when it is next started, but it is running now and
    # nothing here can tell what it installed.
    warn "the running instance's completion marker names no package plan, so it cannot be matched against vendor/package-plan.json"
    warn "if this instance predates this check, 'make clean' then 'make all' installs the configured plan and records it"
  elif [[ "$marker_fp" != "$plan_fp" ]]; then
    die "the running IRIS instance was installed from a different package plan.

  installed: ${marker_fp}
  configured: ${plan_fp}  (vendor/package-plan.json, from $(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("sourceImage","?"))' "${VENDOR_DIR}/package-plan.json" 2>/dev/null || echo '?'))

  Its endpoints validate against the profiles and declare the search parameters
  of the plan it was installed from, not the configured one, so a round against
  it would report the previous installation's conformance as this one's.

  A package plan is installed once, at first start, and cannot be changed under a
  running instance. Install the configured plan with:

      make clean && make all

  Keep the instance as it is instead by restoring the suite version it was
  installed for in .env (TESTSUITE_VERSION) and running 'make packages'."
  else
    ok "the running instance holds the configured package plan (${plan_fp:0:12})"
  fi
else
  rm -f "$STATUS_FILE" "${STATE_DIR}/bootstrap.done"
fi

log "IRIS      ${IRIS_IMAGE}:${IRIS_VERSION} at ${IRIS_IP}"
log "gateway   ${WEBGATEWAY_IMAGE}:${WEBGATEWAY_VERSION} at ${WEBGATEWAY_IP} (serves the FHIR endpoints; IRIS has no web server)"
log "endpoints ${FHIR_BASE_STUFE3}, ${FHIR_BASE_STUFE5}"
log "packages  ${pkg_dirs} unpacked package directories"

# -----------------------------------------------------------------------------
# 2. Start
# -----------------------------------------------------------------------------
# No --profile, so only `iris`, `webgateway` and the network come up.
# --remove-orphans clears a test suite container left behind by an interrupted
# previous round, which would otherwise hold the static IP.
#
# Both services are named explicitly rather than relying on webgateway's depends_on:
# `compose up iris` alone would start only IRIS, leaving the SUT with no HTTP at all
# and the healthcheck - which probes through the gateway - permanently red.
log "creating the network and starting IRIS + the Web Gateway"
compose up -d --remove-orphans iris webgateway \
  || die "compose up failed - see the output above.
  Common causes: the licence key is not a valid iris.key; the subnet or a
  published port is taken (run 'make preflight'); an image is not pulled
  (run 'make resolve')."

# Confirm the network really is what was asked for. A pre-existing network with
# the right name but a different subnet would be reused silently by compose, and
# the whole point of this rig is a known, firewallable address range.
actual_subnet="$(docker network inspect "${COMPOSE_PROJECT_NAME:-isik}-net" \
  --format '{{range .IPAM.Config}}{{.Subnet}} {{end}}' 2>/dev/null | tr -d ' ' || true)"
if [[ "$actual_subnet" != "$ISIK_SUBNET" ]]; then
  die "network ${COMPOSE_PROJECT_NAME:-isik}-net has subnet '${actual_subnet}' but .env says '${ISIK_SUBNET}'
  A network from an earlier round with different settings is being reused.
  Remove it with 'make clean' and start again."
fi

actual_ip="$(docker inspect "$IRIS_CONTAINER" \
  --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' 2>/dev/null || true)"
[[ "$actual_ip" == "$IRIS_IP" ]] \
  || die "IRIS came up at ${actual_ip}, not the configured ${IRIS_IP} - the gateway's rendered CSP.ini points at ${IRIS_IP} and would not reach it"

ok "IRIS container running at ${actual_ip} on ${actual_subnet}"

# The gateway's address matters as much as IRIS's, and for the same reason: it is
# written literally into the rendered Tiger configuration the suite runs against,
# and into the IRIS container's healthcheck environment. A gateway at an
# unexpected address would make every scenario fail with a connection error.
gw_ip="$(docker inspect "$GATEWAY_CONTAINER" \
  --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' 2>/dev/null || true)"
[[ "$gw_ip" == "$WEBGATEWAY_IP" ]] \
  || die "the Web Gateway came up at ${gw_ip:-nowhere}, not the configured ${WEBGATEWAY_IP} - the rendered Tiger configuration points at ${WEBGATEWAY_IP} and would not reach it"

ok "Web Gateway running at ${gw_ip}, published on host port ${IRIS_PORT_WEB:-52773}"

# -----------------------------------------------------------------------------
# 3. Wait for the bootstrap
# -----------------------------------------------------------------------------
# Four independent things are watched, because each catches a failure the others
# cannot see:
#
#   the IRIS container a crashed IRIS (out of memory, unreadable licence file) never
#                      reports unhealthy - it stops existing as a running container
#   the gateway        IRIS's health is measured through it, so a dead gateway
#                      presents as an unhealthy IRIS and would otherwise be
#                      diagnosed as a FHIR problem
#   bootstrap.status   the only early view of a terminal install failure, and the
#                      only source of progress detail
#   health status      the authoritative readiness signal, since it is what
#                      `depends_on: service_healthy` gates the test suite on
step "Waiting for the FHIR endpoints (about 5 minutes cold, seconds on reuse)"
log "progress is also visible with: make logs"

started_at="$(date +%s)"
last_status=''
last_health=''
# Long enough that the progress line is not noise, short enough to notice a
# failure promptly.
POLL_INTERVAL=10

while :; do
  now="$(date +%s)"
  elapsed=$(( now - started_at ))

  if (( elapsed > BOOTSTRAP_TIMEOUT )); then
    warn "still not ready after $(( elapsed / 60 )) minutes"
    [[ -f "$STATUS_FILE" ]] && warn "last bootstrap status: $(cat "$STATUS_FILE")"
    die "timed out waiting for the FHIR endpoints (IRIS_BOOTSTRAP_TIMEOUT=${BOOTSTRAP_TIMEOUT}s)
  The container is left running on purpose so the failure can be examined:
    tail -n 100 run/iris-state/bootstrap.log
    docker logs ${IRIS_CONTAINER}
  Raise IRIS_BOOTSTRAP_TIMEOUT in .env if the host is simply slow."
  fi

  # --- a) is the container still there? --------------------------------------
  state="$(docker inspect "$IRIS_CONTAINER" --format '{{.State.Status}}' 2>/dev/null || echo gone)"
  case "$state" in
    running) ;;
    gone)
      die "container ${IRIS_CONTAINER} disappeared while starting up - was it removed by hand?" ;;
    exited|dead)
      code="$(docker inspect "$IRIS_CONTAINER" --format '{{.State.ExitCode}}' 2>/dev/null || echo '?')"
      warn "last 40 lines of container output:"
      docker logs --tail 40 "$IRIS_CONTAINER" 2>&1 | sed 's/^/    /' >&2 || true
      die "IRIS exited with code ${code} before the endpoints were ready.
  A licence problem is the usual cause: check the output above for a message
  about iris.key, and confirm the key matches the IRIS version and includes the
  FHIR/HealthShare capability." ;;
    *)
      log "container state: ${state}" ;;
  esac

  # --- a2) is the gateway still there? ---------------------------------------
  # Watched separately because IRIS's health is now gated on it: the healthcheck
  # probes the FHIR endpoints through the gateway, so a gateway that has died takes
  # IRIS's health down with it, and without this the operator would wait out the
  # whole 45 minute start_period to be told "the endpoints do not serve /metadata"
  # about a perfectly healthy IRIS.
  gw_state="$(docker inspect "$GATEWAY_CONTAINER" --format '{{.State.Status}}' 2>/dev/null || echo gone)"
  case "$gw_state" in
    running) ;;
    *)
      warn "last 40 lines of Web Gateway output:"
      docker logs --tail 40 "$GATEWAY_CONTAINER" 2>&1 | sed 's/^/    /' >&2 || true
      die "the Web Gateway container is ${gw_state}, so the SUT has no HTTP surface at all.
  IRIS may well be installing correctly, but its healthcheck probes the endpoints
  through this container and can never pass.
  Apache refuses to start on a malformed configuration: check the rendered files
  run/webgateway/CSP.ini and run/webgateway/isik-vhost.conf, then re-run
  scripts/30-render-config.sh." ;;
  esac

  # --- b) what does the bootstrap say? ---------------------------------------
  status=''
  [[ -f "$STATUS_FILE" ]] && status="$(cat "$STATUS_FILE" 2>/dev/null || true)"

  case "$status" in
    failed:*)
      # The whole reason this loop reads the status file: report now instead of
      # waiting out the remaining start_period.
      warn "last 40 lines of run/iris-state/bootstrap.log:"
      tail -n 40 "$BOOTSTRAP_LOG" 2>/dev/null | sed 's/^/    /' >&2 || true
      die "the IRIS bootstrap failed: ${status#failed: }
  Full log: run/iris-state/bootstrap.log
  The container is left running so the instance can be inspected:
    docker exec -it ${IRIS_CONTAINER} iris session iris" ;;
  esac

  if [[ -n "$status" && "$status" != "$last_status" ]]; then
    log "[$(printf '%2d' $(( elapsed / 60 )))m] ${status}"
    last_status="$status"
  fi

  # --- c) is it healthy? -----------------------------------------------------
  health="$(docker inspect "$IRIS_CONTAINER" \
    --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' 2>/dev/null || echo none)"

  if [[ "$health" == none ]]; then
    # No healthcheck means nothing to wait for; the explicit probe in step 4 then
    # becomes the only readiness gate. Not expected - docker-compose.yml declares
    # one - so it is worth saying out loud.
    warn "the container declares no healthcheck; relying on the direct endpoint probe instead"
    break
  fi

  if [[ "$health" != "$last_health" ]]; then
    log "[$(printf '%2d' $(( elapsed / 60 )))m] docker health: ${health}"
    last_health="$health"
  fi

  if [[ "$health" == healthy ]]; then
    ok "IRIS reports healthy after $(( elapsed / 60 ))m $(( elapsed % 60 ))s"
    break
  fi

  if [[ "$health" == unhealthy ]]; then
    # Reached only after start_period has expired and the check has failed
    # `retries` times, so by this point it is a real, settled failure.
    warn "healthcheck output:"
    docker inspect "$IRIS_CONTAINER" \
      --format '{{range .State.Health.Log}}{{.Output}}{{end}}' 2>/dev/null \
      | tail -n 20 | sed 's/^/    /' >&2 || true
    die "IRIS is reporting unhealthy: the bootstrap finished but the endpoints do not serve /metadata.
  See run/iris-state/bootstrap.log, and docs/en/02-setup.md 'Troubleshooting'."
  fi

  sleep "$POLL_INTERVAL"
done

# -----------------------------------------------------------------------------
# 4. Independent verification from the host
# -----------------------------------------------------------------------------
# The healthcheck reaches the gateway from inside the subnet, on ${WEBGATEWAY_IP}:80
# - the same path the test suite uses. This repeats the check from outside, over the
# published port, because that is a genuinely different path: it proves the port
# publication works, which is what the seeder and every `make status` / manual curl
# depend on, none of which run inside the subnet.
step "Verifying the endpoints from the host"

verify_endpoint() {
  local stufe="$1" url code
  url="$(sut_base_url_host "$stufe")/metadata"

  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 \
      -u "${SUT_USER}:${SUT_PASSWORD}" \
      -H 'Accept: application/fhir+json' \
      "$url" 2>/dev/null)" || code=000

  if [[ "$code" != 200 ]]; then
    warn "${stufe}: ${url} -> HTTP ${code} (expected 200)"
    return 1
  fi

  # A 200 is not sufficient on its own: an IRIS error page and a login redirect
  # can both be 200. Confirm the body is actually a CapabilityStatement, and
  # report the FHIR version while the response is in hand - a mismatch with R4
  # would invalidate the entire round.
  local fhir_version
  fhir_version="$(curl -s --max-time 30 \
      -u "${SUT_USER}:${SUT_PASSWORD}" \
      -H 'Accept: application/fhir+json' "$url" 2>/dev/null \
    | python3 -c '
import json, sys
try:
    doc = json.load(sys.stdin)
except Exception:
    sys.exit(1)
if doc.get("resourceType") != "CapabilityStatement":
    sys.exit(1)
print(doc.get("fhirVersion", "?"))
' 2>/dev/null)" || {
    warn "${stufe}: ${url} returned 200 but not a CapabilityStatement"
    return 1
  }

  ok "${stufe}: $(sut_base_url_host "$stufe") -> CapabilityStatement, FHIR ${fhir_version}"
  [[ "$fhir_version" == 4.0.* ]] \
    || warn "${stufe}: endpoint reports FHIR ${fhir_version}; ISiK is specified against R4 (4.0.1)"
  return 0
}

# Only the levels the pinned suite carries. The namespaces the SUT installs come
# from vendor/package-plan.json, which plan_packages.py builds over the same
# suite_levels_lc() set, so under a 3.x suite there is no ISIK3 namespace to probe
# and no endpoint behind FHIR_BASE_STUFE3. Probing it anyway would report a missing
# endpoint as an SUT fault when nothing asked for it to exist.
failed=0
for stufe in $(suite_levels_lc); do
  verify_endpoint "$stufe" || failed=1
done
(( failed == 0 )) || die "an endpoint is not reachable from the host - see above"

# -----------------------------------------------------------------------------
# 5. Provenance
# -----------------------------------------------------------------------------
# Written by ISiK.Setup on the SUT itself and consumed by the documentation
# generators. Its absence is not fatal - the endpoints demonstrably work - but it
# must be flagged, because the generated documentation would otherwise silently
# lose the "what is actually installed" record.
if [[ -f "$PROVENANCE" ]]; then
  summary="$(python3 - "$PROVENANCE" <<'PY' 2>/dev/null || true
import json, sys
with open(sys.argv[1], encoding="utf-8") as fh:
    doc = json.load(fh)
print(f"  IRIS: {doc.get('irisVersion', '?')}")
print(f"  strategy: {doc.get('strategyClass', '?')}")
for ep in doc.get("endpoints") or []:
    print(f"  {ep.get('level')}: {ep.get('endpoint')} in {ep.get('namespace')}"
          f" - {len(ep.get('loadedPackages') or [])} packages loaded")
PY
)"
  [[ -n "$summary" ]] && printf '%s\n' "$summary" >&2
  ok "provenance recorded in run/iris-state/iris-provenance.json"
else
  warn "run/iris-state/iris-provenance.json was not written - the generated documentation will be missing the SUT's own record of what was installed"
fi

step "Unit under test ready"
