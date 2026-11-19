#!/usr/bin/env bash
# =============================================================================
# 30-render-config.sh - render the deploy-time configuration files
# =============================================================================
# Produces everything the test suite container reads, everything the Web Gateway
# needs in order to serve the SUT's FHIR endpoints at all, and the credential sheet
# for the tester - from templates plus the resolved deployment configuration:
#
#   run/tiger/tiger-isik-<level>.yaml         unattended run  (runTestsOnStart: true,
#                                                              activateWorkflowUi: false)
#   run/tiger/manual/tiger-isik-<level>.yaml  MODE=manual     (runTestsOnStart: false,
#                                                              activateWorkflowUi: true)
#   run/webgateway/CSP.ini                    the gateway's IRIS server connection
#   run/webgateway/isik-vhost.conf            Apache: which paths mod_csp claims
#   run/testdata/<Level>/*.yaml               the suite's logical-key contract
#   run/tiger/RENDERED.txt                    what was rendered, from what, when
#   run/tiger/TESTDATA-PARITY.txt             our contract files vs the release's
#   run/CREDENTIALS.md                        the credential sheet for the tester
#
# and, only for a round with CONNECT_ENABLE=true (see section 7 of .env.example):
#
#   run/tiger/tiger-isik-stufe5-connect.yaml  the Connect module's own invocation
#   run/tiger/manual/…-connect.yaml           its MODE=manual variant
#   run/webgateway/isik-vhost-tls.conf        Apache: the HTTPS listener bearer
#                                             tokens require
#   run/as/realm-isik.json                    the authorization server's realm
#
# None of those four is produced otherwise, and that is what makes a round
# without the authorization server byte-identical to every round taken before
# the Connect module could be run at all.
#
# <level> is every level the pinned suite contains - suite_levels() in lib.sh,
# which is Stufe3 and Stufe5 up to 2.7.0 and Stufe5 alone from 3.0.0, that
# release having removed ISiK Level 3 entirely. Nothing here enumerates levels
# itself, so a level the suite does not carry produces no configuration file, no
# staged contract and no report section, rather than an empty one that reads as a
# level which ran and scored nothing.
#
# Why render at all, rather than committing finished YAML: the SUT URL, the
# generated API password and the port assignments are deploy-time facts. A
# committed file would either carry a placeholder that someone must remember to
# edit - the single most likely way for a round to be run against the wrong
# server - or a secret. Templating makes the substitution mechanical and
# verifiable, and the RENDERED.txt record makes it auditable.
#
# Why run/testdata is a copy rather than a direct mount of testdata/: our
# testdata/ tree also holds the fabricated FHIR instances under testdata/fhir/,
# which the suite must not see (it would try to read them as configuration files
# and Tiger resolves additionalConfigurationFiles relative to /app/testdata).
# The copy contains exactly the contract files the Tiger configuration lists.
# =============================================================================

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

step "Rendering the test tool configuration"

load_env
# TESTSUITE_VERSION is required here even though nothing is downloaded: it
# selects the suite generation, and therefore which levels exist, which
# templates are rendered and which contract is staged.
#
# FHIR_BASE_STUFE3 stays required for every generation. It names an endpoint of
# the system under test, not a level of the test suite: the IRIS bootstrap
# installs both endpoints and the Web Gateway's virtual host claims both paths
# regardless of which levels the pinned suite carries. Dropping it under a 3.x
# suite would change the system under test between rounds and make the two
# rounds' results incomparable.
require_var TESTSUITE_VERSION IRIS_IP FHIR_BASE_STUFE3 FHIR_BASE_STUFE5 \
            SUT_USER SUT_PASSWORD \
            IRIS_MGMT_USER IRIS_MGMT_PASSWORD WEBGATEWAY_IP

# Per generation, not fixed: 3.0.0 changed both the set of levels and the key
# namespaces, so its templates and its contract live in their own trees and the
# 2.7.0 ones stay exactly as they were. See suite_tiger_dir() /
# suite_testdata_root() in lib.sh for why the trees are parallel rather than
# overwritten - the August 2026 baseline round must stay re-creatable from this
# repository.
TIGER_TMPL_DIR="$(suite_tiger_dir)"
WEBGATEWAY_TMPL_DIR="${REPO_ROOT}/webgateway"
TESTDATA_SRC="$(suite_testdata_root)"
OUT_WEBGATEWAY="${RUN_DIR}/webgateway"
OUT_TIGER="${RUN_DIR}/tiger"
OUT_TIGER_MANUAL="${OUT_TIGER}/manual"
OUT_TESTDATA="${RUN_DIR}/testdata"
OUT_AS="${RUN_DIR}/as"              # Connect rounds only; see section 2b

WORKFLOW_UI_PORT=9010     # the port INSIDE the container; see compose/testsuite.yml
PROXY_ADMIN_PORT=9011

# -----------------------------------------------------------------------------
# 1. Render the Tiger configuration
# -----------------------------------------------------------------------------
# Substitution is done in python rather than with sed, for two reasons: the
# password may contain any character (it is generated, and a `/` or `&` would
# corrupt a sed replacement), and an unsubstituted @@PLACEHOLDER@@ must be a hard
# error rather than something that reaches the container.
render() {
  local tmpl="$1" out="$2" run_tests="$3" base_url="$4"

  mkdir -p "$(dirname "$out")"

  # The WorkflowUI is the mirror image of runTestsOnStart, and deliberately derived
  # from it rather than passed separately: an unattended run must NOT activate it.
  # Tiger blocks for 120 s waiting for a browser to fetch status from the UI and
  # aborts the run outright if none does - `[ERROR] No feedback from workflow Ui,
  # aborting!`, `Tests run: 0`, and a test-report.zip holding only pom.xml, all
  # inside two minutes. In a detached container nothing ever attaches. Upstream's
  # README prescribes exactly this ("set lib.activateWorkflowUi to false" for CI);
  # ours had it hardcoded true for both variants, and that cost a round.
  #
  # MODE=manual is the case the UI exists for, and there runTestsOnStart is false,
  # so one boolean serves both: activateWorkflowUi = NOT runTestsOnStart.
  local activate_ui
  [[ "$run_tests" == true ]] && activate_ui=false || activate_ui=true

  SUT_BASE_URL="$base_url" \
  SUT_USER="$SUT_USER" \
  SUT_PASSWORD="$SUT_PASSWORD" \
  WORKFLOW_UI_PORT="$WORKFLOW_UI_PORT" \
  PROXY_ADMIN_PORT="$PROXY_ADMIN_PORT" \
  RUN_TESTS_ON_START="$run_tests" \
  ACTIVATE_WORKFLOW_UI="$activate_ui" \
  python3 - "$tmpl" "$out" <<'PY'
import os
import sys

# What counts as a placeholder is one definition for all three renderers; what
# may be substituted is per family and stays in this function. See the module's
# own header.
sys.path.insert(0, os.path.join(os.environ["SCRIPTS_DIR"], "lib"))
from template_placeholders import leftover_placeholders, unknown_placeholders

tmpl_path, out_path = sys.argv[1], sys.argv[2]

# Only these keys are substitutable. An unknown @@NAME@@ in a template is a
# template bug and must not silently render as an empty string.
SUBST = {
    "SUT_BASE_URL":       os.environ["SUT_BASE_URL"],
    "SUT_USER":           os.environ["SUT_USER"],
    "SUT_PASSWORD":       os.environ["SUT_PASSWORD"],
    "WORKFLOW_UI_PORT":   os.environ["WORKFLOW_UI_PORT"],
    "PROXY_ADMIN_PORT":   os.environ["PROXY_ADMIN_PORT"],
    "RUN_TESTS_ON_START": os.environ["RUN_TESTS_ON_START"],
    "ACTIVATE_WORKFLOW_UI": os.environ["ACTIVATE_WORKFLOW_UI"],
}

with open(tmpl_path, encoding="utf-8") as fh:
    text = fh.read()

unknown = unknown_placeholders(text, SUBST)
if unknown:
    sys.exit(f"30-render-config.sh: {tmpl_path}: unknown placeholder(s): {', '.join(unknown)}")

for key, value in SUBST.items():
    text = text.replace(f"@@{key}@@", value)

left = leftover_placeholders(text)
if left:
    sys.exit(f"30-render-config.sh: {out_path}: unresolved placeholder(s): {', '.join(left)}")

# The result must be valid YAML before the container gets it: Tiger fails with a
# Spring configuration stack trace that says nothing useful about which line is
# wrong.
try:
    import yaml
    yaml.safe_load(text)
except ImportError:
    print("  (PyYAML unavailable - rendered YAML not syntax-checked)", file=sys.stderr)
except Exception as exc:  # yaml.YAMLError
    sys.exit(f"30-render-config.sh: {out_path}: rendered file is not valid YAML: {exc}")

with open(out_path, "w", encoding="utf-8") as fh:
    fh.write(text)
PY

  # 0644, not 0600, and that is forced by the consumer rather than chosen.
  #
  # This file is bind-mounted into the test suite container, which runs as its own
  # unprivileged uid 100 (`tiger-testsuite`) - not as the operator. A bind mount
  # carries the host inode's owner and mode straight through, so a 0600 file owned
  # by the host user is unreadable inside the container, and Tiger fails with
  #
  #   Error while reading configuration from file '/app/tiger-isik-stufe3.yaml'
  #   with cause 'java.nio.file.AccessDeniedException: tiger-isik-stufe3.yaml'
  #
  # followed by a Maven BUILD FAILURE and a test-report.zip containing nothing but
  # pom.xml. It is the same uid-versus-bind-mount trap the licence key springs (see
  # docs/en/02-setup.md), and it cost a round here.
  #
  # chown to uid 100 would be the tighter fix but needs root, which the operator may
  # not have - and unlike run/iris-state, this is a file compose mounts read-only, so
  # 0644 grants the container exactly the read it needs and nothing more. The
  # protection that actually matters for this file is elsewhere and unaffected: run/
  # is gitignored, `make check` verifies that mechanically, and the credential it
  # carries is documented on purpose (see run/CREDENTIALS.md).
  chmod 0644 "$out"
}

rm -rf "$OUT_TIGER"
mkdir -p "$OUT_TIGER" "$OUT_TIGER_MANUAL"

RENDERED_CONFIGS=()      # the unattended variants, consumed by section 2 below
nlevels=0
for stufe in $(suite_levels_lc); do
  tmpl="${TIGER_TMPL_DIR}/tiger-isik-${stufe}.yaml.tmpl"
  [[ -f "$tmpl" ]] || die "template missing: ${tmpl#$REPO_ROOT/}
  suite_levels() reports '$(suite_levels)' for TESTSUITE_VERSION=${TESTSUITE_VERSION:-?},
  so a template is required for each. Templates are per generation: see
  suite_tiger_dir() in scripts/lib.sh."

  base_url="$(sut_base_url "$stufe")"

  # Unattended: the suite runs on container start and exits.
  render "$tmpl" "${OUT_TIGER}/tiger-isik-${stufe}.yaml" true "$base_url"
  # Manual: the container comes up, serves the WorkflowUI and waits.
  render "$tmpl" "${OUT_TIGER_MANUAL}/tiger-isik-${stufe}.yaml" false "$base_url"

  RENDERED_CONFIGS+=("${OUT_TIGER}/tiger-isik-${stufe}.yaml")
  nlevels=$((nlevels+1))
  log "${stufe}: SUT is ${base_url}"
done
ok "rendered $((nlevels*2)) Tiger configuration files (${nlevels} unattended, ${nlevels} manual)"

# The Connect module's own invocation. One level, two configurations - the only
# place in this rig where that happens, and it is forced by the module rather
# than chosen: its feature files carry their own `Authorization: Bearer` header,
# and a Tiger route with an `authentication:` block REPLACES that header. Measured
# on 2026-11-13: run through the ordinary Stufe 5 configuration the recorded
# traffic contained no bearer token at all, and the SUT answered 200 to every
# request including the one the feature requires to be refused - four of six
# scenarios "passed" on the harness's credential rather than on the token under
# test. The full reasoning, including why this configuration addresses the HTTPS
# listener, is in the template's own header.
#
# Rendered as a sibling of the per-level configurations and added to
# RENDERED_CONFIGS, so the test-data staging and the baseKey check below treat it
# exactly like any other rendered configuration.
if connect_enabled; then
  require_var SUT_TLS_HOST

  connect_tmpl="${TIGER_TMPL_DIR}/tiger-isik-stufe5-connect.yaml.tmpl"
  [[ -f "$connect_tmpl" ]] || die "template missing: ${connect_tmpl#$REPO_ROOT/}
  CONNECT_ENABLE is true, which requires the Connect module's own Tiger
  configuration. Templates are per generation: see suite_tiger_dir() in
  scripts/lib.sh. The Connect module exists in suite generation 3.x only."

  case " $(suite_levels_lc) " in
    *" stufe5 "*) ;;
    *) die "CONNECT_ENABLE is true but the pinned suite ${TESTSUITE_VERSION} carries no Stufe 5 ($(suite_levels)) - the Connect module is part of ISiK Stufe 5" ;;
  esac

  connect_base_url="$(sut_base_url_tls stufe5)"
  render "$connect_tmpl" "${OUT_TIGER}/tiger-isik-stufe5-connect.yaml" true "$connect_base_url"
  render "$connect_tmpl" "${OUT_TIGER_MANUAL}/tiger-isik-stufe5-connect.yaml" false "$connect_base_url"
  RENDERED_CONFIGS+=("${OUT_TIGER}/tiger-isik-stufe5-connect.yaml")
  ok "rendered the Connect module's own Tiger configuration (SUT is ${connect_base_url})"
fi

# -----------------------------------------------------------------------------
# 1b. Render the Web Gateway configuration
# -----------------------------------------------------------------------------
# The gateway is what actually serves HTTP for the SUT - the official IRIS images
# ship no web server (see sut_base_url() in lib.sh) - so its configuration is as
# load-bearing as the Tiger configuration and is generated from templates for the
# same reasons: no hand-edited copy that can disagree with the deployment, and a
# hard failure on an unsubstituted placeholder rather than a container that starts
# and 404s.
#
# Separate substitution table from the Tiger one: these files are INI and Apache
# config, not YAML, and the keys have nothing in common. Sharing render() would
# mean allowing Tiger's placeholders in Apache config and vice versa, and losing
# the YAML syntax check that render() performs.
render_gateway() {
  local tmpl="$1" out="$2"

  mkdir -p "$(dirname "$out")"

  IRIS_IP="$IRIS_IP" \
  IRIS_SUPERSERVER_PORT=1972 \
  GATEWAY_IRIS_USER="$GATEWAY_IRIS_USER" \
  GATEWAY_IRIS_PASSWORD="$GATEWAY_IRIS_PASSWORD" \
  FHIR_BASE_STUFE3="$FHIR_BASE_STUFE3" \
  FHIR_BASE_STUFE5="$FHIR_BASE_STUFE5" \
  SUT_TLS_HOST="${SUT_TLS_HOST:-}" \
  SUT_PORT_HTTPS_INTERNAL="${SUT_PORT_HTTPS_INTERNAL:-443}" \
  python3 - "$tmpl" "$out" <<'PY'
import os
import sys

sys.path.insert(0, os.path.join(os.environ["SCRIPTS_DIR"], "lib"))
from template_placeholders import leftover_placeholders, unknown_placeholders

tmpl_path, out_path = sys.argv[1], sys.argv[2]

SUBST = {
    "IRIS_IP":               os.environ["IRIS_IP"],
    "IRIS_SUPERSERVER_PORT": os.environ["IRIS_SUPERSERVER_PORT"],
    "GATEWAY_IRIS_USER":     os.environ["GATEWAY_IRIS_USER"],
    "GATEWAY_IRIS_PASSWORD": os.environ["GATEWAY_IRIS_PASSWORD"],
    "FHIR_BASE_STUFE3":      os.environ["FHIR_BASE_STUFE3"],
    "FHIR_BASE_STUFE5":      os.environ["FHIR_BASE_STUFE5"],
    # Used by the HTTPS virtual host only, which is rendered for a Connect round
    # only. Present in the table unconditionally so that the two gateway
    # templates share one substitution contract; an unused key renders nothing.
    "SUT_TLS_HOST":            os.environ["SUT_TLS_HOST"],
    "SUT_PORT_HTTPS_INTERNAL": os.environ["SUT_PORT_HTTPS_INTERNAL"],
}

with open(tmpl_path, encoding="utf-8") as fh:
    text = fh.read()

unknown = unknown_placeholders(text, SUBST)
if unknown:
    sys.exit(f"30-render-config.sh: {tmpl_path}: unknown placeholder(s): {', '.join(unknown)}")

for key, value in SUBST.items():
    text = text.replace(f"@@{key}@@", value)

left = leftover_placeholders(text)
if left:
    sys.exit(f"30-render-config.sh: {out_path}: unresolved placeholder(s): {', '.join(left)}")

with open(out_path, "w", encoding="utf-8") as fh:
    fh.write(text)
PY
}

# The account the gateway uses for its own service connection to IRIS. CSPSystem
# is the product's account for this purpose; the bootstrap sets its password to the
# management password and marks it non-expiring, because the official image ships
# it expired and an expired password here produces a 500 on every request with
# only "Access Denied" in the gateway log to go on.
GATEWAY_IRIS_USER="${GATEWAY_IRIS_USER:-CSPSystem}"
GATEWAY_IRIS_PASSWORD="$IRIS_MGMT_PASSWORD"

rm -rf "$OUT_WEBGATEWAY"
mkdir -p "$OUT_WEBGATEWAY"

# The HTTPS virtual host is rendered for a Connect round only. It is a SECOND
# file beside the :80 virtual host, never a replacement: the other nine Stufe 5
# modules and both Stufe 3 rounds keep being served by exactly the configuration
# they were served by before this listener existed, which is what makes their
# results comparable across rounds. compose/authserver.yml is the only thing that
# mounts it.
GATEWAY_FILES=(CSP.ini isik-vhost.conf)
if connect_enabled; then
  require_var SUT_TLS_HOST
  GATEWAY_FILES+=(isik-vhost-tls.conf)
fi

for f in "${GATEWAY_FILES[@]}"; do
  tmpl="${WEBGATEWAY_TMPL_DIR}/${f}.tmpl"
  [[ -f "$tmpl" ]] || die "template missing: ${tmpl#$REPO_ROOT/}"
  render_gateway "$tmpl" "${OUT_WEBGATEWAY}/${f}"
done

# CSP.ini carries the gateway's IRIS credential. Same treatment as the rendered
# Tiger configuration: the value is documented in the reports by design, but the
# live file mimics production. The vhost configs hold no secret.
chmod 0600 "${OUT_WEBGATEWAY}/CSP.ini"
chmod 0644 "${OUT_WEBGATEWAY}/isik-vhost.conf"
if connect_enabled; then
  chmod 0644 "${OUT_WEBGATEWAY}/isik-vhost-tls.conf"
  ok "rendered the Web Gateway HTTPS virtual host (${SUT_TLS_HOST}:${SUT_PORT_HTTPS_INTERNAL:-443})"
fi
ok "rendered the Web Gateway configuration (IRIS at ${IRIS_IP}:1972 as ${GATEWAY_IRIS_USER})"

# -----------------------------------------------------------------------------
# 2. Stage the test-data contract
# -----------------------------------------------------------------------------
# These YAML files are the suite's own contract: every key is referenced from a
# feature file as ${<namespace>.<key>} and its value is the resource id that must
# exist in the SUT. The files that exist upstream - 11 in 2.7.0, 9 in 3.0.9, see
# suite_contract_files() - are carried over byte-identically, and step 3 below
# proves it rather than asserting it, because a changed value would mean our data
# conforms to us rather than to the official configuration. The
# zz-upstream-key-defects*.yaml overlays are ours; see suite_overlay_files() and
# docs/en/05-testdata.md.
#
# Only the files the corresponding tiger-isik-*.yaml lists are staged, and the
# list is read from the rendered configuration rather than restated here: a file
# present in additionalConfigurationFiles but absent on disk makes Tiger abort at
# startup, and the two lists must not be able to drift apart.
rm -rf "$OUT_TESTDATA"
mkdir -p "$OUT_TESTDATA"

expected=0
missing=""
while read -r rel; do
  [[ -n "$rel" ]] || continue
  expected=$((expected+1))
  src="${TESTDATA_SRC}/${rel#testdata/}"
  if [[ ! -f "$src" ]]; then
    missing="${missing}${missing:+, }${rel}"
    continue
  fi
  dest="${OUT_TESTDATA}/${rel#testdata/}"
  mkdir -p "$(dirname "$dest")"
  cp "$src" "$dest"
done < <(python3 - "${RENDERED_CONFIGS[@]}" <<'PY'
import sys

import yaml

seen = []
for path in sys.argv[1:]:
    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    for entry in cfg.get("additionalConfigurationFiles") or []:
        name = entry.get("filename")
        if name and name not in seen:
            seen.append(name)
print("\n".join(seen))
PY
)

if [[ -n "$missing" ]]; then
  die "test-data contract files listed in the Tiger configuration but absent from ${TESTDATA_SRC#$REPO_ROOT/}/: ${missing}"
fi

# The baseKeys of the rendered configuration must be exactly the namespaces the
# feature files of this generation reference. The check exists because the two
# are stated in different places - the namespace list in lib.sh, the baseKeys in
# the Tiger templates - and because neither failure direction is visible at run
# time: a missing baseKey leaves every ${<ns>.<key>} of that module as
# unrecognised text, which the suite sends verbatim in a request URL and the SUT
# answers 404, i.e. it reads as a resource the system under test does not have;
# a surplus baseKey loads a module the features never reference, which seeds
# resources no scenario reads.
python3 - "$(suite_namespaces)" "${RENDERED_CONFIGS[@]}" <<'PY'
import sys

import yaml

expected = set(sys.argv[1].split())
found = {}
for path in sys.argv[2:]:
    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    for entry in cfg.get("additionalConfigurationFiles") or []:
        base = entry.get("baseKey")
        name = entry.get("filename", "?")
        if not base:
            sys.exit(f"30-render-config.sh: {path}: entry '{name}' has no baseKey; "
                     "a test-data file loaded without one is unreachable from any feature file")
        found.setdefault(base, []).append(name)

missing = sorted(expected - set(found))
surplus = sorted(set(found) - expected)
if missing or surplus:
    parts = []
    if missing:
        parts.append("namespaces the feature files reference but the rendered "
                     "configuration does not load: " + ", ".join(missing))
    if surplus:
        parts.append("baseKeys the rendered configuration loads but no feature "
                     "file references: " + ", ".join(surplus))
    sys.exit("30-render-config.sh: " + "; ".join(parts)
             + "\n  Expected exactly: " + " ".join(sorted(expected))
             + "\n  Reconcile suite_namespaces() in scripts/lib.sh with the baseKey "
               "of every entry in the Tiger templates.")

print(f"  {len(found)} test-data namespace(s): " + " ".join(sorted(found)))
PY

staged="$(find "$OUT_TESTDATA" -name '*.yaml' | wc -l | tr -d ' ')"
(( staged == expected )) \
  || die "staged ${staged} test-data files but the Tiger configuration lists ${expected}"

# Every key must have a non-empty value: an empty one resolves to the literal
# string "${data.<key>}" in the request URL and produces a 404 that reads as a
# missing resource rather than as a configuration hole.
#
# With one recorded exception, below. The upstream files must stay byte-identical
# (step 3 enforces it), so an empty value upstream cannot be fixed here - it can
# only be either tolerated with a reason or made fatal, and making it fatal would
# make the round impossible to run at all.
python3 - "$OUT_TESTDATA" <<'PY'
import os
import sys

import yaml

# Keys that are empty in the official release and must stay that way, with the
# reason each one is harmless. Anything not listed here is a hole in OUR test data
# and stops the round.
#
# Verified by grepping every feature file of both pinned releases - 220 files in
# 2.7.0, 203 in 3.0.9 - for the key under both of its possible qualifications
# (${data.appointment-read-specialty-system} and
# ${terminplanung.appointment-read-specialty-system}): no feature references it
# in either release, and it is the only empty value in either contract. Every
# place the Appointment specialty system is actually needed spells it out inline
# as the literal http://ihe-d.de/CodeSystems/AerztlicheFachrichtungen -
#   Stufe5/Terminplanung/Appointment-Read.feature
#   Stufe5/Terminplanung/Appointment-Search.feature
#   Stufe5/Terminplanung/Schedule-Search.feature        (3.0.9)
#   Stufe5/Terminplanung/HealthcareService-Search.feature (3.0.9)
# - so the key is an unused leftover and no request URL can inherit its
# emptiness. The path is the same in both generations, so one entry covers both.
UPSTREAM_EMPTY_OK = {
    "Stufe5/terminplanung.yaml:appointment-read-specialty-system":
        "empty upstream; referenced by no feature file in 2.7.0 or 3.0.9 (the "
        "Terminplanung features hardcode the system inline)",
}

root = sys.argv[1]
empty = []
tolerated = []
total = 0
for dirpath, _, filenames in os.walk(root):
    for name in sorted(filenames):
        if not name.endswith(".yaml"):
            continue
        path = os.path.join(dirpath, name)
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        if not isinstance(data, dict):
            sys.exit(f"30-render-config.sh: {path}: expected a flat key -> value mapping")
        for key, value in data.items():
            total += 1
            if value is None or str(value).strip() == "":
                ident = f"{os.path.relpath(path, root)}:{key}"
                (tolerated if ident in UPSTREAM_EMPTY_OK else empty).append(ident)

if empty:
    sys.exit(
        "30-render-config.sh: test-data keys with no value: "
        + ", ".join(empty)
        + "\n  Each key must name a resource id that exists in the SUT; an empty "
          "value resolves to a literal ${data.<key>} in the request URL."
        + "\n  If the key is empty in the official release and provably unused, "
          "record it in UPSTREAM_EMPTY_OK in this script with the evidence."
    )

print(f"  {total} test-data keys, {total - len(tolerated)} populated")
for ident in sorted(tolerated):
    print(f"  empty by design: {ident} - {UPSTREAM_EMPTY_OK[ident]}")
PY

ok "staged ${staged} test-data contract files into run/testdata/"

# -----------------------------------------------------------------------------
# 2b. Connect: the authorization server's realm
# -----------------------------------------------------------------------------
# Rendered here, after the test-data contract has been staged, because one of its
# values comes from that contract: the `patient` launch-context claim of the two
# patient-context clients must be the patient the suite's own test data declares
# as the patient in context. In a production SMART launch that claim is
# established by the authorization server during the launch; there is no launch
# in a client-credentials round, so it is configured - and configured from the
# suite's file rather than from ours, so that the realm cannot drift away from
# the scenarios that read it.
#
# Not rendered at all unless CONNECT_ENABLE=true, and compose/authserver.yml is
# the only thing that mounts it. Removed unconditionally first, like every other
# output directory here, so that turning Connect off leaves no realm behind for a
# later round to import by accident.
rm -rf "$OUT_AS"

# A function, like render() and render_gateway() above, and for a reason beyond
# symmetry: `make check` reads each render function's substitution table on its
# own and holds exactly one template directory to it, so a placeholder that
# belongs to the realm cannot be silently accepted in an Apache configuration or
# the other way round. Pooling the tables would defeat that separation, and the
# function boundary is what the check uses to find them.
render_realm() {
  local tmpl="$1" out="$2" contract="$3"

  AS_REALM="$AS_REALM" \
  AS_TOKEN_LIFESPAN="${AS_TOKEN_LIFESPAN:-3600}" \
  AS_CLIENT_SECRET="$AS_CLIENT_SECRET" \
  AS_RESOURCE_CLIENT_ID="$AS_RESOURCE_CLIENT_ID" \
  SUT_AUDIENCE="$SUT_AUDIENCE" \
  CONNECT_TOKEN_MAP="$(connect_token_clients)" \
  python3 - "$tmpl" "$out" "$contract" <<'PY'
import json
import os
import sys

import yaml

sys.path.insert(0, os.path.join(os.environ["SCRIPTS_DIR"], "lib"))
from template_placeholders import leftover_placeholders, unknown_placeholders

tmpl_path, out_path, contract_path = sys.argv[1], sys.argv[2], sys.argv[3]

with open(contract_path, encoding="utf-8") as fh:
    contract = yaml.safe_load(fh) or {}
patient = str(contract.get("read-patient-in-context-id", "") or "").strip()
if not patient:
    sys.exit(f"30-render-config.sh: {contract_path}: no read-patient-in-context-id - "
             "the patient-context clients cannot be configured")

SUBST = {
    "AS_REALM":                 os.environ["AS_REALM"],
    "AS_TOKEN_LIFESPAN":        os.environ["AS_TOKEN_LIFESPAN"],
    "AS_CLIENT_SECRET":         os.environ["AS_CLIENT_SECRET"],
    "AS_RESOURCE_CLIENT_ID":    os.environ["AS_RESOURCE_CLIENT_ID"],
    "SUT_AUDIENCE":             os.environ["SUT_AUDIENCE"],
    "CONNECT_PATIENT_IN_CONTEXT": patient,
}

with open(tmpl_path, encoding="utf-8") as fh:
    text = fh.read()

unknown = unknown_placeholders(text, SUBST)
if unknown:
    sys.exit(f"30-render-config.sh: {tmpl_path}: unknown placeholder(s): {', '.join(unknown)}")

for key, value in SUBST.items():
    # Every placeholder but one sits inside a JSON string literal, and the
    # generated client secret is the only value not under our control
    # character by character. json.dumps gives the correct escaping for the
    # literal; the numeric lifespan is left bare and is validated by the parse
    # below.
    if key == "AS_TOKEN_LIFESPAN":
        text = text.replace(f"@@{key}@@", value)
    else:
        text = text.replace(f"@@{key}@@", json.dumps(value)[1:-1])

left = leftover_placeholders(text)
if left:
    sys.exit(f"30-render-config.sh: {out_path}: unresolved placeholder(s): {', '.join(left)}")

try:
    realm = json.loads(text)
except Exception as exc:
    sys.exit(f"30-render-config.sh: {out_path}: rendered file is not valid JSON: {exc}")

if not isinstance(realm.get("accessTokenLifespan"), int):
    sys.exit("30-render-config.sh: AS_TOKEN_LIFESPAN must be an integer number of "
             f"seconds, got {os.environ['AS_TOKEN_LIFESPAN']!r}")

# The realm must define exactly the clients the mint step will ask for, each
# holding exactly the one scope its token is under test for. The two are stated
# in different files - connect_token_clients() in scripts/lib.sh and this
# template - and neither failure direction is visible at run time: a missing
# client fails the mint with an opaque `invalid_client`, and a client holding
# two scopes silently makes a refusal scenario pass for the wrong reason.
expected = {}
for line in os.environ["CONNECT_TOKEN_MAP"].splitlines():
    if not line.strip():
        continue
    _key, client, scope = line.split("|")
    expected[client] = scope

clients = {c.get("clientId"): c for c in realm.get("clients") or []}
problems = []
for client, scope in sorted(expected.items()):
    if client not in clients:
        problems.append(f"no client '{client}' (needed for scope {scope})")
        continue
    have = clients[client].get("defaultClientScopes") or []
    if have != [scope]:
        problems.append(f"client '{client}' has default scopes {have}, expected exactly ['{scope}']")

declared = {s.get("name") for s in realm.get("clientScopes") or []}
for scope in sorted(set(expected.values()) - declared):
    problems.append(f"scope '{scope}' is used by a client but not declared in clientScopes")

if problems:
    sys.exit("30-render-config.sh: the authorization server realm does not match "
             "connect_token_clients() in scripts/lib.sh:\n  - " + "\n  - ".join(problems))

# The `_comment` arrays carry this file's rationale, which belongs with the
# artefact rather than only in the template - but they are not part of
# Keycloak's RealmRepresentation. Rather than relying on the importer to
# tolerate unknown properties, they are removed here: the template keeps the
# explanation, the rendered file keeps only what Keycloak defines.
def strip_comments(node):
    if isinstance(node, dict):
        return {k: strip_comments(v) for k, v in node.items() if k != "_comment"}
    if isinstance(node, list):
        return [strip_comments(v) for v in node]
    return node

with open(out_path, "w", encoding="utf-8") as fh:
    json.dump(strip_comments(realm), fh, indent=2, ensure_ascii=False)
    fh.write("\n")

print(f"  realm '{realm['realm']}': {len(clients)} clients, "
      f"{len(realm.get('clientScopes') or [])} scopes, patient in context {patient}")
PY
}

if connect_enabled; then
  require_var AS_REALM AS_CLIENT_SECRET AS_RESOURCE_CLIENT_ID SUT_AUDIENCE

  CONNECT_CONTRACT="${OUT_TESTDATA}/Stufe5/connect.yaml"
  [[ -f "$CONNECT_CONTRACT" ]] || die "the Connect test-data contract was not staged (${CONNECT_CONTRACT#$RUN_DIR/}) - the rendered Tiger configuration must list testdata/Stufe5/connect.yaml"

  mkdir -p "$OUT_AS"

  render_realm "${REPO_ROOT}/keycloak/realm-isik.json.tmpl" \
               "${OUT_AS}/realm-isik.json" \
               "$CONNECT_CONTRACT"

  # 0644, and forced by the consumer exactly as the rendered Tiger configuration
  # is: the file is bind-mounted into the Keycloak container, which runs as its
  # own unprivileged account, and a bind mount carries the host inode's mode and
  # owner straight through. An unreadable import file makes Keycloak start with
  # an empty realm, which presents as every token request failing with
  # `invalid_client` rather than as a file permission problem.
  #
  # It carries the clients' shared secret. That secret is generated per
  # deployment by scripts/10-resolve-versions.sh, is listed in
  # run/CREDENTIALS.md on purpose, and grants nothing beyond minting a token for
  # this round's own test realm. run/ is gitignored and `make check` verifies
  # that mechanically.
  chmod 0644 "${OUT_AS}/realm-isik.json"
  ok "rendered the authorization server realm into run/as/realm-isik.json"
fi

# -----------------------------------------------------------------------------
# 3. Prove upstream parity of the contract files
# -----------------------------------------------------------------------------
# The claim "our test-data contract is the official one" underpins the
# Bestaetigungsverfahren submission, so it is checked mechanically against the
# copy 20-fetch-packages.sh fetched from the pinned release tag - not against a
# comment. (Not from the image: the image carries no /app/testdata at all. See
# fetch_upstream_sources() in 20-fetch-packages.sh.) Three outcomes here, and a
# fourth that a later step can substitute for the first:
#
#   IDENTICAL  same bytes as the released file. Expected for every file
#              suite_contract_files() lists - 11 in 2.7.0, 9 in 3.0.9.
#   MODIFIED   same path, different bytes. Fatal: someone edited a value, and the
#              run would test against a contract that is not the official one.
#   ADDED      no counterpart upstream. Expected only for the
#              zz-upstream-key-defects*.yaml overlays, which are ours by design;
#              anything else is fatal.
#
#   PARAMETERISED  written over an IDENTICAL row by `make tokens`, for the one
#              file whose token keys the official release ships empty. It is a
#              checked claim - update_parity() in scripts/lib/connect_tokens.py
#              requires every other key to still hold the released value - and it
#              exists because this step necessarily runs BEFORE the tokens are
#              minted, so leaving the row at IDENTICAL would make the round's own
#              evidence claim byte-identity for a file that no longer has it.
#
# When vendor/upstream/testdata is absent (someone ran this before `make packages`)
# the check is skipped with a warning rather than failing: the staged files are
# still valid, but the evidence record must say the parity was not verified.
UPSTREAM_TESTDATA="${VENDOR_DIR}/upstream/testdata"
PARITY_FILE="${OUT_TIGER}/TESTDATA-PARITY.txt"

# Files that legitimately have no upstream counterpart, per generation. Anything
# else that shows up as ADDED is an accident and must stop the round.
ALLOWED_ADDED="$(suite_overlay_files | tr '\n' ' ')"

if [[ -d "$UPSTREAM_TESTDATA" ]]; then
  modified=""; added=""; identical=0
  {
    printf '# Test-data contract parity: staged vs the pinned official release\n'
    printf '# generated %s by scripts/30-render-config.sh\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '# upstream copy fetched by scripts/20-fetch-packages.sh from\n'
    printf '#   https://raw.githubusercontent.com/%s/%s/testdata/\n' \
           "${TESTSUITE_REPO:-gematik/isik-testsuite}" "${TESTSUITE_VERSION:-?}"
    printf '# The release tag, not the image: %s:%s carries no /app/testdata - the\n' \
           "${TESTSUITE_IMAGE:-?}" "${TESTSUITE_VERSION:-?}"
    printf '# contract is operator-supplied at run time and is mounted in by this rig.\n'
    printf '#\n'
    printf '# IDENTICAL  byte-identical to the released file\n'
    printf '# ADDED      no counterpart upstream (expected: the two overlay files)\n'
    # Written by a later step, not by this one, but documented here because this
    # is the file a reader has in front of them. See update_parity() in
    # scripts/lib/connect_tokens.py.
    printf '# PARAMETERISED  identical to the released file except in named keys whose\n'
    printf '#            released value was the empty token placeholder - written by\n'
    printf '#            `make tokens` for a round with CONNECT_ENABLE=true, with the\n'
    printf '#            keys listed at the end of this file. Any OTHER difference in\n'
    printf '#            such a file is a MODIFIED and stops the round.\n'
    printf '#\n'
    printf '# %-13s %-46s %s\n' 'STATUS' 'FILE' 'SHA256'
    while IFS= read -r f; do
      rel="${f#$OUT_TESTDATA/}"
      up="${UPSTREAM_TESTDATA}/${rel}"
      if [[ ! -f "$up" ]]; then
        printf '  %-13s %-46s %s\n' 'ADDED' "$rel" "$(sha256_of "$f")"
        case " $ALLOWED_ADDED " in
          *" $rel "*) ;;
          *) added="${added}${added:+, }${rel}" ;;
        esac
      elif cmp -s "$f" "$up"; then
        printf '  %-13s %-46s %s\n' 'IDENTICAL' "$rel" "$(sha256_of "$f")"
        identical=$((identical+1))
      else
        printf '  %-13s %-46s %s\n' 'MODIFIED' "$rel" "$(sha256_of "$f")"
        modified="${modified}${modified:+, }${rel}"
      fi
    done < <(find "$OUT_TESTDATA" -name '*.yaml' | sort)
  } >"$PARITY_FILE"

  if [[ -n "$modified" ]]; then
    die "test-data contract files differ from the official ${TESTSUITE_REPO:-gematik/isik-testsuite}@${TESTSUITE_VERSION:-?} release: ${modified}
  These files must stay byte-identical to the official release: a changed value
  means the run tests against a contract that is not the certified one. To add
  keys, put them in one of the overlay files instead: ${ALLOWED_ADDED}
  (relative to ${TESTDATA_SRC#$REPO_ROOT/}/).
  Diff with: diff -u vendor/upstream/testdata/<file> ${TESTDATA_SRC#$REPO_ROOT/}/<file>"
  fi
  if [[ -n "$added" ]]; then
    die "test-data files with no upstream counterpart: ${added}
  Only these overlays may be added: ${ALLOWED_ADDED}
  Remove the file, or fold its keys into the overlay for the namespace it
  belongs to and document them in docs/en/05-testdata.md."
  fi
  ok "test-data parity verified: ${identical} files byte-identical to ${TESTSUITE_REPO:-gematik/isik-testsuite}@${TESTSUITE_VERSION:-?}"
else
  warn "vendor/upstream/testdata is absent - test-data parity NOT verified (run 'make packages' first)"
  {
    printf '# Test-data contract parity: NOT VERIFIED\n'
    printf '# generated %s by scripts/30-render-config.sh\n#\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '# vendor/upstream/testdata did not exist when this ran, so the staged\n'
    printf '# contract files were NOT compared against the official release.\n'
    printf '# Re-run `make packages` followed by `make up` to produce the comparison.\n'
  } >"$PARITY_FILE"
fi

# -----------------------------------------------------------------------------
# 4. Record what was rendered
# -----------------------------------------------------------------------------
# Cited by docs/en/04-testtool.md. Records the source template's checksum and every
# substituted value, so the rendering can be reproduced and checked line by line.
# The API password is one of those values: this is a lab environment whose
# credentials are documented on purpose (see CREDENTIALS.md, written below), and a
# record that omitted it could not be used to reproduce the rendering.
{
  printf '# Rendered test tool configuration\n'
  printf '# generated %s by scripts/30-render-config.sh\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '# configuration source: %s\n' "${ENV_LOADED_FROM#$REPO_ROOT/}"
  printf '#\n'
  printf '# Suite generation, which selects everything below that is per-level:\n'
  printf '  TESTSUITE_VERSION      %s  (generation %s)\n' \
         "$TESTSUITE_VERSION" "$(suite_generation)"
  printf '  levels                 %s\n' "$(suite_levels)"
  printf '  templates              %s\n' "${TIGER_TMPL_DIR#$REPO_ROOT/}"
  printf '  test-data contract     %s\n' "${TESTDATA_SRC#$REPO_ROOT/}"
  printf '  key namespaces         %s\n' "$(suite_namespaces)"
  printf '\n'
  printf '# Substituted values, in full. SUT_PASSWORD is generated per deployment\n'
  printf '# and kept in secrets/sut.password; see CREDENTIALS.md beside this file.\n'
  printf '#\n'
  for stufe in $(suite_levels_lc); do
    printf '  SUT_BASE_URL (%-7s) %s\n' "$stufe" "$(sut_base_url "$stufe")"
  done
  printf '  SUT_USER               %s\n' "$SUT_USER"
  printf '  SUT_PASSWORD           %s\n' "$SUT_PASSWORD"
  printf '  WORKFLOW_UI_PORT       %s\n' "$WORKFLOW_UI_PORT"
  printf '  PROXY_ADMIN_PORT       %s\n' "$PROXY_ADMIN_PORT"
  printf '\n'
  printf '# Per-variant, not global: activateWorkflowUi is the negation of\n'
  printf '# runTestsOnStart. An unattended run must have the UI OFF, or Tiger waits 120s\n'
  printf '# for a browser that never attaches and then aborts the run with no scenarios\n'
  printf '# executed at all.\n'
  printf '#\n'
  printf '  unattended             runTestsOnStart: true,  activateWorkflowUi: false\n'
  printf '  manual                 runTestsOnStart: false, activateWorkflowUi: true\n'
  printf '\n'
  printf '# Web Gateway substitutions. The gateway serves both SUT_BASE_URLs above -\n'
  printf '# the IRIS images ship no web server - so these values are part of the same\n'
  printf '# record: getting them wrong makes every endpoint unreachable.\n'
  printf '#\n'
  printf '  IRIS_IP                %s\n' "$IRIS_IP"
  printf '  IRIS_SUPERSERVER_PORT  %s\n' 1972
  printf '  GATEWAY_IRIS_USER      %s\n' "$GATEWAY_IRIS_USER"
  printf '  GATEWAY_IRIS_PASSWORD  %s\n' "$GATEWAY_IRIS_PASSWORD"
  printf '  WEBGATEWAY_IP          %s\n' "$WEBGATEWAY_IP"
  printf '\n'
  if connect_enabled; then
    printf '# ISiK Stufe 5 "Connect". This round runs the Connect module, which requires\n'
    printf '# an authorization server, an HTTPS listener and six pre-minted access tokens.\n'
    printf '# The module runs as its own invocation with its own Tiger configuration; the\n'
    printf '# header of that file states why. No token value is recorded here or anywhere\n'
    printf '# else - run/tiger/TOKENS.txt identifies each one by SHA-256.\n'
    printf '#\n'
    printf '  CONNECT_ENABLE         %s\n' "${CONNECT_ENABLE}"
    printf '  SUT_BASE_URL (connect) %s\n' "$(sut_base_url_tls stufe5)"
    printf '  SUT_AUDIENCE           %s\n' "${SUT_AUDIENCE}"
    printf '  SUT_TLS_HOST           %s\n' "${SUT_TLS_HOST}"
    printf '  SUT_PORT_HTTPS_INTERNAL %s\n' "${SUT_PORT_HTTPS_INTERNAL:-443}"
    printf '  AS_IMAGE               %s:%s\n' "${AS_IMAGE:-?}" "${AS_VERSION:-?}"
    printf '  AS_HOSTNAME            %s\n' "${AS_HOSTNAME:-?}"
    printf '  AS_REALM               %s\n' "${AS_REALM}"
    printf '  AS_TOKEN_LIFESPAN      %s\n' "${AS_TOKEN_LIFESPAN:-3600}"
    printf '  AS_RESOURCE_CLIENT_ID  %s\n' "${AS_RESOURCE_CLIENT_ID}"
    printf '  AS_CLIENT_SECRET       %s\n' "${AS_CLIENT_SECRET}"
    printf '  patient in context     %s  (read from the staged testdata/Stufe5/connect.yaml)\n' \
           "$(python3 -c 'import sys,yaml;print(yaml.safe_load(open(sys.argv[1]))["read-patient-in-context-id"])' "${OUT_TESTDATA}/Stufe5/connect.yaml")"
    printf '\n'
    printf '# The TLS material this listener and the authorization server present:\n'
    printf '# see run/tls/TLS.txt.\n'
    printf '\n'
  fi
  printf '# Whether the staged test-data contract matches the official release:\n'
  printf '# see TESTDATA-PARITY.txt beside this file.\n'
  printf '\n'
  printf '# %-50s %s\n' 'FILE' 'SHA256'
  # Templates, then the files rendered from them. Both lists are built rather
  # than written out, because which of them exist depends on CONNECT_ENABLE and a
  # round must not record a checksum for a file it did not render.
  #
  # The Connect templates are listed only for a round that rendered them, even
  # though they are present in the repository either way: two rounds of the same
  # configuration must produce the same record, so that a diff between them
  # shows a difference in what was tested rather than a difference in the
  # repository's inventory.
  template_files=()
  for f in "${TIGER_TMPL_DIR}"/*.tmpl "${WEBGATEWAY_TMPL_DIR}"/*.tmpl; do
    case "$(basename "$f")" in
      tiger-isik-stufe5-connect.yaml.tmpl|isik-vhost-tls.conf.tmpl)
        connect_enabled || continue ;;
    esac
    template_files+=( "$f" )
  done
  rendered_files=( "${OUT_TIGER}"/*.yaml "${OUT_TIGER_MANUAL}"/*.yaml
                   "${OUT_WEBGATEWAY}/CSP.ini" "${OUT_WEBGATEWAY}/isik-vhost.conf" )
  if connect_enabled; then
    template_files+=( "${REPO_ROOT}/keycloak/realm-isik.json.tmpl" )
    rendered_files+=( "${OUT_WEBGATEWAY}/isik-vhost-tls.conf"
                      "${OUT_AS}/realm-isik.json" )
  fi
  for f in "${template_files[@]}"; do
    printf '  %-50s %s\n' "$(basename "$(dirname "$f")")/$(basename "$f")" "$(sha256_of "$f")"
  done
  for f in "${rendered_files[@]}"; do
    printf '  %-50s %s\n' "${f#$RUN_DIR/}" "$(sha256_of "$f")"
  done
  while IFS= read -r f; do
    printf '  %-50s %s\n' "${f#$RUN_DIR/}" "$(sha256_of "$f")"
  done < <(find "$OUT_TESTDATA" -name '*.yaml' | sort)
} >"${OUT_TIGER}/RENDERED.txt"

ok "wrote run/tiger/RENDERED.txt"

# -----------------------------------------------------------------------------
# 5. The credential sheet
# -----------------------------------------------------------------------------
# One place a human tester can look up every credential this instance uses, with
# ready-to-paste commands. Written here because this script already holds every
# value and both sut_base_url helpers, and because it shares run/'s lifecycle:
# regenerated by `make up`, removed by `make clean`, gitignored throughout.
#
# The passwords themselves live in secrets/, which `make clean` does NOT remove -
# so `make credentials` can still print them after the sheet is gone.
#
# Mode 0600 like every other file here that carries a credential. Not because the
# value is a meaningful secret in this environment - the reports state it on purpose
# - but because the live environment must keep production-like file modes to be a
# faithful stand-in for a production deployment.
CRED_FILE="${RUN_DIR}/CREDENTIALS.md"

# Resolved once rather than substituted inline: the heredoc below is unquoted (it
# has to interpolate), so a command substitution inside it would run there too -
# harder to read, and a failure would surface as a malformed document rather than as
# a failed script.
SUT_BASE_HOST3="$(sut_base_url_host stufe3)"
SUT_BASE_HOST5="$(sut_base_url_host stufe5)"

# The endpoint table's rows. Both are listed, including one the round may not
# install: the namespaces the SUT creates come from vendor/package-plan.json, which
# plan_packages.py builds over suite_levels_lc(), so under a 3.x suite - which
# dropped ISiK Level 3 - only the Stufe 5 endpoint exists. The sheet must not
# describe an endpoint as "what the suite uses" when the suite does not carry that
# level, and must not imply a URL answers when nothing was installed behind it. The
# third column therefore states scope-and-existence, derived from the same helper
# the rendering loop used.
ENDPOINT_ROWS=""
for stufe in stufe3 stufe5; do
  case " $(suite_levels_lc) " in
    *" $stufe "*) scope="yes - this round" ;;
    *)            scope="no - not in ${TESTSUITE_VERSION}, not installed" ;;
  esac
  ENDPOINT_ROWS+="| Stufe ${stufe#stufe} | $(sut_base_url_host "$stufe") | $(sut_base_url "$stufe") | ${scope} |
"
done

{
  cat <<EOF
# Access credentials - this instance

GENERATED by \`scripts/30-render-config.sh\` on $(date -u +%Y-%m-%dT%H:%M:%SZ) UTC.
Regenerated by every \`make up\`. Not in source control.

This document exists because the credentials of this environment are meant to be
looked up, not protected. The rig is a lab instance brought up for one round of
ISiK conformance testing and destroyed once the results have been verified; access
to it is controlled physically and at the network perimeter, not by these
passwords. They are kept in place - never empty, never absent - so that the system
under test stays a faithful stand-in for a production deployment, and they are
documented here and in the round's reports so that any verdict can be reproduced
by hand.

Each deployment generates its own. Nothing here carries over to another instance.

## Credentials

| Purpose | User | Password | Used by | Stored in |
|---|---|---|---|---|
| FHIR API (HTTP Basic) | \`${SUT_USER}\` | \`${SUT_PASSWORD}\` | the test suite, the seeder, every \`curl\` below | \`secrets/sut.password\` |
| IRIS Management Portal | \`${IRIS_MGMT_USER}\` | \`${IRIS_MGMT_PASSWORD}\` | the human tester | \`secrets/iris-mgmt.password\` |
| Web Gateway → IRIS | \`${GATEWAY_IRIS_USER}\` | \`${IRIS_MGMT_PASSWORD}\` | the gateway's own service connection - not for interactive use | \`secrets/iris-mgmt.password\` (shared with the row above) |

The FHIR API user holds one role, \`ISiKFHIRUser\`, carrying only the endpoint
resource - deliberately not \`%All\`, so the account testing the server cannot
reconfigure it. All three passwords are set to never expire and no account is
flagged for a password change, because either condition arriving mid-round would
present as a wave of refused requests indistinguishable from an authorisation gap in
the SUT. Note that these endpoints refuse an unauthenticated request with **404, not
401** - measured: correct credential 200, missing 404, wrong 404 - so a credential
problem cannot be told from a wrong path by status code alone. See
docs/en/03-sut-iris.md.

The third row is infrastructure, not a login. \`${GATEWAY_IRIS_USER}\` is the account
the Web Gateway authenticates with when it opens its own connection to IRIS, and it
deliberately carries the same password as the Management Portal account: the
bootstrap sets it on the IRIS side and \`run/webgateway/CSP.ini\` receives it on the
gateway side from the same generated value, so the two cannot drift. If it is ever
wrong, every FHIR request returns a bare HTTP 500 and the only trace is
\`Connection Validation Failed ... Access Denied\` in the gateway's log.

## Endpoints

| Level | From the host | From inside the subnet (what the suite uses) | Exercised |
|---|---|---|---|
${ENDPOINT_ROWS}
Both endpoints are installed by every round and their configuration is verified
before any test runs, because they are a property of the system under test. The
right-hand column says whether the pinned test suite, \`${TESTSUITE_VERSION}\`,
carries that level at all: ISiK Level 3 was removed from the suite in release
3.0.0, so a 3.x round exercises Stufe 5 only and the Stufe 3 endpoint stays
installed but untested.

What is verified is not identical for both, and it depends on the round. In an
ordinary round both endpoints are left requiring HTTP Basic authentication, which
is the credential in the first row above. In a round with \`CONNECT_ENABLE=true\`
the Stufe 5 endpoint additionally accepts OAuth 2.0 bearer tokens, because the
ISiK Stufe 5 \`Connect\` module presents them - see the section below. The Stufe 3
endpoint is unaffected either way.

Both address columns address the **Web Gateway**, not IRIS. The IRIS for Health images ship
no web server at all, so the gateway at \`${WEBGATEWAY_IP}\` is the FHIR server's
entire HTTP surface; IRIS itself is reachable only on its superserver port
\`${IRIS_IP}:1972\`, which speaks no HTTP. The suite uses the right-hand column,
which is what the rendered Tiger configuration and the gap report contain. From the
host, use the left - the same gateway, via the published port ${IRIS_PORT_WEB:-52773}.

## Ready to paste

Both endpoints answer a CapabilityStatement, which is the quickest confirmation
that the credential and the endpoint are both good:

\`\`\`bash
curl -u '${SUT_USER}:${SUT_PASSWORD}' \\
  -H 'Accept: application/fhir+json' \\
  ${SUT_BASE_HOST3}/metadata

curl -u '${SUT_USER}:${SUT_PASSWORD}' \\
  -H 'Accept: application/fhir+json' \\
  ${SUT_BASE_HOST5}/metadata
\`\`\`

Read back a resource the seeder created, or re-issue a request the gap report
quotes - the \`Authorization\` header there is this same credential:

\`\`\`bash
curl -u '${SUT_USER}:${SUT_PASSWORD}' \\
  -H 'Accept: application/fhir+json' \\
  '${SUT_BASE_HOST3}/Patient?_count=5'
\`\`\`

Management Portal, in a browser on the host:

    http://127.0.0.1:${IRIS_PORT_WEB:-52773}/csp/sys/UtilHome.csp
    user ${IRIS_MGMT_USER}, password ${IRIS_MGMT_PASSWORD}

It must log in without asking for a password change. If it does ask, the bootstrap
did not complete its security step - check \`run/iris-state/bootstrap.log\`.

A terminal on the instance, no password needed (\`docker exec\` is already root):

\`\`\`bash
make shell-iris
\`\`\`

EOF

  # The Connect section exists only for a round that has an authorization
  # server. A sheet that described one that was never started would send the
  # tester looking for a container that does not exist.
  if connect_enabled; then
    cat <<EOF
## ISiK Stufe 5 "Connect" - the authorization server

This round runs the \`Connect\` module, so it has a second credential domain: an
authorization server issues the access tokens the module presents to the FHIR
server. Everything here is generated per deployment and is thrown away with the
round.

| Purpose | User / client | Secret | Used by | Stored in |
|---|---|---|---|---|
| Authorization server admin console | \`${AS_ADMIN_USER:-kcadmin}\` | \`${AS_ADMIN_PASSWORD:-?}\` | the human tester | \`secrets/as-admin.password\` |
| The six token-holder clients | see \`run/as/realm-isik.json\` | \`${AS_CLIENT_SECRET}\` | \`make tokens\` | \`secrets/as-client.secret\` |
| The FHIR server's own client | \`${AS_RESOURCE_CLIENT_ID}\` | \`${AS_CLIENT_SECRET}\` (shared) | IRIS, to introspect a presented token | \`secrets/as-client.secret\` |

The third row is infrastructure, not a login: it is the client IRIS authenticates
with when it asks the authorization server about a token it has been presented.
Without it the introspection call goes out unauthenticated and every Connect
scenario fails with a bare 401 that looks like a rejected signature.

| What | Where |
|---|---|
| Authorization server, from the host | \`https://127.0.0.1:${AS_PORT_HTTPS:-8443}/\` (certificate issued for \`${AS_HOSTNAME}\`) |
| Admin console | \`https://127.0.0.1:${AS_PORT_HTTPS:-8443}/admin/\` |
| Issuer, as IRIS discovered it | \`https://${AS_HOSTNAME}:${AS_PORT_HTTPS_INTERNAL:-8443}/realms/${AS_REALM}\` |
| The SUT over TLS, which is what the module addresses | \`$(sut_base_url_tls stufe5)\` |
| Token audience | \`${SUT_AUDIENCE}\` |
| CA, certificates and fingerprints | \`run/tls/TLS.txt\` |

The browser will not trust that certificate: it is issued by this round's own
local CA (\`run/tls/ca.crt\`), because the isolated test subnet has no publicly
resolvable name a public certificate could be issued for.

### The access tokens

The module's feature files send their own \`Authorization: Bearer\` header, taking
the token from the suite's test data - which ships the six values as
\`SET-YOUR-TOKEN-HERE\` for the operator to fill in. \`make tokens\` does that:

\`\`\`bash
make tokens                            # mint six tokens and stage them
scripts/55-mint-tokens.sh --verify     # check what is staged, change nothing
make test-connect                      # run the module straight afterwards
\`\`\`

A token lives ${AS_TOKEN_LIFESPAN:-3600} seconds. \`make test-connect\` refuses to
start with less than ten minutes left on any of them, because an expired token
presents as a total authorization failure in the system under test rather than
as a stale credential. Note the order: \`make up\` re-renders the configuration
and therefore re-stages the test data from the official contract, restoring the
placeholders, so it is always \`make up\` first, then \`make tokens\`.

| Where the tokens are | What it holds |
|---|---|
| \`run/testdata/Stufe5/connect.yaml\` | the six token values, staged for the suite |
| \`run/tiger/TOKENS.txt\` | which token was minted for which scope, by SHA-256 only |
| each round's \`debug-report.zip\` | the recorded traffic, which contains the headers |

Unlike every other credential in this document, an access token is **not** to be
reproduced in a report or a document: it is a bearer credential, and the first
two rows above are the only records a round keeps. See
\`scripts/70-collect-report.sh\`.

EOF
  fi

  cat <<EOF
## Where else these values appear

Every one of them on purpose:

| Where | What it holds |
|---|---|
| \`make credentials\` | this sheet, or the live values if \`run/\` was cleaned |
| \`make status\` | both pairs, alongside the observed environment |
| \`.env.resolved\` | the round's frozen configuration, mode 0600 |
| \`run/tiger/*.yaml\` | \`${SUT_PASSWORD}\` as the suite's configured credential |
| \`run/tiger/RENDERED.txt\` | every substituted value, for reproducing the rendering |
| \`run/webgateway/CSP.ini\` | \`${IRIS_MGMT_PASSWORD}\` as the gateway's service credential |
| \`analysis/results.md\` §1 | the *Access credentials* table, in each archived report |
| \`GAP-ANALYSIS.md\` | verbatim \`Authorization: Basic\` headers in the quoted exchanges |
| \`<variant>/run.json\` | the \`credentials\` block the results analysis reads |

Only one rule applies: none of this may reach source control. \`secrets/\`,
\`.env\`, \`.env.resolved\`, \`run/\` and \`reports/\` are all gitignored, and
\`make check\` verifies that mechanically.
EOF
} >"$CRED_FILE"

chmod 0600 "$CRED_FILE"
ok "wrote run/CREDENTIALS.md (${SUT_USER}:${SUT_PASSWORD}, ${IRIS_MGMT_USER}:${IRIS_MGMT_PASSWORD})"

step "Test tool configuration ready"
