# 3. The unit under test: IRIS for Health

## What is under test, exactly

`containers.intersystems.com/intersystems/irishealth:2026.2`, unmodified, with
two FHIR repository endpoints installed at container start. There is no
`Dockerfile`, no derived image and no patched class: everything below is
*configuration applied through documented IRIS APIs*, by
[iris/src/ISiK/Setup.cls](../../iris/src/ISiK/Setup.cls), driven by
[iris/bootstrap/bootstrap.sh](../../iris/bootstrap/bootstrap.sh).

That distinction is the point. A conformance result is only meaningful if the
reader can obtain the same product: the tag, the registry digest and the
instance's own `$ZV` build string are all recorded in `.env.resolved` and in the
SUT's self-written `iris-provenance.json`, and the exact set of knobs turned is
the list in [§ Configuration deviations](#configuration-deviations) below.

## Why two endpoints

ISiK Stufe 3 pins `de.basisprofil.r4#1.4.0`; ISiK Stufe 5 pins `#1.5.4`. A FHIR
endpoint resolves each canonical URL to exactly one definition, so one endpoint
cannot carry both. The two levels therefore get separate namespaces and separate
endpoints, each loaded only with its own package set:

| Level | Namespace | Endpoint | Base profile package |
|---|---|---|---|
| ISiK Stufe 3 | `ISIK3` | `/isik3/fhir/r4` | `de.basisprofil.r4@1.4.0` |
| ISiK Stufe 5 | `ISIK5` | `/isik5/fhir/r4` | `de.basisprofil.r4@1.5.4` |

Both are configurable (`IRIS_NS_STUFE3`, `FHIR_BASE_STUFE3`, …) and nothing
downstream hardcodes them; `InstallLevel()` cross-checks the value it was given
against the package plan and fails if `.env` changed after `make packages`
([Setup.cls:422](../../iris/src/ISiK/Setup.cls#L422)).

The storage strategy is `HS.FHIRServer.Storage.JsonAdvSQL.InteractionsStrategy` —
the Resource Repository, which is what the IRIS documentation itself uses for a
standalone FHIR server and therefore what a reader of the results will expect to
have been tested.

## What the bootstrap does

`bootstrap.sh` is run by the image's own `iris-main` entrypoint via `-a`, once
IRIS is live. It cannot be a build step: the FHIR installation API operates on a
running instance.

1. **Check configuration.** Every `ISIK_*` variable must be non-empty. A missing
   one fails before a namespace is created, not halfway through.
2. **Short-circuit.** `state/bootstrap.done` means a previous start already
   installed everything; a needless package re-import costs tens of minutes. The
   marker has to say *which* installation, not merely that there was one — see
   [What the completion marker has to establish](#what-the-completion-marker-has-to-establish).
3. **Compile and run.** `iris session iris -U HSCUSTOM` loads `/opt/isik/src` with
   `$system.OBJ.LoadDir(…, "ck", …)` and calls `ISiK.Setup.Run()`. Compiling at
   runtime rather than shipping a pre-built export keeps the ObjectScript
   readable and diffable in the repository, which matters for a deliverable an
   auditor may read. `HSCUSTOM` is not an arbitrary choice — see
   [Why the bootstrap session runs in HSCUSTOM](#why-the-bootstrap-session-runs-in-hscustom).
4. **Verify independently.** Both `<base>/metadata` are fetched as the API user
   and must return 200. The setup class reporting success is not accepted as
   proof; a bootstrap that claims success while the endpoint 404s would send the
   whole round chasing phantom gaps.
5. **Publish state.** `bootstrap.status` at every transition, `bootstrap.done` at
   the end, everything teed into `bootstrap.log` — all in `run/iris-state/`, so
   the host can read them without `docker exec`, and `70-collect-report.sh`
   archives them as evidence.

Inside `Run()`: read configuration → read the package plan → `EnsureSecurity()`
once, instance-wide → per level `EnsureNamespace` → `ImportPackages` →
`EnsureEndpoint` → `ConfigureEndpoint` → `SecureEndpoint` → `WriteProvenance()`
last, so the provenance record cannot exist unless everything before it
succeeded.

Every step checks for its own result first, so a re-run repairs a partial
installation instead of failing or duplicating.

### Why the bootstrap session runs in HSCUSTOM

Two requirements have to hold at the same time, and each of the two namespaces one
would reach for first satisfies only one of them. Both were established by probing
`irishealth:2026.2` on the deployment host, not from documentation:

- **The HealthShare installer classes must be visible.**
  `HS.Util.Installer.Foundation` and `HS.FHIRServer.Installer` are mapped into
  `HSLIB` and `HSCUSTOM` but **not** into `%SYS`. Counted via
  `%Dictionary.CompiledClass` per namespace: `HSLIB` holds 7944 `HS.*` classes,
  `%SYS` holds none. A session started in `%SYS` reaches the end of the security
  phase and then raises `<CLASS DOES NOT EXIST>` on the first `Foundation` call.
- **`ISiK.Setup` must be compilable, so the routine database must be writable.**
  `HSLIB` is mounted read-only, so `LoadDir` there fails with
  `ERROR #5883: Item 'ISiK.Setup' is mapped from a database that you do not have
  write permission on`.

`HSCUSTOM` is the namespace the product provides for customer code alongside the
`HS` library mappings, and it satisfies both. That the arrangement actually works
was verified rather than assumed: a throwaway class compiled in `HSCUSTOM` stays
callable after `set $namespace = "%SYS"`, which is what `EnsureSecurity()` and
`SecureEndpoint()` do, and what `ImportPackages()` does for each ISiK namespace —
all under `new $namespace`.

### One deliberate weakness in the idempotency

There is no documented IRIS predicate for "is this namespace FHIR-enabled". The
guard is a marker global, `^ISiK.Setup("fhirEnabled")`, written by
`EnsureNamespace()`. The obvious substitutes are worse:
`HS.FHIRMeta.Storage.Package` lives in HSLIB and is mapped into every Foundation
namespace whether FHIR is installed or not, so its existence proves nothing; and
reading its storage definition through `$$$comClassKeyGet` would depend on an
internal API with no cross-release compatibility guarantee.

The tradeoff, stated so nobody has to rediscover it: a namespace FHIR-enabled by
some *other* means — the Management Portal, `HS.FHIRServer.ConsoleSetup` — is not
recognised and `InstallNamespace()` would run against it again. The case does not
arise here, because every round starts from an empty container.

### The host-side markers belong to the container, and clearing them cost a round

`bootstrap.status` and `bootstrap.done` in `run/iris-state/` are the *other* half of
the state, and the rule for them is not symmetric with the global above: they are
written by the container at every start, and the host's only legitimate business
with them is removing a **stale** pair left by a container that is no longer
running.

Clearing them unconditionally at the top of `make up` — which is what
[`40-up.sh`](../../scripts/40-up.sh) originally did, on the reasonable-sounding
grounds that a previous round's progress must not be read as this round's — is
wrong, because `compose up -d` on an already-running, unchanged container **does
nothing at all**. It reports `Container isik-iris Running` and leaves the process
untouched. Nothing re-runs the bootstrap, so nothing rewrites the two files, and
their deletion has erased the record of an installation that is still there and
still serving.

What follows is precise and misleading in both directions:

1. [`healthcheck.sh`](../../iris/bootstrap/healthcheck.sh) reads the absent
   `bootstrap.status` as "the bootstrap has not started yet" and reports unhealthy.
2. `make up` then fails with *"the bootstrap finished but the endpoints do not serve
   /metadata"* — about a SUT that answers 200 on both endpoints throughout, which
   `curl` confirms in one command while `make up` insists otherwise.

This happened here, on the second `make seed` of a live environment, and it cost the
round. The fix is one condition: if `docker inspect` reports the IRIS container
`running`, its markers describe the instance this invocation is about to wait for,
so leave them alone. Only the not-running case clears them.

The general form of the finding, worth carrying to any similar rig: **`compose up -d`
is not a bring-up, it is a reconciliation.** Any host-side action that assumes a
container start will happen is wrong whenever the desired state is already met.

### What the completion marker has to establish

Not "an installation happened here", but "the installation in this instance is
the one this round is configured for". The marker therefore holds the
**fingerprint of the package plan it installed**, computed by
[`plan_fingerprint.py`](../../iris/bootstrap/plan_fingerprint.py) over the suite
image the plan came from and, per endpoint, its namespace, its base URL and the
ordered packages loaded into it. Not a digest of the plan file: regenerating the
plan can reword a comment or reorder a mapping without changing one package, and
a digest would spend minutes reinstalling for that.

A second copy of the fingerprint is written to `/usr/irissys/mgr/isik-bootstrap.done`,
inside the container. The reason is the asymmetry that the section above exploits
in the other direction: the host-side marker outlives the container, and the
instance's databases do not — there is no named volume for them, so a recreate
destroys every namespace and endpoint while the marker survives to call the
installation present. Reuse requires **both** copies to match this start's plan.
Anything else reinstalls.

Three places act on it, because the same error is reachable from three
distances:

| Where | What it does |
|---|---|
| `bootstrap.sh`, at container start | Reuses the installation only if both markers carry this plan's fingerprint; otherwise installs, and logs which of the two conditions failed |
| `40-up.sh`, before a round | Refuses to run against a **running** instance whose marker names another plan. The only place that case is catchable: `compose up -d` does not restart an unchanged container, so the container's own guard is never reached |
| `70-collect-report.sh`, at publication | Refuses to publish a round whose archived `iris-provenance.json` disagrees with its archived `package-plan.json`, from the two files the bundle itself carries — so the check is repeatable without this rig |

This is defect 31 in
[08-known-limitations.md](08-known-limitations.md#one-further-defect-found-by-asking-what-already-installed-installed),
where the measurement that found it is recorded: a round pinned to suite 3.0.9
that ran against an endpoint installed from the 2.7.0 plan, two ISiK package
versions behind what it published.

## FHIR packages

**Which** packages are loaded is not this project's decision. The authoritative
list is the test suite image's own `refv-plugins/valmodule-*-refv-plugin.zip`
configs — the dependency lists of the Gematik reference validator that will judge
every response. `scripts/lib/plan_packages.py` reads them and writes
`vendor/package-plan.json`; `scripts/lib/order_packages.py` topologically sorts
each endpoint's list from the packages' own `package.json` dependency
declarations, because `HS.FHIRMeta.Load.NpmLoader.importPackages()` needs
dependencies to appear earlier in the list.

If the SUT and the validator disagreed on a package version, the suite would
report profile failures that are artefacts of version skew rather than gaps in
IRIS — the worst possible outcome for an exercise whose entire product is a gap
analysis. Deriving the list from the validator's own configs is what prevents
that.

34 packages are in play across both levels. The ISiK-level-carrying ones:

| Package | Level |
|---|---|
| `de.gematik.isik-basismodul@3.1.1` | Stufe 3 |
| `de.gematik.isik-dokumentenaustausch@3.1.1` | Stufe 3 |
| `de.gematik.isik-medikation@3.1.1` | Stufe 3 |
| `de.gematik.isik-terminplanung@3.1.1` | Stufe 3 |
| `de.gematik.isik-vitalparameter@3.1.1` | Stufe 3 |
| `de.gematik.isik3-dokumentenaustausch-terminology@1.0.0` | Stufe 3 |
| `de.gematik.isik@5.1.2` | Stufe 5 |

The rest are the dependency closure: `hl7.fhir.r4.core@4.0.1`,
`de.basisprofil.r4` at both pins, `de.medizininformatikinitiative.kerndatensatz.*`,
`dvmd.kdl.r4*`, `hl7.terminology.r4` at four versions, `ihe.*`, `hl7.fhir.uv.*`.
The full list with per-endpoint assignment is in `vendor/package-plan.json` and,
read back *from the instance* rather than restated from the plan, in
`iris-provenance.json`.

Five properties of that plan are declared rather than silently applied, each
recorded in the plan file itself:

* **`hl7.fhir.r4.core@4.0.1` is implicit.** `isik3-basismodul` names it; `isik5`
  does not, because the validator always has core R4. An IRIS endpoint's package
  list must contain it — it supplies the base resource definitions the canonical
  map is built on — so it is added to both levels and flagged `implicit: true`.
* **Version collisions are recorded, not resolved by guessing.** Several
  dependency lists pin two or more versions of one package (`isik5` needs
  `hl7.terminology.r4` at 5.0.0, 6.3.0 *and* 6.5.0). That is legal for the
  validator, which resolves canonicals across its whole set, but ambiguous for an
  IRIS endpoint with a single canonical map. All versions are *imported*; the
  endpoint's package list keeps the highest. Each collision appears under
  `versionCollisions`.
* **`snapshotDependencies` are excluded from the load plan — with one forced
  exception.** The validator uses them to build snapshots but ships no payload for
  them, and they contribute no profiles the SUT must know about. The exception is
  `hl7.terminology.r4@5.3.0`: `ihe.formatcode.fhir@1.2.0`, which
  `isik3-dokumentenaustausch` pins as a real dependency, declares it in its own
  `dependencies`, and IRIS resolves declared dependencies by exact id (see
  [Unresolvable dependency declarations](#unresolvable-dependency-declarations)
  below). Such a package is promoted into the load plan, recorded under
  `snapshotDependenciesPromoted`, and — because no bundled payload exists — fetched
  from the package registry even under `PACKAGE_SOURCE=refv`; the `MANIFEST.txt`
  header names it as the one line whose bytes did not come from the image. The
  resulting asymmetry is declared too: the SUT then holds one package the
  validator's dependency list does not pin for this level. It supplies terminology
  only, no ISiK profile the suite asserts against, and the endpoint's package list
  is unaffected because the version-collision rule keeps only the highest version
  of a name there.
* **Example-only packages are excluded.** `de.gematik.isik@5.1.2` depends on
  `hl7.fhir.r4.examples@4.0.1`, which the validator legitimately needs. It is
  ~198 MB across 5310 files and every one of its 658 StructureDefinitions repeats
  a canonical already defined by core R4, so importing it would add a large
  duplicate-canonical set to the endpoint's definition map while contributing
  nothing the SUT must conform to. Packages whose `package.json` declares
  `"type": "fhir.examples"` are dropped from both lists and recorded under
  `excludedPackages` with the reason. They stay in `vendor/packages/` and in
  `MANIFEST.txt`, because they *are* part of what the validator uses and thus
  part of the provenance record.

### Unresolvable dependency declarations

Ordering the packages is not sufficient. `HS.FHIRMeta.Load.NpmLoader.coreVersion()`
walks the `dependencies` of every package being imported and resolves each entry
with an exact-id `HS.FHIRMeta.Storage.Package.GetById("<name>@<version>")`. There is
no semver resolution and no tolerance for a missing entry: the first unresolvable
declaration aborts the entire import with `ERROR #5809: Object to Load not found`,
*after* the namespace has been created. Established by dumping `coreVersion()` out
of `%Dictionary.CompiledMethod` on `irishealth:2026.2` and reproducing each abort —
not from documentation.

Four declarations in the ISiK closure are unresolvable that way, and they do not
share a cause. **None of them is an ISiK conformance failure of the SUT**, and the
distinction matters because it decides who owns the finding, so it is carried into
the plan as `dependencyRewrites[].kind` and from there into the reports:

| Kind | Declaration | Owner |
|---|---|---|
| `wildcard` | `de.gematik.isik-{medikation,terminplanung,vitalparameter,dokumentenaustausch}@3.1.1` → `de.gematik.isik-basismodul: 3.1.x` | **IRIS.** A semver patch range is valid under the FHIR NPM package specification; the packages are correct as published. The limitation is that the loader does no range resolution |
| `absent-version` | `hl7.fhir.uv.subscriptions-backport.r4@1.1.0` → `hl7.fhir.r4.core: 4.0.0` | **Upstream HL7.** 4.0.0 was never published — the registry returns 404 for it; R4 is 4.0.1 |
| `excluded-from-sut` | `hl7.fhir.uv.sdc@3.0.0` → `hl7.fhir.r4.examples@4.0.1` | **This project.** The example corpus is deliberately not loaded (see above) |

`order_packages.py` makes each declaration resolvable in the unpacked
`vendor/packages/<id>/package.json`: a range or absent version is repointed to the
version this endpoint actually loads, and the edge to an excluded package is
*removed* rather than repointed — substituting a different package would fabricate
a dependency the guide does not declare. A declaration naming a package that is
neither loaded nor excluded is left alone and reported, because that would mean the
plan is missing a package: a condition for a human to look at, not one to paper
over.

What is modified and what is not, precisely: only the unpacked `package.json` files,
which are what IRIS reads at import time. The `.tgz` payloads in `vendor/tgz/` are
**not** touched, so every sha256 in `MANIFEST.txt` still attests the bytes exactly
as published. Every single change is recorded in `dependencyRewrites` with its
`kind` and its reason and archived with the round, so the modified state of the tree
is a declared property of the evidence rather than a hidden one.

Import is confirmed rather than assumed: after `importPackages()` returns, every
package in the level's `loadOrder` is re-checked with `IsPackageLoaded()` and a
missing one is fatal. A package silently absent here would surface much later as
unexplained profile failures.

`EnsureEndpoint()` also calls
`HS.FHIRServer.Installer.InitializeProfileValidator()`, which loads the
endpoint's profiles into the `FHIR_Validation_Server` external language server.
A failure there is logged as a warning and is **not** fatal: the conformance run
is judged by Tiger's reference validator, not by IRIS. Only the seeder's
pre-flight `$validate` check depends on it, and `50-seed.sh` degrades gracefully
and says so.

It does in fact fail on this package set, reproducibly, on both endpoints:

```
WARNING: profile validator initialisation reported: FHIR profile validation not
enabled due to unexpected error.; ERROR #8104: Gateway Exception: <GATEWAY>
java.lang.IllegalStateException
org.linuxforhealth.fhir.registry.util.Index.load(Index.java:95)
index version was not set
```

The cause is one of the imported packages carrying a `.index.json` without the
`index-version` field that IBM's `linuxforhealth` FHIR registry — which IRIS's
validator embeds — requires. It is a property of the published ISiK/HL7 package
set meeting that library's expectations, not of the endpoint configuration, and it
is why the warning is non-fatal by design rather than by tolerance. The
consequence is bounded and stated in the reports: IRIS's own `$validate` is
unavailable for the seeder's pre-flight, and nothing in the conformance verdict
depends on it.

## Security

The suite authenticates to the SUT with HTTP Basic. Authenticated access is used
rather than open access because it is closer to a real deployment, it exercises
the REST layer's auth path, and it keeps the endpoints from being open to
anything that reaches the subnet.

`EnsureSecurity()` builds, in this order:

1. **Resource `ISiK_FHIR`** — guards the FHIR web applications, created with no
   public permission, so reaching an endpoint requires the role below. No leading
   `%`: that prefix is reserved for system-defined security objects and
   `Security.Roles.Create()` rejects it outright.
2. **Role `ISiKFHIRUser`** — holds `ISiK_FHIR:U`. `GrantedRoles` is deliberately
   empty; in particular `%All` is **not** granted. The API user must not be able
   to reconfigure the server it is testing.
3. **The API user** (`SUT_USER`, default `isikapi`) — that role and nothing else,
   with `ChangePassword 0`, `Enabled 1`, `AccountNeverExpires 1`,
   `PasswordNeverExpires 1`. On a re-run with a regenerated password the existing
   user is *modified* rather than skipped, because otherwise the rendered Tiger
   configuration and the server would disagree and every scenario would fail on
   authentication.
4. **The predefined accounts** — `_SYSTEM`, `SuperUser`, `Admin`, `CSPSystem` are
   enabled and marked non-expiring; the one named by `IRIS_MGMT_USER` also gets
   the documented management password. The list is a superset: which accounts a
   given IRIS edition predefines varies, so a missing one is logged and skipped
   rather than fatal. `CSPSystem` is deliberately excluded from the password
   change — it is the account the CSP gateway authenticates with, and changing it
   without reconfiguring the gateway would break the web server the FHIR
   endpoints are served through. If `IRIS_MGMT_USER` is not one of the predefined
   accounts it is created, with `%All`, since administrative portal access is its
   whole purpose.
5. **`UnExpireUserPasswords("*")`** — every account, not just ours, with the
   returned count logged.

Steps 4 and 5 exist for one concrete reason: **the official image ships its
predefined accounts with expired passwords.** The Management Portal answers that
with a forced-password-change page, and both `make manual` and
`run/CREDENTIALS.md` point the tester at that portal. Without these steps the
documented link cannot be used at all.

Nothing here expires or demands a change mid-round, deliberately and for one
reason: either condition arriving during a run would present as a wave of refused
requests indistinguishable from an authorisation gap in the SUT — and, as the next
section shows, not even distinguishable from a missing path by status code.

### The password policy is *not* relaxed

`PasswordPattern` is left at the IRIS default (`3.32ANP`). The generated
passwords satisfy it by construction — see `gen_password()` in
[scripts/lib.sh](../../scripts/lib.sh) — so the SUT keeps a production-like
security configuration. No `Security.System.Modify()` call is made.

The one accommodation is diagnostic: `ThrowOnPasswordError()` wraps every
password-setting call and, on failure, appends the instance's *actual*
`PasswordPattern` (read via `##class(Security.System).Get(.props)`) to the error
text. A password rejected by policy would otherwise present as a bootstrap
failure with no stated cause. The remedy it points at is the generator, not the
instance policy.

### The web application

`InstallInstance()` creates the endpoint's CSP application with
`AutheEnabled = 64` (`AutheUnauthenticated`) — the IRIS default.
`SecureEndpoint()` switches it to `32` (`AuthePassword`), so an unauthenticated
request is refused rather than served anonymously.

#### It is refused with 404, not 401

This surprises everyone who meets it, and it is worth stating precisely. Probed
through the gateway against this installation:

| Credential | Response |
|---|---|
| The API user, correct | **200** with the CapabilityStatement |
| None | **404** |
| A wrong user or password | **404** |

With `AuthePassword` and no `AutheUnauthenticated` bit, the CSP dispatcher does not
challenge — it declines to route the request at all, and reports that as "no such
path". Three consequences, each of which has already cost time here:

* **404 does not mean the endpoint is missing.** It is the single most misleading
  symptom in this rig, because the honest reading of it is wrong.
  [`healthcheck.sh`](../../iris/bootstrap/healthcheck.sh) therefore spells out both
  causes in its 404 message and names the checks in order.
* **`wget` needs `--auth-no-challenge`.** Credentials are normally sent only after
  a 401 invites them; no challenge ever arrives here, so without the flag `wget`
  never sends the credential and reports 404 against a perfectly healthy endpoint.
  See [`http_probe.sh`](../../iris/bootstrap/http_probe.sh).
* **A wrong credential is indistinguishable from a wrong path** by status code
  alone. The distinguishing evidence is IRIS's audit log, which is why
  [`91-status.sh`](../../scripts/91-status.sh) prints the credential it used
  alongside the code it got.

Tiger's `healthcheckUrl` is probed without the route's credential and so sees this
404. That is still valid proof of reachability — only something answering HTTP at
that address can produce it — and Tiger treats any HTTP answer as healthy
([04-testtool.md](04-testtool.md#the-deviations-and-why-each-exists)).

Two things are deliberately left alone:

* **`Resource`.** If the installer set one, it is preserved and *granted to*
  `ISiKFHIRUser` instead of being replaced. Overwriting it would change how a
  stock IRIS FHIR endpoint is protected; an inspector comparing this installation
  to a normal one should find the difference to be the authentication mode and
  nothing else.
* **`MatchRoles`.** That is where `InstallInstance()` puts the database and SQL
  privileges the FHIR service needs. They apply by virtue of the application, not
  of the caller — so the API user needs no database roles of its own and cannot
  use them anywhere else.

Credentials themselves — how they are generated, where they are written, and why
they are published in the reports — are in [02-setup.md](02-setup.md#credentials)
and [08-known-limitations.md](08-known-limitations.md#credential-documentation).

## The HTTP surface is not in this container

The web application above exists, is enabled and dispatches to
`HS.FHIRServer.RestHandler` — and none of that makes IRIS answer an HTTP request,
because **the official IRIS for Health images contain no web server.** The evidence
and the resulting topology decision are in
[01-architecture.md](01-architecture.md#the-sut-is-two-containers); this section
covers what it means for the SUT container specifically.

Consequences for anyone debugging inside `isik-iris`:

* `curl http://localhost:52773/...` from inside the container cannot work, and not
  only for the obvious reason: **the image ships no `curl` either.** It has `wget`.
  A probe that appears to hang is usually neither IRIS nor the network but a missing
  binary.
* `iris.cpf` carries `WebServer=0` / `WebServerPort=0`. Setting them to `1` and
  `52773` and restarting changes nothing — there is no server for the setting to
  start. The image's `parameters.isc` records the reason plainly:
  `csp_gateway.configure_apache: N`.
* Nothing in the container listens on 52773 at any point. Only 1972 (superserver),
  2188 and 42623 do.

### Verification is therefore not an HTTP check

[`VerifyEndpoints()`](../../iris/src/ISiK/Setup.cls) runs *inside* IRIS and cannot
issue an HTTP request even in principle, and the gateway container is not
guaranteed to be up when it runs. So it verifies the state the gateway will serve —
the application's existence, `Enabled`, `DispatchClass`, `NameSpace`,
`AutheEnabled` — and then produces a CapabilityStatement through
`InteractionsStrategy.GetMetadataResource()`, which is the same code path an HTTP
`GET /metadata` reaches once the gateway hands the request over. The end-to-end
HTTP path is checked from outside, by
[`healthcheck.sh`](../../iris/bootstrap/healthcheck.sh), through the gateway.

Note `GetMetadataResource()` **on the strategy**. `HS.FHIRServer.*.Interactions`
has `LoadMetadata()`/`SetMetadata()` — storage-side accessors for the stored
statement — but no `GetMetadata()`; calling one costs a full 30-minute install
before failing with `<METHOD DOES NOT EXIST>`. In `irishealth:2026.2` the only
zero-argument method returning the resource is
`HS.FHIRServer.{API,Storage.Json,Storage.JsonAdvSQL}.InteractionsStrategy::GetMetadataResource`.

### The four directives that route FHIR

Each of these was established empirically during bring-up, and each has a distinct
failure signature. They are rendered into `run/webgateway/isik-vhost.conf` from
[webgateway/isik-vhost.conf.tmpl](../../webgateway/isik-vhost.conf.tmpl).

| Directive | Symptom without it | Cause |
|---|---|---|
| `CSP On` | HTTP 404 on every FHIR path | `CSPFileTypes` defaults to `csp cls zen cxw`, so `mod_csp` declines extensionless paths — and every FHIR path is extensionless |
| `CGIPassAuth On` | HTTP 503, `CSPServer Error displaying login page ns=ISIK3` in `messages.log` | Apache 2.4 withholds the `Authorization` header from handlers unless asked. IRIS sees an unauthenticated request and tries to render a login page |
| The `Location` blocks in the **vhost** | The blocks are silently ignored | `apache2.conf:222` includes `conf-enabled/*.conf` at main-server level, so a `Location` placed in `conf-available/CSP.conf` never applies to the vhost that actually serves port 80 |
| `Username=CSPSystem` + `Password=` in `CSP.ini`'s `[IRIS]` block | Bare HTTP 500 on every request; `Connection Validation Failed … 403 Forbidden … Access Denied` in the gateway log | The gateway opens its own authenticated service connection to IRIS, separate from the FHIR credential |

`csp-handler-sa` is the only registered handler name in `webgateway:2026.2`
(Apache 2.4.58, module `csp_module_sa` from `/opt/webgateway/bin/CSPa24.so`).

The gateway's `CSPSystem` password is not an operator setting: it is assigned from
`IRIS_MGMT_PASSWORD`, so the value the bootstrap sets on the IRIS side and the value
in the rendered `CSP.ini` cannot drift. Note also that setting `RELOAD=1` on the
gateway container makes it rewrite `CSP.ini` and obfuscate the password in place,
which is why the template does not set it.

## Configuration deviations

Every setting below is applied by `ConfigureEndpoint()` to *both* endpoints, is
`.env`-configurable, and is read back out of the instance into
`iris-provenance.json` so the documented value is the observed one.

| Setting | Value set here | `.env` key | Why |
|---|---|---|---|
| `DefaultSearchPageSize` | 100 | `FHIR_DEFAULT_PAGE_SIZE` | Set explicitly rather than inherited, so a search without `_count` returns a known page size that is recorded in the provenance |
| `MaxSearchPageSize` | 500 | `FHIR_MAX_PAGE_SIZE` | The suite issues searches with explicit `_count` values; a ceiling below them would truncate the bundle |
| `MaxSearchResults` | 10000 | `FHIR_MAX_SEARCH_RESULTS` | Several scenarios search deliberately broadly. A truncated result set presents as "resource not in bundle" — i.e. as a search-semantics gap rather than as pagination |
| `DefaultPreferHandling` | `lenient` | `FHIR_PREFER_HANDLING` | Makes IRIS ignore search parameters it does not recognise instead of rejecting the request. This is the FHIR-specified default behaviour and what ISiK expects |
| `DebugMode` | 0 | — | Debug mode adds diagnostic detail to `OperationOutcome`, which would make the recorded responses differ from a production deployment's — and those responses are the evidence in the gap report |
| `AutheEnabled` (CSP application) | 32 = `AuthePassword`, replacing the installer's 64 = `AutheUnauthenticated` | — | See above |

The three search-sizing values are raised relative to the product defaults, which
are lower; the exact defaults are whatever the pulled build ships and are visible
in the Management Portal for the round's own instance. What matters for reading a
result is the value that was in force, and that is read back from the instance
into `iris-provenance.json` rather than restated from `.env`.

Two of these deserve emphasis because they *change what is measured*:

**`DefaultPreferHandling=lenient` is the conformant setting, but it hides
unsupported search parameters.** With `lenient`, a search using a parameter IRIS
does not index returns an unfiltered — hence wrong — result set instead of an
error, and the scenario fails on the *content* of the bundle. Setting
`FHIR_PREFER_HANDLING=strict` turns each such case into an explicit
`OperationOutcome` naming the parameter, which is far more useful for gap
hunting. It is not the default here because it changes what the run measures, and
a reportable round must measure conformance. Use it for a diagnostic round and
say so in the round's notes.

**Raised page sizes make the run pass or fail on semantics rather than on
pagination.** They are raised, never lowered, so no scenario can succeed that
would have failed at the product default for a conformance reason.

Everything else on the endpoint, on the namespace and on the instance is stock.
The complete list of deliberate deviations across the whole rig — SUT, test tool,
test data and reporting — is consolidated in
[08-known-limitations.md](08-known-limitations.md).

### Two write-path behaviours the gap report depends on

Neither is a configuration choice of this rig — both are stock behaviour of the
pinned build, measured against the running instance because the gap report's
categories name an IRIS locus and a locus must be a fact. They are recorded here
so a developer can re-measure them in one command before opening any source.

**Writes are validated against base R4, not against the endpoint's profiles.**
`ResourceValidation` is `always` on both endpoints — the stock value; nothing in
this repository sets it. Even so:

```bash
# valid R4, but no identifier and no name, so invalid against the ISiK Patient profile
curl -u "$SUT_USER:$SUT_PASSWORD" -H 'Content-Type: application/fhir+json' -X POST \
  -d '{"resourceType":"Patient","gender":"other"}' \
  "$BASE/Patient"                                   # -> 201 Created

# an element that is not in the base schema at all
curl -u "$SUT_USER:$SUT_PASSWORD" -H 'Content-Type: application/fhir+json' -X POST \
  -d '{"resourceType":"Patient","notARealElement":true}' \
  "$BASE/Patient"                                   # -> 400, <HSFHIRErr>UnexpectedPropertyName
```

So the write path enforces the base R4 schema and not the profiles the endpoint
loaded, while `$validate` against those same profiles answers correctly. This is
what the gap report's `WRITE_VALIDATION_GAP` category points at, and it is why 16
of the reference round's failures are one finding rather than sixteen.

**A create returns no body unless the request asks for one.**

```bash
curl -D- -u "$SUT_USER:$SUT_PASSWORD" -H 'Content-Type: application/fhir+json' -X POST \
  -d '{"resourceType":"RiskAssessment","status":"final","subject":{"reference":"Patient/1"}}' \
  "$BASE/RiskAssessment"
# -> 201 Created, CONTENT-LENGTH: 0, LOCATION: .../RiskAssessment/92/_history/1
# with -H 'Prefer: return=representation': 201 and the resource in the body
```

Six Stufe 5 `*-Create` scenarios send no `Prefer` header and then assert
`($this is X) or ($this is Parameters)` over the response, so they fail in the HAPI
parser rather than on an assertion (`HAPI-1857: Did not find any content to parse`).
There is no endpoint setting for this: `HS.FHIRServer.API.ConfigData` carries
`DefaultPreferHandling`, which governs unknown *search parameters*, and no
return-preference property at all — so the default is decided in code, which is the
locus the report names.

## The provenance record

`WriteProvenance()` writes `run/iris-state/iris-provenance.json`: what this
instance actually *is*, produced by the SUT itself rather than restated from
`.env`, so that the documented configuration is the observed one. It carries
`$ZV`, the strategy class, the API and management **account names**, the package
plan's source image, and per level: namespace, endpoint, the loaded package list
read back with `HS.FHIRMeta.Storage.Package.GetAllPackages()`, and the five
configuration values read back from `GetServiceConfigData()`.

The distinction between the plan and this file is the distinction between what
was asked for and what is installed. Only the second is evidence.

Passwords are **not** in it. That is not the old suppression policy: the
credential channel is `run/CREDENTIALS.md` plus the `credentials` block in
`run.json`, which the results analysis renders into §1 of every `results.md`. One
channel, so there is one thing to look at. `70-collect-report.sh` copies this
file into `provenance/iris-provenance.json` of every evidence bundle.
