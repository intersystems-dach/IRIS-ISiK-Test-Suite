# 6. Executing the test procedure

## What one round is

```
make preflight  →  resolve  →  packages  →  up  →  seed  →  test  →  report
                                                              │
                                                    test-stufe3, then
                                                    test-stufe5 — serialised
                                                              │
                                                    then, only with
                                                    CONNECT_ENABLE=true:
                                                    tokens, then test-connect
```

`make all` runs the chain end to end, and the two conformance runs dominate it —
bring-up is a rounding error by comparison. Measured on two hosts, both
`PACKAGE_SOURCE=refv`:

| Phase | 8 vCPU / 31 GiB | 16 vCPU / 62 GiB | Note |
|---|---|---|---|
| `up` — cold bootstrap, both namespaces | **4m40s** | **3m31s** | The FHIR package import is most of it, and it is I/O bound rather than CPU bound |
| `up` — reuse path, namespaces already present | **7s** | **0s** | What a re-run of an existing instance costs |
| `seed` | under a minute | **22s** | 131 resources across both endpoints, each read back and `$validate`d |
| `test-stufe3` | **1250 s** (20m50s) | **1084 s** (18m4s) | 506 cases. Completes inside the JVM's default heap |
| `test-stufe5` | **2050 s** (34m10s) | **1803 s** (30m3s) | 895 cases, **with `TESTSUITE_HEAP_MAX=16g`**. Without it the level does not finish at all — see below |
| `report` + `gap-report` | under a minute | **23s** | Pure functions of the archives |
| Full `make all`, cold | ~1h05m, summed | **54m**, timed | Only the 16 vCPU round was driven as one `make all`, 08:47:13 to 09:41:44 UTC, image pulls included |

Both level figures are `run.durationSeconds` from each round's own `run.json`, so any
round can be compared against them directly. The wall-clock ceiling is
`TESTSUITE_TIMEOUT`, 3 h per level, which the measured Stufe 5 uses about a sixth
of — the margin exists for the heap-starved case, not for the healthy one.

**Doubling the host bought 12%.** Compare the two levels rather than the totals,
since those are the phases measured the same way on both hosts: 3300 s together on
8 vCPU, 2890 s on 16. Neither level parallelises across scenarios — the suite drives
one scenario at a time and spends most of it waiting on the SUT — so twice the
machine does not buy anything like twice the speed. The larger host is worth having
for the heap headroom described below, not for the throughput. Sizing guidance is in
[INSTALL.md](../../INSTALL.md#2-hardware).

**More heap bought nothing either — and that is the point.** Stufe 5 re-run on the
16 vCPU host at `TESTSUITE_HEAP_MAX=32g` took **1838 s** (30m38s) against **1803 s**
(30m03s) at 16g — the same within noise — and produced *identical* verdicts: 839 of
895 passed both times, the same 86 failing cases, the same 33 distinct defects. So
16g is not a performance setting that more of improves; it is a threshold. Below it
the level is lost, above it nothing changes.

One consequence of re-running a level into an existing round: it **overwrites** that
level's archives and `run.json` rather than adding to them, because the round
directory holds one result per level. The bundle therefore documents the 32g run
(`durationSeconds: 1840`, `testTool.scenarioHeapMax: 32g`,
`overrides: TESTSUITE_HEAP_MAX=32g`) and not the 16g one it replaced. Copy the
directory aside first if both need to be kept.

Bring-up figures are measurements, not guarantees: the package import is I/O bound,
so a slower disk or a cold package cache moves it a long way, and
`IRIS_BOOTSTRAP_TIMEOUT` therefore allows 90 minutes. That margin is a safety
ceiling, not an expectation — [`40-up.sh`](../../scripts/40-up.sh) returns as soon
as the endpoints answer, and its failure paths catch a broken install in seconds
rather than leaving it to the timeout.

Nothing prompts, and every target wraps exactly one script, so any phase can be
repeated or run by hand ([02-setup.md](02-setup.md#running-a-phase-by-hand)).

**The two levels never run concurrently.** The upstream image runs one Maven
profile per container start and wipes its report directory at the beginning of
every run, so `make test` is `test-stufe3` then `test-stufe5`, and each level's
archives are lifted out of the container before the next one is allowed to start.
See [04-testtool.md](04-testtool.md#what-the-entrypoint-does-and-everything-that-follows-from-it).

**A round configured for `Connect` has one further invocation, appended and not
substituted.** `make test` runs every level first, then — only if
`connect_enabled` — mints the access tokens and runs Stufe 5's `Connect` module a
second time, on real bearer tokens, through its own Tiger configuration. The
complete official scope is unchanged and still includes those cases; this adds an
archive rather than narrowing one. It runs last so the tokens are obtained as late
as possible, and the scripts are called directly rather than through
`make test-connect` so that their one-hour life is not spent re-walking the
preparation chain. The archive is `connect/`, the supersede rule that relates it
to `stufe5/` is in [08-connect.md](08-connect.md#it-does-not-narrow-the-official-run),
and the measured wall-clock cost is under two minutes for 32 cases.

## The run identity

`RUN_ID` is computed **once** per `make` invocation
(`run-$(shell date -u +%Y%m%dT%H%M%SZ)`, `:=` not `=`) and passed down explicitly,
so both levels of one round land in the same `reports/<run-id>/` directory. With
recursive expansion each use would produce a different timestamp and a round would
scatter itself across two directories.

It can be set to re-derive documents for a finished round:

```sh
make report RUN_ID=run-20260806T093000Z
```

**It must be set explicitly when the invocations are driven separately**, which is
how a `Connect` round is driven by hand. Each bare `make` computes its own
timestamp, so three unqualified invocations would scatter one round across three
directories and the supersede rule would have nothing to apply:

```sh
RUN_ID=run-20261116T120000Z make test-stufe5
RUN_ID=run-20261116T120000Z make test-connect
RUN_ID=run-20261116T120000Z make report
```

`RUN_ID` is honoured from the environment by `scripts/lib.sh` rather than only
from the `make` command line, so either form works. `make test` needs none of
this — it drives all of them from one invocation.

## Exit codes: what "success" means here

**A failing conformance run is the expected outcome of this project.** IRIS for
Health is known not to be fully ISiK compliant, so the harness must distinguish
*the SUT failed a scenario* from *the harness failed to produce evidence*, and act
only on the second.

| Layer | Exit status means |
|---|---|
| Maven, inside the container | Nothing usable. The upstream profiles set `testFailureIgnore=false`, so `BUILD FAILURE` is what a *normal* failing conformance run reports — it cannot distinguish that from a run that never started |
| The container | Nothing. Its entrypoint ends in `\|\| true`. `run.json` records `containerExitCode` and no code path reads it back |
| `60-run-tests.sh` | 0 for a run in which every scenario failed; **non-zero when `test-report.zip` is absent, or when it contains no `unresolvedReports/*.json`** |
| `70-collect-report.sh` | 0 even when it reports an anomaly, so `make report` always reaches `make gap-report`. Non-zero only for a genuine harness error — no run directory, no archive at all, no requirement extraction |
| `50-seed.sh` | Non-zero on a test-data defect. This one *does* block, deliberately |

The asymmetry is the design. No evidence means there is nothing to analyse, which
is fatal. A wall of failing scenarios is the deliverable.

**An archive is not by itself evidence,** and this cost a round. `make test-stufe3`
once returned in 21 seconds reporting `OK test-report.zip 4.0K (archive intact)`
over an archive holding nothing but `pom.xml`: Tiger had died reading its own
configuration before a single scenario started (see
[04-testtool.md](04-testtool.md#the-rendered-files-carry-a-credential)), and
because the entrypoint's `mv` is no more conditional than its `|| true`, the empty
archive was still produced, moved, checksummed and accepted. `60-run-tests.sh`
therefore counts the archive's `unresolvedReports/*.json` entries — the Serenity
`TestOutcome` records, one per executed scenario, and the same files every verdict
in [07-reports.md](07-reports.md) is derived from — and treats zero of them as a
harness error, recording the count as `artefacts.testOutcomeRecords` in `run.json`
either way. The question is asked from the certification artefact rather than from
a log line, and it is the same question the analysis asks, asked early enough to
be actionable.

`make test` must never stop before `make report`: the gap analysis is what turns
those failures into something a developer can act on.

## Pre-flight, before any container starts

[60-run-tests.sh](../../scripts/60-run-tests.sh) refuses to start a level until
all of the following hold. Each check exists because its failure mode is a
*plausible-looking wrong report* rather than an error.

| Checked | Why it is fatal |
|---|---|
| Both rendered `run/tiger/tiger-isik-stufe{3,5}.yaml` and `run/testdata` exist | Tiger answers a missing configuration with a Spring stack trace that never names the file |
| `<project>-iris` is running | Otherwise every scenario fails on connection refused |
| `run/seed/<Level>.json` exists | The suite seeds nothing. A run against an unseeded endpoint wastes an hour and then reports our own missing test data as a wall of IRIS conformance gaps — the one misattribution this project exists to avoid |
| …and reports **zero** incomplete resources (`writeFailed + readFailed`) | Same reason. The record is inspected, not trusted, and its summary is carried into `run.json` so the report says which corpus the run started from |
| The tag expression names the level being run | Maven would run the `stufe3` profile while the filter selected nothing, and the symptom is a report full of zeroes with no error reported anywhere |
| No stale `<project>-testsuite-*` container | An exited container holds its network endpoint until removed, and all three services share one static address, so `compose up` fails with "address already in use" |

Two conditions warn rather than block:

* `validationErrors > 0` in the seed record — reachable only with
  `SEED_VALIDATE_STRICT=0`, which is an explicit operator decision. Blocking would
  override that decision; instead it is said out loud and recorded in `run.json`
  so the analysis can qualify the affected scenarios rather than counting them as
  IRIS gaps.
* A tag expression other than the official scope — see below.

## Scope, and how a debugging run is marked

| Level | Default tag expression |
|---|---|
| Stufe 3 | `@Stufe3 and (not @Optional)` |
| Stufe 5 | `@Stufe5 and (not @Optional)` |

These are the upstream `pom.xml` profile defaults and they *are* the official
certification scope: every mandatory scenario, no optional ones. Any other
expression sets `conformanceScope: false` in `run.json`, prints a warning naming
the official expression, and makes the generated results document state that the
round is a debugging run whose report must not be submitted for the
*Bestätigungsverfahren*.

The value is exported into the compose environment explicitly rather than left to
the compose default, so the string that ran and the string recorded in `run.json`
are the same string.

## Watching a run

The container's entire output is streamed to
`reports/<run-id>/<variant>/run.log` from the moment it starts — to a file, not to
the terminal, because this runs for up to three hours, usually detached, and the
file is what gets archived. It is also the only place a Tiger startup failure is
explained.

Progress is printed every 120 s as the log's line count plus its last non-empty
line, truncated to 140 characters:

```
[ 14m] 8213 log lines | │ Then FHIR current response body is a valid Patient resource …
```

That pairing is deliberate: the line count proves motion, the tail line says what
is happening, and neither depends on a suite version's log format. Without it a
healthy run is indistinguishable from a hung one on a remote host. A Maven stack
trace would otherwise swamp the progress log, hence the truncation.

```sh
tail -F reports/<run-id>/stufe5/run.log      # the whole thing
make status                                   # containers, network, endpoints, credentials
```

The Tiger WorkflowUI is on `http://127.0.0.1:9010` and the traffic log on 9011
during a run.

`TESTSUITE_TIMEOUT` (default 10800 s = 3 h) is the wall-clock ceiling per level.
Polling is every 15 s.

## Stufe 5 needs heap, and running short of it costs the whole level

**`TESTSUITE_HEAP_MAX=16g` is not a performance tunable.** It is the difference
between a Stufe 5 round that finishes and one that is lost after hours while
appearing healthy the entire time. This was measured, not anticipated.

Tiger's `RbelLogger` retains a **parsed tree of every HTTP message of the whole
run** — that retention is what makes `tgrCurrentResponse`-style assertions and the
traffic log possible. Stufe 5 sends several times Stufe 3's traffic, and the run
does not release it, so the heap grows monotonically across the level. On the lab
host (8 vCPU, 31 GiB) at roughly 600 of Stufe 5's 895 cases:

| | Before | After |
|---|---|---|
| Throughput | ~12 scenarios/min | **~0.5 scenarios/min** |
| Heap (G1, `jcmd GC.heap_info`) | — | 8,209,643K of 8,216,576K — **99.9%** |
| Container CPU | ~775% | **0.3%** |

The mechanism: once the heap is full the Rbel parser threads die of
`OutOfMemoryError`, and every subsequent scenario then blocks for the full
100-second timeout in `RbelMessageHistory.waitForGivenMessagesToBeParsed` before
continuing. The remaining 281 cases projected to about **9 hours** against the 3-hour
`TESTSUITE_TIMEOUT` — and a timed-out run produces **no archive at all**, so the
level would have been lost entirely.

Three things make this genuinely hard to spot, which is why it is documented at
this length:

* **The run never reports an error.** The log keeps growing, the tail line keeps
  changing, scenarios keep being reported as passed and failed. The progress line
  says `13890 log lines | … Saved HTML report of scenario …`, which is what a
  healthy run says.
* **The container looks idle, not overloaded.** 0.3% CPU reads like a hang or a
  network wait. It is the opposite: the main thread is blocked waiting on futures
  that will never complete because the threads that would complete them are dead.
* **Stufe 3 gives no warning.** It completes in 20 minutes inside the default heap
  with zero tripped timeouts, so a green Stufe 3 says nothing about Stufe 5.

The heap reaches the right JVM via
`MAVEN_ARGS=-DargLine=-Xmx${TESTSUITE_HEAP_MAX}` in
[compose/testsuite.yml](../../compose/testsuite.yml). **`MAVEN_OPTS` would be
wrong**: it sizes the Maven JVM, whereas the scenarios — and the Rbel history —
live in the JVM `maven-failsafe-plugin` forks. The upstream `pom.xml` sets no
`argLine` of its own in 2.7.0, so nothing is overridden. Left unset, the JVM takes
its own default of a quarter of host RAM, which on a 31 GiB host is 7.84 GiB and is
exactly the value that failed.

Detection now exists at all three layers:

| Layer | What it does |
|---|---|
| `make preflight` | Warns when `TESTSUITE_HEAP_MAX` is unset, or when the host has under 24 GiB |
| `60-run-tests.sh` | Greps each progress interval for `OutOfMemoryError` and, on the first hit, warns that the run will not finish and names the remedy |
| `results_report.py` | Raises the **blocking** `scenarioJvmOom` anomaly from the archived `run.log`, so an affected level cannot be read as a conformance result |

`run.json` records `testTool.scenarioHeapMax`, so a round can be judged after the
fact. The value is `null` when the JVM default was used — which is itself the
explanation for a round that timed out.

### Re-running one level with more heap

The remedy is to re-run the affected level alone, against the same round:

```sh
TESTSUITE_HEAP_MAX=32g make test-stufe5 RUN_ID=run-20260807T084713Z
make report RUN_ID=run-20260807T084713Z
```

`TESTSUITE_HEAP_MAX` is one of four settings a command line may override —
`TESTSUITE_TIMEOUT`, `IRIS_BOOTSTRAP_TIMEOUT` and `MANUAL_STUFE` are the others,
listed as `RUN_OVERRIDABLE` in [scripts/lib.sh](../../scripts/lib.sh). They share
one property: each describes *how* a level is run, and none of them changes what is
being tested.

That distinction is the reason the mechanism exists at all. `load_env` sources the
configuration file with `set -a`, so the file's value overwrites anything the caller
exported — which means that before this list existed, `TESTSUITE_HEAP_MAX=32g make
test-stufe5` was **silently discarded**: the container started with the file's old
heap, the run reported success, and nothing anywhere said the override had been
ignored. Found by walking [INSTALL.md](../../INSTALL.md) on a fresh host, where a
32g round turned out to be a second 16g round.

The same defect had a second half, and it survived that fix: **`MODE` and
`PACKAGE_SOURCE` were documented as overridable and were not.** The resolver
rewrites their lines when it writes `.env.resolved`, so an override handed to `make
resolve` works — but handed to any later phase it was discarded exactly as the heap
setting had been. `PACKAGE_SOURCE=simplifier make packages` ran the `refv` route and
logged `PACKAGE_SOURCE=refv` while doing it. Found on the official 2026.2 round when
regenerating `PARITY.txt` *after* the round, which is precisely when it is needed:
the parity table is evidence about the packages, and producing it must not require
editing the finished round's own provenance record. They now live in
`CONFIG_OVERRIDABLE`, are reinstated the same way, and — because `PACKAGE_SOURCE`
changes which bytes are loaded rather than merely how a level runs — an overridden
pass stamps the fact into the header of the `MANIFEST.txt` and `PARITY.txt` it
writes, so no artefact can claim a payload source its pass did not use.

`load_env` now reinstates these settings after sourcing, logs each one that takes
effect (`override: TESTSUITE_HEAP_MAX=32g (.env.resolved says 16g)`), and records
them in that level's `run.json` as `overrides`, which the results document states as
*Command-line overrides*. An override that takes effect invisibly is only
marginally better than one that is ignored.

Everything else stays file-only deliberately. `.env.resolved` is the provenance
record for the round, and a version or an endpoint that could be changed per
invocation would let a round test one thing and record another.

## The timeout path, and why it usually yields nothing

Maven assembles both archives in `post-integration-test`, i.e. at the very end.
**An interrupted run produces no archive at all — not a partial one.** On timeout
the script therefore:

1. warns, naming `TESTSUITE_TIMEOUT` and the elapsed minutes;
2. stops the container *gracefully* — `docker stop -t 150` against the 120 s
   `stop_grace_period` in `compose/testsuite.yml`, so Maven gets the full grace
   period plus margin to finish an assembly already in progress;
3. archives whatever exists and says honestly that there may be nothing;
4. dies with the remedy: raise `TESTSUITE_TIMEOUT` and run again.

## Archiving, immediately and verbatim

Archiving happens **before anything else**, because the next container start wipes
the report volume.

* Primary route: `docker cp <container>:/app/report/.`. This resolves the
  container's mount points, so it reaches into the named volume even after the
  container has exited.
* Fallback: a throwaway container is *created* but never started, with the report
  volume attached, purely as a handle for `docker cp`. It uses the test suite image
  itself, because that image is certainly present locally — pulling anything else
  at this point could fail on an air-gapped host.

Archives are classified by substring (`*test-report*.zip`, `*debug-report*.zip`),
never by exact name: the upstream Maven assembly descriptors put the project
version in the filename, so an exact match would break on the next suite release.

Each archive is then verified with `unzip -t`, which reads the central directory
and every entry's CRC. **An archive Maven was still writing is worse than a
missing one — it looks like evidence and is not.** A corrupt archive is reported
as such and kept.

`SHA256SUMS` is written over the archives as received, so an inspector can
re-compute the checksums without trusting this repository.

## `run.json`: what was run

Written whether or not the run produced an archive, because a run that produced
nothing is itself a fact the round has to account for. Every value reaches the
Python writer through the **environment**, never interpolated into the source: a
tag expression contains parentheses and quotes and a Tiger error message can
contain anything at all, and substituting those into source would be one stray
quote away from a syntax error in the very step that writes the evidence.

| Key | Contents |
|---|---|
| `runId`, `level`, `mavenProfile` | Identity |
| `variant` | **Which of the round's invocations this archive is** — `stufe3`, `stufe5` or `connect`, and the name of the archive directory. Two of them carry level `Stufe5`, which is why the directory is named after this and not after the level |
| `scopePartition` | **How this archive's scope relates to the level's official scope.** Four values, below. Read by `results_report.py` rather than inferred from the tag expression, so a restricted archive is never presented as a statement about the whole level |
| `authorizationServer` | Connect round only, `null` otherwise: the image and its digest, the issuer, the audience, the grant type (`client_credentials`) and where the token record is to be read — cited at its place **in the bundle**, not at the `run/` path it was written to, since `make clean` deletes that. Never a token value, and never the realm secret |
| `startedAt`, `finishedAt`, `durationSeconds`, `timedOut` | Timing |
| `containerExitCode` | Recorded, never acted on |
| `testsToRun`, `officialScope`, `conformanceScope` | The scope, and whether it was the certification scope |
| `sut` | Base URL from the subnet and from the host, image, digest, `$ZV` build, namespace |
| `testTool` | Image, digest, IP |
| `packageSource`, `mode` | The two deploy-time decisions |
| `credentials` | API user/password/scheme and management user/password, **on purpose** |
| `seed` | The seed record's summary: resources, written, read back, validated, validation errors, endpoint, the server's self-reported software |
| `artefacts` | Archive names, which is the test and which the debug report, `run.log`, and sha256 per archive |

`credentials` is top-level rather than inside `sut`, so nothing that already reads
`sut` changes shape. It carries a `_comment` stating why it is there. The archive
hashes are computed in Python during the same read that writes them, so `run.json`
and `SHA256SUMS` describe the same bytes by construction rather than by two
independent shell-outs.

### The four scope partitions

| `scopePartition` | What the archive is | `conformanceScope` |
|---|---|---|
| `complete` | The level's official certification scope. **The submission artefact** | `true` |
| `connect-only` | The `@Connect` module alone, selected with upstream's own tag and run with real bearer tokens against the TLS endpoint. A subset of the official scope, run a second time: additional evidence that **supersedes** the Connect cases of the round's complete archive and is not added to them | `false` |
| `without-connect` | The official scope minus `@Connect`. A deliberate restriction — useful for re-running the other nine modules while working on something else — and **not** a submission artefact. 32 mandatory cases do not run, and the operator is warned so on the terminal | `false` |
| `narrowed` | None of these: a debugging run. Its report is not a conformance result and must not be submitted | `false` |

`conformanceScope` says one thing only: whether *this archive* is the level's
official scope. A restricted run is not, however deliberate, because the
submission form takes one `test-report.zip` per level — and `scopePartition` is
what tells a deliberate restriction apart from a debugging run.

Where a round holds both a `complete` and a `connect-only` archive of the same
level, the merged level report in `analysis/` records
`complete+connect-superseded`, recounts its totals from the effective case set so
that no figure is a sum over two measurements of the same case, and names the
superseded module. The supersede rule is in
[08-connect.md](08-connect.md#it-does-not-narrow-the-official-run).

## What is fatal at the end of a level, and what is not

* **No `test-report.zip` → fatal.** Even a run in which every scenario fails
  produces it. Its absence is a harness error, and the script prints the last 30
  log lines and points at `run.log`; the usual causes are an unreachable SUT, an
  unparseable `tiger-isik-<variant>.yaml`, or a missing test-data file.
* **No `debug-report.zip` → warning.** The certification artefact is present, so
  the round stands. But the debug archive is where the request/response bodies and
  validator messages live, so without it the gap analysis can name the failing
  scenarios and little else. The warning says exactly that.

## The two denominators

Every figure in the results document is reported twice, and the two are not
interchangeable:

* **Executed cases** — everything Cucumber ran, *including* each feature's
  `@vorbedingung` / `@Precondition` block. Those blocks are real scenarios and they
  do execute; their steps record the required test data as prose and assert nothing
  against the SUT.
* **Asserting cases** — the executed cases that actually test the SUT. **This is
  the conformance denominator.**

| | Stufe 3 | Stufe 5 | Total |
|---|---:|---:|---:|
| Executed | 506 | 895 | **1401** |
| Asserting | 432 | 783 | **1215** |
| Precondition blocks | 74 | 112 | **186** |

The 186 precondition blocks are 13 % of the executed count and they pass
unconditionally, because they assert nothing. A rate computed over 1401 therefore
cannot fall below 13 % however the SUT behaves: it is **too favourable**, which is
the kind of error an inspector looks for first. 1215 is the only denominator a
conformance statement may use.

**That table is counted on suite 2.7.0**, the last version to carry both levels;
3.0.0 removed the Stufe 3 tests outright ([04-testtool.md](04-testtool.md)). All
three figures are properties of the suite version and move with it — on 3.0.9 the
same counts are 1 233 executed, 1 044 asserting and 189 precondition blocks, for
Stufe 5 alone. Never carry a denominator from one suite version into a statement
about another. `results_report.py` derives all three per round from that round's
own outcome records rather than assuming any of them, which is what makes the
reconciliation below a straight equality check.

A `Scenario Outline` counts **one case per `Examples` row**. That is Serenity's own
rule (`TestOutcome.getTestCount()` returns `testSteps.size()` for a data-driven
outcome), which is why these totals exceed the scenario-keyword count and why the
reconciliation can be a straight equality check.

`results_report.py` reconciles three independent counts per level and reports any
disagreement as an anomaly rather than picking one:

1. **Expected** — from `vendor/requirements.json`, i.e. from the pinned image's own
   feature files evaluated against the tag expression the level actually ran with.
2. **Report** — counted from the `TestOutcome` files in
   `official/extracted/test-report/unresolvedReports/`.
3. **Independent** — `results.counts.total` from `serenity-summary.json` in the
   debug archive, produced by Serenity's own summary reporter from the same
   outcomes. It exists solely to catch a counting error in this project's
   generator.

   It is independent of *this generator*, not of Serenity. Where a level contains
   two feature files of the same basename it reproduces Serenity's own
   misattribution and reads high — 905 rather than 895 for Stufe 5 — so the
   equality is asserted as `Report + discarded foreign steps == Independent`, and
   the agreement is recorded as a `totalsReconciled` note naming the difference.
   A difference that does *not* add up is still the blocking `totalsDisagree`.

### Two feature files of the same basename

Serenity 5.3.10 attaches the wrong `Examples:` table to an outcome when two
feature files in one run share a basename. Stufe 5 of ISiK 2.7.0 has **16** such
basenames among its 124 features and Stufe 3 has none, so only Stufe 5 is
affected — and a clean Stufe 3 reconciliation is therefore no evidence that the
counting is right.

What it costs, measured on a full run:

| Symptom | Measured |
|---|---|
| Plain `Scenario:`s given another scenario's 6-to-14-row `Examples` table | 57 outcomes (`foreignExampleTable`) |
| Steps of the *other* file interleaved into a record, each numbered `1:`…`5:` twice | 10 steps in 2 outcomes (`foreignExampleStep`) |
| Case count | **905** reported against the specified **895** |
| A failing case reported as passing | **1** — `Stufe5/Medikation/Practitioner-Search.feature:40` |

That last one is the reason this is corrected rather than noted. The scenario has
`result: FAILURE` on both the outcome and its single step, but row 0 of the
foreign table it was handed says `SUCCESS`; preferring the row — which is correct
for a genuine outline, where the row carries the per-example verdict — turns a
real ISiK conformance failure into a pass.

`results_report.py` therefore attributes each executed step to an `Examples:` row
by the parameters the step records in its own title (Serenity writes them there
as `"3: <scenario> ({searchParamValue=url, searchParamType=uri})"`), never by
position. Across both levels of a full run every one of the 74 genuinely
data-driven outcomes matched each of its steps to exactly one row, in order and
without repetition, and the only unmatched steps were the 10 belonging to the
other file. Both levels then reconcile exactly.

The correction lives in the analysis only. `test-report.zip` — the artefact the
*Bestätigungsverfahren* is based on — is never modified, and every corrected
outcome is listed with its own report path in §7 so the correction can be checked
against the archive by hand.

## Anomalies that change how a result reads

Recorded in §7 of `results.md` and in `results.json`. These are properties of the
run or of the archive, **not conformance findings**. Byte-identical records are
deduplicated (one misplaced feature file would otherwise raise the same record once
per scenario) and the markdown table caps each kind at 4 rows with a count of the
rest, since a table of hundreds of near-identical rows hides the other anomalies
instead of reporting them. The complete uncollapsed list is always in
`results.json`.

The blocking set — `scopeMismatch`, `notExecuted`, `missingTestReport`,
`totalsDisagree`, `wrongLevel`, `archiveNotExtracted`, `scenarioJvmOom` — means the
report does not cover the full certification scope, and the document says so in a
block quote.

`scopeExcess` is the opposite direction of `scopeMismatch` and is reported in
different words, because *more* cases than the pinned suite specifies is not a
coverage gap: the scope is covered and no verdict is missing, but some case was
counted twice. It is nevertheless a blocking anomaly for the exit status, where
`scopeMismatch` is not — a narrowed debugging run legitimately produces fewer
cases, nothing legitimately produces more.
`archiveChanged` is stronger still: an archive that no longer matches the checksum
recorded when the run finished means the bundle cannot be used as evidence at all,
because the official report is supposed to be immutable from the moment
`60-run-tests.sh` copies it out of the container.

## Interactive execution

```sh
make manual                                        # brings up + seeds, then waits
make clean && MANUAL_STUFE=stufe3 make manual      # switch level
```

`MODE=manual` renders a second set of Tiger configuration files with
`runTestsOnStart: false` into `run/tiger/manual/` and mounts *those*, so an
unattended run cannot pick up a non-auto-running configuration by accident.
[90-manual.sh](../../scripts/90-manual.sh) greps the file it is about to mount to
confirm it really is the non-auto-running variant: mounting the wrong one would
launch a full conformance run (895 cases at Stufe 5) that nobody asked for.

**A manually driven session produces no report archive**, for the
`post-integration-test` reason above. Use it for exploration; use an unattended run
for anything whose result will be reported.

## Re-running parts of a round

| Command | Re-runs | Touches archives |
|---|---|---|
| `make report RUN_ID=<id>` | Extraction, results document, citation check, manifest, then the gap report | No |
| `make gap-report RUN_ID=<id>` | The gap analysis only | No |
| `make seed` | The whole corpus, idempotently (PUT-with-ID overwrites) | — |
| `make test-stufe5` | One level; brings the environment up first if needed | Overwrites that level's |

**Pass `RUN_ID` when running one level into an existing round**, and pass the
directory name in full:

```sh
make test-stufe5 RUN_ID=run-20260806T184641Z    # joins that round
make test-stufe5                                # starts a NEW round of its own
```

The variable is `RUN_ID` and its value includes the `run-` prefix — it *is* the
directory name under `reports/`. Without it each invocation mints a fresh id from the
current UTC time, so two levels run one after the other land in two different round
directories and `make report` then finds one level per round.

A single-level round is legitimate — the *Bestätigungsverfahren* confirms each level
separately — so nothing rejects it. That is exactly why the mistake is quiet: the
accidentally-split round and the deliberate one look identical. The results document
raises a `partialRound` anomaly and, next to the run-identity table, names this
mechanism as the likely cause and how to check for a sibling directory. Recovering
costs no test time: move the level's directory into the round that should hold both
and re-run `make report RUN_ID=<id>`, since the report targets only read archives.
`make test` and `make all` pass `RUN_ID` down themselves, so the normal paths cannot
get this wrong.

Both report targets are pure functions of the archives plus
`vendor/requirements.json`, so they can be re-run from an archived bundle on a
different machine and their output diffed between rounds. Several scenarios mutate
the corpus (`*-Update-Original-Example`, `Patient-Delete-Example`), which is why
`make test` depends on `seed` and why re-seeding is the intended reset.
