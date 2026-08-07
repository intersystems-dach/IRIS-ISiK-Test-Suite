#!/usr/bin/env bash
# =============================================================================
# 10-resolve-versions.sh - freeze what is being tested into .env.resolved
# =============================================================================
# Writes .env.resolved: a copy of the operator's .env with the version fields
# resolved, plus the image digests. Everything downstream reads this file, and the
# generated documentation cites it. It is the provenance record for the round - the
# answer to "what exactly did you test?".
#
# Resolution is decided PER AXIS, not by MODE alone, because the two axes are not
# equally safe to float:
#
#   the SUT (IRIS + Web Gateway)     floats when MODE=latest, on IRIS_VERSION_TRACK.
#                                    Free: the build under test is whatever is newest
#                                    on the track, and the digest recorded below
#                                    identifies it exactly either way.
#
#   the test suite (isik-testsuite)  floats ONLY when TESTSUITE_VERSION_TRACK=latest,
#                                    which no default sets. Not free: this repository's
#                                    authored test-data corpus is coupled to one
#                                    release's feature files, its ${data.*} contract,
#                                    its scenario counts and its by-version upstream
#                                    defect workarounds. A release bump has to be
#                                    re-verified by hand - see INSTALL.md.
#
# So MODE=latest means "newest SUT build, pinned test definition", MODE=pinned means
# both taken verbatim from .env, and MODE=manual is pinned with different run
# behaviour. When the suite is pinned, the newest available release is looked up
# anyway and reported as a notice, so that a stale pin is visible rather than silent.
#
# All three images are pulled here, because a digest can only be recorded for an
# image that is actually present, and because failing on a bad tag or a missing
# registry credential is much cheaper now than after the FHIR packages have been
# unpacked.
# =============================================================================

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

step "Resolving versions (MODE=${MODE:-from .env})"

load_env raw
require_var IRIS_IMAGE TESTSUITE_IMAGE TESTSUITE_REPO WEBGATEWAY_IMAGE IRIS_IP

# -----------------------------------------------------------------------------
# Version resolution
# -----------------------------------------------------------------------------

# Newest published release tag of gematik/isik-testsuite. The GitHub "releases/
# latest" endpoint already excludes drafts and pre-releases, which is exactly the
# "latest stable" semantics we want.
#
# Called two ways, which is why the failure mode is a parameter. With $1 empty it is
# resolving the version to use and unreachable means the round cannot proceed as
# configured. With $1 = "soft" it is only checking whether the pin is stale, and
# unreachable is of no consequence: it prints nothing and returns 1.
resolve_latest_testsuite() {
  local soft="${1:-}"
  local api="https://api.github.com/repos/${TESTSUITE_REPO}/releases/latest" body tag
  if ! body="$(curl -fsSL -H 'Accept: application/vnd.github+json' "$api" 2>/dev/null)"; then
    if [[ "$soft" == soft ]]; then return 1; fi
    die "cannot reach ${api} - TESTSUITE_VERSION_TRACK=latest needs internet access; leave it empty to use the pinned TESTSUITE_VERSION"
  fi
  tag="$(printf '%s' "$body" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tag_name",""))' 2>/dev/null)" || true
  if [[ -z "$tag" ]]; then
    if [[ "$soft" == soft ]]; then return 1; fi
    die "could not read tag_name from ${api}"
  fi
  # Upstream tags releases as a bare version ("2.7.0"); tolerate a leading "v".
  printf '%s' "${tag#v}"
}

# Newest tag on the configured track for an InterSystems registry image. The
# registry implements the Docker Registry v2 API and requires a bearer token
# obtained with the same credentials `docker login` stored, so the credential is
# read from the Docker config rather than asked for again.
#
# Parameterised over the image because two images are resolved this way: IRIS for
# Health and the Web Gateway, both from containers.intersystems.com and both
# version-tracked. Every fallback path returns the caller's pinned version, so a
# registry that cannot be enumerated degrades to MODE=pinned behaviour for that one
# image rather than failing the round.
#
#   $1  image reference without tag (e.g. containers.intersystems.com/intersystems/irishealth)
#   $2  track prefix (e.g. 2026)
#   $3  pinned version to fall back to
#   $4  human label for messages (e.g. "IRIS")
resolve_latest_tag() {
  local image="$1" track="$2" fallback="$3" label="$4"
  local registry="${image%%/*}" repo="${image#*/}"
  local cfg="${DOCKER_CONFIG:-$HOME/.docker}/config.json"
  local auth tags newest

  [[ -n "$track" ]] || die "MODE=latest needs a version track for ${label} in .env (e.g. 2026)"

  auth="$(python3 - "$cfg" "$registry" <<'PY'
import json, sys
try:
    with open(sys.argv[1]) as fh:
        cfg = json.load(fh)
except Exception:
    sys.exit(0)
for host, entry in (cfg.get("auths") or {}).items():
    if sys.argv[2] in host and entry.get("auth"):
        print(entry["auth"])
        break
PY
)" || true

  if [[ -z "$auth" ]]; then
    warn "no usable credential for ${registry} in ${cfg/#$HOME/~} (a credential helper stores it outside config.json)"
    warn "cannot enumerate ${label} tags; falling back to the pinned ${label} version ${fallback:-unset} from .env"
    printf '%s' "${fallback:?a pinned ${label} version must be set when tag enumeration is unavailable}"
    return 0
  fi

  # Token exchange, then the tag list. Failure at either step is not fatal: the
  # pinned version from .env is a correct, if less current, answer.
  #
  # The token endpoint is *discovered*, not assumed. Distribution-spec registries
  # advertise it in the WWW-Authenticate challenge on an unauthenticated /v2/, and
  # the path is registry-specific: containers.intersystems.com answers
  # realm="https://containers.intersystems.com/v2/token", while the more common
  # layout (and Docker Hub's) puts it at /token or on a separate auth host. Guessing
  # produced a 404 whose only visible effect was a silent fall back to the pinned
  # version - the resolution looked like it had happened when it had not.
  local realm token
  realm="$(curl -sSI "https://${registry}/v2/" 2>/dev/null \
    | tr -d '\r' \
    | sed -n 's/^[Ww][Ww][Ww]-[Aa]uthenticate:.*realm="\([^"]*\)".*/\1/p' \
    | head -n1)"
  if [[ -z "$realm" ]]; then
    warn "${registry} advertised no token realm on /v2/; falling back to the pinned ${label} version ${fallback:-unset}"
    printf '%s' "${fallback:?}"
    return 0
  fi
  token="$(curl -fsSL -H "Authorization: Basic ${auth}" \
      "${realm}?service=${registry}&scope=repository:${repo}:pull" 2>/dev/null \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("token",""))' 2>/dev/null)" || true
  if [[ -z "$token" ]]; then
    warn "token exchange with ${registry} failed; falling back to the pinned ${label} version ${fallback:-unset}"
    printf '%s' "${fallback:?}"
    return 0
  fi

  tags="$(curl -fsSL -H "Authorization: Bearer ${token}" \
      "https://${registry}/v2/${repo}/tags/list" 2>/dev/null \
    | python3 -c 'import json,sys; print("\n".join(json.load(sys.stdin).get("tags") or []))' 2>/dev/null)" || true
  if [[ -z "$tags" ]]; then
    warn "tag enumeration on ${registry}/${repo} returned nothing; falling back to the pinned ${label} version ${fallback:-unset}"
    printf '%s' "${fallback:?}"
    return 0
  fi

  # Highest version-sorted tag on the track, excluding preview/experimental
  # lines: a conformance baseline must be measured on a released build.
  newest="$(printf '%s\n' "$tags" \
    | grep -E "^${track}(\.[0-9]+)*$" \
    | grep -vEi 'preview|beta|alpha|em|rc' \
    | sort -V | tail -n1)"
  [[ -n "$newest" ]] \
    || die "no released tag matching '${track}*' found in ${registry}/${repo} - check the ${label} version track in .env"
  printf '%s' "$newest"
}

# --- Axis 1: the unit under test ---------------------------------------------
# Floats on MODE=latest. The build under test is a measurement either way: the
# digest and the IRIS build string (%SYSTEM.Version) are both recorded below, so a
# floated round is every bit as re-identifiable as a pinned one - it just cannot be
# re-created by tag alone months later, which is what R_IRIS_SOURCE says.
if [[ "$MODE" == latest ]]; then
  R_IRIS_VERSION="$(resolve_latest_tag "$IRIS_IMAGE" "${IRIS_VERSION_TRACK:-}" \
                      "${IRIS_VERSION:-}" "IRIS")"
  # The gateway is resolved on the IRIS track, not on one of its own, because it
  # is version-matched to IRIS deliberately - see .env.example section 2b. Its
  # published tags follow the same major.minor scheme (2022.1 ... 2026.2), so the
  # same track expression selects the matching build.
  R_WEBGATEWAY_VERSION="$(resolve_latest_tag "$WEBGATEWAY_IMAGE" "${IRIS_VERSION_TRACK:-}" \
                            "${WEBGATEWAY_VERSION:-}" "Web Gateway")"
  R_IRIS_VERSION_SOURCE=resolved-latest
  log "SUT resolved from the registry: IRIS=${R_IRIS_VERSION}, webgateway=${R_WEBGATEWAY_VERSION} (track ${IRIS_VERSION_TRACK:-unset})"
else
  require_var IRIS_VERSION WEBGATEWAY_VERSION
  R_IRIS_VERSION="$IRIS_VERSION"
  R_WEBGATEWAY_VERSION="$WEBGATEWAY_VERSION"
  R_IRIS_VERSION_SOURCE=pinned
  log "SUT pinned in .env: IRIS=${R_IRIS_VERSION}, webgateway=${R_WEBGATEWAY_VERSION}"
fi

# --- Axis 2: the test tool ---------------------------------------------------
# Pinned unless TESTSUITE_VERSION_TRACK says otherwise, and MODE has no say in it.
# The reason is in this file's header and stated in full in INSTALL.md: the test
# suite version fixes the scenario inventory, the FHIR package versions its
# reference validator validates against, the ${data.*} placeholder contract this
# repository's test data satisfies, and which upstream defects need working around.
# All four are part of the result, and three of them are things a release can change
# without any visible failure until a scenario asserts against data that is no
# longer shaped the way the feature file expects.
R_TESTSUITE_LATEST=unknown
case "${TESTSUITE_VERSION_TRACK:-}" in
  latest)
    R_TESTSUITE_VERSION="$(resolve_latest_testsuite)"
    R_TESTSUITE_LATEST="$R_TESTSUITE_VERSION"
    R_TESTSUITE_VERSION_SOURCE=resolved-latest
    log "test suite resolved from GitHub releases: isik-testsuite=${R_TESTSUITE_VERSION}"
    warn "TESTSUITE_VERSION_TRACK=latest floats the test tool. The authored test-data corpus in testdata/ was verified against ${TESTSUITE_VERSION:-a pinned release}, not against ${R_TESTSUITE_VERSION}: re-run 'make check' and re-read docs/en/05-testdata.md before treating this round as a conformance result. See INSTALL.md."
    ;;
  ''|pinned)
    require_var TESTSUITE_VERSION
    R_TESTSUITE_VERSION="$TESTSUITE_VERSION"
    R_TESTSUITE_VERSION_SOURCE=pinned
    log "test suite pinned in .env: isik-testsuite=${R_TESTSUITE_VERSION} (the test definition is deliberately not floated)"
    # A stale pin should be visible, not silent - but discovering it must never cost
    # the round, so an unreachable GitHub is passed over without comment.
    if R_TESTSUITE_LATEST="$(resolve_latest_testsuite soft)"; then
      if [[ "$R_TESTSUITE_LATEST" != "$R_TESTSUITE_VERSION" ]]; then
        warn "a newer test suite release exists upstream: ${R_TESTSUITE_LATEST} (pinned here: ${R_TESTSUITE_VERSION}). This is a notice, not a problem: bumping it requires re-verifying the test-data corpus against the new release's feature files. INSTALL.md has the checklist."
      fi
    else
      R_TESTSUITE_LATEST=unknown
    fi
    ;;
  *)
    die "TESTSUITE_VERSION_TRACK must be empty (use the pinned TESTSUITE_VERSION) or 'latest' (got: '${TESTSUITE_VERSION_TRACK}')"
    ;;
esac

# -----------------------------------------------------------------------------
# Credentials
# -----------------------------------------------------------------------------
# Two accounts, one password each, generated per instance of this lab environment
# by gen_password() in lib.sh - see its comment for why the form is what it is.
# Both are kept in secrets/ so that a re-run, a manual curl, the rendered Tiger
# configuration and the credential sheet all agree on the same value, and both are
# documented in the round's reports on purpose.
#
#   sut.password        the FHIR API user the test suite authenticates as
#   iris-mgmt.password  the IRIS Management Portal, for the human tester. The
#                       official image's predefined accounts arrive with expired
#                       passwords, so without this the portal cannot be used at all
#                       - and `make manual` advertises it.
#
# Never empty: an unauthenticated endpoint would be a functional deviation from a
# production deployment, which is precisely what this environment must not be.
PW_FILE="${SECRETS_DIR}/sut.password"
MGMT_PW_FILE="${SECRETS_DIR}/iris-mgmt.password"

SUT_PASSWORD="$(resolve_password "${SUT_PASSWORD:-}" "$PW_FILE" "FHIR API password")"
IRIS_MGMT_PASSWORD="$(resolve_password "${IRIS_MGMT_PASSWORD:-}" "$MGMT_PW_FILE" \
                        "IRIS Management Portal password")"
IRIS_MGMT_USER="${IRIS_MGMT_USER:-_SYSTEM}"

# -----------------------------------------------------------------------------
# Web Gateway settings
# -----------------------------------------------------------------------------
# Defaulted here so that a .env predating the Web Gateway still produces a
# .env.resolved compose can interpolate. The values match .env.example section 2b;
# the append loop in the writer below warns for each one it has to add, so a stale
# .env is reported rather than silently compensated for.
#
# WEBGATEWAY_VERSION takes the resolved value, not the .env value, because in
# MODE=latest they differ - the same reason IRIS_VERSION is rewritten rather than
# copied.
WEBGATEWAY_VERSION="$R_WEBGATEWAY_VERSION"

# Derived from IRIS_IP rather than defaulted to a literal, because the subnet is the
# operator's choice (ISIK_SUBNET) and a hard-coded 172.31.0.30 would be outside it
# on any relocated deployment - which compose rejects with "invalid IPv4 address"
# and no hint about where the address came from. .30 is free by construction: .10
# and .20 are IRIS and the test suite.
WEBGATEWAY_IP="${WEBGATEWAY_IP:-${IRIS_IP%.*}.30}"
WEBGATEWAY_PORT_INTERNAL="${WEBGATEWAY_PORT_INTERNAL:-80}"
GATEWAY_IRIS_USER="${GATEWAY_IRIS_USER:-CSPSystem}"

# -----------------------------------------------------------------------------
# Pull + digest
# -----------------------------------------------------------------------------
# The digest, not the tag, is what makes the run auditable: a tag can be moved,
# a digest cannot.
#
# A pull failure is not automatically fatal: 00-preflight.sh already documents the
# pre-pulled route ("ignore this if you use a credential helper or a pre-pulled
# image"), and the licensed IRIS registry is not reachable from every lab network.
# If the image is already present locally it is used, and the substitution is
# recorded loudly - in the log here and in the provenance record below, since a
# side-loaded image is a provenance fact an inspector must be able to see.
#
# Prints two space-separated fields, "<hex-digest> <source>", because a command
# substitution runs in a subshell and a variable set inside the function would not
# survive it. The source is:
#   registry    pulled from the registry in this round; the digest is a registry
#               digest and the image can be re-pulled by it
#   side-loaded pull failed, a local copy was used; the digest is an image ID
pull_and_digest() {
  local ref="$1" digest source='registry'
  log "pulling ${ref}"
  if ! docker pull --quiet "$ref" >/dev/null 2>&1; then
    source='side-loaded'
    docker image inspect "$ref" >/dev/null 2>&1 \
      || die "cannot pull ${ref}, and no local copy exists - check the tag, and 'docker login ${ref%%/*}' if this is the licensed registry.
  To side-load instead: pull the image elsewhere, 'docker save' it, copy it over
  and 'docker load' it, so that '${ref}' resolves locally. See docs/en/02-setup.md."
    warn "could not pull ${ref}; using the local copy already present"
    warn "  the round is testing a side-loaded image - its provenance is the image ID below, not a registry digest"
  fi
  digest="$(docker image inspect "$ref" --format '{{if .RepoDigests}}{{index .RepoDigests 0}}{{end}}' 2>/dev/null || true)"
  # A locally built, imported or side-loaded image has no RepoDigests; record the
  # image ID so the field is never silently empty in the provenance record.
  if [[ -z "$digest" ]]; then
    digest="$(docker image inspect "$ref" --format '{{.Id}}')"
    warn "${ref} has no registry digest (locally built, imported or side-loaded); recording image ID instead"
  fi
  # Return the bare hex, with no algorithm prefix and no repository part: the
  # caller adds "sha256:". RepoDigests are "repo@sha256:hex" while .Id is
  # "sha256:hex", so stripping only up to '@' would leave the prefix in place on
  # the ID path and emit "sha256:sha256:hex" into the provenance record.
  digest="${digest##*@}"
  # The trailing newline is required, not cosmetic: `read` returns non-zero when it
  # reaches EOF without a delimiter, and lib.sh runs under `set -e`, so a bare
  # `printf '%s %s'` would abort the script at the call site with no diagnostic.
  printf '%s %s\n' "${digest#sha256:}" "$source"
}

IRIS_REF="${IRIS_IMAGE}:${R_IRIS_VERSION}"
TESTSUITE_REF="${TESTSUITE_IMAGE}:${R_TESTSUITE_VERSION}"
WEBGATEWAY_REF="${WEBGATEWAY_IMAGE}:${R_WEBGATEWAY_VERSION}"

read -r R_IRIS_DIGEST R_IRIS_SOURCE                   < <(pull_and_digest "$IRIS_REF")
read -r R_TESTSUITE_DIGEST R_TESTSUITE_SOURCE         < <(pull_and_digest "$TESTSUITE_REF")
# The gateway's digest is recorded with the same weight as the other two: it is part
# of the SUT, not test scaffolding, so "what exactly did you test?" is not fully
# answered without it.
read -r R_WEBGATEWAY_DIGEST R_WEBGATEWAY_SOURCE       < <(pull_and_digest "$WEBGATEWAY_REF")

# The exact IRIS build string, read out of the pulled image without starting it.
# This is the version an inspector will compare against the SUT documentation, and
# it is more specific than the tag: "2026.2.0.221.0" for tag "2026.2".
#
# Source is /usr/irissys/parameters.isc, key "product_info.version" - verified by
# inspection of irishealth:2026.2 and :2025.3. Two more obvious-looking sources are
# not usable: /usr/irissys/cbin/ is empty in these images, and `iris version`
# without a running instance prints only an internal number ("525"). A wrong path
# here is silent, because the fallback text below is itself plausible, so this
# reads the file and greps for the key rather than globbing for a file that may
# not exist.
R_IRIS_ZV="$(docker run --rm --entrypoint sh "$IRIS_REF" -c \
    'sed -n "s/^product_info\.version:[[:space:]]*//p" /usr/irissys/parameters.isc 2>/dev/null | head -n1' \
    2>/dev/null | tr -d '\r' || true)"
if [[ -n "$R_IRIS_ZV" ]]; then
  ok "IRIS build ${R_IRIS_ZV}"
else
  # Not fatal: the bootstrap records $ZV from the running instance into
  # iris-provenance.json, so the build is still documented, just not this early.
  warn "could not read product_info.version from ${IRIS_REF}; the build string will come from the running instance"
  R_IRIS_ZV="(read from the running instance at bootstrap)"
fi

ok "IRIS      ${IRIS_REF}  sha256:${R_IRIS_DIGEST:0:19}..."
ok "gateway   ${WEBGATEWAY_REF}  sha256:${R_WEBGATEWAY_DIGEST:0:19}..."
ok "test tool ${TESTSUITE_REF}  sha256:${R_TESTSUITE_DIGEST:0:19}..."

# -----------------------------------------------------------------------------
# Write .env.resolved
# -----------------------------------------------------------------------------
# Produced by copying .env line by line and replacing only the fields that were
# resolved. Copying rather than regenerating preserves the operator's comments
# and any setting this script does not know about, so .env.resolved stays a
# readable, complete statement of the configuration.
TMP="${ENV_RESOLVED_FILE}.tmp"

{
  cat <<EOF
# =============================================================================
# .env.resolved - GENERATED by scripts/10-resolve-versions.sh. Do not edit.
# =============================================================================
# Frozen configuration for this test round. Every target after 'make resolve'
# reads this file, not .env, so that editing .env mid-round cannot silently
# change what is being tested.
#
# This is the provenance record cited by docs/en/03-sut-iris.md,
# docs/en/04-testtool.md and the generated results analysis.
#
#   generated:       $(date -u +%Y-%m-%dT%H:%M:%SZ)
#   generated on:    $(uname -srm) / $(hostname)
#   source:          .env
#   deploy mode:     ${MODE}
#   SUT version:     ${R_IRIS_VERSION} (${R_IRIS_VERSION_SOURCE})
#   test suite:      ${R_TESTSUITE_VERSION} (${R_TESTSUITE_VERSION_SOURCE}, newest upstream: ${R_TESTSUITE_LATEST})
#   docker:          $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo unknown)
#   compose:         $(docker compose version --short 2>/dev/null || echo unknown)
# =============================================================================

EOF

  # Rewrite the resolved fields; pass everything else through untouched.
  while IFS= read -r line; do
    case "$line" in
      IRIS_VERSION=*)       printf 'IRIS_VERSION=%s\n' "$R_IRIS_VERSION" ;;
      TESTSUITE_VERSION=*)  printf 'TESTSUITE_VERSION=%s\n' "$R_TESTSUITE_VERSION" ;;
      WEBGATEWAY_VERSION=*) printf 'WEBGATEWAY_VERSION=%s\n' "$R_WEBGATEWAY_VERSION" ;;
      SUT_PASSWORD=*)       printf 'SUT_PASSWORD=%s\n' "$SUT_PASSWORD" ;;
      IRIS_MGMT_USER=*)     printf 'IRIS_MGMT_USER=%s\n' "$IRIS_MGMT_USER" ;;
      IRIS_MGMT_PASSWORD=*) printf 'IRIS_MGMT_PASSWORD=%s\n' "$IRIS_MGMT_PASSWORD" ;;
      MODE=*)              printf 'MODE=%s\n' "$MODE" ;;
      PACKAGE_SOURCE=*)    printf 'PACKAGE_SOURCE=%s\n' "${PACKAGE_SOURCE:-refv}" ;;
      *)                   printf '%s\n' "$line" ;;
    esac
  done <"$ENV_FILE"

  # A .env written before a key existed has no line for the rewriter to replace,
  # so anything still missing is appended rather than silently dropped: compose
  # interpolates ${IRIS_MGMT_PASSWORD:?...} and ${WEBGATEWAY_IP:?...} and would
  # otherwise fail with a message pointing at compose rather than at the stale .env.
  # The WEBGATEWAY_* keys are the ones most likely to be missing, since a .env
  # copied from an earlier version of this rig predates the Web Gateway entirely.
  for key in SUT_PASSWORD IRIS_MGMT_USER IRIS_MGMT_PASSWORD \
             WEBGATEWAY_IMAGE WEBGATEWAY_VERSION WEBGATEWAY_IP \
             WEBGATEWAY_PORT_INTERNAL GATEWAY_IRIS_USER; do
    grep -q "^${key}=" "$ENV_FILE" && continue
    printf '%s=%s\n' "$key" "${!key}"
    warn "${ENV_FILE#$REPO_ROOT/} defines no ${key}; appended it to .env.resolved (compare against .env.example)"
  done

  cat <<EOF

# -----------------------------------------------------------------------------
# Resolved provenance (appended by scripts/10-resolve-versions.sh)
# -----------------------------------------------------------------------------
# Image digests pin the exact bits that were tested. Reproduce this round with:
#   docker pull ${IRIS_IMAGE}@sha256:${R_IRIS_DIGEST}
#   docker pull ${WEBGATEWAY_IMAGE}@sha256:${R_WEBGATEWAY_DIGEST}
#   docker pull ${TESTSUITE_IMAGE}@sha256:${R_TESTSUITE_DIGEST}
#
# Three images, not two: the Web Gateway serves every FHIR request the suite issues,
# because the IRIS images contain no web server at all, so it is part of the system
# under test and its digest belongs in the provenance record.
#
# A side-loaded image (pulled elsewhere, 'docker save'/'docker load'ed here
# because the licensed registry was unreachable) has no registry digest, so the
# value below is that image's local ID instead. It still identifies the exact bits
# that were tested and is still comparable between hosts - 'docker load' preserves
# the ID - but it cannot be pulled by, and the corresponding IMAGE_SOURCE field
# says which of the two kinds it is.
RESOLVED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
RESOLVED_MODE=${MODE}
IRIS_IMAGE_REF=${IRIS_REF}
IRIS_IMAGE_DIGEST=sha256:${R_IRIS_DIGEST}
IRIS_IMAGE_SOURCE=${R_IRIS_SOURCE}
IRIS_BUILD_VERSION=${R_IRIS_ZV}
WEBGATEWAY_IMAGE_REF=${WEBGATEWAY_REF}
WEBGATEWAY_IMAGE_DIGEST=sha256:${R_WEBGATEWAY_DIGEST}
WEBGATEWAY_IMAGE_SOURCE=${R_WEBGATEWAY_SOURCE}
TESTSUITE_IMAGE_REF=${TESTSUITE_REF}
TESTSUITE_IMAGE_DIGEST=sha256:${R_TESTSUITE_DIGEST}
TESTSUITE_IMAGE_SOURCE=${R_TESTSUITE_SOURCE}

# How each of the two version axes got its value: 'pinned' means it was taken
# verbatim from .env, 'resolved-latest' means it was discovered upstream at the
# timestamp above. The distinction is what a reader needs in order to know whether
# re-running with the same .env would test the same thing - for a resolved axis it
# would not, and the digest above is then the only exact reference.
#
# TESTSUITE_LATEST_AVAILABLE is the newest published release seen at resolve time,
# recorded even when the pin was kept, so a report states how stale its test tool
# was rather than leaving that to be looked up later. 'unknown' means GitHub was
# not reachable, which is not an error - the round does not depend on it.
IRIS_VERSION_SOURCE=${R_IRIS_VERSION_SOURCE}
TESTSUITE_VERSION_SOURCE=${R_TESTSUITE_VERSION_SOURCE}
TESTSUITE_LATEST_AVAILABLE=${R_TESTSUITE_LATEST}
EOF
} >"$TMP"

# .env.resolved carries the API password, so it is never world-readable.
chmod 0600 "$TMP"
mv "$TMP" "$ENV_RESOLVED_FILE"

ok "wrote .env.resolved"
step "Versions resolved"
