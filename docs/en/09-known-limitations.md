# 9. Known limitations and deliberate deviations

This document exists so that no deviation in this rig has to be discovered by
reading source. Two kinds of statement are collected here:

* **Deviations** — things this project deliberately does differently from a
  production deployment or from the shipped configuration. Each has a reason and
  none is hidden.
* **Limitations** — things a passing or failing result here does *not* prove.

Nothing below is a defect to be fixed silently. If a future change removes one, the
reason it existed has to be addressed too.

## What a result from this rig does and does not prove

**Does prove.** That the Gematik ISiK test suite, at the version the round pins
(`TESTSUITE_VERSION`, recorded per round in `provenance/env.resolved.txt`), run
in its official container with the official certification tag expression,
against a stock IRIS for Health endpoint of the pinned build, loaded with
exactly the FHIR packages the suite's own reference validator validates against,
produced the verdicts recorded in `test-report.zip`.

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
| Anything about the SMART App Launch flow | The suite states it does not evaluate the flow; it requires a token and asserts what the SUT does with it. No authorization-code exchange, no app launch and no consent step is exercised ([08-connect.md](08-connect.md#what-a-connect-result-does-and-does-not-prove)) |
| That the `Connect` verdicts of the submitted archive are a statement about token handling | They are not, and the bundle says so per case. The complete official run measures those 32 cases **without** a bearer token; the `connect/` archive measures them with one and supersedes them. The submitted `test-report.zip` is unchanged either way |

## Deviations from a production deployment

### Credential documentation

**Every credential this environment uses is published, verbatim, in the generated
reports.** Not redacted, not hashed, not referenced by filename. Two exceptions
exist and are stated under *The two credentials a round withholds* below; they
are exceptions on purpose and for a reason, not a softening of this rule.

Where it appears, deliberately:

| Location | Form |
|---|---|
| `analysis/results.md` §1 *Access credentials* | A table: API user, API password, scheme, Management Portal user, Management Portal password. Sourced as `<variant>/run.json`, key `credentials`, like every other claim in the document |
| `<variant>/run.json` → `credentials` | Machine-readable, top-level, with a `_comment` stating why |
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

### The two credentials a round withholds

A round with `CONNECT_ENABLE=true` introduces two kinds of credential that do not
fit the policy above, and
[scripts/lib/redact_credentials.py](../../scripts/lib/redact_credentials.py) is
where the difference is implemented.

| Withheld | Why it does not fit | Published instead |
|---|---|---|
| The six access tokens | Published verbatim they would be **useless to a reader** — expired long before anyone opens the bundle — while still being a credential of the kind this rig otherwise keeps at 0600 | The test-data key the token was staged for, plus its SHA-256. Strictly more informative than the value: the reader of an exchange learns *which scope's* token was presented, which is the question a 401 or a 403 raises |
| `AS_CLIENT_SECRET` and `AS_ADMIN_PASSWORD` | Neither expires with the round. The first mints fresh tokens for as long as the realm exists; the second opens the authorization server's admin console. Neither is needed to verify a verdict — a reader re-mints with `make tokens` | Nothing. They stay in the live 0600 files the operator works from |

**What makes a string a token is the string itself**, not a list of the values the
round currently holds: an access token carries `iss` and `exp`, and this realm's
also carry `scope`, so the claims are read out of the token. Matching only against
the staged values would pass a bundle re-collected after the tokens were re-minted
while its log still carried six credentials that were live when it was written.

**What is deliberately not redacted:** `<variant>/official/` — the archives the
test suite wrote, and their verbatim extraction. Those are the artefact the
*Bestätigungsverfahren* is based on, their checksums are the suite's own, and the
recorded traffic in `debug-report.zip` contains the `Authorization` headers as
sent. Rewriting them would make the bundle's central claim — that the official
report is byte-for-byte as the suite wrote it — false. The subtree is passed as an
explicit exemption, the bundle README states that it holds the headers, and the
tokens in it are expired by the time the bundle is read.

**It is proved, not claimed.** Three scripts write into a bundle —
`70-collect-report.sh`, then `80-gap-report.sh` and `81-case-evidence.sh`, which
append to it afterwards. Each gates its own output through
`assert_no_published_credentials()`, so the statement holds for the finished
bundle and not only for the part one stage produced. The scan caught a live token
pair in `case-evidence.json` during development, which is why the masking now
happens where entries are built rather than where they are rendered.

See [08-connect.md](08-connect.md#tokens-are-credentials).

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
| `AutheEnabled = 32` on the CSP applications | The installer's default is 64 (`AutheUnauthenticated`). An unauthenticated request is refused rather than served anonymously — though it is refused with **404, not 401**, because the CSP dispatcher declines to route rather than challenging. Measured: correct credential 200, missing 404, wrong 404 ([03-sut-iris.md](03-sut-iris.md#it-is-refused-with-404-not-401)). **Applies to a round without an authorization server, which is the default.** With `CONNECT_ENABLE=true` the endpoint is left exactly as the FHIR server's own installer created it (`8288`, no application resource) — not a relaxation, but the posture a bearer token requires, and the *less* deviating of the two. Established by measuring `InstallCSPApp` itself ([08-connect.md](08-connect.md#the-endpoints-authentication-posture)) |
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
| Route-level Basic `authentication` | So **the official feature files stay byte-identical.** A modified feature file would invalidate the run. The block **replaces** any `Authorization` header a feature file sets for itself — measured 2026-11-13 — which is why the one module that authenticates for itself is run through a second configuration that carries no such block ([08-connect.md](08-connect.md#why-connect-is-a-separate-invocation)) |
| `workflowUiPort` 9012 → 9010 | Upstream's own README and `dc-testsuite.yml` publish 9010; pinned so the bound and forwarded ports cannot disagree |
| `runTestsOnStart: false` for `MODE=manual` | Rendered as *separate* files under `run/tiger/manual/`, mounted only by the `manual` profile, so an unattended run cannot pick one up by accident |
| One extra `additionalConfigurationFiles` entry, listed **last** | The `zz-upstream-key-defects.yaml` overlay can only add keys, never override an upstream value |

**The feature files themselves are never modified.** Neither is the image: nothing
in this repository rebuilds it, patches a class or edits a scenario.

## Deviations introduced by a `Connect` round

Everything in this section applies **only** when `CONNECT_ENABLE=true`, which is
not the default. With it false, none of it is deployed and the round is configured
exactly as every round taken before this capability existed — which is what keeps
the August 2026.2 baseline round, and every round taken since, re-creatable from
this repository. The switch is recorded in `.env.resolved` and in each round's
provenance, so a round states which of the two configurations it ran.

Full detail, with the measurement behind each statement, is in
[08-connect.md](08-connect.md).

| Deviation | Consequence for reading a result |
|---|---|
| A third-party authorization server runs in the subnet | **It is not part of the system under test.** Keycloak 26.4.7, scaffolding exactly like the test suite container. The specification requires the FHIR server to *consume* tokens issued elsewhere, so the conformance statement is about that consumption. It is deliberately not an InterSystems product: a SUT issuing the tokens it then validates would make the test circular |
| The SUT's endpoint is left in the installer's authentication posture, not the tightened one | `AutheEnabled` 8288 and no application resource, instead of 32 and `ISiK_FHIR`. The *less* deviating of the two postures, and the one a bearer token requires; unauthenticated access is still refused, by the FHIR server itself with 401. A round in this posture is not comparable, request for request, with one in the other |
| TLS terminates at the Web Gateway, under a certificate from a local CA | Required by the product, not chosen: the FHIR server will not evaluate a bearer token on a connection `%request.Secure` reports as insecure. **No `X-Forwarded-Proto` is set anywhere in this rig** — the product's own check is left to do its job. The trust anchor is `run/tls/ca.crt`, so replaying a request by hand needs `--cacert`, and the certificate name is not in host DNS, so it also needs `--resolve` |
| The other nine modules keep the `:80` listener | Two listeners in front of one endpoint, not two endpoints. Deliberate: it keeps the nine modules' results comparable with every round taken before this one |
| One upstream test-data file is modified | `testdata/Stufe5/connect.yaml`, six keys, from the placeholder `SET-YOUR-TOKEN-HERE` to a real token — which is the suite's own documented instruction to the operator. `run/tiger/TESTDATA-PARITY.txt` reports the file as **PARAMETERISED** rather than IDENTICAL and names the six keys, so the modification is bounded and visible |
| Two kinds of credential are withheld from the published evidence | The six access tokens, and the realm's shared client secret together with the authorization server's admin password — the **exception** to the credential policy above, stated in full under [*The two credentials a round withholds*](#the-two-credentials-a-round-withholds). The official archives are exempt and stay byte-for-byte; `assert_no_published_credentials()` proves the rest rather than asserting it |
| The `Connect` module runs twice and a round holds two Stufe 5 archives | The complete official scope is **not** narrowed, because the certification process asks for a singular `test-report.zip`. For the 32 `Connect` cases the `connect/` archive's verdicts supersede the complete run's; they are never summed. Every derived record names the archive it read (`scopePartition`, `measuredIn`, `measurements[]`) |
| The host needs ~1.1 GiB more memory | A host sized for a round without this overlay is not automatically large enough for one with it. IRIS's own 25 % default is unchanged by the overlay, so the extra memory has to be real |
| The authorization server's state is ephemeral | `--db=dev-file`, realm re-imported from `run/as/realm-isik.json` at every start. Nothing in it is worth persisting; it is stated because production mode deprecates that database as a default, so the choice is explicit rather than inherited |

**HugePages are not configured, and this rig does not configure them.**
InterSystems recommends them for every host running IRIS. Without them IRIS's real
memory consumption is materially higher than its configured shared-memory size
suggests, and the 1.1 GiB above is on top of that. Like the firewall rules, this
is a host-level decision made outside this repository — stated here so that a host
sized from the shared-memory figure alone is not mistaken for a sized host.

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

Ten further defects, 11 to 20, were found afterwards by auditing this rig against
the rounds it had already produced rather than by the bump, and are recorded in
the two sections that follow this one: 11 to 13 in the derived documents, 14 to
20 in the test data and in the attribution that is supposed to keep a defect in
the test data out of the product's gap count.

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

The round that carried this change and nothing else joins all 1 234 of its cases
with the round before it: 0 regressions, one case newly passing — the scenario
above — and no other module's failure count moved
(`new round 20261110/results/comparison-C-vs-D.json`).

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

The round that carried this change and nothing else joins all 1 234 of its cases
with the round before it: 0 regressions, five cases newly passing — exactly the
five charged under GAP-006 and GAP-009 — and no other module's failure count
moved (`new round 20261110/results/comparison-D-vs-E.json`). That round's own
reconciliation charges 59 cases to the system under test, which is the count the
preceding round reached only once this cause had been traced by hand.

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

### Three further defects, found by auditing the rig against its own rounds

Defects 1 to 10 were surfaced by the suite bump: each stopped a round or changed a
figure in one. These three were not. They were found by reading the rig's own
output against the artefacts it had already archived, after the rounds were
complete, and all three are in the **derived** documents — `analysis/results.md`,
`GAP-ANALYSIS.md` and the attribution index they are joined through. None of them
can alter an official report: those archives are written by the suite inside its
own container and are only copied by this rig. Where a derived figure and an
official one disagree, the official one is correct and the derived document has a
bug, which is the rule under which all three were found.

All three are fixed. Two changed a reported figure and one did not; which is
which is stated per defect.

**11. One case in every Stufe 5 round that the pinned suite never selected.**
Each of the five rounds reported one more Stufe 5 case than its own tag
expression selects: 896 against 895 in rounds A and B3, 1 234 against 1 233 in
rounds C, D and E. The discrepancy was recorded as a non-blocking
`scopeMismatch` anomaly in `analysis/results.md` and carried forward unexplained.

The cause is two upstream behaviours meeting. ISiK splits its Stufe 5 outlines
into a selected and an `@Optional` `Examples:` block — measured on 2.7.0, 32 of
the 47 Stufe 5 outlines in scope carry at least one block the level's tag
expression excludes, 69 excluded rows in total, while Stufe 3 has none — and
Serenity writes the *whole* `dataTable` into the outcome regardless of which
blocks the run selected; and
Serenity 5.3.10's basename collision (recorded above) duplicates steps between
two features that share a basename. A duplicated step is attributed to an
`Examples:` row by matching the parameter values in its own title, and for one
step the only row it matched was in a block the level's tag expression excludes:
`_tag` is row 2 of the selected block of `Stufe5/Basis/AllergyIntolerance-Search`
and the first row of the `@Optional` block of
`Stufe5/Medikation/AllergyIntolerance-Search`. The result was a phantom case —
and it **passed**, so the error ran in the direction that flatters the product.

The 2026-08-07 baseline round reported 895 correctly, from the same counting
logic: `git` shows the port to 3.0.9 changed which levels this tool iterates and
nothing about how it attributes a step to a row. What differed is the input.
Serenity duplicated **10** steps in the baseline round and **11** in round A —
`serenity-summary.json` reports 905 cases against 906 — and of round A's eleven,
exactly one matches only an excluded row, where of the baseline's ten, none does.
The defect was therefore latent in every round and visible only in a round whose
report happened to carry that particular duplicate, which is the worst property a
counting defect can have: it does not reproduce on demand.

`excluded_example_rows()` in `scripts/lib/results_report.py` now attributes each
`dataTable` row to an `Examples:` block by line number, evaluates the level's
compiled tag expression against that block's effective tags, and makes rows from
unselected blocks ineligible as match targets. It guards against guessing: if
every block's row count does not come out exactly, it excludes nothing rather
than attribute rows it does not understand. The `foreignExampleStep` anomaly
detail now states, for such a step, that the only row it matches is in a block
this level does not select, so the scenario cannot have run it.

Measured by re-deriving round A from its archived bundle: Stufe 3 unchanged at
506 of 506; Stufe 5 now 895 cases — 783 asserting, 112 precondition — of which
839 passed and 56 failed, against the official report's own `casesExecuted: 895`
and `casesAssertive: 783`. A key-level diff of the per-case verdicts shows
exactly one case removed, the `_tag` phantom at line 46, and **no verdict changed
anywhere**. Serenity's own 906 still reconciles as 895 plus the 11
`foreignExampleStep` outcomes, so the blocking `totalsDisagree` check still
balances.

`scopeMismatch` is also split by direction, because it was non-blocking in both
and the two directions are not alike. A run that reports *fewer* cases than the
tag expression selects is legitimate — a narrowed debugging run does exactly
that — and keeps `scopeMismatch`. A run that reports *more* is never legitimate,
and now emits `scopeExcess`, which is in the exit-status blocking set. The
asymmetry is deliberate and is stated in `docs/en/06-execution.md` next to the
blocking set itself.

**12. A failure this rig authored, charged to the product, with no channel able
to see it.** Defect 10's eight round-A cases reached the official report as a
profile non-conformance of the system under test, and the gap report had no means
to say otherwise: all eight were categorised `PROFILE_NONCONFORMANCE` and counted
against the product until the cause was traced by hand. Both of the generator's
existing test-data channels are structurally blind to this class, and it is worth
stating why, because neither blindness is a bug in itself:

* **the HTTP channel cannot see it.** These are Tiger `TGR` validation steps,
  which Serenity records with no `restQuery`; `failure.http` is `null` on all
  eight. There is no status, no request and no response body to reason from.
* **the seed channel cannot see it either.** `provenance/seed/<Level>.json`
  records a pre-run `$validate`, but that validator is *the SUT's own*. On round A
  it reported `validationErrors: 0` across all 58 Stufe 3 and 73 Stufe 5 seeded
  resources. A build that does not enforce a profile cannot flag data that
  violates it — and whether it enforces the profile is precisely the condition
  under test, so this channel's silence carries no information here.

The question itself has one honest answer: a rejected value is the product's
fault only if the product returned something other than what it was given. That
is answerable from an artefact written *before* the run, so a sixth evidence
source was added. `scripts/70-collect-report.sh` now archives the rendered
instances of each level, `build/fhir/<Level>/`, into `provenance/corpus/<Level>/`;
`gap_report.py` parses `location=…,message=Value is 'X' but must be 'Y'` out of
the reference validator's message, resolves the dotted element path — including
`[n]` subscripts — in every staged instance of that resource type at that level,
and re-attributes to `TEST_DATA_DEFECT` only when a staged instance carries the
rejected value **and** no staged resource of that type carries the required one.
The entry prints both columns, so the join can be checked rather than trusted.

Measured on round A's archived report with the matching pre-fix render staged as
its corpus: 86 failing cases resolve to 32 findings, 30 charged to the system
under test and 2 — eight cases, six in Stufe 5 from the `Patient` body and two in
Stufe 3 from the `Coverage` body — charged to this project. Before the change the
same bundle produced 33 findings, all 33 charged to the product. Negative
control: round E, executed with defect 10's per-level resolution in place,
carries no value-mismatch message at all, so there is nothing to re-attribute and
nothing is.

Two notes kept because each cost a wrong answer once:

* the obvious pattern is wrong. `ValidationMessage\[[^]]*\]` stops at the `]`
  inside `location=Patient.identifier[1].type.coding.code` and matches 2 of the 8
  occurrences — the two bracket-free `Coverage` ones — which reads as a much
  smaller problem than it is. The location character class must admit brackets.
* one rejected element is reported once per profile that pins it. The `Patient`
  case is reported twice, by `identifier-kvid-10` and by `ISiKPatient`: 12
  message occurrences across 6 cases. The `(element, rejected, required)` triple
  is therefore deduplicated before the join, so that the evidence table shows one
  row of evidence rather than two, and so that the confirmation is not weighted
  by how many profiles happen to pin the same element.

A boundary, stated rather than hidden: `provenance/corpus/` is archived from this
change forward. The rounds A to E bundles do not contain it, so re-deriving their
gap reports re-attributes nothing unless the matching render is staged into them
by hand — which is what the measurement above did, in a scratch copy, and not in
the archived bundle. The official reports in those bundles are untouched either
way.

**13. The attribution index asserted a measurement it was in no position to
make.** `WRITE_VALIDATION_GAP`'s `irisLocus` in `gap-analysis/build_index.py`
stated a property of build 2026.2.0.221.0's write path. `index.json` is built by
`make packages`, before any container is started and with no SUT reachable, and
it is then joined against whichever round's results it is handed — so that
sentence would be reproduced verbatim in the gap report of every later build,
including a build in which it is false. This defect changed no figure in any
round; it put an unsupported sentence in a document whose entire value is that
every sentence in it is supported.

The locus now names the subsystem and the setting a developer would open — the
interactions strategy's Add/Update path and the endpoint's configured
`ResourceValidation` setting — states that `$validate` is a separate code path
whose result does not settle this one, and points at a two-request probe to be
run against the round's own endpoint and recorded beside it. The probe is
specified in `docs/en/07-reports.md`, together with the rule that makes it
necessary: a measurement from an earlier build must not be reported as a property
of a later one. The `why` text of the first `responseCodeRules` entry is narrowed
the same way, to what the case establishes rather than what it suggests.

Both of these are strings in the index, so they take effect when `index.json` is
next rebuilt by `make packages`. A bundle whose index predates this change
reproduces the old text, which is correct behaviour — the report quotes the index
the round was joined against, and the index carries its own build timestamp in the
report header for exactly this reason.

### Seven further defects, found by auditing the corpus against the suite's own validator

Defects 11 to 13 are in the documents this rig derives. These seven are one level
deeper: six are in the test data itself, or in the attribution that exists to keep
a defect in the test data out of the product's gap count, and the seventh is in
how the gap report writes its own output.

They were found by putting one question to the round of record: of the 79 failing
cases it charged to the system under test, how many would have failed whatever the
product did? The question is answerable because that round archived the rendered
corpus it seeded (`provenance/corpus/`), so the bodies the suite's validator
rejected can be read rather than reconstructed. The measured answer is **21 of
79**, in six distinct fixture defects. None of them is in the official reports,
which record what the suite observed and are correct; all of them are in the
interpretation this rig put on those reports.

All seven are fixed. The fixture fixes change what a round observes and therefore
take effect only in a round executed after them; the attribution fixes re-derive
from an archived bundle, and the measurements below were taken that way.

**14. One of our fixtures was dragged into four other scenarios' search
results.** Four Stufe 5 scenarios search `Encounter?location=Location/Location-Read-Example`
and then validate the result Bundle against core FHIR. The suite's contract names
that ward for the in-progress Encounter and for the Medikation Encounter, but our
in-progress Encounter was placed only in room `Z001`, so the single Encounter the
server could return at that ward was the **planned** one — the one resource in the
corpus that carries `http://hl7.org/fhir/5.0/StructureDefinition/extension-Encounter.plannedStartDate`,
which a FHIR R4 core validation rejects as `invalid Version '5.0'`. Every one of
those searches therefore returned exactly the body that cannot pass the step that
follows it. Reported as GAP-006 of the round of record, 3 cases, category
`PROFILE_NONCONFORMANCE`, charged to the product.

The fix is in the fixtures and is described element by element in
[05-testdata.md](05-testdata.md): the in-progress Encounter now occupies the
searched ward by literal reference and the room by identifier only, which keeps
`location.location.reference` single-valued for the four `all(...)` assertions
that walk it, and the planned Encounter was moved to `Location-Example`, which
gained the `standortnummer` identifier its ward slice requires. What cannot be
separated this way is the planned Encounter's patient, class and type: the suite
requires all three to collide with other searches, and that residue is defect 18
below.

**15. Five authored bodies that the product accepted and the suite's own
validator rejects.** The remaining 18 of the 21 cases are five independent
content defects in bodies this project authors, each of which made a scenario
fail for a reason no implementation could have avoided:

| Rejected by the reference validator | Our body | Cases |
|---|---|---:|
| `Unknown code 'http://fhir.de/CodeSystem/KontaktDiagnoseProzedur#hauptdiagnose'`, and with it `Encounter.diagnosis.use.coding:Diagnosetyp: minimum required = 1` | the in-progress Encounter's `diagnosis.use` coding | 8 |
| `Practitioner.name.text: minimum required = 1` and `Unknown code 'urn:oid:1.2.276.0.76.5.514#010'` | the Practitioner fixture carried neither the name text nor a resolvable qualification code | 3 |
| `ValueSet.expansion.contains.version: minimum required = 1` | the ValueSet fixture's expansion, which this project authors in full, gave no `version` on any of its seven entries | 4 |
| `MedicationStatement.effective[x]: minimum required = 1` | 2 of the 4 staged MedicationStatement bodies carried no `effective` element | 2 |
| `Coding.version: minimum required = 1 (from … ISiKATCCoding)` | the MedicationAdministration body's ATC coding carried no `version` | 1 |

Every one of these was accepted by the system under test's own `$validate`
operation during stage 5 of seeding, over all 131 bodies, with 0 errors. That is
the measurement that settles what the seed step's `$validate` is worth as an
oracle for our own data: nothing. It is the artefact under test being asked
whether the test data is correct.

All five are corrected in `testdata/fhir/`, and the corrections are held in place
by defect 19's preflight rather than by vigilance: the current corpus validates
clean against the suite's own validator, 123 of 129 bodies checked and 0 invalid.

**16. The attribution reached a verdict on one of the three ways the validator
says no.** Defect 12 gave `gap_report.py` the ability to prove that a rejected
*value* came from our corpus. It could not read the other two things the
reference validator says: that a required element is **absent**, and that a
**code cannot be resolved**. Both were left with the product, and the cardinality
veto that defect 12 installed actively suppressed the absent-element case.

`corpus_evidence()` now resolves all three shapes, and absence is decided by
`absence_verdict()`, which is a three-way verdict rather than a predicate:

* **ours** — no staged resource of that type carries the element, so no response
  could have carried it whatever the product did;
* **the product's** — *every* staged resource of that type carries it, so
  whichever one the scenario read had the element and something between the
  stored resource and the wire dropped it;
* **indeterminate** — the location names a profile slice, or the level staged no
  resource of that type, or only some staged resources carry it. A `TGR`
  validation step records no request path, so the instance the scenario read
  cannot be identified, and a corpus that supports neither verdict must say so.

Conservative in both directions, and the blockers are printed rather than
implied: nothing is attributed to this project unless some finding is positively
ours, and nothing is attributed if any finding is positively the product's, if a
cardinality phrase this module cannot parse is also present, or if a
`but only found 0` clause escaped the pattern.

Measured by re-deriving the round of record from its archived bundle: 79 failing
cases resolve to 29 findings, 25 charged to the system under test and 4 — 15
cases — to this project, against the published 30 findings with all 79 cases
charged to the product. The four are the first three rows of defect 15's table;
the remaining two rows are defect 17.

**17. Two blind spots that remain, stated rather than closed.** Of defect 15's
five fixture defects, two are invisible to the attribution even now, and both
cases stay with the product in the re-derived report — which is the correct
behaviour for an unexplained rejection, and also an over-charge of 3 cases that
only a human reading of the corpus caught:

* `MedicationStatement.effective[x]` was carried by 2 of 4 staged bodies, so the
  verdict is `indeterminate`. The failing step is a `TGR` validation with no
  request path; the instance the scenario read is not recoverable from the
  official report.
* `Coding.version` is rooted in a FHIR **datatype**, not a resource type: the
  message attributes it to `ISiKATCCoding`, whose `type` is `Coding`. There is no
  staged resource to join it to. The report now says exactly that, derived from
  the attribution index's `profileElements[...]["type"]`, in place of the earlier
  "the level staged no Coding" — which was true, and read as a statement about
  this corpus rather than about the shape of the message. The verdict is
  unchanged; what changed is that the sentence is now defensible.

**18. Scenarios that no implementation can pass were charged to the product.**
Three extensions from FHIR R5 are required by Stufe 5 feature files and defined
only by an ISiK package for FHIR R4, so a body that satisfies the scenario
requiring one cannot survive the core validation the same suite applies to a
Bundle containing it. `build_index.py` now inventories them from the two
artefacts that prove the contradiction — the feature line that requires the URL
and the package that defines it — and `index.json` carries the result:

| Extension URL | Required by | Defined by |
|---|---|---|
| `…/extension-Encounter.plannedStartDate` | `Stufe5/Basis/Encounter-Read-Planned.feature:52`, `Stufe5/ICU/VitalSignMinimal/Encounter-Read-Planned.feature:53` | `de.gematik.isik#5.1.3` |
| `…/extension-Appointment.replaces` | `Stufe5/Terminplanung/Appointment-Read.feature:44` | `de.gematik.isik#5.1.3` |
| `…/extension-Schedule.name` | `Stufe5/Terminplanung/Schedule-Read.feature:41` | `de.gematik.isik#5.1.3` |

(Measured on the 3.0.9 index; the 2.7.0 corpus states the same requirement at
`Stufe3/Basis/Encounter-Read-Planned.feature:48` and
`Stufe5/Basis/Encounter-Read-Planned.feature:51`.)

A failure the index can trace to such a URL is now categorised
`UPSTREAM_SUITE_CONFLICT`, which joins `TEST_DATA_DEFECT` and
`ENVIRONMENT_PREREQUISITE` in `NOT_SUT_CATEGORIES`: excluded from the headline
count, reported in its own section of the gap report, with `irisLocus: none` and
a stated feature of "not determinable — the scenario tests two incompatible
requirements at once". The category is deliberately narrow. It is applied only
when the index lists the URL with a requiring feature line **and** a defining
package, so a scenario is never excused by this rig on the strength of its own
failure message.

**19. The preflight could not see the fabricated bodies.** Stage 4 of seeding
validates the rendered corpus with the suite's own reference validator, which is
the only oracle independent of both the product and this project's opinion of its
own data — and it read each body's `meta.profile` to decide what to validate it
against. A fabricated body carries no `meta.profile`, because stage 1 stamps that
element only where the feature files state the profile; the scenario passes the
profile URL to the validation step instead. So the bodies with the least
provenance — the ones this project invented — were exactly the ones the check
skipped.

The profile is not in the body, but it is in the suite. The requirements corpus
records, per fixture, which profiles the in-scope scenarios assert against it, so
`validate_corpus.sh` now derives a `<Level>/<ResourceType>/<id> → profiles` map
from it and `CorpusValidator` validates a profileless body against that. The key
carries the level deliberately: the same fixture id is asserted against the v3
profile under Stufe 3 and the v5 profile under Stufe 5, and validating a Stufe 3
body against the Stufe 5 profile would manufacture a defect. A body that no
in-scope scenario asserts any profile against is reported as
`no-profile-asserted` rather than as a skip — the difference being that the first
is a checked statement about the suite and the second was an assumption.

Measured on the 3.0.9 corpus: 129 bodies, 6 carry no `meta.profile`
(`Appointment-Replaced-Example`, `Device-Read-Example`, three ICU extension
fixtures and `patient-id-not-in-context`), the map holds 101 fixtures, and it
asserts no profile against any of the 6. So nothing was in fact left unchecked by
the old skip in this corpus — which is now a measurement rather than a hope, and
the number to watch when the next fabricated fixture is added.

**20. Redirecting the gap report's Markdown left its JSON in the published
bundle.** `gap_report.py --run-dir <bundle> --out-md /tmp/…` is how this report is
re-derived against an archived round for a measurement — every figure in defects
16 and 17 above was taken that way. `--out-json` defaulted to the run directory
independently of `--out-md`, so a re-derivation wrote one half of a published
pair: the bundle then held a Markdown document generated by one version of the
code and a JSON document generated by another. It happened to the round of
record while this section was being written; the file was restored byte-for-byte
from the round's own slim archive, which is why both forms are archived.

Redirecting one output now redirects both unless both are named. The published
`gap-analysis.json` of the round of record was verified identical to the archived
copy after restoration, and its reconciliation still reads 30 entries over 79
cases — the figures the published Markdown states.

### Three further defects, found by running the repaired rig

**21. The report volume is shared between suite versions, and whichever image
wrote it last owns its contents.** The suite image's entrypoint is

```
sh -c "rm -rf $REPORT_DIR/* ; mvn clean verify … || true ; mv -v $APP_HOME/target/*report.zip $REPORT_DIR/"
```

with `REPORT_DIR=/app/report` backed by the named volume `isik-tiger-report`, out
of which `60-run-tests.sh` copies the archives. Both halves of that line run as
the image's own user, and the two releases this project runs do not share one:
`2.7.0` runs as root, `3.0.9` as an unprivileged user. A volume last written under
2.7.0 therefore holds root-owned files in a directory a 3.0.9 run can neither
unlink nor write to, so the clear at the start fails and the `mv` at the end
fails — each printing one line into a container log of some tens of thousands.
The run then completes normally and **the volume still holds the previous round's
archives**, which this rig copies out and certifies.

Every downstream check passed, because a stale report is a structurally valid
report: the zips open, their records parse, the hashes recorded in `run.json`
match the files on disk, and the reconciliation balances. The round carried
another round's result under its own run id, its own `run.json`, and its own
provenance.

It happened to `run-20261111T105824Z` (3.0.9, Stufe 5), run directly after a
2.7.0 round. Measured: the archived zips were stamped `2026-11-11T08:45:33Z`
against a run that started `10:58:24Z`, all 628 outcome records in them were
timestamped `2026-11-11T08`, and the container log opens with
`rm: can't remove '/app/report/debug-report.zip': Permission denied`. The
archives Maven actually wrote were still in `/app/target` inside the exited
container, stamped `11:37:01Z`, and were recovered intact — 917 outcome records,
`test-report.zip` 2,106,638 bytes, `debug-report.zip` 154,939,065 bytes. The
round's `run.json` records the recovery, the superseded hashes and the superseded
record count, so the substitution is on the face of the bundle.

Two things were wrong and both are fixed. The volume is now emptied by a
throwaway root container before each level — making its prior ownership
irrelevant — and left group- and world-writable so the next run's user can write
into it whichever image that run uses. And the archive is now checked against the
clock: Maven writes the archives at the very end of a level, so a genuine archive
is always newer than that level's start and a carried-over one is always older by
at least a level. Section 4a of `60-run-tests.sh` asserts exactly that, and where
it fires it first tries to replace the stale copy from `/app/target` in the
exited container rather than discard a completed forty-minute run. It fails the
level only if that recovery does not produce a fresh archive, and it deletes
nothing.

The remedy is the clear; the assertion is the proof. Neither alone would be
enough: the clear can be defeated by any future image that runs as a third user,
and the assertion cannot distinguish a stale archive from no archive at all
without it.

**Every published round was then audited against the same failure**, by comparing
each level's archive mtime and the record timestamps inside it with the run window
in its own `run.json`. Rounds A, B3, C, D, E and F are all internally consistent:
each archive falls inside its own run's window. No published figure is affected by
this defect, and the only round that was is the one named above, which is
republished from the recovered archives.

**22. The gap report's closing summary kept its own copy of a list the generator
owns.** `80-gap-report.sh` prints a one-line work summary after the generator
runs, and to say how many findings are the product's it needed to know which
categories are not. It held that list literally — and held two of the three
entries, missing `UPSTREAM_SUITE_CONFLICT`. On the round of record it therefore
printed 21 findings against the product where the report said 20.

No document was affected: the generator writes every number in
`GAP-ANALYSIS.md` and `gap-analysis.json` from `NOT_SUT_CATEGORIES` itself, and
the shell line is console output only. It was also caught by the script's own
cross-check against `reconciliation.irisGapEntries`, which printed a warning
naming both numbers and saying which to believe — which is how it was found.
That is the check working as designed, but a check that fires on every run is a
defect, not a safeguard.

The list is a property of the generator, so the generator now states it:
`gap_report.py` publishes it as `reconciliation.notSutCategories` and the shell
reads it from the document it has just written. The cross-check stays, and now
means something stronger — both numbers are derived from one list in one file, so
a disagreement means the document is internally inconsistent rather than that the
wrapper is out of date.

The same summary also reported findings and cases in one parenthetical, in the
form `18 distinct finding(s) (16 SUT, 2 not the SUT's: 0 test-data, 0
suite-conflict and 7 unmet-precondition case(s))`, where the three trailing
numbers are cases and read as though they decomposed the two before them. Findings
are now counted on one line and cases on the next, each in its own unit.

**23. A second Practitioner fixture carried the same two content defects as the
first.** `testdata/fhir/v3/Stufe5/Practitioner-ICUextPractitionerWalterIntensiv834.json`
was missing `Practitioner.name.text`, which `ti-practitioner|1.1.1` requires
(`min: 1`), and carried qualification code `010` in
`urn:oid:1.2.276.0.76.5.514`, which the validation module cannot resolve. Both are
the defects corrected in `Practitioner-Practitioner-Read-Example.json` under
defect 15 above, and the correction here is the same: the `text` element is
populated and the code is `011001` (`FA Allgemeinmedizin`), which the same module
demonstrably resolves.

It was the extended preflight of defect 19 that found it — the ICU extension
fixtures are three of the six bodies that carry no `meta.profile`, so the old
`meta.profile`-driven check had never read this one at all. That is the second
fixture of the same family to be corrected, which is the argument for the
preflight rather than for a case-by-case fix.

**It changed no verdict.** The 3.0.9 Stufe 5 round reported zero
`TEST_DATA_DEFECT` cases; the ICU module's two failing cases are a
CapabilityStatement search-parameter declaration (`GAP-002`) and a search-result
assertion (`GAP-008`); and no finding in the round mentions
`Practitioner.name.text`, a qualification code, or this fixture's id. The fixture
was latent — invalid as fabricated, read by no assertion that could see the
difference — which is why the round was not re-run for it.

### Two further defects, found by taking the repaired rig back to 2.7.0

Both were found by the first 2.7.0 round run after the 3.0.9 work, and neither
could have been found by a 3.x round: release 3.0.0 removed ISiK Stufe 3 from the
suite, so a 3.x round exercises exactly half of what these two concern.

**24. The renderer could not read this project's own test-data overlay.** A body
authored once for both levels resolves `{{data.<key>}}` against the contract, and
`load_contract()` builds that contract out of `vendor/requirements.json` — out of
the keys the suite's *feature files* reference. That is the right and deliberately
sole source for every key the suite defines, but it cannot see a key the suite
references nowhere, and this project's `zz-upstream-key-defects*.yaml` overlays
define precisely those: keys an upstream feature file references but upstream's
own test data leaves undefined, and values a level's contract omits although its
profiles require the element.

The suite reads those overlays — `30-render-config.sh` lists them last in
`additionalConfigurationFiles`, so they can only add — and resolves
`${data.<key>}` in a Gherkin step from them. The renderer could not, so the same
placeholder in the same file was a value at one level and a hard failure at the
other: `{{data.atc-code-version}}` resolved under Stufe 5, whose own
`medikation.yaml` happens to define the key, and did not resolve under Stufe 3,
where only the overlay does. Seven bodies failed to render and the round could
not start. The overlay entry's own stated purpose is that without it the shared
body has to carry a literal and the two levels silently drift — so the renderer's
inability to read it was a hole in that mechanism rather than a missing feature.

`load_overlay()` now reads the overlays as the *rendered configuration for this
round* names them, rather than from a list kept in the renderer: the
configuration is the authoritative statement of what the suite will load and
under which `baseKey`, and reading it is what makes the two contracts the same
set by construction. Only entries whose filename marks them as this project's are
read, and the result is merged *under* `requirements.json`, so every key the
suite documents still comes from the suite. Measured on 2.7.0: exactly one key is
contributed, `data.atc-code-version` at Stufe 3, and none at Stufe 5.

**25. A definition the validator could not resolve was charged to this project's
test data.** The ISiK packages define several extensions under canonicals of the
form `http://hl7.org/fhir/5.0/StructureDefinition/extension-Encounter.plannedStartDate`,
and the ISiK Encounter profiles slice `Encounter.extension` on them. The
validator does not resolve such a canonical out of the package that defines it —
it reads the `hl7.org/fhir/<version>/` authority as a cross-version reference —
and so reports, per slice:

```
Slicing cannot be evaluated: Problem with use of resolve() - profile
[CanonicalType[http://hl7.org/fhir/5.0/StructureDefinition/extension-Encounter.plannedStartDate]]
on Encounter.extension:plannedStartDate could not be resolved (@char 1)
```

Stage 4 counted that as an error and failed the seed, naming
`Stufe3/Encounter-Encounter-Read-Appointment-Example.json` as a body that does
not satisfy the profile it claims. It does satisfy it. The body carries one
extension, `Aufnahmegrund`, and no planned date at all; the message fires on any
body carrying any extension validated against such a profile, and it is the
validator stating that *it* could not resolve a definition the *profile* points
at. Both modules that could judge this body ship the definition —
`StructureDefinition-PlannedStartDate.json`, inside
`de.gematik.isik-basismodul-3.1.1.tgz` — so it is present and still not
resolvable, which is what makes this the packages' and the validator's
interaction rather than anyone's content. No body this project could author would
remove the message.

Such a message is now counted and reported separately, under `unresolvable` in
`corpus-validation.json` and as a `NOTE` line rather than an `INVALID` line, and
it does not make a body invalid. It is reported rather than filtered, because the
count is the thing to watch if a future package set does resolve these canonicals
— at that point they become real validation results again. Measured on the 2.7.0
corpus: 131 bodies, 0 invalid, 1 body with 2 unresolvable slice definitions.

The same root cause already had a run-time counterpart. `suite_conflict_verdict()`
recognised the instance-side form of it — `Extension url '…' is not valid (invalid
Version '5.0')`, which is the core R4 validator refusing the extension a body
carries — and classified a scenario failing on it as `UPSTREAM_SUITE_CONFLICT` on
positive evidence from the index: the canonical has to be one the suite's own
feature files assert *and* one a package this round loaded defines. It now
recognises the profile-side form on the same evidence. The two thresholds differ
on purpose: at run time the question is whether to charge the product, and a
message is not evidence enough for that; in stage 4 the question is only whether
to charge this project's data, and a message that makes no claim about the body
cannot.

### One further defect, found by asking who could re-derive a published figure

**26. The one statement a release decision turns on was produced outside the
rig.** Every figure in this project's documents is required to trace to an
artefact (`07-reports.md`), and the generators that produce them are part of the
rig, checked by `make check`, invoked by a `make` target and documented under
`docs/`. For the figures *within* a round that held: the results analysis cites
the official report case by case, and the gap analysis joins that analysis. The
comparison *between* two rounds did not. "Nothing that passed before fails now"
is not visible in either round's archives — it is a join over both — and the
generator that made that join lived beside the round's documents, as
`compare-rounds.py` in the round folder, called by hand.

It was a careful program, not a scratch one, and its figures were right. The
defect is where it lived. A tool outside `scripts/` is in no `make` target, is
not reached by the repository's self-check, is absent from `docs/`, is not
installed on a lab host by `make provision`, and — because the round folders are
untracked evidence rather than source — would not be found by whoever runs the
next round. The number that decides whether a release ships was the only number
in this project whose generator a reader could not get to from the repository.

`scripts/lib/compare_rounds.py` is that generator, moved into the rig and
reached by `make compare BASELINE=<run-id> CANDIDATE=<run-id>`. It reproduces
all nine published comparisons from the archived bundles: the counts, the
per-level totals, and the regression and newly-passing entries one for one.
Nothing published was wrong, which is why this entry is in the rig's defect list
and not in any round's.

Three properties were carried over from the round-folder script, because each
exists to stop a wrong number rather than to tidy the code:

* **Scenario Outline rows are matched across suite versions by their parameter
  bindings, not by their index.** Removing one executed row shifts every index
  below it, and an index join then compares each shifted row against a different
  test. That is not hypothetical: between 2.7.0 and 3.0.9 the
  `Observation-Search` outline moved its `_tag` and `subject` rows into an
  `@Optional` block, and an index join fabricated a regression at
  `combo-code-value-quantity` with a matching fix at `component-code` out of one
  unchanged failure. This was re-measured while moving the tool: joining 2.7.0
  against 3.0.9 by index reports 2 regressions and 2 fixes where the binding
  join reports 1 and 1.
* **An Examples row whose bindings the analysis does not record is fatal under
  that key, never silently joined by position.** A positional fallback for a
  subset of rows is worse than one for all rows, because the failure would be
  invisible.
* **A repeated join key is fatal.** If a key does not identify a case uniquely
  within one round, the join would compare one round's case against an arbitrary
  one of the other's.

Two properties are new, and the second is the one presentational difference from
the hand-made files:

* The output is written into the candidate's bundle and recorded in its
  `analysis/MANIFEST.txt`, so archiving the round archives the comparison with
  it, and an inspector verifying the bundle does not find an unlisted file.
* **Only levels both rounds ran are compared**, and a `levels` field records
  which. A Stufe-5-only round is comparable with a two-level round, on Stufe 5.
  Where that case arose, the hand-made file reported the baseline's full totals
  and listed its 506 Stufe 3 cases in `onlyInBaseline`; counting a difference in
  scope as 506 absent cases reads like a mass regression. Every count quoted in
  a document is identical under both conventions.

The cost of the weaker cross-version key is stated rather than hidden, as it was
before: a scenario the newer suite renamed, or an Examples row whose parameters
it changed, appears in `onlyInBaseline` and `onlyInCandidate` instead of in the
comparison, and those two lists are how a reader sees how many cases a
cross-version comparison does not cover.

### Two further defects, found by asking which criteria the gate never sees

**27. The corpus gate read only one of the suite's two search shapes.** A search
scenario that asserts `entry.resource.count() > 0` is a requirement on the seed
data, and `search_reference_check.py` exists to hold the corpus to those
requirements before a container starts. It mined them from the query string of a
`Get FHIR resource at "…?a=1&b=2"` step. The suite has a second shape — `Post
FHIR search request to "…/_search" … with parameters:`, which carries the
criteria in a Gherkin table — and the pattern that recognises a search of a
collection cannot match it, because the type is followed by `/_search"` rather
than by `?` or `"`. 41 steps in the pinned 2.7.0 tree take that shape. Not one
of their criteria was ever checked.

Nine of the 41 search by `_tag`, and eight of those nine are `@Optional` and so
outside the certification selection. One is not:
`Stufe5/Basis/Observation-Statuses-Search.feature:63`
searches `Observation` by `_tag` and requires the result to be non-empty. No
seeded `Observation` carried any `meta.tag` — only an `Appointment` and a
`DocumentReference` did — so the system under test answered with an empty bundle,
which is the correct answer to that query against that corpus, and the round
recorded a failure against the product. It did so in round G (1 case, GAP-012)
and in round H (2 cases, because 2.7.0 also has
`Stufe5/Vitalparameter/Observation-Search.feature:49` mandatory where 3.0.9 has
it `@Optional`). Both rounds were re-run after the remedy; neither is published.

Two things had to change for the gate to see it. The step shape is now mined by
`RE_SEARCH_POST` and `table_query()`, which converts the table into the query
string the suite will send. And `_tag` had to become resolvable at all: the
parameters every resource has are defined once with base `Resource` and an
expression rooted there, so the per-type lookup `(Observation, _tag)` could never
hit, and the criterion was reported as "not modelled" — counted honestly, but not
checked. `token_path()` now falls back to the `Resource`-based definition.

The remedy in the corpus is one `meta.tag`, on
`testdata/fhir/Stufe5/Observation-Arterial-Blood-Pressure-Read-Example.json`;
that fixture and no other because 2.7.0's mandatory site searches `_tag`
together with `_id` and then requires that id in the bundle. With it, Stufe 5
reports 47 of 47 criteria satisfied.

**28. A Gherkin table cell was split on the pipe it was escaping.** The parser
split a table row on every `|`. In Gherkin a cell carries a literal pipe as
`\|`, and the suite writes exactly that in all ten of its table-borne token
criteria — `| ${data.tag-system}\|${data.tag-value} | ${data.search-count} |` is
two cells, not three.

This defect was latent for as long as defect 27 existed: nothing read those
tables, so nothing was wrong with the way they were read. Fixing 27 made it live
in the same hour, and the first run of the repaired gate is what exposed it. It
reported three unsatisfied criteria instead of one. Two were its own: a `_tag`
whose value stopped at `common-meta-tag-de\` with the code displaced into the
next column, and a `Patient?identifier=http://fhir.de/sid/gkv/kvid-10\` reported
as unsatisfied while the check's own evidence line printed the seeded `Patient`
carrying that very system. A check that prints what it found alongside what it
wanted is why that took one reading rather than one round.

`_split_row()` now honours `\|`, `\n` and `\\`, and keeps any other escape
verbatim — `| 4\d\d |`, a status-code regex in an Examples column, must not lose
its backslashes. Nothing else moved: rebuilding `vendor/requirements.json` across
both levels gives the same 506 and 895 executed cases, the same 432 and 783
asserting cases, and not one of its 220 features with a changed count.

### One further defect, found by asking what a crash proves

**29. A step that crashed was counted as a product failure.** The gap report
classifies a failing case by what the failure says. One failure in round G says
nothing at all: `java.lang.NullPointerException`, `Cannot invoke
"String.replaceAll(String, String)" because the return value of
"org.hl7.fhir.r4.utils.FHIRPathEngine.convertToString(org.hl7.fhir.r4.model.Base)"
is null`. That is the test suite's own expression engine failing to evaluate,
not a comparison of an expectation against a response, so the case establishes
neither a pass nor a failure of the system under test. It was nonetheless
charged to the product, which claimed more than the record carries.

What the step asserts is that the bundle contains a given `Medication`, and the
response did contain it. The response also contained a second entry — an
error-severity `OperationOutcome`, correctly marked `search.mode = outcome` and
carrying no `fullUrl`, which is how FHIR says a server reports a problem with a
search it did execute. Three sibling scenarios in the same feature run the
identical step against bundles with no such entry
(`Stufe5/Medikation/Medication-Search.feature`, by `ingredient-code`, by
`lot-number`, by `status`); all three pass. The one scenario whose response
carries the outcome entry is the one that crashed. The step cannot read a
conformant searchset.

`suite_crash_verdict()` now settles such a case ahead of every other rule, on
two signals that have to agree: the error type is a `NullPointerException`, and
the message names the method whose return value was null as one belonging to the
suite or to a library it evaluates with (`org.hl7.fhir`, `de.gematik`,
`net.serenitybdd`, `net.thucydides`, `io.cucumber`). An exception that names the
*response* as the thing it could not handle — a parse failure on an empty body,
which is defect GAP-015's signature and a real statement about the product — is
left to the ordinary classification. The category it lands in,
`UPSTREAM_SUITE_CONFLICT`, is excluded from the gap count; its label is now
"Test suite defect" rather than "Test suite contradicts itself", which described
only the one instance known when it was written.

The reason text points the reader at the exchange anyway, because this one holds
a product finding that the crash obscured: the search was
`Medication?ingredient.code=V03AB23&_id=…`, the answer was HTTP 200 with the
requested resource, and the self link records the executed search as
`Medication?_count=100&_id=…` — the chained criterion was not honoured, and the
`OperationOutcome` says a type modifier is required because `ingredient` can
reference either `Substance` or `Medication`. That behaviour belongs in the
report; this case's failure does not. On round G the rule fires exactly once: 42
failing cases, 41 of them the product's.

### One further defect, found by asking what an entry's title covers

**30. An entry described one of its cases and was titled after it.** The gap
report groups failing cases by root cause and then describes each group from the
*first* case that reached it. Where a group covers cases that differ in what
they tested, every field of that description — the title, the FHIR feature, the
expectation, the cited requirement — was true of one case and asserted of all of
them.

Measured on the two published rounds, before the fix:

a) Round I's largest finding was titled "reference element `subject`" and
   counted six cases. Their steps name three different elements: `subject`
   (`Stufe3/Medikation/MedicationAdministration-Search.feature:81`, `Stufe5` :55),
   `performer.actor` (`Stufe3` :91, `Stufe5` :101) and `context`
   (`Stufe3/Medikation/MedicationStatement-Search.feature:55`, `Stufe3` :56).
   The suite's `reference-element-all` step words its failure identically for
   all three, and the group key falls back to the message, so three elements
   arrived at one key and two of them were not described at all.

b) The same message-based key put genuinely different searches in one entry.
   Round J's composite-parameter finding also held a
   `DocumentReference?creation=2026-01-31&patient=…` case; round I's held that
   one, a `MedicationRequest?requester.identifier=…` case and a
   `Patient?given:contains=An%26na%5C%2CVic%24tor%7Ca` case. None of the three
   was visible in the report: the entry named an `Observation` search on
   `combo-code-value-quantity`.

c) The same key split one defect in two in the opposite direction. Round J
   reported the unhonoured `Observation` composite parameter as two entries,
   because one feature file spells the parameter into its error text
   ("…do not fully match the combo-code-value-quantity search criteria") and the
   others do not.

Two changes. First, a group's description is now merged over every case it
covers: the feature and the expectation list each distinct value, and the cited
requirements and packages are the union. Where the cases share one search shape
the title is that shape and a note records the queries as measured, because
joining several search URLs into a heading is unreadable without being more
precise. A group whose cases were attributed to different *mechanisms* is
reported as a warning rather than merged, since one entry cannot state two
mechanisms without saying which case has which. Second, a search-semantics
finding is keyed on what was searched rather than on how the suite worded the
failure: the resource type, the set of parameter names, and which of the two
assertions failed — that the result set was empty, or that its members did not
satisfy the criteria. The suite's wording carries a stack frame and varies
between feature files; neither belongs in the identity of a defect.

Nothing about any case's attribution changes, so no round's charged case count,
category count or reconciliation moves. What changes is how those cases are
grouped and described: round J now reports 17 findings, 14 of them the
product's, from the same 41 failing and 33 charged cases; round I reports 21
findings, 20 of them the product's, from the same 50 failing and 47 charged
cases — three more work items than before, because three real product findings
had been absorbed into an entry about something else.

Both published rounds were re-analysed from their own archived results. Round I
ran on suite 2.7.0, so its 2.7.0 attribution index was rebuilt first and the
rebuilt package set was verified identical, version by version and digest by
digest, to the manifest the round itself recorded; the lab was then returned to
the 3.0.9 state of the round of record and verified against round J's manifest
the same way. The official reports are untouched by all of this: the gap
analysis is a derived document.

One limitation remains, and is not worked around: the same product behaviour
still appears as two entries when the suite exercises it with different
companion criteria. In round I, `MedicationRequest?requester.identifier` is one
entry and `MedicationRequest?patient&requester.identifier` another, because the
Stufe 3 and Stufe 5 features send the chain with and without a patient
criterion. The two entries state the identical expectation, and an entry's
identity is deliberately mechanical — what the record shows, not what a reader
infers from it. Grouping by root cause across such entries is the assessment's
job, and the assessment does it there.

### One further defect, found by asking what "already installed" installed

**31. The marker said an installation was present, not which one.** The IRIS
container installs the FHIR endpoints at first start and then writes
`run/iris-state/bootstrap.done`. A later start that finds the marker reuses the
installation instead of repeating a package import that takes minutes. The
marker was empty: it recorded that an install had happened, not what it had
installed. Two things make that the wrong question, and both occurred here.

a) **A round can be pinned to a different test-suite version than the one the
   running instance was installed for.** The package plan is derived from the
   suite image and rewritten by `make packages`; the marker lives on the host
   and survives that. Round J (`run-20261111T144543Z`) was pinned to suite
   3.0.9 and analysed against the 3.0.9 plan, but it ran against the instance
   installed at 12:24:57Z from the 2.7.0 plan. Its archived
   `provenance/iris-provenance.json` records `packagePlanSource`
   `gematik1/isik-testsuite:2.7.0` where its archived `provenance/package-plan.json`
   records `sourceImage` `gematik1/isik-testsuite:3.0.9`, and the ISIK5 endpoint
   held `de.gematik.isik@5.1.2`, `de.fhir.medication@1.0.0`,
   `hl7.terminology.r4@6.5.0` and `hl7.fhir.uv.sdc@3.0.0` where the plan assigns
   `5.1.3`, `1.0.4`, `7.1.0` and `4.0.0-ballot`. The round's own log states the
   reuse in one line — "isik-iris is already running - keeping its bootstrap
   markers" — and nothing else anywhere said that the packages were another
   round's. The loaded packages are what an IRIS FHIR endpoint validates against
   and what it declares in its `CapabilityStatement`, so a profile or search
   parameter introduced in `5.1.3` was absent for a reason that lay in the rig.

b) **A container recreate destroys the installation and leaves the marker
   behind.** The instance's databases live in the container's writable layer;
   there is no named volume for them. Anything that recreates the container
   therefore removes every namespace and endpoint while the marker, on a host
   bind mount, continues to report the installation as present. The next round
   then fails at its first request, for a cause nothing in its output names.

Three changes, each closing the hole at a different distance from the error.

The marker now carries the fingerprint of the plan it installed — the suite
image the plan came from and, per endpoint, its namespace, its base URL and the
ordered packages loaded into it (`iris/bootstrap/plan_fingerprint.py`, used by
every side so that they cannot disagree about what "the same installation"
means). A second copy is written inside the container's own filesystem, where it
shares the lifetime of the databases rather than of the bind mount. Reuse
requires both copies to match this start's plan; anything else reinstalls, which
costs minutes and never a wrong measurement. It is deliberately not a digest of
the plan file, which would force a reinstall when regeneration reworded a
comment without changing a single package.

`make up` refuses to start a round against a running instance whose marker names
another plan, and says which two plans those are and what to do about it. This
is the only place that case can be caught: `compose up -d` on an unchanged,
already-running container does not restart it, so the container's own guard is
never reached. It is a stop rather than an automatic rebuild — destroying a live
installation is the operator's decision, and a mistyped `TESTSUITE_VERSION`
should cost a corrected line in `.env` rather than a reinstallation.

`make report` refuses to publish a round whose archived provenance disagrees
with its archived plan. The check is made from the two files the bundle itself
carries, so an inspector can repeat it without this rig, and it needs no
fingerprint to be useful: comparing the plan's `sourceImage` against the SUT's
recorded `packagePlanSource` is what identified this defect, and it is the
fallback for any round installed before the fingerprint existed. Applied to the
rounds already on record, the check passes round I — the 2.7.0 plan it publishes
is the one its endpoints were installed from — and refuses round J.

Round J is therefore withdrawn as a measurement of suite 3.0.9: what it measured
was conformance against the ISiK packages of the 2.7.0 plan. It is superseded by
round K, which installed the 3.0.9 plan cold and measured that. Round I is
unaffected and remains the 2.7.0 round of record. No part of round J's analysis
is reused: the round was re-run rather than re-analysed, because the difference
is in what the SUT was, not in how its answers were read.

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
| Credentials documented on purpose | `README.md` §*Access credentials*, `analysis/results.md` §1, `<variant>/run.json` `credentials._comment` |
| The two credentials a round withholds | The bundle `README.md` §*Access credentials*, `CASE-EVIDENCE.md` §3, and the marker at each site, which names the test-data key or the scope and the SHA-256 |
| That the authorization server is not the SUT | `connect/run.json` `authorizationServer`, and the bundle `README.md` |
| Which archive measured a given case | `<variant>/run.json` `scopePartition`, the merged level's `measurements[]`, and `measuredIn` on every case record |
| Which certificates the round used | `provenance/tiger-TLS.txt` |
| Which tokens the round presented | `provenance/tiger-TOKENS.txt` — scope, issuer, audience, lifetime and SHA-256 per token, no values |
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
