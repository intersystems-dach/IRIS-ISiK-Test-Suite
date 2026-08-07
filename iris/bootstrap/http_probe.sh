#!/usr/bin/env bash
# =============================================================================
# http_probe.sh - one authenticated HTTP status probe, sourced inside the SUT
# =============================================================================
# Sourced by bootstrap.sh and healthcheck.sh. Both need the same thing: "what
# status code does this URL return for the API user?", from inside the IRIS
# container, where the available tooling is not what a normal host offers.
#
# The reason this exists as its own file: the official irishealth image ships
# wget but NOT curl (verified against 2026.2 by inspecting the image). Both
# callers were originally written against curl, which made the bootstrap report
# HTTP 000 - indistinguishable from "the endpoint does not answer" - when the real
# cause was `curl: command not found`. Getting that wrong in two places
# independently is how a rig ends up with a healthcheck that disagrees with its own
# bootstrap, so the probe is defined once.
#
# Defines: http_probe <url> -> prints a three-digit status code, or 000.
# Exits non-zero at source time if neither client exists, because a probe that
# cannot probe must not silently report 000 forever.
#
# Two properties both branches must share, each learned from this rig answering 404
# to a probe while the endpoint was in fact serving perfectly:
#
#   1. Issue a GET, not a HEAD. IRIS's FHIR RestHandler answers HEAD /metadata with
#      404 on a URL that returns 200 for GET, so wget's --spider (which sends HEAD)
#      reports "nothing serving this path" about a healthy endpoint.
#
#   2. Send the credential preemptively. This is the one that actually bit: the
#      endpoint answers an *unauthenticated* request with **404, not 401**. curl -u
#      sends Basic credentials on the first request and gets its 200; wget by default
#      waits to be challenged, and since no challenge ever arrives it only ever
#      issues the anonymous request and reports that 404 - hence
#      --auth-no-challenge.
#
# Both were latent in a file whose whole purpose is that the bootstrap and the
# healthcheck cannot disagree about the same server: identical URL, identical
# credential, two clients, two verdicts. Any future branch added here must be
# checked against a *known-good* endpoint, not only against a broken one.
#
# Note the second point when reading any diagnosis built on status codes: a 404 from
# these endpoints means "not found *or* not authenticated", so a credential fault
# does not present as 401. The callers' 404 messages say so.
# =============================================================================

if command -v curl >/dev/null 2>&1; then
  # Preferred: reports the code directly, with no output parsing.
  http_probe() {
    curl -s -o /dev/null -w '%{http_code}' \
      --max-time "${HTTP_PROBE_TIMEOUT:-20}" \
      -u "${ISIK_SUT_USER}:${ISIK_SUT_PASSWORD}" \
      -H 'Accept: application/fhir+json' \
      "$1" 2>/dev/null || echo 000
  }
elif command -v wget >/dev/null 2>&1; then
  http_probe() {
    local out
    # Three flags here are load-bearing; see the header for how each was learned.
    #
    # --auth-no-challenge sends the Basic header on the *first* request instead of
    # waiting for a 401. Without it this probe reports 404 against an endpoint that
    # answers 200 to `curl -u`, because the endpoint's reply to an unauthenticated
    # request is 404 and wget takes that at face value - it never retries, so the
    # credentials above are simply never sent.
    #
    # -O /dev/null, not --spider: --spider sends HEAD, which this server answers with
    # 404 on a URL that serves 200 for a GET. Discarding the body keeps the ~900 KB
    # CapabilityStatement off the disk while still requesting it.
    #
    # -S echoes the response headers to stderr, which is where the status line appears
    # even on success.
    out="$(wget -O /dev/null -S \
             -T "${HTTP_PROBE_TIMEOUT:-20}" -t 1 \
             --auth-no-challenge \
             --user="${ISIK_SUT_USER}" --password="${ISIK_SUT_PASSWORD}" \
             --header='Accept: application/fhir+json' \
             "$1" 2>&1 || true)"

    # Matched off the "  HTTP/1.1 200 OK" line, NOT off the "HTTP request sent,
    # awaiting response... " line above it: wget ends that line after the ellipsis
    # and prints the status on the next one, so a single-line pattern matches
    # nothing (verified against GNU Wget 1.21.4 - 200, 404 and 401 all parse).
    #
    # Last status line wins. With --auth-no-challenge there is normally only one
    # response, but a redirect - or a challenge from some other component in the
    # chain, the gateway included - logs more than one, and it is the final status
    # that describes what the caller got.
    printf '%s\n' "$out" \
      | sed -n 's|^[[:space:]]*HTTP/[0-9.]*[[:space:]]\{1,\}\([0-9][0-9][0-9]\).*|\1|p' \
      | tail -n1 \
      | grep -E '^[0-9]{3}$' || echo 000
  }
else
  echo "neither curl nor wget is available inside the container - cannot probe the endpoints" >&2
  exit 1
fi
