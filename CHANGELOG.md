# Changelog

Versions in this file are the **rig's own**. They are independent of the IRIS
build a deployment tests and of the Gematik suite version it pins — both of those
are recorded per round in `reports/<run-id>/provenance/`, not here. The current
rig version is in [VERSION](VERSION). Dates are ISO 8601 (YYYY-MM-DD).

Scope of this file: what the tooling and the documentation do. **Results of test
rounds are deliberately absent** — see
[what this repository does not contain](README.md#what-this-repository-does-not-contain).

## 1.0.0 — 2026-11-13

First release-style milestone. The rig is intended to be handed to a third party
at this point: everything needed to reproduce a round on a user-supplied IRIS
image is tracked, and everything that is not ours to publish is documented instead
of shipped.

### Added

- **Build from an installation kit.** `make image-from-kit KIT=… SUPERVISOR_REF=…`
  ([scripts/02-build-image-from-kit.sh](scripts/02-build-image-from-kit.sh),
  [kit/Dockerfile.iris](kit/Dockerfile.iris),
  [kit/Dockerfile.webgateway](kit/Dockerfile.webgateway)) builds an IRIS for Health
  image and a matching Web Gateway image from a container installation kit, for the
  case where the build to be tested is published as a kit and not as an image. The
  kit's own metadata is read from the archive rather than supplied by hand, and the
  resulting image carries labels recording the kit file, its sha256 and the fact
  that the image was not published by InterSystems.
- **[kit/README.md](kit/README.md)** — the three supported ways of obtaining the
  unit under test (pull from `containers.intersystems.com`, side-load an image file
  the operator already holds, build from a kit from `downloads.intersystems.com`),
  what a round on each may claim, and the three permitted provenance sources for
  the installer parameters a kit build has to supply.
- **[generated/testsuite-3.0.9/](generated/testsuite-3.0.9/)** — a version-labelled
  snapshot of the rendered Stufe 5 test corpus and the gap-analysis index for suite
  3.0.9, with `SHA256SUMS`, so a reader can inspect and diff the corpus without
  first standing up a deployment. Read by nothing: the live render at `build/` is
  still generated per deployment and still untracked, for the reason given in
  [.gitignore](.gitignore).
- **Release-hygiene checks** in [scripts/99-check.sh](scripts/99-check.sh)
  (section 8, `make check`). Enforced against the git index: no tracked archives,
  licence keys or private keys; no tracked round bundles or generator output; the
  per-deployment paths still ignored; no tracked file above 1 MiB outside
  `generated/`; every `generated/*/SHA256SUMS` verifies; and the example
  configuration pointing only at private-range addresses.
- **Licence-key expectations** in [secrets/README.md](secrets/README.md) — what the
  file is, and the four properties of a key (product, platform family, capacity,
  expiry) that have to match the round. No key is distributed with this repository
  and none is required to read it.

### Changed

- The documentation no longer states conformance figures for any IRIS build.
  Measured counts of the *suite* (executed, asserting and precondition-only cases)
  remain, now labelled with the suite version they were counted on, because they
  are properties of the test suite and are what a denominator has to be chosen
  from. Round citations remain only where they evidence that a defect in this rig
  was real and was fixed.
- `.env.example` and `README.md` no longer name a particular IRIS build or a fixed
  package count as if they were the rig's subject. The rig resolves both from
  whatever the deployment pins.
- [INSTALL.md](INSTALL.md) §5 covers the kit route alongside the registry login and
  side-loading.

### Fixed

- `docs/en/06-execution.md` and `docs/de/01-uebersicht.md` disagreed on the
  direction of the error introduced by computing a success rate over all executed
  cases rather than over the asserting ones, and neither stated a derivation. Both
  now state the provable form: the precondition-only blocks assert nothing and pass
  unconditionally, so a rate over the executed count has an arithmetic floor and is
  therefore too favourable. The asserting count is the only admissible denominator.

### Not included, by rule

IRIS images and kits, the Gematik test suite image, any credential or licence key,
and the working data or reports of test rounds. Each is documented with the
official way to obtain it.

Only the credentials are excluded because they are sensitive. The rest are
excluded because they are **out of scope**: this repository is the instrument, and
a product image is not ours to distribute while a measurement is the operator's to
take. Our own earlier rounds are not confidential, and the commits before this
milestone that carry them are left in place rather than rewritten — they are a
record of one round on one build at one date, not a statement about the build
anybody else is about to test.

## Before 1.0.0

Development history from 2026-08-07, in `git log`. It is not reconstructed here:
those commits predate the scope rule above and several carry round data, so a
changelog entry summarising them would describe a repository this one no longer is.
