# =============================================================================
# ISiK conformance test environment - orchestration
# =============================================================================
# Every target is a thin wrapper over a script in scripts/. The logic lives
# there so that it is inspectable, `bash -n`-checkable, and runnable by hand on
# a host where make is unavailable.
#
# Quick start on a fresh host:
#
#     make provision                # Docker + tools; needs sudo, once per host
#                                   # then start a new login shell (docker group)
#     cp .env.example .env          # edit if needed
#     cp /path/to/iris.key secrets/ # see secrets/README.md
#     docker login containers.intersystems.com
#     make all
#
# Read `make help` for the full target list, and INSTALL.md for the from-scratch
# path including hardware sizing and the two per-installation choices.
# =============================================================================

SHELL := /bin/bash
# -e: fail fast. -u: an unset variable is a bug, not an empty string.
# -o pipefail: a failure inside a pipeline must not be masked by a later `tee`.
#
# --norc --noprofile: recipe shells must not read the host's startup files. Two
# reasons, one cosmetic and one real. On Debian and Ubuntu, /etc/bash.bashrc opens
# with `[ -z "$PS1" ] && return`, which is an unset-variable error under -u, so a
# recipe shell that reads it prints "PS1: unbound variable" - observed on the
# Ubuntu 24.04 deployment host when make was started detached with its stdin still
# attached to a socket. More importantly, a round must not be influenced by
# whatever aliases, functions, PATH edits or exported variables the operator's
# profile happens to set: what the recipe does is then not reproducible on another
# host, which is the one property this rig cannot give up.
.SHELLFLAGS := --norc --noprofile -eu -o pipefail -c
.DEFAULT_GOAL := help

# Absolute repository root. Scripts are invoked with this exported so they never
# depend on the caller's working directory - the environment is deployed on a
# different machine and may be driven from anywhere.
REPO_ROOT := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
export REPO_ROOT

SCRIPTS := $(REPO_ROOT)/scripts

# --- Configuration ----------------------------------------------------------
# .env is the operator's deploy-time configuration; .env.resolved is written by
# `make resolve` and additionally records image digests and the resolved FHIR
# package list. Targets after `resolve` read .env.resolved, so that a run cannot
# silently drift when someone edits .env mid-round.
ENV_FILE          := $(REPO_ROOT)/.env
ENV_RESOLVED_FILE := $(REPO_ROOT)/.env.resolved

# MODE may be overridden per invocation (`MODE=manual make manual`), which is
# why it is not simply read from .env here: the command line must win.
#
# These two are handled by the resolver, which rewrites their lines when it writes
# .env.resolved. The run-behaviour knobs - TESTSUITE_HEAP_MAX, TESTSUITE_TIMEOUT,
# IRIS_BOOTSTRAP_TIMEOUT, MANUAL_STUFE - are overridable too, but through
# RUN_OVERRIDABLE in scripts/lib.sh rather than here: they need no make variable,
# since an environment assignment on the command line is already exported to the
# recipe. Nothing else may be overridden per invocation, because .env.resolved is
# the round's provenance record.
MODE ?=
PACKAGE_SOURCE ?=
export MODE PACKAGE_SOURCE

# Compose is always invoked with both files from the repository root, because
# compose/testsuite.yml uses project-root-relative bind mounts.
COMPOSE := docker compose \
	--env-file $(ENV_RESOLVED_FILE) \
	-f $(REPO_ROOT)/docker-compose.yml \
	-f $(REPO_ROOT)/compose/testsuite.yml

# The authorization server overlay, for a round that asked for one. Evaluated by
# asking scripts/lib.sh rather than by reading .env here, so that `make clean`
# tears down exactly what compose() brought up and the two cannot disagree about
# what CONNECT_ENABLE=true means. Deferred (=, not :=) so it costs nothing on an
# invocation that does not use it, and empty on any failure - a round without the
# overlay then behaves exactly as it did before the overlay existed.
CONNECT_OVERLAY = $(shell . '$(SCRIPTS)/lib.sh' >/dev/null 2>&1 \
	&& load_env >/dev/null 2>&1 \
	&& connect_enabled >/dev/null 2>&1 \
	&& printf -- '-f %s/%s' '$(REPO_ROOT)' "$$CONNECT_COMPOSE_FILE")

# A single run id shared by every target in one `make all` invocation, so the
# Stufe 3 and Stufe 5 reports of one round land in the same reports/ directory.
# Recursively expanded (=) would produce a different timestamp per use, so it is
# computed once (:=) here and passed down explicitly.
RUN_ID ?= run-$(shell date -u +%Y%m%dT%H%M%SZ)
export RUN_ID

.PHONY: help provision image-from-kit preflight resolve packages up seed \
        tokens test test-stufe3 test-stufe5 test-connect report gap-report \
        case-evidence compare manual all status credentials logs shell-iris \
        check clean destroy

# -----------------------------------------------------------------------------
help: ## Show this help
	@printf '\nISiK conformance test environment\n\n'
	@printf 'Usage: make <target> [MODE=latest|pinned|manual] [PACKAGE_SOURCE=refv|simplifier]\n\n'
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[1m%-16s\033[0m %s\n", $$1, $$2}'
	@printf '\nMODE defaults to the value in .env (latest) and governs the unit under test\n'
	@printf 'only; the test suite version is pinned separately. Command line wins.\n\n'

# --- Phase 0: the host itself -----------------------------------------------
# Deliberately not a prerequisite of anything. It needs sudo, it runs once per
# host, and its last step sends the operator away to start a new login shell -
# none of which belongs in the dependency chain of a test round.

provision: ## Install this rig's prerequisites on a pristine host (needs sudo; run once)
	@$(SCRIPTS)/01-provision-host.sh

# --- Phase 0b: an image from an installation kit -----------------------------
# Also not a prerequisite of anything, and for the same reason: it produces an
# INPUT to a round rather than a step of one. The normal path is a published
# image pulled by `make resolve`; this exists for the case where the build that
# must be tested is available as a kit and not as an image. Read kit/README.md
# before quoting a round taken on the result - a self-built image is not a
# published one, and the reports have to say so.
#
# KIT and SUPERVISOR_REF are passed through rather than read from .env: they
# describe one build, not the deployment, and nothing about a round may depend
# on them after the image exists.

image-from-kit: ## Build an IRIS/Web Gateway image from an installation kit (KIT=... SUPERVISOR_REF=...)
	@KIT='$(KIT)' SUPERVISOR_REF='$(SUPERVISOR_REF)' COMPONENT='$(COMPONENT)' \
	 IMAGE_PREFIX='$(IMAGE_PREFIX)' IMAGE_TAG='$(IMAGE_TAG)' BASE_IMAGE='$(BASE_IMAGE)' \
	 $(SCRIPTS)/02-build-image-from-kit.sh

# --- Phase 1: prepare -------------------------------------------------------

preflight: ## Check host prerequisites (docker, compose v2, licence key, registry login, free ports)
	@$(SCRIPTS)/00-preflight.sh

resolve: preflight ## Resolve versions + image digests -> .env.resolved (provenance record)
	@$(SCRIPTS)/10-resolve-versions.sh

packages: resolve ## Fetch + unpack the FHIR packages the suite validates against
	@$(SCRIPTS)/20-fetch-packages.sh
	@# The test-data specification is derived from the feature files the pinned
	@# image just yielded: 221 @Precondition prose blocks become the requirement
	@# tables and the resource inventory, plus vendor/requirements.json, which
	@# `make seed` validates its work list against.
	@$(SCRIPTS)/25-extract-requirements.sh
	@# The gap-analysis index is built here, while the feature files and the
	@# unpacked packages are both present: it maps scenario -> validation module
	@# -> supplying FHIR package. Building it now keeps `make gap-report` a pure
	@# join, with no network or image access.
	@$(REPO_ROOT)/gap-analysis/build_index.py

# --- Phase 2: bring up the unit under test ----------------------------------

up: packages ## Start IRIS, install both endpoints, wait until healthy
	@# Before the configuration is rendered: the rendered virtual host names the
	@# certificate files, and the authorization server will not start without
	@# them. A no-op unless CONNECT_ENABLE=true.
	@$(SCRIPTS)/22-make-tls.sh
	@$(SCRIPTS)/30-render-config.sh
	@$(SCRIPTS)/40-up.sh

seed: up ## Load the fabricated test data into both endpoints and $$validate it
	@$(SCRIPTS)/50-seed.sh

# --- Phase 3: test ----------------------------------------------------------
# A failing conformance run is the EXPECTED outcome of this project (IRIS is
# known not to be fully ISiK compliant), so these targets deliberately succeed
# when tests fail. 60-run-tests.sh exits non-zero only on a harness error -
# principally a missing report zip.

test-stufe3: seed ## Run the ISiK Stufe 3 conformance suite
	@$(SCRIPTS)/60-run-tests.sh stufe3

test-stufe5: seed ## Run the ISiK Stufe 5 conformance suite
	@$(SCRIPTS)/60-run-tests.sh stufe5

# --- the Connect module, on real access tokens ------------------------------
# A second invocation of Stufe 5, restricted to the one module that presents a
# bearer token per request. The complete Stufe 5 run above is unchanged and
# remains the round's submission artefact; this one is the evidence that the
# module's tokens were actually validated. See the header of
# scripts/60-run-tests.sh.
#
# Both targets refuse to do anything useful with CONNECT_ENABLE=false, which is
# the default: there is no authorization server to mint against.

tokens: up ## Mint this round's ISiK Connect access tokens into the staged test data
	@$(SCRIPTS)/55-mint-tokens.sh

# `seed tokens`, in that order: seeding takes minutes and a token lives an hour,
# so the tokens are obtained last, immediately before the run that presents
# them. 60-run-tests.sh re-checks their remaining life before it starts the
# container and refuses a run that cannot finish inside it.
test-connect: seed tokens ## Run the ISiK Stufe 5 Connect module with real bearer tokens
	@$(SCRIPTS)/60-run-tests.sh connect

# Every level the pinned suite carries, sequentially - they share the report
# volume, which is why they are serialised and why each archives before the next
# starts. The levels come from suite_levels() in scripts/lib.sh rather than being
# listed here, because release 3.0.0 removed ISiK Level 3: under a 3.x suite
# `60-run-tests.sh stufe3` would start a container whose tag expression selects no
# scenario, and an empty report is the one outcome that must not be produced
# silently in a round whose product is a statement about the SUT.
#
# A round with CONNECT_ENABLE=true appends one further invocation after the
# levels, not instead of one of them: the complete scope has already run and is
# archived, and this adds the same level's Connect module a second time on real
# access tokens. It runs last so that the tokens are minted as late as possible,
# and the scripts are called directly rather than through `make test-connect` so
# that the token lifetime is not spent re-walking the preparation chain.
test: seed ## Run every level the pinned suite carries, sequentially
	@set -e; \
	levels="$$(. scripts/lib.sh; load_env; suite_levels)"; \
	[ -n "$$levels" ] || { echo "make test: suite_levels named no level - see above" >&2; exit 1; }; \
	for level in $$levels; do \
	  $(SCRIPTS)/60-run-tests.sh "$$(echo "$$level" | tr 'A-Z' 'a-z')"; \
	done; \
	if (. scripts/lib.sh >/dev/null 2>&1; load_env >/dev/null 2>&1; connect_enabled); then \
	  $(SCRIPTS)/55-mint-tokens.sh; \
	  $(SCRIPTS)/60-run-tests.sh connect; \
	fi

# --- Phase 4: evidence ------------------------------------------------------

report: ## Extract the official reports verbatim + generate the cited results analysis
	@$(SCRIPTS)/70-collect-report.sh
	@$(MAKE) --no-print-directory gap-report
	@$(MAKE) --no-print-directory case-evidence

gap-report: ## Generate the standalone developer gap analysis (English, per failure)
	@$(SCRIPTS)/80-gap-report.sh

# Chained after gap-report rather than offered only on demand, because its value
# is comparative: an annex that exists for one round and not for the round before
# it cannot be diffed, which is most of what it is for. It reads the gap analysis,
# so it must run after it, and it needs no container, so it costs seconds.
case-evidence: ## Generate the per-case evidence annex (input, expected, received, repro)
	@$(SCRIPTS)/81-case-evidence.sh

# Not part of `make all`: a comparison needs a second round, and which round
# that is, is a judgement about what the figure is meant to show (the previous
# release, the previous suite version, the previous rig fix) rather than
# something this Makefile can pick.
compare: ## Join two rounds: BASELINE=<run-id|path> [CANDIDATE=<run-id|path>]
	@$(SCRIPTS)/85-compare-rounds.sh

# --- Composite --------------------------------------------------------------

all: ## Full round: preflight -> resolve -> packages -> up -> seed -> test -> report
	@$(MAKE) --no-print-directory test RUN_ID=$(RUN_ID)
	@$(MAKE) --no-print-directory report RUN_ID=$(RUN_ID)

manual: ## MODE=manual: bring up + seed, start the WorkflowUI, do not auto-run
	@$(MAKE) --no-print-directory seed MODE=manual
	@$(SCRIPTS)/90-manual.sh

# --- Inspection -------------------------------------------------------------

status: ## Show container, network and endpoint state
	@$(SCRIPTS)/91-status.sh

credentials: ## Print this instance's credentials (documented on purpose - lab environment)
	@$(SCRIPTS)/92-credentials.sh

logs: ## Tail the IRIS bootstrap log
	@tail -n 200 -F $(REPO_ROOT)/run/iris-state/bootstrap.log

shell-iris: ## Open an IRIS terminal session in the SUT container
	@docker exec -it $${COMPOSE_PROJECT_NAME:-isik}-iris iris session iris

check: ## Static self-check of this repository (syntax, YAML, compose config)
	@$(SCRIPTS)/99-check.sh

# --- Teardown ---------------------------------------------------------------
# reports/ is never touched: it is the evidence trail. Delete it by hand once
# the results have been confirmed and archived.

clean: ## Remove containers, network and the report volume (keeps reports/ and vendor/)
	@# The CONNECT_OVERLAY file and --profile connect: without the overlay, compose
	@# does not know the authserver and testsuite-connect services exist and would
	@# leave both containers and the TLS material of the round behind. The overlay
	@# expands to nothing when CONNECT_ENABLE is not true, so the teardown of a
	@# round without it is unchanged.
	@$(COMPOSE) $(CONNECT_OVERLAY) --profile stufe3 --profile stufe5 --profile connect --profile manual down --volumes --remove-orphans
	@rm -rf $(REPO_ROOT)/run
	@printf 'Kept: reports/ (evidence), vendor/ (packages), .env.resolved (provenance)\n'

destroy: clean ## clean + delete vendor/ and .env.resolved (full reset, keeps reports/)
	@rm -rf $(REPO_ROOT)/vendor
	@rm -f $(ENV_RESOLVED_FILE)
	@printf 'Kept: reports/ only. Next run starts from scratch.\n'
