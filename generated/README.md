# `generated/`

Version-labelled snapshots of artefacts the rig **generates**, one directory per
test suite version: `generated/testsuite-<version>/`.

They exist so that a reader can inspect what the rig produces, and a new operator
can diff their own render against ours, without first standing up a deployment.
Each directory carries its own `README.md` stating when and by what it was
generated, and a `SHA256SUMS` over every file beside it. `make check` verifies
those checksums.

**Nothing here is read by the rig.** The live artefacts are regenerated per
deployment at their working paths (`build/fhir/`, `gap-analysis/index.json`), which
stay untracked for the reason given in [.gitignore](../.gitignore): they are
derived from whichever suite version a deployment pins, and a tracked copy at the
live path would eventually disagree with the deployment silently. A copy under a
path that *names* the version it belongs to cannot be mistaken that way, which is
why this directory is the deliberate exception rather than a contradiction.

A snapshot is **not** a result. It is input the rig fabricates for the system under
test, plus the attribution index the analysis joins against — never a measurement
of anybody's product. Measurements are not tracked in this repository at all; see
[what this repository does not contain](../README.md#what-this-repository-does-not-contain).

## Adding a snapshot for a new suite version

```sh
# build/fhir/ is rendered by `make seed`, which needs the SUT up; a finished
# round (`make all`) has therefore already produced it for the pinned version.
make packages up seed
mkdir -p generated/testsuite-<version>/fhir
cp -r build/fhir/<Level> generated/testsuite-<version>/fhir/
cd generated/testsuite-<version>
find . -type f ! -name SHA256SUMS | sed 's|^\./||' | LC_ALL=C sort \
  | xargs sha256sum >SHA256SUMS
```

Then write the directory's `README.md`: the suite image and **digest** the corpus
was rendered against, the date, the script that rendered it, and anything the
snapshot does not contain. A snapshot whose provenance is not stated is not
evidence of anything.
