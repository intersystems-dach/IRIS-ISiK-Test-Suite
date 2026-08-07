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
#   4. the placeholders the templates use are exactly the ones the renderer
#      substitutes, in both directions
#   5. the test-data contract: no undefined ${data.*} keys, no empty values, and
#      the requirement extractor runs clean over all 220 feature files
#  5b. the fabricated test data itself: it renders, it satisfies every assertion
#      the feature files make about it, it carries every reference a search
#      scenario filters on, and it can be ordered for seeding. These are the same
#      three checks `make seed` runs, repeated here because they need neither a
#      container nor a licence key
#   6. cross-file consistency: Makefile targets have scripts, compose mounts have
#      renderers, .env.example defines what the scripts read
#   7. `docker compose config` validates, if docker is available
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
python3 - <<'PY'
import pathlib, sys

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
if fails:
    sys.exit(1)
noun, verb = ("class", "carries") if checked == 1 else ("classes", "carry")
print(f"  PASS  {checked} ObjectScript {noun} {verb} no line-split macro invocation")
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
for p in sorted(pathlib.Path("tiger").glob("*.tmpl")):
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
print(f"  PASS  {len(paths)} YAML files parse; both Tiger templates render to valid YAML")
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
# renderer knows. There are two render functions with deliberately disjoint
# substitution tables - render() for Tiger's YAML, render_gateway() for the Web
# Gateway's INI and Apache config - and pooling them would accept a Tiger placeholder
# in an Apache file, and vice versa, which is exactly the mix-up the separation
# exists to prevent.
python3 - <<'PY'
import pathlib, re, sys

renderer = pathlib.Path("scripts/30-render-config.sh").read_text(encoding="utf-8")

# Each render function's SUBST dict is the authority for what that family can
# substitute. Split on the function definitions so the two tables stay apart; the
# order of the definitions in the file is what defines the boundary.
bounds = [(m.group(1), m.start()) for m in re.finditer(r"^(render|render_gateway)\(\) \{", renderer, re.M)]
if len(bounds) != 2:
    print(f"  FAIL  expected render() and render_gateway() in scripts/30-render-config.sh, found {len(bounds)}")
    sys.exit(1)

tables = {}
for i, (name, start) in enumerate(bounds):
    end = bounds[i + 1][1] if i + 1 < len(bounds) else len(renderer)
    tables[name] = set(re.findall(r'"([A-Z0-9_]+)":\s*os\.environ\[', renderer[start:end]))
    if not tables[name]:
        print(f"  FAIL  could not find the SUBST table inside {name}() in scripts/30-render-config.sh")
        sys.exit(1)

# Which template directory each function renders.
FAMILIES = [("tiger", "render"), ("webgateway", "render_gateway")]

fails = 0
total = 0
for directory, func in FAMILIES:
    known = tables[func]
    used = set()
    templates = sorted(pathlib.Path(directory).glob("*.tmpl"))
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
print(f"  PASS  {total} placeholders across 2 template families, renderers and templates agree")
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

for p in sorted(pathlib.Path("tiger").glob("*.tmpl")):
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
if [[ ! -d vendor/upstream/features ]]; then
  skip "vendor/upstream/features absent - undefined-key check needs 'make packages' first"
  # The value check does not need the features and is always worth doing.
  python3 - <<'PY'
import pathlib, sys
import yaml

fails, total, upstream_empty = 0, 0, []
for p in sorted(pathlib.Path("testdata").glob("Stufe*/*.yaml")):
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
  python3 - <<'PY'
import os, pathlib, sys
import yaml

# The same Gherkin reader the requirement extractor uses, so "in scope" means the
# same thing in this check as it does in the generated documentation. Two
# implementations of tag scoping would eventually disagree, and the disagreement
# would show up as a check that passes while the documentation says otherwise.
sys.path.insert(0, "scripts/lib")
import feature_parser as fp

fails = 0
for stufe in ("Stufe3", "Stufe5"):
    defined = {}
    in_scope = {}
    for p in sorted(pathlib.Path("testdata", stufe).glob("*.yaml")):
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        for k, v in data.items():
            defined[k] = (p.name, v)
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
    # inheritance (feature_parser.parse_tag_expression / count_cases), not by
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
        skipped = fp.count_cases(feature, predicate) == 0
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
        print(f"  FAIL  {stufe}: ${{data.{k}}} used at {in_scope[k][0]} but defined nowhere in testdata/{stufe}/")
        fails += 1
    unused_oos = sorted(k for k in used
                        if k not in in_scope and k not in defined and k not in runtime)
    print(f"  {'PASS' if not missing else 'FAIL'}  {stufe}: {len(defined)} keys defined, "
          f"{len(used)} referenced, {len(runtime)} set at run time, {len(missing)} unresolved")
    for k in unused_oos:
        print(f"        not required: ${{data.{k}}} is used only by features that "
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
  if err="$(python3 scripts/lib/extract_requirements.py \
              --features-root vendor/upstream/features \
              --testdata-root testdata \
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
if [[ ! -f vendor/requirements.json ]]; then
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
  if out="$(python3 scripts/lib/build_testdata.py \
              --packages "$CHECK_PACKAGES" --out "$BUILD_TMP" 2>&1)"; then
    pass "test data renders for both levels"
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

  for level in Stufe3 Stufe5; do
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

  if [[ -d "${BUILD_TMP}/Stufe3" || -d "${BUILD_TMP}/Stufe5" ]]; then
    # Tag expressions come from the environment if set, exactly as the extractor
    # does it, so a narrowed debugging scope is checked at the scope it will run.
    if out="$(python3 scripts/lib/search_reference_check.py \
                --data-root "$BUILD_TMP" \
                --tags-stufe3 "${TESTS_TO_RUN_STUFE3:-@Stufe3 and (not @Optional)}" \
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
  if [[ -d "${BUILD_TMP}/Stufe3" || -d "${BUILD_TMP}/Stufe5" ]]; then
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
  for level in Stufe3 Stufe5; do
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
python3 - <<'PY'
import pathlib, re, sys

compose = pathlib.Path("compose/testsuite.yml").read_text(encoding="utf-8")
mounted = set(re.findall(r"- \./run/(tiger/[A-Za-z0-9._/-]+\.yaml):", compose))
renderer = pathlib.Path("scripts/30-render-config.sh").read_text(encoding="utf-8")

fails = 0

# The renderer builds its output paths from a loop variable, so there is no
# literal list of them in the code to compare against. Its header block does list
# them, and that block is what the operator and the documentation both read - so
# checking against it verifies the two things that can drift: the header going
# stale, and a mount pointing at a file nothing produces.
declared = set(re.findall(r"#   (run/tiger/[A-Za-z0-9._/-]+\.yaml)", renderer))
declared = {d[len("run/"):] for d in declared}
if not declared:
    print("  FAIL  scripts/30-render-config.sh header lists no run/tiger/*.yaml outputs")
    sys.exit(1)
only_mounted = mounted - declared
only_declared = declared - mounted
for m in sorted(only_mounted):
    print(f"  FAIL  compose mounts run/{m} but scripts/30-render-config.sh does not list it as an output")
    fails += 1
for d in sorted(only_declared):
    print(f"  FAIL  scripts/30-render-config.sh writes run/{d} but no compose service mounts it")
    fails += 1
if fails:
    sys.exit(1)
print(f"  PASS  {len(mounted)} rendered files, compose mounts and renderer outputs agree")

# The same invariant for the Web Gateway's two rendered files, which the base compose
# file mounts rather than compose/testsuite.yml. Worth its own check: these two are
# what makes the SUT answer HTTP at all, so a mount pointing at a file the renderer
# does not write means every scenario in the round fails with a connection error.
base = pathlib.Path("docker-compose.yml").read_text(encoding="utf-8")
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
    "MANIFEST", "PARITY", "BUNDLED_DIR", "PROMOTED_FETCHED",
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
    # Locals of the preflight memory check. TESTSUITE_HEAP_MAX, which that check
    # reads, IS operator-facing and is documented in .env.example.
    "MEM_KB", "MEM_GIB",
    "PORT_CLASH", "EXISTING_NET", "KEY", "C_RESET", "C_BOLD", "C_RED", "C_GRN",
    # 00-preflight.sh's licence-key locals. IRIS_KEY_PATH is exported for the compose
    # mount rather than authored: preflight resolves the operator's file
    # case-insensitively, so its path is measured, not configured. KEY_MODE /
    # KEY_OWNER / NEW_MODE are the observed and post-fix permissions.
    "IRIS_KEY_PATH", "KEY_MODE", "KEY_OWNER", "NEW_MODE",
    "C_YEL", "C_DIM", "FAILURES", "SKIPPED",
    "LIB", "BUILD_DIR", "BUILD_TMP", "SEED_DIR", "SEED_LOG", "CHECK_PACKAGES",
    # 60-run-tests.sh. TESTS_TO_RUN is derived from the documented
    # TESTS_TO_RUN_STUFE3/5 and exported for compose, so it is an internal name
    # even though the container reads it as its own setting.
    "STUFE", "LEVEL", "VOLUME", "ARCHIVE_DIR", "OFFICIAL_DIR", "RUN_TIMEOUT",
    "TESTS_TO_RUN", "OFFICIAL_SCOPE", "LOG_PID", "EXIT_CODE",
    # 70-collect-report.sh
    "RUN_REPORT_DIR", "ANALYSIS_DIR", "PROV_DIR",
    # 80-gap-report.sh
    "GAP_DIR", "GAP_INDEX", "GAP_RUN_DIR", "GAP_RESULTS", "GAP_FEATURES",
    "GAP_MD", "GAP_JSON", "GAP_MANIFEST", "GAP_TMP",
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
             "R_TESTSUITE_LATEST"}
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
  for path in secrets/sut.password secrets/iris-mgmt.password secrets/iris.key \
              .env .env.resolved run/CREDENTIALS.md; do
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
  if out="$(SUT_PASSWORD=checkonly IRIS_MGMT_PASSWORD=checkonly docker compose \
              --env-file .env.example \
              -f docker-compose.yml -f compose/testsuite.yml \
              config 2>&1)"; then
    pass "docker compose config validates both files"
    # The static addresses are the whole basis of the isolation claim, so confirm
    # they survive interpolation rather than trusting the file.
    for ip in "$(grep -E '^IRIS_IP=' .env.example | cut -d= -f2)" \
              "$(grep -E '^TESTSUITE_IP=' .env.example | cut -d= -f2)"; do
      printf '%s' "$out" | grep -q "$ip" \
        || fail "the resolved compose configuration does not contain the static address ${ip}"
    done
    note "services: $(printf '%s' "$out" | python3 -c '
import sys, yaml
print(", ".join(sorted((yaml.safe_load(sys.stdin) or {}).get("services", {}))))' 2>/dev/null || echo '?')"
  else
    fail "docker compose config rejected the configuration:"
    printf '%s\n' "$out" | sed 's/^/        /' | head -n 20
  fi
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
