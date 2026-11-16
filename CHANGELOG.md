# Changelog

Versions in this file are the **rig's own**. They are independent of the IRIS
build a deployment tests and of the Gematik suite version it pins — both of those
are recorded per round in `reports/<run-id>/provenance/`, not here. The current
rig version is in [VERSION](VERSION). Dates are ISO 8601 (YYYY-MM-DD).

Scope of this file: what the tooling and the documentation do. **Results of test
rounds are deliberately absent** — see
[what this repository does not contain](README.md#what-this-repository-does-not-contain).

## 1.1.0 — 2026-11-16

The ISiK Stufe 5 module `Connect` is the only one that authenticates for itself,
with an OAuth 2.0 access token read out of the test data, and the only one that
therefore needs an authorization server and a TLS listener. This release makes a
round able to supply both — **off by default**, and without narrowing the official
run.

Everything here is configuration of the system under test of the kind a customer
performs when attaching a FHIR server to an identity provider. No code is written
into the SUT, no extension point is overridden, and token validation, scope
evaluation and compartment filtering remain the product's, on the request path, as
shipped.

### Added

- **`CONNECT_ENABLE`** ([.env.example](.env.example) §7,
  [scripts/lib.sh](scripts/lib.sh)) — one switch deciding which compose files are
  in play, which configuration is rendered, which TLS material is issued and which
  authentication posture the endpoint is left in. Default `false`: a round behaves
  exactly as it did before this feature existed unless asked not to. A value that
  is neither true nor false is rejected rather than guessed.
- **An authorization server as a test instrument**
  ([compose/authserver.yml](compose/authserver.yml),
  [keycloak/realm-isik.json.tmpl](keycloak/realm-isik.json.tmpl)) — Keycloak,
  deliberately not an InterSystems product: a system under test that issues the
  tokens it then validates would make the measurement circular. One client per
  SMART scope, six in all, rendered from the suite's own test-data contract.
- **`make tokens`** ([scripts/55-mint-tokens.sh](scripts/55-mint-tokens.sh),
  [scripts/lib/connect_tokens.py](scripts/lib/connect_tokens.py)) — obtains the six
  access tokens by `client_credentials` and stages each into the test-data key the
  feature files read it from, after proving key-by-key that the staged contract is
  still the release's. `--verify` reports each token's key, scope and remaining
  lifetime. Called immediately before the run, because a token lives an hour and
  seeding takes minutes.
- **`make test-connect`** ([Makefile](Makefile),
  [scripts/60-run-tests.sh](scripts/60-run-tests.sh)) — a second Stufe 5
  invocation, **appended and not substituted**, through its own Tiger
  configuration ([tiger/v3/tiger-isik-stufe5-connect.yaml.tmpl](tiger/v3/tiger-isik-stufe5-connect.yaml.tmpl))
  that carries no `authentication:` block, so the header the feature file sets for
  itself reaches the SUT. Measured on 2026-11-13: run through the ordinary
  configuration the recorded traffic carried no bearer token at all and four of six
  scenarios "passed" on the harness's credential rather than on the token under
  test.
- **A TLS listener** ([scripts/22-make-tls.sh](scripts/22-make-tls.sh),
  [webgateway/isik-vhost-tls.conf.tmpl](webgateway/isik-vhost-tls.conf.tmpl)) —
  the FHIR server evaluates a bearer token only over a connection it considers
  secure, which is correct product behaviour and not switchable. Real TLS from a
  per-round local CA; **no `X-Forwarded-Proto` is set anywhere**. Every certificate's
  subject, issuer, serial, validity, fingerprint and SAN is recorded, private keys
  are not.
- **OAuth 2.0 configuration of the SUT** ([iris/src/ISiK/OAuth.cls](iris/src/ISiK/OAuth.cls),
  [iris/src/ISiK/Setup.cls](iris/src/ISiK/Setup.cls)) — an outbound TLS
  configuration, a server definition obtained by discovery, a resource-server
  client definition, and the expected audience with the endpoint's advertised SMART
  capabilities. `SearchFiltering` is deliberately left at the installer's default,
  so the measurement states what the product does as shipped.
- **Four scope partitions and the supersede rule**
  ([scripts/lib/results_report.py](scripts/lib/results_report.py)) — each `run.json`
  records `scopePartition` (`complete`, `connect-only`, `without-connect`,
  `narrowed`), and for the 32 `Connect` cases the `connect/` verdicts supersede the
  complete run's rather than being summed with them. Totals are recounted from the
  effective set, `measurements[]` stays unmerged, and every case record names the
  archive that measured it in `measuredIn`.
- **Credential withholding**
  ([scripts/lib/redact_credentials.py](scripts/lib/redact_credentials.py)) — the two
  credentials this rig's publish-by-design policy cannot cover: the six access
  tokens, replaced by their test-data key and SHA-256, and the realm's client secret
  and admin password, withheld from the bundle and left only in the live 0600 files.
  Recognised by the shape of the value rather than by a list of what the round
  currently holds, applied before the bundle manifest is computed, and then
  **proved** by a re-scan whose hit is a build failure. `<variant>/official/` is
  exempt: those archives must stay byte-for-byte as the suite wrote them.
- **[docs/en/08-connect.md](docs/en/08-connect.md)** — what the module requires and
  who supplies it, why the authorization server is not part of the system under
  test, why TLS is the product's requirement, the endpoint's two authentication
  postures with the measurement that established which is the installer's own, what
  is configured inside IRIS and what is not, the tokens, why the invocation is
  separate, why the official run is not narrowed, how to run it, and what a result
  does and does not prove.

### Changed

- `provenance/` now also carries `tiger-TLS.txt`, the round's certificate
  inventory. [scripts/22-make-tls.sh](scripts/22-make-tls.sh) stated that a report
  could say which certificates a round used, but the file lived in `run/`, which
  `make clean` deletes — the claim would have decayed with the first teardown.
- `docs/en/08-known-limitations.md` is now
  [docs/en/09-known-limitations.md](docs/en/09-known-limitations.md), with all
  cross-references updated. It gains the deviations a `Connect` round introduces,
  the two kinds of credential a round withholds, and the statement that the
  submitted archive's `Connect` verdicts are not a statement about token handling.
- The documents use `<variant>` where they used `<stufe>`: a bundle directory is
  named after an invocation, not after a level, and a `Connect` round holds two
  Stufe 5 archives.
- [README.md](README.md) and [INSTALL.md](INSTALL.md) state the additional ~1.1 GiB
  a `Connect` round needs, and that **HugePages are not configured by this rig**
  although InterSystems recommends them.
- **The stated minimum host for a round with `CONNECT_ENABLE=true` is 36 GiB of
  RAM**, not 32 GiB. The measured floor for a round without the overlay is 31 GiB,
  so 32 GiB does not clear it once the overlay's ~1.1 GiB is added. The row is
  derived arithmetic and says so: every `Connect` round so far has run on the
  16 vCPU / 64 GiB host. The overlay's other measured costs — 718 MB for the
  authorization-server image, ~60 MB for the second archive, 107 s of wall-clock
  time — are stated with it, in [README.md](README.md#hardware),
  [INSTALL.md](INSTALL.md#2-hardware), [docs/en/08-connect.md](docs/en/08-connect.md#additional-hardware),
  [docs/de/02-bestaetigungsverfahren.md](docs/de/02-bestaetigungsverfahren.md) and
  [.env.example](.env.example).
- [`scripts/00-preflight.sh`](scripts/00-preflight.sh) now follows
  `CONNECT_ENABLE`: it warns below 26 GiB of RAM instead of 24 GiB, and below
  26 GiB of free disk instead of 25 GiB, when the overlay is on. Previously the
  check was overlay-blind, so a host that was sized for the smaller round passed
  silently.
- [README.md](README.md) no longer claims a round covers "Stufe 3 and Stufe 5".
  Which levels a round covers follows the pinned suite version — a 2.x suite
  carries both, a 3.x suite carries Stufe 5 only — which is what
  `suite_levels()` has implemented since 3.x support landed. The `Connect`
  paragraph now states the mechanism (a second invocation through a route with no
  `authentication:` block), the supersede rule, and that the switch is read from
  `.env` only.
- The German documents cover the additional procedure step, the fourth container,
  the subset tag expression, the supersede rule and the withheld credentials.

### Not included, by rule

Unchanged from 1.0.0, with one addition: **no access token and no realm secret is
published in a round's evidence bundle.** What is published instead is each token's
SHA-256 together with the scope, issuer, audience and lifetime it was minted with —
enough to prove which credential an exchange presented, and not enough to present
it.

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
