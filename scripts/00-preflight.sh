#!/usr/bin/env bash
# =============================================================================
# 00-preflight.sh - host prerequisite check
# =============================================================================
# Runs before anything is downloaded or started. Its job is to fail early with a
# message that names the exact missing prerequisite and how to satisfy it,
# because the deployment host is remote and a half-started run leaves state to
# clean up.
#
# Checks, in order:
#   1. required commands
#   2. Docker daemon reachable, Compose v2
#   3. .env present and MODE valid
#   4. IRIS licence key present and readable
#   5. registry credential for containers.intersystems.com
#   6. no host port collisions
#   7. no subnet collision with an existing docker network or host route
#   8. writable output directories
#   9. free disk space
#  10. host RAM, and the heap the scenario JVM will get
# =============================================================================

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

step "Preflight"

# --- 1. Required commands ---------------------------------------------------
need_cmd docker  "install Docker Engine 20.10+ (https://docs.docker.com/engine/install/)"
need_cmd curl    "used to talk to the FHIR endpoints and to Simplifier"
need_cmd tar     "used to unpack FHIR .tgz packages"
need_cmd unzip   "used to unpack refv-plugins/*.zip and the report archives"
need_cmd python3 "used by the seeder, the results analysis and the gap report"
need_cmd awk
need_cmd sed

# python3 must be able to read YAML: the testdata contracts, the refv plugin
# configs and the rendered Tiger configuration are all YAML.
python3 -c 'import yaml' 2>/dev/null \
  || die "python3 cannot import yaml - install PyYAML (pip install pyyaml, or apt install python3-yaml)"
ok "required commands present"

# --- 2. Docker ---------------------------------------------------------------
docker info >/dev/null 2>&1 \
  || die "cannot talk to the Docker daemon - is it running, and is your user in the 'docker' group?"

docker compose version >/dev/null 2>&1 \
  || die "'docker compose' (Compose v2) not available - the legacy 'docker-compose' script is not supported, because this repository uses compose profiles and the long-form depends_on/condition syntax"

COMPOSE_V="$(docker compose version --short 2>/dev/null || echo unknown)"
ok "docker ok, compose ${COMPOSE_V}"

# --- 3. Configuration --------------------------------------------------------
# Raw .env: preflight runs before `resolve`, so .env.resolved does not exist yet.
load_env raw
ok "configuration loaded from ${ENV_LOADED_FROM#$REPO_ROOT/} (MODE=${MODE}, PACKAGE_SOURCE=${PACKAGE_SOURCE:-refv})"

require_var IRIS_IMAGE IRIS_VERSION TESTSUITE_IMAGE TESTSUITE_VERSION \
            IRIS_NS_STUFE3 IRIS_NS_STUFE5 FHIR_BASE_STUFE3 FHIR_BASE_STUFE5 \
            ISIK_SUBNET ISIK_GATEWAY IRIS_IP TESTSUITE_IP

case "${PACKAGE_SOURCE:-refv}" in
  refv|simplifier) ;;
  *) die "PACKAGE_SOURCE must be 'refv' or 'simplifier' (got: '${PACKAGE_SOURCE}')" ;;
esac

# The two FHIR endpoint paths must differ, or the second InstallInstance call
# overwrites the first and the ISiK 3 / ISiK 5 package split silently collapses.
[[ "$FHIR_BASE_STUFE3" != "$FHIR_BASE_STUFE5" ]] \
  || die "FHIR_BASE_STUFE3 and FHIR_BASE_STUFE5 must differ - the two levels pin incompatible de.basisprofil.r4 versions and need separate endpoints"
[[ "$IRIS_NS_STUFE3" != "$IRIS_NS_STUFE5" ]] \
  || die "IRIS_NS_STUFE3 and IRIS_NS_STUFE5 must differ, for the same reason"

# IRIS namespace names: uppercase alphanumeric, must start with a letter.
for ns in "$IRIS_NS_STUFE3" "$IRIS_NS_STUFE5"; do
  [[ "$ns" =~ ^[A-Za-z][A-Za-z0-9-]{0,29}$ ]] \
    || die "invalid IRIS namespace name '${ns}' - must start with a letter and contain only letters, digits and hyphens"
done

for base in "$FHIR_BASE_STUFE3" "$FHIR_BASE_STUFE5"; do
  [[ "$base" == /* ]] || die "FHIR base path '${base}' must start with '/'"
  [[ "$base" != */ ]] || die "FHIR base path '${base}' must not end with '/' - it is used verbatim as the IRIS web application name"
done

# --- 4. Licence key ----------------------------------------------------------
# The operator supplies a production IRIS licence as a file named iris.key in
# secrets/. The name is matched case-insensitively, because the file arrives from
# WRC or a colleague's mail client as often as not, and IRIS.key or Iris.Key is
# the same file. Its resolved path is exported for the compose mount rather than
# hard-coded, so the operator's file is used where it lies, under its own name.
#
# Deliberately NOT validated by content here. A licence key is only meaningfully
# valid if IRIS accepts it, and inspecting the file to guess whether it will is
# how a preflight check ends up rejecting a key that works - a Core Capacity key,
# for instance, carries no user-unit count at all. This step therefore establishes
# only that a non-empty file is present and that IRIS will be able to read it;
# whether the licence itself is usable is decided by IRIS at startup, and the
# bootstrap fails on IRIS's own licence error (see iris/bootstrap/bootstrap.sh).
KEY=""
while IFS= read -r candidate; do
  KEY="$candidate"
  break
done < <(find "$SECRETS_DIR" -maxdepth 1 -type f -iname 'iris.key' 2>/dev/null | sort)

[[ -n "$KEY" ]] \
  || die "IRIS licence key missing - put a production IRIS for Health licence key at secrets/iris.key (the name is matched case-insensitively). See secrets/README.md"
[[ -r "$KEY" ]] \
  || die "${KEY#$REPO_ROOT/} is not readable by the current user"
[[ -s "$KEY" ]] \
  || die "${KEY#$REPO_ROOT/} is empty - it must be the licence key file itself, not a placeholder"

IRIS_KEY_PATH="$KEY"
export IRIS_KEY_PATH
ok "licence key present at ${KEY#$REPO_ROOT/} ($(wc -c <"$KEY" | tr -d ' ') bytes)"

# The key is bind-mounted read-only into the container, and the mount preserves the
# host inode's owner and mode. IRIS runs as uid 51773 (`irisowner` in the official
# image), so a key that is mode 0400 owned by the deploying user - the natural thing
# to do with a licence file - is invisible to it.
#
# IRIS does not fail to start on that. It logs "LMF Error: No valid license key",
# falls back to a zero-capacity diagnostic licence, installs everything below
# perfectly, and then fails every single FHIR request with "ERROR #5915: Unable to
# allocate a license" - visible only in the audit log, and indistinguishable at the
# HTTP layer from an authentication or routing fault. So the permissions are fixed
# here rather than reported: this is a single-round lab environment whose access is
# controlled physically, the key is gitignored either way, and a manual chmod step
# on every fresh deployment host is exactly the kind of thing that gets skipped.
KEY_MODE="$(stat -c '%a' "$KEY" 2>/dev/null || stat -f '%Lp' "$KEY" 2>/dev/null || echo '')"
KEY_OWNER="$(stat -c '%u' "$KEY" 2>/dev/null || stat -f '%u' "$KEY" 2>/dev/null || echo '')"

if [[ -n "$KEY_MODE" && "${KEY_MODE: -1}" == 0 ]]; then
  chmod o+r "$KEY" 2>/dev/null \
    || die "${KEY#$REPO_ROOT/} is mode ${KEY_MODE} and cannot be made readable by uid ${IRIS_CONTAINER_UID} (chmod failed - are you the owner?).
  IRIS would run on a zero-capacity diagnostic licence and every FHIR request would
  fail with 'ERROR #5915: Unable to allocate a license'."
  NEW_MODE="$(stat -c '%a' "$KEY" 2>/dev/null || stat -f '%Lp' "$KEY" 2>/dev/null || echo '')"
  warn "${KEY#$REPO_ROOT/} was mode ${KEY_MODE}, which uid ${IRIS_CONTAINER_UID} inside the container cannot read - set to ${NEW_MODE} so IRIS can load the licence"
  KEY_MODE="$NEW_MODE"
fi

# Ownership only matters when the file is root-owned and group/other-unreadable, or
# when it lives on a mount that maps uids. chown needs privilege we may not have and
# is not required once the file is world-readable, so it is attempted only if the
# mode fix above was somehow not enough.
if [[ ! -r "$KEY" ]]; then
  chown "$(id -u)" "$KEY" 2>/dev/null \
    || die "cannot make ${KEY#$REPO_ROOT/} readable (owner uid ${KEY_OWNER:-unknown}, mode ${KEY_MODE:-unknown}) - copy it to secrets/ as your own user"
fi
ok "licence key readable by uid ${IRIS_CONTAINER_UID} inside the container (mode ${KEY_MODE:-unknown})"

# --- 5. Registry credential --------------------------------------------------
# Only relevant when the IRIS image comes from the licensed registry. A local or
# mirrored image (IRIS_IMAGE pointing elsewhere) needs no login.
IRIS_REGISTRY="${IRIS_IMAGE%%/*}"
if [[ "$IRIS_REGISTRY" == *.* ]]; then
  DOCKER_CFG="${DOCKER_CONFIG:-$HOME/.docker}/config.json"
  if [[ -f "$DOCKER_CFG" ]] && grep -q "$IRIS_REGISTRY" "$DOCKER_CFG"; then
    ok "registry credential for ${IRIS_REGISTRY} found in ${DOCKER_CFG/#$HOME/~}"
  else
    # A credential helper may keep the secret outside config.json, so this is a
    # warning rather than a hard failure - but the image pull will then say so.
    warn "no credential for ${IRIS_REGISTRY} in ${DOCKER_CFG/#$HOME/~}"
    warn "  run: docker login ${IRIS_REGISTRY}   (WRC username + registry token, not your WRC password)"
    warn "  ignore this if you use a credential helper or a pre-pulled image"
  fi
fi

# --- 6. Host port collisions -------------------------------------------------
# Only the ports this rig publishes. Checked with whatever is available: `ss`
# and `netstat` are both absent on some minimal hosts, in which case a bind test
# via python is used.
port_in_use() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -Hltn "sport = :${port}" 2>/dev/null | grep -q . && return 0 || return 1
  elif command -v netstat >/dev/null 2>&1; then
    netstat -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${port}\$" && return 0 || return 1
  else
    python3 - "$port" <<'PY'
import socket, sys
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.bind(("0.0.0.0", int(sys.argv[1])))
except OSError:
    sys.exit(0)   # in use
finally:
    s.close()
sys.exit(1)       # free
PY
  fi
}

PORT_CLASH=0
for p in "${IRIS_PORT_WEB:-52773}" "${IRIS_PORT_SUPER:-51972}" \
         "${TESTSUITE_PORT_WORKFLOW_UI:-9010}" "${TESTSUITE_PORT_PROXY_LOG:-9011}"; do
  if port_in_use "$p"; then
    warn "host port ${p} is already in use"
    PORT_CLASH=1
  fi
done
if (( PORT_CLASH )); then
  # Not fatal: the port may be held by a previous round of this same rig, which
  # `make clean` will release. Naming the remedy is more useful than refusing.
  warn "change the *_PORT_* values in .env, or run 'make clean' if a previous round is still up"
else
  ok "published host ports are free"
fi

# --- 7. Subnet collision -----------------------------------------------------
# A second docker network on the same CIDR would make the static IP assignment
# fail at container start with an opaque error; a host route on the same CIDR
# would break NAT. Both are worth catching here.
EXISTING_NET="$(docker network ls --format '{{.Name}}' | while read -r n; do
  cidr="$(docker network inspect "$n" --format '{{range .IPAM.Config}}{{.Subnet}} {{end}}' 2>/dev/null || true)"
  [[ " $cidr " == *" ${ISIK_SUBNET} "* ]] && printf '%s\n' "$n"
done || true)"

if [[ -n "$EXISTING_NET" ]]; then
  if [[ "$EXISTING_NET" == "${COMPOSE_PROJECT_NAME:-isik}-net" ]]; then
    ok "subnet ${ISIK_SUBNET} already held by our own network (${EXISTING_NET}) - will be reused"
  else
    die "subnet ${ISIK_SUBNET} is already used by docker network '${EXISTING_NET}' - pick a different ISIK_SUBNET in .env, or remove that network"
  fi
else
  ok "subnet ${ISIK_SUBNET} is free"
fi

# The declared addresses must actually lie inside the declared subnet; getting
# this wrong yields a container that never starts.
python3 - "$ISIK_SUBNET" "$ISIK_GATEWAY" "$IRIS_IP" "$TESTSUITE_IP" <<'PY' \
  || die "network addresses are inconsistent - see the message above"
import ipaddress, sys
net = ipaddress.ip_network(sys.argv[1], strict=True)
names = ("ISIK_GATEWAY", "IRIS_IP", "TESTSUITE_IP")
addrs = [ipaddress.ip_address(a) for a in sys.argv[2:]]
bad = [f"{n}={a} is not inside {net}" for n, a in zip(names, addrs) if a not in net]
if len(set(addrs)) != len(addrs):
    bad.append("ISIK_GATEWAY / IRIS_IP / TESTSUITE_IP must all differ")
if bad:
    print("\n".join("  " + b for b in bad), file=sys.stderr)
    sys.exit(1)
print(f"  {net}: gateway {addrs[0]}, IRIS {addrs[1]}, test suite {addrs[2]}")
PY
ok "network plan consistent"

# --- 8. Output directories ---------------------------------------------------
for d in "$VENDOR_DIR" "$RUN_DIR" "$REPORTS_DIR" "${RUN_DIR}/iris-state"; do
  mkdir -p "$d" || die "cannot create ${d#$REPO_ROOT/} - check filesystem permissions"
done
ok "output directories writable"

# --- 9. Disk space -----------------------------------------------------------
# Rough floor: the IRIS image is ~5 GB, the test suite image ~2 GB, the unpacked
# FHIR packages ~1.5 GB, plus IRIS databases and the report archives.
AVAIL_KB="$(df -Pk "$REPO_ROOT" | awk 'NR==2 {print $4}')"
NEED_KB=$(( 25 * 1024 * 1024 ))
if (( AVAIL_KB < NEED_KB )); then
  warn "only $(( AVAIL_KB / 1024 / 1024 )) GiB free at ${REPO_ROOT} - 25 GiB recommended (images ~7 GB, unpacked packages ~1.5 GB, IRIS databases, report archives)"
else
  ok "$(( AVAIL_KB / 1024 / 1024 )) GiB free"
fi

# --- 10. Memory, and the heap the scenarios get -------------------------------
# Warns rather than blocks, but this is the most consequential warning in the
# script. A Stufe 5 run that is short of heap does not fail: Tiger's RbelLogger
# retains a parsed tree of every HTTP message of the run, and when the heap fills
# its parser threads die and each subsequent scenario blocks for the full
# 100-second parse timeout. Throughput fell from ~12 scenarios/minute to ~0.5 on
# the lab host, which projects to hours past TESTSUITE_TIMEOUT - and a timed-out
# run yields NO archive, because Maven assembles it in post-integration-test. So
# the level is lost, after hours, with no error and the container at 0.3% CPU.
# TESTSUITE_HEAP_MAX in .env.example carries the full measurement.
MEM_KB="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)"
if (( MEM_KB > 0 )); then
  MEM_GIB=$(( MEM_KB / 1024 / 1024 ))
  if [[ -z "${TESTSUITE_HEAP_MAX:-}" ]]; then
    # Unset means the JVM decides, and it takes 1/4 of RAM - which is exactly the
    # value that was not enough on a 31 GiB host.
    warn "TESTSUITE_HEAP_MAX is unset, so the scenario JVM gets the JVM default of about $(( MEM_GIB / 4 )) GiB (1/4 of ${MEM_GIB} GiB). That default was NOT enough for Stufe 5 on a 31 GiB host: the run stalled at ~600 of 895 cases and would have produced no archive. Set TESTSUITE_HEAP_MAX=16g in .env"
  elif (( MEM_GIB < 24 )); then
    warn "${MEM_GIB} GiB RAM. Stufe 5 needs about 16 GiB of heap for the scenario JVM (TESTSUITE_HEAP_MAX=${TESTSUITE_HEAP_MAX}) plus room for IRIS (~2.2 GiB) and the Web Gateway; 24 GiB or more is recommended. Below that, expect the Rbel out-of-memory stall described in .env.example"
  else
    ok "${MEM_GIB} GiB RAM, scenario heap ${TESTSUITE_HEAP_MAX}"
  fi
else
  warn "cannot read /proc/meminfo - RAM not verified. Stufe 5 needs about 16 GiB of scenario heap; see TESTSUITE_HEAP_MAX in .env.example"
fi

step "Preflight passed"
