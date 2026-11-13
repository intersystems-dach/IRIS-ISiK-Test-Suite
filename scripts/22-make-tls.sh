#!/usr/bin/env bash
# =============================================================================
# 22-make-tls.sh - issue the test environment's TLS material
# =============================================================================
# Produces, under run/tls/:
#
#   ca.crt  ca.key      a local certificate authority, this round's trust anchor
#   sut.crt sut.key     server certificate for the SUT's HTTPS listener
#   as.crt  as.key      server certificate for the authorization server
#   TLS.txt             what was issued, for whom, with which fingerprints
#
# WHY TLS IS NEEDED AT ALL. The ISiK Stufe 5 `Connect` module presents OAuth 2.0
# bearer tokens. The FHIR server refuses to even look at a bearer token on a
# connection it does not consider secure: HS.FHIRServer.RestHandler's
# PrelimTokenCheck requires HS.Local.FHIRServer.Util.BaseURL.IsRequestSecure(),
# which is derived from %request.Secure, i.e. from real TLS terminating at the
# Web Gateway. That is correct product behaviour - a bearer token is a bearer
# credential - and it is not configurable away. So the round supplies genuine
# TLS rather than asserting a scheme the connection does not have: no
# X-Forwarded-Proto is set anywhere in this rig, and the product's own check is
# left to do its job.
#
# WHY A LOCAL CA. The two certificates are issued by one CA so that the SUT, the
# Web Gateway, the authorization server and the test suite can all be given a
# single trust anchor. Nothing here is published, nothing is long-lived, and the
# fingerprints are recorded in TLS.txt so a report can state exactly which
# certificates a round used. A publicly trusted certificate would require a
# publicly resolvable name, which the isolated test subnet deliberately does not
# have.
#
# Run only when CONNECT_ENABLE=true; a no-op otherwise, so that a round without
# the authorization server is unaffected by this script's existence.
# =============================================================================

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

load_env

if ! connect_enabled; then
  log "CONNECT_ENABLE is not true - no TLS material needed, skipping"
  exit 0
fi

step "Issuing the test environment's TLS material"

require_var AS_HOSTNAME AS_IP WEBGATEWAY_IP SUT_TLS_HOST
need_cmd openssl

OUT_TLS="${REPO_ROOT}/run/tls"

# Subject fields. The organisation is ours and the CN says what the certificate
# is for; neither is load-bearing (verification is by SAN), but a certificate
# whose subject reads like a production certificate would be misleading evidence.
TLS_SUBJECT_BASE="/C=DE/O=InterSystems DACH/OU=ISiK conformance test environment"

# Validity. Deliberately short for the leaves: this material exists for the
# duration of a test round, and a certificate that outlives the round it was
# issued for invites reuse in a context nobody checked. The CA outlives them so
# that a re-issue does not change the trust anchor mid-round.
CA_DAYS=3650
LEAF_DAYS=90

# -----------------------------------------------------------------------------
# 1. Reuse what is still valid
# -----------------------------------------------------------------------------
# A fresh CA invalidates every token already minted against the old one and
# forces a gateway restart, so existing material is kept when it is still good.
# "Still good" is checked, not assumed: the files exist, the leaves verify
# against the CA, and nothing expires within the next 7 days.
reuse=false
if [[ -s "${OUT_TLS}/ca.crt" && -s "${OUT_TLS}/ca.key" \
   && -s "${OUT_TLS}/sut.crt" && -s "${OUT_TLS}/sut.key" \
   && -s "${OUT_TLS}/as.crt"  && -s "${OUT_TLS}/as.key" ]]; then
  if openssl verify -CAfile "${OUT_TLS}/ca.crt" "${OUT_TLS}/sut.crt" "${OUT_TLS}/as.crt" >/dev/null 2>&1 \
     && openssl x509 -in "${OUT_TLS}/sut.crt" -checkend 604800 -noout >/dev/null 2>&1 \
     && openssl x509 -in "${OUT_TLS}/as.crt"  -checkend 604800 -noout >/dev/null 2>&1; then
    reuse=true
  else
    warn "existing TLS material in run/tls is incomplete, unverifiable or about to expire - re-issuing"
  fi
fi

if [[ "$reuse" == true ]]; then
  ok "reusing the existing TLS material in run/tls (verified against run/tls/ca.crt)"
else
  mkdir -p "$OUT_TLS"

  # Private keys are written under this umask, so they are 0600 from the moment
  # they exist rather than being chmod'ed afterwards.
  umask 077

  # --- The CA ---
  openssl req -x509 -newkey rsa:2048 -nodes \
      -keyout "${OUT_TLS}/ca.key" -out "${OUT_TLS}/ca.crt" -days "$CA_DAYS" \
      -subj "${TLS_SUBJECT_BASE}/CN=ISiK test environment local CA" \
      >/dev/null 2>&1 \
    || die "openssl could not create the local CA - see the output above"

  # --- One server certificate ---
  # $1 = file stem, $2 = common name, $3 = SAN list in openssl syntax
  issue() {
    local stem="$1" cn="$2" san="$3"

    openssl req -newkey rsa:2048 -nodes \
        -keyout "${OUT_TLS}/${stem}.key" -out "${OUT_TLS}/${stem}.csr" \
        -subj "${TLS_SUBJECT_BASE}/CN=${cn}" >/dev/null 2>&1 \
      || die "openssl could not create a key/CSR for ${stem}"

    # Written as a file rather than passed with -addext: -addext is ignored by
    # `openssl x509 -req` on the versions this rig runs on, and the silent
    # consequence is a certificate with no subjectAltName at all, which every
    # modern client rejects with a name-mismatch error that points nowhere.
    printf 'subjectAltName=%s\nbasicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n' \
      "$san" >"${OUT_TLS}/${stem}.ext"

    openssl x509 -req -in "${OUT_TLS}/${stem}.csr" \
        -CA "${OUT_TLS}/ca.crt" -CAkey "${OUT_TLS}/ca.key" -CAcreateserial \
        -out "${OUT_TLS}/${stem}.crt" -days "$LEAF_DAYS" \
        -extfile "${OUT_TLS}/${stem}.ext" >/dev/null 2>&1 \
      || die "openssl could not sign the certificate for ${stem}"

    rm -f "${OUT_TLS}/${stem}.csr" "${OUT_TLS}/${stem}.ext"
  }

  # The SUT, as every client in the round addresses it: the name the test suite
  # and the token audience use, the compose service name, the gateway's static
  # IP, and the loopback for probes run from the host through the published port.
  issue sut "$SUT_TLS_HOST" \
    "DNS:${SUT_TLS_HOST},DNS:webgateway,DNS:localhost,IP:${WEBGATEWAY_IP},IP:127.0.0.1"

  # The authorization server, as IRIS and as `make tokens` address it.
  issue as "$AS_HOSTNAME" \
    "DNS:${AS_HOSTNAME},DNS:localhost,IP:${AS_IP},IP:127.0.0.1"

  # Certificates are public; only the keys are not.
  chmod 0644 "${OUT_TLS}/ca.crt" "${OUT_TLS}/sut.crt" "${OUT_TLS}/as.crt"
  chmod 0600 "${OUT_TLS}/ca.key" "${OUT_TLS}/sut.key" "${OUT_TLS}/as.key"

  openssl verify -CAfile "${OUT_TLS}/ca.crt" "${OUT_TLS}/sut.crt" "${OUT_TLS}/as.crt" >/dev/null 2>&1 \
    || die "the certificates just issued do not verify against the CA just issued - see run/tls"

  ok "issued a local CA and two server certificates valid for ${LEAF_DAYS} days"
fi

# -----------------------------------------------------------------------------
# 2. The record
# -----------------------------------------------------------------------------
# What a reader of a Connect round's evidence needs in order to know which
# certificates were in play, without being given the keys.
{
  printf '# TLS material of this test environment\n'
  printf '# Written by scripts/22-make-tls.sh at %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '#\n'
  printf '# Private keys live beside this file and are 0600. They are NOT reproduced\n'
  printf '# here and must not appear in any report; the fingerprints below identify\n'
  printf '# the certificates unambiguously without them.\n\n'
  for stem in ca sut as; do
    printf '=== %s.crt ===\n' "$stem"
    openssl x509 -in "${OUT_TLS}/${stem}.crt" -noout \
      -subject -issuer -serial -dates -fingerprint -sha256 2>/dev/null | sed 's/^/  /'
    openssl x509 -in "${OUT_TLS}/${stem}.crt" -noout -ext subjectAltName 2>/dev/null \
      | sed '1d;s/^ */  SAN: /'
    printf '\n'
  done
} >"${OUT_TLS}/TLS.txt"
chmod 0644 "${OUT_TLS}/TLS.txt"

ok "recorded the certificate inventory in run/tls/TLS.txt"
step "TLS material ready"
