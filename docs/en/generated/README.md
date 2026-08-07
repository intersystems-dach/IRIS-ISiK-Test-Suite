# Generated documentation

This directory is **not** tracked by git (except this file). Its contents are
derived from the feature files of the test suite version *this deployment pinned*,
and are rewritten by:

```
make packages          # or, directly: scripts/25-extract-requirements.sh
```

| File | What it is |
|---|---|
| `REQUIREMENTS-Stufe3.md` | Per-module test-data requirements for ISiK Stufe 3: every feature's `@vorbedingung` prose quoted verbatim, its `${data.*}` keys with the IDs the suite's own contract assigns them, the profile its responses must conform to, and the test cases it depends on. |
| `REQUIREMENTS-Stufe5.md` | The same for ISiK Stufe 5. |
| `RESOURCE-INVENTORY.md` | The fabrication work list: one row per resource that must exist in the SUT before a run, with its FHIR resource type, ID, required profile, and the features that need it. Plus the residue that needs a human decision — keys whose type is stated only in prose, keys referenced but never defined, keys defined but never used. |

A companion machine-readable artefact goes to `vendor/requirements.json`; it is
read by `50-seed.sh` (which refuses to seed a key the suite does not define) and
by the gap report (which quotes the precondition prose when a scenario fails).
It lives under `vendor/` rather than here because it is a build artefact, not a
document.

## Why these are generated and not written

The ISiK test suite seeds nothing. Every scenario reads resources that must
already exist in the system under test, addressed by IDs the suite supplies
through `${data.<key>}` configuration keys. The suite ships the *keys* — 11 YAML
files, 287 keys across both levels — but states the *content* those resources must
have only as German or English prose inside each feature file's `@Precondition` /
`@vorbedingung` scenario. There are 221 such blocks across 220 feature files.

That prose is the authoritative field-level specification for our test data.
Reading it by hand and hoping nothing was missed is not a method that survives an
audit, and a missed field produces a conformance failure that looks exactly like
an IRIS gap — the one error this exercise must not make. So the extraction is
mechanical and repeatable, and the prose is carried through **verbatim and
untranslated**: paraphrasing would put an interpretation between the
specification and the data.

## Why they are not committed

The requirement tables are only meaningful relative to one `TESTSUITE_VERSION`.
A committed copy would keep rendering the version that happened to be pinned when
someone last ran `make packages` on their machine, and would then disagree —
silently — with a round that pinned a different version. Since these documents
*are* the test-data specification, a stale copy is worse than no copy.

Every generated file names its own source (image reference and digest) in its
header, so an inspector can re-pull that exact image and reproduce the quotes.
