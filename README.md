# ISiK conformance test environment for InterSystems IRIS for Health

A docker-compose lab rig that runs the **official Gematik ISiK test suite** against
an **InterSystems IRIS for Health** FHIR repository and turns the result into an
evidence bundle.

One command brings up the unit under test, installs the endpoints and the FHIR
packages the Gematik reference validator validates against, seeds a fabricated test
corpus derived from the suite's own preconditions, runs every conformance level the
pinned suite carries, and writes the reports:

```sh
make all
```

**Which conformance levels a round covers follows the pinned suite version**, not
this rig: a **2.x** suite carries **ISiK Stufe 3** and **ISiK Stufe 5**, a **3.x**
suite carries **Stufe 5** only. The rig runs the levels the pinned image carries
and names them in the bundle; which version is pinned is yours to choose
([INSTALL.md section 6](INSTALL.md#6-the-two-version-choices-both-yours)).

**Everything is on an isolated Docker subnet with fixed addresses**, NAT'd to the
internet through the host. Nothing needs to reach in.

## What it produces

Per round, in `reports/<run-id>/`:

| | |
|---|---|
| `<variant>/official/test-report.zip` | The **authoritative** Gematik archives, byte-for-byte, one per invocation of the suite — `stufe3/`, `stufe5/` and, when it is enabled, `connect/`. This is what the *Bestätigungsverfahren* is based on and what is uploaded to TITUS |
| `analysis/results.md` | The cited results document: every claim traced to a path inside those archives |
| `GAP-ANALYSIS.md` | Per distinct defect: the test step, the FHIR feature and the package that supplies it, expected vs. actual. For product developers, in English |
| `CASE-EVIDENCE.md` | Its annex, per failing case: the input data used, the output expected and received with both bodies verbatim, and a command that replays the request |
| `analysis/scenario-verdicts.csv` | One row per test case |
| `analysis/MANIFEST.txt` | sha256 of every file in the bundle |
| `provenance/` | Resolved versions and image digests, the seed record, the SUT's own install record, the package manifest, the credentials used, and the version of this rig that produced the round |

The derived documents are pure functions of the archives and can be regenerated on
any machine from an archived bundle: `make report` extracts the archives, writes
`analysis/`, and then chains `gap-report` and `case-evidence`, which derive the
two developer documents from what it extracted.

### The `Connect` module needs a second invocation

One ISiK Stufe 5 module, `Connect`, authenticates with OAuth 2.0 bearer tokens
that its feature files set themselves, from the suite's own test data. A Tiger
route that carries an `authentication:` block **replaces** that header, and the
complete run needs such a route for everything else — so the module cannot be
measured inside it. The rig therefore **appends a second invocation** of the same
suite version, scoped `@Stufe5 and @Connect and (not @Optional)`, through a route
that carries no `authentication:` block and over the HTTPS listener the endpoint
requires before it will read a bearer token at all.

It is **off by default** (`CONNECT_ENABLE=false`, set in `.env`). Turning it on
**narrows nothing**: the complete run is still executed and published unchanged,
and a `connect/` archive is added beside `stufe5/`. For the module's 32 cases the
`connect/` verdicts then *supersede* the complete run's — never summed, never
averaged — and every case in the derived documents names the archive that
measured it. It also adds an authorization server (Keycloak, deliberately not an
InterSystems product), a TLS listener on the Web Gateway, and the round's own
one-hour bearer tokens — which, with that server's realm secrets, are the only
credentials this rig withholds from a bundle instead of documenting in it. See
[docs/en/08-connect.md](docs/en/08-connect.md).

ISiK is designed to be a constantly evolving, moving target, rather than a static set of one-time rules. IRIS for Health is therefore *expected* to fail parts of
this at times, which is why the gap analysis is a first-class deliverable rather than an appendix.

## What this repository does not contain

Four categories of thing are absent **by rule**. Each has
machinery here to obtain or produce it, and a document saying how.

| Absent | Why | Where to get it |
|---|---|---|
| **IRIS / IRIS for Health images and installation kits** | they are licensed product, and this repository is not a distribution channel for them | `containers.intersystems.com` for images, `downloads.intersystems.com` for kits, both with your own credentials. An image you already hold as a local file is supported too — [kit/README.md](kit/README.md) |
| **The gematik ISiK test suite** | it is gematik's, pinned and pulled, never vendored | `make resolve` pulls `gematik1/isik-testsuite:<version>` from Docker Hub; no credential needed. [docs/en/04-testtool.md](docs/en/04-testtool.md) |
| **Licence keys, registry tokens and passwords** | a credential in git history cannot be withdrawn | described, never shipped: [secrets/README.md](secrets/README.md) states the format, the validity and the source of each. `make check` fails if one is ever tracked |
| **Round bundles, reports and verdicts** | out of scope: this repository is the instrument, not the measurement. The measuring is yours to do, and a result shipped here would be a result about somebody else's system | `make all` produces them, into `reports/`, which is `.gitignore`d |

The one thing generated that *is* committed is a version-labelled snapshot of the
rendered test corpus, under [generated/](generated/) — so that you can diff your
own render against ours before taking a round and know whether a difference you
then measure is a difference in the test data. It is read by nothing and is not
an input to a round.

This rule took effect with version 1.0.0 (see [CHANGELOG.md](CHANGELOG.md)).
Commits before it carry our own working data and reports. They are not
confidential — they are simply out of scope for this repository, which is
tooling — so they are left in place rather than rewritten. Read them as a record
of a particular round on a particular build, taken on our rig, at that date; not
as a statement about any build you are about to test.

## Hardware

| | vCPU | RAM | Disk |
|---|---|---|---|
| **Recommended**, with or without `Connect` | **16** | **64 GiB** | **256 GB** |
| Minimum, round without `Connect` | 8 | 32 GiB | 128 GB |
| Minimum, round with `CONNECT_ENABLE=true` | 8 | **36 GiB** | 128 GB |

A full round without the `Connect` overlay, from a cold start, takes **54 minutes**
on the recommended host and roughly an hour and five on the minimum one, and
leaves 26 GB of disk in use. Size
upward rather than downward: a Stufe 5 run short of scenario heap does not slow
down, it silently loses the level after hours. [INSTALL.md](INSTALL.md#2-hardware)
traces every figure to a measurement.

**The `Connect` overlay raises the memory minimum, and only the memory minimum.**
It adds an authorization server — a JVM, limited to 1 GiB, measured at 601 MiB
resident — and a TLS listener on the Web Gateway, together about **1.1 GiB** that
has to be real memory: IRIS's own default claim of 25 % of host memory is
unchanged by the overlay. The measured floor for a round *without* it is 31 GiB,
and only at `TESTSUITE_HEAP_MAX=16g`, so a 32 GiB host no longer clears the floor
once the overlay is added; **36 GiB** is that floor plus the overlay plus the
margin the 32 GiB row never had. On disk the overlay costs the 718 MB
authorization-server image and about 60 MB of additional archive, which 128 GB
absorbs. In wall-clock time the second invocation measured **107 seconds** — it
runs 32 cases, not the whole level.

That 36 GiB row is **derived arithmetic, not a measurement**: every `Connect`
round so far has been taken on the 16 vCPU / 64 GiB host, which absorbs the
overlay without any of this reasoning. Details:
[docs/en/08-connect.md](docs/en/08-connect.md#additional-hardware).

This tooling was developed using an Azure VM and its Ubuntu 24.04 LTS x86_64 image as the lab rig, starting from its mid-2026 stock configuration. There is nothing particular about Azure that is required, but it is what supplied our installation baseline. If your baseline differs, you might need to adapt the installation procedure accordingly.

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
resolved independently: the SUT build currently floats to the newest 2026-line release, and
the **test suite version stays pinned** — the authored test corpus is coupled to one
release's feature files, and floating it can misattribute our own data defect to the
product. [INSTALL.md section 6](INSTALL.md#6-the-two-version-choices-both-yours) has
both, and how to disagree with either.

The `Connect` module is a third choice, and it is off. Set `CONNECT_ENABLE=true`
in `.env` before `make all` and the round appends the second invocation by itself,
authorization server and tokens included. It is deliberately **not** settable from
the command line: the rig reads it from the file only, so that a round cannot test
one thing and record another.

```sh
make help            # every target
make status          # containers, network, endpoints
make credentials     # this instance's credentials, with ready-to-paste curl lines
make tokens          # re-mint this round's bearer tokens (Connect rounds only)
make test-connect    # re-run only the Connect module, against fresh tokens
make clean           # teardown; never touches reports/ or secrets/
```

## Documentation

| | |
|---|---|
| [INSTALL.md](INSTALL.md) | From a bare Ubuntu 24.04 VM to a full report set. Start here for a new host |
| [docs/en/](docs/en/) | The rig in full: [architecture](docs/en/01-architecture.md), [setup](docs/en/02-setup.md), [the SUT](docs/en/03-sut-iris.md), [the test tool](docs/en/04-testtool.md), [the test data](docs/en/05-testdata.md), [execution](docs/en/06-execution.md), [reports](docs/en/07-reports.md), [bearer tokens](docs/en/08-connect.md), [limitations](docs/en/09-known-limitations.md) |
| [docs/de/](docs/de/) | Regulatorische Dokumentation zum *Bestätigungsverfahren* — auf Deutsch, weil die ISiK-Spezifikation es ist |
| [secrets/README.md](secrets/README.md) | What goes in `secrets/`, and what is generated |
| [kit/README.md](kit/README.md) | How to obtain the unit under test: pull a published image, side-load one you hold, or build one from an installation kit |
| [generated/](generated/) | Version-labelled snapshots of what the rig generates, for diffing against your own |
| [.env.example](.env.example) | Every deploy-time setting, with its reasoning next to it |
| [CHANGELOG.md](CHANGELOG.md) | What each version of the rig changed. The version in [VERSION](VERSION) is recorded in every round's `provenance/rig-version.txt`, so a bundle says which rig produced it |

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
verifies that mechanically.

The two credentials the `Connect` module introduces are the exception, and they
are **withheld** from a bundle rather than documented in it: the round's bearer
tokens, which last an hour and would be expired before any reader opened the
bundle, and the authorization server's realm secrets, which do not expire with the
round and are not needed to verify a verdict — a reader re-mints with
`make tokens`. In place of each token the bundle states the test-data key it was
staged for and its SHA-256, which answers the question a `401` actually raises:
*which* scope's token was presented. Inside `<variant>/official/`, which has to
stay byte-for-byte, nothing is rewritten. Stated as a deliberate deviation in
[docs/en/09-known-limitations.md](docs/en/09-known-limitations.md#credential-documentation).

## Licence

MIT — see [LICENSE](LICENSE).
