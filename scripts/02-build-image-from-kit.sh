#!/usr/bin/env bash
# =============================================================================
# Build a container image from an InterSystems installation kit
# =============================================================================
#   make image-from-kit KIT=/path/to/IRISHealth-<version>-<platform>.tar.gz \
#                       SUPERVISOR_REF=containers.intersystems.com/intersystems/irishealth:<tag>
#
# Phase 0b, beside `make provision`: it produces an input to a round, it is not
# part of one, and it is deliberately not a prerequisite of any other target.
#
# WHEN THIS IS THE RIGHT TOOL. The rig's normal path is a published image pulled
# from containers.intersystems.com - `make resolve` does that and records the
# registry digest, which is what makes a round citable. Use this script only
# when the build you have to test is not available to you as a container image
# but is available as an installation kit. An image you built yourself is one
# more variable in the measurement, and the rounds taken on it must say so.
#
# WHERE A KIT COMES FROM. downloads.intersystems.com, with your InterSystems
# credentials, is the official distribution point for installation kits; your
# account team or the WRC can tell you which kits your licence entitles you to.
# A kit you already hold on the local file system works just as well: this
# script never goes to the network for it.
#
# WHAT IT DOES NOT DO. It does not fetch, and it cannot produce, an image
# identical to a published one. See the DEVIATION notes in kit/Dockerfile.iris
# and kit/Dockerfile.webgateway: a kit ships no container supervisor, so the
# supervisor is copied from a published image of the same release line, named by
# SUPERVISOR_REF. Read those headers before quoting a round taken on the result.
#
# Everything the Dockerfiles need is read out of the kit and out of
# SUPERVISOR_REF, never guessed:
#
#   build string   <kit>/dist/csp/buildver
#   product        <kit>/.product            (e.g. IRISHealth, IRIS)
#   platform       the kit directory name's last segment (e.g. dockerubuntux64)
#   base image     ID and VERSION_ID from /etc/os-release inside SUPERVISOR_REF
#
# Variables, all optional except KIT:
#
#   KIT             kit tarball, or an already-extracted kit directory  (required)
#   COMPONENT       iris | webgateway | both                            (both)
#   SUPERVISOR_REF  published image of the same release line   (required for iris)
#   IMAGE_PREFIX    registry/namespace for the built tags             (local)
#   IMAGE_TAG       tag to apply                        (the detected build string)
#   BASE_IMAGE      override the detected base image                  (detected)
#   KIT_WORK        where to extract                      ($REPO_ROOT/run/kit-build)
#   KEEP_EXTRACT    1 to keep the extracted kit after a successful build     (unset)
# =============================================================================

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

KIT="${KIT:-}"
COMPONENT="${COMPONENT:-both}"
SUPERVISOR_REF="${SUPERVISOR_REF:-}"
IMAGE_PREFIX="${IMAGE_PREFIX:-local}"
IMAGE_TAG="${IMAGE_TAG:-}"
BASE_IMAGE="${BASE_IMAGE:-}"
KIT_WORK="${KIT_WORK:-${REPO_ROOT}/run/kit-build}"
KEEP_EXTRACT="${KEEP_EXTRACT:-}"

KIT_DIR_TEMPLATES="${REPO_ROOT}/kit"

case "$COMPONENT" in
  iris|webgateway|both) ;;
  *) die "COMPONENT must be one of: iris, webgateway, both (got: '$COMPONENT')" ;;
esac

step "Prerequisites"

need_cmd docker
need_cmd tar
need_cmd sha256sum

# RUN --mount=type=bind is a BuildKit feature. Docker has used BuildKit by
# default since 23.0, but a host with the legacy builder would fail deep inside
# the build with a syntax error about the flag, which is a confusing place to
# find out. Checked and stated here instead.
if ! docker buildx version >/dev/null 2>&1; then
  die "docker buildx not available - these Dockerfiles need BuildKit (Docker 23.0+). \
Install docker-buildx-plugin, or run \`make provision\`."
fi
export DOCKER_BUILDKIT=1

[[ -n "$KIT" ]] || die "KIT is not set - give the kit tarball or an extracted kit directory: make image-from-kit KIT=/path/to/IRISHealth-<version>-<platform>.tar.gz"
[[ -e "$KIT" ]] || die "KIT does not exist: $KIT"
KIT="$(cd "$(dirname "$KIT")" && printf '%s/%s' "$(pwd)" "$(basename "$KIT")")"

# ---------------------------------------------------------------------------
# The kit: hash it, then make a build context out of it
# ---------------------------------------------------------------------------
# The sha256 of the tarball is the only identifier of the kit that survives into
# the image, as de.intersystems.isik.kit-sha256. A round taken on a self-built
# image is only reproducible if the kit it came from can be recognised again, so
# this is recorded even though it costs a full read of a multi-gigabyte file.
#
# An already-extracted directory cannot be hashed meaningfully (the extraction
# is not reproducible byte-for-byte), so that case records "unrecorded" and says
# so rather than inventing a value.

step "Reading the kit"

KIT_FILE_LABEL=''
KIT_SHA256='unrecorded'
CLEAN_EXTRACT=''

if [[ -d "$KIT" ]]; then
  KIT_CONTEXT="$KIT"
  KIT_FILE_LABEL="$(basename "$KIT") (extracted directory)"
  warn "KIT is a directory: the kit's sha256 cannot be recorded in the image labels"
else
  KIT_FILE_LABEL="$(basename "$KIT")"
  log "hashing ${KIT_FILE_LABEL} (this reads the whole file)"
  KIT_SHA256="$(sha256sum "$KIT" | cut -d' ' -f1)"
  log "kit sha256 ${KIT_SHA256}"

  # A kit tarball extracts to exactly one top-level directory, named
  # <product>-<version>-<platform>. That inner directory - not its parent - is
  # the build context, because both Dockerfiles bind-mount the context at
  # /mnt/kit and then run ./irisinstall_silent and ./dist/csp/GatewayInstall
  # from it. Read the name rather than deriving it from the file name: the two
  # agree in every kit seen so far, but the archive is authoritative.
  log "listing the archive to find its top-level directory"
  KIT_TOP="$(tar tzf "$KIT" | head -n 200 | sed -n 's;^\([^/][^/]*\)/.*;\1;p' \
             | sort -u | head -n 2)"
  [[ -n "$KIT_TOP" ]] || die "cannot determine the kit's top-level directory from $KIT_FILE_LABEL - is it an InterSystems installation kit?"
  [[ "$(printf '%s\n' "$KIT_TOP" | wc -l)" -eq 1 ]] \
    || die "the kit archive has more than one top-level directory, which no InterSystems kit does: $(printf '%s ' $KIT_TOP)"

  KIT_CONTEXT="${KIT_WORK}/${KIT_TOP}"
  if [[ -f "${KIT_CONTEXT}/.product" ]]; then
    log "already extracted at ${KIT_CONTEXT#$REPO_ROOT/} - reusing it"
  else
    # Roughly 4 to 5 GB extracted. KIT_WORK defaults under run/, which .gitignore
    # excludes and `make clean` does not touch, so a second build of the same kit
    # skips the extraction.
    mkdir -p "$KIT_WORK"
    log "extracting to ${KIT_WORK} (several GB, a few minutes)"
    tar xzf "$KIT" -C "$KIT_WORK"
    [[ -f "${KIT_CONTEXT}/.product" ]] \
      || die "extraction did not produce ${KIT_CONTEXT}/.product - the archive is not an InterSystems installation kit"
    CLEAN_EXTRACT=1
  fi
fi

# --- What the kit says it is -------------------------------------------------
[[ -f "${KIT_CONTEXT}/dist/csp/buildver" ]] \
  || die "${KIT_CONTEXT#$REPO_ROOT/}/dist/csp/buildver is missing - this is not an InterSystems installation kit"

KIT_VERSION="$(tr -d '[:space:]' < "${KIT_CONTEXT}/dist/csp/buildver")"
[[ -n "$KIT_VERSION" ]] || die "dist/csp/buildver is empty - cannot identify the build"

KIT_PRODUCT='IRISHealth'
if [[ -s "${KIT_CONTEXT}/.product" ]]; then
  KIT_PRODUCT="$(tr -d '[:space:]' < "${KIT_CONTEXT}/.product")"
fi

# The platform is the last hyphen-separated segment of the kit directory name.
# cplatname in the kit is a platform *detection* script, not a name file, so it
# cannot simply be read; `cplatname display <name>` maps the name to its human
# form, which is what the log line below uses when it is available.
KIT_PLATFORM="${KIT_CONTEXT##*/}"
KIT_PLATFORM="${KIT_PLATFORM##*-}"

PLATFORM_DISPLAY="$KIT_PLATFORM"
if [[ -x "${KIT_CONTEXT}/cplatname" ]]; then
  PLATFORM_DISPLAY="$("${KIT_CONTEXT}/cplatname" display "$KIT_PLATFORM" 2>/dev/null \
                      || printf '%s' "$KIT_PLATFORM")"
fi

ok "kit is ${KIT_PRODUCT} ${KIT_VERSION} for ${KIT_PLATFORM} (${PLATFORM_DISPLAY})"

[[ -n "$IMAGE_TAG" ]] || IMAGE_TAG="$KIT_VERSION"

# A container kit carries dist/dev/Container/imageBuildSteps.sh, which quiesces
# the databases so that the IRIS.DAT files in the image are consistent. A plain
# server kit does not, and an image built without that step starts with
# databases that IRIS considers to have been shut down uncleanly. Refuse rather
# than produce one.
if [[ "$COMPONENT" != webgateway ]]; then
  [[ -x "${KIT_CONTEXT}/dist/dev/Container/imageBuildSteps.sh" ]] \
    || die "this kit carries no dist/dev/Container/imageBuildSteps.sh, so it is \
not a container kit (its platform is '${KIT_PLATFORM}'). Obtain the kit for a \
container platform - the names begin with 'docker' - or build only the web \
gateway: COMPONENT=webgateway"
fi

# ---------------------------------------------------------------------------
# The base image
# ---------------------------------------------------------------------------
# A kit installs onto the distribution it was built for, and getting that wrong
# is not a build failure but a subtly different product: different libc,
# different OpenSSL, different Apache. Rather than hard-code a mapping that goes
# stale, read it out of SUPERVISOR_REF - a published image of the same release
# line, which is by definition the distribution InterSystems built that line on.

if [[ "$COMPONENT" != webgateway ]]; then
  [[ -n "$SUPERVISOR_REF" ]] || die "SUPERVISOR_REF is not set. Building IRIS from a kit \
requires a published image of the same release line to take the container supervisor from \
(/tini, /iris-main, /irisHealth.sh), which no kit ships - see kit/README.md. For example: \
SUPERVISOR_REF=containers.intersystems.com/intersystems/irishealth:<tag>"
fi

if [[ -z "$BASE_IMAGE" && -n "$SUPERVISOR_REF" ]]; then
  step "Determining the base image"
  log "reading /etc/os-release from ${SUPERVISOR_REF}"
  if ! docker image inspect "$SUPERVISOR_REF" >/dev/null 2>&1; then
    log "not present locally - pulling (needs \`docker login containers.intersystems.com\`)"
    docker pull "$SUPERVISOR_REF" \
      || die "cannot pull ${SUPERVISOR_REF}. Log in to the registry, or name an image you already hold."
  fi
  _os="$(docker run --rm --entrypoint cat "$SUPERVISOR_REF" /etc/os-release 2>/dev/null || true)"
  _id="$(printf '%s\n' "$_os" | sed -n 's/^ID=\"\{0,1\}\([^\"]*\)\"\{0,1\}$/\1/p')"
  _ver="$(printf '%s\n' "$_os" | sed -n 's/^VERSION_ID=\"\{0,1\}\([^\"]*\)\"\{0,1\}$/\1/p')"
  if [[ -n "$_id" && -n "$_ver" ]]; then
    BASE_IMAGE="${_id}:${_ver}"
    ok "base image ${BASE_IMAGE}, as used by ${SUPERVISOR_REF}"
  else
    warn "could not read ID/VERSION_ID from ${SUPERVISOR_REF} - falling back to ubuntu:24.04"
    warn "if that is not the distribution this release line is built on, pass BASE_IMAGE=<image>"
    BASE_IMAGE='ubuntu:24.04'
  fi
fi
[[ -n "$BASE_IMAGE" ]] || BASE_IMAGE='ubuntu:24.04'

# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
# The Dockerfiles live outside the build context on purpose: the context is the
# kit, and the kit must not be polluted with files of ours.

# build_component <component> <tag> [component-specific --build-arg ...]
#
# The component names its own Dockerfile: there is one per component, named after
# it, and passing the path as well was stating the same fact twice. Everything
# after the tag is handed to docker build unchanged - the --build-args only that
# component needs.
build_component() {
  local what="$1" tag="$2"
  shift 2
  local dockerfile="${KIT_DIR_TEMPLATES}/Dockerfile.${what}"

  step "Building ${tag}"
  log "context ${KIT_CONTEXT}"
  log "dockerfile ${dockerfile#$REPO_ROOT/}"
  log "this installs ${KIT_PRODUCT} inside the build and takes a while - 10 to 20 minutes for IRIS"

  docker build \
    -f "$dockerfile" \
    -t "$tag" \
    --build-arg "BASE_IMAGE=${BASE_IMAGE}" \
    --build-arg "IRIS_PLATFORM=${KIT_PLATFORM}" \
    --build-arg "KIT_FILE=${KIT_FILE_LABEL}" \
    --build-arg "KIT_SHA256=${KIT_SHA256}" \
    "$@" \
    "$KIT_CONTEXT" \
    || die "the ${what} build failed. The build log above is the record; nothing was tagged."

  ok "built ${tag}"
}

IRIS_TAG=''
GATEWAY_TAG=''

if [[ "$COMPONENT" == iris || "$COMPONENT" == both ]]; then
  # The tag is lower-cased because a docker repository name may not contain
  # capitals, while .product does (IRISHealth).
  IRIS_TAG="${IMAGE_PREFIX}/$(printf '%s' "$KIT_PRODUCT" | tr '[:upper:]' '[:lower:]'):${IMAGE_TAG}"
  build_component iris "$IRIS_TAG" \
    --build-arg "SUPERVISOR_REF=${SUPERVISOR_REF}" \
    --build-arg "IRIS_VERSION=${KIT_VERSION}" \
    --build-arg "IRIS_PRODUCT=${KIT_PRODUCT}"
fi

if [[ "$COMPONENT" == webgateway || "$COMPONENT" == both ]]; then
  GATEWAY_TAG="${IMAGE_PREFIX}/webgateway:${IMAGE_TAG}"
  build_component webgateway "$GATEWAY_TAG" \
    --build-arg "GATEWAY_VERSION=${KIT_VERSION}"
fi

# ---------------------------------------------------------------------------
# Hand over
# ---------------------------------------------------------------------------
if [[ -n "$CLEAN_EXTRACT" && -z "$KEEP_EXTRACT" ]]; then
  step "Cleaning up"
  log "removing the extracted kit at ${KIT_CONTEXT#$REPO_ROOT/} (KEEP_EXTRACT=1 keeps it)"
  rm -rf "$KIT_CONTEXT"
fi

step "Built"

# A locally built image has no registry digest - only a local content ID, which
# is what the resolver records for an image it did not pull. Printed here so the
# operator can write it down before anything retags or rebuilds.
for t in "$IRIS_TAG" "$GATEWAY_TAG"; do
  [[ -n "$t" ]] || continue
  printf '  %-48s %s\n' "$t" "$(docker image inspect --format '{{.Id}}' "$t")" >&2
done

cat >&2 <<EOF

To test on these images, set in .env:

  MODE=pinned
EOF

[[ -z "$IRIS_TAG" ]] || cat >&2 <<EOF
  IRIS_IMAGE=${IRIS_TAG%:*}
  IRIS_VERSION=${IRIS_TAG##*:}
EOF

[[ -z "$GATEWAY_TAG" ]] || cat >&2 <<EOF
  WEBGATEWAY_IMAGE=${GATEWAY_TAG%:*}
  WEBGATEWAY_VERSION=${GATEWAY_TAG##*:}
EOF

cat >&2 <<EOF

then \`make resolve\`, which will find these images locally, record them as
side-loaded, and note the image ID in .env.resolved in place of a registry
digest. \`make all\` then takes the round.

Two things any report on that round has to say, because they are true:

  * the image was built from a kit by this rig, not published by InterSystems.
    Both images carry de.intersystems.isik.not-published-by-intersystems=true.
  * the container supervisor came from ${SUPERVISOR_REF:-a published image}, not
    from the kit. See the DEVIATION note in kit/Dockerfile.iris.
EOF
