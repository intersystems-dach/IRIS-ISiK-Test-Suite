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

### Re-checked against test suite 3.0.9

The instruction above — a bumped suite version leaves every workaround to be
re-checked, because a fixed upstream defect turns its workaround into a deviation
— was carried out for release 3.0.9. Each row states what was measured, not what
was expected.

| Defect | State in 3.0.9 | Consequence |
|---|---|---|
| Stray `$` regex anchors | **1 of 4 occurrences remains**: `Stufe5/Medikation/MedicationStatement-Search.feature:58`. The other three were in Stufe 3 features (`Medikation/MedicationRequest-Search.feature:71`, `Medikation/MedicationStatement-Search.feature:83`, and the already commented-out `Basis/Encounter-Read-In-Progress.feature:44`) and are absent because release 3.0.0 removed ISiK Level 3, not because they were corrected | `STRAY_ANCHOR` is still required and still fires. One Level 5 scenario fails regardless of the data, attributed to the test suite |
| `@Precondition:` with a trailing colon | **Unchanged: 4 occurrences**, out of 205 `@Precondition` tags in the 203 feature files | The normalisation in `feature_parser.py` is still required |
| Undefined test-data keys | **Unchanged in substance: 4 referenced and never defined, the same four as in 2.7.0**, measured by resolving all 312 distinct `${<module>.<key>}` references against all 345 defined keys. Two are aliased, in `testdata/v3/Stufe5/zz-upstream-key-defects-basis.yaml` and `-medikation.yaml`; two are deliberately not | `RiskAssessment-read-id` still changes a verdict and is still aliased. The per-module `baseKey` namespaces introduced at 3.0.0 mean the aliases are now two files instead of one, with no change of content |
| Scheme-inconsistent `Bundle` canonical | **Unchanged: still present, 45 occurrences** (64 in 2.7.0; the reduction is Level 3's removal). The features assert `https://hl7.org/fhir/StructureDefinition/Bundle`; `hl7.fhir.r4.core#4.0.1` declares `http://hl7.org/fhir/StructureDefinition/Bundle`, confirmed by reading the package's own `StructureDefinition-Bundle.json` | The scheme-insensitive match is still required |

None of the four was corrected upstream, so no workaround became obsolete and
none was removed. One new defect was found, below.

### A new upstream defect, present only in 3.0.9: prose and step state different units

`Stufe5/Labor/Observation-Read-Troponin.feature` states the measured value twice
and inconsistently:

| Line | Text | Reads as |
|---|---|---|
| 37 | `* Value: 0.1 ng/L` | the vendor is to seed 0.1 nanogram per litre |
| 67 | `value.where(value ~ 0.1 and code = 'ng/mL' and unit.exists() and system = 'http://unitsofmeasure.org').exists()` | the server must return 0.1 nanogram per *millilitre* |
| 68 | `referenceRange.where(high.value ~ 0.14 and high.code = 'ng/mL' …)` | likewise for the reference range |

`ng/L` and `ng/mL` are both valid UCUM codes and differ by a factor of 1000, and
the stated number is the same in both places, so the two statements describe
physically different concentrations. A vendor who seeds what the prose says fails
the step.

**Not worked around, because it needs no workaround — only a decision about which
statement governs.** The step is the executable artefact and is what produces the
verdict, so `testdata/fhir/v3/Stufe5/Observation-ISiKLaboruntersuchungTroponin-Example.json`
carries `ng/mL`. `Quantity.unit`, which the step requires only to *exist* and for
which neither the prose nor any step states a text, is written from the UCUM code
the step itself asserts; that is the one text which cannot contradict the step.
`reconcile_testdata.py` records the substitution in the body's `used` list rather
than making it silently.

**Effect on a verdict: none that is attributable to the SUT.** The scenario passes
or fails on whether IRIS returns what the step demands, which is what the step
would demand of any vendor. The defect is that the specification tells the vendor
to prepare something else.

### Ten defects in this rig, surfaced by the same bump

Recorded here because this is the class of thing a version bump is supposed to
expose. All ten are fixed. Defects 1 to 8 changed no verdict in any round, and the
reason is stated per defect rather than asserted once. Defects 9 and 10 are set
apart: 9 would have charged a missing test resource to the product, and 10 did
charge our own authoring to it and additionally moved one verdict between two
rounds. Attribution is the one thing this rig exists to get right, so both carry
their evidence in full.

Defects 1 to 3 are in the phase pipeline and were reached in that order, each
stopping a round; defects 4 to 8 are in `reconcile_testdata.py`, the tool that
brings the fabricated Level 5 bodies into line with the suite's assertions, and are
grouped after them. Defect 9 is in `search_reference_check.py`, the gate that
decides whether an empty search result is the system's deficiency or ours, and
defect 10 in the authored corpus and the build step that renders it per level.

**1. The base FHIR R4 package lost its only bundled payload.**
`hl7.fhir.r4.core#4.0.1` is added to every level by `plan_packages.py` as an
implicit requirement: the reference validator always has core R4 and so does not
pin it, while an IRIS endpoint's package list must contain it. Under 2.7.0 that
addition cost nothing, because the `isik3-basismodul` plugin named core R4
explicitly and therefore bundled its `.tgz`, which the payload pool is shared
across. Release 3.0.0 removed ISiK Level 3, leaving `isik5` as the only plugin —
and its dependency list starts at `de.basisprofil.r4#1.5.4`. The image carries 19
payloads for a 20-package plan, so `make packages` stopped at the load-order gate
with `missing package directories: vendor/packages/hl7.fhir.r4.core#4.0.1`.

The gate was right and the fetcher was incomplete: `20-fetch-packages.sh` fetched
only `snapshotDependenciesPromoted` from the package registry. It now also fetches
plan entries marked `implicit: true`, with the same archive verification — the
downloaded tarball's own `package.json` must state the name and version asked for —
and records them in a *separate* `MANIFEST.txt` block, because the reason this
payload is absent from the image is not the reason the promoted one is. A package
named by a plugin and absent from the image is still a hard failure; only the
planner's own additions are fetchable this way. Consequence, stated rather than
hidden: under a 3.x suite `PACKAGE_SOURCE=refv` takes two payloads from the
registry rather than one.

**2. Six sites named both levels literally.**
Two authorities were already generation-aware — `30-render-config.sh` renders one
Tiger configuration per `suite_levels_lc()`, and `plan_packages.py` builds
`endpoints` over the same set — so under a 3.x suite there is no
`tiger-isik-stufe3.yaml` and no `endpoints.stufe3`, both correctly. Six consumers
still assumed two of everything, and each failed in turn:

| Site | Assumed | Now |
|---|---|---|
| `40-up.sh`, config guard | `run/tiger/tiger-isik-stufe3.yaml` exists | one expected file per `suite_levels_lc()` |
| `40-up.sh`, endpoint check | `FHIR_BASE_STUFE3` answers with a `CapabilityStatement` | `verify_endpoint` over `suite_levels_lc()` |
| `60-run-tests.sh`, config guard | the same file exists | the same per-level list |
| `ISiK.Setup.Run()` | `plan.endpoints.stufe3` is present; aborted with `ERROR #5001` when it was not | installs `PlanLevels(plan)`, and refuses a plan that describes no known level |
| `ISiK.Setup.VerifyEndpoints()` and `WriteProvenance()` | both levels were installed | the same `PlanLevels(plan)` |
| `healthcheck.sh` | both endpoints serve `/metadata` | probes the endpoints the plan describes, read from the same `package-plan.json` the setup class reads |

The failure mode this removes is not a false pass but a false fault: a Stufe 3
endpoint that was never installed, reported as an unreachable SUT or a misconfigured
gateway, in a round where nothing asked for it to exist. Two of the six would have
produced a *false record* rather than a false fault — `WriteProvenance` writes
`iris-provenance.json`, which the report generators read as the statement of which
endpoints this round tested, and `healthcheck.sh` decides whether the SUT is ever
declared healthy. Both now refuse an empty level list explicitly, because a health
verdict that verified no endpoint at all, or a provenance record listing none, must
not be reachable by omission.

`60-run-tests.sh` additionally now **refuses** a level the pinned suite does not
carry, rather than running it. The suite answers a tag expression that selects no
scenario with a successful build and an empty report, which in the archive is
indistinguishable from a level that ran and found nothing. The refusal is in the
script and not only in the Makefile's `test` target, because the script is
reachable several ways.

**3. The search-URL pattern modelled one of three shapes.**
`search_reference_check.py` recognised a search URL only in the shape
`http://fhirserver/<Type>/?<query>`. The Connect module introduced at 3.0.0
writes two further shapes — `http://fhirserver/Encounter?_count=10` (12
occurrences in 3.0.9) and `http://fhirserver/DocumentReference` with no query at
all (11) — so the assertion at
`Stufe5/Connect/Patient-Wildcard-Read-Scope.feature:49` was reported as a step
shape the check does not model. The pattern now accepts all three, excluding
`/metadata`, which has the same shape and is not a search. The requirement turns
out to be one the corpus already satisfies, and is recorded as a fifth site on
`Encounter.subject -> Patient-Read-Example`. Against the 2.7.0 corpus the widened
pattern enumerates the same 110 requirements as before, so no earlier round's
validation is affected.

**4. An asserted substring was written as the whole value.** A step of the form

```
And FHIR current response body evaluates the FHIRPath
    "issued.toString().contains('2024-05-14')"
```

states a substring of the value, not the value. The reconciler wrote the literal
itself, giving `"issued": "2024-05-14"`, which satisfies the step and is not a FHIR
`instant`. Eight Labor Observations were rejected at the seed phase with `The value
'2024-05-14' of Property 'issued' of Type 'Observation' is a malformed 'instant'`,
which is where this was found — the round stopped before it could test anything.
The rule now takes the value from the feature's own precondition prose (`* Issued:
2024-05-14T09:15:00+02:00`) wherever the prose states one of which the asserted
literal is a substring, and reports the assertion as one for a person wherever it
does not. This is the sixth prose construct and the only one that overrides a value
a step states, so it is deliberately narrow and, like the other five, every value
is cited by file and line in `testdata/fhir/v3/PROSE-FACTS-Stufe5.txt`.

The underlying error is worth naming, because it is the one error a tool like this
must not make: it was satisfying steps rather than stating the specification. A
body that satisfies every step and is not a valid instance measures nothing.

**5. A `display` was written into a `Quantity`.** The rule that supplies a missing
`display` from the pinned packages keyed on a node carrying `code` and `system` —
which a `Coding` carries and so does a `Quantity`. It therefore produced
`valueQuantity.display`, an element FHIR R4 does not define. The guard is now
structural: a `code`/`system` node is treated as a `Coding` only if it carries no
key outside the `Coding` element set. Never present in a committed corpus; it
appeared only once the display index was widened (defect 7) to a package that
defines UCUM unit texts.

**6. A selector that matched nothing appended instead of completing, making a
feature contradict itself.** `component.where(code.coding.where(code = '8480-6' and
system = 'http://loinc.org' and version = '${icuextended.loinc-version}').exists())`
was read as "no component satisfies this, so append one" against an ISiK package
example whose three components carry those very LOINC codes without a `version`. The
three pressure selectors therefore appended three further components, and the body
carried six — against `component.count() = 3` asserted in the same feature, three
lines above. Five ICU Extended bodies carried the extra components (left and right
atrial, left and right ventricular, and pulmonary arterial pressure: 15 component
entries where the features assert 5 × 3). The same reading duplicated `coding`
entries wherever a feature asserted a `version` the package example did not carry,
which is most of the corpus: regenerating it removes 193 of 546 `coding` entries
and adds no element any assertion does not name.

A reading on which a feature contradicts itself is never the reading to take. The
navigator now distinguishes three states rather than two: an entry that *satisfies*
the selector, an entry that *conflicts* with it, and an entry that is merely
*silent* where the selector states a value. Where exactly one entry conflicts with
nothing, that entry is the one the selector is about and the missing value is
written into it; where two or more do, the choice is ambiguous and an entry is
appended as before. Silence is not agreement, so this governs only *where* a value
is written, never whether an assertion holds.

Not caught by `assertion_check.py`, and that is a limitation of the checker, not an
oversight: it classifies `count()` as `UNVERIFIED`, one of 271 assertions in that
class for Stufe 5, because evaluating it requires a FHIRPath engine. The corpus
consequently reported `EXACT 384/384 ELEMENT 680/680 LITERAL 509/509 satisfied` while
five bodies failed an assertion in the features. What did report it was the
reconciler's own `left for a human` list — `component.count() = 3; the body has 6` —
which is why that list is printed rather than counted.

**7. An example's usage of a code was treated as a definition of it.** The display
index read every `Coding` in every pinned package, and dropped a `(code, system)`
pair displayed two different ways. `hl7.fhir.r4.examples#4.0.1` — a declared
dependency of `hl7.fhir.r4.core#4.0.1`, and so genuinely loaded by the endpoint —
writes `v3-ObservationInterpretation` code `N` as `"normal"` in one Observation
example, while the CodeSystem in `hl7.fhir.r4.core#4.0.1` defines it as `"Normal"`.
The pair was therefore dropped as ambiguous, and seven Labor Observations could not
be given the display `interpretation.coding.where(... and display.empty().not())`
requires. The index now ranks its two sources: a `CodeSystem.concept.display` is the
specification defining a display and wins over any number of usages; usages are
consulted only for codes no shipped CodeSystem defines, which is the normal case
here, since LOINC and SNOMED CT are not redistributable and appear in usages only.
Two CodeSystems disagreeing drops the pair outright rather than falling back to a
usage.

This also removes an unstated dependence on *which* packages were passed to the
tool: the committed corpus had been reconciled against one package, a later run
against all of them, and the two gave different displays for the same code. The
corpus is now regenerated by one command over the full planned package set, and a
second pass over its own output changes nothing.

**8. An element's cardinality was decided by its name, ignoring what contains it.**
`identifier` is repeating on a resource — `Patient.identifier` is 0..\*, and the
table of repeating element names says so — but `Reference.identifier` is 0..1. The
assertion
`specimen.identifier.where(system = '...' and value = 'PRB-2024-1001').exists()`
therefore produced `"specimen": {"identifier": [{...}]}`, and IRIS rejected the
eight Labor Observations at the seed phase with `Property 'identifier' of Type
'Reference' cannot be an Array`. Cardinality is now decided by the containing
element first: inside a `Reference`, `reference`, `type`, `identifier` and
`display` are each 0..1 whatever they are elsewhere, and the parent element name is
carried through the navigator so that the decision is available where it is made.
Regenerating the corpus changed exactly those eight files and nothing else.

Like defect 4, this was caught by IRIS's own validator rather than by a gate of
this rig: `make check` passed on the corpus that carried it, because no check here
holds R4 cardinality, and an `exists()` assertion is satisfied by a single-element
array as readily as by an object. Also like defect 4, it is a case of satisfying a
step rather than stating the specification.

**9. A search criterion was a requirement on the corpus, and nothing held it.**
`search_reference_check.py` exists so that "no search results were found" is never
charged to IRIS when the corpus is what is missing. It mined that requirement from
two *assertion* shapes, both of which name a reference. A search can also demand
data through its own query string, with nothing in the scenario naming the element:

```gherkin
Scenario: Search for the RiskAssessment that belong to a Patient, by Risk
  Then Get FHIR resource at "http://fhirserver/RiskAssessment/?risk=
      http://terminology.hl7.org/CodeSystem/risk-probability%7Chigh&patient=..."
  And FHIR current response body evaluates the FHIRPath
      'entry.resource.count() > 0' ...
```

That `risk` searches `RiskAssessment.prediction.qualitativeRisk` is stated only in
the `SearchParameter` definition in `hl7.fhir.r4.core#4.0.1`, so the corpus
satisfied every reference requirement and still held no resource this search could
find: the seeded `RiskAssessment-Read-Example` carried no `prediction` at all.

What makes this one worth recording precisely is how it presented. In Round C the
search returned `total 0` and the case was charged to IRIS as a conformance
defect. In Round A — same rig, same corpus deficiency — the identical request
returned two resources and the case passed. The two resources were
`RiskAssessment/74` and `RiskAssessment-Update-Original-Example`, both written
earlier *in that same run* by the suite's own Create and Update scenarios: suite
2.7.0 executed those before the searches (17:29:37 and 17:43:24, against 17:55:30)
and 3.0.9 after them (21:31:53 and 21:46:54, against 21:18:49). So the corpus never
satisfied the scenario in either round, and the earlier round's pass rested on an
execution order that is not the suite's contract. A defect that reports itself as a
product regression on a version bump is the most expensive kind this rig can hold,
which is why the gate was extended rather than the one resource quietly fixed.

Token criteria are now mined from the query string of the search that precedes an
`entry.resource.count() > 0` assertion — only there, since only there does the
suite require a non-empty result — resolved to an element path through the pinned
packages' `SearchParameter` definitions, and checked like any other requirement.
Measured over Stufe 5 of 3.0.9: 53 criteria in scope, 84 not modelled and reported
as such. The check gates `make all`, so it is deliberately conservative: a
criterion fails only when the parameter resolves to exactly one plain element path
rooted at the searched type. `Observation.code | Observation.component.code` is two
and is skipped rather than halved, and a `where(...)` filter or an `as` cast is
skipped likewise — guessing a path would make the gate fail rounds over its own
modelling error. The authored corpus gained the one element the criterion needs,
`prediction.qualitativeRisk` of `risk-probability|high`. That element is not
invented for the search: `de.gematik.isik#5.1.3`'s `ISiKAMTSBewertung` marks
`RiskAssessment.prediction.qualitativeRisk` `mustSupport: true` at 0..1 and binds
it `extensible` to `http://hl7.org/fhir/ValueSet/risk-probability`, whose
CodeSystem is the one the suite's criterion names. The seeded resource validates
against the profile with no error-severity issue.

One false positive of the first implementation is recorded because it is the kind
that would make a gate like this untrustworthy: query strings are form-encoded, so
`telecom=030+1234567` is `030 1234567`, and decoding with `unquote` rather than
`unquote_plus` reported a passing scenario as unsatisfied against a correctly
seeded Patient.

**10. One code, two levels, and the base profile changed it between them.**
`http://fhir.de/StructureDefinition/identifier-kvid-10` fixes `Identifier.type` to
`identifier-type-de-basis#GKV` in `de.basisprofil.r4#1.4.0`, which the Stufe 3
endpoint pins, and to `#KVZ10` in `#1.5.4`, which Stufe 5 pins. Two bodies in
`common/` carried the code as a literal, one of each:

| Authored body | Code | Rejected in | Validator message |
|---|---|---|---|
| `Patient-Patient-Read-Example` | `GKV` | Stufe 5 | `Patient.identifier[1].type.coding.code`: `Value is 'GKV' but must be 'KVZ10'` |
| `Coverage-Coverage-Read-Statutory-Example` | `KVZ10` | Stufe 3 | `Coverage.subscriber.identifier.type.coding.code`: `Value is 'KVZ10' but must be 'GKV'`, and `Unknown code … #KVZ10` |

Each arrived in the official report as a profile non-conformance of the system
under test, and neither is one: IRIS stored and returned the code this rig seeded.
Counted on the verdict tables: round A carried eight such cases, two in Stufe 3
from the `Coverage` body and six in Stufe 5 from the `Patient` body; round C
carried five, charged as `PROFILE_NONCONFORMANCE` under GAP-006 and GAP-009
before the cause was traced.

The code is now written as
`{{pattern.IdentifierKvid10.Identifier.type.coding.code}}` and resolved per level
out of the `StructureDefinition` in that level's own pinned package — the same
principle as `{{profile.Name}}`, extended from a canonical to a fixed value. A
full body under `Stufe3/` and `Stufe5/` would have worked too, and was rejected:
it duplicates 137 lines of the most heavily asserted resource in the corpus to
vary one code, and the first drift between the two copies would surface as a
conformance failure in one level only — which is the failure mode the build step
exists to prevent, and which this defect is an instance of.

Two guards, because a resolver that guesses is worse than a literal. A name
defined more than once across the level's pins, at different canonicals or
different versions, is refused rather than taken by directory order — both
versions of `de.basisprofil.r4` are unpacked side by side, so order would decide
the code. And a fixed value that repeats is refused rather than indexed at `[0]`.
Measured: `1.4.0` pinned alone resolves `GKV`, `1.5.4` alone resolves `KVZ10`,
both pinned together resolve nothing and say why.

**This one changed a verdict, and it is the only one of the ten that did.** The
scenario `Stufe5/Basis` *Search for Patients by Name, group by `_count`* failed in
round A and passed in round C, and the first reading of that pair is a product
improvement. It is not. The two rounds issued the identical request, `GET
/Patient/?name=Graf&_count=20`, and both responses carried the same
`Patient-Read-Example` with the same `GKV` identifier. The step validates one
Patient out of the search bundle, and what changed is which: round A's bundle held
two entries with `Patient-Read-Example` first, round C's held four with
`ICUext-PatientinMusterfrau-617` — a fixture suite 3.0.9's ICU module required,
and one that carries no insurance identifier at all — first. The suite's 3.0.9
validator still rejects the same body in the same round: the identical message,
naming `ISiKPatient|5.1.3`, stands against five other scenarios of round C. So the
case did not change because the system under test changed, and recording it as a
fix would have credited the product for a reordered search result.

**The standing remedy for defects 5 and 8, recorded and not yet taken.** Both
arise from the same source: `reconcile_testdata.py` holds its FHIR R4 structural
knowledge in five hand-maintained tables — `LIST_ELEMENTS`,
`LIST_ELEMENTS_BY_TYPE`, `SCALAR_ELEMENTS_BY_TYPE`, `NON_CHOICE_DATETIME` and
`CHOICE_ELEMENTS` — keyed by element name and, at most, resource type. A table
keyed that way cannot express `Reference.identifier` being 0..1 while
`Patient.identifier` is 0..\*, nor distinguish a `Coding` from a `Quantity`; each
entry is also a statement about R4 that no artefact in this repository verifies.
The same facts are published, per element path, in the `StructureDefinition`
snapshots of `hl7.fhir.r4.core#4.0.1`, which is already pinned, already unpacked
into `vendor/packages/` and already loaded into every endpoint. `choice_type_check.py`
already reads them: its `load_r4_elements` builds 7790 element paths with their
types and cardinalities from those base definitions, following a child path into
the definition of its parent's type. Replacing the five tables with that index
would make each of these two defects unrepresentable rather than fixed.

It is recorded here rather than attempted because it rewrites the element-resolution
core of the tool that produces the test data, and it was reached between two phases
of a round already under way; a change of that reach must be made with the corpus
diffed element by element against the current one, not while a round is waiting on
it. The two container-aware tables added for defect 8 are therefore an interim
narrowing and are marked as such in the source.

### In the ISiK packages rather than in the suite: same version, two definitions

Found by the parity gate on the official 2026.2 round, and **not worked around** —
it needs no workaround, only a decision about which copy to load. Three packages are
published on `packages.simplifier.net` in content that differs from the copy bundled
inside `gematik1/isik-testsuite:2.7.0`, under the same `name@version`:

| Package | Difference | Effect on a verdict |
|---|---|---|
| `de.gematik.isik-terminplanung#3.1.1` | `ISiKKalender` is modelled differently — the `Schedule.extension:KalenderName` slice above all — and the bundled copy is internally stamped `version: 3.0.9`, `date: 2025-07-31` against the published `3.1.1` / `2025-12-01` | **Would change verdicts** if the published copy were loaded: the SUT would enforce a different profile than the one asserted against it |
| `de.gematik.isik#5.1.2` | The published `ISiKOrganisationFachabteilung` lacks the `Organization.type:Fachbereich.coding` element the bundled copy constrains to `min: 1, mustSupport: true`; stamped eight days later | Same |
| `de.medizininformatikinitiative.kerndatensatz.medikation#1.0.11` | The published copy's implementation-guide file is named `…-1.x..json`, with a doubled dot | Cosmetic |

The bundled copy is authoritative, because the reference validator validates against
the files in its own image. So `PACKAGE_SOURCE=refv` — the default — is the correct
setting for any round whose verdict is to be attributed to the SUT, and the official
2026.2 round used it. **This is a defect in the ISiK publication, not in the SUT and
not in this rig**, and it is the second of its kind after the `3.1.x` wildcard: a
same-version divergence between what is published and what is validated against is
exactly the class of problem a `name@version` pin is supposed to make impossible.

`PARITY.txt` names the rows, `PARITY-DETAIL.txt` the fields, and both are carried
into `provenance/packages/` of the delivered bundle.

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
