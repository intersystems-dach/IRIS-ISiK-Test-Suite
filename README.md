# ISiK conformance test environment for InterSystems IRIS for Health

A docker-compose lab rig that runs the **official Gematik ISiK test suite** against
an **InterSystems IRIS for Health** FHIR repository, for **ISiK Stufe 3** and
**ISiK Stufe 5**, and turns the result into an evidence bundle.

One command brings up the unit under test, installs both endpoints and the 35 FHIR
packages the Gematik reference validator validates against, seeds a fabricated test
corpus derived from the suite's own preconditions, runs both conformance levels, and
writes the reports:

```sh
make all
```

**Everything is on an isolated Docker subnet with fixed addresses**, NAT'd to the
internet through the host. Nothing needs to reach in.

## What it produces

Per round, in `reports/<run-id>/`:

| | |
|---|---|
| `stufe3/official/test-report.zip`, `stufe5/official/…` | The **authoritative** Gematik archives, byte-for-byte — what the *Bestätigungsverfahren* is based on and what is uploaded to TITUS |
| `analysis/results.md` | The cited results document: every claim traced to a path inside those archives |
| `GAP-ANALYSIS.md` | Per failure: the test step, the FHIR feature and the package that supplies it, expected vs. actual. For product developers, in English |
| `analysis/scenario-verdicts.csv` | One row per test case, 1401 of them |
| `analysis/MANIFEST.txt` | sha256 of every file in the bundle |
| `provenance/` | Resolved versions and image digests, the seed record, the SUT's own install record, the package manifest, the credentials used |

The derived documents are pure functions of the archives and can be regenerated on
any machine from an archived bundle. IRIS for Health is *expected* to fail parts of
this, which is why the gap analysis is a first-class deliverable rather than an
appendix.

## Hardware

| | vCPU | RAM | Disk |
|---|---|---|---|
| **Recommended** | **16** | **64 GiB** | **256 GB** |
| Minimum | 8 | 32 GiB | 128 GB |

A full round from a cold start takes **54 minutes** on the recommended host and
roughly an hour and five on the minimum one, and leaves 26 GB of disk in use. Size
upward rather than downward: a Stufe 5 run short of scenario heap does not slow
down, it silently loses the level after hours. [INSTALL.md](INSTALL.md#2-hardware)
traces every figure to a measurement.

## Quick start

On a host that already has Docker and Compose v2 — otherwise start at
[INSTALL.md](INSTALL.md):

```sh
git clone https://github.com/intersystems-dach/IRIS-ISiK-Test-Suite
cd IRIS-ISiK-Test-Suite
cp .env.example .env                    # every setting documented where the value is
cp /path/to/your/iris.key secrets/      # yours; `make up` fixes its ownership
docker login containers.intersystems.com
make check && make all
```

Two things are per-installation secrets you supply: the **IRIS licence key** and a
**`containers.intersystems.com` login**. Two things are per-installation choices,
resolved independently: the SUT build floats to the newest 2026-line release, and
the **test suite version stays pinned** — the authored test corpus is coupled to one
release's feature files, and floating it can misattribute our own data defect to the
product. [INSTALL.md section 6](INSTALL.md#6-the-two-version-choices-both-yours) has
both, and how to disagree with either.

```sh
make help            # every target
make status          # containers, network, endpoints
make credentials     # this instance's credentials, with ready-to-paste curl lines
make clean           # teardown; never touches reports/ or secrets/
```

## Documentation

| | |
|---|---|
| [INSTALL.md](INSTALL.md) | From a bare Ubuntu 24.04 VM to a full report set. Start here for a new host |
| [docs/en/](docs/en/) | The rig in full: [architecture](docs/en/01-architecture.md), [setup](docs/en/02-setup.md), [the SUT](docs/en/03-sut-iris.md), [the test tool](docs/en/04-testtool.md), [the test data](docs/en/05-testdata.md), [execution](docs/en/06-execution.md), [reports](docs/en/07-reports.md), [limitations](docs/en/08-known-limitations.md) |
| [docs/de/](docs/de/) | Regulatorische Dokumentation zum *Bestätigungsverfahren* — auf Deutsch, weil die ISiK-Spezifikation es ist |
| [secrets/README.md](secrets/README.md) | What goes in `secrets/`, and what is generated |
| [.env.example](.env.example) | Every deploy-time setting, with its reasoning next to it |

Source comments and technical description are English; regulatory documentation is
German, because ISiK's own specification is German-only on purpose and translating a
requirement would put an interpretation between the specification and the test.

## Credentials appear in the reports on purpose

Each deployment generates its own readable, policy-compliant passwords and states
them in `run/CREDENTIALS.md` and in §1 of every results document, so that a human
tester can reproduce any verdict by hand. This is a lab instance brought up for one
round and destroyed once the results are verified; access is controlled physically
and at the network perimeter, not by these values, which exist — never empty, never
absent — only so the SUT stays a faithful stand-in for a production deployment. The
one rule that does apply: **none of it reaches source control**, and `make check`
verifies that mechanically. Stated as a deliberate deviation in
[docs/en/08-known-limitations.md](docs/en/08-known-limitations.md#credential-documentation).

## Licence

MIT — see [LICENSE](LICENSE).
