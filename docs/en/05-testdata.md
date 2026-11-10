# 5. Test data

## The problem

**The ISiK test suite seeds nothing.** Every scenario reads resources that must
already exist in the system under test, at exactly the IDs the suite's own
`testdata/**.yaml` contract names. The suite ships the *keys* — 11 YAML files,
287 keys across both levels — but states the *content* those resources must have
only as German or English prose inside each feature file's `@Precondition` /
`@vorbedingung` scenario. There are 221 such blocks across 220 feature files, 186
of them in the default run scope.

Fabricating that corpus is therefore a precondition of the entire conformance
run, and its correctness is load-bearing in one specific way:

> A missing or non-conformant seeded resource produces a failing scenario that is
> **indistinguishable, in the official report, from an IRIS conformance gap.**

Avoiding that misattribution is what most of the machinery in this section is
for. Everything else follows from it: the extraction is mechanical rather than
hand-read, the corpus is checked three ways before a container is started, and
`$validate` runs against IRIS's own validator before every round.

## The pipeline

```
pinned image  ──25-extract-requirements.sh──▶  vendor/requirements.json
  features/                                    docs/en/generated/REQUIREMENTS-*.md
  testdata/                                    docs/en/generated/RESOURCE-INVENTORY.md
                                                        │
testdata/fhir/{common,Stufe3,Stufe5,extra}/  ──build_testdata.py──▶  build/fhir/<level>/
                                                        │
                          assertion_check.py ───────────┤  (host, no container)
                     search_reference_check.py ─────────┤
                                                        ▼
                                        seed_client.py ──▶ both endpoints
                                                        ▼
                                             run/seed/<Level>.json + SEED.txt
```

### Extraction

`scripts/25-extract-requirements.sh` → `scripts/lib/extract_requirements.py`
parses the feature files *taken out of the pinned image* — not from a separately
downloaded tarball, so the requirements provably come from the release that will
run — and produces:

| Artefact | Contents |
|---|---|
| `vendor/requirements.json` | The machine-readable contract: per level, per feature, the `${data.*}` keys with the IDs the suite assigns them, the asserted profiles, every extracted assertion, and the resource inventory |
| `docs/en/generated/REQUIREMENTS-Stufe3.md`, `-Stufe5.md` | Per-module requirement tables with each `@vorbedingung` block's prose quoted **verbatim and untranslated** |
| `docs/en/generated/RESOURCE-INVENTORY.md` | The fabrication work list: one row per resource, with type, ID, required profile and the features needing it — plus the residue that needs a human decision |

The prose is carried through untranslated on purpose: it is the authoritative
field-level specification, and paraphrasing it would put an interpretation
between the specification and the data. That is the same reason ISiK is published
in German only.

The generated documents are **not committed** — see
[docs/en/generated/README.md](generated/README.md). They are meaningful only
relative to one `TESTSUITE_VERSION`, and since they *are* the test-data
specification, a stale copy is worse than no copy.

Types are inferred **only** from steps that state them (`/Patient/${data.x}`,
`references resource with ID "Patient/${data.x}"`). Nothing is inferred from a
key's spelling: `account-read-patient-id` merely *looks* like a Patient. Keys with
no typed use are listed separately rather than guessed at.

Profiles are attributed the way Tiger itself attributes them: a step
`FHIR current response body is a valid <module> resource and conforms to profile
"<canonical>"` asserts against whatever the preceding `Get FHIR resource at`
fetched, so each canonical lands on exactly the resource the suite lands it on.
Name matching would not work — ISiK profile names are German, so
`ISiKAbrechnungsfall` is the Account profile and
`ISiKKontaktGesundheitseinrichtung` the Encounter one.

### Authoring

112 authored JSON files render to 58 Stufe 3 and 73 Stufe 5 resources:

```
testdata/fhir/common/<Type>-<id>.json          level-neutral body, no meta.profile
testdata/fhir/Stufe3/<Type>-<id>.json          per-level override (complete body)
testdata/fhir/Stufe5/<Type>-<id>.json
testdata/fhir/extra/<level>/<Type>-<id>.json   required, but named by no data key
build/fhir/<level>/<Type>-<id>.json            rendered output (gitignored)
```

There is a build step because 40 of the 84 distinct `(type, id)` pairs are
required by *both* levels and 37 of those need the same content. What differs is
the profile canonical: ISiK 3 lives under
`https://gematik.de/fhir/isik/v3/<Modul>/StructureDefinition/`, ISiK 5 under
`https://gematik.de/fhir/isik/StructureDefinition/`. Authoring 40 pairs of files
kept in sync by hand would mean that the first time they drifted, the difference
would surface as a conformance failure in one level only — which reads exactly
like an IRIS gap.

`build_testdata.py` resolves three things per level:

* **`meta.profile`** — stamped from `vendor/requirements.json`, i.e. from the
  profile the feature files actually assert on that resource in that level. Never
  hand-typed. A wrong profile claim would make the reference validator check the
  instance against the wrong StructureDefinition, and the resulting failure would
  be ours while looking like the SUT's.
* **`{{data.<key>}}`** — replaced with the ID *that level's* contract assigns to
  the key. References are written this way rather than as literal IDs so a shared
  body stays correct where the two levels assign different IDs to the same role.
  An unresolvable key is a hard error, never a placeholder shipped into a request.
* **`{{profile.<Name>}}`** — resolved to the canonical URL that this level's
  package set defines for that profile name, by reading the StructureDefinitions
  themselves rather than assembling a URL from a pattern. Needed for the 9
  inventory rows no assertion covers (a resource that is only a reference target,
  or is deleted, is never validated by the suite, so there is no canonical to
  stamp). The claim is worth making rather than omitting, because IRIS `$validate`
  runs over the seed corpus before every round — so a profile claim becomes a real
  check of our own authoring.

Where the levels genuinely disagree — `CodeSystem-Read-Example.name` is
`testkatalog` in Stufe 3 and `Test Catalog` in Stufe 5 — a full body under
`Stufe3/` or `Stufe5/` overrides `common/` for that level only.

**`extra/` is separate on purpose.** Those bodies have materially weaker
provenance: every `common/` and `Stufe*/` file traces to an inventory row derived
from a feature assertion, whereas an `extra/` file traces to a human reading a
search expression. Stufe 5's `Appointment-Book-By-Schedule` is the case in 2.7.0 —
the *Schedule* is named by a key and is therefore in the inventory, but the free
`Slot` inside it is named nowhere, and the scenario fails without it. Left
unseeded that reads as an IRIS search defect rather than as absent test data.
Keeping these apart means the inventory count still reconciles against the
extractor, and a reviewer can see at a glance which resources exist on our
judgement. Each carries a `_provenance` comment naming the scenario that requires
it; the key is stripped before the file is written, since it is not valid FHIR.

### Fabrication, for the modules release 3.0.0 added

The section above describes the authored corpus, which is how every body for test
suite 2.x came to exist. Release 3.0.0 added four Level 5 modules — Connect, ICU
Minimal, ICU Extended and Labor — whose contracts name 56 resources this repository
had no body for, to be written against 132 `StructureDefinition`s. Hand-writing
them would have made the rig's own test data the most likely cause of a failure in
a round whose entire product is a statement about the SUT, so the 3.x corpus under
`testdata/fhir/v3/Stufe5/` is *fabricated from the specification* by two tools, run
in sequence, neither of which invents anything.

**`derive_testdata.py` — the body comes from the specification's own examples.**
`de.gematik.isik#5.1.3`, the package the 3.0.9 validator plugin validates against,
ships 199 example instances under `examples/`. For 52 of the 56 resources exactly
one of them claims a profile that `vendor/requirements.json` records as asserted for
that resource, so the clinical content is the specification's. The match must be
unique: no candidate and two candidates are both reported and skipped, because a
choice between two examples is not one the feature files determine. Three things
are changed and no others — `id`, `meta` and `text` are dropped (the renderer stamps
the first two from `requirements.json`; the third is generated narrative carrying the
example's own ids), and every `Reference.reference` is rewritten to the contract
placeholder for *its location in the body*, from a table, never from the value it
happens to hold. A location the table does not cover is a hard error naming the
path, because leaving a package example's id in place would seed a dangling
reference and the suite would report it as a SUT defect. Earlier packages ship no
examples at all — `de.gematik.isik#5.1.2`, used by suite 2.7.0, contains none — so
the tool is applicable from 5.1.3 onwards and says so rather than producing nothing.

The remaining 4 of the 56 have no example claiming an asserted profile
(`Encounter/ICUext-Department-Case-617`, `Patient/ICUext-PatientinMusterfrau-617`,
`Patient/patient-id-not-in-context`, `Practitioner/ICUextPractitionerWalterIntensiv834`)
and are authored. Three further bodies are named by no data key and carry a
`_provenance` comment naming the scenario that requires them, exactly as `extra/`
does for 2.x.

**`reconcile_testdata.py` — the body is then brought into line with the
assertions.** A published example satisfies the profile; it does not necessarily
satisfy what a feature file asserts about this resource in this scenario — a LOINC
`version`, a `display`, an `issued`, a particular `interpretation`. This tool writes
exactly what the assertions in `vendor/requirements.json` determine and reports
everything else, by name, as an item for a person. Three properties make its output
usable as evidence:

* **It writes only what is determined.** An assertion that states a value writes
  that value. An assertion that requires an element to be non-empty without stating
  its content writes nothing and is reported. Over the Stufe 5 corpus 5 assertions
  are currently reported — all on
  `MedicationStatement-MedicationStatement-Read-Rate-Example`, and all because the
  assertion names no `where` to choose among entries the body already has.
* **Where it reads the feature's prose, it cites it.** The precondition prose is
  irregular, so only six formulaic constructs are read — the display of a code, the
  unit of a UCUM code, the system of an identifier, an identifier stated outright,
  the FHIR type the prose names for a choice element, and a timestamp of which a
  step asserts a substring. A construct the resource's own features state two ways
  is discarded rather than resolved, and every value taken this way is written with
  its file and line to `testdata/fhir/v3/PROSE-FACTS-Stufe5.txt`, so each is
  auditable against the suite text without re-running the tool.
* **A display comes from a definition, not a usage.** `CodeSystem.concept.display`
  in a pinned package outranks any number of `Coding.display` occurrences in that
  package's examples; usages are consulted only for codes no shipped CodeSystem
  defines, which covers LOINC and SNOMED CT, neither of which is redistributable.
  A pair two CodeSystems display differently is left alone.

Regenerating the corpus is one command per tool over the pinned package set, and a
second reconcile pass over its own output changes nothing:

```bash
python3 scripts/lib/derive_testdata.py \
    --requirements vendor/requirements.json --level Stufe5 \
    --package 'vendor/packages/de.gematik.isik#5.1.3' \
    --contract testdata/v3/Stufe5 \
    --src testdata/fhir --src testdata/fhir/v3 \
    --out testdata/fhir/v3/Stufe5

python3 scripts/lib/reconcile_testdata.py \
    --requirements vendor/requirements.json --level Stufe5 \
    --contract testdata/v3/Stufe5 \
    $(for d in vendor/packages/*/; do printf -- '--package %s ' "$d"; done) \
    --features vendor/upstream/features \
    --prose-facts testdata/fhir/v3/PROSE-FACTS-Stufe5.txt \
    --dir testdata/fhir/v3/Stufe5
```

Both tools have produced defects of their own, four of which are recorded with their
causes in [08-known-limitations.md](08-known-limitations.md). One of the four is
worth repeating here, because it bounds what the checks in the next section prove:
`assertion_check.py` classifies a `count()` assertion as `UNVERIFIED`, so a corpus
can report every assertion satisfied while five bodies carry six components against
`component.count() = 3`. That defect was found by this tool's own list of items left
for a person, which is why that list is printed in full rather than counted.

### Checking, before any container starts

Three host-side checks run in `make check`, and the first two also in `make seed`,
so a data defect costs seconds rather than a 45-minute bring-up followed by a
misleading report. They are repeated in `make seed` because `build/` is regenerated
there, and checking the tree that was just rendered is the only check that covers
what will actually be sent.

**`assertion_check.py`** checks each authored resource against the assertions
extracted from the feature files. The suite evaluates real FHIRPath — 494
`exists()`, 455 `where()`, plus `~`, `implies`, `ofType()`, `replaceMatches()`
across 566 expressions — and implementing a subset of that grammar would produce a
checker that is right about most expressions and quietly wrong about the rest.
A false pass is precisely the failure mode being defended against, so every
assertion is reported **under the strength it received**:

| Strength | What it means |
|---|---|
| `EXACT` | A literal path (or element) and a required value, resolved against the JSON and compared. A mismatch is an error |
| `ELEMENT` | FHIRPath reduced to the root elements it names. `address.where(…).exists()` unambiguously requires `address`; predicate unverified |
| `LITERAL` | The quoted string literals in an expression must appear somewhere in the resource. Deliberately weak — says nothing about *where* — but catches a wrong code system or a typo'd code |
| `UNVERIFIED` | Nothing checkable could be derived, or the assertion is negative (`.empty()`, `not(…)`) where a present element is not evidence either way |

The `UNVERIFIED` count is printed, never hidden: it is the honest measure of how
much of the specification this check does not cover. IRIS `$validate` and the run
itself cover the rest.

**`search_reference_check.py`** covers the search-scenario reference requirements
no other check can see: a scenario that searches for resources referencing another
resource requires that reference to exist, and neither the profile nor the
assertion extractor can tell you that.

**`choice_type_check.py`** exists because of a defect this project made and had to
find the hard way. A FHIR choice element is spelled with its type appended —
`Annotation.author[x]` is `authorString` or `authorReference` in R4 JSON, **never**
`author` — and one authored body wrote the bare name. That is valid JSON, it looks
right on inspection, and the element it names simply does not exist.

The check resolves every path in the **rendered** tree against the element paths of
the loaded R4 packages (the rendered tree rather than the authored sources, so a
`${data.*}` placeholder that resolves into a choice element is covered too) and
fails `make check` on any bare choice name. The current corpus is clean: 131
resources against 7790 known element paths.

Why it is a static check rather than left to the seeder: the seeder's `$validate`
*did* catch that one instance — but only after a licensed image, a 4m40s bootstrap
and a seeding pass. And it would only have caught it in an *asserted* body. The
same misspelling in a resource that exists solely as a reference target has no
profile assertion, so `$validate` passes it, the reference silently fails to
resolve, and the scenario searching through it fails in the official report as an
IRIS search defect. That is the misattribution this project exists to avoid, and it
is worth 40 lines of Python to make it impossible.

### Seeding

`seed_client.py`, per endpoint, four passes in this order:

1. **Write** — `PUT <base>/<Type>/<id>` in the dependency order
   `seed_order.py` derives. PUT rather than POST, because the suite addresses every
   resource by the exact ID its contract names and a server-assigned ID would break
   every scenario at once. This relies on FHIR *update as create*, which is
   optional in the specification, so the CapabilityStatement is read first and what
   it says about `updateCreate` is recorded either way.
2. **Re-write** the resources written ahead of one of their own reference targets,
   making the corpus referentially complete even on an endpoint that rejects
   forward references.
3. **Read back** every resource with `GET`. A 2xx on the write is not proof: the
   resource has to be retrievable at the URL the suite will request, through the
   same read path.
4. **Validate** every authored body with `POST <base>/<Type>/$validate`. Last, so
   a validation failure cannot stop the corpus from being complete and
   inspectable. The *authored* body is submitted rather than the stored one —
   they differ only in `meta.versionId`/`meta.lastUpdated`, which no profile
   constrains, and submitting the stored one would make the check depend on the
   read-back it is meant to be independent of.

The ordering is computed from the corpus's own references rather than hardcoded:
an edge `A → B` exists whenever A's JSON contains a literal
`"reference": "<Type>/<id>"` naming another resource in the same level. A static
type ranking survives only as the tiebreaker, so that Kahn's algorithm produces a
deterministic order and two rounds' seed logs are diffable.

**One genuine cycle exists** and is reported rather than hidden. In Stufe 5:

```
Encounter-Read-In-Progress-Example → Condition-Read-Active-Example
Condition-Read-Active-Example      → Condition-Read-Resolved-Example
                                   → Encounter-Read-In-Progress-Example
Condition-Read-Resolved-Example    → Encounter-Read-In-Progress-Example
```

`Encounter.diagnosis.condition` points at a Condition and `Condition.encounter`
points back. Both directions are required by the suite's own precondition prose,
so neither reference can be dropped and no total order satisfies it. The node is
emitted anyway with `cycleBroken` naming the edges unsatisfied at the moment it is
written, which is what pass 2 re-attempts.

`$validate` is IRIS's own validator against the profiles IRIS loaded — not this
repository's opinion of its own data — which is what makes it worth having.
`SEED_VALIDATE_STRICT=1` (the default) refuses to proceed on any error-severity
issue. `information` and `warning` issues are recorded but tolerated: the IRIS
validator warns about terminology it cannot expand locally, and treating that as
a defect would block every round for reasons outside the SUT.

Seeding is idempotent — PUT-with-ID overwrites — and re-running it is the intended
way to reset the corpus after a scenario has mutated it. Several scenarios do
mutate it (`*-Update-Original-Example`, `Patient-Delete-Example`), which is why
`make test` depends on `seed` and why the two levels are seeded independently.

The record is `run/seed/<Level>.json` plus a human-readable `run/seed/SEED.txt`,
both archived into `provenance/` of the evidence bundle. `60-run-tests.sh` reads
the record and **refuses to start** if any resource was not written or not
readable.

## Contract parity

`run/testdata/` is a *copy* of `testdata/Stufe{3,5}/*.yaml`, not a mount of
`testdata/` — our tree also holds the fabricated FHIR instances under
`testdata/fhir/`, which the suite must not see, because Tiger resolves
`additionalConfigurationFiles` relative to `/app/testdata` and would try to read
them as configuration.

`30-render-config.sh` compares every staged contract file against the official
release copy and **fails the round** if any differs:

* a **MODIFIED** file is fatal — a changed value means the run tests against a
  contract that is not the certified one;
* an **ADDED** file with no upstream counterpart is fatal too, except for the two
  allow-listed `zz-upstream-key-defects.yaml` overlays.

The file-by-file result, with sha256 per file, is written to
`run/tiger/TESTDATA-PARITY.txt` and archived. The 11 upstream files total 289
lines and are byte-identical to the 2.7.0 release.

### Where the comparison baseline comes from

Not from the image. Verified against `gematik1/isik-testsuite:2.7.0`: `/app` holds
only `downloadDeps.sh`, `pom.xml`, `refv-plugins/`, `report/`, `src/` and
`target/`. There is **no `/app/testdata`**, and no `tiger-isik-stufe{3,5}.yaml`
either — both are operator-supplied at run time, which is precisely why this rig
renders the Tiger configuration from `tiger/*.tmpl` and mounts both trees in.

`20-fetch-packages.sh` therefore fetches the 11 contract files from the suite's own
release tag (`https://raw.githubusercontent.com/gematik/isik-testsuite/<version>/testdata/`),
pinned to the same `TESTSUITE_VERSION` the image is pinned to. A fetch failure is
**fatal**, because without the baseline the parity claim above cannot be made and
an inspector has no proof the contract is unmodified.

The split from the feature files is deliberate, and the reason differs per
artefact:

| Artefact | Source | Why that source |
|---|---|---|
| Feature files | the image | The gap analysis *quotes their lines* as the definition of what was executed. A separately downloaded copy would prove nothing about what the pinned image ran |
| Test-data contract | the release tag | Compared for *equality*, never quoted. Proving "our copy equals the release" is exactly what the comparison does, and the release tag is that release |
| `pom.xml` | the image | Carries the tag expressions the run actually used |
| `tiger-isik-stufe{3,5}.yaml`, `serenity.properties` | the release tag | Documentation baselines for the `[DEVIATION]` diffs; absent from the image |

The parity file records this provenance in its own header, so the reader of an
archived `TESTDATA-PARITY.txt` is not left to assume which copy was compared.

## Upstream test-suite defects found

These are defects in the pinned test suite, not in IRIS. Each is recorded here,
handled mechanically in exactly one place, and **never counted as an IRIS gap**.
None is silently healed: every workaround is annotated at its site with the
feature file and line that motivates it.

### 1. Three stray regex anchors in string-comparison steps

Three steps in 2.7.0 write a `$` *after* the closing brace of the placeholder in
the plain-string form of the assertion:

| Feature | Line | Step |
|---|---|---|
| `Stufe3/Medikation/MedicationRequest-Search.feature` | 71 | `… references resource with ID "Encounter/${data.medication-encounter-id}$"` |
| `Stufe3/Medikation/MedicationStatement-Search.feature` | 83 | `… references resource with ID "Patient/${data.medication-patient-id}$"` |
| `Stufe5/Medikation/MedicationStatement-Search.feature` | 57 | same |

In the *FHIRPath* form the `$` belongs there — it anchors a `matches()` regex. In
*this* form the value is compared as a string, so the anchor is a literal
character in the expected id, and no resource can carry it. The suite's own
authors hit this: `Stufe3/Basis/Encounter-Read-In-Progress.feature:44` is the same
typo *commented out*, replaced on line 45 by the identical step without the `$`.

Handled at `scripts/lib/search_reference_check.py:STRAY_ANCHOR`: the anchor is
stripped so the underlying data requirement is still checked — the reference
genuinely has to be there, and it is, in all three cases — and the fact is
recorded on the entry, so a run failing those three scenarios is attributed to the
test suite.

### 2. Four malformed `@Precondition:` tags

`Stufe5/Basis/Organization-Read.feature`, `Location-Read.feature`,
`Location-Room-Read.feature` and `Location-Bed-Placement-Read.feature` spell the
tag `@Precondition:`, with a colon. To Cucumber that is a *different* tag — a tag
name is `@` plus everything up to whitespace — but it changes nothing at run time,
because neither profile's tag expression mentions `@Precondition` and the blocks
execute either way (they are selected by the feature-level `@Stufe5` tag) and
their steps assert nothing.

It matters only to the extraction. Read literally, those four blocks would be
counted as **asserting test cases**, and their prose — which is the field
specification for `organization-read-id` and the three Location keys — would be
left out of the requirement tables. `feature_parser.py`'s `is_precondition`
therefore normalises a trailing colon and matches case-insensitively, covering all
four spellings; the four are listed under *Malformed precondition tags* in
`RESOURCE-INVENTORY.md`.

### 3. Undefined `${data.*}` keys

An undefined key renders as the **literal string** `${data.<key>}` in the
rendered step. In an error message that is cosmetic; in a request URL it produces
a search for that literal string, an empty bundle, and a failure whose cause is a
suite defect but whose appearance is a missing resource in the SUT.

Three are aliased, in `testdata/Stufe{3,5}/zz-upstream-key-defects.yaml`, loaded
**last** under the same `baseKey: data` so the entries are purely additive:

| Key | Level / scope | Effect if left undefined | Aliased to |
|---|---|---|---|
| `practitioner-id` | Stufe 3, `@Mandatory` — in scope | Cosmetic only. `Practitioner-Search.feature:53` uses the correct `practitioner-read-id` in the FHIRPath; only the accompanying error-message text uses this spelling, so the verdict is unaffected | `Practitioner-Read-Example` |
| `Location-read-active-id` | Stufe 5, `@Optional` — **not** in the default scope | Cosmetic only, three uses in `Location-Search.feature:43,65,76`, all inside an error-message argument | `Location-Read-Example` |
| `RiskAssessment-read-id` | Stufe 5, `@Mandatory` — in scope | **Changes a verdict.** Used in the request URL at `RiskAssessment-Search.feature:38,39`: `/RiskAssessment/?_id=${data.RiskAssessment-read-id}`. Left undefined it searches for the literal string, gets an empty bundle, and fails a mandatory scenario | `RiskAssessment-Read-Example` |

The last one deserves the careful reading it got: line 32 of that feature carries
an `@Optional` tag, but it belongs to the *second* `Examples:` block of the
preceding Scenario Outline, not to the Scenario on line 37. The scenario using the
key is mandatory and does run by default. The difference from the upstream key is
capitalisation only — `Stufe5/medikation.yaml` defines `riskassessment-read-id` in
lower case, and Tiger's configuration keys are case-sensitive, so the two do not
resolve to each other. The value is kept identical to the upstream key
deliberately: both names must denote the same seeded instance.

Every alias points at the value of an existing upstream key. **Nothing invents a
resource**; the referenced instances are the ones the upstream keys already
require, so no additional test data is needed.

**Two further undefined keys are deliberately *not* aliased,** and are listed in
the overlay so a future audit does not "fix" them:

* `documentreference-update-stored-id` — not a static test datum at all. It is
  written at runtime by
  `Stufe5/Dokumentenaustausch/DocumentReference-UpdateMetadata.feature:33`
  (`FHIR evaluate FHIRPath "id" … store first element … in variable
  "data.documentreference-update-stored-id"`) and read on the next line.
  Predefining it would be harmless but pointless; this is correct upstream
  behaviour. That feature is `@Disabled @Optional` in any case.
* `medicationadministration-read-request-id` — used by
  `Stufe5/Medikation/MedicationAdministration-Read-Request.feature:18-19`, which is
  tagged `@Disabled` and `@Optional`, has its `@Medikation` tag commented out, and
  whose `@Precondition` scenario reads literally "TODO". An unfinished upstream
  stub that never executes: there is no defined resource for it to point at and
  nothing to seed.

### 4. Two scheme-inconsistent canonicals

The feature files assert profile `https://hl7.org/fhir/StructureDefinition/Bundle`
while the packages declare it as `http://hl7.org/fhir/StructureDefinition/Bundle`
(`hl7.fhir.r4.core@4.0.1`). The gap-analysis index matches on the
scheme-insensitive canonical and records the fact in `index.json` under `notes`,
rather than reporting every Bundle assertion as an unattributable profile.

### 5. Residue that is not a defect

`RESOURCE-INVENTORY.md` also lists, per level:

* **ID-shaped keys with no typed use** (2 across both levels) — the key names a
  resource but no step states its FHIR type, so the type has to be read out of the
  precondition prose. Listed so it is consciously resolved rather than silently
  missing.
* **Keys defined but never referenced** (1 in Stufe 3, 31 in Stufe 5) — defined in
  the suite's own test data, used by no feature file in this release. No resource
  needs to be fabricated for them. This is normal churn in a specification suite,
  not a defect.

## Scope, measured

Numbers below come from `vendor/requirements.json`, i.e. from the pinned image's
own feature files. They are what the results document reconciles against.

| | Stufe 3 | Stufe 5 | Total |
|---|---|---|---|
| Feature files | 96 | 124 | 220 |
| …in the default scope | 74 | 111 | 185 |
| **Test cases executed** | **506** | **895** | **1401** |
| …of which asserting | 432 | 783 | 1215 |
| …of which precondition blocks | 74 | 112 | 186 |
| `${data.*}` keys defined | 97 | 190 | 287 |
| Resources in the inventory | 56 | 70 | — |

A `Scenario Outline` counts one case per `Examples` **row**, which is how Cucumber
and therefore Serenity count — and the reason these figures exceed the
scenario-keyword count. The 186 precondition blocks execute but assert nothing;
see [06-execution.md](06-execution.md) for why both denominators are reported.

Per module, executed / asserting:

| Module | Stufe 3 | Stufe 5 |
|---|---|---|
| Basis | 181 / 157 | 293 / 257 |
| Dokumentenaustausch | 41 / 37 | 31 / 28 |
| Medikation | 165 / 139 | 323 / 278 |
| Terminplanung | 69 / 59 | 144 / 129 |
| Vitalparameter | 47 / 38 | 104 / 91 |
| Shared | 3 / 2 | — |
