#!/bin/bash
# =============================================================================
# bootstrap.sh - runs INSIDE the IRIS container, after IRIS has started
# =============================================================================
# Invoked by the official image's iris-main entrypoint via `-a`, which runs this
# once IRIS is up. The FHIR installation must run against a live instance, so
# this cannot be an image build step - and doing it at container start means one
# unmodified official image serves all three deploy modes.
#
# Responsibilities:
#   1. make the ObjectScript source and the package plan reachable
#   2. drive ISiK.Setup.Run() in a non-interactive IRIS session
#   3. leave a readable log and a machine-readable state marker on the host
#
# The healthcheck (healthcheck.sh) reads the marker this script writes, so the
# container only reports healthy once the endpoints genuinely exist.
#
# Everything is configured through the ISIK_* environment variables set in
# docker-compose.yml; nothing is hardcoded here.
# =============================================================================

set -uo pipefail   # deliberately NOT -e: see the trap discussion below

STATE_DIR="${ISIK_STATE_DIR:-/opt/isik/state}"
LOG="${STATE_DIR}/bootstrap.log"
STATUS_FILE="${STATE_DIR}/bootstrap.status"
SRC_DIR="/opt/isik/src"

mkdir -p "$STATE_DIR"

# Everything from here on is teed to the log so that the whole installation is
# inspectable from the host (`make logs`) and archivable as evidence, while still
# appearing in `docker logs`.
exec > >(tee -a "$LOG") 2>&1

say() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

# The status file is the healthcheck's contract and the host's only view of
# progress. Written at every transition so a stalled bootstrap is diagnosable.
set_status() { printf '%s\n' "$1" >"$STATUS_FILE"; }

# `-e` is off on purpose: a failure must still record a terminal status, or the
# healthcheck would poll a stale "running" marker until start_period expires and
# report an unhelpful timeout instead of the real error.
fail() {
    say "FAILED: $*"
    set_status "failed: $*"
    exit 1
}

say "=========================================================================="
say "ISiK test environment - IRIS bootstrap"
say "=========================================================================="
set_status "running: starting"

# --- Sanity: configuration ---------------------------------------------------
for var in ISIK_NS_STUFE3 ISIK_NS_STUFE5 ISIK_FHIR_BASE_STUFE3 ISIK_FHIR_BASE_STUFE5 \
           ISIK_SUT_USER ISIK_SUT_PASSWORD ISIK_MGMT_USER ISIK_MGMT_PASSWORD \
           ISIK_PACKAGE_ROOT ISIK_PACKAGE_PLAN; do
    [[ -n "${!var:-}" ]] || fail "environment variable ${var} is not set"
done

# Configured, not necessarily installed: which of the two ISiK.Setup creates comes
# from the package plan's `endpoints` (see its PlanLevels()), and a 3.x suite has no
# ISiK Level 3. The banner therefore says what it is - the configuration it was
# handed - and the install log below states what was actually created.
say "configured:  ${ISIK_NS_STUFE3} ${ISIK_FHIR_BASE_STUFE3} (Stufe 3), ${ISIK_NS_STUFE5} ${ISIK_FHIR_BASE_STUFE5} (Stufe 5)"
say "installed:   whichever of those the package plan describes - see below"
say "api user:    ${ISIK_SUT_USER}"
say "mgmt user:   ${ISIK_MGMT_USER} (Management Portal; password set to never expire)"
say "packages:    ${ISIK_PACKAGE_ROOT}"

# --- Sanity: inputs ----------------------------------------------------------
[[ -f "$ISIK_PACKAGE_PLAN" ]] \
    || fail "package plan not found at ${ISIK_PACKAGE_PLAN} - run 'make packages' on the host"

[[ -d "$SRC_DIR/ISiK" ]] \
    || fail "ObjectScript source not mounted at ${SRC_DIR}/ISiK"

pkg_count="$(find "$ISIK_PACKAGE_ROOT" -maxdepth 2 -name package.json | wc -l | tr -d ' ')"
(( pkg_count > 0 )) \
    || fail "no unpacked FHIR packages under ${ISIK_PACKAGE_ROOT} - run 'make packages' on the host"
say "found ${pkg_count} unpacked FHIR package directories"

# --- Sanity: IRIS accepted the licence ---------------------------------------
# IRIS does not refuse to start without a usable licence. It logs
# "LMF Error: No valid license key", falls back to a zero-capacity diagnostic
# licence, and lets the whole installation below succeed - namespaces, packages,
# endpoints, security, all of it - after which every single FHIR request fails with
# "ERROR #5915: Unable to allocate a license", recorded only in the audit log. At
# the HTTP layer that is indistinguishable from an authentication or routing fault,
# which is why it is caught here, at its cause, before the ~30 minute install.
#
# The verdict is IRIS's own, taken from what the licence manager (LMF) wrote to
# messages.log at startup. Nothing about the key file is inspected or interpreted:
# no unit counts, no expiry arithmetic, no product-name matching. A licence is
# valid exactly when IRIS says it is - the alternative rejects working keys, as an
# earlier version of this check did with a Core Capacity key, whose
# KeyLicenseUnits() is legitimately 0 because its capacity is expressed in cores.
say "checking that IRIS accepted the licence"

MESSAGES_LOG=/usr/irissys/mgr/messages.log

# Only this boot's portion of the log is evidence about this boot's licence.
# messages.log is append-only and the official image ships one already populated:
# irishealth:2026.2 arrives carrying a complete build-time startup/shutdown cycle
# from 07/30/26, including "LMF Error: No valid license key" - the build ran
# unlicensed, quite legitimately. Grepping the whole file therefore reports that
# build's licence state as this deployment's, which failed a perfectly good Core
# Capacity key here with the very message this check exists to surface.
#
# IRIS writes a "Startup of InterSystems IRIS [...]" banner at every start, so the
# text after the last one is the current instance's log and nothing else. If no
# banner is present the log does not cover this boot at all; that yields no
# activation line either, and the check below reports the licence state as unknown,
# which is the correct verdict rather than a guess.
this_boot() {
    awk '/Startup of InterSystems IRIS/ { n = NR }
         { line[NR] = $0 }
         END { if (n) for (i = n; i <= NR; i++) print line[i] }' "$MESSAGES_LOG" 2>/dev/null || true
}

lmf_error="$(this_boot | grep -a 'LMF Error' | tail -3 || true)"
if [[ -n "$lmf_error" ]]; then
    fail "IRIS rejected the licence key. Its licence manager reported:

${lmf_error}

       IRIS is running on the zero-capacity diagnostic licence, on which every FHIR
       request fails with 'ERROR #5915: Unable to allocate a license'.
       Check that secrets/iris.key is a production IRIS for Health key valid for
       this version ($(iris --version 2>/dev/null || echo 'see messages.log')) and
       platform, and that it has not expired. If the key is known good, confirm the
       container can read it: it is bind-mounted at /usr/irissys/mgr/iris.key and
       IRIS runs as uid $(id -u)."
fi

# A licence that loaded says so explicitly, so require the positive statement too
# rather than inferring success from the absence of an error.
lmf_ok="$(this_boot | grep -a 'LMF Info' \
    | grep -aiE 'Activating|Licensed for|License Key Order Number' | tail -3 || true)"
if [[ -z "$lmf_ok" ]]; then
    fail "IRIS logged no licence activation for this boot - neither 'LMF Error' nor an
       'LMF Info: Activating' line appears after the last 'Startup of InterSystems
       IRIS' banner in ${MESSAGES_LOG}.
       The licence state is unknown, and continuing risks a 30 minute install that
       ends in 'ERROR #5915: Unable to allocate a license' on every request.
       Note that earlier LMF lines in that file may well be the image's own
       build-time log, which is why only the current boot's portion is read.
       Check ${MESSAGES_LOG} and that /usr/irissys/mgr/iris.key is readable by
       uid $(id -u)."
fi
say "  licence accepted by IRIS:"
while IFS= read -r line; do
    [[ -n "$line" ]] && say "    ${line#*] }"
done <<<"$lmf_ok"

# --- Idempotency -------------------------------------------------------------
# The container may be restarted without a rebuild (docker restart, host reboot).
# The ObjectScript side is itself idempotent, but re-running a ~30 minute package
# import for nothing is worth avoiding, so a completed marker short-circuits.
#
# What the marker has to establish, though, is not "an install happened here
# once". It is "the installation in this instance is the one this round asks
# for". Two things break that, and both were measured on this rig:
#
#   a changed plan     The marker lives in the host-mounted state directory,
#                      while the package plan is rewritten by `make packages`.
#                      Pinning the suite to another version and starting again
#                      therefore reuses the previous round's packages. That is
#                      how a round pinned to suite 3.0.9 came to measure an
#                      ISIK5 endpoint carrying de.gematik.isik 5.1.2 from the
#                      2.7.0 plan: the profiles it validated against and the
#                      search parameters it declared were the previous round's,
#                      so the round measured the rig and not the product.
#
#   a wiped instance   The instance's databases live in the container's writable
#                      layer - there is no named volume for them - so anything
#                      that recreates the container destroys every namespace and
#                      endpoint, while the marker, being on the host, survives
#                      and reports the installation as present. The round then
#                      fails at the first request against an endpoint that is
#                      no longer there, for a reason nothing on the host states.
#
# So the marker records the fingerprint of the plan it installed, and a second
# copy of it is written inside the container's own filesystem. Reuse requires
# both to agree with this start's plan: the plan must be the same one, and the
# installation must still physically be there. Anything else reinstalls - which
# costs half an hour, and never a wrong measurement. A marker from before this
# became the rule carries no fingerprint and so reinstalls once.
DONE_MARKER="${STATE_DIR}/bootstrap.done"
# Not under $STATE_DIR: the point of this one is that it shares the lifetime of
# the databases the install created, which the writable layer has and the bind
# mount does not. mgr/ is where those databases are.
INSTANCE_MARKER=/usr/irissys/mgr/isik-bootstrap.done

plan_fp="$(python3 /opt/isik/bootstrap/plan_fingerprint.py "$ISIK_PACKAGE_PLAN" 2>&1)" \
    || { say "the package plan's fingerprint could not be computed: ${plan_fp}"
         say "this start will install rather than reuse"
         plan_fp=""; }
[[ -n "$plan_fp" ]] && say "package plan fingerprint: ${plan_fp}"

if [[ -n "$plan_fp" && -f "$DONE_MARKER" && -f "$INSTANCE_MARKER" ]] \
   && [[ "$(head -n 1 "$DONE_MARKER" 2>/dev/null)" == "$plan_fp" ]] \
   && [[ "$(head -n 1 "$INSTANCE_MARKER" 2>/dev/null)" == "$plan_fp" ]]; then
    say "this instance already holds the installation this plan describes - reusing it"
    say "delete ${DONE_MARKER} (or run 'make clean') to force a reinstall"
    set_status "done: reused previous installation"
    exit 0
fi

if [[ -f "$DONE_MARKER" ]]; then
    # Say which of the two it was, because they call for different things from
    # the operator: a changed plan is a deliberate act, a wiped instance is not.
    if [[ ! -f "$INSTANCE_MARKER" ]]; then
        say "a completion marker is present on the host but this instance holds no"
        say "installation of its own - the container was recreated and its databases"
        say "went with it. Installing from scratch."
    else
        say "the completion marker describes a different package plan"
        say "  installed: $(head -n 1 "$DONE_MARKER" 2>/dev/null || true)"
        say "  this plan: ${plan_fp}"
        say "Reusing it would measure the previously installed packages. Installing"
        say "from scratch."
    fi
    # Neither marker may survive a failed reinstall: a later start must not read
    # a half-removed installation as complete.
    rm -f "$DONE_MARKER" "$INSTANCE_MARKER"
fi

# --- Drive IRIS --------------------------------------------------------------
# `iris session iris -U <namespace> < script` is the documented non-interactive
# entry point. The setup class is compiled from source and then run; compiling at
# runtime (rather than shipping a pre-built .xml export) keeps the ObjectScript
# readable in the repository and reviewable in a diff, which matters for a
# deliverable an auditor may read.
#
# HSCUSTOM, and neither %SYS nor HSLIB. Two independent requirements have to hold
# at once here, and each of the two obvious namespaces satisfies only one of them.
#
#   1. The HealthShare library classes the install depends on -
#      HS.Util.Installer.Foundation and HS.FHIRServer.Installer - must be visible.
#      They are mapped into HSLIB and HSCUSTOM but NOT into %SYS: verified by
#      querying %Dictionary.CompiledClass in each namespace on irishealth 2026.2
#      (HSLIB has 7944 HS.* classes, %SYS has none). Running from %SYS raised
#      <CLASS DOES NOT EXIST> on the first Foundation call, after the security
#      phase had already completed.
#
#   2. ISiK.Setup must be compilable, which needs a writable routine database.
#      HSLIB is mounted read-only, so loading there fails with "ERROR #5883: Item
#      'ISiK.Setup' is mapped from a database that you do not have write permission
#      on" - and note the failure mode: LoadDir reports errors but $system.OBJ
#      still returns before the class exists, so the diagnostic is the load error,
#      not a later <CLASS DOES NOT EXIST>.
#
# HSCUSTOM is the namespace the product provides for exactly this - customer code
# alongside the HS library mappings - and it satisfies both: verified by compiling
# and running a throwaway class there. Setup.cls switches to %SYS itself
# (EnsureSecurity, SecureEndpoint) and to each ISiK namespace in turn for the FHIR
# work, all under `new $namespace`; a class compiled in HSCUSTOM stays callable
# across those switches, which was verified too rather than assumed.
say "compiling and running ISiK.Setup"
set_status "running: installing FHIR endpoints (a few minutes; measured 4m40s cold)"

# The heredoc is passed on stdin, and `iris session` reads it the way a terminal
# would: one line, one command. A brace-delimited block spanning several lines is
# therefore NOT valid here even though it is valid inside a .cls - it raises
# <SYNTAX> on the opening line, and, worse, execution simply continues with the
# next line, so the body of a failed `if` runs unconditionally. That produced a
# bootstrap that terminated itself with an error while reporting errors=0.
#
# Everything below is consequently a single-line command. All real logic lives in
# iris/src/ISiK/Setup.cls, where multi-line blocks are fine.
#
# An `if { }` block IS accepted as long as it opens and closes on one line, which is
# what the load check below uses. Note the argument is written `$get(errors,0)'=0`
# with no spaces: a space before the `'=` operator raised <SYNTAX> here, whereas the
# same expression is accepted inside a .cls. LoadDir's byref `errors` is a count and
# is undefined when nothing failed, hence $get with a default.
#
# Run() reads its configuration from the ISIK_* environment variables and terminates
# the process itself on failure, which is what makes the shell-level check below
# meaningful.
iris session iris -U HSCUSTOM <<'OBJECTSCRIPT'
do $system.OBJ.LoadDir("/opt/isik/src", "ck", .errors, 1)
write !, "load errors: ", $get(errors,0), !
if $get(errors,0)'=0 { do $system.Process.Terminate($job, 1) }
do ##class(ISiK.Setup).Run()
halt
OBJECTSCRIPT
rc=$?

if (( rc != 0 )); then
    fail "ISiK.Setup.Run() exited ${rc} - see the log above; the FHIR endpoints are NOT installed"
fi

# --- Verify what was installed ----------------------------------------------
# Trust nothing: the setup class reports success, and this independently confirms
# that both endpoints exist, are configured as intended and are reachable. A
# bootstrap that claims success while an endpoint 404s would send the whole test
# round chasing phantom gaps.
#
# The check is made from INSIDE IRIS rather than over HTTP, because there is no
# HTTP to be had here: the official images ship no web server, and the container
# that provides one - the Web Gateway - sits outside this container and cannot
# come up until IRIS is listening. Probing http://localhost:52773 from here is
# what the earlier version of this script did, and it reported HTTP 000 on a
# perfectly good installation.
#
# What is verified instead is the state the gateway will serve: the web
# application exists, is enabled, dispatches to the FHIR REST handler, is bound to
# the right namespace, and its FHIR service answers a CapabilityStatement request
# through the same interaction layer an HTTP request would reach. The end-to-end
# HTTP path is then confirmed from the host by healthcheck.sh through the gateway,
# which is where that check belongs.
say "verifying both endpoints, from inside IRIS"

verify_out="$(iris session iris -U HSCUSTOM <<'OBJECTSCRIPT' 2>&1
do ##class(ISiK.Setup).VerifyEndpoints()
halt
OBJECTSCRIPT
)"
verify_rc=$?

printf '%s\n' "$verify_out" | while IFS= read -r line; do
    [[ -n "$line" ]] && say "  ${line}"
done

if (( verify_rc != 0 )) || ! printf '%s' "$verify_out" | grep -qa 'VERIFY OK'; then
    fail "endpoint verification failed - see the lines above. The FHIR endpoints are
       installed but not serving as configured, so the test round would measure the
       rig rather than the SUT."
fi

# Both markers carry the fingerprint of the plan that was just installed, so the
# next start can tell whether this installation is the one it wants (see the
# Idempotency section). The copy in mgr/ shares its lifetime with the databases.
printf '%s\n' "$plan_fp" >"${STATE_DIR}/bootstrap.done"
printf '%s\n' "$plan_fp" >/usr/irissys/mgr/isik-bootstrap.done

# The same fingerprint goes into the SUT's own provenance record, which is what a
# published round archives. Written here rather than by ISiK.Setup because this is
# where it is computed, and a round whose archived plan does not match the
# installation it measured has to be refusable from the bundle alone - which is
# what scripts/70-collect-report.sh does with this field.
python3 - "${STATE_DIR}/iris-provenance.json" "$plan_fp" <<'PY' || \
    say "note: the plan fingerprint could not be added to iris-provenance.json"
import json, sys
path, fp = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as handle:
    doc = json.load(handle)
doc["packagePlanFingerprint"] = fp
with open(path, "w", encoding="utf-8") as handle:
    json.dump(doc, handle, indent=2, ensure_ascii=False)
    handle.write("\n")
PY

set_status "done: endpoints installed and verified"
say "=========================================================================="
say "bootstrap complete"
say "  provenance: ${STATE_DIR}/iris-provenance.json"
say "  HTTP is served by the webgateway container, not by IRIS - see"
say "  docs/en/01-architecture.md"
say "=========================================================================="
