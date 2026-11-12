# 7. Reports and the evidence bundle

## Three documents, three audiences, one authority

| Document | Audience | Status |
|---|---|---|
| `<stufe>/official/test-report.zip` | Gematik / TITUS | **Authoritative.** The *Bestätigungsverfahren* artefact. Nothing in this repository modifies it |
| `analysis/results.md` | German regulatory inspector | Derived. A cited index into the official report; states *what* happened, never *why* |
| `GAP-ANALYSIS.md` | IRIS for Health developer | Derived and interpretive. States *why*, in FHIR terms. Explicitly **not** regulatory documentation |
| `CASE-EVIDENCE.md` | the same developer, at the keyboard | Derived. The annex of the document above: one clause per failing case rather than per defect, quoting the exchange verbatim |

The separation is the point. An inspector must be able to tell evidence from
interpretation at a glance, so the interpretation lives in a differently named
file at the bundle root rather than inside `analysis/`. And every derived
statement carries the path of the official file that substantiates it, because a
German regulatory reader works with the artefacts they already know and is
entitled to distrust anything a script wrote.

> Where a derived document and the official report disagree, **the official report
> is correct and the derived document has a bug.** Both generated documents say so
> in their own text. That is not modesty; it is the only claim that makes a derived
> document usable as evidence at all.

Three audiences per round, and `make all` produces all of them. The fourth row is
an annex and not a fourth audience: it is addressed to the reader of the row above
it, for the part of that reader's work the deduplicated document cannot serve, and
it is generated in the same step. A further document exists for the one question no
single round can answer — whether anything that used to pass now fails — and
because it is a join over *two* rounds it is written only when a pair is named:
`comparison-vs-<run-id>.json`, below.

## The bundle

```
reports/<run-id>/
  README.md                              the bundle's own entry point
  analysis/results.md                    the cited results document
  analysis/results.json                  the same, machine-readable
  analysis/scenario-verdicts.csv         one row per test case
  analysis/MANIFEST.txt                  sha256 of every file here
  GAP-ANALYSIS.md                        the developer work list
  gap-analysis.json                      the same, machine-readable, for diffing rounds
  CASE-EVIDENCE.md                       its annex, one clause per failing case
  case-evidence.json                     the same, machine-readable, for diffing rounds
  comparison-vs-<run-id>.json            what moved against an earlier round, if asked for
  stufe3/official/*.zip                  untouched, as the suite wrote them
  stufe3/official/SHA256SUMS             their checksums, as received
  stufe3/official/extracted/test-report/  verbatim extraction
  stufe3/official/extracted/debug-report/ verbatim extraction
  stufe3/run.json, run.log
  stufe5/…
  provenance/                            everything needed to explain the result
```

**The bundle is complete without the repository.** Provenance that lives in `run/`
and `vendor/` is copied in, because `make clean` deletes `run/` and the next round
regenerates `vendor/` — after which an archived report would cite files that no
longer exist. `reports/` is never deleted by any target.

### Extraction

Each archive is extracted into `official/extracted/<kind>/`, one directory per
archive, classified by substring (`*test-report*.zip`, `*debug-report*.zip`) so a
suite release that changes the version in the filename does not break every
citation. They must not share a directory: both contain a `serenity`-adjacent
layout and `debug-report.zip` has an `empty_folder` its assembly descriptor creates
on purpose, so a merged extraction would make every citation ambiguous about which
archive it came from.

Destinations are removed and re-extracted rather than overwritten. `make report`
is re-run freely — after fixing a generator, for instance — and a stale file left
behind from a previous suite version would be cited as evidence.

### What each archive contains

| `test-report.zip` (submitted) | `debug-report.zip` (not submitted) |
|---|---|
| `unresolvedReports/*.json` — the Serenity `TestOutcome` records | `serenity/` — the browsable HTML report, with request and response bodies |
| `serenity-summary.html` — the single-page summary | `serenity-summary.json` — Serenity's own counts, used as an independent check |
| `pom.xml` — which makes the tool-chain version table in [04-testtool.md](04-testtool.md) verifiable from the submission alone | `config/tiger.yaml` — *this project's rendered configuration*, as the suite received it |
| | `logs/` — Maven and server logs |

`config/tiger.yaml` inside the debug archive is exactly
`run/tiger/tiger-isik-<stufe>.yaml`, credential included. That is what
`provenance/tiger-RENDERED.txt` lets an inspector confirm: it lists each rendered
file with its sha256, so the config in the official archive can be matched against
the one this project generated. The configuration is deliberately not copied into
`provenance/` a second time — the archive already holds it verbatim.

### Provenance

| File | What it proves |
|---|---|
| `seed/SEED.txt`, `seed/<Level>.json` | Which corpus the run started from: PUT status, read-back, `$validate` issues, per resource |
| `seed/assertions-<Level>.json` | The rendered corpus checked against the assertions the feature files make |
| `seed/search-references.json` | The search scenarios' reference requirements, checked against the corpus |
| `iris-provenance.json` | The SUT's own record: `$ZV`, namespaces, endpoints, the package ids it reports it loaded, the five configuration values read back |
| `iris-bootstrap.log` | The SUT's install log, verbatim |
| `tiger-RENDERED.txt` | Checksums of the rendered configuration, and the values substituted into it |
| `tiger-TESTDATA-PARITY.txt` | File-by-file parity of the staged test-data contract against the suite's own |
| `packages/MANIFEST.txt` | Every FHIR package loaded into the SUT, with its tarball sha256 |
| `packages/PARITY.txt`, `packages/PARITY-DETAIL.txt` | Bundled-vs-published package parity, and the fields behind every non-`MATCH` verdict. Present if any pass used `PACKAGE_SOURCE=simplifier`; a carried-over table is stamped as such and describes the comparison, not this round's payloads |
| `package-plan.json` | Which package version went to which endpoint, and why |
| `env.resolved.txt` | The deployment's whole resolved configuration — image digests, versions pulled, generated credentials. The single file from which the round can be reconstructed |
| `requirements.json` | Every scenario, precondition block and assertion extracted from the suite's own feature files — what makes the scope figures checkable without the suite image |
| `CREDENTIALS.md` | The credential sheet as generated at deployment |

`env.resolved.txt` is copied byte-for-byte, not filtered, and named `.txt` rather
than `.env` so an inspector's tooling reads it instead of sourcing it. An absent
provenance source is warned about, never fatal: a bundle from a partial round is
still worth having, and `results.md` says which provenance is missing.

## `analysis/results.md`

### How the citations are built

Serenity records neither the feature-file line of a scenario nor a readable report
filename — the names are a sha256 of the outcome's qualified id — so neither can
point an inspector at the requirement that was tested. Three joins bridge that:

1. `userStory.path` is the feature path relative to the suite's own `features/`
   directory (`Stufe3/Basis/Account-Read.feature`), which is exactly the `rel` key
   of `vendor/requirements.json`. That yields level, module and scope.
2. The scenario *name* is matched against the parsed feature file to recover its
   declaration line, its tags, and whether it is a `@vorbedingung` /
   `@Precondition` block rather than a test.
3. `dataTable.rows[i].lineNumber` gives the exact `Examples`-row line for a
   Scenario Outline, and `testSteps[i].lineNumber` the line of the failing step.

Two citation targets are recorded per case whenever both exist:

* `<stufe>/official/extracted/test-report/unresolvedReports/<name>.json` — inside
  the **submitted** archive, so an inspector holding only the submission can still
  follow the citation;
* `<stufe>/official/extracted/debug-report/serenity/<name>.html` — the same
  outcome as a browsable page with request and response bodies.

They share a base name because Serenity derives both from one `ReportNamer`. The
name is read from the file we were given and the `.html` sibling is **probed**,
never reconstructed.

A document from `unresolvedReports/` is taken as a `TestOutcome` only if it has
both `testSteps` and `name`: Tiger's `generate-serenity-reports` mojo copies every
root `*.json` in there, and Serenity writes more than outcomes. Anything else is
counted and reported as skipped, never silently dropped.

### The failing step, not its ancestor

A Cucumber step in Serenity is a tree: the scenario's `Then …` owns the Tiger glue
calls it expanded into, and all of them inherit `FAILURE`. The ancestor names the
requirement; the **deepest** failing step names the assertion. Both are recorded —
the leaf because that is what a developer needs, the ancestor because that is what
an inspector reads.

### Sections

| § | Contents |
|---|---|
| 1 | What was run: level, Maven profile, tag expression, whether it was the official scope, timings, SUT image + digest + `$ZV` build + namespace, suite image + digest, deploy mode, package source, container exit code. Then **Access credentials**, then the test corpus the run started from |
| 2 | Scope and reconciliation — the three independent counts side by side |
| 3 | Results: asserting cases only (the conformance figure), then all executed cases by raw `TestResult`, then per module per level |
| 4 | Failures, with citations — grouped by level and module |
| 5 | Every executed case — the complete verdict list, `P` marking a precondition block |
| 6 | Provenance and checksums: the archives, the FHIR packages with tarball hashes, the **package-tree modifications the IRIS loader required**, and the table of copied provenance files |
| 7 | Anomalies |
| 8 | How to verify this document |

Verdicts group Serenity's `TestResult` values: **passed** = `SUCCESS`; **failed** =
`FAILURE`, `ERROR`, `COMPROMISED`, `UNSUCCESSFUL`; **other** = `PENDING`,
`SKIPPED`, `IGNORED`, `ABORTED`, `UNDEFINED`. The raw value is carried per case in
`results.json` and in the CSV, so nothing is lost to the grouping.

Two distinctions the document makes explicitly, because both are easy to misread:

* **No test case at all ≠ nothing failed.** A report with no cases in it is
  missing evidence, not a clean sheet, and §4 says so in those terms.
* **A clean sheet is the *unexpected* outcome.** IRIS for Health is known not to be
  fully ISiK compliant, so no failures is more likely to mean the run did not test
  what it was supposed to. §4 tells the reader to check §2 and §7 first.

Messages are truncated to 320 characters inline — a Tiger validator message can run
to several kilobytes of profile diagnostics — with the full text in `results.json`
and, verbatim, in the official report.

### §6 states where the package tree was modified

§6 does not only list the packages and their checksums; it also states, in the same
section, the one place where this project modified something *inside* the package
tree the SUT was loaded from. IRIS resolves every declared dependency by exact
package id and aborts the whole import on the first one it cannot resolve, so four
declarations had to be rewritten and one snapshot-only package had to be loaded.

That belongs next to the checksums rather than in a footnote, for two reasons. The
reader is entitled to know that the unpacked `package.json` files differ from the
published ones — and, equally, that the `.tgz` payloads do **not**, so every sha256
in the table above still attests the published bytes. And each row names the owner
of its finding, because a reader who meets "we changed a package" in a conformance
report will otherwise reasonably wonder whether the SUT's result was manufactured:
one is an IRIS loader limitation, one an upstream IG defect, one an ISiK validator
configuration gap, one a deliberate exclusion by this project. **None is a
conformance finding against the unit under test.** The full record, with the reason
recorded per change, is `provenance/package-plan.json` → `dependencyRewrites` and
`snapshotDependenciesPromoted`; the section is generated from it and cites it, and
the citation check below covers that path like any other.

The mechanism itself, and how each of the four cases was established, is in
[03-sut-iris.md](03-sut-iris.md#unresolvable-dependency-declarations).

### §8, and what a reader can actually re-do

§8 is a script: verify `SHA256SUMS`, re-extract the archives yourself next to ours,
follow any citation from §4 or §5 to its `TestOutcome` JSON, read the same outcome
as a page with bodies, open the whole Serenity report, then regenerate this
document and diff it. It then gives the `curl -u` form for re-issuing a cited
request against a rebuilt environment, and notes that the endpoint in §1 is the
address *inside the isolated subnet* — from the deployment host, substitute
`127.0.0.1` and the published port, both of which `provenance/CREDENTIALS.md` lists.

It closes by pointing at `docs/en/generated/REQUIREMENTS-Stufe{3,5}.md`, which
quote every `@vorbedingung` block verbatim and untranslated, indexed by the same
`feature:line` citations used above.

## The citation check

The claim that makes the bundle usable is that every path it cites resolves. That
is **checked, not asserted**: after the results document is generated,
`70-collect-report.sh` walks `results.json` — both official archives per level,
both citation targets per case, every provenance record — and reports the count of
distinct cited paths and how many are missing, naming up to ten. A dangling
citation is exactly the defect an inspector finds first.

The check warns rather than aborts, because `make report` must reach
`make gap-report`.

## The bundle manifest

`analysis/MANIFEST.txt` is sha256 over every file in the bundle. Two subtleties,
both of which would otherwise make `sha256sum -c` fail on a correct bundle:

**Order.** `README.md` is written *before* the manifest and is covered by it. The
obvious order — manifest first — leaves the README either unlisted (first run) or
listed with the previous generation's hash (re-run, since it carries a timestamp).
Either way the check fails on a finished bundle, and an unlisted or mismatching
file in an evidence bundle is indistinguishable from tampering to the reader most
likely to check. The README also states the bundle's file count, which is counted
from the same file set the manifest covers, so the two agree by construction.

**Exclusions — exactly two.** `analysis/MANIFEST.txt`, which cannot contain its own
hash, and `provenance/.index.json`, which is scaffolding handed to the generator
and reproduced as a table in §6. `provenance/packages/MANIFEST.txt` is *not*
excluded: it is evidence, and excluding it by name would leave the package
checksums outside the integrity check.

`80-gap-report.sh` and `81-case-evidence.sh` run after the manifest is written, so
each appends its own two files' hashes — dropping any previous entries for those
paths first, then re-sorting with `LC_ALL=C sort -k2` to reproduce the byte order
`70-collect-report.sh` produced. That is what keeps two rounds' manifests diffable
and `sha256sum -c` passing on the finished bundle either way.

```sh
cd reports/<run-id> && sha256sum -c analysis/MANIFEST.txt
```

## `GAP-ANALYSIS.md`

### A pure join

The gap analysis reads exactly two files: the run's own `analysis/results.json`
(what failed) and `gap-analysis/index.json` (what each step asserts and which FHIR
package supplies it). No network, no test suite image, no IRIS, no licence key.

That is what makes it reproducible from an archived bundle months later, which is
what lets a developer diff two rounds — and with `--generated-at` fixed, two runs
are byte-identical.

It joins over `results.json` rather than over the archives directly so that both
documents count the same cases from the same source. `80-gap-report.sh` therefore
distinguishes two operator errors with different fixes: no bundle means the round
has not been run; no `results.json` means it has but `make report` has not.

### Joining a failing step to the indexed corpus

A failure is only as informative as the indexed step it joins to: the index is what
supplies the request context, the resource type and the operation, so a failure
that does not join degrades to a vaguer FHIR feature and stops grouping with its
siblings. The join is tried by line first, because that is exact, then by the step
text.

The text passes normalise through `build_index.shape_of` — **the same function that
produced every `shape` in the index**, imported rather than reimplemented. This was
a real defect: the generator used to carry its own near-copy, which rewrote `'arg'`
to `"X"` where the index keeps `'X'`, and left `<placeholder>` untouched. Every
single-quoted step — which is every FHIRPath assertion — therefore failed to match,
and so did every Scenario Outline template step. Measured on the reference round:
**21 failing steps did not join, 20 of them for this reason alone**, and fixing it
cut uncategorised failures from 34 to 19. Do not reintroduce a local normaliser.

The 21st is a Gherkin DocString step: Serenity reports the step text with the
DocString appended while the index stores the step line alone
(`FHIR current response body evaluates the FHIRPaths:`), so the two shapes cannot
be made equal by any normalisation. A last pass matches the first line only, within
the named scenario, because a bare step line is a weaker key than a full shape.

**The warning, when it fires, names no cause it has not established.** It prints
what the index was built from and how many feature files it holds, and marks any
entry whose feature is absent from the index `[feature not indexed]` — that mark is
what distinguishes a genuine version mismatch, which `make packages` fixes, from a
generator defect, which it does not. An earlier version asserted the mismatch
unconditionally and told the operator to rebuild; in the one round where it fired
that was wrong — the index was the pinned 2.7.0 with all 220 features present — and
it cost an investigation. It also labels the line it prints (`step line 92` vs
`scenario at line 92, no step line recorded`), because the fallback used to be
printed bare, as though it were a step line.

`index.json` is built by `make packages`, while the feature files and the unpacked
packages are both present, and **cannot be rebuilt from the bundle** — the bundle
holds no packages. An index older than the feature corpus is warned about rather
than fatal (it may legitimately predate a re-run of the same pinned versions), and
the report prints the index's build timestamp in its header so the reader can
always see which corpus was used. A stale index would otherwise attribute this
round's failures to the previous round's packages, silently.

### What every entry carries

The three elements the brief requires, in this order:

1. **What test step was executed** — the resolved Gherkin step with
   `feature:line`, the Serenity sub-step that actually failed, the Scenario Outline
   row's data, and the HTTP exchange in a ` ```http ` block.
2. **What FHIR feature was tested** — the mechanism (a profile constraint, a
   search parameter, a CapabilityStatement declaration, an operation), the ISiK
   conformance resource that requires it, and the supplying package by
   `name@version`.
3. **The expected and the actual result** — the expectation in FHIR terms, then the
   verbatim validator message and response body as the official report recorded
   them.

Written for a reader with intimate IRIS knowledge including source access, very
good FHIR knowledge and almost no ISiK knowledge: every ISiK term is expanded on
first use, and every requirement is stated in FHIR terms rather than by citing
German specification prose.

The HTTP exchange is quoted **verbatim, including the `Authorization: Basic …`
header Tiger sent.** A developer has to be able to replay the exact request that
failed without reconstructing it. See
[08-known-limitations.md](08-known-limitations.md#credential-documentation).

### Attribution: how a package becomes an answer

Not annotated — derived. Profile assertions resolve through the canonical URL to
the package that declares that `StructureDefinition`. Interactions, search
parameters and operations resolve to the ISiK `CapabilityStatement` that declares
them, with its `capabilitystatement-expectation` code (`SHALL` / `SHOULD` / `MAY`)
quoted, so the *strength* of the requirement is visible rather than implied.

**Element constraints come from the snapshot, not the differential.** The
differential looks cheaper and gives wrong answers:
`ISiKAbrechnungsfall`'s differential says nothing about
`Account.identifier.type`'s cardinality — it only marks it `mustSupport` — while
its snapshot carries `min=1` on the `Abrechnungsnummer` slice. The validator's most
common complaint on that profile is exactly
`Account.identifier[0].type: minimum required = 1`, so a differential-only index
would report that constraint as merely `mustSupport` and understate the
requirement. Element **ids** are kept for the required set, not just paths: the id
names the slice (`Account.identifier:Abrechnungsnummer.type`), which is the
difference between "every identifier needs a type" and "the billing-number
identifier needs a type". A developer needs the second reading. A profile shipped
without a snapshot falls back to the differential and is *recorded* as such, so the
report can say the attribution is narrower for that profile rather than silently
offering less.

CapabilityStatements are read only from `de.gematik.isik*` packages, so an ISiK
failure cannot be attributed to an HL7 example resource.

### Categories

Ten, a closed set. A category exists to answer one question: **which part of IRIS
would a developer open.** Nothing is added without that answer and nothing is
force-fitted — a step shape the classifier cannot place is recorded as
`UNCLASSIFIED` rather than filed under the nearest-looking heading, because a wrong
category sends a developer to the wrong source file, which is worse than sending
them nowhere.

| Category | Signal | IRIS locus (summary) |
|---|---|---|
| `UNSUPPORTED_OPERATION` | A FHIR operation (`$book`, `$update-metadata`) is invoked or declared and the SUT does not implement it | The endpoint's `OperationHandlerClass`; built-ins on `HS.FHIRServer.Storage.BuiltInOperations` |
| `PROFILE_NONCONFORMANCE` | The body does not satisfy an ISiK `StructureDefinition`, or a FHIRPath assertion over a `mustSupport` element fails | Usually resource *content*, not IRIS code. IRIS is implicated when the element was stored and is missing from the response |
| `WRITE_VALIDATION_GAP` | The status code disagrees with the profile on a write: a body ISiK forbids was accepted, or one it requires was refused | The write path's validation *depth* — the strategy's Add/Update path and the endpoint's `ResourceValidation` setting. Which schema a given build enforces there is a measurement on a running SUT, not a property of the index; see [the probe](#probing-the-write-paths-validation-depth) |
| `MISSING_SEARCH_PARAM` | A required search parameter is absent from the SUT's CapabilityStatement, or a search using it does not filter | Search-parameter indexing; an unindexed parameter is not advertised |
| `CAPABILITY_STATEMENT_GAP` | `/metadata` omits an interaction, resource type or operation ISiK declares `SHALL` | CapabilityStatement generation — usually means the type or interaction is not *enabled*, not merely undeclared |
| `SEARCH_SEMANTICS` | The search executes but returns the wrong set — a modifier, prefix, chain, `_include` or paging behaviour | The search layer and `SearchQueryProcessor` |
| `RESPONSE_FORMAT` | The REST envelope is wrong rather than the resource: content type, charset, `_format`, status, or the identity of what a read returned | The REST layer / read path. Deliberately also covers `resource has ID` assertions — same code path |
| `REFERENCE_INTEGRITY` | A `Reference.reference` does not resolve, or a referenced resource is missing from a bundle the SUT assembled | Reference storage and rewriting; `_include` / `_revinclude` assembly |
| `TEST_DATA_DEFECT` | The seeded resource was absent, unreadable or already flagged by `$validate` before the run | **None.** Ours, not the product's |
| `UNCLASSIFIED` | The step shape is not in the classification table — almost always a suite version that grew a new shape | Unknown; the step and response are quoted verbatim so the entry is still actionable |

#### Probing the write path's validation depth

`WRITE_VALIDATION_GAP` is regularly the largest single class of a round, and the
question a developer asks first — *does this build check a write against the base
R4 schema only, or against the endpoint's profiles?* — cannot be answered from the
report. The index is written by `make packages`, before any SUT exists, so it
states the locus and leaves the depth to be measured per round, against that
round's own endpoint, while the stack is still up:

```sh
B=http://127.0.0.1:52773/isik5/fhir/r4            # the round's endpoint
A='-u <api-user>:<api-password>'                  # provenance/CREDENTIALS.md
# 1. valid base R4, invalid against ISiKPatient (no identifier, no name)
curl -s -o /dev/null -w '%{http_code}\n' $A -H 'Content-Type: application/fhir+json' \
  -d '{"resourceType":"Patient","gender":"other"}' "$B/Patient"
# 2. the same body with an element base R4 itself does not know
curl -s $A -H 'Content-Type: application/fhir+json' \
  -d '{"resourceType":"Patient","gender":"other","notAnElement":"x"}' "$B/Patient"
```

`2xx` on (1) with `4xx` on (2) is the asymmetry: the write path enforced base R4
and not the endpoint's profiles. Record the two status codes, the endpoint's
`ResourceValidation` setting and the build string beside the round, delete the
resources the probe created, and say which build was measured. A measurement from
an earlier build must not be reported as a property of a later one.

**The IRIS locus column is different in kind from everything else in the report,
and says so.** No artefact in this repository maps a FHIR feature to an IRIS class:
those loci are authored by this project — the subsystems a developer would open —
and `index.json` carries that provenance statement, which the report reproduces
verbatim in §3. Verify against the product source before acting on one.

### Category resolution: six sources, increasing evidence

1. the step's shape — what kind of assertion this is;
2. the request context — what the assertion was made *about*;
3. the HTTP response — what the SUT actually did;
4. **the assertion message** — what the SUT did when (3) is unavailable;
5. the seed records — whether our own data was already broken;
6. **the staged corpus** — whether the rejected content is the content we sent.

Each overrides the previous **only when it knows strictly more**, and every entry
states which one decided it. Only (5) and (6) can turn an IRIS gap into a
test-data defect, and each needs positive evidence — from `provenance/seed/` and
`provenance/corpus/` respectively — to do so. Absent that evidence the failure
stays attributed to the SUT, because quietly excusing a failure is the one error
this report must not make.

**(6) exists because (3) and (5) are both blind to the corpus-authoring class.**
When the suite validates a resource against a profile and the reference validator
reports `Value is 'X' but must be 'Y'`, the question "is this the product's
fault?" has exactly one honest answer: it is the product's fault only if the
product returned something other than what it was given. (3) cannot answer it —
these are Tiger TGR steps, so `failure.http` is `null` on every one of them. (5)
cannot answer it either — the pre-run `$validate` is *the SUT's own* validator,
and a build that does not enforce the profile reports no errors on data that
violates it, which is precisely the condition under test. So the parser joins the
rejected element path against `provenance/corpus/<Level>/`, the FHIR instances
this round actually rendered and seeded, matched on resource type and level. The
re-attribution fires only when the staged instance carries the rejected value
*and* no staged resource of that type carries the required one; the resulting
entry prints both columns so an inspector can check the join. A message form the
parser does not recognise, or a corpus directory absent from the bundle, leaves
the failure attributed to the SUT.

**(4) exists because (3) is structurally absent for a whole class of step.** Tiger
issues `TGR send …` requests through its own proxy, so Serenity records no
`restQuery` for them and `failure.http` is `null` on every one — measured: no
`restQuery` anywhere in the report of a TGR-only scenario. The status the SUT
actually returned is then only in the assertion's own message, in AssertJ's fixed
`Element value:\n<actual>\nExpected:\n<regex>` form. Parsing it recovered all 17
`$.responseCode` failures of the reference round, which had otherwise reached the
report uncategorised. `expected` is a *regex*, so the rules match on the class of
status the suite demanded (`20\d`, `4\d\d`, `422`) rather than on one literal code,
and a message form the parser does not recognise stays `UNCLASSIFIED` rather than
being classified on a guess. The rule table is `index.json`'s `responseCodeRules`
and `responseBodyRules`, next to `statusRules`, so an inspector can check the
reasoning without reading the generator.

### Deduplication, and the number that matters

Around 1400 test cases share a small number of root causes: one missing search
parameter fails a whole Scenario Outline, one missing operation fails every
scenario in its feature. Entries are collapsed by root cause and each carries the
full list of affected scenarios (capped at 12 with "… and N more"). Without this
the report would run to hundreds of near-identical entries and be useless as the
one thing it has to be — a work list.

So §1 reports two numbers and a ratio: *failing cases attributable to the SUT* →
*distinct defects*, with the deduplication factor. **The count that matters for
planning is the defect count, not the failure count.** `80-gap-report.sh` prints
the same figures on the terminal, read out of `gap-analysis.json` rather than
recomputed, so the summary line and the document cannot disagree.

### Test-data defects are segregated

A failure caused by this project's own seeded data is not an IRIS gap. Those are
proven from `provenance/seed/<Level>.json` — the pre-run `$validate` and read-back
records, written *before* the run — moved into a clearly marked appendix (§5), and
excluded from the gap count, so the headline number cannot be inflated by harness
defects. Each entry in the appendix quotes the pre-run evidence on the specific
resource.

### The feature-scoped-response coupling

Tiger's "current response" is scoped to the *feature*, not the scenario. The
generator walks Serenity's steps in the suite's own order, remembers the last
exchange seen, and marks an entry `downstreamOf` when the exchange it quotes was
produced by an earlier scenario — naming that scenario and its line. **An entry so
marked should be diagnosed by fixing its antecedent first**, since it may be a
consequence rather than an independent finding. See
[04-testtool.md](04-testtool.md#a-coupling-worth-knowing-about).

### §6 Reconciliation

Every failing case in the official report must produce exactly one categorised
finding. The section states each figure — failing cases in the official report,
failure records analysed, categorised findings, of which test-data defects,
SUT-attributable cases, distinct defects after deduplication — and whether it
balances. **If it does not balance, the report is incomplete and must not be used
as a work list**, and it says so.

Two health sub-sections appear when relevant. *Attribution index health* reports
step-shape drift: each shape in the classification table records how often it
occurs in 2.7.0, so a version bump that renames or drops a shape shows up as a
mismatch rather than as silence, and unrecognised shapes are listed by name so the
table can be extended. *Notes recorded while building the index* carries findings
like the scheme-inconsistent `Bundle` canonical
([05-testdata.md](05-testdata.md#4-two-scheme-inconsistent-canonicals)).

`--strict` makes an unbalanced or uncategorised analysis exit non-zero. Without it
the exit status is always 0 — including when every test in the round failed,
because a conformance gap is the expected result of this project and not a harness
error. The document is written either way.

### §8 — the package loader, which is not a conformance finding

The one section of the gap report that is not about the test run. Loading the
reference validator's own package set into IRIS required four dependency
declarations to be rewritten and one snapshot-only package to be loaded, because
`HS.FHIRMeta.Load.NpmLoader` resolves every declared dependency by exact package id
and aborts the whole import on the first one it cannot resolve — after the namespace
has been created. The mechanism and how each case was established are in
[03-sut-iris.md](03-sut-iris.md#unresolvable-dependency-declarations).

It appears in *this* report and not only in `results.md` because two of the four
findings are IRIS's own behaviour, and this report's audience is the only one that
can act on them: no semver range resolution, and an abort that names the
unresolvable id but not the package that declared it. §8 says what a fix would look
like, since the report is a work list. The other two rows are named specifically so
nobody spends time on them — one is an upstream IG defect, one is this project's own
exclusion.

**It is excluded from the §1 gap count, and from `gaps` in `gap-analysis.json`**
(it is carried as `packageLoaderFindings` instead). Counting an installation-time
limitation as a conformance failure would inflate the headline figure, and it would
bury a finding whose owner and whose fix are different from every entry in §4.

### §2 Coverage — why it comes before the gaps

A gap analysis is only as complete as the run behind it. §2 states cases executed
against cases expected in scope, passed, failed, and features that produced no
result at all, per level. When a level is short of its expected count, a block
quote says so explicitly and points at `results.md` §7: **defects present only in
the unexecuted features are absent from the report**, and an incomplete run must
not be mistaken for a clean one.

## `CASE-EVIDENCE.md`

The annex of the gap analysis, written by `gap-analysis/case_evidence.py` through
`scripts/81-case-evidence.sh`, which `make report` runs after `make gap-report`.

### Why the deduplication needs an annex

Deduplication is what makes `GAP-ANALYSIS.md` a usable work list and is also what
it costs: an entry names its affected scenarios without showing any of them, so it
carries no request body, no response and no way to replay a case. That is the right
document for deciding *what* to fix and the wrong one for *fixing* it. The annex is
the other half, one clause per failing case rather than per defect, answering the
four questions a developer asks at the keyboard — what input the case used, what
output it expected, what output it received, and how to reproduce it.

Both documents are generated from the same `analysis/results.json` and read the
same `gap-analysis.json`, and the annex imports the category titles and the
non-SUT category set from `gap_report.py` rather than restating them, so the two
cannot come to disagree about which cases exist or what a case was attributed to.

### Where each clause comes from

Every clause is a join over three files of the round's own bundle — which cases
failed and where each outcome record lies (`analysis/results.json`), what the
failure was attributed to (`gap-analysis.json`), and the suite's own record of the
exchange (`official/test-report.zip` and `official/debug-report.zip`). The zips are
read directly, so no extraction has to be present and the annex can be generated on
a machine that has never run the suite; where an `official/extracted/` tree is
present it is used in preference, because that is the tree the citations in
`results.md` name. The output is byte-identical either way — generated from the zips
on one host and from an extraction on another, the four files of rounds I and K
matched by sha256.

**Both archives have to be there.** The exchange exists only in
`debug-report.zip`, which the slim archives in `archives/` deliberately do not
carry. Run against a slim bundle, the generator produces a complete document in
which every clause states its input, its expectation and the recorded error and
no clause quotes a request or a response; §1 of that document says so, and §5
separates those cases from the ones whose own traffic log is genuinely empty.
Measured on round K's slim archive on 2026-11-12: 41 clauses, 0 exchanges. The
annex therefore has to be generated where the full bundle is, or from one that
was archived with its debug archives.

A Scenario Outline writes one outcome record for all of its examples, so the record
is first narrowed to the example's own step subtree by the label the suite gave it.
Within that subtree the exchange the assertion was made about is the last response
in the attached RBelLog together with the request before it. Responses the suite
rendered as hex because it did not parse them — which is every gzip-encoded
response — are unhexed and inflated, so the body shown is the body the SUT sent.
Headers are reproduced as they arrived, original casing included.

### What is withheld, and what is not

The `Authorization: Basic` value, and only that. This laboratory's credentials are
generated per deployment and documented on purpose in `provenance/CREDENTIALS.md`
and in §1 of `results.md`, so withholding them protects nothing — but this is the
document that gets forwarded to a product organisation, and a document that travels
should not carry a credential in copy-pasteable form. The replay commands name
`$SUT_USER` / `$SUT_PASSWORD` instead. The suite's own unresolved placeholder token
is **exempt** from the withholding, because for the SMART-on-FHIR cases that
placeholder reaching the endpoint is the evidence the finding rests on.

Request bodies are verbatim. Response bodies are capped (default 20 000
characters), and a capped body records the sha256 of the *complete* body, its
character count and a truncation flag, so a reader can prove that what they have is
the body the archive holds. The cap exists because the CapabilityStatement of this
SUT is near a megabyte and appears in many cases; without it the JSON was 9.4 MB
for one round.

### Cases with no exchange

The feature-scoped-response coupling has a visible consequence here. A case whose
assertion is made about a response obtained in an *earlier scenario* of the same
feature has no exchange of its own: its own traffic log records zero messages.
Those cases are reported in §5 by name, with the reason, rather than dropped — four
of round K's 41 failures are of this class.

§5 keeps three circumstances apart, because they are not equivalent and only the
first is a statement about the run: the traffic log is **present and empty**, which
is the case above; the official report **attaches no log** to the case at all; or
the log the case names is **not in the bundle**, which is what a missing
`debug-report.zip` looks like and is a property of the bundle rather than of the
case. `--strict` makes the presence of any of them a non-zero exit for a caller who
wants to be told; without it the exit status is always 0, because a conformance gap
is the expected result of this project.

### Diffing two rounds

Entries are keyed and ordered by level, module, feature file, feature line and
example index — never by a sequence number — so an unchanged case is byte-identical
between two rounds' documents apart from the per-run values a response carries
anyway (`Date`, `Content-Length`, generated resource ids). Pass the same
`--generated-at` to both and the timestamp does not become the first difference.
This is why the target is chained into `make report` rather than offered on demand:
an annex that exists for one round and not for the round before it cannot be
diffed, which is most of what it is for.

## `comparison-vs-<run-id>.json`

A further document, for a further reader, written only when asked for:

```sh
make compare BASELINE=run-20260807T121756Z CANDIDATE=run-20261111T121705Z
```

A round answers *is the product conformant*. A release decision turns on the
other question — *has anything that used to work stopped working* — and that
answer is in neither round's archives, because it is a join over both. Hence a
generator and an artefact of its own, which lands in the candidate's bundle and
in its manifest, so that archiving the round archives the comparison with it.

Each joined case is classified exactly once as `STABLE`, `FIXED`, `REGRESSION`,
`PERSISTING` or `PRECONDITION`, and all five counts are always written, zeros
included: *0 regressions* is the sentence the document exists to support and must
not have to be deduced from a missing key. Preconditions are separated because
the suite uses them to establish state, so their verdicts say nothing about
conformance. `REGRESSION` is the only class that is a finding about the product;
the other counts are what let a reader see that the two rounds are comparable at
all.

Within one suite version cases are joined on their exact address — level,
feature file, scenario line, Examples index. Across two suite versions the lines
move and tables are edited, so `--join auto` drops the line and matches Scenario
Outline rows **by their parameter bindings rather than by their index**: an index
join would compare each row below an inserted one against a different test and
manufacture regressions. Cases the weaker key cannot pair appear in
`onlyInBaseline` / `onlyInCandidate`, which is how a reader sees what a
cross-version comparison does not cover. Only levels both rounds ran are
compared, and `levels` records which those were.

Like the gap analysis, it is a pure join over archived evidence — no container,
no network, no licence key — so two slim archives are enough to re-derive it
anywhere. The document names its two inputs, their run ids and its `generator`,
and carries no timestamp, so the same two bundles regenerate it byte for byte and
a reader who disbelieves a figure can find out what produced it. Exit status is
about the comparison and not about the SUT: a round
that regressed in fifty places exits 0 and says so, unless the caller asks for
`--fail-on-regression`.

## Regenerating

```sh
make report RUN_ID=run-20260806T093000Z        # extraction + results + citations + manifest
                                               # + gap report + case evidence
make gap-report RUN_ID=run-20260806T093000Z    # the gap analysis alone
make case-evidence RUN_ID=run-20260806T093000Z # its per-case annex alone
make compare BASELINE=run-20260807T121756Z CANDIDATE=run-20260806T093000Z
```

None of them re-runs the tests and none of them touches the archives. All are pure
functions of the bundle plus `vendor/requirements.json` (results) or
`gap-analysis/index.json` (gaps), so a bundle can be copied to another machine and
its derived documents regenerated and diffed there. The annex needs neither index:
it reads the bundle and `gap-analysis.json` only.

`80-gap-report.sh` passes any further arguments through to `gap_report.py`, so
`--generated-at`, `--out-md`, `--out-json` and `--index` are available for diffing
two rounds without the timestamp making every line differ. When `--out-md` or
`--out-json` redirects the output, the manifest step is skipped — there is nothing
at the default paths to hash. `81-case-evidence.sh` behaves the same way and
additionally accepts `--max-body` and `--strict`.

Both wrappers append their two files to `analysis/MANIFEST.txt`, which
`70-collect-report.sh` wrote before them, dropping any previous entry for the same
path first and re-sorting — so re-generating updates the hashes instead of
accumulating stale ones, and an inspector running `sha256sum -c` does not find two
unlisted files at the bundle root, which is what tampering would look like.
