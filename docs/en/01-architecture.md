# 1. Architecture

## What this environment is

Three containers on one isolated docker network:

```
                    host
   ┌────────────────────────────────────────────────────────────────────┐
   │  127.0.0.1:52773 ──────┐                    ┌─ 127.0.0.1:9010     │
   │  127.0.0.1:51972 ──┐   │                    ├─ 127.0.0.1:9011     │
   │                    │   │                    │                     │
   │        br-isik ────┴───┴────────────────────┴───────────────       │
   │            │  172.31.0.0/24, gateway 172.31.0.1 (= host)           │
   │            │                                                      │
   │   ┌────────┴─────────┐  ┌──────────────────┐  ┌─────────────────┐ │
   │   │ isik-iris        │◄─┤ isik-webgateway  │◄─┤ isik-testsuite- │ │
   │   │ 172.31.0.10      │  │ 172.31.0.30      │  │ stufeN          │ │
   │   │                  │  │                  │  │ 172.31.0.20     │ │
   │   │ IRIS for Health  │  │ Web Gateway      │  │                 │ │
   │   │ 2026.2           │  │ 2026.2 (Apache)  │  │ gematik1/       │ │
   │   │                  │  │                  │  │ isik-testsuite  │ │
   │   │ superserver      │  │ :80              │  │ 2.7.0           │ │
   │   │ :1972 only       │  │ /isik3/fhir/r4 ──┼──┤ MAVEN_PROFILE=  │ │
   │   │ (no HTTP at all) │  │ /isik5/fhir/r4 ──┼──┤ stufe3 or 5     │ │
   │   └──────────────────┘  └──────────────────┘  └─────────────────┘ │
   │      └──── both are the system under test ────┘                   │
   └────────────────────────────────────────────────────────────────────┘
```

`isik-iris` **and** `isik-webgateway` together are the unit under test — see
[The SUT is two containers](#the-sut-is-two-containers) below, which is the single
most surprising fact about this topology. `isik-testsuite-stufeN` is the test tool;
it exists only while a level is running, and exactly one level runs at a time.

## The SUT is two containers

**The official IRIS for Health images ship no web server.** Not an unconfigured
one — none at all. Verified on `irishealth:2026.2` and `2025.3`, both licensed and
unlicensed:

* only ports 1972, 2188 and 42623 listen; nothing on 52773
* there is no `httpd`, `apache2` or `nginx` binary anywhere in the image
* `iris.cpf` carries `WebServer=0` and `WebServerPort=0`; forcing them to `1` and
  `52773` changes nothing, because there is no server to start
* the image's own `parameters.isc` records `csp_gateway.configure_apache: N`
* no IRIS variant in the registry bundles one

The images nonetheless declare `52773/tcp` in `ExposedPorts` and label it
`com.intersystems.ports.default.webserver: 52773`, which is what makes the absence
easy to miss: everything about the image *looks* as though HTTP is available.

So the rig runs the separate official Web Gateway image in front of IRIS. That is
the supported InterSystems deployment for HTTP and the same topology a production
ISiK deployment would use, which keeps the SUT representative of the product as
shipped rather than of a workaround.

The consequence runs through the whole rig: **the gateway is part of the SUT, not
test scaffolding.** Every FHIR request the suite issues passes through it. Concretely,

* its image digest is recorded in `.env.resolved` and in the evidence bundle's
  provenance alongside IRIS's — "what exactly did you test?" is not fully answered
  without it;
* [`healthcheck.sh`](../../iris/bootstrap/healthcheck.sh) probes `/metadata`
  *through* the gateway, so the `iris` service's health deliberately spans two
  containers. A green IRIS behind a broken gateway serves nothing, and reporting it
  healthy would be a lie the whole round is gated on. There is no dependency cycle:
  the `webgateway` service waits on IRIS's *start*, not its health;
* [`91-status.sh`](../../scripts/91-status.sh) reports the gateway in its own
  section, before the endpoints, because an unhealthy gateway explains unhealthy
  endpoints rather than the other way round.

Four Apache/`mod_csp` directives are required to make it route FHIR, each
established empirically during bring-up and each with a distinct failure signature.
They are in [03-sut-iris.md](03-sut-iris.md#the-four-directives-that-route-fhir).

The Apache-based image is the default; `webgateway-nginx` and
`webgateway-lockeddown` are published drop-in alternatives and both work here.
Override `WEBGATEWAY_IMAGE` at deploy time.

## The network

One user-defined bridge network, `isik-net`, declared in
[docker-compose.yml](../../docker-compose.yml):

| Property | Value | Why |
|---|---|---|
| Subnet | `172.31.0.0/24` | A dedicated /24 so the whole round is one firewall CIDR |
| Gateway | `172.31.0.1` | The host. Internet access (image pulls, package downloads) is NAT'd through it, which is Docker's default bridge behaviour |
| Host bridge name | `br-isik` | Fixed via `com.docker.network.bridge.name`, so a firewall rule can say `-i br-isik` instead of matching a generated `br-<hash>` |
| IRIS | `172.31.0.10` | Static |
| Test suite | `172.31.0.20` | Static |

Static addresses are not cosmetic. The rendered Tiger configuration, the seed
log, the `Authorization`-bearing requests quoted in the gap report and any host
firewall rule all name the same literal string, so an inspector comparing them
does not have to resolve a container name that no longer exists.

The two published port pairs are for the operator only. The test suite reaches
IRIS over the subnet and never uses `127.0.0.1`.

**Isolation is enforced outside this repository.** The rig gives you one bridge
interface and one CIDR to target; it does not install firewall rules. Blocking
egress from `172.31.0.0/24` except to the registries, or blocking it entirely
after `make packages`, is a host-level decision.

## The containers

### `isik-iris`

The official licensed image, `containers.intersystems.com/intersystems/irishealth:2026.2`,
unmodified. There is no `Dockerfile` in this repository and no derived image: the
FHIR installation runs at container start, through the image's own `iris-main`
entrypoint hook (`-a /opt/isik/bootstrap/bootstrap.sh`).

That choice has three consequences worth stating:

* The SUT is stock IRIS. Anyone reading a result can pull the same image by
  digest and see the same product.
* One image serves all three deploy modes, because the difference between them is
  configuration, not build.
* Bring-up costs minutes, dominated by the FHIR package import — measured at
  4m40s cold and 7s on the reuse path
  ([06-execution.md](06-execution.md#what-one-round-is)). The healthcheck's
  `start_period` is nevertheless 45 minutes, because the import is I/O bound and a
  slow host must not be declared unhealthy while it is making progress; that
  margin is exactly why [`40-up.sh`](../../scripts/40-up.sh) reports progress from
  the bootstrap's own status file instead of just polling Docker, which during a
  start period reports `starting` even for an install that has already failed for
  good.

Mounts, all read-only except the state directory:

| Host | Container | What |
|---|---|---|
| `secrets/iris.key` | `/usr/irissys/mgr/iris.key` | Operator-supplied licence key |
| `iris/bootstrap/` | `/opt/isik/bootstrap/` | Bootstrap driver + healthcheck |
| `iris/src/` | `/opt/isik/src/` | `ISiK.Setup` ObjectScript source |
| `vendor/` | `/opt/isik/vendor/` | Unpacked FHIR packages + the package plan |
| `run/iris-state/` | `/opt/isik/state/` | Bootstrap log, status file, provenance JSON (writable) |

See [03-sut-iris.md](03-sut-iris.md) for what the bootstrap does.

### `isik-testsuite-stufeN`

`gematik1/isik-testsuite:2.7.0`, unmodified, declared three times in
[compose/testsuite.yml](../../compose/testsuite.yml) under compose profiles
`stufe3`, `stufe5` and `manual`. Three services rather than one parameterised
service, because the upstream image runs exactly one Maven profile per container
start and its entrypoint wipes the report directory at the beginning of every
run:

```sh
rm -rf $REPORT_DIR/* ; mvn clean verify -ntp -P${MAVEN_PROFILE} \
    -DTESTS_TO_RUN="${TESTS_TO_RUN}" || true ; \
    mv -v $APP_HOME/target/*report.zip $REPORT_DIR/
```

All three are profile-gated, so `make up` brings up IRIS alone. They share one
static IP because they are never concurrent — the Makefile serialises the levels
and archives each report before starting the next. See
[04-testtool.md](04-testtool.md) for the configuration and
[06-execution.md](06-execution.md) for what that entrypoint implies about exit
codes.

## Configuration flow

Nothing is hardcoded in compose or in the ObjectScript. One configuration file
flows through the whole round:

```
.env.example  ──cp──▶  .env  ──10-resolve-versions.sh──▶  .env.resolved
                        │                                      │
                        │ operator's deploy-time decisions      │ + image digests
                        │                                      │ + generated passwords
                        │                                      │ mode 0600
                        └──────────────────────────────────────┴──▶ everything downstream
                                                                     compose --env-file
                                                                     scripts (load_env)
                                                                     rendered Tiger YAML
                                                                     provenance/env.resolved.txt
```

`.env` is the operator's file. `.env.resolved` is the round's frozen record and
the authoritative answer to "what exactly was tested?" — every target after
`make resolve` reads it, so editing `.env` mid-round cannot silently change what
is running. It is copied verbatim into each evidence bundle.

Deploy modes, selected by `MODE`:

| MODE | Versions | Reproducible | Use |
|---|---|---|---|
| `pinned` | Exactly what `.env` says | Yes | **Default.** Any round whose result will be reported |
| `latest` | Newest test suite release + newest IRIS tag on `IRIS_VERSION_TRACK` | No | Convenience only |
| `manual` | Same as `pinned` | Yes | Brings up and seeds, then leaves the Tiger WorkflowUI running without auto-starting anything |

## Phases

Every Makefile target is a thin wrapper over one script, so each phase can be run
by hand on a host without `make`. The dependency chain is real: `make test`
alone brings the whole environment up first.

| Phase | Target | Script | What it produces |
|---|---|---|---|
| 0 | `preflight` | [00-preflight.sh](../../scripts/00-preflight.sh) | Nothing. Fails early on a missing prerequisite, port collision, subnet collision, absent licence key or missing registry login |
| 1 | `resolve` | [10-resolve-versions.sh](../../scripts/10-resolve-versions.sh) | `.env.resolved`, both image digests, the two generated passwords in `secrets/` |
| 2 | `packages` | [20-fetch-packages.sh](../../scripts/20-fetch-packages.sh) | `vendor/packages/` (unpacked), `vendor/upstream/` (the suite's own features and test data, taken out of the pinned image), `vendor/package-plan.json` |
| | | [25-extract-requirements.sh](../../scripts/25-extract-requirements.sh) | `vendor/requirements.json` + the requirement tables under `docs/en/generated/` |
| | | [build_index.py](../../gap-analysis/build_index.py) | `gap-analysis/index.json` — step shape → FHIR feature → supplying package |
| 3 | `up` | [30-render-config.sh](../../scripts/30-render-config.sh) | `run/tiger/*.yaml`, `run/testdata/`, `run/tiger/RENDERED.txt`, `run/CREDENTIALS.md` |
| | | [40-up.sh](../../scripts/40-up.sh) | A live, healthy SUT with both endpoints answering |
| 4 | `seed` | [50-seed.sh](../../scripts/50-seed.sh) | `build/fhir/<level>/`, both endpoints loaded, `run/seed/*.json` |
| 5 | `test` | [60-run-tests.sh](../../scripts/60-run-tests.sh) | `reports/<run-id>/<variant>/official/*.zip` + `run.json` + `run.log` |
| 6 | `report` | [70-collect-report.sh](../../scripts/70-collect-report.sh) | The evidence bundle: extracted archives, `analysis/results.md`, `provenance/`, `MANIFEST.txt` |
| | | [80-gap-report.sh](../../scripts/80-gap-report.sh) | `GAP-ANALYSIS.md` + `gap-analysis.json` |
| | | [81-case-evidence.sh](../../scripts/81-case-evidence.sh) | `CASE-EVIDENCE.md` + `case-evidence.json` — the annex of the gap analysis, one clause per failing case |

Inspection targets — `status`, `credentials`, `logs`, `shell-iris`, `check` — read
the live environment and change nothing.

## Directory layout

```
docker-compose.yml        network + SUT
compose/testsuite.yml     test tool overlay, profile-gated
Makefile                  orchestration; every target wraps one script
.env.example              every deploy-time decision, documented

scripts/                  the phases above, plus lib.sh and the inspection tools
scripts/lib/              Python: test-data build + checks, seeding, results analysis
iris/bootstrap/           bootstrap.sh + healthcheck.sh (mounted into the SUT)
iris/src/ISiK/Setup.cls   the FHIR installation, as ObjectScript
tiger/*.yaml.tmpl         Tiger configuration templates, annotated [DEVIATION]
testdata/                 the suite's own contract files + our authored FHIR corpus
gap-analysis/             the attribution index builder and the gap report
docs/                     this documentation (en) + regulatory documentation (de)

secrets/                  licence key + generated passwords          (gitignored)
vendor/                   packages, the suite's own sources, plans   (gitignored)
build/                    rendered FHIR test data                   (gitignored)
run/                      rendered config, credential sheet, state  (gitignored)
reports/<run-id>/         the evidence bundles                      (gitignored)
```

The four gitignored trees are all regenerable except `reports/` and
`secrets/iris.key`. `reports/` is the evidence trail and no target deletes it.

## Portability

The deployment host is a different machine, so the rig is designed to be copied
there and run:

* No image builds. Both images come from a registry, by digest-recorded tag.
* No compiled artefacts. Shell and Python 3 with `PyYAML` are the only host
  requirements beyond Docker; `00-preflight.sh` checks each one by name.
* No absolute paths. `REPO_ROOT` is derived from the Makefile's own location and
  exported, so scripts work from any working directory.
* Compose bind mounts are project-root relative, and the Makefile always invokes
  compose from the repository root with both `-f` files, because Compose resolves
  relative paths against the *first* `-f` file's directory.
* `make check` is a static self-check that runs without Docker: shell and Python
  syntax, YAML parse, cross-file consistency, and — where Docker is present —
  `docker compose config`.
