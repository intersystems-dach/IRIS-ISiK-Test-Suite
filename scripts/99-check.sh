#!/usr/bin/env bash
# =============================================================================
# 99-check.sh - static self-check of this repository
# =============================================================================
# `make check`. Verifies the repository is internally consistent WITHOUT starting
# anything, pulling anything or needing a licence key. Safe on any host with
# bash and python3; every check that needs a tool the host may lack is skipped
# with a notice rather than failed.
#
# Why this exists. The environment is deployed on a remote machine and one full
# round costs hours, most of it in the FHIR package import. A typo in a script or
# a placeholder that no longer matches its renderer is worth catching in two
# seconds rather than forty minutes in.
#
# What is checked:
#   1. shell syntax of every *.sh
#   2. python syntax of every *.py, INCLUDING blocks embedded in the shell
#      scripts - `bash -n` cannot see inside a heredoc, so a broken f-string
#      there would otherwise surface only at run time
#   3. every YAML file parses; the Tiger templates parse once rendered
#  3b. the authorization server's realm renders to valid JSON and defines exactly
#      the clients, scopes and launch contexts the Connect tokens are minted
#      from - Keycloak imports a malformed realm as an EMPTY one and starts
#      anyway, so this failure is otherwise invisible until the first token
#      request
#   4. the placeholders the templates use are exactly the ones the renderer
#      substitutes, in both directions
#   5. the test-data contract: no undefined ${<namespace>.<key>} references, no
#      empty values, and the requirement extractor runs clean over every feature
#      file of the pinned release
#  5b. the fabricated test data itself: it renders, it satisfies every assertion
#      the feature files make about it, it carries every reference a search
#      scenario filters on, and it can be ordered for seeding. These are the same
#      three checks `make seed` runs, repeated here because they need neither a
#      container nor a licence key
#   6. cross-file consistency: Makefile targets have scripts, compose mounts have
#      renderers, .env.example defines what the scripts read
#   7. `docker compose config` validates BOTH configurations - with and without
#      the authorization server overlay - if docker is available
#
# Exits non-zero if any check fails, so it is usable in CI.
# =============================================================================

# Deliberately not `set -e`: this script must run every check and report all
# failures at once. A run that stops at the first problem turns one fix-and-retry
# cycle into several, which on a remote host is exactly what this file exists to
# avoid.
set -uo pipefail

REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$REPO_ROOT" || exit 1

if [[ -t 1 ]]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_RED=$'\033[31m'
  C_GRN=$'\033[32m'; C_YEL=$'\033[33m'; C_DIM=$'\033[2m'
else
  C_RESET=''; C_BOLD=''; C_RED=''; C_GRN=''; C_YEL=''; C_DIM=''
fi

FAILURES=0
SKIPPED=0

section() { printf '\n%s=== %s ===%s\n' "$C_BOLD" "$*" "$C_RESET"; }
pass()    { printf '  %sPASS%s  %s\n' "$C_GRN" "$C_RESET" "$*"; }
fail()    { printf '  %sFAIL%s  %s\n' "$C_RED" "$C_RESET" "$*"; FAILURES=$((FAILURES+1)); }
skip()    { printf '  %sSKIP%s  %s\n' "$C_YEL" "$C_RESET" "$*"; SKIPPED=$((SKIPPED+1)); }
note()    { printf '  %s      %s%s\n' "$C_DIM" "$*" "$C_RESET"; }

# -----------------------------------------------------------------------------
# Which suite generation this working tree is checked against
# -----------------------------------------------------------------------------
# Sections 5 and 5b walk the levels, the test-data contract and the Tiger
# templates of ONE suite generation, and release 3.0.0 changed all three: ISiK
# Level 3 was removed from the suite, and the single `data` key namespace became
# one namespace per module. A check that assumed either shape would report the
# other one as broken, so the shape is resolved first, from the same
# TESTSUITE_VERSION a round would use.
#
# Precedence: an exported value, then .env, then .env.example. The last entry is
# what lets `make check` run on a fresh clone with no configuration at all, which
# is most of what this script exists for. Only the pinned version is read, not
# TESTSUITE_VERSION_TRACK=latest - resolving that needs the network, and this
# script must work without it; a tracked round that resolves to another
# generation is caught by 25-extract-requirements.sh instead.
env_file_var() {
  # Last assignment wins, as sourcing the file does. Surrounding quotes are
  # stripped because .env accepts them and the value is used as a version string.
  local v
  [[ -f "$1" ]] || return 1
  v="$(sed -n "s/^[[:space:]]*$2=//p" "$1" | tail -n1 | tr -d "\"'")"
  [[ -n "$v" ]] || return 1
  printf '%s' "$v"
}

SUITE_VERSION="${TESTSUITE_VERSION:-}"
SUITE_VERSION_FROM="the environment"
if [[ -z "$SUITE_VERSION" ]]; then
  if SUITE_VERSION="$(env_file_var .env TESTSUITE_VERSION)"; then
    SUITE_VERSION_FROM=".env"
  elif SUITE_VERSION="$(env_file_var .env.example TESTSUITE_VERSION)"; then
    SUITE_VERSION_FROM=".env.example"
  else
    SUITE_VERSION=""
  fi
fi

# Resolved through lib.sh in a subshell, never by a second copy of the mapping
# here: two copies would eventually disagree, and the symptom would be a check
# that passes against levels no round runs. A subshell because lib.sh sets -e,
# which this script deliberately does not (see the header), and because it
# assigns PACKAGES_DIR unconditionally, which section 5b lets the caller override.
SUITE_GENERATION=''
SUITE_LEVELS=''
SUITE_TESTDATA_ROOT=''
SUITE_TIGER_DIR=''
SUITE_FHIR_ROOTS=''
if [[ -z "$SUITE_VERSION" ]]; then
  fail "TESTSUITE_VERSION is set neither in the environment nor in .env nor in .env.example - the suite generation, and with it which ISiK levels exist, cannot be resolved"
elif ! shape="$(TESTSUITE_VERSION="$SUITE_VERSION" REPO_ROOT="$REPO_ROOT" \
                bash -c '. scripts/lib.sh
                         printf "%s\n%s\n%s\n%s\n%s\n" "$(suite_generation)" \
                           "$(suite_levels)" "$(suite_testdata_root)" \
                           "$(suite_tiger_dir)" "$(suite_fhir_roots)"' 2>&1)"; then
  fail "TESTSUITE_VERSION=${SUITE_VERSION} (from ${SUITE_VERSION_FROM}) does not resolve to a suite generation:"
  printf '%s\n' "$shape" | sed 's/^/        /' | head -n 12
else
  { read -r SUITE_GENERATION
    read -r SUITE_LEVELS
    read -r SUITE_TESTDATA_ROOT
    read -r SUITE_TIGER_DIR
    read -r SUITE_FHIR_ROOTS; } <<< "$shape"
fi
# Repo-relative, for the messages: an absolute path to a clone is noise in a
# failure a reader has to act on.
SUITE_TESTDATA_REL="${SUITE_TESTDATA_ROOT#$REPO_ROOT/}"
SUITE_TIGER_REL="${SUITE_TIGER_DIR#$REPO_ROOT/}"

# -----------------------------------------------------------------------------
section "1. Shell syntax"
# -----------------------------------------------------------------------------
n=0
while IFS= read -r f; do
  n=$((n+1))
  if err="$(bash -n "$f" 2>&1)"; then
    :
  else
    fail "${f}: ${err}"
  fi
done < <(find scripts iris -name '*.sh' -type f 2>/dev/null | sort)
(( n > 0 )) && pass "${n} shell scripts parse"

# Executable bits: a script the Makefile invokes directly must be executable, or
# make fails with a bare "Permission denied" that names no cause.
while IFS= read -r f; do
  [[ -x "$f" ]] || fail "${f} is not executable (chmod +x)"
done < <(find scripts iris -name '*.sh' -type f 2>/dev/null | sort)

if command -v shellcheck >/dev/null 2>&1; then
  if shellcheck -S warning scripts/*.sh iris/bootstrap/*.sh >/dev/null 2>&1; then
    pass "shellcheck clean at -S warning"
  else
    # Advisory: shellcheck findings are worth seeing but are not all defects, and
    # this repository must not become unbuildable because of a style preference.
    printf '  %sWARN%s  shellcheck reported findings:\n' "$C_YEL" "$C_RESET"
    shellcheck -S warning scripts/*.sh iris/bootstrap/*.sh 2>&1 | sed 's/^/        /' | head -n 40
  fi
else
  skip "shellcheck not installed - style/quoting issues not checked"
fi

# -----------------------------------------------------------------------------
section "2. Python syntax"
# -----------------------------------------------------------------------------
n=0
while IFS= read -r f; do
  n=$((n+1))
  if err="$(python3 -m py_compile "$f" 2>&1)"; then
    :
  else
    fail "${f}: ${err}"
  fi
done < <(find scripts gap-analysis -name '*.py' -type f 2>/dev/null | sort)
(( n > 0 )) && pass "${n} python files compile" || skip "no .py files yet"
find . -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

# Python embedded in the shell scripts. `bash -n` validates the shell around a
# heredoc but never looks inside it, so this is the only thing standing between a
# broken f-string and a failure forty minutes into a run.
python3 - <<'PY'
import re, pathlib, sys

fails, checked = 0, 0
for p in sorted(pathlib.Path("scripts").rglob("*.sh")) + sorted(pathlib.Path("iris").rglob("*.sh")):
    text = p.read_text(encoding="utf-8")
    blocks = []
    # Quoted heredoc delimiter only: an unquoted one is shell-interpolated before
    # python sees it, so its pre-expansion form is not valid python by design.
    for m in re.finditer(r"python3[^\n]*<<'([A-Za-z_]+)'\n(.*?)\n\1\b", text, re.S):
        blocks.append((m.group(2), text[:m.start()].count("\n") + 1))
    for m in re.finditer(r"python3 -c '((?:[^']|'\")*?)'\s", text, re.S):
        blocks.append((m.group(1), text[:m.start()].count("\n") + 1))
    for src, line in blocks:
        checked += 1
        try:
            compile(src, f"{p}:{line}", "exec")
        except SyntaxError as e:
            fails += 1
            print(f"  FAIL  {p}:{line} embedded python: {e.msg} (block line {e.lineno})")
if fails:
    sys.exit(1)
print(f"  PASS  {checked} embedded python blocks compile")
PY
(( $? == 0 )) || FAILURES=$((FAILURES+1))

# -----------------------------------------------------------------------------
section "2b. ObjectScript"
# -----------------------------------------------------------------------------
# There is no ObjectScript compiler on the host, so this is not a syntax check. It
# is a check for the one class of defect that has actually cost a deployment here:
# a preprocessor macro invocation split across lines.
#
# ObjectScript continues an ordinary call across lines happily, so the construct
# looks correct and reads correctly. $$$ERROR is not an ordinary call: the
# preprocessor expands it before the compiler sees the line, and it does not honour
# the continuation, so the class fails to load with "MPP5612: Referenced macro
# missing right paren" followed by a cascade of consequent errors. This surfaced
# only on the deployment host, after the licence check, forty minutes into `make up`
# - which is exactly the kind of failure the static checks exist to move forward.
#
# The heuristic is deliberately crude - a line containing a macro with more '(' than
# ')' - because that is precisely the shape of the defect. Build the message into a
# variable and pass it to the macro on one line instead.
# The second defect class is the mirror image of the first, and cost a bootstrap
# cycle on 2026-11-16: a formal specification naming a class the COMPILING
# namespace cannot see. These classes are loaded into the FHIR namespace, where
# the system packages (Security, Config, SYS) are not mapped, so
#
#     ClassMethod M(app As Security.Applications)
#
# fails to load with "ERROR #5373: Class 'Security.Applications' ... does not
# exist", even though the method body may legitimately reach that class after
# `set $namespace = "%SYS"`. A `#dim` of the same type is harmless - the compiler
# treats it as a hint - which is what makes the error look arbitrary. Type the
# parameter %RegisteredObject and let the property access bind at run time.
python3 - <<'PY'
import pathlib, re, sys

# Packages that exist only in %SYS. Not an exhaustive list of system packages -
# a list of the ones these classes actually configure.
SYSTEM_PKGS = ("Security", "Config", "SYS")
SIGNATURE = re.compile(r"^\s*(?:Class)?Method\s+([A-Za-z0-9]+)\s*\((.*)\)\s*(?:As\s|\{|$)")
TYPED = re.compile(r"\bAs\s+((?:%s)\.[A-Za-z0-9.]+)" % "|".join(SYSTEM_PKGS))

fails, checked = 0, 0
for p in sorted(pathlib.Path("iris/src").rglob("*.cls")):
    checked += 1
    for i, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        stripped = ln.strip()
        # Comment lines are not preprocessed, and this file's own explanatory
        # comments name the construct they warn about.
        if stripped.startswith("//") or stripped.startswith("///") or stripped.startswith("#;"):
            continue
        if "$$$" in ln and ln.count("(") > ln.count(")"):
            fails += 1
            print(f"  FAIL  {p}:{i}: macro invocation split across lines - "
                  f"the preprocessor will reject it (MPP5612)")
            print(f"        {stripped}")
        sig = SIGNATURE.match(ln)
        if sig:
            # Only the formal specification, not the return type: a return type is
            # resolved the same way, but none of these methods returns one.
            for cls in TYPED.findall(sig.group(2)):
                fails += 1
                print(f"  FAIL  {p}:{i}: {sig.group(1)}() declares a parameter "
                      f"As {cls}, which the FHIR namespace cannot resolve at "
                      f"compile time (#5373)")
                print(f"        type it %RegisteredObject - the body already "
                      f"switches to %SYS")
if fails:
    sys.exit(1)
noun, verb = ("class", "carries") if checked == 1 else ("classes", "carry")
print(f"  PASS  {checked} ObjectScript {noun} {verb} no line-split macro "
      f"invocation and no unresolvable formal specification")
PY
(( $? == 0 )) || FAILURES=$((FAILURES+1))

# -----------------------------------------------------------------------------
section "3. YAML"
# -----------------------------------------------------------------------------
if ! python3 -c 'import yaml' 2>/dev/null; then
  skip "PyYAML not installed - YAML not parsed (pip install pyyaml)"
else
  python3 - <<'PY'
import pathlib, sys
import yaml

fails = 0
paths = []
for pat in ("testdata/**/*.yaml", "compose/*.yml", "docker-compose.yml"):
    paths += sorted(pathlib.Path(".").glob(pat))

for p in paths:
    try:
        # Compose files use ${VAR:?msg} interpolation, which is valid YAML but
        # whose *meaning* compose supplies; parsing only checks the syntax here,
        # and `docker compose config` in section 7 checks the semantics.
        yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception as exc:
        fails += 1
        print(f"  FAIL  {p}: {exc}")

# The Tiger files are templates and are not valid YAML until rendered: an
# unquoted @@SUT_BASE_URL@@ as a list item is fine, but the renderer's job is to
# guarantee the *result* parses, so that is what is checked.
#
# The values stand in for the real ones; only the *shape* of the result is being
# checked here. The one place a value matters is asserted in section 4, where
# activateWorkflowUi's polarity is checked against render()'s derivation - false
# for an unattended run, since a true there aborts the run before any scenario.
SUBST = {
    "SUT_BASE_URL": "http://172.31.0.10:52773/isik5/fhir/r4",
    "SUT_USER": "isikapi",
    "SUT_PASSWORD": "pw",
    "WORKFLOW_UI_PORT": "9010",
    "PROXY_ADMIN_PORT": "9011",
    "RUN_TESTS_ON_START": "true",
    "ACTIVATE_WORKFLOW_UI": "false",
}
for p in sorted(pathlib.Path("tiger").rglob("*.tmpl")):
    text = p.read_text(encoding="utf-8")
    for k, v in SUBST.items():
        text = text.replace(f"@@{k}@@", v)
    try:
        doc = yaml.safe_load(text)
    except Exception as exc:
        fails += 1
        print(f"  FAIL  {p} (rendered): {exc}")
        continue
    for key in ("servers", "tigerProxy", "lib", "additionalConfigurationFiles"):
        if key not in doc:
            fails += 1
            print(f"  FAIL  {p}: rendered configuration has no '{key}' section")
    # Rendered as a YAML boolean, not the string "false" - Tiger reads this through
    # Spring's relaxed binding, but a quoted or mistyped value would be a silent
    # fallback to the default (true), which is the failure mode being guarded.
    if doc.get("lib", {}).get("activateWorkflowUi") is not False:
        fails += 1
        print(f"  FAIL  {p}: lib.activateWorkflowUi renders as "
              f"{doc.get('lib', {}).get('activateWorkflowUi')!r}, not the boolean false")

if fails:
    sys.exit(1)
print(f"  PASS  {len(paths)} YAML files parse; "
      f"{len(list(pathlib.Path('tiger').rglob('*.tmpl')))} Tiger templates render to valid YAML")
PY
  (( $? == 0 )) || FAILURES=$((FAILURES+1))
fi

# -----------------------------------------------------------------------------
section "3b. Authorization server realm"
# -----------------------------------------------------------------------------
# The realm is a template like the Tiger configurations, and like them it is only
# valid once rendered - `"accessTokenLifespan": @@AS_TOKEN_LIFESPAN@@` is not JSON
# until the number is in place. The renderer performs this check too, but at
# render time, which in a Connect round is thirty minutes before the import
# would fail; and the two invariants below are properties of the repository
# rather than of a round, so they are worth asserting without one.
#
# What makes a drift here expensive: Keycloak imports an invalid or incomplete
# realm as an EMPTY realm and starts anyway, so the symptom is every token
# request failing with `invalid_client` - which reads as a credential problem,
# not as a malformed file.
if [[ ! -f keycloak/realm-isik.json.tmpl ]]; then
  fail "keycloak/realm-isik.json.tmpl is missing - compose/authserver.yml mounts the file rendered from it"
elif ! python3 -c 'import yaml' 2>/dev/null; then
  skip "PyYAML not installed - the realm's test-data cross-check needs it"
else
  CONNECT_TOKEN_MAP="$(REPO_ROOT="$REPO_ROOT" bash -c '. scripts/lib.sh; connect_token_clients' 2>&1)" \
  CONNECT_CONTRACT_FILE="${SUITE_TESTDATA_ROOT}/Stufe5/connect.yaml" \
  python3 - <<'PY'
import json, os, pathlib, re, sys

import yaml

text = pathlib.Path("keycloak/realm-isik.json.tmpl").read_text(encoding="utf-8")

# Stand-ins only; the shape of the result is what is being checked. The lifespan
# is a bare number in the template, so a non-numeric stand-in here would fail the
# parse for the wrong reason.
SUBST = {
    "AS_REALM": "isik",
    "AS_TOKEN_LIFESPAN": "3600",
    "AS_CLIENT_SECRET": "x",
    "AS_RESOURCE_CLIENT_ID": "isik-resource-server",
    "SUT_AUDIENCE": "https://isik-webgateway/isik5/fhir/r4",
    "CONNECT_PATIENT_IN_CONTEXT": "Patient-Read-Example",
}
for k, v in SUBST.items():
    text = text.replace(f"@@{k}@@", v)

left = sorted(set(re.findall(r"@@([A-Z0-9_]+)@@", text)))
if left:
    print(f"  FAIL  keycloak/realm-isik.json.tmpl: placeholder(s) this check does not "
          f"know: {', '.join(left)} - add them to render_realm() and here")
    sys.exit(1)

try:
    realm = json.loads(text)
except Exception as exc:
    print(f"  FAIL  keycloak/realm-isik.json.tmpl does not render to valid JSON: {exc}")
    sys.exit(1)

fails = 0

# 1. One client per token the mint step asks for, each holding exactly the one
#    scope its token is under test for. A client holding two scopes would make a
#    refusal scenario pass for the wrong reason, which no test output reveals.
expected = {}
for line in os.environ["CONNECT_TOKEN_MAP"].splitlines():
    if line.strip():
        _key, client, scope = line.split("|")
        expected[client] = scope

clients = {c.get("clientId"): c for c in realm.get("clients") or []}
declared_scopes = {s.get("name") for s in realm.get("clientScopes") or []}
for client, scope in sorted(expected.items()):
    if client not in clients:
        print(f"  FAIL  realm defines no client '{client}', which connect_token_clients() "
              f"mints the {scope} token from")
        fails += 1
        continue
    have = clients[client].get("defaultClientScopes") or []
    if have != [scope]:
        print(f"  FAIL  realm client '{client}' has default scopes {have}, "
              f"expected exactly ['{scope}']")
        fails += 1
    if scope not in declared_scopes:
        print(f"  FAIL  realm client '{client}' uses scope '{scope}', which clientScopes "
              f"does not declare")
        fails += 1

# 2. The launch context, and only where it belongs. A `patient/...` scope is
#    meaningless without a patient in context, and a `user/...` or `system/...`
#    token that carried one would silently satisfy the scenarios that exist to
#    show the difference.
def patient_claim(client):
    for m in client.get("protocolMappers") or []:
        if (m.get("config") or {}).get("claim.name") == "patient":
            return (m["config"] or {}).get("claim.value")
    return None

for client, scope in sorted(expected.items()):
    c = clients.get(client)
    if c is None:
        continue
    claim = patient_claim(c)
    if scope.startswith("patient/") and not claim:
        print(f"  FAIL  realm client '{client}' holds {scope} but hardcodes no `patient` "
              f"claim - the scenarios in patient context have no patient")
        fails += 1
    if not scope.startswith("patient/") and claim:
        print(f"  FAIL  realm client '{client}' holds {scope} but hardcodes a `patient` "
              f"claim ({claim!r}) - only a patient-context token may carry one")
        fails += 1

# 3. The six keys are the SUITE's, not ours. The mapping names the test-data keys
#    the mint step writes into, so a key the suite's own contract does not define
#    would be written to a file nothing reads - and the scenario would then send
#    the literal `${connect.<key>}` as its bearer token.
contract = pathlib.Path(os.environ["CONNECT_CONTRACT_FILE"])
if contract.is_file():
    data = yaml.safe_load(contract.read_text(encoding="utf-8")) or {}
    for line in os.environ["CONNECT_TOKEN_MAP"].splitlines():
        if not line.strip():
            continue
        key, _client, scope = line.split("|")
        if key not in data:
            print(f"  FAIL  connect_token_clients() names test-data key '{key}' "
                  f"({scope}) but {contract} does not define it")
            fails += 1

if fails:
    sys.exit(1)
print(f"  PASS  realm renders to valid JSON: {len(clients)} clients, "
      f"{len(declared_scopes)} scopes, {len(expected)} token holders matching "
      f"connect_token_clients()"
      + ("" if contract.is_file() else f" (no {contract} in this generation)"))
PY
  (( $? == 0 )) || FAILURES=$((FAILURES+1))
fi

# -----------------------------------------------------------------------------
section "4. Template placeholders"
# -----------------------------------------------------------------------------
# Both directions matter. A placeholder the renderer does not know would reach the
# container verbatim; a substitution the templates never use is dead configuration
# that suggests a rename went half-finished.
#
# Checked per template family, not against a pooled set of every placeholder the
# renderer knows. There are three render functions with deliberately disjoint
# substitution tables - render() for Tiger's YAML, render_gateway() for the Web
# Gateway's INI and Apache config, render_realm() for the authorization server's
# realm - and pooling them would accept a Tiger placeholder in an Apache file, or
# the realm's client secret in either, which is exactly the mix-up the separation
# exists to prevent.
python3 - <<'PY'
import pathlib, re, sys

renderer = pathlib.Path("scripts/30-render-config.sh").read_text(encoding="utf-8")

FUNCS = ("render", "render_gateway", "render_realm")

# Each render function's SUBST dict is the authority for what that family can
# substitute. Split on the function definitions so the tables stay apart; the
# order of the definitions in the file is what defines the boundary.
bounds = [(m.group(1), m.start())
          for m in re.finditer(r"^(%s)\(\) \{" % "|".join(FUNCS), renderer, re.M)]
if len(bounds) != len(FUNCS):
    print(f"  FAIL  expected {', '.join(f'{f}()' for f in FUNCS)} in "
          f"scripts/30-render-config.sh, found {len(bounds)}")
    sys.exit(1)

# The keys of the SUBST literal itself, not only the ones read from the
# environment: one realm value is taken from the staged test data rather than
# from a setting, and a table read as if it were environment-only would report
# that placeholder as unsubstituted.
tables = {}
for i, (name, start) in enumerate(bounds):
    end = bounds[i + 1][1] if i + 1 < len(bounds) else len(renderer)
    block = re.search(r"^SUBST = \{\n(.*?)^\}", renderer[start:end], re.M | re.S)
    if not block:
        print(f"  FAIL  could not find the SUBST table inside {name}() in scripts/30-render-config.sh")
        sys.exit(1)
    tables[name] = set(re.findall(r'^\s*"([A-Z0-9_]+)":', block.group(1), re.M))
    if not tables[name]:
        print(f"  FAIL  the SUBST table inside {name}() in scripts/30-render-config.sh is empty")
        sys.exit(1)

# Which template directory each function renders.
FAMILIES = [("tiger", "render"), ("webgateway", "render_gateway"),
            ("keycloak", "render_realm")]

fails = 0
total = 0
for directory, func in FAMILIES:
    known = tables[func]
    used = set()
    # Recursive: the 3.x templates live in a per-generation subdirectory
    # (tiger/v3/), and a template no check ever reads is a template whose
    # placeholders can drift away from the renderer unnoticed.
    templates = sorted(pathlib.Path(directory).rglob("*.tmpl"))
    if not templates:
        print(f"  FAIL  {directory}/ contains no *.tmpl files but {func}() renders from it")
        fails += 1
        continue
    for p in templates:
        for line in p.read_text(encoding="utf-8").splitlines():
            # Comment lines are skipped: the templates' own headers explain the
            # convention by writing @@PLACEHOLDER@@ literally, which is
            # documentation rather than a substitution the renderer must know.
            # Both '#' (INI, Apache, YAML) and no other comment syntax occur here.
            if line.lstrip().startswith("#"):
                continue
            used |= set(re.findall(r"@@([A-Z0-9_]+)@@", line))

    for name in sorted(used - known):
        print(f"  FAIL  {directory}/*.tmpl use @@{name}@@ but {func}() does not substitute it")
        fails += 1
    for name in sorted(known - used):
        print(f"  FAIL  {func}() substitutes {name} but no {directory}/*.tmpl uses @@{name}@@")
        fails += 1
    total += len(used)

if fails:
    sys.exit(1)
print(f"  PASS  {total} placeholders across {len(FAMILIES)} template families, "
      f"renderers and templates agree")
PY
(( $? == 0 )) || FAILURES=$((FAILURES+1))

# The one placeholder whose *value* matters as much as its presence.
# activateWorkflowUi must be false for an unattended run: with it true, Tiger waits
# 120 s for a browser to fetch status from the WorkflowUI and, when none does,
# aborts the run before executing a single scenario - two minutes, `Tests run: 0`,
# and a test-report.zip holding only pom.xml. It is a one-word regression with no
# other symptom, so the derivation is asserted rather than trusted: the templates
# must take the value from a placeholder (not hardcode it), and render() must
# compute it as the negation of runTestsOnStart.
python3 - <<'PY'
import pathlib, re, sys

renderer = pathlib.Path("scripts/30-render-config.sh").read_text(encoding="utf-8")
fails = 0

for p in sorted(pathlib.Path("tiger").rglob("*.tmpl")):
    body = [ln for ln in p.read_text(encoding="utf-8").splitlines()
            if not ln.lstrip().startswith("#")]
    hits = [ln for ln in body if "activateWorkflowUi" in ln]
    if not hits:
        print(f"  FAIL  {p} sets no activateWorkflowUi - the default is true, which aborts an unattended run")
        fails += 1
        continue
    for ln in hits:
        if "@@ACTIVATE_WORKFLOW_UI@@" not in ln:
            print(f"  FAIL  {p} hardcodes activateWorkflowUi ({ln.strip()}); it must be rendered per variant")
            fails += 1

# The negation itself, in render(). Matched on the two branches rather than on an
# exact line, so reformatting the conditional does not fail the check while a
# reversed polarity still does.
if not re.search(r'run_tests"?\s*==\s*true\s*\]\]\s*&&\s*activate_ui=false\s*\|\|\s*activate_ui=true',
                 renderer):
    print("  FAIL  scripts/30-render-config.sh does not derive activate_ui as the negation of "
          "run_tests (unattended => activateWorkflowUi: false)")
    fails += 1

if fails:
    sys.exit(1)
print("  PASS  activateWorkflowUi is rendered per variant and false for an unattended run")
PY
(( $? == 0 )) || FAILURES=$((FAILURES+1))

# -----------------------------------------------------------------------------
section "5. Test-data contract"
# -----------------------------------------------------------------------------
# Every ${data.<key>} the feature files reference must resolve, and every defined
# key must have a value. An undefined key renders as the literal string
# "${data.<key>}" in a request URL, producing a 404 that reads as a missing
# resource in the SUT - the single most misleading failure this rig can produce.
#
# Needs the feature files, which `make packages` extracts from the pinned image.
#
# Which keys, and which levels, is a property of the pinned release: the checks
# below read the contract of ONE generation out of $(suite_testdata_root) and
# judge scope for the levels of $(suite_levels).
if [[ -n "$SUITE_GENERATION" ]]; then
  note "suite ${SUITE_VERSION} (${SUITE_GENERATION}, from ${SUITE_VERSION_FROM}): levels ${SUITE_LEVELS}, contract ${SUITE_TESTDATA_REL}/, templates ${SUITE_TIGER_REL}/"
fi
if [[ -z "$SUITE_GENERATION" ]]; then
  skip "suite generation unresolved - the test-data contract cannot be located"
elif [[ ! -d vendor/upstream/features ]]; then
  skip "vendor/upstream/features absent - undefined-key check needs 'make packages' first"
  # The value check does not need the features and is always worth doing.
  python3 - "$SUITE_TESTDATA_ROOT" $SUITE_LEVELS <<'PY'
import pathlib, sys
import yaml

root = pathlib.Path(sys.argv[1])
# The levels of the pinned release, not every directory present: both generations
# are kept in the tree, and a file from the other one is not part of this round's
# contract.
levels = sys.argv[2:]

fails, total, upstream_empty = 0, 0, []
for p in sorted(q for lv in levels for q in (root / lv).glob("*.yaml")):
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        print(f"  FAIL  {p}: expected a flat key -> value mapping")
        sys.exit(1)
    ours = p.name.startswith("zz-")
    for k, v in data.items():
        total += 1
        if v is None or not str(v).strip():
            # An empty value in one of OUR overlay files is a defect: the only
            # reason those files exist is to supply a value. In an upstream
            # contract file it is upstream's business, and those files are held
            # byte-identical to the image by 30-render-config.sh, so "fixing" one
            # here would break the parity check instead.
            if ours:
                print(f"  FAIL  {p}:{k} has no value")
                fails += 1
            else:
                upstream_empty.append(f"{p.parent.name}/{p.name}:{k}")
if fails:
    sys.exit(1)
print(f"  PASS  {total} test-data keys, {len(upstream_empty)} empty upstream value(s)")
for e in upstream_empty:
    print(f"        empty upstream by design: {e}")
PY
  (( $? == 0 )) || FAILURES=$((FAILURES+1))
else
  python3 - "$SUITE_TESTDATA_ROOT" "$SUITE_TIGER_DIR" $SUITE_LEVELS <<'PY'
import os, pathlib, sys
import yaml

# The same Gherkin reader the requirement extractor uses, so "in scope" means the
# same thing in this check as it does in the generated documentation. Two
# implementations of tag scoping would eventually disagree, and the disagreement
# would show up as a check that passes while the documentation says otherwise.
sys.path.insert(0, "scripts/lib")
import feature_parser as fp
import extract_requirements as er

root = pathlib.Path(sys.argv[1])
tiger_dir = pathlib.Path(sys.argv[2])
levels = sys.argv[3:]

# A test datum is identified by its QUALIFIED name `<namespace>.<key>`, because
# that is what a feature file writes and because 3.0.9 defines three key names in
# several modules with DIFFERENT values per module - a bare key is therefore not
# an identity. The namespace a contract file's keys live in is the `baseKey` the
# Tiger configuration loads it under, and nothing derives it from the file name
# (dokumentenaustausch.yaml -> `dokument`), so it is read from the configuration
# templates by the same loader the extractor uses.
basekeys = er.load_basekeys(tiger_dir)

fails = 0
for stufe in levels:
    defined = {}
    in_scope = {}
    for p in sorted((root / stufe).glob("*.yaml")):
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        # The shape check the extractor's minimal reader deliberately leaves to
        # this script: a nested mapping would be silently misread there.
        if not isinstance(data, dict):
            print(f"  FAIL  {p}: expected a flat key -> value mapping")
            sys.exit(1)
        ns = basekeys.get(f"{stufe}/{p.name}")
        if ns is None:
            # Staged but never loaded: every key in it resolves to nothing, and a
            # scenario then sends the literal placeholder to the system under test.
            print(f"  FAIL  {p} is staged as test data but no Tiger template under "
                  f"{tiger_dir}/ loads it, so its key namespace is unknown")
            fails += 1
            continue
        for k, v in data.items():
            defined[f"{ns}.{k}"] = (p.name, v)
            # Only our own overlay files are held to having a value; see the
            # sibling branch above for why upstream's empties are left alone.
            if (v is None or not str(v).strip()) and p.name.startswith("zz-"):
                print(f"  FAIL  {p}:{k} has no value")
                fails += 1

    feat_root = pathlib.Path("vendor/upstream/features", stufe)
    if not feat_root.is_dir():
        continue

    # Keys a feature file writes at run time via a Tiger store step are defined by
    # the scenario itself, so they are legitimately absent from the contract.
    runtime = set()
    used = {}
    # A key used only by a feature the suite never executes needs no test datum, so
    # scope has to be judged before an absent key is called a defect.
    #
    # Scope is evaluated with the real tag expression against real Cucumber tag
    # inheritance (parse_tag_expression / count_asserting_cases), not by
    # grepping the file header. The difference is not academic: a tag immediately
    # above an `Examples:` block belongs to that block rather than to the scenario
    # that follows, and Stufe5/Medikation/RiskAssessment-Search.feature is exactly
    # that shape - a header-grep reads its @Optional as excluding a scenario that
    # in fact runs, and would then dismiss a genuinely missing key as out of scope.
    expr = os.environ.get(f"TESTS_TO_RUN_{stufe.upper()}") or f"@{stufe} and (not @Optional)"
    predicate = fp.parse_tag_expression(expr)
    for feature in fp.load_features(feat_root):
        f = feature.path
        text = feature.text
        runtime |= fp.runtime_keys_in(text)
        skipped = fp.count_asserting_cases(feature, predicate) == 0
        for i, line in enumerate(text.splitlines(), 1):
            for key in fp.data_keys_in(line):
                where = f"{f.relative_to(feat_root.parent)}:{i}"
                used.setdefault(key, []).append(where)
                if not skipped:
                    in_scope.setdefault(key, []).append(where)

    # Required = wanted by at least one in-scope feature. Built up from the
    # in-scope files rather than subtracted from the skipped ones, so a key used by
    # both an executed and a skipped feature stays required.
    missing = sorted(k for k in in_scope if k not in defined and k not in runtime)
    for k in missing:
        print(f"  FAIL  {stufe}: ${{{k}}} used at {in_scope[k][0]} "
              f"but defined nowhere in {root}/{stufe}/")
        fails += 1
    unused_oos = sorted(k for k in used
                        if k not in in_scope and k not in defined and k not in runtime)
    print(f"  {'PASS' if not missing else 'FAIL'}  {stufe}: {len(defined)} keys defined, "
          f"{len(used)} referenced, {len(runtime)} set at run time, {len(missing)} unresolved")
    for k in unused_oos:
        print(f"        not required: ${{{k}}} is used only by features that "
              f"{expr!r} does not select ({used[k][0]})")

if fails:
    sys.exit(1)
PY
  (( $? == 0 )) || FAILURES=$((FAILURES+1))

  # The requirement extractor, run for its exit status. Two failure modes are
  # worth two seconds here rather than an hour into a round: feature_parser raises
  # on any Gherkin construct it does not model (a silently skipped block would
  # miscount scenarios and drop a requirement), and the extractor refuses outright
  # when one ${data.*} key is used with two different FHIR resource types, because
  # its level-wide type merge is then invalid. Both are release-bump hazards, and
  # both would otherwise first appear during `make packages`.
  # Run at the generation's own shape: with the wrong --levels it would exit on a
  # level the corpus does not carry, and without --tiger-templates every key of a
  # 3.x corpus would be read as living in the 2.x namespace `data`.
  if err="$(python3 scripts/lib/extract_requirements.py \
              --features-root vendor/upstream/features \
              --testdata-root "$SUITE_TESTDATA_ROOT" \
              --tiger-templates "$SUITE_TIGER_DIR" \
              --levels "$SUITE_LEVELS" \
              --out-dir "$(mktemp -d)" 2>&1 >/dev/null)"; then
    pass "requirement extraction succeeds over all feature files"
  else
    fail "requirement extraction failed:"
    printf '%s\n' "$err" | sed 's/^/        /' | head -n 20
  fi
fi

# -----------------------------------------------------------------------------
section "5b. Fabricated test data"
# -----------------------------------------------------------------------------
# The same three checks `make seed` runs before it writes anything, repeated here
# because they need neither a container nor a licence key and because this is the
# only place they get run on a host that will never deploy.
#
# What they protect against is one specific misattribution: a seeded resource that
# misses an assertion, or that lacks a reference a search scenario filters on,
# produces a failing scenario indistinguishable in the official report from an
# IRIS conformance gap. Catching it here costs two seconds; catching it after a
# round costs the round and, worse, may not be caught at all.
#
# PACKAGES_DIR is honoured if the caller exports one, so this section is runnable
# on a host where the packages were unpacked somewhere other than vendor/.
CHECK_PACKAGES="${PACKAGES_DIR:-vendor/packages}"
if [[ -z "$SUITE_GENERATION" ]]; then
  skip "suite generation unresolved - the authored test data cannot be rendered"
elif [[ ! -f vendor/requirements.json ]]; then
  skip "vendor/requirements.json absent - test-data checks need 'make packages' first"
# maxdepth 3, not 2: 20-fetch-packages.sh unpacks with --strip-components=1 so
# package.json sits at the package directory's root, but build_testdata.py also
# accepts the un-stripped `<pkg>/package/` layout that a plain `tar -xzf` produces,
# and this check must not refuse a tree the renderer would happily read.
# -L, so that a symlinked vendor/packages is traversed. That is the other natural
# way to satisfy the note above about packages unpacked elsewhere - a bind mount or
# a symlink to a scratch disk, since the unpacked set is ~1 GB - and without -L
# find silently returns nothing, which reads as "packages absent" and skips the
# checks rather than reporting a problem.
elif [[ -z "$(find -L "$CHECK_PACKAGES" -maxdepth 3 -name package.json -print -quit 2>/dev/null)" ]]; then
  # Without the unpacked packages, {{profile.Name}} cannot be resolved to the
  # canonical that level's endpoint would load, so every affected resource would
  # be reported as unrenderable. That is an absent prerequisite, not a defect.
  skip "no unpacked FHIR packages under ${CHECK_PACKAGES} - test-data checks need 'make packages' first"
else
  BUILD_TMP="$(mktemp -d)"
  # Rendered into a temporary tree rather than build/: `make check` must not
  # disturb a corpus a seeding run may be about to write, and a check that mutates
  # the working tree cannot be run while something else is in progress.
  # One --src per authored tree of the generation, shared tree first, exactly as
  # 50-seed.sh renders it: a 3.x round reads testdata/fhir and testdata/fhir/v3,
  # and checking only one of them would miss the resources the other supplies.
  CHECK_SRC=()
  for root in $SUITE_FHIR_ROOTS; do CHECK_SRC+=(--src "$root"); done
  if out="$(python3 scripts/lib/build_testdata.py \
              --packages "$CHECK_PACKAGES" --out "$BUILD_TMP" \
              --levels "$SUITE_LEVELS" "${CHECK_SRC[@]}" 2>&1)"; then
    pass "test data renders for ${SUITE_LEVELS}"
    printf '%s\n' "$out" | grep -E '^Stufe[35]:' | sed 's/^/        /'
    # The renderer notes every meta.profile claim that no assertion corroborates -
    # legitimate for a `never-read` or `fixture-body` row, where the claim is the
    # author's reading of the precondition prose. There are ~26 of them and listing
    # each would bury the checks around it, so they are counted here and enumerated
    # in the seed step's own output and in docs/en/05-testdata.md.
    n_notes="$(printf '%s\n' "$out" | grep -c 'claims a profile that no assertion' || true)"
    (( n_notes > 0 )) && note "${n_notes} uncorroborated meta.profile claim(s) - expected for never-read and fixture-body rows; run build_testdata.py to list them"
  else
    fail "test-data rendering failed:"
    printf '%s\n' "$out" | sed 's/^/        /' | head -n 30
  fi

  for level in $SUITE_LEVELS; do
    if [[ ! -d "${BUILD_TMP}/${level}" ]]; then
      skip "${level}: nothing rendered - assertion check not run"
      continue
    fi
    if out="$(python3 scripts/lib/assertion_check.py --level "$level" \
                --data-root "$BUILD_TMP" 2>&1)"; then
      pass "${level}: $(printf '%s' "$out" | grep -E '^  (EXACT|ELEMENT|LITERAL) ' \
              | tr -s ' ' | paste -sd' ' -)"
    else
      fail "${level}: the rendered test data does not satisfy the suite's assertions:"
      printf '%s\n' "$out" | grep -E '^  FAIL|^FAIL' | sed 's/^/        /' | head -n 20
    fi
  done

  # "Anything rendered at all", over the generation's own levels: a 3.x round has
  # no Stufe3 directory by construction, and testing for one would read that as an
  # empty render and skip the three checks below.
  RENDERED_ANY=0
  for level in $SUITE_LEVELS; do
    [[ -d "${BUILD_TMP}/${level}" ]] && RENDERED_ANY=1
  done

  if (( RENDERED_ANY )); then
    # Tag expressions come from the environment if set, exactly as the extractor
    # does it, so a narrowed debugging scope is checked at the scope it will run.
    # --tags-stufe3 is passed only when the suite carries Stufe 3, as 50-seed.sh
    # does it, so that a 3.x round names no expression for a level it will not run.
    SR_TAGS=()
    [[ " ${SUITE_LEVELS} " == *" Stufe3 "* ]] && \
      SR_TAGS+=(--tags-stufe3 "${TESTS_TO_RUN_STUFE3:-@Stufe3 and (not @Optional)}")
    if out="$(python3 scripts/lib/search_reference_check.py \
                --data-root "$BUILD_TMP" \
                --levels "$SUITE_LEVELS" \
                "${SR_TAGS[@]}" \
                --tags-stufe5 "${TESTS_TO_RUN_STUFE5:-@Stufe5 and (not @Optional)}" 2>&1)"; then
      pass "search-scenario reference requirements are satisfied"
      printf '%s\n' "$out" | grep -E '^Stufe[35]:' | sed 's/^/        /'
      # Upstream defects, not ours. Surfaced because a reader of a failing run
      # needs to know these scenarios cannot pass against any conformant server.
      printf '%s\n' "$out" | grep -E '^  upstream' | sed 's/^/        /'
    else
      fail "a search scenario requires a reference no seeded resource carries:"
      printf '%s\n' "$out" | sed 's/^/        /' | head -n 30
    fi
  fi

  # Choice elements written by their bare name. Run against the *rendered* tree
  # rather than the authored sources, so a placeholder that resolves into a choice
  # element is covered too. See scripts/lib/choice_type_check.py for why this is a
  # static check at all: the seeder's $validate did catch the one instance of this,
  # but only after a licensed image, a bootstrap and a seeding pass - and the same
  # misspelling in a reference target would have surfaced in the official report as
  # an IRIS search failure rather than as our own data defect.
  if (( RENDERED_ANY )); then
    if out="$(python3 scripts/lib/choice_type_check.py \
                --src "$BUILD_TMP" --packages "$CHECK_PACKAGES" 2>&1)"; then
      pass "$(printf '%s\n' "$out" | tail -n1)"
    else
      fail "test data writes a FHIR choice element by its bare name:"
      printf '%s\n' "$out" | sed 's/^/      /' | head -n 20
    fi
  fi

  # Reference ordering. Reported rather than failed: a cycle is a property of the
  # suite's own precondition prose (Encounter.diagnosis <-> Condition.encounter),
  # not a defect in this repository, and the seeder closes it with a second write.
  for level in $SUITE_LEVELS; do
    [[ -d "${BUILD_TMP}/${level}" ]] || continue
    if out="$(python3 scripts/lib/seed_order.py --level "$level" \
                --data-root "$BUILD_TMP" 2>&1)"; then
      cyc="$(printf '%s\n' "$out" | awk -F'\t' '$4 != ""' | wc -l | tr -d ' ')"
      pass "${level}: $(printf '%s\n' "$out" | wc -l | tr -d ' ') resources orderable"
      (( cyc > 0 )) && note "${level}: ${cyc} written ahead of a reference target (re-written in the seeder's second pass): $(printf '%s\n' "$out" | awk -F'\t' '$4 != "" {print $2"/"$3" -> "$4}' | paste -sd'; ' -)"
    else
      fail "${level}: seed ordering failed:"
      printf '%s\n' "$out" | sed 's/^/        /' | head -n 10
    fi
  done

  rm -rf "$BUILD_TMP"
fi

# -----------------------------------------------------------------------------
section "6. Cross-file consistency"
# -----------------------------------------------------------------------------
# Every script the Makefile calls must exist. A missing one is otherwise only
# discovered at the moment that target runs, which for 80-gap-report.sh is after
# the entire test round.
missing_scripts=0
while IFS= read -r s; do
  [[ -f "$s" ]] || { fail "the Makefile invokes ${s} but it does not exist"; missing_scripts=1; }
done < <(grep -oE '\$\(SCRIPTS\)/[0-9a-z-]+\.sh' Makefile | sed 's|\$(SCRIPTS)|scripts|' | sort -u)
(( missing_scripts == 0 )) && pass "every script the Makefile invokes exists"

# Compose bind-mount sources under run/ must be produced by the renderer, and the
# renderer's outputs must be mounted. A mismatch means compose fails at container
# start with a message about a path, not about the cause.
python3 - "$SUITE_LEVELS" <<'PY'
import pathlib, re, sys

# Every ISiK level this rig has ever rendered a configuration for - NOT the levels
# of this round. compose/ is one file shared by both suite generations, so a mount
# for a level the pinned release no longer carries is correct and must not fail;
# the service that mounts it is simply never started. A mount for a level no
# generation ever had is a typo, and that is what this list separates out.
ALL_LEVELS = ("stufe3", "stufe5")
# Unresolved generation (reported by its own check in section 5): fall back to
# every known level, so this check still compares something.
levels = sys.argv[1].lower().split() or list(ALL_LEVELS)

# Both suite compose files, because the Connect module's own service lives in the
# authorization server overlay: it cannot run without it. Read as one text, since
# the question here is only whether SOME service mounts the file - which service,
# and under which condition, is section 7's business.
COMPOSE_FILES = ("compose/testsuite.yml", "compose/authserver.yml")
compose = "".join(pathlib.Path(f).read_text(encoding="utf-8")
                  for f in COMPOSE_FILES if pathlib.Path(f).is_file())
mounted = set(re.findall(r"- \./run/(tiger/[A-Za-z0-9._/-]+\.yaml):", compose))
renderer = pathlib.Path("scripts/30-render-config.sh").read_text(encoding="utf-8")

fails = 0

# The renderer writes one configuration per suite_levels(), so neither the code
# nor its header carries a literal list of output paths: the header names each
# FAMILY, as `run/tiger/tiger-isik-<level>.yaml`. That header block is what the
# operator and the documentation both read, so comparing the families expanded
# over the pinned generation's levels against the mounts verifies the two things
# that can drift: the header going stale, and a mount pointing at a file nothing
# produces.
families = {d[len("run/"):]
            for d in re.findall(r"#   (run/tiger/[A-Za-z0-9._/<>-]+\.yaml)", renderer)
            if "<level>" in d}
if not families:
    print("  FAIL  scripts/30-render-config.sh header lists no "
          "run/tiger/...<level>....yaml output families")
    sys.exit(1)


def expand(lvs):
    return {f.replace("<level>", lv) for f in families for lv in lvs}


# Outputs the header names literally rather than per level: the Connect module's
# configuration is one file for one module of one level, so it has no family. It
# is rendered only for a round with CONNECT_ENABLE=true and mounted only by the
# overlay, but both sides of that are static facts about the repository and are
# therefore checked unconditionally.
literals = {d[len("run/"):]
            for d in re.findall(r"#   (run/tiger/[A-Za-z0-9._/<>-]+\.yaml)", renderer)
            if "<level>" not in d}

expected = expand(levels) | literals    # rendered by THIS generation
ever = expand(ALL_LEVELS) | literals    # rendered by some generation

for d in sorted(expected - mounted):
    print(f"  FAIL  scripts/30-render-config.sh writes run/{d} but no compose service mounts it")
    fails += 1
for m in sorted(mounted - ever):
    print(f"  FAIL  compose mounts run/{m} but scripts/30-render-config.sh does not list it as an output")
    fails += 1
if fails:
    sys.exit(1)
print(f"  PASS  {len(expected)} rendered configuration file(s) for {' '.join(levels)}, "
      f"compose mounts and renderer outputs agree")
for m in sorted(mounted - expected):
    print(f"        mounted but not rendered: run/{m} - the pinned release carries only "
          f"{', '.join(levels)}, and the service mounting it is not started")

# The same invariant for the Web Gateway's two rendered files, which the base compose
# file mounts rather than compose/testsuite.yml. Worth its own check: these two are
# what makes the SUT answer HTTP at all, so a mount pointing at a file the renderer
# does not write means every scenario in the round fails with a connection error.
base = pathlib.Path("docker-compose.yml").read_text(encoding="utf-8")
# Plus the overlay, which adds the HTTPS virtual host beside the :80 one the base
# file mounts. Same reasoning as for the Tiger configurations above.
if pathlib.Path("compose/authserver.yml").is_file():
    base += pathlib.Path("compose/authserver.yml").read_text(encoding="utf-8")
gw_mounted = set(re.findall(r"- \./run/(webgateway/[A-Za-z0-9._-]+):", base))
gw_declared = {d[len("run/"):] for d in
               re.findall(r"#   (run/webgateway/[A-Za-z0-9._-]+)", renderer)}

gw_fails = 0
for m in sorted(gw_mounted - gw_declared):
    print(f"  FAIL  compose mounts run/{m} but scripts/30-render-config.sh does not list it as an output")
    gw_fails += 1
for d in sorted(gw_declared - gw_mounted):
    print(f"  FAIL  scripts/30-render-config.sh writes run/{d} but no compose service mounts it")
    gw_fails += 1
if gw_fails:
    sys.exit(1)
if not gw_mounted:
    print("  FAIL  docker-compose.yml mounts no run/webgateway/* file - the SUT would serve no HTTP")
    sys.exit(1)
print(f"  PASS  {len(gw_mounted)} rendered Web Gateway files, mounts and renderer outputs agree")
PY
(( $? == 0 )) || FAILURES=$((FAILURES+1))

# Variables the scripts read from the environment should be documented in
# .env.example, or an operator has no way to discover them.
python3 - <<'PY'
import pathlib, re, sys

example = pathlib.Path(".env.example").read_text(encoding="utf-8")
declared = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", example, re.M))

# Names set by the scripts themselves or by compose, not by the operator.
internal = {
    "REPO_ROOT", "RUN_ID", "SCRIPTS_DIR", "ENV_FILE", "ENV_RESOLVED_FILE",
    "VENDOR_DIR", "PACKAGES_DIR", "RUN_DIR", "REPORTS_DIR", "SECRETS_DIR",
    "ENV_LOADED_FROM", "TESTSUITE_REF", "PLUGINS_DIR", "TGZ_DIR", "UPSTREAM_DIR",
    "MANIFEST", "PARITY", "BUNDLED_DIR", "PROMOTED_FETCHED", "IMPLICIT_FETCHED",
    # 20-fetch-packages.sh's parity bookkeeping. SUBSTITUTED holds the payloads the
    # simplifier route could not download and filled from the image; the rest are
    # the classified rows and their tallies.
    "SUBSTITUTED", "PARITY_ROWS", "N_BUNDLED_TOTAL",
    "PARITY_JSON", "PARITY_DETAIL", "CONTENT_VERDICT", "verdict", "fname",
    "PARITY_CARRIED",
    "n_match", "n_equiv", "n_differ", "n_bundled", "n_absent", "substituted", "status",
    "REPORT_DIR", "DOCKER_CONFIG", "HOME",
    "LC_ALL", "BASH_SOURCE", "SUBST", "TIGER_TMPL_DIR", "TESTDATA_SRC",
    "OUT_TIGER", "OUT_TIGER_MANUAL", "OUT_TESTDATA", "WORKFLOW_UI_PORT",
    "PROXY_ADMIN_PORT", "UPSTREAM_TESTDATA", "PARITY_FILE", "ALLOWED_ADDED",
    "CRED_FILE", "MGMT_PW_FILE", "SUT_BASE_HOST3", "SUT_BASE_HOST5",
    "SUT_BASE_NET3", "SUT_BASE_NET5",
    # 30-render-config.sh's accumulators: the configuration files it rendered this
    # round, and the endpoint rows of run/CREDENTIALS.md. Both are built from
    # suite_levels(), so an operator-set value would describe a round other than
    # the one being rendered.
    "RENDERED_CONFIGS", "ENDPOINT_ROWS",
    # Web Gateway rendering internals. GATEWAY_IRIS_PASSWORD is deliberately NOT an
    # operator setting: it is assigned from IRIS_MGMT_PASSWORD so that the value the
    # bootstrap sets on CSPSystem and the value in the rendered CSP.ini cannot drift.
    # An operator override would break that link and produce a bare HTTP 500 on every
    # FHIR request. GATEWAY_IRIS_USER *is* documented in .env.example, since which
    # account to use is a legitimate choice.
    "WEBGATEWAY_TMPL_DIR", "OUT_WEBGATEWAY", "GATEWAY_IRIS_PASSWORD",
    "IRIS_SUPERSERVER_PORT", "GATEWAY_CONTAINER",
    # lib.sh's password alphabets. Not settings: the character set is a property of
    # gen_password()'s guarantees (no ambiguous glyphs, and the IRIS default
    # PasswordPattern satisfied by construction), which an operator override would
    # silently break.
    "PW_ALPHA_LOWER", "PW_ALPHA_UPPER", "PW_ALPHA_DIGIT",
    # IRIS_UID is 40-up.sh's local for the documented IRIS_CONTAINER_UID setting,
    # not a second knob.
    "IRIS_UID",
    "IRIS_CONTAINER", "STATE_DIR", "STATUS_FILE", "BOOTSTRAP_LOG", "PROVENANCE",
    "BOOTSTRAP_TIMEOUT", "POLL_INTERVAL", "PROJECT", "NET_NAME", "CONTAINER",
    "UI_PORT", "PROXY_PORT", "UI_TIMEOUT", "WANT_TAG", "PW_FILE", "TMP",
    "IRIS_REF", "WEBGATEWAY_REF",
    "IRIS_REGISTRY", "DOCKER_CFG", "COMPOSE_V", "AVAIL_KB", "NEED_KB",
    # Locals of the preflight disk check. The floor and the reason it names both
    # follow CONNECT_ENABLE, which IS documented, and are derived from the
    # measurements in INSTALL.md section 2 - a host requirement is measured, not
    # configured, so neither is an operator setting.
    "NEED_GIB", "NEED_WHY", "AVAIL_GIB",
    # Locals of the preflight memory check. MEM_NEED_GIB and MEM_OVERLAY_NOTE are
    # the threshold and the clause of the warning that explains it, set together
    # for the same reason as NEED_GIB above. TESTSUITE_HEAP_MAX, which that check
    # reads, IS operator-facing and is documented in .env.example.
    "MEM_KB", "MEM_GIB", "MEM_NEED_GIB", "MEM_OVERLAY_NOTE",
    "PORT_CLASH", "EXISTING_NET", "KEY", "C_RESET", "C_BOLD", "C_RED", "C_GRN",
    # 00-preflight.sh's licence-key locals. IRIS_KEY_PATH is exported for the compose
    # mount rather than authored: preflight resolves the operator's file
    # case-insensitively, so its path is measured, not configured. KEY_MODE /
    # KEY_OWNER / NEW_MODE are the observed and post-fix permissions.
    "IRIS_KEY_PATH", "KEY_MODE", "KEY_OWNER", "NEW_MODE",
    "C_YEL", "C_DIM", "FAILURES", "SKIPPED",
    "LIB", "BUILD_DIR", "BUILD_TMP", "SEED_DIR", "SEED_LOG", "CHECK_PACKAGES",
    # 99-check.sh's own resolution of the suite shape. SUITE_VERSION is read FROM
    # the documented TESTSUITE_VERSION (in the environment, .env or .env.example)
    # and the rest are derived from it by suite_*() in lib.sh - a separate
    # operator setting would let `make check` examine a generation no round runs.
    "SUITE_VERSION", "SUITE_VERSION_FROM", "SUITE_GENERATION", "SUITE_LEVELS",
    "SUITE_TESTDATA_ROOT", "SUITE_TIGER_DIR", "SUITE_FHIR_ROOTS",
    "SUITE_TESTDATA_REL", "SUITE_TIGER_REL", "CHECK_SRC", "SR_TAGS",
    "RENDERED_ANY",
    # 02-build-image-from-kit.sh. Deliberately NOT in .env.example: every one of
    # these describes one kit and one build of one image, not the deployment. The
    # script runs before a round exists and writes nothing a round reads, so an
    # .env entry for KIT would be a setting that silently went stale the moment
    # the image was built - which is the failure mode .env.resolved exists to
    # prevent everywhere else. They are passed per invocation, as `make
    # image-from-kit KIT=... SUPERVISOR_REF=...`, and the Makefile forwards them.
    "KIT", "COMPONENT", "SUPERVISOR_REF", "IMAGE_PREFIX", "IMAGE_TAG",
    "BASE_IMAGE", "KIT_WORK", "KEEP_EXTRACT",
    # ...and its locals: what it read out of the kit, and its bookkeeping.
    "KIT_CONTEXT", "KIT_TOP", "KIT_VERSION", "KIT_PRODUCT", "KIT_PLATFORM",
    "PLATFORM_DISPLAY", "KIT_FILE_LABEL", "KIT_SHA256", "KIT_DIR_TEMPLATES",
    "CLEAN_EXTRACT", "IRIS_TAG", "GATEWAY_TAG", "DOCKER_BUILDKIT",
    # 60-run-tests.sh. TESTS_TO_RUN is derived from the documented
    # TESTS_TO_RUN_STUFE3/5/CONNECT and exported for compose, so it is an
    # internal name even though the container reads it as its own setting.
    # VARIANT is the invocation this run is (stufe3 | stufe5 | connect), taken
    # from the command line like `make test-connect` does; the SCOPE_* names are
    # the tag expressions it is compared against and the partition it was found
    # to be, which are derived from the variant and must not be assertable by
    # configuration - a round that could *declare* itself the complete scope
    # would make the field worthless.
    "STUFE", "LEVEL", "VOLUME", "ARCHIVE_DIR", "OFFICIAL_DIR", "RUN_TIMEOUT",
    "TESTS_TO_RUN", "OFFICIAL_SCOPE", "LOG_PID", "EXIT_CODE",
    "VARIANT", "SERVICE", "DEFAULT_TAGS", "SCOPE_PARTITION", "SCOPE_NOTE",
    "SCOPE_WITHOUT_CONNECT", "SCOPE_CONNECT_ONLY", "CONFORMANCE_SCOPE",
    "SUT_URL", "SUT_URL_HOST", "TIGER_CONFIG", "WHAT",
    # 22-make-tls.sh. The subject base and the two validity periods are
    # properties of the round's throwaway CA, not settings: the certificates are
    # regenerated whenever they are close to expiry, and a leaf valid for years
    # would outlive the environment it was issued for.
    "OUT_TLS", "TLS_SUBJECT_BASE", "CA_DAYS", "LEAF_DAYS",
    # 30-render-config.sh's output directory for the authorization server's realm.
    "OUT_AS",
    # 55-mint-tokens.sh. All locals: the endpoints it derives from the documented
    # AS_HOSTNAME / AS_REALM, the request body it posts, and the values it then
    # compares a minted token's claims against. EXPECTED_ISSUER / ACTUAL_ISSUER
    # are the two sides of that comparison - an operator-set "expected" value
    # would let a token from another issuer pass the check it exists to fail.
    "AS_ORIGIN", "AS_REALM_URL", "TOKEN_ENDPOINT", "BODY_FILE", "WORK",
    "EXPECTED_ISSUER", "ACTUAL_ISSUER", "EXPECTED_AUDIENCE", "MIN_REMAINING",
    # Supplied by the operating system, not by this rig.
    "TMPDIR",
    # STARTED_AT is when this invocation began, taken once and then compared
    # against the mtime of every archive the suite produced, so that a stale
    # report left in the shared volume cannot be collected as this round's.
    # Measured, never configured: an operator-supplied value would defeat the
    # very assertion it feeds.
    "STARTED_AT",
    # 70-collect-report.sh
    "RUN_REPORT_DIR", "ANALYSIS_DIR", "PROV_DIR",
    # 80-gap-report.sh
    "GAP_DIR", "GAP_INDEX", "GAP_RUN_DIR", "GAP_RESULTS", "GAP_FEATURES",
    "GAP_MD", "GAP_JSON", "GAP_MANIFEST", "GAP_TMP",
    # 81-case-evidence.sh. The same shape as the GAP_* names above: resolved
    # paths inside the round's own bundle, plus the gap analysis it reads to
    # attribute each case. Nothing an operator configures - the annex has no
    # knob but the run id, which is RUN_ID.
    "CE_DIR", "CE_RUN_DIR", "CE_RESULTS", "CE_GAP_JSON",
    "CE_MD", "CE_JSON", "CE_MANIFEST", "CE_TMP",
    # 85-compare-rounds.sh. BASELINE and CANDIDATE are per-invocation arguments
    # of `make compare`, like RUN_ID above: which two rounds to join is a
    # judgement about what a figure is meant to show, so it is made on the
    # command line and not carried in .env, where it would silently apply to
    # the next comparison as well. COMPARE_MD asks for the human-readable form
    # beside the JSON. The CMP_* names are the resolved paths.
    "BASELINE", "CANDIDATE", "COMPARE_MD",
    "CMP_BASE", "CMP_CAND", "CMP_JSON", "CMP_OUT_DIR", "CMP_MANIFEST",
    "CMP_REL", "CMP_TMP",
    # scripts/lib/validate_corpus.sh - seed stage 4, the suite's own reference
    # validator run against the rendered corpus. All four are locals: the
    # absolute forms of the directories the caller passed (docker needs absolute
    # bind-mount sources), the directory the script itself lives in, and the
    # argument that is added only when a requirements corpus was supplied.
    "SELF_DIR", "CORPUS_ABS", "OUT_ABS", "ASSERTED_ARG",
    # 01-provision-host.sh. None of these is a knob: they are what the host
    # already is (VERSION_CODENAME and SUDO_USER come from /etc/os-release and
    # sudo respectively) or what the script decided to install. An operator who
    # set one by hand would be describing a host other than the one being
    # provisioned.
    "TARGET_USER", "APT_PKGS", "MISSING_PKGS", "DOCKER_KEYRING", "DOCKER_LIST",
    "DOCKER_REPO", "VERSION_CODENAME", "OS_NAME", "OS_VER", "GROUP_ADDED",
    "SOCKET_OK", "SUDO_USER", "DEBIAN_FRONTEND", "UBUNTU_CODENAME",
    "VERSION_ID",
}
# R_* is 10-resolve-versions.sh's own naming for a *resolved* value: the outcome
# of resolution, not an input to it. They are deliberately not in .env.example -
# an operator who set one by hand would be overriding the resolver with a value
# that then appeared in .env.resolved as if it had been measured.
internal |= {"R_IRIS_VERSION", "R_IRIS_DIGEST", "R_IRIS_ZV", "R_IRIS_SOURCE",
             "R_TESTSUITE_VERSION", "R_TESTSUITE_DIGEST", "R_TESTSUITE_SOURCE",
             "R_WEBGATEWAY_VERSION", "R_WEBGATEWAY_DIGEST", "R_WEBGATEWAY_SOURCE",
             # Which way each axis was resolved, and the newest suite release seen
             # while doing it. Written to .env.resolved as IRIS_VERSION_SOURCE /
             # TESTSUITE_VERSION_SOURCE / TESTSUITE_LATEST_AVAILABLE, which are in
             # the `resolved` set below.
             "R_IRIS_VERSION_SOURCE", "R_TESTSUITE_VERSION_SOURCE",
             "R_TESTSUITE_LATEST",
             # The authorization server's image, resolved only when
             # CONNECT_ENABLE=true. AS_REF is the tag reference composed from the
             # documented AS_IMAGE and AS_VERSION, like IRIS_REF above.
             "R_AS_DIGEST", "R_AS_SOURCE", "AS_REF"}
# Computed by load_env in lib.sh: the run-behaviour settings this invocation was
# given on the command line, as "NAME=value" pairs. An output of the configuration
# load, not an input to it - setting it in .env would claim an override that never
# happened. RUN_OVERRIDABLE is the list load_env honours, likewise code.
internal |= {"ENV_OVERRIDES", "RUN_OVERRIDABLE", "CONFIG_OVERRIDABLE"}
# Recorded into .env.resolved by 10-resolve-versions.sh rather than authored.
# *_IMAGE_SOURCE says whether the digest beside it is a registry digest or the
# local ID of a side-loaded image; it is measured at pull time, never authored.
#
# *_VERSION_SOURCE records HOW a version was arrived at - verbatim from .env, or
# resolved upstream at the timestamp above - and TESTSUITE_LATEST_AVAILABLE is the
# newest release seen while resolving. All three are observations made during
# `make resolve`; the corresponding operator-facing settings are MODE and
# TESTSUITE_VERSION_TRACK, which .env.example documents.
resolved = {
    "RESOLVED_AT", "RESOLVED_MODE", "IRIS_IMAGE_REF", "IRIS_IMAGE_DIGEST",
    "IRIS_IMAGE_SOURCE", "IRIS_BUILD_VERSION", "TESTSUITE_IMAGE_REF",
    "TESTSUITE_IMAGE_DIGEST", "TESTSUITE_IMAGE_SOURCE",
    "WEBGATEWAY_IMAGE_REF", "WEBGATEWAY_IMAGE_DIGEST", "WEBGATEWAY_IMAGE_SOURCE",
    "IRIS_VERSION_SOURCE", "TESTSUITE_VERSION_SOURCE",
    "TESTSUITE_LATEST_AVAILABLE",
    # Present only in a round with CONNECT_ENABLE=true.
    "AS_IMAGE_REF", "AS_IMAGE_DIGEST", "AS_IMAGE_SOURCE",
}

used = set()
for p in sorted(pathlib.Path("scripts").rglob("*.sh")):
    for line in p.read_text(encoding="utf-8").splitlines():
        # Comment lines are skipped. Several scripts document the very shell
        # syntax being matched here - "${NAME:-default}" written out in a comment
        # to explain a convention - and counting those as reads produces failures
        # for variables named NAME and VAR that no script has ever used.
        if line.lstrip().startswith("#"):
            continue
        # ${NAME:-default}, ${NAME:?msg}, ${NAME} - operator-facing settings are
        # always read with one of these forms.
        used |= set(re.findall(r"\$\{([A-Z][A-Z0-9_]{2,})[:}]", line))

undocumented = sorted(used - declared - internal - resolved)
if undocumented:
    for n in undocumented:
        print(f"  FAIL  scripts read ${{{n}}} but .env.example does not document it")
    sys.exit(1)
print(f"  PASS  every operator-facing variable the scripts read is documented in .env.example")
PY
(( $? == 0 )) || FAILURES=$((FAILURES+1))

# The one hard constraint on credentials: they must never reach source control.
# Everything else about them is deliberately open - each round's passwords are
# printed by `make credentials`, listed in run/CREDENTIALS.md and stated verbatim in
# the generated reports - which is exactly why this check exists. A future
# .gitignore edit that let a generated password into the index would be silent
# otherwise, and a password in git history is not something this project can undo
# by destroying the environment.
if ! command -v git >/dev/null 2>&1 || ! git rev-parse --git-dir >/dev/null 2>&1; then
  skip "not a git working tree (or git absent) - the gitignore check needs one"
else
  ignore_fails=0
  # run/as/realm-isik.json carries the realm's shared client secret and
  # run/tls/ca.key the key of the round's local CA; both are generated per
  # deployment, like the passwords above, and neither may ever be committed.
  for path in secrets/sut.password secrets/iris-mgmt.password secrets/iris.key \
              secrets/as-admin.password secrets/as-client.secret \
              .env .env.resolved run/CREDENTIALS.md \
              run/as/realm-isik.json run/tls/ca.key; do
    # check-ignore works on paths that do not exist, which is the point: the
    # patterns must be right before the file is ever generated.
    git check-ignore -q "$path" 2>/dev/null \
      || { fail "${path} is NOT gitignored - a generated credential could be committed"; \
           ignore_fails=$((ignore_fails+1)); }
  done
  (( ignore_fails > 0 )) \
    || pass "the credential-bearing paths are all gitignored"

  # And nothing carrying one is staged or tracked right now.
  tracked="$(git ls-files -- secrets/sut.password secrets/iris-mgmt.password \
                            secrets/iris.key .env .env.resolved run/ 2>/dev/null || true)"
  if [[ -n "$tracked" ]]; then
    fail "credential-bearing files are TRACKED by git:"
    printf '%s\n' "$tracked" | sed 's/^/        /'
  else
    pass "no credential-bearing file is tracked"
  fi
fi

# -----------------------------------------------------------------------------
section "7. Compose validation"
# -----------------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  skip "docker not installed - 'docker compose config' not run"
elif ! docker compose version >/dev/null 2>&1; then
  skip "docker compose v2 not available - 'docker compose config' not run"
else
  # config resolves interpolation, merges both files and validates the schema
  # without starting anything. .env.example stands in for .env so the check works
  # on a host that has never been configured; the two passwords are required by
  # docker-compose.yml's ${...:?...} interpolations and are empty in the example,
  # because a real deployment generates them.
  #
  # MEASURED, 2026-11-16 (compose v5.7.0): `config` OMITS every service whose
  # profile is not active, so the profiles a round uses have to be named here or
  # the resolved document contains the two long-running containers and nothing
  # else. An earlier version of this check asserted the suite's static address by
  # grepping the whole document and passed for the wrong reason: the address also
  # appears in the `x-testsuite-common` extension block, which `config` echoes
  # verbatim whether any service uses it or not. The assertions below therefore
  # read the SERVICE, not the document.
  if out="$(SUT_PASSWORD=checkonly IRIS_MGMT_PASSWORD=checkonly docker compose \
              --env-file .env.example \
              -f docker-compose.yml -f compose/testsuite.yml \
              --profile stufe5 \
              config 2>&1)"; then
    pass "docker compose config validates the base configuration"
    # The static addresses are the whole basis of the isolation claim, so confirm
    # they survive interpolation rather than trusting the file.
    if ! ip_err="$(printf '%s' "$out" | \
         EXPECT_IRIS_IP="$(env_file_var .env.example IRIS_IP)" \
         EXPECT_SUITE_IP="$(env_file_var .env.example TESTSUITE_IP)" \
         python3 -c '
import os, sys, yaml
svc = (yaml.safe_load(sys.stdin) or {}).get("services", {})
want = {"iris": os.environ["EXPECT_IRIS_IP"],
        "testsuite-stufe5": os.environ["EXPECT_SUITE_IP"]}
for name, ip in want.items():
    s = svc.get(name)
    if s is None:
        sys.exit("the resolved configuration defines no %s service" % name)
    got = ((s.get("networks") or {}).get("isik-net") or {}).get("ipv4_address")
    if got != ip:
        sys.exit("%s resolves to address %r, not the configured %r" % (name, got, ip))
if "stufe5" not in (svc["testsuite-stufe5"].get("profiles") or []):
    sys.exit("testsuite-stufe5 is not gated by the \"stufe5\" profile"
             " - it would start together with the environment")
' 2>&1)"; then
      fail "the resolved base configuration is wrong:"
      printf '%s\n' "$ip_err" | sed 's/^/        /' | head -n 5
    fi
    note "services (profile stufe5): $(printf '%s' "$out" | python3 -c '
import sys, yaml
print(", ".join(sorted((yaml.safe_load(sys.stdin) or {}).get("services", {}))))' 2>/dev/null || echo '?')"
  else
    fail "docker compose config rejected the configuration:"
    printf '%s\n' "$out" | sed 's/^/        /' | head -n 20
  fi

  # And the same with the authorization server overlay, which is the OTHER
  # configuration this repository has to be able to produce. Both are checked on
  # every invocation regardless of CONNECT_ENABLE: a round runs one of them, but
  # the repository owes both, and the one not currently selected is exactly the
  # one whose breakage would go unnoticed.
  #
  # The three generated credentials stand in as above. AS_ADMIN_PASSWORD and
  # AS_CLIENT_SECRET are required by compose/authserver.yml's ${...:?...}
  # interpolations and are empty in .env.example for the same reason SUT_PASSWORD
  # is: `make up` generates them per deployment.
  if out_as="$(SUT_PASSWORD=checkonly IRIS_MGMT_PASSWORD=checkonly \
               AS_ADMIN_PASSWORD=checkonly AS_CLIENT_SECRET=checkonly \
               docker compose \
                 --env-file .env.example \
                 -f docker-compose.yml -f compose/testsuite.yml \
                 -f "$(bash -c '. scripts/lib.sh >/dev/null 2>&1; printf "%s" "$CONNECT_COMPOSE_FILE"')" \
                 --profile stufe5 --profile connect \
                 config 2>&1)"; then
    pass "docker compose config validates the configuration with the authorization server"
    # The Connect module's own suite service must be profile-gated, or `make up`
    # would start a second suite container beside the one the round is running,
    # both bound to the same static address. Checked on the RESOLVED
    # configuration, because a profile can also be lost in a merge - activating
    # the profile above is what makes the service visible at all, and the
    # `profiles` key it carries there is what proves the gate survived.
    if ! conn_err="$(printf '%s' "$out_as" | python3 -c '
import sys, yaml
svc = (yaml.safe_load(sys.stdin) or {}).get("services", {})
c = svc.get("testsuite-connect")
if c is None:
    sys.exit("the resolved configuration defines no testsuite-connect service")
if "connect" not in (c.get("profiles") or []):
    sys.exit("testsuite-connect is not gated by the \"connect\" profile "
             "- it would start together with the round")
if "authserver" not in svc:
    sys.exit("the overlay was merged but no authserver service is present")
' 2>&1)"; then
      fail "the resolved configuration with the authorization server is wrong:"
      printf '%s\n' "$conn_err" | sed 's/^/        /' | head -n 5
    fi
    note "services with the overlay (profiles stufe5, connect): $(printf '%s' "$out_as" | python3 -c '
import sys, yaml
print(", ".join(sorted((yaml.safe_load(sys.stdin) or {}).get("services", {}))))' 2>/dev/null || echo '?')"
  else
    fail "docker compose config rejected the configuration with the authorization server:"
    printf '%s\n' "$out_as" | sed 's/^/        /' | head -n 20
  fi
fi

# -----------------------------------------------------------------------------
section "8. Release hygiene"
# -----------------------------------------------------------------------------
# What this repository may and may not contain, enforced rather than remembered.
# The rules are stated for readers in README.md, "What this repository does not
# contain"; this is the mechanical half. They took effect at version 1.0.0, and
# every one of them exists because the alternative is a repository that cannot be
# handed to a third party:
#
#   * a product image or installation kit is licensed material and this is not a
#     distribution channel for it;
#   * the gematik test suite is gematik's, pinned and pulled, never vendored, and
#     so is the authorization server the `Connect` overlay adds: both are
#     somebody else's software under somebody else's licence, and this repository
#     carries the reference and the configuration, never the payload;
#   * a credential in git history cannot be withdrawn;
#   * a round bundle belongs to whoever took it. Ours are not secret - they are
#     simply not what this repository is for, which is the instrument.
#
# Checked against the INDEX, not the working tree: the point is what a commit
# would carry. An operator's own `reports/`, `build/` and `vendor/` are expected
# to exist locally and are none of this check's business.
if ! command -v git >/dev/null 2>&1 || ! git rev-parse --git-dir >/dev/null 2>&1; then
  skip "not a git working tree (or git absent) - the release-hygiene checks need one"
else
  # --- No images, kits or any other bulk archive --------------------------
  # Extension-based, because that is what someone adds by accident. A kit is a
  # .tar.gz and an image is a .tar; both are caught by the same rule, and so is
  # the round bundle shape (.zip) that rule 4 covers from the other direction.
  bulk="$(git ls-files -- '*.tar' '*.tar.gz' '*.tgz' '*.tar.zst' '*.tar.bz2' \
                          '*.tar.xz' '*.zip' '*.iso' '*.7z' '*.rar' 2>/dev/null || true)"
  if [[ -n "$bulk" ]]; then
    fail "archive or image files are tracked - no product image, kit or bundle belongs in this repository:"
    printf '%s\n' "$bulk" | sed 's/^/        /'
  else
    pass "no image, kit or archive file is tracked"
  fi

  # --- The authorization server is referenced, not redistributed -----------
  # The rule the gematik test suite has always been subject to applies equally to
  # the authorization server the `Connect` overlay adds: it is Keycloak's, pulled
  # from its own registry by `make resolve`, and what this repository carries is
  # the realm definition it imports - which is ours, written here, and a template
  # because the client secret and the audience are deploy-time facts.
  #
  # Two properties make that true and either can be lost quietly, so both are
  # checked:
  #
  #   1. the reference is a concrete version. A floating tag would mean a round
  #      could not state which build issued its tokens, and a token's issuer,
  #      audience, scope and lifetime are properties of that build - which is
  #      why the resolver records the digest it pulled.
  #   2. keycloak/ holds nothing but templates of ours. A theme, a provider jar
  #      or an unpacked distribution dropped in there would turn this repository
  #      into a redistribution channel for software it does not own. The archive
  #      rule above catches the packed forms; this catches the unpacked one.
  as_fails=0
  # As in the address check below: an absent value returns non-zero and is
  # reported by the emptiness test, not by the status.
  as_image="$(env_file_var .env.example AS_IMAGE || true)"
  as_version="$(env_file_var .env.example AS_VERSION || true)"
  if [[ -z "$as_image" || -z "$as_version" ]]; then
    fail "AS_IMAGE / AS_VERSION are not both set in .env.example - the authorization server must be pinned, not floating"
    as_fails=$((as_fails+1))
  elif [[ ! "$as_version" =~ ^[0-9]+(\.[0-9]+)*(-[A-Za-z0-9.]+)?$ ]]; then
    fail "AS_VERSION=${as_version} in .env.example is not a concrete version - a round could not state which build issued its tokens"
    as_fails=$((as_fails+1))
  fi
  as_vendored="$(git ls-files -- keycloak/ 2>/dev/null | grep -vE '\.(tmpl|md)$' || true)"
  if [[ -n "$as_vendored" ]]; then
    fail "keycloak/ carries files that are not templates of ours - the authorization server is pulled, never vendored:"
    printf '%s\n' "$as_vendored" | sed 's/^/        /'
    as_fails=$((as_fails+1))
  fi
  (( as_fails > 0 )) \
    || pass "the authorization server is pinned (${as_image}:${as_version}) and only its realm template is tracked"

  # --- No licence keys, and no private keys at all ------------------------
  # Section 6 already covers secrets/iris.key by name. This covers the forms a
  # key actually arrives in: the .ISCkey InterSystems issues, under whatever long
  # descriptive file name it was issued with, and the SSH/TLS private keys that
  # accumulate in a rig's working tree.
  keys="$(git ls-files -- '*.ISCkey' '*.isckey' '*.key' '*.pem' '*.p12' '*.pfx' \
                          'id_rsa*' 'id_ed25519*' 2>/dev/null || true)"
  if [[ -n "$keys" ]]; then
    fail "key material is tracked:"
    printf '%s\n' "$keys" | sed 's/^/        /'
  else
    pass "no licence key and no private key is tracked"
  fi

  # --- No measured results -------------------------------------------------
  # A round bundle is recognisable three ways and all three are checked, because
  # any one of them can be defeated by moving a directory: the path reports/, a
  # run-id directory anywhere (run-<ISO8601>), and the file names the generators
  # write. The last is the one that catches a single report pasted in "just for
  # reference", which is how this rule gets broken in practice.
  results="$(git ls-files -- reports/ 2>/dev/null || true)"
  results+="$(git ls-files 2>/dev/null | grep -E '(^|/)run-20[0-9]{6}T[0-9]{6}Z(/|$)' || true)"
  results+="$(git ls-files -- '*/analysis/results.md' '*/analysis/results.json' \
                              '*scenario-verdicts.csv' '*test-report.zip' \
                              '*debug-report.zip' 'GAP-ANALYSIS.md' \
                              'CASE-EVIDENCE.md' 2>/dev/null || true)"
  if [[ -n "$results" ]]; then
    fail "measured results are tracked - the machinery belongs here, the measurement does not:"
    printf '%s\n' "$results" | sed 's/^/        /'
  else
    pass "no round bundle, report or verdict is tracked"
  fi

  # --- The working paths stay ignored -------------------------------------
  # These are not credentials, so section 6 does not cover them; they are the
  # paths a round writes through. If one of them stopped being ignored, the next
  # `git add -A` after a round would commit several gigabytes of somebody's
  # measurement, and the operator would have no warning at all.
  hygiene_fails=0
  for path in reports/ run/ vendor/ build/ gap-analysis/index.json \
              docs/en/generated/REQUIREMENTS-Stufe5.md; do
    git check-ignore -q "$path" 2>/dev/null \
      || { fail "${path} is NOT gitignored - a round's output could be committed"; \
           hygiene_fails=$((hygiene_fails+1)); }
  done
  (( hygiene_fails > 0 )) || pass "the paths a round writes through are all gitignored"

  # --- Nothing large, except the labelled snapshot ------------------------
  # The largest hand-written file in this repository is a generator of about
  # 140 kB, so a megabyte is a wide margin rather than a tight budget. generated/
  # is exempt by design: it holds version-labelled snapshots of what the rig
  # produces, and the attribution index alone is several megabytes. Anything else
  # over the limit is a payload that arrived by accident.
  big=''
  while IFS= read -r f; do
    [[ -f "$f" ]] || continue
    case "$f" in generated/*) continue ;; esac
    sz="$(stat -c %s "$f" 2>/dev/null || echo 0)"
    (( sz > 1048576 )) && big+="${f} (${sz} bytes)"$'\n'
  done < <(git ls-files)
  if [[ -n "$big" ]]; then
    fail "tracked files outside generated/ exceed 1 MiB - is this a payload rather than source?"
    printf '%s' "$big" | sed 's/^/        /'
  else
    pass "no tracked file outside generated/ exceeds 1 MiB"
  fi

  # --- The labelled snapshots verify --------------------------------------
  # A snapshot whose checksums do not verify is worse than no snapshot: its whole
  # purpose is that a third party can diff their own render against it and trust
  # the comparison.
  snap_fails=0; snap_seen=0
  for sums in generated/*/SHA256SUMS; do
    [[ -f "$sums" ]] || continue
    snap_seen=$((snap_seen+1))
    if ( cd "$(dirname "$sums")" && sha256sum -c --quiet SHA256SUMS ) >/dev/null 2>&1; then
      note "$(dirname "$sums"): $(wc -l < "$sums") files verify"
    else
      fail "$(dirname "$sums"): SHA256SUMS does not verify"
      snap_fails=$((snap_fails+1))
    fi
  done
  if (( snap_seen == 0 )); then
    skip "no generated/*/SHA256SUMS to verify"
  elif (( snap_fails == 0 )); then
    pass "every snapshot under generated/ verifies against its own SHA256SUMS"
  fi

  # --- No deployment host baked in ----------------------------------------
  # A lab host's address is not a secret, but it is also not reproducible by
  # anyone else: a reader who finds one in here cannot tell whether it is an
  # example or something they are supposed to be able to reach.
  #
  # Scanning for IPv4 literals is NOT the way to check this, and the attempt is
  # instructive: the tracked files legitimately contain `1.2.276.0` (the root OID
  # of the German healthcare identifier namespaces), `129.6.58.42` (inside an
  # OID), and build strings of the shape `2026.2.0.221.0`. All three match any
  # dotted-quad pattern, none is an address. So the two things that can be
  # checked exactly are checked exactly instead:
  #
  #   1. the rig's own configured addresses are private, so a deployment host
  #      cannot arrive through .env.example;
  #   2. no tracked file carries a remote shell invocation, which is the form a
  #      host actually gets baked in as.
  #
  # Account names are deliberately not checked: the committed attribution index
  # records the absolute paths of the host it was generated on, verbatim and on
  # purpose, and generated/testsuite-3.0.9/README.md says so.
  host_fails=0
  for var in IRIS_IP TESTSUITE_IP AS_IP; do
    # env_file_var returns non-zero for an absent or empty value, which the
    # emptiness test below already reports with a better message, so its status
    # is deliberately ignored here.
    val="$(env_file_var .env.example "$var" || true)"
    if [[ -z "$val" ]]; then
      fail "${var} is not set in .env.example"; host_fails=$((host_fails+1))
    elif [[ ! "$val" =~ ^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.) ]]; then
      fail "${var}=${val} in .env.example is not an RFC 1918 address - the rig's subnet must be private"
      host_fails=$((host_fails+1))
    fi
  done

  remote="$(git grep -lIE '(ssh|scp|rsync)[^|;&]*[[:alnum:]_.-]+@[[:alnum:]_.:-]+' \
              -- . ':(exclude)generated' 2>/dev/null || true)"
  if [[ -n "$remote" ]]; then
    fail "tracked files carry a remote shell invocation - a deployment host must not be baked in:"
    printf '%s\n' "$remote" | sed 's/^/        /'
    host_fails=$((host_fails+1))
  fi

  (( host_fails > 0 )) || pass "the rig's subnet is private and no deployment host is baked in"

  # --- The version is a statement, not a decoration -----------------------
  # VERSION is copied into every round's provenance/rig-version.txt, so a reader
  # of a bundle resolves it against this repository's CHANGELOG to find out what
  # the rig did at the time. That only works while the two agree, and the way they
  # stop agreeing is a release that bumps one of them.
  ver_fails=0
  if [[ ! -f VERSION ]]; then
    fail "VERSION is missing - a round's provenance would record the rig as 'unrecorded'"
    ver_fails=$((ver_fails+1))
  else
    ver="$(head -n1 VERSION | tr -d '[:space:]')"
    if [[ ! "$ver" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$ ]]; then
      fail "VERSION='${ver}' is not a semantic version"
      ver_fails=$((ver_fails+1))
    elif [[ ! -f CHANGELOG.md ]]; then
      fail "CHANGELOG.md is missing - VERSION='${ver}' then documents nothing"
      ver_fails=$((ver_fails+1))
    elif ! grep -qE "^## +${ver//./\\.}( |\$)" CHANGELOG.md; then
      fail "CHANGELOG.md has no '## ${ver}' section - the released version is undocumented"
      ver_fails=$((ver_fails+1))
    fi
  fi
  (( ver_fails > 0 )) || pass "VERSION ${ver} has a CHANGELOG.md section"
fi

# -----------------------------------------------------------------------------
printf '\n%s' "$C_BOLD"
if (( FAILURES == 0 )); then
  printf '%sAll checks passed%s' "$C_GRN" "$C_RESET"
  (( SKIPPED > 0 )) && printf ' (%d skipped)' "$SKIPPED"
  printf '\n\n'
  printf 'Note: these are STATIC checks. They do not prove the environment runs -\n'
  printf 'only that it is internally consistent. Run %smake all%s for that.\n\n' "$C_BOLD" "$C_RESET"
  exit 0
else
  printf '%s%d check(s) failed%s\n\n' "$C_RED" "$FAILURES" "$C_RESET"
  exit 1
fi
