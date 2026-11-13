# Generated artefacts, test suite 3.0.9

A snapshot of what this rig **generates** rather than what it contains as
source, taken for one pinned test suite version. It is committed so that a
third party can check their own generated artefacts against ours, and so that
the record survives the test suite image moving on. It is **not** an input to a
round: nothing here is read by any script, and a round regenerates all of it.

The live paths these were taken from — `build/fhir/`, `gap-analysis/index.json`
— stay excluded by `.gitignore` on purpose, and that has not changed. Tracking
them *there* would freeze one suite version's profile canonicals and resource
IDs into the working tree, where the next `make packages` would silently
contradict them. Tracking a copy *here*, under a path that names the version it
belongs to, has the opposite property: it can only ever be read as "this is what
3.0.9 produced".

## Provenance

| | |
|---|---|
| Test suite | `gematik1/isik-testsuite:3.0.9` |
| Suite image digest | `sha256:7e15822f06c2afed9f4211dfab24ca9ebaa98ca03285c1286d379429ffcdcf00` |
| FHIR corpus rendered | 2026-11-11, by `scripts/50-seed.sh` → `scripts/lib/build_testdata.py` |
| Attribution index generated | 2026-11-12T07:57:33Z, by `gap-analysis/build_index.py` |
| Level | Stufe 5 only — suite 3.x carries no Stufe 3 (`docs/en/04-testtool.md`) |
| FHIR package set it was rendered against | the 22 packages the 3.0.9 package plan installs into namespace `ISIK5` |

## Contents

### `fhir/Stufe5/` — 129 resources

The rendered FHIR corpus: the resources that must already exist in the system
under test before the suite runs, one file per resource, named
`<Type>-<id>.json`.

These are a **render**, not a source. The sources are tracked, in
`testdata/fhir/{common,Stufe5,extra}` plus the per-level YAML contracts in
`testdata/Stufe5/`; `build_testdata.py` resolves them into these files by
stamping `meta.profile` from `vendor/requirements.json` and substituting
`{{<ns>.<key>}}` references from the suite's own key→ID contract. Read that
script's header for why the indirection exists — in short, 40 resources are
required by both levels under different profile canonicals, and maintaining two
hand-written copies of each would make the first drift look like a product
defect.

The render is therefore a function of three things, only one of which is in this
repository: the authored sources (tracked), the suite version's ID contract and
the resolved FHIR package set (both from the suite image and the package
registries). That is why the output is worth snapshotting — the inputs are not
all ours to pin.

### `gap-analysis-index.json` — the attribution index

The index `gap-analysis/gap_report.py` joins a failure against to name the FHIR
feature, the supplying package and the IRIS subsystem a developer would open.
It carries its own provenance block, including the suite digest above.

Two things to know before reading it:

- **It records the absolute paths of the host it was generated on**
  (`/home/azureuser/isik/vendor/...`). Those are kept verbatim. Rewriting a
  generated provenance record to look tidier would make it a worse record, and
  the paths disclose nothing beyond a default account name.
- Its `irisLociProvenance` field says so itself: the IRIS subsystem names in it
  were **authored by this project**, not derived from any artefact. They are
  where to look, not what is wrong. Verify against the product source before
  acting on one.

## What is deliberately not here

**The generated requirement documents for 3.0.9.**
`docs/en/generated/REQUIREMENTS-Stufe5.md` and `RESOURCE-INVENTORY.md` are
produced by `scripts/25-extract-requirements.sh` from the pinned suite's feature
files. The copies that exist are from an earlier suite version and would
misdescribe 3.0.9, so none are included rather than a wrong one. They are fully
regenerable and cost nothing:

```sh
make packages        # resolve -> fetch packages -> extract requirements
ls docs/en/generated/
```

**Anything measured.** No round bundle, report, log or verdict is in this
repository, by standing rule. The machinery to produce them is; the measuring is
the reader's to do. See the "What this repository does not contain" section of
the top-level `README.md`.

## Reproducing this snapshot

```sh
cp .env.example .env            # set TESTSUITE_VERSION=3.0.9, MODE as required
make packages                   # needs the suite image; see kit/README.md
make up
make seed                       # renders build/fhir/ and loads it

diff -r generated/testsuite-3.0.9/fhir/Stufe5 build/fhir/Stufe5
```

An empty diff means your render agrees with ours, and any conformance
difference you then measure is not a difference in the test data. A non-empty
diff is worth understanding **before** taking a round: the likely causes, in
order, are a different resolved FHIR package set (`PACKAGE_SOURCE`, and the
registries' current contents), a different suite patch version, and a local edit
under `testdata/`.

`SHA256SUMS` covers every file in this directory except itself:

```sh
cd generated/testsuite-3.0.9 && sha256sum -c SHA256SUMS
```
