#!/usr/bin/env bash
# =============================================================================
# 91-status.sh - what is running, and is it what was intended?
# =============================================================================
# `make status`. Read-only: it changes nothing and is safe to run at any point,
# including in the middle of a conformance run.
#
# Two audiences, both served by the same output:
#
#   the operator   "is it up, how far along is the bootstrap, can I seed yet?"
#   the inspector  "does the running environment match the documented one?" -
#                  the network CIDR, the two static addresses, the image digests
#                  and the endpoint paths are printed as observed, not as
#                  configured, so the two can be compared.
#
# Deliberately does not exit non-zero for a not-yet-ready environment: "IRIS is
# still importing packages" is a normal state, not an error, and a non-zero exit
# would make `make status` unusable inside a shell `&&` chain. It exits non-zero
# only when it cannot read the configuration at all.
#
# The output includes this instance's credentials, in full. That is deliberate and
# it does mean this output should not be pasted anywhere the environment is not:
# the rig is a single-round lab instance whose credentials are documented on
# purpose (see run/CREDENTIALS.md and `make credentials`), and a tester diagnosing
# a 401 needs to see the credential the probe below actually used.
# =============================================================================

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

load_env
require_var IRIS_IP TESTSUITE_IP ISIK_SUBNET FHIR_BASE_STUFE3 FHIR_BASE_STUFE5 SUT_USER \
            WEBGATEWAY_IP

PROJECT="${COMPOSE_PROJECT_NAME:-isik}"
NET_NAME="${PROJECT}-net"
IRIS_CONTAINER="${PROJECT}-iris"
GATEWAY_CONTAINER="${PROJECT}-webgateway"
STATE_DIR="${RUN_DIR}/iris-state"

# Two-column key/value output. Plain text on purpose: this gets pasted into
# tickets and into the documentation.
kv() { printf '  %-26s %s\n' "$1" "$2"; }

# -----------------------------------------------------------------------------
step "Configuration"
kv "source" "${ENV_LOADED_FROM#$REPO_ROOT/}"
kv "MODE" "${MODE}"
kv "PACKAGE_SOURCE" "${PACKAGE_SOURCE:-refv}"
kv "IRIS" "${IRIS_IMAGE}:${IRIS_VERSION}"
kv "Web Gateway" "${WEBGATEWAY_IMAGE:-?}:${WEBGATEWAY_VERSION:-?}"
kv "test suite" "${TESTSUITE_IMAGE}:${TESTSUITE_VERSION}"
if [[ -n "${IRIS_IMAGE_DIGEST:-}" ]]; then
  kv "IRIS digest" "${IRIS_IMAGE_DIGEST}"
  # Recorded and shown with the same weight as IRIS's: the gateway serves every
  # FHIR request, so it is part of the SUT an inspector is comparing against.
  kv "gateway digest" "${WEBGATEWAY_IMAGE_DIGEST:-?}"
  kv "test suite digest" "${TESTSUITE_IMAGE_DIGEST:-?}"
  kv "resolved at" "${RESOLVED_AT:-?}"
else
  # Reading .env rather than .env.resolved means `make resolve` has not run, so
  # there are no digests and nothing downstream is frozen yet.
  kv "digests" "not resolved yet - run 'make resolve'"
fi

# -----------------------------------------------------------------------------
step "Network"
if docker network inspect "$NET_NAME" >/dev/null 2>&1; then
  observed_subnet="$(docker network inspect "$NET_NAME" \
    --format '{{range .IPAM.Config}}{{.Subnet}} {{end}}' 2>/dev/null | tr -d ' ')"
  observed_gw="$(docker network inspect "$NET_NAME" \
    --format '{{range .IPAM.Config}}{{.Gateway}} {{end}}' 2>/dev/null | tr -d ' ')"
  observed_br="$(docker network inspect "$NET_NAME" \
    --format '{{index .Options "com.docker.network.bridge.name"}}' 2>/dev/null || true)"

  kv "name" "$NET_NAME"
  if [[ "$observed_subnet" == "$ISIK_SUBNET" ]]; then
    kv "subnet" "${observed_subnet}  (matches .env)"
  else
    kv "subnet" "${observed_subnet}  ** .env says ${ISIK_SUBNET} **"
  fi
  kv "gateway" "${observed_gw}  (host NAT)"
  kv "host bridge" "${observed_br:-<generated>}"

  # Attached containers with their addresses: the check that the static IPAM
  # assignment actually took effect.
  docker network inspect "$NET_NAME" \
    --format '{{range .Containers}}  {{printf "%-26s" .Name}} {{.IPv4Address}}
{{end}}' 2>/dev/null | sed '/^$/d' || true
else
  kv "name" "${NET_NAME} - does not exist (run 'make up')"
fi

# -----------------------------------------------------------------------------
step "Containers"
# --all so that a finished test suite container still appears: its exit code is
# part of the picture, and the upstream entrypoint's `|| true` means even a
# failed conformance run exits 0.
if ! docker ps --all --filter "label=com.docker.compose.project=${PROJECT}" \
      --format '  {{printf "%-30s" .Names}} {{printf "%-22s" .State}} {{.Status}}' 2>/dev/null \
      | grep -q .; then
  printf '  no containers for compose project "%s"\n' "$PROJECT"
else
  docker ps --all --filter "label=com.docker.compose.project=${PROJECT}" \
    --format '  {{printf "%-30s" .Names}} {{printf "%-22s" .State}} {{.Status}}'
fi

# -----------------------------------------------------------------------------
step "IRIS bootstrap"
if [[ -f "${STATE_DIR}/bootstrap.status" ]]; then
  status="$(cat "${STATE_DIR}/bootstrap.status" 2>/dev/null || true)"
  kv "status" "${status:-<empty>}"
  case "$status" in
    failed:*) kv "" "see run/iris-state/bootstrap.log for the full log" ;;
  esac
else
  kv "status" "no status file - the container has not started, or run/ was cleaned"
fi

# The marker carries the fingerprint of the plan it installed, so "present" and
# "describes what is configured now" are two different statements and the second
# is the one that matters: a marker from another plan means the next start
# reinstalls, and a round started before it does measures the packages named by
# the marker rather than the ones in vendor/package-plan.json.
if [[ -f "${STATE_DIR}/bootstrap.done" ]]; then
  marker_fp="$(head -n 1 "${STATE_DIR}/bootstrap.done" 2>/dev/null || true)"
  plan_fp=""
  [[ -f "${VENDOR_DIR}/package-plan.json" ]] \
    && plan_fp="$(python3 "${REPO_ROOT}/iris/bootstrap/plan_fingerprint.py" \
                  "${VENDOR_DIR}/package-plan.json" 2>/dev/null || true)"
  if [[ -z "$marker_fp" ]]; then
    kv "done marker" "present, from before the fingerprint was recorded (the next start reinstalls)"
  elif [[ -z "$plan_fp" ]]; then
    kv "done marker" "present for plan ${marker_fp:0:12}; no plan to compare it against (run make packages)"
  elif [[ "$marker_fp" == "$plan_fp" ]]; then
    kv "done marker" "present for plan ${marker_fp:0:12}, which is the configured one (a restart reuses this installation)"
  else
    kv "done marker" "present for plan ${marker_fp:0:12}, but vendor/package-plan.json is ${plan_fp:0:12}"
    kv "" "the installed packages are not the configured ones - the next start reinstalls"
  fi
else
  kv "done marker" "absent"
fi

if [[ -f "${STATE_DIR}/bootstrap.log" ]]; then
  kv "log" "run/iris-state/bootstrap.log ($(wc -l <"${STATE_DIR}/bootstrap.log" | tr -d ' ') lines)"
  printf '  last line: %s\n' "$(tail -n1 "${STATE_DIR}/bootstrap.log" 2>/dev/null || true)"
fi

# The container's own health verdict, plus the most recent check output - which is
# where healthcheck.sh explains itself.
if docker inspect "$IRIS_CONTAINER" >/dev/null 2>&1; then
  health="$(docker inspect "$IRIS_CONTAINER" \
    --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}<no healthcheck>{{end}}' 2>/dev/null || true)"
  kv "docker health" "${health}"
  last_check="$(docker inspect "$IRIS_CONTAINER" \
    --format '{{if .State.Health}}{{range .State.Health.Log}}{{.Output}}{{end}}{{end}}' 2>/dev/null \
    | tail -n 6 || true)"
  [[ -n "$last_check" ]] && printf '%s\n' "$last_check" | sed 's/^/    /'
fi

# -----------------------------------------------------------------------------
step "Web Gateway (the SUT's HTTP surface)"
# Reported as its own section rather than folded into "Containers", because when the
# endpoints below are unreachable this is the first thing to look at: the IRIS images
# ship no web server, so every FHIR request in the round passes through here, and an
# unhealthy gateway explains an unhealthy IRIS rather than the other way round.
if docker inspect "$GATEWAY_CONTAINER" >/dev/null 2>&1; then
  gw_state="$(docker inspect "$GATEWAY_CONTAINER" --format '{{.State.Status}}' 2>/dev/null || true)"
  gw_health="$(docker inspect "$GATEWAY_CONTAINER" \
    --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}<no healthcheck>{{end}}' 2>/dev/null || true)"
  gw_ip="$(docker inspect "$GATEWAY_CONTAINER" \
    --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' 2>/dev/null || true)"

  kv "container" "${GATEWAY_CONTAINER}  ${gw_state}  (health: ${gw_health})"
  if [[ "$gw_ip" == "$WEBGATEWAY_IP" ]]; then
    kv "address" "${gw_ip}  (matches .env; this is what the test suite connects to)"
  else
    kv "address" "${gw_ip:-none}  ** .env says ${WEBGATEWAY_IP} - the suite would not reach it **"
  fi
  kv "published as" "127.0.0.1:${IRIS_PORT_WEB:-52773} -> ${gw_ip}:${WEBGATEWAY_PORT_INTERNAL:-80}"
  kv "IRIS connection" "${GATEWAY_IRIS_USER:-CSPSystem} -> ${IRIS_IP}:1972 (the gateway's own service connection)"
else
  kv "container" "${GATEWAY_CONTAINER} does not exist - run 'make up'"
  kv "" "without it the SUT serves no HTTP at all: IRIS has no web server of its own"
fi

# -----------------------------------------------------------------------------
step "FHIR endpoints"
# Probed from the host over the published port - the same path the seeder uses, and
# one hop further out than the suite's, which goes straight to the gateway on the
# subnet. Authenticated, because the endpoints require it; an unauthenticated probe
# would report 401 everywhere and say nothing about whether the server works.
probe_endpoint() {
  local stufe="$1" url code body

  url="$(sut_base_url_host "$stufe")/metadata"

  if [[ -z "${SUT_PASSWORD:-}" ]]; then
    kv "$stufe" "${url%/metadata}  (SUT_PASSWORD unset - not probed)"
    return
  fi

  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 \
      -u "${SUT_USER}:${SUT_PASSWORD}" \
      -H 'Accept: application/fhir+json' "$url" 2>/dev/null)" || code=000

  if [[ "$code" != 200 ]]; then
    case "$code" in
      000) kv "$stufe" "${url%/metadata}  -> unreachable (is the webgateway container up?)" ;;
      401) kv "$stufe" "${url%/metadata}  -> 401 (user ${SUT_USER} cannot authenticate; a licence failure also presents as 401 - check IRIS's audit log for #5915)" ;;
      403) kv "$stufe" "${url%/metadata}  -> 403 (authenticated, but lacks the endpoint resource)" ;;
      # 404 is ambiguous here, and misleadingly so: with AutheEnabled=32 the CSP
      # dispatcher refuses an unauthenticated request by reporting no such path
      # rather than by challenging, so a credential problem and a wrong path give
      # the same code. Measured: correct credential 200, missing 404, wrong 404.
      # The credential is printed above, so the reader can tell which they have.
      404) kv "$stufe" "${url%/metadata}  -> 404 (ambiguous: EITHER the credential was rejected - these endpoints refuse with 404, not 401 - OR there is no endpoint at this path / no gateway Location block for it. Check the credential above first, then IRIS's audit log)" ;;
      500) kv "$stufe" "${url%/metadata}  -> 500 (the gateway's own IRIS connection was refused - check the ${GATEWAY_IRIS_USER:-CSPSystem} password in run/webgateway/CSP.ini)" ;;
      503) kv "$stufe" "${url%/metadata}  -> 503 (routed, but IRIS would not serve it - usually a missing CGIPassAuth)" ;;
      *)   kv "$stufe" "${url%/metadata}  -> HTTP ${code}" ;;
    esac
    return
  fi

  # Report from the CapabilityStatement itself: the FHIR version and how many
  # resource types the endpoint claims. The resource count is a cheap sanity check
  # on the package set - an endpoint with only the core R4 packages exposes
  # noticeably fewer types than one carrying the ISiK profiles.
  body="$(curl -s --max-time 20 -u "${SUT_USER}:${SUT_PASSWORD}" \
      -H 'Accept: application/fhir+json' "$url" 2>/dev/null \
    | python3 -c '
import json, sys
# Values are pulled into locals before formatting: a backslash inside an f-string
# expression is a syntax error before Python 3.12, and the deployment host may
# well be older than that.
try:
    doc = json.load(sys.stdin)
except Exception:
    print("200, but the body is not JSON"); raise SystemExit
rt = doc.get("resourceType")
if rt != "CapabilityStatement":
    print("200, but resourceType is " + repr(rt)); raise SystemExit
version = doc.get("fhirVersion", "?")
types = sum(len(r.get("resource") or []) for r in (doc.get("rest") or []))
print("FHIR " + str(version) + ", " + str(types) + " resource types")
' 2>/dev/null || echo "200")"
  kv "$stufe" "${url%/metadata}  -> ${body}"
}

probe_endpoint stufe3
probe_endpoint stufe5

# The addresses as the test suite sees them. Printed alongside the host-side probe
# because they are what the rendered Tiger configuration contains, and an inspector
# comparing the documentation against the running environment needs both forms.
kv "as the suite sees them" "$(sut_base_url stufe3)"
kv "" "$(sut_base_url stufe5)"

# -----------------------------------------------------------------------------
step "Credentials"
# In full, on purpose - see the note in this file's header. Printed right below the
# endpoint probe because the two are read together: a 401 above and the credential
# here are one diagnosis, not two.
kv "FHIR API" "${SUT_USER} / ${SUT_PASSWORD:-** unset - run make resolve **}"
kv "Management Portal" "${IRIS_MGMT_USER:-_SYSTEM} / ${IRIS_MGMT_PASSWORD:-** unset - run make resolve **}"
kv "portal URL" "http://127.0.0.1:${IRIS_PORT_WEB:-52773}/csp/sys/UtilHome.csp"
if [[ -f "${RUN_DIR}/CREDENTIALS.md" ]]; then
  kv "sheet" "run/CREDENTIALS.md (\`make credentials\` prints it)"
else
  kv "sheet" "absent - regenerated by 'make up'; 'make credentials' still works"
fi

# What the SUT itself recorded about its installation.
if [[ -f "${STATE_DIR}/iris-provenance.json" ]]; then
  python3 - "${STATE_DIR}/iris-provenance.json" <<'PY' 2>/dev/null || true
import json, sys
with open(sys.argv[1], encoding="utf-8") as fh:
    doc = json.load(fh)
print(f"  {'IRIS build':26} {doc.get('irisVersion', '?')}")
for ep in doc.get("endpoints") or []:
    pkgs = ep.get("loadedPackages") or []
    print(f"  {ep.get('level', '?') + ' packages':26} {len(pkgs)} loaded in namespace {ep.get('namespace')}")
PY
fi

# -----------------------------------------------------------------------------
step "Prepared artefacts"
kv "package plan" "$([[ -f ${VENDOR_DIR}/package-plan.json ]] && echo present || echo 'absent - run make packages')"
if [[ -d "$PACKAGES_DIR" ]]; then
  kv "unpacked packages" "$(find -L "$PACKAGES_DIR" -maxdepth 2 -name package.json 2>/dev/null | wc -l | tr -d ' ') directories"
fi
if [[ -d "${VENDOR_DIR}/upstream/features" ]]; then
  kv "upstream features" "$(find "${VENDOR_DIR}/upstream/features" -name '*.feature' | wc -l | tr -d ' ') files extracted from the image"
fi
kv "gap-analysis index" "$([[ -f ${REPO_ROOT}/gap-analysis/index.json ]] && echo present || echo 'absent - built by make packages')"
kv "rendered Tiger config" "$([[ -f ${RUN_DIR}/tiger/tiger-isik-stufe3.yaml ]] && echo present || echo 'absent - run make up')"
if [[ -f "${RUN_DIR}/tiger/TESTDATA-PARITY.txt" ]]; then
  if grep -q 'NOT VERIFIED' "${RUN_DIR}/tiger/TESTDATA-PARITY.txt"; then
    kv "test-data parity" "NOT VERIFIED - see run/tiger/TESTDATA-PARITY.txt"
  else
    # Counted on the rows, which are the indented lines, and not on the file: the
    # legend at the top of that record names every status it explains, so a
    # grep over the whole file counts the explanation as a file.
    parity_n() { grep -cE "^  ${1} " "${RUN_DIR}/tiger/TESTDATA-PARITY.txt" || true; }
    parity_line="$(parity_n IDENTICAL) files identical to the official release"
    # Only for a Connect round, and worth a word rather than a number: this is
    # the one file the round writes this round's bearer tokens into.
    if [[ "$(parity_n PARAMETERISED)" != "0" ]]; then
      parity_line="${parity_line}, $(parity_n PARAMETERISED) carrying this round's access tokens"
    fi
    kv "test-data parity" "$parity_line"
  fi
fi

# -----------------------------------------------------------------------------
step "Reports"
if [[ -d "$REPORTS_DIR" ]] && find "$REPORTS_DIR" -mindepth 1 -maxdepth 1 -type d | grep -q .; then
  while IFS= read -r d; do
    zips="$(find "$d" -name '*report.zip' 2>/dev/null | wc -l | tr -d ' ')"
    kv "$(basename "$d")" "${zips} report archive(s)$([[ -f ${d}/GAP-ANALYSIS.md ]] && echo ', GAP-ANALYSIS.md' || echo '')"
  done < <(find "$REPORTS_DIR" -mindepth 1 -maxdepth 1 -type d | sort)
else
  printf '  no runs archived yet\n'
fi

printf '\n'
