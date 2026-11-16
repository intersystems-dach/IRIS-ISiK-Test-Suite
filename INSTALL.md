# INSTALL — from a pristine Ubuntu 24.04 host to a full report set

This is the from-scratch path: a freshly provisioned virtual machine with nothing
on it but the operating system, and at the end of it a complete evidence bundle for
ISiK Stufe 3 and Stufe 5 against InterSystems IRIS for Health.

Every figure below is a measurement taken on a real run, and the section it appears
in says where. Nothing here is an estimate unless it says so.

If your host already has Docker and Compose v2, you can skip to section 4 —
[docs/en/02-setup.md](docs/en/02-setup.md) is the shorter, prerequisites-assumed
version of the same procedure.

| Section | |
|---|---|
| [1. What you need before you start](#1-what-you-need-before-you-start) | Three things, two of them secrets |
| [2. Hardware](#2-hardware) | Sizing, with the measurements behind each number |
| [3. Provision the host](#3-provision-the-host) | Docker and the rest, scripted or by hand |
| [4. Get the repository](#4-get-the-repository) | |
| [5. The two secrets](#5-the-two-secrets-both-yours) | Licence key, registry login |
| [6. The two version choices](#6-the-two-version-choices-both-yours) | What floats, what is pinned, how to disagree |
| [7. The one setting that can lose a run](#7-the-one-setting-that-can-lose-a-run) | Scenario heap |
| [8. Run it](#8-run-it) | |
| [9. Collect the evidence](#9-collect-the-evidence) | |
| [10. Tear it down](#10-tear-it-down) | |
| [11. If something goes wrong](#11-if-something-goes-wrong) | The five failures that actually happened |
| [12. About the credentials in the reports](#12-about-the-credentials-in-the-reports) | They are there on purpose |

---

## 1. What you need before you start

1. **A virtual machine** running Ubuntu 24.04, sized per section 2, with outbound
   HTTPS. Nothing needs to reach *in* — the rig publishes ports on the loopback
   side of the host for your own convenience, and the test suite talks to the
   system under test over an isolated Docker subnet.
2. **An InterSystems IRIS for Health licence key.** Yours, per installation. Not in
   this repository and not obtainable from it.
3. **A login for `containers.intersystems.com`** — a WRC account username and a
   *registry token*. Also yours, also per installation.

Both secrets are covered in section 5. Neither is ever written into this
repository's tracked files, and `make check` verifies that mechanically.

## 2. Hardware

|  | vCPU | RAM | Disk | Verdict |
|---|---|---|---|---|
| **Recommended** | **16** | **64 GiB** | **256 GB** | Validated end to end, with and without the `Connect` overlay: full round in **54 minutes**, 26 GB of disk used |
| Minimum, without `Connect` | 8 | 32 GiB | 128 GB | Completes both levels, roughly an hour and five minutes, no headroom |
| Minimum, with `CONNECT_ENABLE=true` | 8 | **36 GiB** | 128 GB | Derived, not measured: the floor below plus the overlay's ~1.1 GiB plus margin |
| Measured floor, without `Connect` | 8 | 31 GiB | 128 GB | Completed both levels *only* at `TESTSUITE_HEAP_MAX=16g` |

**Bias upward deliberately.** A Stufe 5 run that is short of scenario heap does not
slow down and does not fail — it silently loses the level. Tiger's `RbelLogger`
retains a parsed tree of every HTTP message of the whole run; when the heap fills,
its parser threads die of `OutOfMemoryError` and every later scenario blocks for the
full 100-second parse timeout. On the 31 GiB host at the JVM's default heap this
happened at roughly 600 of 895 cases: throughput fell from ~12 scenarios/minute to
~0.5, projecting about **nine hours** for the remainder against a three-hour
timeout — and a timed-out run produces **no archive at all**, because Maven
assembles it in `post-integration-test`. The container sat at 0.3% CPU throughout,
which reads as an idle hang rather than as memory exhaustion. Stufe 3 completes
inside the default and never shows it, so a green Stufe 3 is no assurance. Full
account: [docs/en/06-execution.md](docs/en/06-execution.md#stufe-5-needs-heap-and-running-short-of-it-costs-the-whole-level).

A larger VM for a few hours is cheaper than discovering that after four of them.

**Where the numbers come from.**

* **RAM.** The scenario JVM wants 16 GiB of heap for Stufe 5, IRIS holds about
  2.2 GiB resident, and the Web Gateway and the host itself want the rest.
  `make preflight` warns below 24 GiB for exactly this reason — below **26 GiB**
  when `CONNECT_ENABLE=true`, which is the same arithmetic with the overlay added
  ([`scripts/00-preflight.sh`](scripts/00-preflight.sh)). 32 GiB is enough for a
  round without the overlay and 36 GiB for one with it; 64 GiB
  is what lets you raise the heap to 32g without arithmetic, and removes the one
  failure mode that costs a whole level.
* **vCPU.** Both sizes completed both levels, and doubling the host took the two
  conformance levels together from 3300 s to 2890 s — **12%**. Neither level
  parallelises across scenarios, so the suite drives one at a time and spends most
  of it waiting on the SUT. 16 vCPU is recommended because it is normally the same
  size class as 64 GiB of RAM, not because a round needs the cores.
* **Disk.** Measured with `docker system df` after a complete round: **26 GB in
  total** on the 16 vCPU host — 8.5 GB of images, 9.4 GB of containers, 146 MB of
  volumes, the unpacked FHIR packages (~1.5 GB) and a **2.8 GB** evidence bundle
  (most of it `debug-report.zip`, which is not submitted but is what the gap
  analysis reads). About 25 GB is the real floor — 26 GB with
  `CONNECT_ENABLE=true` — which is what `make preflight` checks. 128 GB is
  comfortable; 256 GB is
  usually the same price bracket and leaves room to keep several rounds' bundles on
  the host.
* **No swap needed.** Both measured hosts ran with swap disabled. Swap does not
  help the failure mode above: a JVM that has hit `-Xmx` does not want more pages,
  it wants a bigger heap.

**Where the `Connect` minimum comes from.** With `CONNECT_ENABLE=true` the rig
also runs an authorization server — a JVM, limited to 1 GiB, measured at 601 MiB
resident during the second invocation — and a TLS listener on the Web Gateway,
together about **1.1 GiB**. IRIS's own default of 25 % of host memory is unchanged
by it, so that memory has to be real and not borrowed from what IRIS already
claims. The measured floor above is 31 GiB, so **32 GiB no longer clears it once
the overlay is added**: 31 + 1.1 is 32.1 GiB before any margin at all. 36 GiB is
that arithmetic plus the margin the 32 GiB row never had, and it is arithmetic —
every `Connect` round taken so far ran on the 16 vCPU / 64 GiB host, which absorbs
the overlay without any of this reasoning. On the minimum host, budget the overlay
against the heap rather than against the margin. The module, what it needs and why
is in [docs/en/08-connect.md](docs/en/08-connect.md).

The overlay's other two costs are small and measured: **718 MB** for the
authorization-server image, counted in `docker system df` images alongside the
8.5 GB above, and about **60 MB** of additional evidence (`connect/`, a second
archive of 32 cases rather than of a whole level). It adds **107 seconds** of
wall-clock time to a round.

**HugePages are not configured by this rig, and InterSystems recommends them.**
Without them IRIS's real memory consumption is materially higher than its
configured shared-memory size suggests, so a host sized from that figure alone is
not a sized host. Configuring them is a host-level decision outside this
repository, like the firewall rules; it is named here because the figures above
were measured *without* them and are therefore the pessimistic case, not the
optimistic one.

## 3. Provision the host

On a first login to a pristine host, `docker`, `make` and `unzip` are absent. The
scripted way, which is idempotent and safe to re-run:

```sh
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/intersystems-dach/IRIS-ISiK-Test-Suite
cd IRIS-ISiK-Test-Suite
scripts/01-provision-host.sh          # asks for your sudo password once
```

Run it from an **interactive** shell, or on a host where your account has
passwordless sudo. Everything it installs is system-wide, so if it can neither
prompt nor use sudo without a password it stops at once and says so, rather than
part-provisioning the host.

It installs `unzip make ca-certificates curl gnupg git python3-yaml jq`, then Docker
Engine, the CLI, containerd, buildx and **the Compose v2 plugin** from Docker's own
apt repository, enables `docker.service`, and adds your account to the `docker`
group. It verifies each result rather than assuming it, and reports what was already
in place.

**Then start a new login session.** Group membership is fixed when a shell starts,
so the shell that ran the script cannot use the Docker socket no matter what the
script does:

```sh
exit            # and reconnect — or, in this shell only: newgrp docker
cd IRIS-ISiK-Test-Suite
docker info >/dev/null && echo ok
```

Re-running `scripts/01-provision-host.sh` after logging back in is safe and
confirms the result.

<details>
<summary>The same thing by hand, if running a script is not acceptable</summary>

```sh
sudo apt-get update
sudo apt-get install -y unzip make ca-certificates curl gnupg git python3-yaml jq

# Docker's own repository: Ubuntu's docker.io predates Compose v2, which this rig
# requires as the `docker compose` subcommand.
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | sudo tee /etc/apt/keyrings/docker.asc >/dev/null
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null

sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
                        docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"     # then log out and back in
```

</details>

Why Docker's repository and not Ubuntu's `docker.io`: this rig uses compose
profiles and the long-form `depends_on` / `condition` syntax, both of which need
Compose **v2** as the `docker compose` subcommand. The legacy `docker-compose`
script is not supported and `make preflight` says so.

## 4. Get the repository

```sh
git clone https://github.com/intersystems-dach/IRIS-ISiK-Test-Suite
cd IRIS-ISiK-Test-Suite
cp .env.example .env
```

`.env` is where every deploy-time decision lives, and `.env.example` documents each
one where the value is. It is gitignored, as is everything else that ends up
holding a credential.

## 5. The two secrets, both yours

### The licence key

```sh
cp /path/to/your/iris.key secrets/
```

Any filename that reads `iris.key` case-insensitively is accepted, so the file can
keep whatever name it arrived under.

**Do not `chmod 0400` it.** A bind mount preserves the host inode's owner and mode,
and IRIS in the container runs as uid **51773** (`irisowner`), not as you — a 0400
key owned by your account is invisible to it. `make up` sets ownership and mode
itself. The failure this avoids is silent and expensive: IRIS does *not* fail to
start, it logs `LMF Error: No valid license key`, falls back to a zero-capacity
diagnostic licence, installs everything successfully over several minutes, and then
fails every FHIR request with `ERROR #5915: Unable to allocate a license` — recorded
only in the audit log, and indistinguishable at the HTTP layer from an
authentication fault. Details in
[docs/en/02-setup.md](docs/en/02-setup.md#the-licence-files-permissions-matter-more-than-they-look)
and [secrets/README.md](secrets/README.md).

A licence key names the version it was issued for, and IRIS may refuse a key issued
for an older release. If a bootstrap reports a licensing failure, that is the first
thing to check — see section 6.

### The registry login

```sh
docker login containers.intersystems.com
```

Username is your WRC account; the password is a **registry token**, not your WRC
password. `make preflight` checks that a credential for this registry is present.

If the host cannot reach `containers.intersystems.com` at all, the image can be
pulled elsewhere and side-loaded with `docker save` / `docker load`; `make resolve`
supports this and records the substitution in the provenance. The procedure is in
[docs/en/02-setup.md](docs/en/02-setup.md#if-the-licensed-registry-is-unreachable-side-loading-the-iris-image).

And if the build you have to test is not available to you as a container image at
all, but is available as an **installation kit** from
`downloads.intersystems.com`, `make image-from-kit` builds one. Read
[kit/README.md](kit/README.md) first: it works, and a round taken on the result
is not a round taken on a published product image, which the reports have to say.

## 6. The two version choices, both yours

Two versions decide what a round means: the build of the **unit under test**, and
the release of the **test suite**. The shipped defaults are *not* symmetric, and the
asymmetry is deliberate.

| Axis | Default | Setting |
|---|---|---|
| IRIS for Health (+ Web Gateway) | newest release on the 2026 line | `MODE=latest`, `IRIS_VERSION_TRACK=2026` |
| Gematik ISiK test suite | **pinned at 2.7.0** | `TESTSUITE_VERSION=2.7.0`, `TESTSUITE_VERSION_TRACK=` (empty) |

**Why the SUT floats.** A conformance verdict is normally wanted about the newest
build of the product, and floating it costs nothing: `.env.resolved` records the
registry digest and the IRIS build string, so the tested build stays exactly
identified. What is given up is re-creating the round *from `.env`* months later,
because the tag will have moved. `run.json` and §1 of the results document both
state which of the two happened (`sut.versionSource`).

**Why the test suite does not.** The suite version fixes four things, and only the
first is visible in a report: the scenario inventory (and so the expected counts,
506 and 895); the FHIR package versions its reference validator validates against;
the `${data.*}` placeholder contract that this repository's authored test data must
satisfy *exactly*; and which upstream suite defects need working around. The third
is why floating it is unsafe rather than merely undesirable — a renamed placeholder
does not fail loudly. The corpus still seeds, and a scenario then asserts against
data that is not what its feature file expects, which surfaces as a **conformance
gap in the system under test**. Misattributing our own test-data defect to the
product is the one outcome this project is built to prevent.

### Disagreeing with either

**Pin the SUT** — for a submission that must be re-creatable from `.env`, or to test
the exact build a licence key was issued for:

```sh
# in .env
MODE=pinned
IRIS_VERSION=2026.2          # WEBGATEWAY_VERSION is version-matched to it
```

**Float the test suite** — an explicit, second opt-in:

```sh
# in .env
TESTSUITE_VERSION_TRACK=latest
```

`make resolve` warns when you do, because the corpus then needs re-verifying:

1. `make check` — section 5 re-resolves every `${data.*}` key the selected feature
   files reference against the authored contracts, and fails on an unresolved one.
   Section 5b re-checks every fabricated resource against the new release's
   requirement extraction.
2. Re-read [docs/en/05-testdata.md](docs/en/05-testdata.md) against the new
   release, in particular the four upstream defects worked around by version. A
   defect fixed upstream turns a workaround into a *new* defect.
3. Only then is the round a conformance result.

**Either way**, `make resolve` looks up the newest published release and records it
as `TESTSUITE_LATEST_AVAILABLE` in `.env.resolved`, warning if the pin is behind.
A stale pin is a stated fact in the report, not something a reader has to check
later. 2.7.0 was published 2026-07-06 and was still the newest release on
2026-08-07.

## 7. The one setting that can lose a run

```sh
# in .env
TESTSUITE_HEAP_MAX=16g
```

This is `-Xmx` for the JVM that runs the scenarios. `16g` is the shipped default and
is what both measured Stufe 5 runs used. **On a 64 GiB host, set `32g`** — the
margin is free there, and section 2 explains what running short costs.

Leaving it empty means "let the JVM decide", which is a quarter of host RAM: 7.84 GiB
on the 31 GiB host, and *not enough*. `make preflight` warns when it is unset or
when the host has less than 24 GiB.

It is delivered to the container as `MAVEN_ARGS=-DargLine=-Xmx…`, not `MAVEN_OPTS`,
because the scenarios run in Failsafe's forked JVM — `MAVEN_OPTS` would size the
wrong process. That detail is load-bearing and is recorded in `.env.example`.

If a level did stall, re-run just that level with more heap, against the same
round:

```sh
TESTSUITE_HEAP_MAX=32g make test-stufe5 RUN_ID=run-20260807T084713Z
make report RUN_ID=run-20260807T084713Z
```

Heap is one of the few settings a command line may override (`TESTSUITE_TIMEOUT`,
`IRIS_BOOTSTRAP_TIMEOUT` and `MANUAL_STUFE` are the others). The override is logged
when it takes effect and recorded in that level's `run.json` as `overrides`, so the
archive says the level was re-run with a different heap. Versions and endpoints are
deliberately *not* overridable: `.env.resolved` is the provenance record, and a
round must not be able to test one thing and record another.

## 8. Run it

```sh
make check        # static self-check of the repository; no Docker needed for most of it
make all          # preflight → resolve → packages → up → seed → test → report
```

Nothing prompts. The two passwords the environment needs are generated at
`make resolve`; see section 12.

Measured on the recommended host — 16 vCPU / 62 GiB, `PACKAGE_SOURCE=refv`, from a
completely cold start (no images, no packages), with the 8 vCPU / 31 GiB figures
beside them:

| Phase | 16 vCPU / 62 GiB | 8 vCPU / 31 GiB |
|---|---|---|
| `resolve` + `packages` | **2m05s** | a few minutes |
| `up` — cold bootstrap | **3m31s** | 4m40s |
| `seed` | **22s** | under a minute |
| `test-stufe3` | **18m04s** (506 cases) | 20m50s |
| `test-stufe5` | **30m03s** (895 cases) | 34m10s |
| `report` + `gap-report` | **23s** | under a minute |
| **Full `make all`** | **54m**, timed | ~1h05m, summed from the phases |

The 16 vCPU total is one wall-clock measurement of a single `make all`, 08:47:13 to
09:41:44 UTC. The 8 vCPU total is the sum of its phases, since that host's round was
driven a phase at a time; treat it as an estimate. The two conformance levels are
the part measured identically on both, and they went from 3300 s to 2890 s — 12% for
twice the machine. Neither level parallelises across scenarios, so the suite drives
one at a time and spends most of it waiting on the SUT. Size for the heap headroom,
not the throughput. `TESTSUITE_TIMEOUT` allows three hours per level, and the margin
exists for the heap-starved case rather than the healthy one.

Disk after a complete round on that host: 26 GB used in total — 8.5 GB of images,
9.4 GB of containers, 146 MB of volumes, and a 2.8 GB evidence bundle.

While it runs, in another shell:

```sh
make status          # containers, network, endpoints, credentials
make logs            # tail the SUT's bootstrap log
make credentials     # this instance's credentials, with ready-to-paste curl lines
```

`make up` prints progress from the bootstrap's own status file rather than Docker's
health status, because Docker reports `starting` for the entire 45-minute
`start_period` regardless — including for a bootstrap that has already failed.

## 9. Collect the evidence

```
reports/<run-id>/
  README.md                          what each file is; start here
  analysis/results.md                the cited results document (regulatory)
  analysis/results.json              the same, machine-readable
  analysis/scenario-verdicts.csv     one row per test case
  analysis/MANIFEST.txt              sha256 of every file in the bundle
  GAP-ANALYSIS.md                    the developer gap analysis (not regulatory)
  gap-analysis.json                  the same, machine-readable, for diffing rounds
  stufe3/official/*.zip              the official archives, byte-for-byte
  stufe3/official/extracted/         verbatim extraction of both archives
  stufe3/run.json, run.log           what was run, and the container's whole output
  stufe5/…                           the same for Stufe 5
  provenance/                        seed records, the SUT's own install record,
                                     package manifest, resolved versions, CREDENTIALS.md
```

`stufe3/official/test-report.zip` and `stufe5/official/test-report.zip` are the
**authoritative** artefacts — what the *Bestätigungsverfahren* is based on and what
is uploaded to TITUS. Nothing in this repository modifies them. Everything under
`analysis/` and both gap-analysis files are *derived*, and re-derivable from the
archives alone on any machine ([docs/en/07-reports.md](docs/en/07-reports.md)).

Verify and take the bundle off the host. The manifest covered 8582 files and
verified in two seconds on the measured round:

```sh
cd reports/<run-id> && sha256sum -c analysis/MANIFEST.txt && cd -
tar czf isik-<run-id>.tar.gz -C reports <run-id>          # 2.8 GB uncompressed
```

Most of that size is the two `debug-report.zip` archives, which are not submitted.
For a submission or a review, `analysis/`, `GAP-ANALYSIS.md`, `provenance/` and both
`test-report.zip` files come to well under 100 MB:

```sh
tar czf isik-<run-id>-slim.tar.gz -C reports \
  <run-id>/README.md <run-id>/analysis <run-id>/GAP-ANALYSIS.md \
  <run-id>/gap-analysis.json <run-id>/provenance \
  <run-id>/stufe3/official/test-report.zip <run-id>/stufe3/run.json \
  <run-id>/stufe5/official/test-report.zip <run-id>/stufe5/run.json
```

The derived documents can be regenerated at any time, including from an archived
bundle on a different machine:

```sh
make report     RUN_ID=run-20260806T093000Z
make gap-report RUN_ID=run-20260806T093000Z
```

## 10. Tear it down

```sh
make clean      # containers, network, report volume, run/ — keeps reports/, vendor/, secrets/
make destroy    # clean + vendor/ + .env.resolved            — still keeps reports/, secrets/
```

Neither touches `reports/`: it is the evidence trail, and deleting it is a manual
decision made after the results are confirmed and archived. Neither touches
`secrets/` either, so `make credentials` still works after teardown — a tester
verifying a result should not lose the credentials that produced it.

## 11. If something goes wrong

These are the five failures that actually happened during development, not a
hypothetical list.

| Symptom | Cause and fix |
|---|---|
| Everything installs, then every FHIR request fails with `#5915: Unable to allocate a license` | The licence key was not readable by uid 51773. Do not tighten its mode by hand; `make up` sets it. [Details](docs/en/02-setup.md#the-licence-files-permissions-matter-more-than-they-look) |
| A Stufe 5 run looks healthy, crawls, and produces no archive | Scenario JVM out of heap. `grep OutOfMemoryError reports/<run-id>/stufe5/run.log`, then `TESTSUITE_HEAP_MAX=32g make test-stufe5 RUN_ID=<round>`. [Details](docs/en/06-execution.md#re-running-one-level-with-more-heap) |
| An endpoint answers 404 | Two causes the status code cannot distinguish: the path is absent, **or** the credential never arrived — these endpoints refuse an unauthenticated request with 404, not 401. Check the credential first with `make credentials` and `make status`. [Details](docs/en/03-sut-iris.md#it-is-refused-with-404-not-401) |
| `make up` says the endpoints do not serve `/metadata`, but `curl` gets 200 | Stale bootstrap markers in `run/iris-state/`. Believe `curl`; continue with `make seed`. [Details](docs/en/03-sut-iris.md#the-host-side-markers-belong-to-the-container-and-clearing-them-cost-a-round) |
| `make up` times out with nothing in `bootstrap.log` | `run/iris-state/` is not writable by uid 51773, so the real error was never recorded. `make up` fixes the ownership itself; if it cannot, the directory needs to be writable by that uid. [Details](docs/en/02-setup.md#uid-51773-is-not-only-about-the-licence-file) |

More: the troubleshooting table in
[docs/en/02-setup.md](docs/en/02-setup.md#troubleshooting), and
[docs/en/09-known-limitations.md](docs/en/09-known-limitations.md) for what this rig
does not prove.

## 12. About the credentials in the reports

Every generated report states the credentials the round used, verbatim. That is
deliberate, not an oversight.

Each deployment generates its own two passwords at `make resolve` — one for the
FHIR API user the test suite authenticates as, one for the IRIS Management Portal —
in the form `Kmrf-7xtq-Vhn3-Pd2s`: readable, and compliant with IRIS's default
password policy by construction, so the instance keeps its product-default security
configuration rather than a weakened one. They are recorded in `run/CREDENTIALS.md`,
printed by `make credentials`, and stated in §1 of every `analysis/results.md`, so
that a human tester can reproduce any verdict by hand.

This is sound because of what the environment is: a lab instance brought up for one
round and destroyed once the results are verified, with access controlled physically
and at the network perimeter rather than by these values. The passwords exist —
never empty, never absent — only so the system under test remains a faithful
stand-in for a production deployment.

The one rule that does apply: **none of it reaches source control.** `secrets/`,
`.env`, `.env.resolved`, `run/` and `reports/` are all gitignored, and `make check`
verifies that mechanically on every run. Stated as a deliberate deviation in
[docs/en/09-known-limitations.md](docs/en/09-known-limitations.md#credential-documentation).

---

## Where to read next

| Document | |
|---|---|
| [README.md](README.md) | What this rig is, in one page |
| [docs/en/](docs/en/) | The rig in full: architecture, the SUT, the test tool, the test data, execution, reports, limitations |
| [docs/de/](docs/de/) | Regulatory documentation for the *Bestätigungsverfahren*, in German |
| [secrets/README.md](secrets/README.md) | What goes in `secrets/`, and what is generated |
| [.env.example](.env.example) | Every deploy-time setting, documented where the value is |
