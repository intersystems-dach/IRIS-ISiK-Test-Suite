#!/usr/bin/env bash
# =============================================================================
# 20-fetch-packages.sh - acquire the FHIR packages the SUT must be loaded with
# =============================================================================
# Version parity is the whole point of this step: the packages loaded into IRIS
# must be exactly the ones the Gematik reference validator inside the test suite
# validates responses against. Anything else produces failures that are artefacts
# of a version skew rather than genuine IRIS gaps - the single worst outcome for
# a gap-analysis exercise.
#
# The authoritative list therefore always comes from the test suite image itself:
# /app/refv-plugins/*.zip, each containing a config.yaml that names one
# `validation.dependencyLists[].fhirPackage` plus its `dependencies`, and the
# matching .tgz payloads.
#
# PACKAGE_SOURCE only decides where the payload bytes come from:
#   refv        use the .tgz files bundled in the image (default, offline)
#   simplifier  download the same name@version from packages.simplifier.net and
#               additionally record a bundled-vs-published sha256 parity table
#
# The same throwaway container also yields the suite's own sources, which several
# later steps need and which must come from the pinned image rather than from a
# separately downloaded tarball - otherwise the feature files the gap analysis
# reasons about are not provably the ones that ran.
#
# Outputs, all under vendor/:
#   vendor/refv-plugins/<module>/config.yaml   extracted plugin descriptors
#   vendor/packages/<name>#<version>/          unpacked package dir (package.json
#                                              at its root - what NpmLoader wants)
#   vendor/packages/MANIFEST.txt               name, version, sha256, source
#   vendor/packages/PARITY.txt                 only when PACKAGE_SOURCE=simplifier
#   vendor/package-plan.json                   per-endpoint ordered package list,
#                                              consumed by the IRIS bootstrap and
#                                              by the documentation generators
#   vendor/upstream/features/**                the suite's feature files, read by
#                                              gap-analysis/build_index.py
#   vendor/upstream/pom.xml                    tag expressions + testFailureIgnore
#
# and, fetched from the release tag rather than the image, because the image does
# not carry them (see fetch_upstream_sources):
#   vendor/upstream/testdata/**                the suite's own test-data contract,
#                                              used by 30-render-config.sh to prove
#                                              our copies are byte-identical
#   vendor/upstream/tiger-isik-stufe{3,5}.yaml the official Tiger configuration,
#                                              the diff baseline for tiger/*.tmpl
#   vendor/upstream/serenity.properties        Serenity settings baseline
# =============================================================================

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

step "Acquiring FHIR packages"

load_env
require_var TESTSUITE_IMAGE TESTSUITE_VERSION IRIS_NS_STUFE3 IRIS_NS_STUFE5 \
            FHIR_BASE_STUFE3 FHIR_BASE_STUFE5
PACKAGE_SOURCE="${PACKAGE_SOURCE:-refv}"

TESTSUITE_REF="${TESTSUITE_IMAGE}:${TESTSUITE_VERSION}"
PLUGINS_DIR="${VENDOR_DIR}/refv-plugins"
TGZ_DIR="${VENDOR_DIR}/tgz"
UPSTREAM_DIR="${VENDOR_DIR}/upstream"

# -----------------------------------------------------------------------------
# 1. Extract refv-plugins from the test suite image
# -----------------------------------------------------------------------------
# `docker create` + `docker cp` rather than `docker run`: no need to start the
# JVM, and it works even if the image's entrypoint would immediately try to run
# the suite. The container is created and removed without ever executing.
#
# One container, several extractions: creating it is the slow part, and taking
# everything from the same container guarantees that the plugin descriptors, the
# feature files and the upstream test-data contract all come from one image.
extract_from_image() {
  local cid
  log "extracting suite artefacts from ${TESTSUITE_REF}"
  cid="$(docker create "$TESTSUITE_REF" 2>/dev/null)" \
    || die "cannot create a container from ${TESTSUITE_REF} - is the image pulled? run 'make resolve'"
  # shellcheck disable=SC2064  # cid must be expanded now, not at trap time
  trap "docker rm -f '$cid' >/dev/null 2>&1 || true" RETURN

  # --- a) refv-plugins: the authoritative package list -----------------------
  rm -rf "$PLUGINS_DIR"
  mkdir -p "$PLUGINS_DIR/_zips"
  docker cp "${cid}:/app/refv-plugins/." "${PLUGINS_DIR}/_zips/" 2>/dev/null \
    || die "no /app/refv-plugins in ${TESTSUITE_REF} - the image layout changed; check the upstream release notes for ${TESTSUITE_VERSION}"

  local n=0 zip base
  shopt -s nullglob
  for zip in "${PLUGINS_DIR}/_zips"/*.zip; do
    # valmodule-isik3-basismodul-refv-plugin.zip -> isik3-basismodul
    base="$(basename "$zip" .zip)"
    base="${base#valmodule-}"; base="${base%-refv-plugin}"
    mkdir -p "${PLUGINS_DIR}/${base}"
    # config.yaml is what we need on disk; the .tgz payloads are only extracted
    # when PACKAGE_SOURCE=refv (below), to avoid ~80 MB of pointless I/O.
    unzip -o -q "$zip" 'config.yaml' -d "${PLUGINS_DIR}/${base}" \
      || die "no config.yaml inside ${zip##*/}"
    n=$((n+1))
  done
  shopt -u nullglob

  (( n > 0 )) || die "no refv-plugin zips found in ${TESTSUITE_REF}"
  ok "extracted ${n} validation-module descriptors"

  # --- b) the suite's own sources --------------------------------------------
  # Taken from the image rather than from a GitHub tarball on purpose. The gap
  # analysis quotes feature-file lines as the definition of what was tested; if
  # those lines came from a separately downloaded copy, nothing would prove they
  # are the lines the pinned image actually executed.
  rm -rf "$UPSTREAM_DIR"
  mkdir -p "$UPSTREAM_DIR"

  docker cp "${cid}:/app/src/test/resources/features/." "${UPSTREAM_DIR}/features/" 2>/dev/null \
    || die "no /app/src/test/resources/features in ${TESTSUITE_REF} - the image layout changed"

  # /app/pom.xml is the only other artefact the image carries; see
  # fetch_upstream_sources() for the ones it does not.
  docker cp "${cid}:/app/pom.xml" "${UPSTREAM_DIR}/pom.xml" 2>/dev/null \
    || warn "could not extract /app/pom.xml - the tag-expression baseline will be missing"

  local nfeat
  nfeat="$(find "${UPSTREAM_DIR}/features" -name '*.feature' | wc -l | tr -d ' ')"
  (( nfeat > 0 )) || die "extracted no feature files from ${TESTSUITE_REF}"
  ok "extracted ${nfeat} feature files from the image"
}

# --- c) the artefacts the image does not carry -------------------------------
# Verified against gematik1/isik-testsuite:2.7.0: /app holds only downloadDeps.sh,
# pom.xml, refv-plugins/, report/, src/ and target/. There is no /app/testdata and
# no tiger-isik-stufe{3,5}.yaml - both are operator-supplied at run time, which is
# exactly why this project renders them from tiger/*.tmpl and mounts them in.
#
# The test-data contract is not optional, though: 30-render-config.sh proves our
# staged copies are byte-identical to the official release, and that proof is what
# lets an inspector accept the contract as unmodified. So it is fetched from the
# release tag in the suite's own git repository - the same source the image was
# built from, pinned to the same version, over TLS.
#
# The distinction from the feature files is deliberate. Feature files must come
# from the image because the gap analysis quotes their lines as the definition of
# what was executed. The contract files are compared for equality, not quoted, so
# fetching them from the tag proves the same thing: our copy equals the release.
fetch_upstream_sources() {
  need_cmd curl
  local repo="${TESTSUITE_REPO:-gematik/isik-testsuite}"
  local raw="https://raw.githubusercontent.com/${repo}/${TESTSUITE_VERSION}"
  local f n=0

  log "fetching the test-data contract from ${repo}@${TESTSUITE_VERSION} (not shipped in the image)"
  local lvl
  for lvl in $(suite_levels); do mkdir -p "${UPSTREAM_DIR}/testdata/${lvl}"; done
  for f in $(suite_contract_files); do
    curl -fsSL --retry 3 --retry-delay 2 \
         -o "${UPSTREAM_DIR}/testdata/${f}" "${raw}/testdata/${f}" \
      || die "cannot fetch testdata/${f} from ${repo}@${TESTSUITE_VERSION}
  Without it the test-data parity check cannot run, and an inspector has no proof
  that the staged contract equals the official release. Check network access to
  raw.githubusercontent.com, or that TESTSUITE_VERSION names an existing tag."
    n=$((n+1))
  done
  ok "fetched ${n} upstream test-data contract files"

  # Documentation baselines only: the diff targets for tiger/*.tmpl and the
  # Serenity settings. Nothing downstream breaks without them.
  for f in $(suite_tiger_baselines); do
    curl -fsSL --retry 2 -o "${UPSTREAM_DIR}/${f}" "${raw}/${f}" \
      || warn "could not fetch ${f} - documentation baselines will be incomplete"
  done
}

extract_from_image
fetch_upstream_sources

# -----------------------------------------------------------------------------
# 2. Build the package plan (which packages, per endpoint)
# -----------------------------------------------------------------------------
# Reads every config.yaml and emits vendor/package-plan.json: for each ISiK level,
# the set of packages that the corresponding IRIS endpoint must be loaded with.
# Ordering is added in step 6, once the package.json dependency declarations are
# available on disk.
log "building the package plan from the plugin descriptors"
python3 "${SCRIPTS_DIR}/lib/plan_packages.py" \
    --plugins-dir "$PLUGINS_DIR" \
    --out "${VENDOR_DIR}/package-plan.json" \
    --stufe3-namespace "$IRIS_NS_STUFE3" \
    --stufe3-endpoint  "$FHIR_BASE_STUFE3" \
    --stufe5-namespace "$IRIS_NS_STUFE5" \
    --stufe5-endpoint  "$FHIR_BASE_STUFE5" \
    --levels "$(suite_levels_lc)" \
    --testsuite "${TESTSUITE_REF}" \
  || die "package planning failed - see the message above"

# -----------------------------------------------------------------------------
# 3. Collect the payloads
# -----------------------------------------------------------------------------
rm -rf "$TGZ_DIR"; mkdir -p "$TGZ_DIR"

collect_from_refv() {
  log "unpacking bundled package payloads"
  local zip n=0
  shopt -s nullglob
  for zip in "${PLUGINS_DIR}/_zips"/*.zip; do
    # Flatten package/<file>.tgz into one pool: the same name@version appears in
    # several plugins (de.basisprofil.r4#1.4.0 is in four of them) and they are
    # byte-identical, so a single pool avoids unpacking each one repeatedly.
    unzip -o -q -j "$zip" 'package/*.tgz' -d "$TGZ_DIR" || true
  done
  shopt -u nullglob
  n="$(find "$TGZ_DIR" -name '*.tgz' | wc -l | tr -d ' ')"
  (( n > 0 )) || die "no package/*.tgz payloads inside the refv plugins"
  ok "collected ${n} distinct package tarballs from the image"
}

collect_from_simplifier() {
  need_cmd curl
  local registry="${SIMPLIFIER_REGISTRY:-https://packages.simplifier.net}"
  log "downloading package payloads from ${registry}"

  # The plan's own package list is the download list, so the simplifier route can
  # never pull a version the validator does not use.
  local total=0 failed=0
  while IFS='#' read -r name version; do
    [[ -n "$name" ]] || continue
    total=$((total+1))
    local out="${TGZ_DIR}/${name}-${version}.tgz"
    # Simplifier serves NPM-style: <registry>/<name>/<version>
    if ! curl -fsSL --retry 3 --retry-delay 2 -o "$out" "${registry}/${name}/${version}"; then
      warn "download failed: ${name}#${version}"
      rm -f "$out"
      failed=$((failed+1))
    fi
  done < <(python3 -c '
import json, sys
plan = json.load(open(sys.argv[1]))
for pkg in plan["allPackages"]:
    print(pkg["id"])
' "${VENDOR_DIR}/package-plan.json")

  if (( failed )); then
    # Falling back rather than failing: the bundled copies are the authoritative
    # ones anyway, and a Simplifier outage should not block a test round. The
    # substitution is recorded in PARITY.txt so it cannot pass unnoticed.
    #
    # The gaps are filled PER PACKAGE, not by re-running collect_from_refv, and the
    # difference is the whole value of the parity table. collect_from_refv unpacks
    # every bundled payload with `unzip -o`, which overwrites the pool wholesale -
    # so a single failed download replaced all 34 successful ones with bundled
    # copies, and PARITY.txt then compared the bundled set against itself and
    # reported MATCH for all of them. Every row was true and the table as a whole
    # was worthless, which is the most expensive kind of wrong: it reads as
    # independent corroboration that the published packages are the validated ones.
    #
    # Observed on the official 2026.2 round with suite 2.7.0, where
    # de.gematik.isik3-dokumentenaustausch-terminology#1.0.0 is not published on
    # Simplifier at all (404 on both the tarball and the package metadata), so the
    # simplifier route can never come back clean for this suite version and the
    # fallback is the normal path rather than an outage.
    warn "${failed}/${total} packages could not be downloaded; filling those gaps from the bundled copies"
    local filled=0 name version out
    while IFS='#' read -r name version; do
      [[ -n "$name" ]] || continue
      out="${TGZ_DIR}/${name}-${version}.tgz"
      [[ -f "$out" ]] && continue
      # Extract just this one payload from whichever plugin zip carries it. The
      # bundled filenames follow <name>-<version>.tgz, which is what the pool is
      # keyed by, so a targeted `unzip -j` cannot touch a downloaded neighbour.
      local zip
      shopt -s nullglob
      for zip in "${PLUGINS_DIR}/_zips"/*.zip; do
        unzip -o -q -j "$zip" "package/${name}-${version}.tgz" -d "$TGZ_DIR" 2>/dev/null && break
      done
      shopt -u nullglob
      [[ -f "$out" ]] \
        || die "${name}#${version} could not be downloaded from ${registry} and no bundled copy exists in the image either.
  The package plan requires it, so there is nothing to load it from. Re-run with
  PACKAGE_SOURCE=refv if the registry is unreachable, or check the package plan."
      filled=$((filled+1))
      # Recorded so PARITY.txt can mark this row BUNDLED. Without it the row would
      # read MATCH - the bytes really are identical to the bundled copy, because
      # they ARE the bundled copy - and a substitution would be indistinguishable
      # from independent corroboration.
      SUBSTITUTED+=("${name}-${version}.tgz")
      log "filled from the image: ${name}#${version}"
    done < <(python3 -c '
import json, sys
plan = json.load(open(sys.argv[1]))
for pkg in plan["allPackages"]:
    print(pkg["id"])
' "${VENDOR_DIR}/package-plan.json")
    ok "downloaded $(( total - failed ))/${total} from ${registry}, filled ${filled} from the image"
  else
    ok "downloaded ${total} packages from ${registry}"
  fi
}

# Filenames the simplifier route could not download and filled from the image
# instead. Declared before the dispatch so PARITY.txt can read it either way.
SUBSTITUTED=()

case "$PACKAGE_SOURCE" in
  refv)       collect_from_refv ;;
  simplifier) collect_from_simplifier ;;
  *)          die "PACKAGE_SOURCE must be 'refv' or 'simplifier' (got '${PACKAGE_SOURCE}')" ;;
esac

# -----------------------------------------------------------------------------
# 3b. Payloads the image does not carry but IRIS cannot import without
# -----------------------------------------------------------------------------
# One class of package is named in the plan yet has no payload in either source
# above, and the reason is a property of IRIS rather than a choice of ours.
#
# plan_packages.py promotes a package out of `snapshotDependencies` and into the
# load plan when a package the endpoint *does* load declares it in its own
# `dependencies` - currently hl7.terminology.r4#5.3.0, declared by
# ihe.formatcode.fhir#1.2.0. HS.FHIRMeta.Load.NpmLoader resolves every declared
# dependency with an exact-id lookup and aborts the whole import when one is
# absent ("ERROR #5809: Object to Load not found"), so it must be loaded even
# though the reference validator only uses it to build snapshots and therefore
# ships no .tgz for it in the refv plugins. It is not in the test suite image at
# all - verified by searching every plugin zip and the image filesystem.
#
# Consequence, stated rather than hidden: this one package always comes from the
# package registry, so PACKAGE_SOURCE=refv is not fully offline. It is recorded in
# the plan under `snapshotDependenciesPromoted`, called out in the MANIFEST header,
# and reported by the results analysis. There is deliberately no fallback: with the
# package missing the import fails inside IRIS after the namespace has been created,
# which is a far worse failure than stopping here with the reason named.
#
# A second class joined it at test suite 3.0.9, for a different reason and with the
# same consequence. `hl7.fhir.r4.core#4.0.1` is added to every level implicitly
# (see plan_packages.py, "The base FHIR R4 package"): the validator always has core
# R4 and so does not pin it, but an IRIS endpoint's package list must contain it.
# Under 2.7.0 that addition cost nothing, because the `isik3-basismodul` plugin
# named core R4 explicitly and therefore bundled its payload, which the pool is
# shared across. Release 3.0.0 removed ISiK Level 3, so the only plugin left is
# `isik5`, whose dependency list starts at `de.basisprofil.r4#1.5.4` - and with it
# went the only bundled copy of core R4 in the image. The package is still required
# and still has to come from somewhere, so it is fetched here, by the same route,
# with the same archive verification, and recorded separately because the reason it
# has no bundled payload is not the same reason.
fetch_promoted_packages() {
  local ids
  ids="$(python3 -c '
import json, sys
plan = json.load(open(sys.argv[1]))
seen = set()
for p in plan.get("snapshotDependenciesPromoted", []):
    seen.add(p["id"]); print(p["id"], "promoted")
# Packages the planner added itself. Only these: a package named by a plugin and
# absent from the image is not a case to paper over with a download, and still
# fails the load-order gate below with its name.
for p in plan.get("allPackages", []):
    if p.get("implicit") and p["id"] not in seen:
        print(p["id"], "implicit")
' "${VENDOR_DIR}/package-plan.json")"
  [[ -n "$ids" ]] || return 0

  need_cmd curl
  local registry="${SIMPLIFIER_REGISTRY:-https://packages.simplifier.net}"

  # What the pool already holds, keyed name#version read from each archive's own
  # package.json - the filenames are ambiguous (dvmd.kdl.r4.2022-2022.1.2.tgz).
  local -A pool=()
  local tgz meta pname pversion
  shopt -s nullglob
  for tgz in "$TGZ_DIR"/*.tgz; do
    meta="$(tar -xzOf "$tgz" package/package.json 2>/dev/null \
      | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["name"], d["version"])' 2>/dev/null || true)"
    [[ -n "$meta" ]] || continue
    read -r pname pversion <<<"$meta"
    pool["${pname}#${pversion}"]=1
  done
  shopt -u nullglob

  local id kind name version out why
  while read -r id kind; do
    [[ -n "$id" ]] || continue
    name="${id%%#*}"; version="${id#*#}"
    if [[ "$kind" == implicit ]]; then
      why="This package is not optional and has no bundled copy: the reference validator
  plugins for this suite version do not name it, so the image carries no payload for
  it, while an IRIS endpoint cannot be built without the base FHIR R4 definitions."
    else
      why="This package is not optional and has no bundled copy. A package the ${PACKAGE_SOURCE}
  route does load declares it as a dependency, and HS.FHIRMeta.Load.NpmLoader
  resolves declared dependencies by exact id: without it the IRIS import aborts with
  ERROR #5809 after the namespace has been created."
    fi
    if [[ -n "${pool[$id]:-}" ]]; then
      log "${kind} dependency ${id} is already in the payload pool"
      continue
    fi
    log "fetching ${kind} dependency ${id} from ${registry} (no payload in the image)"
    out="${TGZ_DIR}/${name}-${version}.tgz"
    curl -fsSL --retry 3 --retry-delay 2 -o "$out" "${registry}/${name}/${version}" \
      || { rm -f "$out"; die "cannot fetch ${id} from ${registry}
  ${why} Check network access to
  ${registry}, then re-run 'make packages'."; }
    # A 200 with the wrong content would surface much later as a mis-named package
    # directory, so the archive is checked here against what was asked for.
    meta="$(tar -xzOf "$out" package/package.json 2>/dev/null \
      | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["name"], d["version"])' 2>/dev/null || true)"
    [[ "$meta" == "${name} ${version}" ]] \
      || { rm -f "$out"; die "${registry}/${name}/${version} served '${meta:-nothing readable}', not ${name} ${version}"; }
    if [[ "$kind" == implicit ]]; then
      IMPLICIT_FETCHED+=("$id")
    else
      PROMOTED_FETCHED+=("$id")
    fi
    ok "fetched ${id} ($(sha256_of "$out"))"
  done <<<"$ids"
}

PROMOTED_FETCHED=()
IMPLICIT_FETCHED=()
fetch_promoted_packages

# -----------------------------------------------------------------------------
# 4. Unpack into directories
# -----------------------------------------------------------------------------
# HS.FHIRMeta.Load.NpmLoader.importPackages() takes a %List of DIRECTORIES, each
# containing package.json at its root - not .tgz archives. FHIR NPM tarballs wrap
# everything in a top-level `package/` member, so that level is stripped.
#
# Directory naming is <name>#<version>, matching the `name#version` notation used
# by the refv config.yaml files and by the FHIR package registry, so the directory
# listing is directly comparable to the plan and to the documentation.
log "unpacking tarballs into package directories"
# The parity artefacts survive the wipe. They are evidence ABOUT the packages, not
# about which payloads this round loaded, and the documented remedy for a content
# difference is exactly to re-run with PACKAGE_SOURCE=refv while keeping the
# simplifier table as separate evidence (docs/en/02-setup.md). Wiping it here would
# destroy the very finding that motivated the re-run. Carried over with a stamp
# saying so, so nobody reads it as a statement about this round's payloads.
PARITY_CARRIED=''
if [[ "$PACKAGE_SOURCE" != simplifier && -f "${PACKAGES_DIR}/PARITY.txt" ]]; then
  PARITY_CARRIED="$(mktemp -d)"
  for f in PARITY.txt PARITY-DETAIL.txt parity-content.json; do
    [[ -f "${PACKAGES_DIR}/${f}" ]] && cp "${PACKAGES_DIR}/${f}" "${PARITY_CARRIED}/"
  done
fi
rm -rf "$PACKAGES_DIR"; mkdir -p "$PACKAGES_DIR"
if [[ -n "$PARITY_CARRIED" ]]; then
  cp "$PARITY_CARRIED"/* "$PACKAGES_DIR"/
  {
    printf '\n# ---------------------------------------------------------------------------\n'
    printf '# CARRIED OVER from an earlier PACKAGE_SOURCE=simplifier pass, and re-stamped\n'
    printf '# %s by a PACKAGE_SOURCE=%s run.\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$PACKAGE_SOURCE"
    printf '# The rows above compare the bundled payloads against the published ones and\n'
    printf '# remain true. They do NOT describe what this round loaded: this round used\n'
    printf '# the %s payloads. MANIFEST.txt is the record of what was loaded.\n' "$PACKAGE_SOURCE"
  } >>"${PACKAGES_DIR}/PARITY.txt"
  rm -rf "$PARITY_CARRIED"
  ok "carried the bundled-vs-published parity table over from the previous pass"
fi

unpacked=0
for tgz in "$TGZ_DIR"/*.tgz; do
  [[ -e "$tgz" ]] || die "no tarballs to unpack in ${TGZ_DIR#$REPO_ROOT/}"

  # Read name+version from the archive's own package.json rather than parsing the
  # filename: a filename like dvmd.kdl.r4.2022-2022.1.2.tgz is ambiguous about
  # where the name ends and the version begins.
  meta="$(tar -xzOf "$tgz" package/package.json 2>/dev/null \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["name"], d["version"])' 2>/dev/null || true)"
  if [[ -z "$meta" ]]; then
    warn "skipping ${tgz##*/}: no readable package/package.json"
    continue
  fi
  read -r pname pversion <<<"$meta"

  dest="${PACKAGES_DIR}/${pname}#${pversion}"
  mkdir -p "$dest"
  # --strip-components=1 turns package/package.json into ./package.json.
  tar -xzf "$tgz" -C "$dest" --strip-components=1 \
    || die "cannot unpack ${tgz##*/}"
  [[ -f "${dest}/package.json" ]] \
    || die "${dest#$REPO_ROOT/} has no package.json at its root - NpmLoader would reject it"
  unpacked=$((unpacked+1))
done
ok "unpacked ${unpacked} package directories into vendor/packages/"

# -----------------------------------------------------------------------------
# 5. Manifest + parity evidence
# -----------------------------------------------------------------------------
# MANIFEST.txt is cited by the results documentation: it is how an inspector
# checks that the packages on the SUT are the published ones.
MANIFEST="${PACKAGES_DIR}/MANIFEST.txt"
{
  printf '# FHIR package manifest for this test round\n'
  printf '# generated %s by scripts/20-fetch-packages.sh\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '#\n'
  printf '# Package list source: %s refv-plugins (authoritative - these are the\n' "$TESTSUITE_REF"
  printf '#   packages the Gematik reference validator validates against)\n'
  printf '# Payload source:      %s\n' "$PACKAGE_SOURCE"
  # An override reached this pass on the command line rather than from the
  # configuration file. Stamped here because the manifest is where the payload
  # claim is made: without it, a manifest produced by an overridden pass would be
  # indistinguishable from one the round's own .env.resolved describes.
  if [[ -n "${ENV_OVERRIDES:-}" ]]; then
    printf '# Overridden:          %s (command line, not %s)\n' \
      "$ENV_OVERRIDES" "$(basename "${ENV_LOADED_FROM:-.env}")"
  fi
  # Named in the manifest and not only in the plan, because the manifest is the
  # document an inspector reads to establish where the bytes came from. Under
  # PACKAGE_SOURCE=refv every other line here comes from the pinned image; these
  # ones cannot, and the exception has to be visible at the point of the claim.
  if (( ${#PROMOTED_FETCHED[@]} )); then
    printf '#\n'
    printf '# Exception: the package(s) below have no payload in the test suite image and\n'
    printf '#   were fetched from %s regardless of PACKAGE_SOURCE. They are\n' "${SIMPLIFIER_REGISTRY:-https://packages.simplifier.net}"
    printf '#   listed by the reference validator only under snapshotDependencies, but a\n'
    printf '#   package the endpoint loads declares them, and IRIS resolves declared\n'
    printf '#   dependencies by exact id - see snapshotDependenciesPromoted in\n'
    printf '#   vendor/package-plan.json.\n'
    for id in "${PROMOTED_FETCHED[@]}"; do
      printf '#     %s\n' "$id"
    done
  fi
  # The same exception for a different reason, kept apart from the one above so the
  # manifest states why each payload could not come from the image.
  if (( ${#IMPLICIT_FETCHED[@]} )); then
    printf '#\n'
    printf '# Exception: the package(s) below have no payload in the test suite image and\n'
    printf '#   were fetched from %s regardless of PACKAGE_SOURCE. They are\n' "${SIMPLIFIER_REGISTRY:-https://packages.simplifier.net}"
    printf '#   not named by any reference-validator plugin of this suite version - the\n'
    printf '#   validator has the base FHIR R4 definitions and does not pin them - while\n'
    printf '#   an IRIS endpoint package list must contain them. Added by the planner and\n'
    printf '#   marked `implicit: true` in vendor/package-plan.json.\n'
    for id in "${IMPLICIT_FETCHED[@]}"; do
      printf '#     %s\n' "$id"
    done
  fi
  printf '#\n'
  printf '# %-56s %-12s %s\n' 'PACKAGE' 'VERSION' 'SHA256(tarball)'
  for tgz in "$TGZ_DIR"/*.tgz; do
    [[ -e "$tgz" ]] || continue
    meta="$(tar -xzOf "$tgz" package/package.json 2>/dev/null \
      | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["name"], d["version"])' 2>/dev/null || true)"
    [[ -n "$meta" ]] || continue
    read -r pname pversion <<<"$meta"
    printf '  %-56s %-12s %s\n' "$pname" "$pversion" "$(sha256_of "$tgz")"
  done | sort
} >"$MANIFEST"
ok "wrote vendor/packages/MANIFEST.txt ($(grep -c '^  ' "$MANIFEST") packages)"

if [[ "$PACKAGE_SOURCE" == simplifier ]]; then
  # Independent evidence that the packages bundled in the test suite image are
  # the ones published on Simplifier. Re-extracts the bundled payloads to a
  # scratch pool so the two sets can be hashed side by side.
  log "recording bundled-vs-published parity"
  BUNDLED_DIR="${VENDOR_DIR}/tgz-bundled"
  rm -rf "$BUNDLED_DIR"; mkdir -p "$BUNDLED_DIR"
  shopt -s nullglob
  for zip in "${PLUGINS_DIR}/_zips"/*.zip; do
    unzip -o -q -j "$zip" 'package/*.tgz' -d "$BUNDLED_DIR" || true
  done
  shopt -u nullglob

  # Content comparison, for every package present on both sides. Byte equality is
  # the wrong test on its own and the 2026.2 round proved it: 33 of 34 packages
  # came back DIFFER, and not one of them was a discrepancy. FHIR tarballs embed
  # timestamps, the two producers serialise JSON differently, and - the big one -
  # the IG publisher ships SNAPSHOT-generated StructureDefinitions while Simplifier
  # serves the DIFFERENTIAL-only form, 342 KB against 20 KB for the same profile.
  # A table where "differs" is the normal answer cannot be used as a gate, so the
  # classification asks what actually matters: do the definitions agree?
  PARITY_JSON="${PACKAGES_DIR}/parity-content.json"
  declare -A CONTENT_VERDICT=()
  PARITY_DETAIL="${PACKAGES_DIR}/PARITY-DETAIL.txt"
  while IFS=$'\t' read -r verdict fname; do
    [[ -n "$fname" ]] && CONTENT_VERDICT["$fname"]="$verdict"
  done < <(python3 "${SCRIPTS_DIR}/lib/package_parity.py" \
             --bundled "$BUNDLED_DIR" --published "$TGZ_DIR" \
             --json "$PARITY_JSON" 2>"$PARITY_DETAIL")

  # Classify first, write second. The row loop must not live inside a `| sort`
  # pipeline: that runs it in a subshell, where the counters increment and are then
  # discarded, so the summary line would report zeros while the rows above it were
  # correct - a footer that contradicts its own table.
  PARITY_ROWS=''
  n_match=0; n_equiv=0; n_differ=0; n_bundled=0; n_absent=0
  for tgz in "$BUNDLED_DIR"/*.tgz; do
    [[ -e "$tgz" ]] || continue
    name="$(basename "$tgz")"
    used="${TGZ_DIR}/${name}"
    substituted=0
    for s in ${SUBSTITUTED[@]+"${SUBSTITUTED[@]}"}; do
      [[ "$s" == "$name" ]] && { substituted=1; break; }
    done
    if [[ ! -f "$used" ]]; then
      status=ABSENT;  n_absent=$((n_absent+1))
    elif (( substituted )); then
      status=BUNDLED; n_bundled=$((n_bundled+1))
    elif [[ "$(sha256_of "$tgz")" == "$(sha256_of "$used")" ]]; then
      status=MATCH;   n_match=$((n_match+1))
    else
      # Bytes differ. The content verdict decides whether that means anything.
      case "${CONTENT_VERDICT[$name]:-differ}" in
        equal|snapshot) status=EQUIV;  n_equiv=$((n_equiv+1)) ;;
        *)              status=DIFFER; n_differ=$((n_differ+1)) ;;
      esac
    fi
    PARITY_ROWS+="$(printf '  %-8s %s' "$status" "$name")"$'\n'
  done
  N_BUNDLED_TOTAL="$(find "$BUNDLED_DIR" -name '*.tgz' | wc -l | tr -d ' ')"

  PARITY="${PACKAGES_DIR}/PARITY.txt"
  {
    printf '# Bundled (test suite image) vs published (Simplifier) package parity\n'
    printf '# generated %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    if [[ -n "${ENV_OVERRIDES:-}" ]]; then
      printf '# by a pass overridden on the command line: %s\n' "$ENV_OVERRIDES"
    fi
    printf '#\n'
    printf '# MATCH    identical bytes - the published package is the validated one\n'
    printf '# EQUIV    different bytes, SAME DEFINITIONS. Expect this to be the normal\n'
    printf '#          answer: tarballs embed timestamps, the two producers serialise\n'
    printf '#          JSON differently, and the image ships snapshot-generated\n'
    printf '#          StructureDefinitions where Simplifier serves differential-only\n'
    printf '#          ones. A snapshot is derived from the differential, so a profile\n'
    printf '#          that agrees except for its presence is the same profile.\n'
    printf '#          Equally good evidence as MATCH. See PARITY-DETAIL.txt.\n'
    printf '# DIFFER   same name@version, and the definitions themselves disagree.\n'
    printf '#          This is the only status that needs a decision. The exact fields\n'
    printf '#          are listed in PARITY-DETAIL.txt.\n'
    printf '# BUNDLED  NOT independent evidence: the download failed and the bundled\n'
    printf '#          copy was used, so there is nothing to compare against. Such a\n'
    printf '#          row would otherwise read MATCH, because the bytes ARE the\n'
    printf '#          bundled bytes.\n'
    printf '# REGISTRY no bundled copy exists to compare against - the image does not\n'
    printf '#          ship this package (see the MANIFEST.txt header)\n#\n'
    if (( n_bundled )); then
      printf '# %d of %d package(s) below are BUNDLED, so this table corroborates parity\n' \
        "$n_bundled" "$N_BUNDLED_TOTAL"
      printf '# for the %d MATCH + %d EQUIV row(s) only. Read the summary at the end.\n#\n' \
        "$n_match" "$n_equiv"
    fi
    if (( n_differ )); then
      printf '# %d package(s) below are DIFFER: the definitions disagree, not just the\n' "$n_differ"
      printf '# bytes. PARITY-DETAIL.txt names the fields. The SUT is loaded with the\n'
      printf '# PUBLISHED copies under PACKAGE_SOURCE=simplifier - re-run with\n'
      printf '# PACKAGE_SOURCE=refv if the round must use what the validator ships.\n#\n'
    fi
    printf '# %-8s %-56s %s\n' 'STATUS' 'PACKAGE' 'VERSION'
    # Both fetch classes belong here: REGISTRY means "no bundled copy exists to
    # compare against", which is true of each for its own reason.
    for id in "${PROMOTED_FETCHED[@]}" "${IMPLICIT_FETCHED[@]}"; do
      printf '  %-8s %s\n' 'REGISTRY' "$id"
    done
    printf '%s' "$PARITY_ROWS" | sort -k2
    printf '#\n# summary: %d MATCH, %d EQUIV, %d DIFFER, %d BUNDLED, %d ABSENT, %d REGISTRY\n' \
      "$n_match" "$n_equiv" "$n_differ" "$n_bundled" "$n_absent" \
      "$(( ${#PROMOTED_FETCHED[@]} + ${#IMPLICIT_FETCHED[@]} ))"
    printf '# corroborated (MATCH + EQUIV): %d of %d comparable package(s)\n' \
      "$(( n_match + n_equiv ))" "$(( n_match + n_equiv + n_differ ))"
  } >"$PARITY"
  rm -rf "$BUNDLED_DIR"
  if (( n_differ )); then
    warn "${n_differ} package(s) differ in content, not just in bytes - see vendor/packages/PARITY-DETAIL.txt"
  fi
  ok "wrote vendor/packages/PARITY.txt ($(( n_match + n_equiv )) corroborated, ${n_differ} differing, ${n_bundled} not compared)"
fi

# -----------------------------------------------------------------------------
# 6. Resolve load order and verify the plan is satisfiable
# -----------------------------------------------------------------------------
# Two jobs, both needing the unpacked package.json files:
#
#   a) Every package the plan names must exist on disk as a directory. Catching a
#      gap here rather than inside IRIS is the difference between a one-line
#      error and a half-installed namespace.
#   b) Topologically order each endpoint's package list. NpmLoader requires a
#      package's dependencies to be present - already imported, or earlier in the
#      same importPackages() call - and only package.json states them.
#
# The ordered lists are written back into package-plan.json as `loadOrder`.
log "resolving load order"
python3 "${SCRIPTS_DIR}/lib/order_packages.py" \
    --plan "${VENDOR_DIR}/package-plan.json" \
    --packages-dir "$PACKAGES_DIR" \
  || die "the package plan cannot be satisfied - see above"
ok "package plan satisfied and ordered"

step "FHIR packages ready"
