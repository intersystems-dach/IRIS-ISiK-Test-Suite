#!/usr/bin/env bash
# =============================================================================
# Shared helpers for scripts/*.sh
# =============================================================================
# Sourced, never executed. Deliberately POSIX-ish bash with no dependencies
# beyond coreutils, curl, tar, unzip and docker, so that the whole rig runs on a
# minimally provisioned deployment host.
#
#   . "$(dirname "$0")/lib.sh"
#
# Provides: logging (log/warn/die/step), env loading (load_env/require_var),
# tool checks (need_cmd), compose invocation (compose), and the run directory
# layout (run_dir/report_dir).
# =============================================================================

set -euo pipefail

# --- Locations ---------------------------------------------------------------
# REPO_ROOT is exported by the Makefile. When a script is run by hand it is
# derived from the script's own location, so `bash scripts/00-preflight.sh` works
# from any working directory.
if [[ -z "${REPO_ROOT:-}" ]]; then
  REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi
export REPO_ROOT

SCRIPTS_DIR="${REPO_ROOT}/scripts"
ENV_FILE="${REPO_ROOT}/.env"
ENV_RESOLVED_FILE="${REPO_ROOT}/.env.resolved"
VENDOR_DIR="${REPO_ROOT}/vendor"
PACKAGES_DIR="${VENDOR_DIR}/packages"
RUN_DIR="${REPO_ROOT}/run"
REPORTS_DIR="${REPO_ROOT}/reports"
SECRETS_DIR="${REPO_ROOT}/secrets"
export SCRIPTS_DIR ENV_FILE ENV_RESOLVED_FILE VENDOR_DIR PACKAGES_DIR
export RUN_DIR REPORTS_DIR SECRETS_DIR

# The uid IRIS runs as inside the official images (the `irisowner` account,
# `User=51773` in the image config). Bind mounts keep the host inode's owner and
# mode, so anything mounted for IRIS to read must be readable by this uid - see
# the licence-key check in 00-preflight.sh for the failure this prevents.
#
# Defaulted rather than assigned: it is an operator-facing setting in .env.example
# (a future image could change the account), so a value already in the environment
# has to win over this fallback.
IRIS_CONTAINER_UID="${IRIS_CONTAINER_UID:-51773}"
export IRIS_CONTAINER_UID

# --- Logging ----------------------------------------------------------------
# Colour only on a TTY, so captured logs (which end up in the evidence bundle)
# stay free of escape sequences.
if [[ -t 2 ]]; then
  _C_RESET=$'\033[0m'; _C_BOLD=$'\033[1m'
  _C_RED=$'\033[31m';  _C_YEL=$'\033[33m'; _C_GRN=$'\033[32m'; _C_DIM=$'\033[2m'
else
  _C_RESET=''; _C_BOLD=''; _C_RED=''; _C_YEL=''; _C_GRN=''; _C_DIM=''
fi

_ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }

log()  { printf '%s[%s] %s%s\n' "$_C_DIM" "$(_ts)" "$*" "$_C_RESET" >&2; }
ok()   { printf '%s[%s] OK   %s%s\n' "$_C_GRN" "$(_ts)" "$*" "$_C_RESET" >&2; }
warn() { printf '%s[%s] WARN %s%s\n' "$_C_YEL" "$(_ts)" "$*" "$_C_RESET" >&2; }
step() { printf '\n%s=== %s ===%s\n' "$_C_BOLD" "$*" "$_C_RESET" >&2; }

# die prints to stderr and exits 1. Every failure path in this repository goes
# through it so that a failed run always states what was expected and what to do
# about it - the deployment host is remote and may not be ours to debug.
die() {
  printf '%s[%s] FAIL %s%s\n' "$_C_RED" "$(_ts)" "$*" "$_C_RESET" >&2
  exit 1
}

# --- Tool checks ------------------------------------------------------------
need_cmd() {
  local cmd="$1" hint="${2:-}"
  command -v "$cmd" >/dev/null 2>&1 && return 0
  die "required command not found: ${cmd}${hint:+ - ${hint}}"
}

# --- Environment ------------------------------------------------------------
# load_env sources the deploy configuration. Prefers .env.resolved (written by
# `make resolve`, includes image digests) and falls back to .env. Every target
# after `resolve` therefore reads the frozen record rather than the editable
# file, so a mid-round edit to .env cannot silently change what is being tested.
#
#   load_env            prefer .env.resolved, fall back to .env
#   load_env raw        force .env (used by 10-resolve-versions.sh itself)
#
# Command-line overrides. Sourcing the file with `set -a` overwrites anything the
# caller exported, so a `FOO=bar make target` for a variable the file also defines
# is silently discarded. Two of those - MODE and PACKAGE_SOURCE - are documented as
# overridable and are handled by the resolver, which rewrites their lines when it
# writes .env.resolved. The RUN_OVERRIDABLE list below extends that to the knobs
# that describe how a level is *run* rather than what is being tested, so that
#
#   TESTSUITE_HEAP_MAX=32g make test-stufe5 RUN_ID=<existing round>
#
# does what it says. Re-running one level of a finished round with more heap is the
# natural response to the out-of-memory stall documented in
# docs/en/06-execution.md, and before this it changed nothing while reporting
# success - the container started with the file's old value and the operator had no
# way to see it. Anything NOT on this list stays file-only on purpose: a round must
# not be able to test one thing and record another, so version and endpoint settings
# are changed by editing .env and re-resolving, which updates the provenance too.
RUN_OVERRIDABLE=(TESTSUITE_HEAP_MAX TESTSUITE_TIMEOUT IRIS_BOOTSTRAP_TIMEOUT MANUAL_STUFE)

# MODE and PACKAGE_SOURCE, the two the paragraph above calls "documented as
# overridable", get the same treatment - and did not, which is the same defect over
# again. The resolver rewrites their lines when it writes .env.resolved, so an
# override handed to `make resolve` works; handed to any LATER phase it was silently
# discarded, because that phase loads .env.resolved and `set -a` overwrote the
# exported value with the file's.
#
#   PACKAGE_SOURCE=simplifier make packages
#
# ran the refv route and said PACKAGE_SOURCE=refv while doing it. Found on the
# official 2026.2 round when regenerating the parity table after the round, which is
# exactly when it is needed: PARITY.txt is evidence ABOUT the packages, and
# producing it must not require editing .env.resolved and thereby rewriting the
# finished round's own provenance record.
#
# These are kept in a separate list from RUN_OVERRIDABLE because they are not
# run-behaviour knobs: PACKAGE_SOURCE changes which payload bytes are loaded. An
# override is therefore logged, recorded in ENV_OVERRIDES (hence in run.json), and
# stamped into the MANIFEST.txt header of the pass that used it - so the round can
# still not test one thing and record another, which is the property the file-only
# rule was protecting.
CONFIG_OVERRIDABLE=(MODE PACKAGE_SOURCE)

load_env() {
  local which="${1:-resolved}" file='' v
  # Captured BEFORE the file is sourced; empty means "no override given", which must
  # not clobber the file's value. Held in a global so the report can state it.
  local -a saved=()
  for v in "${RUN_OVERRIDABLE[@]}" "${CONFIG_OVERRIDABLE[@]}"; do saved+=("${!v:-}"); done

  if [[ "$which" == raw ]]; then
    file="$ENV_FILE"
  elif [[ -f "$ENV_RESOLVED_FILE" ]]; then
    file="$ENV_RESOLVED_FILE"
  else
    file="$ENV_FILE"
  fi

  [[ -f "$file" ]] || die "configuration file missing: ${file#$REPO_ROOT/} - run: cp .env.example .env"

  # `set -a` exports everything the file defines, which is what compose and the
  # containers need. The file is trusted operator input (it is not fetched), so
  # sourcing it is acceptable and keeps `#` comments and defaults working.
  set -a
  # shellcheck disable=SC1090  # path is computed, by design
  . "$file"
  set +a

  # Reinstate the overrides the file just overwrote, and say so: an override that
  # takes effect invisibly is only marginally better than one that is ignored.
  ENV_OVERRIDES=''
  local i=0
  for v in "${RUN_OVERRIDABLE[@]}" "${CONFIG_OVERRIDABLE[@]}"; do
    if [[ -n "${saved[$i]}" && "${saved[$i]}" != "${!v:-}" ]]; then
      log "override: ${v}=${saved[$i]} (${file#$REPO_ROOT/} says ${!v:-unset})"
      ENV_OVERRIDES="${ENV_OVERRIDES}${ENV_OVERRIDES:+ }${v}=${saved[$i]}"
      printf -v "$v" '%s' "${saved[$i]}"
      export "${v?}"
    fi
    i=$(( i + 1 ))
  done
  export ENV_OVERRIDES

  ENV_LOADED_FROM="$file"
  export ENV_LOADED_FROM

  # MODE and PACKAGE_SOURCE may be overridden on the make/shell command line.
  # The Makefile exports them (possibly empty); an empty override must not
  # clobber the file's value.
  [[ -n "${MODE:-}" ]] || die "MODE is not set in ${file#$REPO_ROOT/}"
  case "$MODE" in
    latest|pinned|manual) ;;
    *) die "MODE must be one of: latest, pinned, manual (got: '$MODE')" ;;
  esac
}

require_var() {
  local name
  for name in "$@"; do
    [[ -n "${!name:-}" ]] || die "${name} must be set in ${ENV_LOADED_FROM:-.env}"
  done
}

# --- Compose ----------------------------------------------------------------
# Always both files, always from the repository root: compose/testsuite.yml uses
# project-root-relative bind mounts, and compose resolves those against the
# directory of the FIRST -f argument.
compose() {
  local env_file="$ENV_RESOLVED_FILE"
  [[ -f "$env_file" ]] || env_file="$ENV_FILE"
  ( cd "$REPO_ROOT" && docker compose \
      --env-file "$env_file" \
      -f docker-compose.yml \
      -f compose/testsuite.yml \
      "$@" )
}

# --- Run identity -----------------------------------------------------------
# One RUN_ID per test round, shared by the Stufe 3 and Stufe 5 reports so that
# one round is one directory under reports/. Generated on first use if the
# Makefile did not supply one (i.e. when a script is run by hand).
run_id() {
  if [[ -z "${RUN_ID:-}" ]]; then
    RUN_ID="run-$(date -u +%Y%m%dT%H%M%SZ)"
    export RUN_ID
  fi
  printf '%s' "$RUN_ID"
}

run_report_dir() { printf '%s/%s' "$REPORTS_DIR" "$(run_id)"; }

# --- Misc -------------------------------------------------------------------
# The SUT base URL as seen from inside the isolated subnet. The test suite and
# the seeder both address IRIS by its static IP rather than by container name:
# the same literal string then appears in the rendered Tiger configuration, in
# the seed log and in the documentation, which is what an inspector cross-checks.
# The FHIR endpoint as reachable from INSIDE the subnet, which is what the test
# suite uses.
#
# It addresses the Web Gateway, not IRIS. The official IRIS for Health images ship
# no web server at all - verified on 2026.2 and 2025.3: only 1972 (superserver) and
# 2188 (arbiter) listen, there is no httpd/apache2/nginx binary anywhere in the
# image, iris.cpf has WebServer=0, and parameters.isc records
# `csp_gateway.configure_apache: N`. The images nonetheless declare 52773/tcp in
# ExposedPorts and label it as the webserver port, which is what makes this
# surprising rather than obvious. The supported way to put HTTP in front of IRIS is
# the separate official webgateway image, so the rig runs one; see the `webgateway`
# service in docker-compose.yml and docs/en/01-architecture.md.
sut_base_url() {
  local stufe="$1" path
  case "$stufe" in
    stufe3|Stufe3|3) path="${FHIR_BASE_STUFE3:?}" ;;
    stufe5|Stufe5|5) path="${FHIR_BASE_STUFE5:?}" ;;
    *) die "sut_base_url: unknown Stufe '$stufe'" ;;
  esac
  printf 'http://%s:%s%s' "${WEBGATEWAY_IP:?WEBGATEWAY_IP must be set}" \
                          "${WEBGATEWAY_PORT_INTERNAL:-80}" "$path"
}

# The same endpoint as reachable from the HOST (published port), used by the
# seeder and by status checks, which run outside the subnet. The published port
# keeps the name IRIS_PORT_WEB and the default 52773 - it is the FHIR server's
# port as far as every operator, document and firewall rule is concerned - but it
# now maps to the gateway's :80 rather than to IRIS.
sut_base_url_host() {
  local stufe="$1" path
  case "$stufe" in
    stufe3|Stufe3|3) path="${FHIR_BASE_STUFE3:?}" ;;
    stufe5|Stufe5|5) path="${FHIR_BASE_STUFE5:?}" ;;
    *) die "sut_base_url_host: unknown Stufe '$stufe'" ;;
  esac
  printf 'http://127.0.0.1:%s%s' "${IRIS_PORT_WEB:-52773}" "$path"
}

# --- Credentials ------------------------------------------------------------
# One instance-specific password, in the form Kmrf-7xtq-Vhn3-Pd2s: four groups of
# four, hyphen-separated. Every instance of this lab environment generates its own,
# and every one of them is documented in the reports (see the CREDENTIAL HANDLING
# note in scripts/70-collect-report.sh), so the shape optimises for a human reading
# it off a screen and typing it into a browser or a curl command:
#
#   * ambiguous glyphs are excluded - no O/0, no I/l/1 - because a credential a
#     tester mistypes is indistinguishable from an authorisation gap in the SUT
#   * grouping makes it transcribable in chunks
#   * complexity is guaranteed BY CONSTRUCTION, not by luck: one group is forced
#     lower-case, one upper-case, one numeric, and the '-' separators supply the
#     punctuation class. IRIS's default PasswordPattern is 3.32ANP (3-32 chars,
#     alphanumeric plus punctuation), so the generated value always satisfies it
#     and the instance's password policy does NOT have to be relaxed - the SUT
#     keeps a production-like security configuration.
#   * '-' is the only punctuation used, so the value is safe unquoted in .env, in
#     `curl -u`, in the YAML templates and in compose interpolation, and it is in
#     ObjectScript's 1P class, which Security.Users accepts.
#
# 16 characters over a 55-symbol alphabet: ~92 bits, which is far beyond what this
# environment needs but costs nothing.
PW_ALPHA_LOWER='abcdefghjkmnpqrstuvwxyz'
PW_ALPHA_UPPER='ABCDEFGHJKLMNPQRSTUVWXYZ'
PW_ALPHA_DIGIT='23456789'

gen_password() {
  local groups=() i
  # Groups 1-3 each guarantee one character class; group 4 is mixed. Which group
  # gets which class is fixed rather than shuffled: a predictable *shape* is not a
  # weakness (the characters are the entropy) and it makes the result easier to
  # read back.
  groups+=("$(_pw_chars "$PW_ALPHA_LOWER" 4)")
  groups+=("$(_pw_chars "$PW_ALPHA_UPPER" 4)")
  groups+=("$(_pw_chars "$PW_ALPHA_DIGIT" 4)")
  groups+=("$(_pw_chars "${PW_ALPHA_LOWER}${PW_ALPHA_UPPER}${PW_ALPHA_DIGIT}" 4)")

  local out=''
  for i in "${groups[@]}"; do
    [[ ${#i} -eq 4 ]] || die "gen_password: /dev/urandom yielded a short group - cannot generate a password"
    out+="${out:+-}${i}"
  done
  printf '%s' "$out"
}

# n characters drawn from the given alphabet. `head -c` closes the pipe early,
# which makes tr exit non-zero under pipefail, so the pipeline is run in a
# subshell with pipefail off rather than being allowed to abort the caller.
_pw_chars() {
  local alphabet="$1" n="$2"
  ( set +o pipefail
    LC_ALL=C tr -dc "$alphabet" </dev/urandom 2>/dev/null | head -c "$n" )
}

# Resolve one credential: an explicit value wins, then an existing file (so a
# re-run, a manual curl and the rendered configuration all agree), then a freshly
# generated one. Prints the password; writes it to the file when it generated one.
#
#   password="$(resolve_password "$explicit" "$file" "human-readable purpose")"
#
# The file is written under `umask 077`. That is not because the value is a
# meaningful secret in this environment - it is documented in the reports on
# purpose - but because the live environment must keep production-like file modes
# to be a faithful stand-in for a production deployment.
resolve_password() {
  local explicit="${1:-}" file="$2" what="$3" value='' src=''
  src="${ENV_LOADED_FROM:-${ENV_FILE}}"

  if [[ -n "$explicit" ]]; then
    log "${what}: using the value configured in ${src#$REPO_ROOT/}"
    printf '%s' "$explicit"
    return 0
  fi

  if [[ -s "$file" ]]; then
    value="$(tr -d '\r\n' <"$file")"
    log "${what}: using the existing ${file#$REPO_ROOT/}"
    printf '%s' "$value"
    return 0
  fi

  value="$(gen_password)"
  ( umask 077; printf '%s\n' "$value" >"$file" )
  ok "${what}: generated ${value} and stored it in ${file#$REPO_ROOT/}"
  printf '%s' "$value"
}

# sha256 of a file, bare hex. Used for the provenance/checksum tables that the
# results documentation cites.
sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  else
    shasum -a 256 "$1" | cut -d' ' -f1
  fi
}
