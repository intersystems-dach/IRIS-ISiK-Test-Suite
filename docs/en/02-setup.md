# 2. Setup: running a round on a fresh host

## Prerequisites

| Requirement | Checked by | Notes |
|---|---|---|
| Docker Engine 20.10+ with Compose **v2** | `make preflight` | `docker compose`, not the legacy `docker-compose` script |
| `curl`, `tar`, `unzip`, `awk`, `sed`, `python3` | `make preflight` | Named individually so a failure says which one |
| Python 3 with `PyYAML` | `make preflight` | `pip install pyyaml` or `apt install python3-yaml` |
| An IRIS for Health licence key | `make preflight` | At `secrets/iris.key`, see [secrets/README.md](../../secrets/README.md) |
| A login to `containers.intersystems.com` | `make preflight` | WRC username + **registry token**, not your WRC password |
| ~25 GiB free disk | `make preflight` (warns) | Images ~7 GB, unpacked packages ~1.5 GB, IRIS databases and reports the rest |
| ≥24 GiB RAM | `make preflight` (warns) | Stufe 5 needs `TESTSUITE_HEAP_MAX=16g` of scenario heap plus IRIS (~2.2 GiB) and the Web Gateway. Running short does not slow the round down, it **loses** it — see [06-execution.md](06-execution.md#stufe-5-needs-heap-and-running-short-of-it-costs-the-whole-level) |
| Free host ports 52773, 51972, 9010, 9011 | `make preflight` | Change the `*_PORT_*` values in `.env` if they collide |
| `172.31.0.0/24` unused | `make preflight` | Collides neither with an existing docker network nor with a host route |

No internet access is needed beyond the two image pulls if `PACKAGE_SOURCE=refv`
(the default): the FHIR packages come out of the test suite image itself.

### If the licensed registry is unreachable: side-loading the IRIS image

`containers.intersystems.com` requires a WRC account, and some lab networks cannot
reach it at all. In that case the image can be pulled on another machine and
transferred as a file. `make resolve` supports this: when `docker pull` fails but a
local copy of the reference exists, it uses that copy and records the substitution
loudly, both in its log and as `IRIS_IMAGE_SOURCE=side-loaded` in `.env.resolved`.

On a machine that *can* reach the registry, and that runs (or emulates)
**linux/amd64** — the IRIS containers are not published for arm64:

```sh
docker login containers.intersystems.com
docker pull --platform linux/amd64 containers.intersystems.com/intersystems/irishealth:2026.2
docker save containers.intersystems.com/intersystems/irishealth:2026.2 \
  | gzip > irishealth-2026.2.tar.gz          # ~4-5 GB compressed
```

Transfer the file to the lab host and load it:

```sh
gunzip -c irishealth-2026.2.tar.gz | docker load
docker image inspect containers.intersystems.com/intersystems/irishealth:2026.2 >/dev/null && echo ok
```

The loaded image **must resolve under exactly the reference `.env` names** —
`${IRIS_IMAGE}:${IRIS_VERSION}` — because that is the reference `make resolve`
looks up. `docker save`/`docker load` preserves the repository tag, so this holds
automatically. If the mirror only offers a different name or version, either
`docker tag` it to the expected reference, or set `IRIS_IMAGE` / `IRIS_VERSION` in
`.env` to what the mirror actually provides; the latter is preferable, since the
provenance record then names the image that was really tested.

Two things to keep in mind:

- **Provenance is weaker.** A side-loaded image has no registry digest of its own
  to be re-pulled by. `.env.resolved` records the image ID instead, which still
  identifies the exact bits tested and is still comparable across hosts, and
  `IRIS_IMAGE_SOURCE` says which of the two kinds the value is.
- **The licence key must match the build.** IRIS validates the version a key was
  issued for. A key naming an older release (its `LicenseCapacity` line spells the
  version out) may be refused by a newer instance, which surfaces as a licensing
  failure in the bootstrap log rather than as a pull problem. If in doubt, obtain
  the image for the version the key names as well, and set `IRIS_VERSION`
  accordingly — a round on the key's own version is worth far more than a blocked
  round on the newest one. Note the deviation in the results if you do: the
  conformance verdict then describes that build, not 2026.2.

  This is guidance for a mismatch, not a known problem: the key used on the lab
  host activated on 2026.2 without complaint, logging `Activating Core Capacity
  license key` and `Licensed for 128 cores.` against build `2026.2 (Build 221U)`.
  The point stands as a thing to check if a bootstrap reports a licensing failure,
  and the preflight above is what would report it.

## A round, start to finish

This section assumes a host that already meets the prerequisites above. For a
*pristine* machine — including installing Docker and sizing the VM —
[INSTALL.md](../../INSTALL.md) is the from-scratch path.

```sh
git clone https://github.com/intersystems-dach/IRIS-ISiK-Test-Suite
cd IRIS-ISiK-Test-Suite

cp .env.example .env                    # edit if needed; the defaults test the newest SUT build
                                        # against the pinned test suite - see INSTALL.md section 6
cp /path/to/your/iris.key secrets/      # `make up` fixes its ownership and mode - see below
docker login containers.intersystems.com

make check                              # static self-check; no Docker needed for most of it
make all                                # the whole round
```

### The licence file's permissions matter more than they look

Do **not** `chmod 0400 secrets/iris.key`. A bind mount preserves the host inode's
owner and mode, and IRIS in the container runs as uid **51773** (`irisowner`), not
as the host user who copied the file. A `0400` key owned by uid 1000 is simply
invisible to it. `make up` therefore sets ownership and mode on the key itself
before starting anything, and `IRIS_CONTAINER_UID` in `.env` is what it uses.

The failure this prevents is worth stating in full, because nothing about it points
at permissions:

1. IRIS **does not fail to start.** It logs `LMF Error: No valid license key`,
   falls back to a zero-capacity diagnostic licence, and carries on.
2. The entire installation then **succeeds** — namespaces, all 35 FHIR packages,
   both endpoints, security, the lot. Several minutes of work that has to be thrown
   away, and on a slower host considerably more.
3. Every subsequent FHIR request fails with `ERROR #5915: Unable to allocate a
   license`, recorded **only** in `%SYS.Audit` — not in `messages.log`, not in the
   HTTP response. At the HTTP layer it is indistinguishable from an authentication
   or routing fault.

Hence the licence preflight in
[`bootstrap.sh`](../../iris/bootstrap/bootstrap.sh), which runs *before* the
install and takes IRIS's own word for the verdict: it requires an
`LMF Info: Activating` line and rejects an `LMF Error`. Two details of that check
are themselves findings:

* It reads **only the current boot's** portion of `messages.log`. That file is
  append-only and the official image ships one already populated, including an
  `LMF Error: No valid license key` from its own unlicensed build. Grepping the
  whole file fails a perfectly good key.
* It inspects **nothing** about the key file — no unit counts, no expiry
  arithmetic, no product matching. A licence is valid exactly when IRIS says it is.
  An earlier version tested `KeyLicenseUnits() != 0` and rejected a working key: on
  a **Core Capacity** key that value is legitimately 0, because the capacity is
  expressed in cores. A good key logs `Activating Core Capacity license key` and
  `Licensed for 128 cores.`

### uid 51773 is not only about the licence file

The same bind-mount ownership rule applies to the one **writable** mount,
`run/iris-state/`, and gets it wrong differently: a directory the operator creates
with a default 0775 is read-only to uid 51773, so the bootstrap cannot write its
status file or its log — and the real ObjectScript error therefore never reaches
`run/iris-state/bootstrap.log`. `make up` reports the generic "IRIS exited before
the endpoints were ready" with no cause recorded anywhere.
[`40-up.sh`](../../scripts/40-up.sh) attempts `chown` to `IRIS_CONTAINER_UID` and
falls back to 0777, which is acceptable for exactly this directory: it holds this
round's log and provenance record, no credentials, and is destroyed with the round.

That makes three uids to keep straight, none of them chosen by this project:

| uid | Who | What it constrains |
|---|---|---|
| **51773** | `irisowner` in the IRIS image | `secrets/iris.key`, `run/iris-state/` |
| **100** | `tiger-testsuite` in the suite image | `run/tiger/*.yaml`, which is why they are 0644 ([08-known-limitations.md](08-known-limitations.md#credential-documentation)) |
| **1000** | the host operator running `make` | Everything as created before the two fixes above |

`make all` is `preflight → resolve → packages → up → seed → test → report`, and the
two conformance runs dominate it. Everything before them is quick: on the 8 vCPU
lab host, bring-up measured 4m40s cold and 7s on the reuse path and `seed` under a
minute, while the two levels took 1250 s and 2050 s — a full round is 54 minutes on
the recommended 16 vCPU host and about an hour and five on that one. See
[06-execution.md](06-execution.md#what-one-round-is) for the measured breakdown and
for why the bootstrap timeout is nevertheless generous.

Nothing prompts. The two passwords the environment needs are generated at
`make resolve` and stored in `secrets/`; see below.

### Watching it

```sh
make status          # containers, network, endpoints, credentials
make logs            # tail the SUT's bootstrap log
make credentials     # this instance's credentials and ready-to-paste curl lines
```

`make up` prints progress from the bootstrap's own status file rather than just
polling Docker's health status, because Docker reports `starting` for the whole
45-minute `start_period` regardless of what the healthcheck returns — including
for a bootstrap that has already failed for good.

### What you get

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

`test-report.zip` inside `stufe3/official/` and `stufe5/official/` is the artefact
the *Bestätigungsverfahren* is based on and what is uploaded to TITUS. Nothing in
this repository modifies it. See [07-reports.md](07-reports.md).

## Running a phase by hand

Every target wraps one script, and each script can be run directly:

```sh
scripts/00-preflight.sh
scripts/60-run-tests.sh stufe5
scripts/70-collect-report.sh
```

They need `REPO_ROOT` exported or will derive it from their own location, and they
read `.env.resolved` if it exists and `.env` otherwise. This is what makes the rig
usable on a host without `make`.

Re-running one phase of a finished round:

```sh
make report RUN_ID=run-20260806T093000Z     # regenerate the derived documents only
make gap-report RUN_ID=run-20260806T093000Z # regenerate just the gap analysis
```

Neither re-runs the tests or touches the archives, so the derived documents can be
regenerated and diffed at any time — including from an archived bundle on a
different machine.

## Credentials

Each deployment generates its own two passwords at `make resolve` and reuses them
for the life of that deployment:

| Purpose | User | Stored in |
|---|---|---|
| FHIR API — the test suite authenticates as this, HTTP Basic | `SUT_USER`, default `isikapi` | `secrets/sut.password` |
| IRIS Management Portal — for the human tester | `IRIS_MGMT_USER`, default `_SYSTEM` | `secrets/iris-mgmt.password` |

Precedence for each: a non-empty value in `.env` wins, then the file in
`secrets/`, then a freshly generated one. Generated passwords look like
`Kmrf-7xtq-Vhn3-Pd2s` — four groups of four, no glyphs that can be misread
(`O 0 I l 1`). That form satisfies IRIS's default `PasswordPattern` by
construction, so the instance's password policy is left at the product default
rather than weakened.

**These credentials are documented on purpose,** in `run/CREDENTIALS.md`, in
`make credentials` and `make status`, in §1 of every generated `results.md`, and
verbatim in the `Authorization` headers the gap analysis quotes. The rig is a lab
instance brought up for one round and destroyed once the results are verified;
access to it is controlled physically and at the network perimeter, not by these
values, which are kept in place — never empty, never absent — only so the SUT
stays a faithful stand-in for a production deployment. The one rule that does
apply: **none of it reaches source control**, and `make check` verifies that
mechanically. See [08-known-limitations.md](08-known-limitations.md#credential-documentation)
for this stated as a deviation.

```sh
make credentials     # the sheet, or the live values if run/ was cleaned
```

## Choosing where the FHIR packages come from

Which packages are loaded is decided by the test suite image either way — the
authoritative `name@version` list comes from the image's own
`refv-plugins/*/config.yaml`, so the SUT is always configured with exactly the
packages the Gematik reference validator validates against. `PACKAGE_SOURCE` only
decides where the *payloads* come from:

| Value | Behaviour |
|---|---|
| `refv` | Use the `.tgz` files bundled inside the image. No internet access needed. **Default.** |
| `simplifier` | Additionally download the same `name@version` tarballs from `packages.simplifier.net`, use those, and record a sha256 parity table bundled-vs-published in `vendor/packages/PARITY.txt` |

`simplifier` is slower but produces independent evidence that the bundled
packages are the published ones. Either is a valid conformance round; the choice
is recorded in `run.json` and in the results document.

## Interactive testing

```sh
make manual                                        # brings up + seeds, then the WorkflowUI
make clean && MANUAL_STUFE=stufe3 make manual      # switch level
```

`MODE=manual` renders a second set of Tiger configuration files with
`runTestsOnStart: false` and mounts *those*, so the container comes up, exposes
the Tiger WorkflowUI on `http://127.0.0.1:9010` and waits. One container runs
exactly one Maven profile, so only one level is available per `make manual`.

[90-manual.sh](../../scripts/90-manual.sh) verifies the mounted file really is
the non-auto-running variant before starting anything: mounting the wrong one
would launch a full conformance run (895 test cases at Stufe 5) that nobody asked
for.

## Teardown

```sh
make clean      # containers, network, report volume, run/    — keeps reports/, vendor/, .env.resolved
make destroy    # clean + vendor/ + .env.resolved             — keeps reports/
```

Neither touches `reports/`: it is the evidence trail, and deleting it is a manual
decision made after the results have been confirmed and archived. `make clean`
also keeps `secrets/`, so `make credentials` still works after teardown — a
tester verifying a result should not lose the credentials that produced it.

## Troubleshooting

| Symptom | Where to look |
|---|---|
| `make up` times out | `run/iris-state/bootstrap.log` and `bootstrap.status`. The timeout (`IRIS_BOOTSTRAP_TIMEOUT`, default 5400s) is generous on purpose; a genuinely broken bootstrap reports itself in the status file within minutes |
| `make up` says the endpoints do not serve `/metadata`, but `curl` gets 200 | The bootstrap markers in `run/iris-state/` were deleted while the container kept running, so nothing rewrote them. Believe `curl`: the SUT is fine. `make up` no longer clears them for a running container, and the run can be continued with `make seed` ([03-sut-iris.md](03-sut-iris.md#the-host-side-markers-belong-to-the-container-and-clearing-them-cost-a-round)) |
| Endpoints answer 404 | Two very different causes, and the status code cannot tell them apart: the path really is absent, **or** the credential never arrived — these endpoints refuse an unauthenticated request with 404 rather than 401. Check the credential first with `make credentials` and `make status` (which prints the credential it used next to the code it got), then `make logs`; the security step is the last thing the bootstrap does. Full account in [03-sut-iris.md](03-sut-iris.md#it-is-refused-with-404-not-401) |
| Management Portal demands a password change | The bootstrap's security step did not complete. The official image ships its predefined accounts with expired passwords; step 4 of `EnsureSecurity()` is what fixes that |
| `make seed` reports test-data defects | Our data, not IRIS. `run/seed/SEED.txt` names each resource and the validator's issue. Nothing proceeds until it is fixed, deliberately |
| No report archive after a run | `reports/<run-id>/<stufe>/run.log`. Maven assembles the archives in `post-integration-test`; a run killed part-way produces no archive at all, not a partial one |
| A Stufe 5 run slows to a crawl, or times out, while looking healthy | `grep OutOfMemoryError reports/<run-id>/stufe5/run.log`. The scenario JVM ran out of heap, Tiger's Rbel parser threads died, and each remaining scenario now burns a 100-second parse timeout — at 0.3% CPU, so it reads as a hang. Re-run just that level with more heap against the same round: `TESTSUITE_HEAP_MAX=32g make test-stufe5 RUN_ID=<round>`, then `make report RUN_ID=<round>`. Full account in [06-execution.md](06-execution.md#re-running-one-level-with-more-heap) |
| A citation in `results.md` does not resolve | `70-collect-report.sh` checks every cited path and warns; re-run `make report` for the round |
