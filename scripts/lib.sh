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

# One labelled fact of a report, in the aligned two-column form `make status` and
# `make credentials` both print. On stdout, not stderr like the four above: these
# lines ARE the output of those two targets, and a tester who redirects them to a
# file expects the facts in the file and the progress log on the terminal. The
# column width is shared rather than per script so that two reports of the same
# environment can be read, and diffed, side by side.
kv() { printf '  %-26s %s\n' "$1" "$2"; }

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

# --- The shape of the pinned test suite -------------------------------------
# gematik/isik-testsuite 3.0.0 changed the suite's shape, not merely its content.
# ReleaseNotes.md for that release states both changes as breaking:
#
#   "Breaking Change: Tests for ISiK Level 3 have been removed"
#   "Breaking Change: Test Resources IDs have been renamed/modified"
#
# and the "removed" section names "ISiK Level 3 Modules, Validation Plugins and
# Settings". Measured against the 3.0.9 tag: the repository carries one Tiger
# configuration (tiger-isik-stufe5.yaml, no stufe3 counterpart), one validation
# plugin (refv-plugins/valmodule-isik5-refv-plugin.zip), and feature files only
# under src/test/resources/features/Stufe5/.
#
# The second shape change is the key namespace. Through 2.7.0 every test-data
# file was loaded under `baseKey: data`, so a feature referenced
# `${data.patient-read-id}`. From 3.0.0 each module file has its own baseKey
# (basis, dokument, medikation, terminplanung, vitalparameter, connect,
# icuminimal, icuextended, labor) and features reference
# `${basis.patient-read-id}`. This is not cosmetic: three key names carry
# different values in different namespaces in 3.0.9 -
#
#   observation-patient-id       icuextended: ICUext-PatientinMusterfrau-617
#                                icuminimal:  Patient-Read-Extended-Example
#                                labor:       Patient-Read-Example
#                                vitalparameter: Patient-Read-Extended-Example
#   observation-encounter-id     four namespaces, three distinct values
#   observation-practitioner-id  four namespaces, two distinct values
#
# so a flat key space would silently resolve one module's placeholder to another
# module's resource. Every tool in this rig therefore identifies a test-datum by
# its fully qualified `<baseKey>.<key>`, which is exactly the string the feature
# files write. For a 2.x suite that is `data.<key>` for every key, so the two
# generations share one implementation rather than branching.
#
# What this function selects is the set of inputs that differ between
# generations - the levels that exist, where the test-data contract lives, and
# which Tiger templates to render. It deliberately does NOT branch on behaviour:
# a round against 2.7.0 after this change renders and runs exactly as before.
suite_generation() {
  local v="${TESTSUITE_VERSION:-}"
  [[ -n "$v" ]] || die "TESTSUITE_VERSION must be set before the suite shape can be resolved"
  case "$v" in
    1.*|2.*)   printf '2x' ;;
    3.*)       printf '3x' ;;
    # A version this rig has never seen must stop the round rather than be
    # guessed at: the shape changed once already, between 2.7.0 and 3.0.0, and
    # the failure mode of guessing wrong is a round that measures the wrong
    # thing while reporting success.
    *) die "TESTSUITE_VERSION='${v}' belongs to no suite generation this rig knows.
  Known: 1.x and 2.x (two levels, single 'data' key namespace), 3.x (Stufe 5
  only, per-module key namespaces). Read the release's ReleaseNotes.md for
  shape changes, then extend suite_generation() in scripts/lib.sh." ;;
  esac
}

# The levels the pinned suite actually contains, as directory names. Scripts loop
# over this rather than over a literal "Stufe3 Stufe5", so that a level the suite
# does not carry is absent from the configuration, the seed, and the reports -
# rather than present and empty, which reads as a level that was run and scored
# nothing.
suite_levels() {
  case "$(suite_generation)" in
    2x) printf 'Stufe3 Stufe5' ;;
    3x) printf 'Stufe5' ;;
  esac
}

# Lower-case form, as used in file names (tiger-isik-stufe5.yaml, reports/stufe5/).
suite_levels_lc() {
  local l out=""
  for l in $(suite_levels); do
    out="${out}${out:+ }$(printf '%s' "$l" | tr '[:upper:]' '[:lower:]')"
  done
  printf '%s' "$out"
}

# Where the generation's test-data contract and Tiger templates live. Kept in
# separate trees rather than overwritten in place so that a round against 2.7.0
# remains re-creatable from this repository at any later commit - the 2026-08-07
# baseline round and this round's primary result both depend on that.
suite_testdata_root() {
  case "$(suite_generation)" in
    2x) printf '%s/testdata' "$REPO_ROOT" ;;
    3x) printf '%s/testdata/v3' "$REPO_ROOT" ;;
  esac
}

suite_tiger_dir() {
  case "$(suite_generation)" in
    2x) printf '%s/tiger' "$REPO_ROOT" ;;
    3x) printf '%s/tiger/v3' "$REPO_ROOT" ;;
  esac
}

# The authored FHIR bodies. The shared `common/` tree is generation-independent
# by construction - a body there carries {{<ns>.<key>}} placeholders and no
# literal ID - so both generations read it, and each adds its own tree for the
# resources only it needs.
suite_fhir_roots() {
  case "$(suite_generation)" in
    2x) printf '%s/testdata/fhir' "$REPO_ROOT" ;;
    3x) printf '%s/testdata/fhir %s/testdata/fhir/v3' "$REPO_ROOT" "$REPO_ROOT" ;;
  esac
}

# The test-data contract of the pinned release, as paths below testdata/ in the
# suite's own repository. 20-fetch-packages.sh downloads exactly this list from
# the release tag and 30-render-config.sh proves the staged copies equal it, so
# the list is the definition of what "unmodified contract" means for a round. It
# is enumerated rather than discovered, because a file silently missing from a
# directory listing would weaken the proof to "equal to whatever we found".
#
# 3.x: nine files, one per Level 5 module, Level 3 having been removed in 3.0.0.
# The four names after vitalparameter are the modules 3.0.0 added.
suite_contract_files() {
  case "$(suite_generation)" in
    2x) printf '%s\n' Stufe3/basis.yaml Stufe3/dokumentenaustausch.yaml \
                      Stufe3/medikation.yaml Stufe3/shared.yaml \
                      Stufe3/terminplanung.yaml Stufe3/vitalparameter.yaml \
                      Stufe5/basis.yaml Stufe5/dokumentenaustausch.yaml \
                      Stufe5/medikation.yaml Stufe5/terminplanung.yaml \
                      Stufe5/vitalparameter.yaml ;;
    3x) printf '%s\n' Stufe5/basis.yaml Stufe5/dokumentenaustausch.yaml \
                      Stufe5/medikation.yaml Stufe5/terminplanung.yaml \
                      Stufe5/vitalparameter.yaml Stufe5/connect.yaml \
                      Stufe5/icu-minimal.yaml Stufe5/icu-extended.yaml \
                      Stufe5/labor.yaml ;;
  esac
}

# Our own additions to the contract: one overlay file per namespace that needs a
# key the release defines nowhere. They are the only files 30-render-config.sh
# may stage without an upstream counterpart, so the list is also the parity
# check's allow-list - a file appearing beside them is an accident and stops the
# round. Each key in them is justified in docs/en/05-testdata.md.
#
# 2.x needs one key per level. 3.x needs two, and they must live in separate
# files because the overlay is loaded under the baseKey of the module whose key
# it completes: Location-read-active-id belongs to `basis`,
# RiskAssessment-read-id to `medikation`.
suite_overlay_files() {
  case "$(suite_generation)" in
    2x) printf '%s\n' Stufe3/zz-upstream-key-defects.yaml \
                      Stufe5/zz-upstream-key-defects.yaml ;;
    3x) printf '%s\n' Stufe5/zz-upstream-key-defects-basis.yaml \
                      Stufe5/zz-upstream-key-defects-medikation.yaml ;;
  esac
}

# The test-data namespaces the feature files of this generation reference, i.e.
# the `baseKey` of every entry in the Tiger configuration. A feature file writes
# ${<namespace>.<key>}, so this list is what makes a placeholder recognisable at
# all: a namespace missing from it turns every key of that module into
# unrecognised text, which seeds nothing and reads downstream as a resource the
# system under test does not have.
#
# 2.x loads all five of its files under the single namespace `data`. 3.0.0
# replaced that with one namespace per module; the names are upstream's and are
# not derivable from the file names (dokumentenaustausch.yaml -> `dokument`,
# icu-minimal.yaml -> `icuminimal`). 30-render-config.sh checks this list against
# the baseKeys of the rendered configuration, so the two cannot drift apart.
#
# Note that 45 of the 262 key names of 3.0.9 are defined in more than one module.
# Three of them - observation-encounter-id, observation-patient-id and
# observation-practitioner-id - carry *different* values per module, which is why
# a test datum is identified throughout this rig by the qualified
# <namespace>.<key> and never by the bare key.
suite_namespaces() {
  case "$(suite_generation)" in
    2x) printf 'data' ;;
    3x) printf 'basis dokument medikation terminplanung vitalparameter connect icuminimal icuextended labor' ;;
  esac
}

# Documentation baselines: the diff targets for this generation's Tiger
# templates. 3.0.0 removed tiger-isik-stufe3.yaml along with the level.
suite_tiger_baselines() {
  case "$(suite_generation)" in
    2x) printf '%s\n' tiger-isik-stufe3.yaml tiger-isik-stufe5.yaml serenity.properties ;;
    3x) printf '%s\n' tiger-isik-stufe5.yaml serenity.properties ;;
  esac
}

# --- The authorization server (ISiK Stufe 5 `Connect`) ----------------------
# Whether this round deploys an OAuth 2.0 authorization server, which is what
# the suite's `Connect` module needs and what no other module does.
#
# One function rather than `[[ "$CONNECT_ENABLE" == true ]]` in a dozen places:
# this is the switch that decides which compose files are in play, which
# configuration files are rendered, which TLS material is issued and which
# authentication posture the SUT is left in. A round is one configuration or the
# other, and every script must agree on which.
#
# Accepts true/1/yes in any case, because this value is typed into .env by hand.
# Anything else - including an empty or unset value - is false, so a round
# behaves exactly as it did before this feature existed unless it was asked not
# to. A value that is neither is rejected rather than guessed: `CONNECT_ENABLE=ture`
# must not silently produce a round that reports Connect scenarios as excluded.
connect_enabled() {
  case "$(printf '%s' "${CONNECT_ENABLE:-false}" | tr '[:upper:]' '[:lower:]')" in
    true|1|yes)   return 0 ;;
    false|0|no|'') return 1 ;;
    *) die "CONNECT_ENABLE='${CONNECT_ENABLE}' is not a boolean - use true or false" ;;
  esac
}

# The compose overlay that adds the authorization server and the TLS listener.
# Named once, here, so that compose(), `make clean` and 99-check.sh cannot
# disagree about it.
CONNECT_COMPOSE_FILE="compose/authserver.yml"

# The six access tokens the suite's Connect scenarios present, as
# `<testdata key>|<authorization server client id>|<SMART scope>`.
#
# This mapping is the single definition shared by three places that must not
# drift apart: the realm the authorization server imports (one client per
# scope), the mint step that fills the tokens in, and the check that the two
# agree. The keys are the suite's own, from
# testdata/v3/Stufe5/connect.yaml - they are a contract with the test suite and
# not ours to rename.
connect_token_clients() {
  cat <<'EOF'
read-patient-scope-allowed|isik-connect-patient-patient-rs|patient/Patient.rs
read-patient-wildcard-scope-allowed|isik-connect-patient-wildcard-rs|patient/*.rs
read-patient-scope-in-user-context-allowed|isik-connect-user-patient-rs|user/Patient.rs
read-patient-wildcard-scope-in-user-context-allowed|isik-connect-user-wildcard-rs|user/*.rs
read-patient-scope-in-system-context-allowed|isik-connect-system-patient-rs|system/Patient.rs
read-wildcard-scope-in-system-context-allowed|isik-connect-system-wildcard-rs|system/*.rs
EOF
}

# The two credentials a round withholds from everything it publishes, asserted
# over paths that are about to be published or have just been written.
#
# A proof rather than a claim: the bundle states that no access token and no
# realm secret of this round is readable in it, and three scripts write into a
# bundle - 70-collect-report.sh, then 80-gap-report.sh and 81-case-evidence.sh,
# which append to it afterwards. Each gates its own output through this, so the
# statement holds for the finished bundle and not only for the part one stage
# produced. scripts/lib/redact_credentials.py sets out which two credentials
# these are and why they are the exception to the rig's policy of publishing a
# round's credentials on purpose.
#
# Arguments are the paths to check, optionally followed by --exempt <substring>
# for a subtree that is deliberately verbatim (the official archives).
#
# The values are passed through the environment, never on a command line.
assert_no_published_credentials() {
  AS_CLIENT_SECRET="${AS_CLIENT_SECRET:-}" AS_ADMIN_PASSWORD="${AS_ADMIN_PASSWORD:-}" \
  python3 "${SCRIPTS_DIR}/lib/redact_credentials.py" scan "$@" \
    --connect-yaml "${RUN_DIR}/testdata/Stufe5/connect.yaml" \
    --env-secret AS_CLIENT_SECRET --env-secret AS_ADMIN_PASSWORD
}

# --- Compose ----------------------------------------------------------------
# Always both files, always from the repository root: compose/testsuite.yml uses
# project-root-relative bind mounts, and compose resolves those against the
# directory of the FIRST -f argument.
#
# The authorization server overlay is appended only for a round that asked for
# it. Its absence - not a profile, not a variable - is what makes a round without
# it byte-identical to every round taken before the overlay existed.
compose() {
  local env_file="$ENV_RESOLVED_FILE"
  [[ -f "$env_file" ]] || env_file="$ENV_FILE"
  local -a files=( -f docker-compose.yml -f compose/testsuite.yml )
  if connect_enabled; then
    files+=( -f "$CONNECT_COMPOSE_FILE" )
  fi
  ( cd "$REPO_ROOT" && docker compose \
      --env-file "$env_file" \
      "${files[@]}" \
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

# --- The SUT's FHIR endpoints -----------------------------------------------
# The configured base path of one level's endpoint, under every spelling of the
# level the rest of the rig uses. The three URL builders below differ in scheme,
# host and port and agree on this; written out once so that a level accepted by
# one of them cannot be rejected by another, which would make a round's URLs
# disagree between the rendered configuration, the seeder and the status report.
#
# The refusal is named after the caller rather than after this function, so that
# each of the three states the message it always stated: the operator needs to
# know which URL could not be built, not where the lookup lives.
fhir_base_path() {
  case "$1" in
    stufe3|Stufe3|3) printf '%s' "${FHIR_BASE_STUFE3:?}" ;;
    stufe5|Stufe5|5) printf '%s' "${FHIR_BASE_STUFE5:?}" ;;
    *) die "${FUNCNAME[1]}: unknown Stufe '$1'" ;;
  esac
}

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
  # `|| exit 1` and not a bare assignment: fhir_base_path refuses an unknown
  # level by calling die, which exits the subshell the substitution runs in, and
  # without this the caller would carry on and print a URL with no path in the
  # one context where `set -e` is suspended - a condition.
  local path
  path="$(fhir_base_path "$1")" || exit 1
  printf 'http://%s:%s%s' "${WEBGATEWAY_IP:?WEBGATEWAY_IP must be set}" \
                          "${WEBGATEWAY_PORT_INTERNAL:-80}" "$path"
}

# The same endpoint as reachable from the HOST (published port), used by the
# seeder and by status checks, which run outside the subnet. The published port
# keeps the name IRIS_PORT_WEB and the default 52773 - it is the FHIR server's
# port as far as every operator, document and firewall rule is concerned - but it
# now maps to the gateway's :80 rather than to IRIS.
sut_base_url_host() {
  local path
  path="$(fhir_base_path "$1")" || exit 1
  printf 'http://127.0.0.1:%s%s' "${IRIS_PORT_WEB:-52773}" "$path"
}

# The same endpoint over TLS, as the Connect module must address it.
#
# A separate function rather than a scheme argument to sut_base_url(): the host
# name differs too. TLS is verified against the certificate's subjectAltName, so
# the URL has to carry the name scripts/22-make-tls.sh issued the certificate
# for, whereas the plain-HTTP URL carries the gateway's IP address - which is
# what the other nine modules' rendered configuration and documentation show,
# and which must not change.
#
# Only meaningful for a round with CONNECT_ENABLE=true; the listener does not
# exist otherwise.
sut_base_url_tls() {
  local path
  path="$(fhir_base_path "$1")" || exit 1
  # The port is omitted when it is the default for the scheme, so that the URL,
  # the certificate's subject and the token audience read the same in the
  # evidence. A non-default port is spelled out, because a token minted for the
  # one would not be accepted at the other.
  local host="${SUT_TLS_HOST:?SUT_TLS_HOST must be set - see .env.example}"
  local port="${SUT_PORT_HTTPS_INTERNAL:-443}"
  if [[ "$port" == "443" ]]; then
    printf 'https://%s%s' "$host" "$path"
  else
    printf 'https://%s:%s%s' "$host" "$port" "$path"
  fi
}

# An authenticated GET against the SUT, in the two forms the rig asks for: the
# status code alone, and the body. Both are used by `make up`'s startup gate and
# by `make status`, which had their own copy of the credential and of the FHIR
# content negotiation - four copies of a request that has to stay identical,
# because the whole point of the status report is that it probes the same way the
# gate did and therefore explains what the gate saw.
#
# Unauthenticated is not an option: the endpoints require a credential, and an
# anonymous probe reports 401 everywhere and says nothing about whether the
# server works.
#
# One timeout, at the longer of the two values the copies used. A probe that
# gives up early on a slow endpoint reports it as unreachable, which is a worse
# answer than a slow one.
FHIR_PROBE_TIMEOUT=30

# The status of the GET, or 000 when the request did not complete at all -
# connection refused, no route, timeout. Never fails, so a caller reads one code
# and decides, rather than having to tell a curl failure from an HTTP refusal.
fhir_http_code() {
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time "$FHIR_PROBE_TIMEOUT" \
      -u "${SUT_USER}:${SUT_PASSWORD}" \
      -H 'Accept: application/fhir+json' "$1" 2>/dev/null)" || code=000
  printf '%s' "$code"
}

# The body of the same GET. Empty on failure, which every caller here treats as
# "not the document I expected" - they parse it before believing it.
fhir_response_body() {
  curl -s --max-time "$FHIR_PROBE_TIMEOUT" \
      -u "${SUT_USER}:${SUT_PASSWORD}" \
      -H 'Accept: application/fhir+json' "$1" 2>/dev/null
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

# --- Documents generated after the bundle was sealed -------------------------
# True when a --out-md or --out-json was passed through to one of the document
# generators, in which case that generator wrote somewhere else, the default
# paths inside the round are legitimately absent, and there is nothing for the
# calling script to check, hash or record.
generator_output_redirected() {
  [[ "$*" == *--out-md* || "$*" == *--out-json* ]]
}

# Record documents generated AFTER the bundle manifest was written.
#
# analysis/MANIFEST.txt is written by 70-collect-report.sh, and both the gap
# analysis and the case-evidence annex are generated after it, so an inspector
# verifying the bundle would find unlisted files - which looks exactly like
# tampering. Their hashes are appended here in the same `sha256sum`-compatible
# format and with the same './' prefix, so that `sha256sum -c
# analysis/MANIFEST.txt` still passes on the finished bundle.
#
# Appending is safe against re-runs: the previous entries for these paths are
# dropped first, so re-generating a document updates its hashes rather than
# accumulating stale ones. The result is re-sorted into the byte order
# 70-collect-report.sh produced (LC_ALL=C sort over the find output), which is
# what makes two rounds' manifests diffable.
#
# Returns non-zero, and changes nothing, on a round that has no manifest at all -
# a bundle `make report` never completed. The caller names the document it could
# not record, because that is what the operator has to act on.
amend_bundle_manifest() {
  local run_dir="$1"; shift
  local manifest="${run_dir}/analysis/MANIFEST.txt"
  [[ -f "$manifest" ]] || return 1

  # The names go into a regular expression, so their dots are escaped: an
  # unescaped `gap-analysis.json` would also drop a `gap-analysis-json` entry.
  local f alt=''
  for f in "$@"; do
    alt+="${alt:+|}${f//./\\.}"
  done

  local tmp
  tmp="$(mktemp "${manifest}.XXXXXX")"
  grep -v -E "[[:space:]]\./(${alt})\$" "$manifest" >"$tmp" || true
  ( cd "$run_dir" \
    && for f in "$@"; do
         [[ -f "$f" ]] && printf '%s  ./%s\n' "$(sha256_of "$f")" "$f"
       done ) >>"$tmp"
  LC_ALL=C sort -k2 "$tmp" >"$manifest"
  rm -f "$tmp"
  log "recorded $# file(s) in analysis/MANIFEST.txt ($(wc -l <"$manifest" | tr -d ' ') entries)"
}
