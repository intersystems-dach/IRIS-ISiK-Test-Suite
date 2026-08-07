#!/usr/bin/env bash
# =============================================================================
# 30-render-config.sh - render the deploy-time configuration files
# =============================================================================
# Produces everything the test suite container reads, everything the Web Gateway
# needs in order to serve the SUT's FHIR endpoints at all, and the credential sheet
# for the tester - from templates plus the resolved deployment configuration:
#
#   run/tiger/tiger-isik-stufe3.yaml          unattended run  (runTestsOnStart: true,
#   run/tiger/tiger-isik-stufe5.yaml                           activateWorkflowUi: false)
#   run/tiger/manual/tiger-isik-stufe3.yaml   MODE=manual     (runTestsOnStart: false,
#   run/tiger/manual/tiger-isik-stufe5.yaml                     activateWorkflowUi: true)
#   run/webgateway/CSP.ini                    the gateway's IRIS server connection
#   run/webgateway/isik-vhost.conf            Apache: which paths mod_csp claims
#   run/testdata/Stufe{3,5}/*.yaml            the suite's logical-key contract
#   run/tiger/RENDERED.txt                    what was rendered, from what, when
#   run/tiger/TESTDATA-PARITY.txt             our contract files vs the release's
#   run/CREDENTIALS.md                        the credential sheet for the tester
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
require_var IRIS_IP FHIR_BASE_STUFE3 FHIR_BASE_STUFE5 SUT_USER SUT_PASSWORD \
            IRIS_MGMT_USER IRIS_MGMT_PASSWORD WEBGATEWAY_IP

TIGER_TMPL_DIR="${REPO_ROOT}/tiger"
WEBGATEWAY_TMPL_DIR="${REPO_ROOT}/webgateway"
TESTDATA_SRC="${REPO_ROOT}/testdata"
OUT_WEBGATEWAY="${RUN_DIR}/webgateway"
OUT_TIGER="${RUN_DIR}/tiger"
OUT_TIGER_MANUAL="${OUT_TIGER}/manual"
OUT_TESTDATA="${RUN_DIR}/testdata"

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
import re
import sys

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

unknown = sorted(set(re.findall(r"@@([A-Z0-9_]+)@@", text)) - set(SUBST))
if unknown:
    sys.exit(f"30-render-config.sh: {tmpl_path}: unknown placeholder(s): {', '.join(unknown)}")

for key, value in SUBST.items():
    text = text.replace(f"@@{key}@@", value)

# Belt and braces: nothing of the form @@...@@ may survive into a file the test
# suite reads. A leftover would be interpreted as a literal URL or credential.
left = re.findall(r"@@[^@]*@@", text)
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

for stufe in stufe3 stufe5; do
  tmpl="${TIGER_TMPL_DIR}/tiger-isik-${stufe}.yaml.tmpl"
  [[ -f "$tmpl" ]] || die "template missing: ${tmpl#$REPO_ROOT/}"

  base_url="$(sut_base_url "$stufe")"

  # Unattended: the suite runs on container start and exits.
  render "$tmpl" "${OUT_TIGER}/tiger-isik-${stufe}.yaml" true "$base_url"
  # Manual: the container comes up, serves the WorkflowUI and waits.
  render "$tmpl" "${OUT_TIGER_MANUAL}/tiger-isik-${stufe}.yaml" false "$base_url"

  log "${stufe}: SUT is ${base_url}"
done
ok "rendered 4 Tiger configuration files (2 unattended, 2 manual)"

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
  python3 - "$tmpl" "$out" <<'PY'
import os
import re
import sys

tmpl_path, out_path = sys.argv[1], sys.argv[2]

SUBST = {
    "IRIS_IP":               os.environ["IRIS_IP"],
    "IRIS_SUPERSERVER_PORT": os.environ["IRIS_SUPERSERVER_PORT"],
    "GATEWAY_IRIS_USER":     os.environ["GATEWAY_IRIS_USER"],
    "GATEWAY_IRIS_PASSWORD": os.environ["GATEWAY_IRIS_PASSWORD"],
    "FHIR_BASE_STUFE3":      os.environ["FHIR_BASE_STUFE3"],
    "FHIR_BASE_STUFE5":      os.environ["FHIR_BASE_STUFE5"],
}

with open(tmpl_path, encoding="utf-8") as fh:
    text = fh.read()

unknown = sorted(set(re.findall(r"@@([A-Z0-9_]+)@@", text)) - set(SUBST))
if unknown:
    sys.exit(f"30-render-config.sh: {tmpl_path}: unknown placeholder(s): {', '.join(unknown)}")

for key, value in SUBST.items():
    text = text.replace(f"@@{key}@@", value)

left = re.findall(r"@@[^@]*@@", text)
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

for f in CSP.ini isik-vhost.conf; do
  tmpl="${WEBGATEWAY_TMPL_DIR}/${f}.tmpl"
  [[ -f "$tmpl" ]] || die "template missing: ${tmpl#$REPO_ROOT/}"
  render_gateway "$tmpl" "${OUT_WEBGATEWAY}/${f}"
done

# CSP.ini carries the gateway's IRIS credential. Same treatment as the rendered
# Tiger configuration: the value is documented in the reports by design, but the
# live file mimics production. The vhost config holds no secret.
chmod 0600 "${OUT_WEBGATEWAY}/CSP.ini"
chmod 0644 "${OUT_WEBGATEWAY}/isik-vhost.conf"
ok "rendered the Web Gateway configuration (IRIS at ${IRIS_IP}:1972 as ${GATEWAY_IRIS_USER})"

# -----------------------------------------------------------------------------
# 2. Stage the test-data contract
# -----------------------------------------------------------------------------
# These YAML files are the suite's own contract: every key is referenced from a
# feature file as ${data.<key>} and its value is the resource id that must exist
# in the SUT. The 11 files that exist upstream are carried over byte-identically -
# step 4 below proves it rather than asserting it - because a changed value would
# mean our data conforms to us rather than to the official configuration. The two
# zz-upstream-key-defects.yaml files are ours; see docs/en/05-testdata.md.
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
done < <(python3 - "${OUT_TIGER}/tiger-isik-stufe3.yaml" "${OUT_TIGER}/tiger-isik-stufe5.yaml" <<'PY'
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
  die "test-data contract files listed in the Tiger configuration but absent from testdata/: ${missing}"
fi

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
# Verified against gematik/isik-testsuite 2.7.0 by grepping all 220 feature files:
# no feature references this key. Both places the Appointment specialty system is
# actually needed spell it out inline -
#   Stufe5/Terminplanung/Appointment-Read.feature:48
#   Stufe5/Terminplanung/Appointment-Search.feature:63,65
# both as the literal http://ihe-d.de/CodeSystems/AerztlicheFachrichtungen - so the
# key is an unused leftover and no request URL can inherit its emptiness.
UPSTREAM_EMPTY_OK = {
    "Stufe5/terminplanung.yaml:appointment-read-specialty-system":
        "empty upstream; referenced by no feature file in 2.7.0 (the two "
        "Appointment features hardcode the system inline)",
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
# 3. Prove upstream parity of the contract files
# -----------------------------------------------------------------------------
# The claim "our test-data contract is the official one" underpins the
# Bestaetigungsverfahren submission, so it is checked mechanically against the
# copy 20-fetch-packages.sh fetched from the pinned release tag - not against a
# comment. (Not from the image: the image carries no /app/testdata at all. See
# fetch_upstream_sources() in 20-fetch-packages.sh.) Three outcomes:
#
#   IDENTICAL  same bytes as the released file. Expected for all 11.
#   MODIFIED   same path, different bytes. Fatal: someone edited a value, and the
#              run would test against a contract that is not the official one.
#   ADDED      no counterpart upstream. Expected only for the two
#              zz-upstream-key-defects.yaml overlays, which are ours by design;
#              anything else is fatal.
#
# When vendor/upstream/testdata is absent (someone ran this before `make packages`)
# the check is skipped with a warning rather than failing: the staged files are
# still valid, but the evidence record must say the parity was not verified.
UPSTREAM_TESTDATA="${VENDOR_DIR}/upstream/testdata"
PARITY_FILE="${OUT_TIGER}/TESTDATA-PARITY.txt"

# Files that legitimately have no upstream counterpart. Anything else that shows
# up as ADDED is an accident and must stop the round.
ALLOWED_ADDED='Stufe3/zz-upstream-key-defects.yaml Stufe5/zz-upstream-key-defects.yaml'

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
    printf '#\n'
    printf '# %-10s %-46s %s\n' 'STATUS' 'FILE' 'SHA256'
    while IFS= read -r f; do
      rel="${f#$OUT_TESTDATA/}"
      up="${UPSTREAM_TESTDATA}/${rel}"
      if [[ ! -f "$up" ]]; then
        printf '  %-10s %-46s %s\n' 'ADDED' "$rel" "$(sha256_of "$f")"
        case " $ALLOWED_ADDED " in
          *" $rel "*) ;;
          *) added="${added}${added:+, }${rel}" ;;
        esac
      elif cmp -s "$f" "$up"; then
        printf '  %-10s %-46s %s\n' 'IDENTICAL' "$rel" "$(sha256_of "$f")"
        identical=$((identical+1))
      else
        printf '  %-10s %-46s %s\n' 'MODIFIED' "$rel" "$(sha256_of "$f")"
        modified="${modified}${modified:+, }${rel}"
      fi
    done < <(find "$OUT_TESTDATA" -name '*.yaml' | sort)
  } >"$PARITY_FILE"

  if [[ -n "$modified" ]]; then
    die "test-data contract files differ from the official ${TESTSUITE_REPO:-gematik/isik-testsuite}@${TESTSUITE_VERSION:-?} release: ${modified}
  These files must stay byte-identical to the official release: a changed value
  means the run tests against a contract that is not the certified one. To add
  keys, put them in testdata/Stufe{3,5}/zz-upstream-key-defects.yaml instead.
  Diff with: diff -u vendor/upstream/testdata/<file> testdata/<file>"
  fi
  if [[ -n "$added" ]]; then
    die "test-data files with no upstream counterpart: ${added}
  Only the zz-upstream-key-defects.yaml overlays may be added. Remove the file,
  or fold its keys into the overlay and document them there."
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
  printf '# Substituted values, in full. SUT_PASSWORD is generated per deployment\n'
  printf '# and kept in secrets/sut.password; see CREDENTIALS.md beside this file.\n'
  printf '#\n'
  printf '  SUT_BASE_URL (stufe3)  %s\n' "$(sut_base_url stufe3)"
  printf '  SUT_BASE_URL (stufe5)  %s\n' "$(sut_base_url stufe5)"
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
  printf '# Whether the staged test-data contract matches the official release:\n'
  printf '# see TESTDATA-PARITY.txt beside this file.\n'
  printf '\n'
  printf '# %-50s %s\n' 'FILE' 'SHA256'
  for f in "${TIGER_TMPL_DIR}"/*.tmpl "${WEBGATEWAY_TMPL_DIR}"/*.tmpl; do
    printf '  %-50s %s\n' "$(basename "$(dirname "$f")")/$(basename "$f")" "$(sha256_of "$f")"
  done
  for f in "${OUT_TIGER}"/*.yaml "${OUT_TIGER_MANUAL}"/*.yaml \
           "${OUT_WEBGATEWAY}/CSP.ini" "${OUT_WEBGATEWAY}/isik-vhost.conf"; do
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
SUT_BASE_NET3="$(sut_base_url stufe3)"
SUT_BASE_NET5="$(sut_base_url stufe5)"

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

| Level | From the host | From inside the subnet (what the suite uses) |
|---|---|---|
| Stufe 3 | ${SUT_BASE_HOST3} | ${SUT_BASE_NET3} |
| Stufe 5 | ${SUT_BASE_HOST5} | ${SUT_BASE_NET5} |

Both columns address the **Web Gateway**, not IRIS. The IRIS for Health images ship
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
| \`<stufe>/run.json\` | the \`credentials\` block the results analysis reads |

Only one rule applies: none of this may reach source control. \`secrets/\`,
\`.env\`, \`.env.resolved\`, \`run/\` and \`reports/\` are all gitignored, and
\`make check\` verifies that mechanically.
EOF
} >"$CRED_FILE"

chmod 0600 "$CRED_FILE"
ok "wrote run/CREDENTIALS.md (${SUT_USER}:${SUT_PASSWORD}, ${IRIS_MGMT_USER}:${IRIS_MGMT_PASSWORD})"

step "Test tool configuration ready"
