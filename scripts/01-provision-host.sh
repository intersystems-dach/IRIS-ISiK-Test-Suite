#!/usr/bin/env bash
# =============================================================================
# 01-provision-host.sh - install this rig's prerequisites on a pristine host
# =============================================================================
# The one script in this repository that changes anything OUTSIDE the repository
# tree. It exists because 00-preflight.sh begins by requiring Docker, Compose v2,
# unzip and PyYAML, and a freshly provisioned Ubuntu 24.04 image has none of the
# first three - so there is a gap between "the operator has a VM" and "the operator
# can run make preflight" that the documentation used to leave to the reader.
#
# Deliberately NOT part of `make all`. It runs once per host, needs sudo, and its
# last step requires the caller to start a new login shell, which no script can do
# on the caller's behalf.
#
# Idempotent by construction: every step tests for its own result first, so a
# second run reports what is already in place and changes nothing. That matters
# more than it sounds - the docker-group step below sends the operator away to log
# in again, and the natural next move on returning is to run this script once more
# to check that it worked.
#
# Installs:
#   apt: unzip make ca-certificates curl gnupg git python3-yaml jq
#   Docker Engine + CLI + containerd + buildx + compose plugin, from Docker's own
#   apt repository (Ubuntu's docker.io predates Compose v2, which this rig needs)
#
# Exactly this set was installed and verified on the two Ubuntu 24.04 lab hosts
# these instructions were measured on.
# =============================================================================

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

step "Provisioning host prerequisites"

# --- 0. Preconditions --------------------------------------------------------
# apt-get is the hard requirement; the Ubuntu version is not. Docker publishes
# packages for every current Debian and Ubuntu release and the rig has no
# distribution-specific content of its own, so a Debian-family host that is not
# 24.04 gets a warning and proceeds. Anything without apt-get has to be provisioned
# by hand, and saying so is more useful than a half-completed run.
need_cmd apt-get "this script provisions Debian-family hosts; on any other distribution install the packages listed in its header by hand, then run 'make preflight'"
need_cmd sudo    "the packages below install system-wide"

OS_NAME='unknown'; OS_VER='unknown'; VERSION_CODENAME=''
if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091  # not present at check time on a non-Linux host
  . /etc/os-release
  OS_NAME="${ID:-unknown}"; OS_VER="${VERSION_ID:-unknown}"
  VERSION_CODENAME="${VERSION_CODENAME:-${UBUNTU_CODENAME:-}}"
fi

if [[ "$OS_NAME" == ubuntu && "$OS_VER" == 24.04 ]]; then
  ok "host is Ubuntu ${OS_VER} (${VERSION_CODENAME:-?}), the platform these instructions were measured on"
else
  warn "host is ${OS_NAME} ${OS_VER}, not Ubuntu 24.04. Proceeding: nothing here is version-specific, but the measured figures in INSTALL.md were taken on 24.04"
fi

[[ -n "$VERSION_CODENAME" ]] \
  || die "/etc/os-release names no VERSION_CODENAME - Docker's apt repository is per-release and the codename cannot be guessed. Add the repository by hand (https://docs.docker.com/engine/install/)"

# One sudo prompt at the start rather than one per step, so an operator watching the
# run is not surprised by a password prompt several minutes in.
sudo -v || die "sudo authentication failed"

# --- 1. Base packages --------------------------------------------------------
# `make` is here because the Makefile is the documented interface, `unzip` because
# both the refv plugins and the official report archives are zip files, and
# python3-yaml because every contract, plugin config and rendered Tiger file this
# rig reads is YAML. git, curl and jq are usually present on a cloud image already;
# naming them costs nothing and removes an assumption from the instructions.
APT_PKGS=(unzip make ca-certificates curl gnupg git python3-yaml jq)

MISSING_PKGS=()
for p in "${APT_PKGS[@]}"; do
  dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q 'ok installed' || MISSING_PKGS+=("$p")
done

if (( ${#MISSING_PKGS[@]} == 0 )); then
  ok "base packages already installed: ${APT_PKGS[*]}"
else
  log "installing: ${MISSING_PKGS[*]}"
  sudo apt-get update -qq || die "apt-get update failed - check the host's network and apt sources"
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${MISSING_PKGS[@]}" \
    || die "failed to install: ${MISSING_PKGS[*]}"
  ok "installed ${#MISSING_PKGS[@]} base package(s)"
fi

# --- 2. Docker's apt repository ----------------------------------------------
# Ubuntu's own docker.io package is not usable here: this rig requires Compose v2
# as the `docker compose` subcommand, and needs the long-form depends_on/condition
# syntax and compose profiles that the legacy docker-compose script does not
# support. Docker's repository is the supported way to get the compose plugin.
DOCKER_KEYRING=/etc/apt/keyrings/docker.asc
DOCKER_LIST=/etc/apt/sources.list.d/docker.list

if [[ -s "$DOCKER_KEYRING" ]]; then
  ok "Docker apt keyring already present at ${DOCKER_KEYRING}"
else
  log "fetching Docker's apt signing key"
  sudo install -m 0755 -d /etc/apt/keyrings
  # The key is fetched over TLS and installed 0644 because apt reads it as _apt.
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | sudo tee "$DOCKER_KEYRING" >/dev/null \
    || die "could not fetch Docker's signing key from download.docker.com - the host needs outbound HTTPS"
  sudo chmod a+r "$DOCKER_KEYRING"
  ok "installed ${DOCKER_KEYRING}"
fi

DOCKER_REPO="deb [arch=$(dpkg --print-architecture) signed-by=${DOCKER_KEYRING}] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable"

if [[ -f "$DOCKER_LIST" ]] && grep -qF "download.docker.com/linux/ubuntu ${VERSION_CODENAME}" "$DOCKER_LIST"; then
  ok "Docker apt repository already configured for ${VERSION_CODENAME}"
else
  printf '%s\n' "$DOCKER_REPO" | sudo tee "$DOCKER_LIST" >/dev/null \
    || die "could not write ${DOCKER_LIST}"
  ok "wrote ${DOCKER_LIST}"
fi

# --- 3. Docker Engine --------------------------------------------------------
if docker compose version >/dev/null 2>&1; then
  ok "Docker Engine with Compose v2 already installed ($(docker --version 2>/dev/null | head -n1))"
else
  log "installing Docker Engine, CLI, containerd, buildx and the compose plugin"
  sudo apt-get update -qq || die "apt-get update failed after adding Docker's repository"
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
      docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin \
    || die "failed to install Docker Engine - see the apt output above"
  ok "installed Docker Engine"
fi

# --- 4. Daemon and group membership -----------------------------------------
# `enable --now` rather than `start`: a lab host is rebooted at least once between
# provisioning and a round often enough that a daemon which does not come back is a
# real failure mode, and it costs nothing to rule out.
if systemctl is-enabled --quiet docker 2>/dev/null && systemctl is-active --quiet docker 2>/dev/null; then
  ok "docker.service is enabled and running"
else
  sudo systemctl enable --now docker || die "could not enable/start docker.service - check 'systemctl status docker'"
  ok "docker.service enabled and started"
fi

# SUDO_USER is the operator when this script is run through sudo; otherwise it is
# the current user. Root is refused a group it already outranks, and adding root to
# the docker group would be meaningless rather than harmless-looking.
TARGET_USER="${SUDO_USER:-$(id -un)}"
if [[ "$TARGET_USER" == root ]]; then
  warn "running as root with no SUDO_USER - no account was added to the 'docker' group. Add the operator's account by hand: sudo usermod -aG docker <user>"
  GROUP_ADDED=false
elif id -nG "$TARGET_USER" 2>/dev/null | tr ' ' '\n' | grep -qx docker; then
  ok "user '${TARGET_USER}' is already in the 'docker' group"
  GROUP_ADDED=false
else
  sudo usermod -aG docker "$TARGET_USER" || die "could not add '${TARGET_USER}' to the 'docker' group"
  ok "added '${TARGET_USER}' to the 'docker' group"
  GROUP_ADDED=true
fi

# --- 5. Verify ---------------------------------------------------------------
# Verifying is the point of the last step: an operator who is told "installed" and
# then hits a permission error two commands later has no way to tell which of the
# five steps above did not take.
step "Verifying"

need_cmd docker "installation reported success but the docker CLI is not on PATH"
need_cmd unzip
need_cmd make
need_cmd python3

docker compose version >/dev/null 2>&1 \
  || die "'docker compose' (Compose v2) is not available even after installing docker-compose-plugin"
ok "docker $(docker --version | sed 's/^Docker version //'), compose $(docker compose version --short)"

python3 -c 'import yaml, sys; sys.stdout.write(yaml.__version__)' >/dev/null 2>&1 \
  || die "python3 cannot import yaml even after installing python3-yaml"
ok "python3 $(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:3])))') with PyYAML $(python3 -c 'import yaml; print(yaml.__version__)')"

# The daemon socket check is separated from the CLI check on purpose, because this
# is the one that fails for a reason the operator has to act on rather than fix.
if docker info >/dev/null 2>&1; then
  ok "the Docker daemon is reachable as '${TARGET_USER}'"
  SOCKET_OK=true
else
  SOCKET_OK=false
fi

step "Provisioning complete"

if [[ "$SOCKET_OK" != true ]]; then
  # Group membership is established at login. The running shell's credentials were
  # fixed when it started and no amount of usermod changes them, so this is not a
  # failure - it is the one step that cannot be automated for the caller.
  warn "the Docker daemon is not yet reachable as '${TARGET_USER}' - group membership takes effect at next login"
  printf '\n  Start a new login session, then continue:\n\n' >&2
  printf '    exit          # and reconnect, or:  newgrp docker\n' >&2
  printf '    cd %s\n' "${REPO_ROOT}" >&2
  printf '    docker info >/dev/null && echo ok\n\n' >&2
  printf '  Re-running this script after logging in is safe and confirms the result.\n\n' >&2
elif [[ "$GROUP_ADDED" == true ]]; then
  warn "'${TARGET_USER}' was added to the 'docker' group in this run; log out and back in before relying on it in other shells"
fi

printf '  Next: the two per-installation secrets, then the round. INSTALL.md sections 5-8:\n\n' >&2
printf '    cp .env.example .env\n' >&2
printf '    cp /path/to/your/iris.key secrets/\n' >&2
printf '    docker login containers.intersystems.com\n' >&2
printf '    make check && make all\n\n' >&2
