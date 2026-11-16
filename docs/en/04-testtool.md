# 4. The test tool: the Gematik ISiK test suite

## What it is and why it is pinned

`gematik1/isik-testsuite:2.7.0` — the official Gematik ISiK test suite, delivered
as a pre-configured container. 2.7.0 is the latest release (published 2026-07-06,
still current on 2026-08-07) and is pinned in `.env.example`.

Pinning is not caution, it is a correctness requirement, and it applies to *this*
axis specifically. Four things are fixed by the version number, and only the first
is visible in a report:

* the scenario inventory, and so the expected case counts (506 and 895);
* the FHIR package versions the bundled reference validator validates against;
* the `${data.*}` placeholder contract the authored corpus in `testdata/` has to
  satisfy exactly ([05-testdata.md](05-testdata.md));
* which upstream suite defects need working around, and how.

The third is the one that makes floating this axis unsafe rather than merely
undesirable. A release that renames or re-shapes a placeholder does not fail
loudly: the corpus still seeds, and a scenario then asserts against data that is
not what its feature file expects — which surfaces as a *conformance gap in the
SUT*. That misattribution is the failure mode this whole project is built to avoid.

So the test tool is **not** floated by `MODE=latest`, which governs the unit under
test only. Floating it takes a second, explicit setting,
`TESTSUITE_VERSION_TRACK=latest`, and doing so carries the re-verification work
described in [INSTALL.md](../../INSTALL.md). `make resolve` looks the newest release
up in either case and records it as `TESTSUITE_LATEST_AVAILABLE`, warning when the
pin is behind — a stale pin is then a stated fact in the report rather than
something a reader has to go and check. `.env.resolved` records the registry digest
alongside the tag, because a tag can be moved and a digest cannot, and
`TESTSUITE_VERSION_SOURCE` says which of the two ways the version was arrived at.

The image is used unmodified. Nothing in this repository rebuilds it, patches a
class or edits a feature file.

## The tool chain inside the image

The suite is a Maven project (`/app`) driving Cucumber scenarios through
Gematik's Tiger test framework, with Serenity BDD as the reporter. The versions
that matter for reading a report, as declared by the pinned image's own
`pom.xml`:

| Component | Version | Why it appears in a report |
|---|---|---|
| Tiger test framework | 4.3.1 | Supplies the Gherkin step vocabulary, the Tiger proxy, the WorkflowUI and the `TigerTest` runner |
| `tiger.fhir` | 1.2.15 | The FHIR-specific steps: profile validation, FHIRPath assertions |
| `tiger-testsuite-validator` | 1.1.9 | Wraps the Gematik reference validator that judges every response body |
| `tiger-cloud-extension` | 4.1.17 | Server-lifecycle types; unused here, since our SUT is `type: externalUrl` |
| Serenity BDD | 5.3.10 | Writes `unresolvedReports/*.json` (the `TestOutcome` records this project reads) and the browsable HTML report |
| JUnit | 6.1.1 | Test platform |
| Java | 17 | Runtime |
| Gematik base image | 4.1.7 | The container's own base |

`make packages` copies `pom.xml` and `serenity.properties` out of the pinned image
into `vendor/upstream/` as documentation baselines, and Maven itself puts
`pom.xml` inside `test-report.zip` — so the version table above is verifiable
from the submitted artefact alone, without trusting this file.

`de.gematik.rbellogger.converter` is set to `WARN` in the rendered configuration
(as upstream does): at `INFO` the Rbel logger prints a decoded dump of every
message and buries the scenario output.

## What the entrypoint does, and everything that follows from it

The upstream image's entrypoint is one line:

```sh
rm -rf $REPORT_DIR/* ; mvn clean verify -ntp -P${MAVEN_PROFILE} \
    -DTESTS_TO_RUN="${TESTS_TO_RUN}" || true ; \
    mv -v $APP_HOME/target/*report.zip $REPORT_DIR/
```

Four consequences shape this whole rig:

1. **One Maven profile per container start.** `MAVEN_PROFILE` is `stufe3` or
   `stufe5`, and the profile also decides which configuration file Maven reads
   (`TIGER_TESTENV_CFGFILE=tiger-isik-${specification.level}.yaml`). Hence three
   separate compose services rather than one parameterised service, hence
   `make test` serialising the two levels, and hence `make manual` offering
   exactly one level per invocation.
2. **`rm -rf $REPORT_DIR/*` at the *start* of every run.** The report volume holds
   one level's output at a time. `60-run-tests.sh` therefore lifts the archives
   out with `docker cp` and computes their checksums *before* it returns, and
   `make test` cannot start the second level until the first is archived.
3. **`|| true` — the container's exit code is meaningless.** Maven's status is
   discarded, so the container exits 0 whether every scenario passed, every one
   failed, or none ran at all. `run.json` records `containerExitCode` and nothing
   ever acts on it. Maven's *own* verdict is no more usable: the profiles set
   `testFailureIgnore=false`, so `BUILD FAILURE` is what a normal failing
   conformance run reports. **The usable success signal is the number of
   `unresolvedReports/*.json` records inside `test-report.zip`** — the archive's
   presence is not enough, because the `mv` is as unconditional as the `|| true`
   and ships an empty archive just as happily (see
   [06-execution.md](06-execution.md#exit-codes-what-success-means-here)).
4. **The archives are assembled in `post-integration-test`, at the end.** A run
   killed part-way produces *no* archive, not a partial one. The timeout path in
   `60-run-tests.sh` therefore stops the container gracefully
   (`docker stop -t 150`, against a 120 s `stop_grace_period`) and reports
   honestly that there is nothing to archive; each archive that does arrive is
   checked with `unzip -t`, because an archive Maven was still writing is worse
   than a missing one — it looks like evidence and is not.

Point 3 has a corollary this project depends on: **a failing conformance run is
the expected outcome here.** IRIS for Health is known not to be fully ISiK
compliant. `60-run-tests.sh` exits 0 for a run in which every scenario failed and
non-zero only for a harness error — a missing archive, or one carrying no test
outcomes, either of which means there is no evidence to analyse. `make test` must
never stop before `make report`.

## Scope: the tag expressions

| Level | Tag expression | Source |
|---|---|---|
| Stufe 3 | `@Stufe3 and (not @Optional)` | `TESTS_TO_RUN_STUFE3`, default = the upstream `pom.xml` profile default |
| Stufe 5 | `@Stufe5 and (not @Optional)` | `TESTS_TO_RUN_STUFE5`, same |

These defaults *are* the official certification scope: all mandatory scenarios,
no optional ones. Two guards sit on them in
[60-run-tests.sh](../../scripts/60-run-tests.sh):

* A tag expression that does not mention the level being run is a **hard stop**.
  Maven would run the `stufe3` profile while the filter selected nothing, and the
  symptom is a report full of zeroes with no error anywhere.
* Any expression other than the official one sets `conformanceScope: false` in
  `run.json` and prints a warning: the run is a debugging run, its report is not a
  conformance result, and it must not be submitted for the
  *Bestätigungsverfahren*. The generated results document states this.

Note the quoting in `.env`: the file is read two ways — `docker compose
--env-file` parses it itself and strips quotes, while `scripts/lib.sh`'s
`load_env` *sources* it in bash, where an unquoted `(` is a syntax error. Any
value containing spaces or parentheses keeps its quotes.

## Configuration

The suite is configured entirely through its Tiger YAML. This project renders it
from annotated templates, one set per suite generation, selected by
`suite_tiger_dir()` in [scripts/lib.sh](../../scripts/lib.sh) from the pinned
`TESTSUITE_VERSION`:

| Generation | Templates | Levels |
|---|---|---|
| 2.x | [tiger/tiger-isik-stufe3.yaml.tmpl](../../tiger/tiger-isik-stufe3.yaml.tmpl), [tiger/tiger-isik-stufe5.yaml.tmpl](../../tiger/tiger-isik-stufe5.yaml.tmpl) | Stufe 3 and Stufe 5 |
| 3.x | [tiger/v3/tiger-isik-stufe5.yaml.tmpl](../../tiger/v3/tiger-isik-stufe5.yaml.tmpl), [tiger/v3/tiger-isik-stufe5-connect.yaml.tmpl](../../tiger/v3/tiger-isik-stufe5-connect.yaml.tmpl) | Stufe 5 only — release 3.0.0 removed ISiK Level 3 |

Each is derived from the official `tiger-isik-stufeN.yaml` shipped in that release
— which `make packages` extracts to `vendor/upstream/` — and **every change from
upstream is annotated `[DEVIATION]`**, so an inspector can diff the two files and
see exactly what was changed and why. The Connect template is derived in turn from
the Stufe 5 one beside it, and its own additional changes are annotated
`[DEVIATION-CONNECT]` so the two layers stay distinguishable. It is rendered only
when `CONNECT_ENABLE=true`; see [08-connect.md](08-connect.md).

The table of deviations below enumerates the 2.x set, and the rationale for each
is not restated in the 3.x templates. Two of the eight rows are **not** deviations
under 3.x, because upstream changed: 3.0.9 ships `servers.fhirserver.source` as a
placeholder to be filled in rather than a literal, and it ships `proxyRoutes`
*active*, with the same instruction, pointing at `http://localhost:8080/fhir`. The
3.x templates carry six annotated deviations each; the Connect one adds its own,
described in [08-connect.md](08-connect.md#why-connect-is-a-separate-invocation).

Two further differences under 3.x are shape changes forced by upstream and not
deviations at all: nine test-data files rather than five, and per-module
`baseKey`s (`${basis.patient-read-id}`) rather than one shared `data`
([05-testdata.md](05-testdata.md)).

`30-render-config.sh` substitutes the `@@PLACEHOLDER@@` tokens in Python rather
than with `sed`: the password is generated and could contain a character `sed`
treats specially, and an *unresolved* placeholder must be a hard error rather than
something that reaches the container. The renderer rejects any placeholder name
it does not know — including one that appears only in a comment, which is why the
templates carry no literal example of the syntax. The rendered YAML is then
parsed with PyYAML before it is written, because Tiger answers a malformed
configuration with a Spring stack trace that never names the offending line.

### The deviations, and why each exists

| Deviation | Upstream | Here | Why |
|---|---|---|---|
| `servers.fhirserver.source` | placeholder | `http://172.31.0.30:80/isik<N>/fhir/r4` | The SUT, by its static subnet address. `.30` is the **Web Gateway**, not IRIS at `.10`: the IRIS images serve no HTTP at all, so the gateway is the SUT's HTTP surface ([01-architecture.md](01-architecture.md#the-sut-is-two-containers)). A literal IP rather than a container name, so the same string appears here, in the seed log, in the gap report's quoted requests and in any host firewall rule |
| `healthcheckUrl` + `startupTimeoutSec: 60` | absent | present | For `type: externalUrl` Tiger starts nothing; with no healthcheck it simply waits out the startup timeout and proceeds. That would turn "IRIS is unreachable" into 506 (Stufe 3) or 895 (Stufe 5) failing test cases indistinguishable from a total conformance collapse. Any HTTP answer counts as healthy, and Tiger probes without the route's credential, so what it sees is the endpoint's unauthenticated answer — **404, not 401** ([03-sut-iris.md](03-sut-iris.md#security)). Still sufficient, and cheap: only something answering HTTP at that address can produce it |
| `tigerProxy.proxyRoutes` | shipped commented out | active | Upstream's own instruction is to activate it "if the SUT endpoint contains a URL path". Ours does (`/isik3/fhir/r4`). The feature files address `http://fhirserver/<Resource>/<id>`; without the route the path prefix is lost and every request 404s |
| `proxyRoutes[].authentication` | absent | Basic, the API user | The IRIS endpoint requires authentication ([03-sut-iris.md](03-sut-iris.md#security)). Attached on the *route* rather than in each step, so **the official feature files stay byte-identical** — a modified feature file would invalidate the run. The block **replaces** any `Authorization` header a feature file sets for itself, which is a property of Tiger and not a configuration choice: see [08-connect.md](08-connect.md#why-connect-is-a-separate-invocation) for the measurement and for the one module that therefore cannot use this file |
| `lib.workflowUiPort` | `9012` | `9010` | Upstream sets 9012 here while its own README and `dc-testsuite.yml` publish 9010. Pinned to the published port so the bound port and the forwarded port cannot disagree |
| `lib.runTestsOnStart` | `true` | `true`, or `false` for `MODE=manual` | The manual variants are rendered into `run/tiger/manual/` as *separate files*, mounted only by the `manual` compose profile, so an unattended run cannot pick up a non-auto-running configuration by accident — and `90-manual.sh` greps the file it is about to mount to confirm it really is the non-auto-running one |
| `lib.activateWorkflowUi` | `true` | **`false`** for an unattended run, `true` for `MODE=manual` | Not a preference — a run-breaker. With the UI active, `TigerDirector` logs `Waiting 120 seconds for workflow Ui to fetch status...` and, when no browser attaches inside that window, aborts the entire run with `[ERROR] No feedback from workflow Ui, aborting!` **before executing a single scenario**. A detached container on a remote host has no browser, so the level ends after ~2 minutes with `Tests run: 0`. Upstream's own README prescribes the same remedy for CI. Nothing is lost: the request and response bodies the gap analysis quotes come from the Serenity report inside `debug-report.zip`, which Maven assembles either way. Rendered as the negation of `runTestsOnStart`, and `make check` asserts both the derivation and the rendered boolean |
| `additionalConfigurationFiles` | 6 entries (Stufe 3) / 5 (Stufe 5) | + one `zz-upstream-key-defects.yaml` | Defines the `${data.*}` keys the upstream feature files reference but the upstream contract files do not define. Listed **last**, so it can only add keys and never override an upstream value. See [05-testdata.md](05-testdata.md) |

Everything else — `trafficVisualization`, the logging level, the order and names of
the upstream `additionalConfigurationFiles` entries — is upstream's.

`run/tiger/RENDERED.txt` records what was rendered from what, with the
substituted values, and `run/tiger/TESTDATA-PARITY.txt` records the file-by-file
comparison of our staged contract files against the ones inside the image. Both
are archived into `provenance/` of the evidence bundle.

### The rendered files carry a credential

`run/tiger/*.yaml` contain the API password and are written mode 0644, inside
gitignored `run/`. The mode is forced by the consumer, not chosen: compose
bind-mounts these files into the suite container, which runs as its own
unprivileged uid 100 (`tiger-testsuite`), and a bind mount carries the host
inode's owner and mode straight through. At 0600 owned by the operator the suite
cannot read its own configuration and fails with

```
[ERROR] Error while reading configuration from file '/app/tiger-isik-stufe3.yaml'
        with cause 'java.nio.file.AccessDeniedException: tiger-isik-stufe3.yaml'
```

followed by `Tests run: 0` and `BUILD FAILURE` — the same uid-versus-bind-mount
trap the licence key springs
([02-setup.md](02-setup.md#the-licence-files-permissions-matter-more-than-they-look)).
`chown` to uid 100 would
be tighter but needs root, which the operator may not have; compose mounts the
file read-only, so 0644 grants the container exactly the read it needs. The
protection that matters for this file is elsewhere and unaffected: `run/` is
gitignored, `make check` verifies that mechanically, and the credential it
carries is published in the reports on purpose — including in the debug archive's
own `config/tiger.yaml`, which *is* this rendered file as the suite received it.
See
[09-known-limitations.md](09-known-limitations.md#credential-documentation).

## How the suite reaches the SUT

```
feature file        http://fhirserver/Patient/12345
      │
      ▼
Tiger proxy         proxyRoutes: http://fhirserver → http://172.31.0.30:80/isik5/fhir/r4
      │             + Authorization: Basic <api user>
      ▼
Web Gateway         :80, Location /isik5/fhir/r4 → csp-handler-sa
   172.31.0.30      │  then over the superserver protocol, as CSPSystem
      │             ▼
IRIS endpoint       172.31.0.10:1972, /isik5/fhir/r4/Patient/12345
```

Note the extra hop. IRIS itself listens on no HTTP port whatsoever, so every
request in the round crosses the Web Gateway — which is why the gateway's image
digest is recorded with the SUT's and why an unhealthy gateway presents as an
unhealthy SUT. See
[01-architecture.md](01-architecture.md#the-sut-is-two-containers).

The `Connect` module takes a different path through the same SUT, because it
carries its own credential and the product will not evaluate a bearer token on a
connection it does not consider secure:

```
feature file        http://fhirserver/Patient/Patient-Read-Example
      │             + Authorization: Bearer <token of one scope>
      ▼
Tiger proxy         proxyRoutes: http://fhirserver → https://isik-webgateway:443/isik5/fhir/r4
      │             NO authentication block — the feature file's header is forwarded untouched
      ▼
Web Gateway         :443, TLS under the round's own CA, then as above
   isik-webgateway
      ▼
IRIS endpoint       same instance, same namespace, same web application
```

Full statement in [08-connect.md](08-connect.md).

Two things follow. First, the feature files are unmodified — the rewrite and the
credential both live in configuration. Second, the Tiger proxy is what records
the traffic: the request and response bodies the gap analysis quotes come from
Tiger's own recording of this exchange, which is also what the
`trafficVisualization` view and the traffic log on port 9011 show.

## A coupling worth knowing about

Tiger's assertion steps operate on a **"current response"** that is scoped to the
*feature*, not to the scenario. A step that asserts over "the response" reads
whatever the most recent request in that feature file produced. Practical
consequences when reading a failure:

* A failing request early in a feature can make later scenarios in the same file
  assert against a response they did not issue. The second failure is then a
  *consequence*, not an independent finding.
* The gap report accounts for this explicitly: it walks Serenity's steps in the
  suite's own order, remembers the last exchange seen, and marks an entry
  `downstreamOf` when the exchange it is quoting was produced by an earlier
  scenario. An entry so marked should be diagnosed by fixing its antecedent
  first, not on its own.

This is a property of the tool, not a defect in it, and not something this
project changes. It is documented here because a developer reading a wall of
failures in one feature file needs to know that the wall may have one cause.

## Running it

Normal path — `make test` (every level the pinned suite carries, serialised, plus
the Connect invocation if the round is configured for it) or `make test-stufe3` /
`make test-stufe5` / `make test-connect` individually. Before a single container
starts,
[60-run-tests.sh](../../scripts/60-run-tests.sh) checks:

* both rendered configuration files and `run/testdata` exist;
* the IRIS container is running;
* `run/seed/<Level>.json` exists and reports **zero** incomplete resources — the
  suite seeds nothing, so a run against an unseeded endpoint wastes an hour and
  then reports our own missing test data as a wall of IRIS conformance gaps, the
  one misattribution this project exists to avoid;
* the tag expression names the level, and whether it is the official scope;
* stale test suite containers are removed — an exited container holds its network
  endpoint until removed, and all three services share one static address.

During the run the container's whole output is streamed to
`reports/<run-id>/<variant>/run.log` (not to the terminal: this runs for up to three
hours, usually detached, and the file is what gets archived), with a progress line
every two minutes carrying the log's line count and its last non-empty line — the
only version-independent way to tell a progressing run from a hung one on a remote
host.

`TESTSUITE_TIMEOUT` (default 10800 s) is the wall-clock ceiling for one level.

Interactive path — `make manual`. See [02-setup.md](02-setup.md#interactive-testing).
Note that a manually driven session produces **no** report archive at all, for
reason 4 above: use it for exploration, and an unattended run for anything whose
result is to be reported.
