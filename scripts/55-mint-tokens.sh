#!/usr/bin/env bash
# =============================================================================
# 55-mint-tokens.sh - obtain the ISiK Stufe 5 "Connect" access tokens
# =============================================================================
# The `Connect` module is the only ISiK module whose test data the suite cannot
# produce for itself. Its feature files send
#
#     | Authorization | Bearer ${connect.read-patient-scope-allowed} |
#
# and the official testdata/Stufe5/connect.yaml ships the six token keys with
# the value SET-YOUR-TOKEN-HERE - the suite's documented instruction to the
# operator is to put real tokens there. This script is that step, performed by
# the round rather than by hand, so that a Connect result is reproducible and
# the tokens' properties are recorded as evidence.
#
#   scripts/55-mint-tokens.sh            request six tokens and stage them
#   scripts/55-mint-tokens.sh --verify   check what is staged now, change nothing
#
# WHAT IT DOES. One OAuth 2.0 client-credentials request per scope against the
# round's own authorization server, over TLS, verified against the round's CA.
# Six clients exist because a token must carry exactly one scope: the module
# tests what each scope does and does not grant, so a token holding two of them
# would make a refusal scenario pass for the wrong reason. The clients, their
# scopes and the test-data keys they fill in are defined once, in
# connect_token_clients() in scripts/lib.sh, and are read from there here.
#
# Validation, the evidence record and the write into the staged test data are in
# scripts/lib/connect_tokens.py, which documents what is checked and why.
#
# TOKENS ARE CREDENTIALS. An access token carries the full authority of its scope
# until it expires. Accordingly: no token value is passed on a command line,
# printed, or logged; the responses are written to a private temporary directory
# that is removed on exit; the only lasting record of a token outside the staged
# test-data file is the SHA-256 in run/tiger/TOKENS.txt.
#
# ORDER MATTERS. scripts/30-render-config.sh re-stages run/testdata/ from the
# official contract and therefore restores the placeholders. Always render
# first, then mint - `make tokens` depends on the rendered configuration for
# exactly this reason, and the check in 60-run-tests.sh refuses to start a
# Connect run if the staged file carries a placeholder or an expired token.
# =============================================================================

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

load_env

MODE=mint
case "${1:-}" in
  '')        MODE=mint ;;
  --verify)  MODE=verify ;;
  *) die "usage: $(basename "$0") [--verify]" ;;
esac

if ! connect_enabled; then
  die "CONNECT_ENABLE is not true - this round has no authorization server, so there are no tokens to mint. See section 7 of .env.example."
fi

need_cmd curl
need_cmd python3

require_var AS_HOSTNAME AS_REALM AS_CLIENT_SECRET SUT_AUDIENCE

AS_PORT_HTTPS="${AS_PORT_HTTPS:-8443}"
AS_PORT_HTTPS_INTERNAL="${AS_PORT_HTTPS_INTERNAL:-8443}"

CA_FILE="${RUN_DIR}/tls/ca.crt"
CONNECT_YAML="${RUN_DIR}/testdata/Stufe5/connect.yaml"
TOKENS_RECORD="${RUN_DIR}/tiger/TOKENS.txt"
HELPER="${SCRIPTS_DIR}/lib/connect_tokens.py"

# Seconds of life a token must still have for a run to be allowed to start. A
# full Stufe 5 Connect run took about four minutes on the reference host; ten
# minutes leaves room for a slower host and for the startup of the suite
# container, and is still well inside the one-hour lifespan.
MIN_REMAINING="${CONNECT_TOKEN_MIN_REMAINING:-600}"

[[ -f "$CA_FILE" ]] || die "no CA certificate at ${CA_FILE#$REPO_ROOT/} - run scripts/22-make-tls.sh (or 'make up') first"
[[ -f "$HELPER" ]]  || die "missing ${HELPER#$REPO_ROOT/}"

# The audience must be the endpoint the suite actually addresses. IRIS accepts a
# token only for the audience its endpoint is configured for (ISiK.OAuth
# AssociateEndpoint), and the suite's route forwards to sut_base_url_tls(); if
# those two strings differ, every Connect scenario fails with a 401 that looks
# like a rejected signature. Checked rather than derived, because SUT_AUDIENCE is
# also what the realm stamps into the token and what the documentation quotes -
# one value, stated once in .env.
EXPECTED_AUDIENCE="$(sut_base_url_tls stufe5)"
if [[ "$SUT_AUDIENCE" != "$EXPECTED_AUDIENCE" ]]; then
  die "SUT_AUDIENCE is '${SUT_AUDIENCE}' but the test suite addresses '${EXPECTED_AUDIENCE}'. A token minted for the former would be refused at the latter; set SUT_AUDIENCE to the endpoint URL."
fi

# The authorization server as reachable from this host. The published port is
# used with --resolve rather than with 127.0.0.1 in the URL, so that the
# certificate is verified against the name it was issued for instead of the
# verification being switched off.
AS_ORIGIN="https://${AS_HOSTNAME}:${AS_PORT_HTTPS}"
AS_REALM_URL="${AS_ORIGIN}/realms/${AS_REALM}"
CURL_COMMON=( --silent --show-error --max-time 30
              --cacert "$CA_FILE"
              --resolve "${AS_HOSTNAME}:${AS_PORT_HTTPS}:127.0.0.1" )

# The issuer IRIS discovered, which is what the tokens must carry. IRIS reaches
# the authorization server inside the subnet, on its internal port, and
# Keycloak is pinned to that origin with KC_HOSTNAME (see compose/authserver.yml)
# so that the issuer does not change with the port a request arrives on.
EXPECTED_ISSUER="https://${AS_HOSTNAME}:${AS_PORT_HTTPS_INTERNAL}/realms/${AS_REALM}"

# Everything a response touches lives in here, and here only: the directory is
# 0700 and is removed however this script ends, including on a failed request.
WORK=""
cleanup() {
  if [[ -n "$WORK" && -d "$WORK" ]]; then
    rm -rf "$WORK"
  fi
}
trap cleanup EXIT

# -----------------------------------------------------------------------------
# 1. The authorization server, and what it says its issuer is
# -----------------------------------------------------------------------------
# Read from the server rather than assumed: a mismatch between the issuer in the
# token and the issuer IRIS discovered is rejected with a bare 401, which would
# otherwise be investigated as a defect in the system under test.
fetch_discovery_document() {
  local out="$1" code
  code="$(curl "${CURL_COMMON[@]}" -o "$out" -w '%{http_code}' \
            "${AS_REALM_URL}/.well-known/openid-configuration" 2>"${WORK}/curl.err")" || {
    die "cannot reach the authorization server at ${AS_REALM_URL} - $(tr -d '\n' <"${WORK}/curl.err"). Is the authserver container up ('make up' with CONNECT_ENABLE=true)?"
  }
  [[ "$code" == "200" ]] || die "the authorization server answered HTTP ${code} for its own discovery document at ${AS_REALM_URL}/.well-known/openid-configuration - is realm '${AS_REALM}' imported?"
}

json_field() {
  # json_field <file> <field> - one scalar, nothing else, via stdin-free argv
  # that never carries a credential (the files here hold public metadata only).
  python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get(sys.argv[2],""))' "$1" "$2"
}

WORK="$(mktemp -d "${TMPDIR:-/tmp}/isik-tokens.XXXXXXXX")"
chmod 0700 "$WORK"

step "Authorization server"

fetch_discovery_document "${WORK}/discovery.json"
ACTUAL_ISSUER="$(json_field "${WORK}/discovery.json" issuer)"
TOKEN_ENDPOINT="$(json_field "${WORK}/discovery.json" token_endpoint)"

if [[ "$ACTUAL_ISSUER" != "$EXPECTED_ISSUER" ]]; then
  die "the authorization server publishes issuer '${ACTUAL_ISSUER}', but IRIS discovered it as '${EXPECTED_ISSUER}' and will reject a token carrying anything else. Check KC_HOSTNAME in compose/authserver.yml against AS_HOSTNAME and AS_PORT_HTTPS_INTERNAL."
fi
ok "reachable over TLS, issuer ${ACTUAL_ISSUER}"
log "token endpoint (as published): ${TOKEN_ENDPOINT}"

# -----------------------------------------------------------------------------
# 2. Verify-only mode
# -----------------------------------------------------------------------------
if [[ "$MODE" == verify ]]; then
  step "Checking the staged Connect access tokens"
  connect_token_clients | python3 "$HELPER" verify \
    --yaml "$CONNECT_YAML" \
    --audience "$SUT_AUDIENCE" \
    --issuer "$EXPECTED_ISSUER" \
    --min-remaining "$MIN_REMAINING"
  ok "the staged Connect test data is usable for a run"
  exit 0
fi

# -----------------------------------------------------------------------------
# 3. Mint
# -----------------------------------------------------------------------------
step "Requesting the Connect access tokens"

mkdir -p "${WORK}/tokens"
chmod 0700 "${WORK}/tokens"

# The request body is written to a file and handed to curl as @file, so neither
# the client secret nor anything derived from it appears in this process's
# command line, where every other user of the host could read it.
BODY_FILE="${WORK}/body"
: >"$BODY_FILE"
chmod 0600 "$BODY_FILE"

# The secret reaches python through the environment rather than argv for the
# same reason.
SECRET_ENC="$(AS_CLIENT_SECRET="$AS_CLIENT_SECRET" python3 -c \
  'import os,urllib.parse;print(urllib.parse.quote(os.environ["AS_CLIENT_SECRET"],safe=""))')"

# The token endpoint addressed through our own origin. The published
# token_endpoint is logged above for the record, but it names the internal port,
# which this host does not necessarily reach.
TOKEN_URL="${AS_REALM_URL}/protocol/openid-connect/token"

minted=0
failures=()
while IFS='|' read -r key client scope; do
  [[ -n "$key" ]] || continue

  printf 'grant_type=client_credentials&client_id=%s&client_secret=%s' \
    "$client" "$SECRET_ENC" >"$BODY_FILE"

  code="$(curl "${CURL_COMMON[@]}" \
            -o "${WORK}/response.json" -w '%{http_code}' \
            -H 'Content-Type: application/x-www-form-urlencoded' \
            -H 'Accept: application/json' \
            --data "@${BODY_FILE}" \
            "$TOKEN_URL" 2>"${WORK}/curl.err")" || {
    failures+=("${key}: the token request failed - $(tr -d '\n' <"${WORK}/curl.err")")
    continue
  }

  if [[ "$code" != "200" ]]; then
    # An error response carries no token, so quoting the server's own
    # description of the problem is safe and is the only useful diagnostic.
    detail="$(json_field "${WORK}/response.json" error_description)"
    [[ -n "$detail" ]] || detail="$(json_field "${WORK}/response.json" error)"
    failures+=("${key}: client ${client} got HTTP ${code}${detail:+ - ${detail}}")
    continue
  fi

  # Extracted into a file, not a shell variable: a variable would be visible in
  # the process environment of everything this script starts afterwards.
  python3 - "${WORK}/response.json" "${WORK}/tokens/${key}" <<'PY'
import json, os, sys
with open(sys.argv[1], encoding="utf-8") as fh:
    body = json.load(fh)
token = body.get("access_token")
if not token:
    sys.exit("the token response contained no access_token")
fd = os.open(sys.argv[2], os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as fh:
    fh.write(token)
PY

  minted=$((minted + 1))
  log "${scope} -> ${key}"
done < <(connect_token_clients)

if (( ${#failures[@]} > 0 )); then
  printf '%s\n' "${failures[@]}" >&2
  die "${#failures[@]} of the Connect access tokens could not be obtained. The realm the authorization server imported must define one client per scope; check run/as/realm-isik.json against connect_token_clients() in scripts/lib.sh, and AS_CLIENT_SECRET."
fi

ok "obtained ${minted} access token(s)"

# -----------------------------------------------------------------------------
# 4. Validate and stage
# -----------------------------------------------------------------------------
step "Staging the Connect access tokens"

# --parity and --upstream: staging the tokens makes the contract file differ from
# the released one, and the parity record written while rendering says IDENTICAL
# because at that point it was. The row is re-stated as PARAMETERISED and the
# difference is proved to be confined to the six token keys - see update_parity()
# in the helper. The upstream copy is the one 20-fetch-packages.sh fetched from
# the release tag; when it is absent the row is still corrected, with the record
# saying that the key-by-key comparison could not be made.
connect_token_clients | python3 "$HELPER" install \
  --token-dir "${WORK}/tokens" \
  --yaml "$CONNECT_YAML" \
  --record "$TOKENS_RECORD" \
  --parity "${RUN_DIR}/tiger/TESTDATA-PARITY.txt" \
  --upstream "${VENDOR_DIR}/upstream/testdata/Stufe5/connect.yaml" \
  --audience "$SUT_AUDIENCE" \
  --issuer "$EXPECTED_ISSUER" \
  --min-remaining "$MIN_REMAINING"

ok "staged into ${CONNECT_YAML#$REPO_ROOT/}, recorded in ${TOKENS_RECORD#$REPO_ROOT/}"
step "Connect access tokens ready"
log "They expire in ${AS_TOKEN_LIFESPAN:-3600}s. Start the run now: make test-connect"
