# 8. Known limitations and deliberate deviations

This document exists so that no deviation in this rig has to be discovered by
reading source. Two kinds of statement are collected here:

* **Deviations** — things this project deliberately does differently from a
  production deployment or from the shipped configuration. Each has a reason and
  none is hidden.
* **Limitations** — things a passing or failing result here does *not* prove.

Nothing below is a defect to be fixed silently. If a future change removes one, the
reason it existed has to be addressed too.

## What a result from this rig does and does not prove

**Does prove.** That the pinned Gematik ISiK test suite, at 2.7.0, run in its
official container with the official certification tag expression, against a stock
IRIS for Health 2026.2 endpoint loaded with exactly the FHIR packages the suite's
own reference validator validates against, produced the verdicts recorded in
`test-report.zip`.

**Does not prove:**

| Not proven | Why |
|---|---|
| ISiK conformance of the *product* | A conformance statement is Gematik's to make, through the *Bestätigungsverfahren*, from `test-report.zip`. This rig produces that artefact; it does not adjudicate it |
| Conformance of any other IRIS configuration | The result is specific to this endpoint configuration, this package set and this storage strategy. See [03-sut-iris.md](03-sut-iris.md#configuration-deviations) |
| Anything about optional scenarios | The certification scope is `@Stufe<N> and (not @Optional)`. Optional scenarios do not run, and their verdicts are unknown |
| Anything about the unexecuted features | If a level runs short of its expected case count, defects present only in the features that produced no result are absent from both reports. Both say so |
| That a failure is IRIS's | Only after the test-data segregation in `GAP-ANALYSIS.md` §5 and the [contract parity check](05-testdata.md#contract-parity). A failure is *attributed*, and the attribution states its evidence |
| That the IRIS locus named in a gap entry is correct | Those loci are authored by this project, not derived from any artefact. `index.json` carries that provenance statement and the report reproduces it verbatim |
| Interoperability with any real system | This is a conformance test against a specification, not an integration test against a partner |

## Deviations from a production deployment

### Credential documentation

**Every credential this environment uses is published, verbatim, in the generated
reports.** Not redacted, not hashed, not referenced by filename.

Where it appears, deliberately:

| Location | Form |
|---|---|
| `analysis/results.md` §1 *Access credentials* | A table: API user, API password, scheme, Management Portal user, Management Portal password. Sourced as `<stufe>/run.json`, key `credentials`, like every other claim in the document |
| `<stufe>/run.json` → `credentials` | Machine-readable, top-level, with a `_comment` stating why |
| `provenance/CREDENTIALS.md` | The credential sheet as generated at deployment, copied into the bundle because `make clean` deletes `run/` |
| `provenance/env.resolved.txt` | The whole resolved configuration, copied byte-for-byte with no filter |
| `GAP-ANALYSIS.md` | The `Authorization: Basic …` header verbatim inside each entry's ` ```http ` block |
| `debug-report.zip` → `config/tiger.yaml` | The official archive's own copy of this project's rendered Tiger configuration, extracted with the same permissions as every other file in it |
| `make credentials`, `make status`, `make manual` | On the terminal |

**Why.** This is a lab instance brought up for exactly one round of testing and
destroyed once the results are verified. Access to it is controlled physically and
at the network perimeter; perimeter defence is a given and out of scope for this
build. Within the environment the credentials are public knowledge and a formal
technicality — kept in place so the SUT remains a faithful stand-in for a
production deployment — rather than an authentication mechanism.

And there is a positive requirement: a human tester verifying a verdict by hand,
or a developer replaying a failing request, has to be able to look the credential
up. It must be *in the bundle*, not in a conversation. §8 of `results.md` shows the
`curl -u` form for exactly that.

**What is still enforced.** Three things, and `make check` verifies the first
mechanically:

1. **Nothing reaches source control.** `git check-ignore` must succeed for
   `secrets/sut.password`, `secrets/iris-mgmt.password`, `.env`, `.env.resolved`
   and `run/CREDENTIALS.md`. A future `.gitignore` edit that let a generated
   password into the index fails the self-check loudly.
2. **The live environment keeps production-like file modes.** `secrets/*.password`,
   `.env.resolved` and `run/CREDENTIALS.md` are 0600. The *evidence copy* is
   documentation and is readable; the environment under test is not itself
   weakened. That asymmetry is the policy, not an oversight — and it is why
   `70-collect-report.sh` deliberately does **not** `chmod 0600` the extracted
   `config/tiger.yaml`.

   One file is 0644 and it is worth saying why, because it looks like an exception
   to this rule and is not: `run/tiger/*.yaml` also carries the API password, but
   it is bind-mounted into the suite container, which runs as uid 100. A bind mount
   carries the host mode through, so 0600 makes the suite unable to read its own
   configuration — it fails with `AccessDeniedException`, runs 0 tests and reports
   `BUILD FAILURE`. The mode is dictated by the consumer, not chosen; `run/` is
   gitignored either way, which is the part check 1 above enforces. See
   [04-testtool.md](04-testtool.md#the-rendered-files-carry-a-credential).
3. **Never an empty or absent password.** An unauthenticated endpoint would be a
   functional deviation from a production deployment, not a simplification — and
   [03-sut-iris.md](03-sut-iris.md#security) explains why 401s arriving mid-round
   would be indistinguishable from an authorisation gap in the SUT.

**Each deployment generates its own.** Two passwords, at `make resolve`, form
`Kmrf-7xtq-Vhn3-Pd2s` — four groups of four, no glyphs that can be misread
(`O 0 I l 1`), ~92 bits, with at least one lower-case, one upper-case and one digit
by construction and `-` supplying punctuation. So nothing published in one round's
reports grants access to any other instance, including a rebuilt one.

**The password policy is not relaxed.** That form satisfies IRIS's default
`PasswordPattern` (`3.32ANP`) by construction, so no `Security.System.Modify()`
call is made and the SUT keeps a production-like security configuration. The plan
permitted relaxing it; it turned out not to be necessary.

**Passwords are absent from exactly one generated file:**
`iris-provenance.json`. That is the SUT's self-description of its installation, and
the credential channel is `CREDENTIALS.md` plus `run.json`. One channel, so there is
one thing to look at. A comment in `Setup.cls` says so, so the omission is not read
as a suppression policy.

### Non-expiring accounts

Every IRIS account is `PasswordNeverExpires 1`, `AccountNeverExpires 1`,
`ChangePassword 0`, and `UnExpireUserPasswords("*")` runs over all of them.

A production deployment would not do this. Here, either condition arriving *during*
a run would present as a wave of refused requests indistinguishable from an
authorisation gap in the SUT — not even identifiable as an authentication problem
from the status code, since these endpoints refuse with 404
([03-sut-iris.md](03-sut-iris.md#it-is-refused-with-404-not-401)) — and
the official image ships its predefined accounts with expired
passwords, which the Management Portal answers with a forced-password-change page,
making the link that `make manual` and `run/CREDENTIALS.md` advertise unusable.

`CSPSystem` is excluded from the password change: it is the account the CSP gateway
authenticates with, and changing it without reconfiguring the gateway would break
the web server the FHIR endpoints are served through.

### The API user has no `%All`

Stricter than some production deployments, and worth stating because it is
deliberate: `ISiKFHIRUser` holds `ISiK_FHIR:U` and nothing else, with an empty
`GrantedRoles`. The account the test suite authenticates as must not be able to
reconfigure the server it is testing.

### Isolation is not enforced by this repository

The rig provides one bridge interface (`br-isik`) and one CIDR
(`172.31.0.0/24`) to target. **It installs no firewall rules.** Blocking egress
from that subnet except to the two registries, or blocking it entirely after
`make packages`, is a host-level decision made outside this repository.

## Deviations in the system under test

Full detail and the per-setting rationale is in
[03-sut-iris.md](03-sut-iris.md#configuration-deviations). Summarised, with the
consequence for reading a result:

| Deviation | Consequence |
|---|---|
| `DefaultPreferHandling = lenient` | **Changes what is measured.** This is the FHIR-specified default and what ISiK expects, but it hides unsupported search parameters: a search using a parameter IRIS does not index returns an unfiltered — hence wrong — result set instead of an error, and the scenario fails on bundle *content*. `FHIR_PREFER_HANDLING=strict` turns each case into an explicit `OperationOutcome` naming the parameter, which is far better for gap hunting but is not a conformance measurement. Use it for a diagnostic round and say so in the round's notes |
| `MaxSearchPageSize = 500`, `MaxSearchResults = 10000`, `DefaultSearchPageSize = 100` | Raised, never lowered, so the run passes or fails on semantics rather than on pagination. No scenario can succeed that would have failed at a product default for a conformance reason. A truncated result set would present as "resource not in bundle" — a search-semantics gap rather than pagination |
| `DebugMode = 0` | Keeps `OperationOutcome` bodies as a production deployment would return them. Those bodies are the evidence in the gap report |
| `AutheEnabled = 32` on the CSP applications | The installer's default is 64 (`AutheUnauthenticated`). An unauthenticated request is refused rather than served anonymously — though it is refused with **404, not 401**, because the CSP dispatcher declines to route rather than challenging. Measured: correct credential 200, missing 404, wrong 404 ([03-sut-iris.md](03-sut-iris.md#it-is-refused-with-404-not-401)) |
| Two namespaces, two endpoints | Forced, not chosen: Stufe 3 pins `de.basisprofil.r4@1.4.0` and Stufe 5 `@1.5.4`, and one endpoint resolves each canonical to exactly one definition |
| Example-only packages excluded from the endpoints | `hl7.fhir.r4.examples@4.0.1` is ~198 MB / 5310 files, and all 658 of its StructureDefinitions repeat a canonical already defined by core R4. Recorded under `excludedPackages`; still in `MANIFEST.txt`, because the validator does use it |
| Version collisions resolved by "import all, list the highest" | Legal for the validator, which resolves canonicals across its whole set; ambiguous for an endpoint with a single canonical map. Every collision is recorded under `versionCollisions` |
| Unresolvable dependency declarations rewritten in the unpacked `package.json` files | Forced by IRIS: `NpmLoader.coreVersion()` resolves every declared dependency by *exact id*, with no semver support, and one unresolvable declaration aborts the whole import (`ERROR #5809`) after the namespace exists. Four are affected, with three different owners — a valid `3.1.x` wildcard the loader cannot resolve, an `hl7.fhir.r4.core: 4.0.0` HL7 never published, and the edge to the deliberately excluded example corpus. **None is an ISiK conformance failure of the SUT.** The `.tgz` payloads are untouched, so every `MANIFEST.txt` sha256 still attests the published bytes; every change is recorded under `dependencyRewrites` with its kind and reason. See [03-sut-iris.md](03-sut-iris.md#unresolvable-dependency-declarations) |
| One `snapshotDependencies` package promoted into the SUT's load plan | Also forced by the exact-id resolution above: `ihe.formatcode.fhir@1.2.0` declares `hl7.terminology.r4: 5.3.0`, which the validator pins only under `snapshotDependencies` and ships no payload for. It is loaded, and — being absent from the image entirely — fetched from the package registry even under `PACKAGE_SOURCE=refv`, which is the one respect in which that route is not offline. The `MANIFEST.txt` header names it. The SUT then holds one package the validator's dependency list does not pin: terminology only, no asserted ISiK profile, endpoint package list unaffected. Recorded under `snapshotDependenciesPromoted` |

The in-force values are read back **out of the instance** into
`iris-provenance.json` rather than restated from `.env`, so the documented
configuration is the observed one.

Everything else on the endpoint, the namespace and the instance is stock. There is
no `Dockerfile`, no derived image and no patched class.

### The SUT is two containers, and that is not a deviation

Worth stating here because it looks like one: the rig runs a second container, the
InterSystems Web Gateway, in front of IRIS. It is not test scaffolding and not a
workaround — **the official IRIS for Health images contain no web server at all**,
so it is the SUT's only HTTP surface, and running it is the supported InterSystems
deployment for HTTP as well as the topology a production ISiK deployment would use.
Evidence and the four Apache directives it needs:
[01-architecture.md](01-architecture.md#the-sut-is-two-containers) and
[03-sut-iris.md](03-sut-iris.md#the-http-surface-is-not-in-this-container).

The consequences for reading a result:

| Consequence | Detail |
|---|---|
| Two image digests define the SUT, not one | `WEBGATEWAY_IMAGE_DIGEST` is recorded in `.env.resolved` and in the bundle's provenance beside `IRIS_IMAGE_DIGEST`. "What exactly did you test?" is not fully answered by the IRIS digest alone |
| The gateway version is matched to the IRIS version | Supported combinations are broader, but matching removes the question from a conformance report entirely: no reviewer has to ask whether a gateway/kernel version skew explains a failed assertion |
| The `iris` service's health spans two containers | Its healthcheck probes `/metadata` through the gateway, because that is the only place the SUT answers HTTP. A green IRIS behind a broken gateway serves nothing, and calling it healthy would be a lie the whole round is gated on |
| An HTTP-layer failure has one more possible locus | A bare 500 on every endpoint is the gateway's own IRIS service connection, not a FHIR fault. `91-status.sh` reports the gateway first for this reason, and the diagnoses in `healthcheck.sh` map each status code to its observed cause |

Overriding `WEBGATEWAY_IMAGE` with `webgateway-nginx` or `webgateway-lockeddown` is
supported and both work here — but it changes the SUT, so it belongs in the round's
notes if used.

## Deviations in the test tool's configuration

Full detail in [04-testtool.md](04-testtool.md#the-deviations-and-why-each-exists);
every one is annotated `[DEVIATION]` at its site in the templates, so an inspector
can diff them against the shipped `tiger-isik-stufeN.yaml`.

| Deviation | Why |
|---|---|
| `servers.fhirserver.source` = the static subnet address | So the same literal string appears in the config, the seed log, the gap report's quoted requests and any host firewall rule |
| `healthcheckUrl` + `startupTimeoutSec: 60` | For `type: externalUrl` Tiger starts nothing and, with no healthcheck, waits out the timeout and proceeds — turning "IRIS is unreachable" into 506 or 895 failing cases indistinguishable from a total conformance collapse |
| `proxyRoutes` activated | Upstream ships it commented out with the instruction to activate it if the endpoint has a URL path. Ours does |
| Route-level Basic `authentication` | So **the official feature files stay byte-identical.** A modified feature file would invalidate the run |
| `workflowUiPort` 9012 → 9010 | Upstream's own README and `dc-testsuite.yml` publish 9010; pinned so the bound and forwarded ports cannot disagree |
| `runTestsOnStart: false` for `MODE=manual` | Rendered as *separate* files under `run/tiger/manual/`, mounted only by the `manual` profile, so an unattended run cannot pick one up by accident |
| One extra `additionalConfigurationFiles` entry, listed **last** | The `zz-upstream-key-defects.yaml` overlay can only add keys, never override an upstream value |

**The feature files themselves are never modified.** Neither is the image: nothing
in this repository rebuilds it, patches a class or edits a scenario.

## Limitations of the test data

The suite seeds nothing, so this project fabricates the entire corpus from the
`@Precondition` prose. That work has bounded confidence, and the bounds are
reported rather than smoothed over. See [05-testdata.md](05-testdata.md).

| Limitation | How it is bounded |
|---|---|
| The prose is interpreted by a human | Every `@vorbedingung` block is quoted **verbatim and untranslated** in `docs/en/generated/REQUIREMENTS-*.md`, indexed by the same `feature:line` citations the results use, so an inspector can check the interpretation against the source |
| `assertion_check.py` does not evaluate full FHIRPath | It reports every assertion under the strength it received — `EXACT`, `ELEMENT`, `LITERAL`, `UNVERIFIED` — and **prints the `UNVERIFIED` count** rather than hiding it. A false pass is the failure mode being defended against; implementing a FHIRPath subset would produce a checker that is right about most expressions and quietly wrong about the rest |
| `LITERAL` says nothing about *where* a value appears | Deliberately weak. It still catches a wrong code system or a typo'd code, which is what it is for |
| `extra/` resources exist on this project's judgement | Kept in a separate directory with a `_provenance` comment naming the scenario that requires each, so the inventory count still reconciles against the extractor and a reviewer can see at a glance which resources are not traceable to a feature assertion |
| 2 keys name a resource whose FHIR type no step states | Listed under *ID-shaped keys with no typed use*. Types are inferred **only** from steps that state them; nothing is inferred from a key's spelling, because `account-read-patient-id` merely *looks* like a Patient |
| 9 inventory rows are never validated by the suite | A resource that is only a reference target, or is deleted, has no profile assertion, so `{{profile.<Name>}}` is resolved from the packages' own StructureDefinitions. IRIS `$validate` over the corpus then makes the claim a real check of our own authoring |
| One genuine reference cycle exists | Stufe 5's `Encounter-Read-In-Progress-Example ↔ Condition-Read-Active-Example ↔ Condition-Read-Resolved-Example`. Both directions are required by the suite's own prose, so no total order satisfies it. The node is emitted with `cycleBroken` naming the unsatisfied edges, and pass 2 re-PUTs it |
| `$validate` tolerates `information` and `warning` issues | The IRIS validator warns about terminology it cannot expand locally; treating that as a defect would block every round for reasons outside the SUT. Error-severity issues block by default (`SEED_VALIDATE_STRICT=1`) |
| Seeding depends on FHIR *update as create* | Optional in the specification, and required here because the suite addresses every resource by the exact ID its contract names. The CapabilityStatement is read first and what it says about `updateCreate` is recorded either way |

Two numbers must not be quoted as if they were the same: `vendor/requirements.json`
reports 56 (Stufe 3) and 70 (Stufe 5) resources, while
`docs/en/generated/RESOURCE-INVENTORY.md` reports 55 and 69. The inventory tables
collapse or exclude one row per level that the JSON keeps, consistent with the
inventory's own documented handling of untyped keys. This is not a defect, but the
figures are not interchangeable.

## Upstream test-suite defects worked around

Four, all in the pinned 2.7.0 release, all handled mechanically in exactly one
place, all annotated at their site with the feature file and line that motivates
them, and **none counted as an IRIS gap.** Detail in
[05-testdata.md](05-testdata.md#upstream-test-suite-defects-found).

| Defect | Handled | Effect on a verdict |
|---|---|---|
| Three stray `$` regex anchors in string-comparison steps | `search_reference_check.py:STRAY_ANCHOR` strips the anchor and records the fact. The suite's own authors hit this at `Stufe3/Basis/Encounter-Read-In-Progress.feature:44`, commented out and replaced on line 45 | Those three scenarios fail regardless; the failure is attributed to the test suite |
| Four `@Precondition:` tags with a trailing colon | `feature_parser.py`'s `is_precondition` normalises the colon and matches case-insensitively | None at run time. Read literally, those four blocks would be counted as *asserting* cases and their prose left out of the requirement tables |
| Three undefined `${data.*}` keys | Aliased in `testdata/Stufe{3,5}/zz-upstream-key-defects.yaml`, each pointing at an existing upstream key's value. **Nothing invents a resource** | Two are cosmetic (error-message text only). `RiskAssessment-read-id` **changes a verdict** — it is used in a request URL in a mandatory in-scope scenario, and left undefined the suite searches for the literal string `${data.RiskAssessment-read-id}` |
| Two scheme-inconsistent `Bundle` canonicals | The index matches scheme-insensitively and records the fact under `notes`, which the gap report reproduces in §6.3 | Otherwise every Bundle assertion would be unattributable |

Two further undefined keys are deliberately **not** aliased and are listed in the
overlay so a future audit does not "fix" them:
`documentreference-update-stored-id` is written at runtime by the feature itself
(correct upstream behaviour), and `medicationadministration-read-request-id` belongs
to a `@Disabled @Optional` stub whose precondition reads "TODO".

If the suite version is bumped, each of these must be re-checked: a fixed upstream
defect leaves a workaround that is now itself a deviation. The step-shape drift
table in `index.json` and the version-pinned counts in the extractor are what make
a bump visible rather than silent.

## Limitations of the harness itself

| Limitation | Detail |
|---|---|
| The container's exit code is meaningless, and so is Maven's | The upstream entrypoint ends in `\|\| true`, so the container always exits 0; recorded as `containerExitCode`, never acted on. Maven's own status cannot substitute either — the profiles set `testFailureIgnore=false`, so `BUILD FAILURE` is exactly what a *normal* failing conformance run reports. **The usable success signal is the count of `unresolvedReports/*.json` inside `test-report.zip`**, recorded as `artefacts.testOutcomeRecords` |
| An interrupted run yields no evidence at all | Maven assembles both archives in `post-integration-test`. Not a partial archive — nothing. The timeout path stops the container gracefully (`docker stop -t 150` against a 120 s `stop_grace_period`) and says so honestly |
| A corrupt archive is possible and is checked for | `unzip -t` over each archive, because an archive Maven was still writing looks like evidence and is not |
| An *empty* archive is possible too, and cost a round | The entrypoint's `mv` is as unconditional as its `\|\| true`, so a run that dies in Tiger's startup still ships a well-formed, checksummed, `unzip -t`-clean `test-report.zip` containing only `pom.xml`. `60-run-tests.sh` §4b counts the outcome records and fails the level as a harness error at zero. Before that check existed, one such run was reported as `OK test-report.zip 4.0K (archive intact)` |
| The WorkflowUI aborts an unattended run if it is left on | `TigerDirector` waits 120 s for a browser to fetch status and then aborts with `No feedback from workflow Ui` — no scenarios, `Tests run: 0`. `activateWorkflowUi` is therefore rendered `false` for the unattended variants and `true` only for `MODE=manual`; `make check` asserts the polarity because it is a one-word regression with no other symptom ([04-testtool.md](04-testtool.md#the-deviations-and-why-each-exists)) |
| `make manual` produces no report | Same `post-integration-test` reason. Use it for exploration only |
| One level at a time, always | The image runs one Maven profile per start and wipes its report directory at the beginning of every run. The two levels are serialised and share one static IP |
| Tiger's "current response" is feature-scoped | A failing request early in a feature can make later scenarios assert against a response they did not issue. The gap report marks such entries `downstreamOf`; **diagnose the antecedent first** |
| The idempotency guard for "is this namespace FHIR-enabled" is a marker global | `^ISiK.Setup("fhirEnabled")`. A namespace FHIR-enabled by other means — the Management Portal, `HS.FHIRServer.ConsoleSetup` — is not recognised. The case does not arise, because every round starts from an empty container. The documented alternatives are worse: `HS.FHIRMeta.Storage.Package` is mapped into every Foundation namespace regardless, and `$$$comClassKeyGet` is an internal API with no cross-release guarantee |
| The bootstrap's host-side state is two files with no lock, and mishandling them cost a round | `bootstrap.status` / `bootstrap.done` are written by the container at every start, so the host may only remove a pair left by a container that is **no longer running**. Clearing them unconditionally looks correct and is not: `compose up -d` on an already-running container does nothing, nothing rewrites them, and their absence reads as "the bootstrap never started" — `make up` then fails with *"the endpoints do not serve /metadata"* against a SUT answering 200 on both. `40-up.sh` now leaves them alone whenever `docker inspect` reports the container `running` ([03-sut-iris.md](03-sut-iris.md#the-host-side-markers-belong-to-the-container-and-clearing-them-cost-a-round)) |
| `InitializeProfileValidator()` failure is non-fatal | The conformance run is judged by Tiger's reference validator, not by IRIS's. Only the seeder's pre-flight `$validate` depends on it, and `50-seed.sh` degrades gracefully and says so |
| The IRIS image has `wget` but **no `curl`** | Verified by inspecting 2026.2. Anything running *inside* the container — the healthcheck, the endpoint probe — must therefore not assume `curl`; [`http_probe.sh`](../../iris/bootstrap/http_probe.sh) prefers it when present and falls back to `wget`, and fails with a clear message if neither is. Two `wget` specifics are load-bearing there: `--auth-no-challenge`, because no 401 challenge ever arrives to prompt it (see the 404 row above), and *not* `--spider`, because it sends `HEAD` and the endpoint answers `HEAD` with 404 on a URL that serves 200 for `GET`. Host-side scripts do use `curl`, which `make preflight` requires |
| Authored test data can silently misspell a choice element | `Annotation.author[x]` is spelled `authorString` or `authorReference` in R4 JSON, never `author`. A bare choice name is not invalid JSON and not obviously wrong on inspection, but the element simply does not exist — so a scenario asserting on it fails for our defect while looking like a SUT gap, the misattribution this project exists to avoid. [`choice_type_check.py`](../../scripts/lib/choice_type_check.py) resolves every authored path against the R4 element paths and `make check` fails on any bare choice element; the current corpus is clean at 131 resources against 7790 paths |
| Stufe 5 can exhaust the scenario JVM's heap, and the symptom looks like a healthy run | Tiger's `RbelLogger` retains a parsed tree of **every** HTTP message of the run. Measured on a 31 GiB host at ~600 of Stufe 5's 895 cases with the JVM's default heap (7.84 GiB, a quarter of RAM): the Rbel parser threads died of `OutOfMemoryError` and every later scenario blocked for the full 100-second `RbelMessageHistory.waitForGivenMessagesToBeParsed` timeout. Throughput fell from ~12 scenarios/min to ~0.5 — about **9 hours** for the remainder against a 3-hour `TESTSUITE_TIMEOUT`, and a timed-out run yields no archive, so the level is lost. Nothing reported an error: the log grew, scenarios kept being reported, and the container sat at **0.3% CPU**, which reads as a hang rather than as memory exhaustion. Stufe 3 completes inside the default and never shows it, so a green Stufe 3 is no assurance. Mitigated at three layers — `TESTSUITE_HEAP_MAX=16g` (delivered as `MAVEN_ARGS=-DargLine=-Xmx…`, because `MAVEN_OPTS` would size the wrong JVM: the scenarios run in the JVM Failsafe forks), a preflight warning on unset heap or under 24 GiB RAM, an `OutOfMemoryError` watch in the progress loop, and the **blocking** `scenarioJvmOom` anomaly so an affected level can never be read as a conformance result. `run.json` records `testTool.scenarioHeapMax` ([06-execution.md](06-execution.md#stufe-5-needs-heap-and-running-short-of-it-costs-the-whole-level)) |
| Serenity attaches the wrong `Examples:` table when two feature files share a basename, and it can turn a failure into a pass | Stufe 5 of ISiK 2.7.0 has **16** duplicated feature basenames among its 124 features — `Practitioner-Search.feature` in both `Basis/` and `Medikation/`, `Patient-Search.feature` and `Encounter-Search.feature` four times each — while Stufe 3 has none, so only Stufe 5 shows it. Serenity 5.3.10 then hands an outcome a `dataTable` belonging to a different scenario of the same basename, and in two cases interleaves both files' example steps into one record. Measured on a full run: **57** Stufe 5 outcomes are plain `Scenario:`s carrying a foreign 6-to-14-row table, **10** steps belong to the other file, the case count comes out as **905** against the specified 895 — and **one** genuinely failing case (`Stufe5/Medikation/Practitioner-Search.feature:40`, "Search for Practitioner resource by ID", `result: FAILURE` on both the outcome and its step) reads as a **pass**, because the foreign table's row says `SUCCESS`. Serenity's own `serenity-summary.json` reproduces all of it, so it is no cross-check here. [`results_report.py`](../../scripts/lib/results_report.py) therefore attributes each executed step to an `Examples:` row by the parameters the step records in its own title rather than by position; both levels then reconcile exactly against `vendor/requirements.json` (506 and 895). Every correction is itemised per outcome in `results.md` §7 as `foreignExampleTable` / `foreignExampleStep`, and `test-report.zip` itself is untouched |
| Product defaults for the search-sizing values are not asserted anywhere | They are whatever the pulled build ships. What matters for reading a result is the value that was *in force*, and that is read back from the instance |
| A round run with `MODE=latest` cannot be re-created from `.env` alone | `MODE` floats the **unit under test** only, and the default is `latest` on the 2026 track, because a conformance verdict is normally wanted about the newest build. What is lost is re-creation *by tag* months later, not identification: `.env.resolved` records the registry **digest** and the IRIS build string, `sut.versionSource` in `run.json` says `resolved-latest`, and §1 of the results document states it. Set `MODE=pinned` when the round must be re-creatable from the file |
| Floating the **test tool** is unsafe, so it is a separate opt-in | `TESTSUITE_VERSION_TRACK` is empty by default and `MODE=latest` does not touch it. The suite version fixes the scenario inventory, the reference validator's FHIR package versions, the `${data.*}` contract the authored corpus satisfies, and the by-version defect workarounds — and a changed placeholder fails *silently*, as a conformance gap attributed to the SUT. Setting `latest` warns and carries the re-verification checklist in [INSTALL.md](../../INSTALL.md). Either way `make resolve` reports the newest available release, so a stale pin is visible ([04-testtool.md](04-testtool.md#what-it-is-and-why-it-is-pinned)) |
| `make check` cannot exercise everything | Its `docker compose config` section is skipped on a host without Docker. Sections 5–8 of the credential verification plan are deployment-host-only by nature |

## Reproducibility boundaries

Reproducible from the bundle alone, on any machine, months later:

* `analysis/*` — a pure function of the archives plus `vendor/requirements.json`;
* `GAP-ANALYSIS.md`, `gap-analysis.json` — a pure function of `results.json` plus
  `gap-analysis/index.json`; with `--generated-at` fixed, two runs are
  byte-identical.

**Not** reproducible from the bundle:

* `gap-analysis/index.json` itself, which needs the unpacked FHIR packages and the
  pinned image's feature files. A stale index would attribute this round's failures
  to the previous round's packages, so its build timestamp is printed in the report
  header and a corpus newer than the index is warned about;
* `docs/en/generated/*`, which is why it is not committed — it is meaningful only
  relative to one `TESTSUITE_VERSION`, and since it *is* the test-data
  specification, a stale copy is worse than no copy;
* the run itself, which needs the licence key, the registry login and the images.

## Where each deviation is stated in the generated documents

So that an inspector reading only a bundle finds them:

| Deviation | Stated in |
|---|---|
| Credentials documented on purpose | `README.md` §*Access credentials*, `analysis/results.md` §1, `<stufe>/run.json` `credentials._comment` |
| Not the official scope | `results.md` §1 as a block quote, `run.json` `conformanceScope: false`, and a warning on the terminal |
| A seeded resource failed `$validate` | `results.md` §1 as a block quote, `run.json` `seed.validationErrors` |
| The run was incomplete | `results.md` §7 anomalies, `GAP-ANALYSIS.md` §2 as a block quote |
| The IRIS loci are authored, not derived | `GAP-ANALYSIS.md` §3, verbatim from `index.json` |
| Upstream index notes (e.g. the `Bundle` canonical) | `GAP-ANALYSIS.md` §6.3 |
| Step-shape drift against 2.7.0 | `GAP-ANALYSIS.md` §6.2 |
| Dependency declarations rewritten for the IRIS loader, and the one snapshot-only package promoted into the SUT | `analysis/results.md` §6 with the owner per row, `GAP-ANALYSIS.md` §8 for the developer view, `provenance/package-plan.json` `dependencyRewrites` + `snapshotDependenciesPromoted` for the full record, and the `provenance/packages/MANIFEST.txt` header for the one payload not taken from the image |
| The configuration the SUT actually ran with | `provenance/iris-provenance.json`, read back from the instance |
| What was substituted into the Tiger configuration | `provenance/tiger-RENDERED.txt` |
| That the contract files match the suite's own | `provenance/tiger-TESTDATA-PARITY.txt` |
