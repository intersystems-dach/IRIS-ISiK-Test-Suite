# 8. The `Connect` module: bearer tokens, TLS and the authorization server

ISiK Stufe 5 carries ten test modules. Nine of them send no credential of their
own and rely on the Tiger proxy's route to authenticate on their behalf
([04-testtool.md](04-testtool.md#the-deviations-and-why-each-exists)). The tenth,
`Connect`, authenticates **per request from the feature file itself**:

```gherkin
| Authorization | Bearer ${connect.read-patient-scope-allowed} |
```

and asserts what the FHIR server does with the presented token — which resources
its scope grants, which it does not, and what the endpoint advertises about
itself at the two SMART discovery documents. That is a different measurement from
the other nine, and it needs a different environment to make it.

This document states what that environment is, what it adds to the system under
test, what it deliberately does not add, and how the resulting evidence is to be
read. It is the companion of [09-known-limitations.md](09-known-limitations.md),
which lists the deviations; here each one is explained once, with its reason.

**It is off by default.** `CONNECT_ENABLE=false` in
[.env.example](../../.env.example) §7, and with it false none of what follows is
deployed: no authorization server, no TLS material, no OAuth configuration inside
IRIS, and the round is configured exactly as every round taken before this
section of the configuration existed. `Connect` is then reported as excluded for
a missing environment prerequisite — category `ENVIRONMENT_PREREQUISITE`, which
the gap report segregates from the SUT's own defects
([07-reports.md](07-reports.md#categories)).

## What the module requires, and who is required to supply it

The suite's own README states the prerequisite and the boundary in one sentence:

> the tests make the assumption that the System-Under-Test is up and running and
> that an Authorization Server is available. The tests do not evaluate the
> SMART-on-FHIR Authorization Flow, instead they require the Bearer Token used
> for sending authenticated requests to the System-Under-Test

— gematik/isik-testsuite `README`, *Running Tests*; quoted in the header of
[compose/authserver.yml](../../compose/authserver.yml).

Three consequences follow, and they are the whole design of this part of the rig:

| The suite requires | Who supplies it | Where |
|---|---|---|
| An authorization server | The rig, as scaffolding | [compose/authserver.yml](../../compose/authserver.yml) |
| A bearer token per scope, in its test data | The rig, by minting one | [scripts/55-mint-tokens.sh](../../scripts/55-mint-tokens.sh) |
| A FHIR server that consumes such a token | **The SUT** | [iris/src/ISiK/OAuth.cls](../../iris/src/ISiK/OAuth.cls) |

Only the third row is the measurement. The first two are the preconditions the
suite declines to establish for itself, and the test data ships the six token keys
with the literal value `SET-YOUR-TOKEN-HERE` — the suite's documented instruction
to the operator is to put real tokens there.

## The authorization server is not part of the system under test

It is test scaffolding, exactly like the test suite container. The ISiK
specification requires the FHIR server to *consume* tokens issued elsewhere; the
conformance statements a round produces are statements about that consumption. A
customer installation supplies its own identity provider, and this file supplies
one so that the suite's `Connect` scenarios exercise a token the FHIR server has
to validate.

**Keycloak, and deliberately not an InterSystems product.** IRIS for Health can
act as an OAuth 2.0 authorization server. Configuring the SUT's own instance to
issue the tokens it then validates would make the test circular, and would add
product configuration no customer deployment of a FHIR server has. An
independent, widely deployed, open-source authorization server is both the
realistic and the defendable choice, and it keeps the SUT untouched except for
the client and trust configuration a customer performs anyway.

| Property | Value | Why it is stated |
|---|---|---|
| Image | `quay.io/keycloak/keycloak:26.4.7` | Pinned by tag in `.env.example`; the resolved digest is in each round's `provenance/env.resolved.txt` |
| Mode | `start`, production mode | **Measured, 2026-11-16:** with `start-dev` the server reported `Listening on: http://0.0.0.0:8080 and https://0.0.0.0:8443` — development mode enables the HTTP listener unconditionally and ignores `KC_HTTP_ENABLED`, putting the token endpoint on the subnet without TLS. Nothing was configured to use it, but a round's TLS posture has to be a property of the deployment, not of what its clients happen to do |
| Database | `--db=dev-file`, realm re-imported at every start | A test instance that holds nothing worth persisting. Production mode deprecates this as a *default*, which is why it is set explicitly rather than inherited |
| Address | `172.31.0.40`, name `isik-as` | The name is in the certificate **and in the `iss` claim of every token**, so it is not cosmetic: IRIS discovers the server under this name, and a token whose `iss` does not match what IRIS discovered is rejected |
| Memory | `AS_MEM_LIMIT=1g`, `AS_HEAP_MAX=512m` | Measured resident set under the token load of a full Connect run: ~600 MiB. The limit is set so this container cannot compete with IRIS for host memory |

The overlay is **not** profile-gated. The presence of the file is the switch, and
the `iris` service declares a dependency on the authorization server so that
`compose up iris webgateway` starts it without any caller having to know it
exists. With the file absent, every target behaves as it did before the file
existed — which is what keeps the August 2026.2 baseline round, and every round
taken since, re-creatable from this repository. The switch is recorded in
`.env.resolved`, so a round's own provenance states which of the two
configurations it ran.

## TLS is required by the product, not chosen by the rig

The FHIR server refuses to **look at** a bearer token on a connection it does not
consider secure. `HS.FHIRServer.RestHandler`'s `PrelimTokenCheck` requires
`HS.Local.FHIRServer.Util.BaseURL.IsRequestSecure()`, which derives from
`%request.Secure` — that is, from real TLS terminating at the Web Gateway. This
is correct product behaviour, a bearer token being a bearer credential, and it is
not configurable away.

So the round supplies genuine TLS rather than asserting a scheme the connection
does not have. **No `X-Forwarded-Proto` is set anywhere in this rig**, and the
product's own check is left to do its job. [22-make-tls.sh](../../scripts/22-make-tls.sh)
issues, under `run/tls/`:

| File | What it is |
|---|---|
| `ca.crt`, `ca.key` | A local certificate authority — this round's single trust anchor |
| `sut.crt`, `sut.key` | Server certificate for the SUT's HTTPS listener, SANs `DNS:isik-webgateway`, `DNS:localhost`, `IP:127.0.0.1` |
| `as.crt`, `as.key` | Server certificate for the authorization server |
| `TLS.txt` | What was issued, for whom, with which fingerprints |

**Why a local CA.** One CA issues both certificates so that the SUT, the Web
Gateway, the authorization server and the test suite can all be given a single
trust anchor. A publicly trusted certificate would require a publicly resolvable
name, which the isolated test subnet deliberately does not have. Nothing here is
published and the leaves are deliberately short-lived: a certificate that
outlives the round it was issued for invites reuse in a context nobody checked.
The CA outlives them so that a re-issue does not change the trust anchor
mid-round. The fingerprints are in `TLS.txt`, archived into the bundle, so a
report can state exactly which certificates a round used.

**Two listeners, one endpoint.** The HTTPS listener is `isik-webgateway:443`
inside the subnet, published on the host as `52443`. It fronts the same Web
Gateway, the same IRIS instance, the same namespace and the same web application
as the `:80` listener the other nine modules use — a second listener in front of
the endpoint, not a second endpoint. The nine keep using `:80` so that their
results stay comparable with every round taken before this one.

**Reproducing a request by hand needs two options.** `isik-webgateway` is the
container's network alias and is not in the host's DNS, and the round's CA is not
in any trust store, so a host-side replay supplies both:

```
curl -i -H "Authorization: Bearer $TOKEN" \
     --resolve isik-webgateway:52443:127.0.0.1 \
     --cacert run/tls/ca.crt \
     https://isik-webgateway:52443/isik5/fhir/r4/Patient/Patient-Read-Example
```

This is the form `CASE-EVIDENCE.md` emits for a bearer-carrying case
([07-reports.md](07-reports.md#where-each-clause-comes-from)), and it is why
`connect/run.json` records `sut.baseUrlFromHost` beside the in-subnet base URL.

## The endpoint's authentication posture

A round with `CONNECT_ENABLE=true` leaves the Stufe 5 endpoint in a **different**
posture from a round without it. This is the single most consequential statement
in this document, so it is given with its measurement.

| | Without an authorization server (the default) | With one |
|---|---|---|
| `AutheEnabled` | `32` — `AuthePassword` | `8288` — `Unauthenticated,Password,Delegated` |
| Application `Resource` | `ISiK_FHIR`, or the installer's own if it set one | empty |
| Set by | `ISiK.Setup.SecureEndpoint()`, a deliberate tightening | **The FHIR server's own installer.** Nothing in this rig changes it |
| Unauthenticated resource request | 404 — the CSP dispatcher declines to route ([03-sut-iris.md](03-sut-iris.md#it-is-refused-with-404-not-401)) | **401**, from the FHIR server itself, with an OAuth client associated |

**Why the Connect posture is the stock one, and not a relaxation chosen to make a
test pass.** Two independent reasons, both properties of the product:

* A request carrying only `Authorization: Bearer …` presents no IRIS credential,
  so it cannot pass a password-only application. The product's token handling
  runs under delegated authentication — which is why the installer enables all
  three bits.
* An application `Resource` is a privilege check against the authenticated IRIS
  user, and the user a token resolves to holds no such privilege. A resource
  added by this rig would turn every token-bearing request into a 404.

**That 8288 and an empty resource are the installer's own choice was established
by measurement,** not assumed: calling
`HS.FHIRServer.Installer.InstallCSPApp()` for a scratch application path — with
and without an OAuth client name — yields `AutheEnabled` 8288 and `Resource`
empty in both cases. The endpoint is therefore *more* stock in the Connect
posture than in the other, and unauthenticated access is nonetheless refused,
because with an OAuth client associated the FHIR server answers an
unauthenticated resource request with 401 itself.

An endpoint found in neither posture is a failure and is not overwritten: it
means something other than `ISiK.Setup` configured it, and silently normalising
it would make the round's own record of its configuration false. See
`SecureEndpoint()` in [iris/src/ISiK/Setup.cls](../../iris/src/ISiK/Setup.cls).

Three `Connect` requests carry no bearer token at all — the CapabilityStatement
and the two SMART discovery documents. They are served unauthenticated by the
endpoint as the installer configures it. **Measured against the SUT:**
`GET /metadata` and `GET /.well-known/smart-configuration` both answer 200 with
no credential.

## What is configured inside IRIS, and what is not

[iris/src/ISiK/OAuth.cls](../../iris/src/ISiK/OAuth.cls) performs four
configuration steps, all of them on objects the product provides for exactly this
purpose, and all of them of the kind a customer performs when connecting their
FHIR server to their own identity provider:

| Step | Object | What it establishes |
|---|---|---|
| 1 | `Security.SSLConfigs` | An outbound TLS configuration naming the trust anchor of the authorization server's certificate. `VerifyPeer` stays on; TLSv1.2 and TLSv1.3 only |
| 2 | `OAuth2.ServerDefinition` | The server, obtained **by discovery** from the authorization server's published metadata |
| 3 | `OAuth2.Client`, type *resource server* | The identity the FHIR server authenticates with when it introspects a presented token, associated with the endpoint through `HS.Util.RESTCSPConfig`'s `OAuthClientName` |
| 4 | `HS.FHIRServer.API.ConfigData`, `HS.FHIRServer.ServiceAdmin` | The endpoint's expected token audience and its advertised SMART on FHIR core capabilities |

It writes no code into the system under test, overrides no `HS.Local.*` extension
point, and changes no behaviour of the FHIR server itself. **Token validation,
scope evaluation and patient-compartment filtering are performed entirely by the
product, on the request path, exactly as shipped** — which is the property that
makes the resulting measurement a statement about InterSystems IRIS for Health
and not about this rig.

**Only base IRIS for Health is used.** Every class named above is part of IRIS for
Health. Nothing is taken from, or reproduces code of, HealthShare Unified Care
Record, which is a separately licensed product: the OAuth 2.0 framework
(`%SYS.OAuth2.*`, `OAuth2.*`) is part of the InterSystems platform, and the FHIR
server's token handling (`HS.FHIRServer.*`) is part of the FHIR repository that
ships with IRIS for Health.

### Deliberately not configured: `SearchFiltering`

`SearchFiltering` is left at the value the product's own installer sets. It
governs whether a patient-scoped search is filtered to the token's patient
compartment or refused. The installed default filters, and measurement showed the
alternative to satisfy *fewer* of the suite's scenarios. Changing it would be a
configuration choice made to influence a result, which is not what this rig does —
and the two residual `Connect` failures of the 2026-11 round are a consequence of
exactly this setting, categorised as `AUTHORIZATION_REFUSAL_FORM` and stated as
such rather than removed ([07-reports.md](07-reports.md#categories)).

### The advertised SMART capabilities are a statement about the deployment

`/.well-known/smart-configuration` is assembled by
`HS.FHIRServer.Util.WellKnown.GetSMARTConfiguration` from the authorization
server's metadata plus a configured capability list. What belongs in that list is
what *this deployment* actually supports, so the six values are a statement about
the deployment and not a claim about the product:

```
launch-standalone, client-confidential-symmetric, context-standalone-patient,
permission-patient, permission-user, permission-v2
```

They are the capabilities the realm in
[keycloak/realm-isik.json.tmpl](../../keycloak/realm-isik.json.tmpl) provides: a confidential
symmetric client, standalone launch with a patient context, and the three SMART v2
permission classes the suite presents tokens for.

## The tokens

Six tokens, one per scope, obtained by one OAuth 2.0 **client-credentials**
request each, over TLS, verified against the round's CA.

| Test-data key (`connect.*`) | Client | Scope |
|---|---|---|
| `read-patient-scope-allowed` | `isik-connect-patient-patient-rs` | `patient/Patient.rs` |
| `read-patient-wildcard-scope-allowed` | `isik-connect-patient-wildcard-rs` | `patient/*.rs` |
| `read-patient-scope-in-user-context-allowed` | `isik-connect-user-patient-rs` | `user/Patient.rs` |
| `read-patient-wildcard-scope-in-user-context-allowed` | `isik-connect-user-wildcard-rs` | `user/*.rs` |
| `read-patient-scope-in-system-context-allowed` | `isik-connect-system-patient-rs` | `system/Patient.rs` |
| `read-wildcard-scope-in-system-context-allowed` | `isik-connect-system-wildcard-rs` | `system/*.rs` |

Defined once, in `connect_token_clients()` in
[scripts/lib.sh](../../scripts/lib.sh), and read from there by every consumer.

**Six clients, because a token must carry exactly one scope.** The module tests
what each scope does and does not grant, so a token holding two of them would make
a refusal scenario pass for the wrong reason.

**The patient in context is configured, not launched.** There is no SMART app
launch in these scenarios, so the launch context cannot be established by one:
the authorization server stamps `CONNECT_PATIENT_IN_CONTEXT` into the tokens it
issues, and that value is reproduced from the suite's own test data
(`testdata/v3/Stufe5/connect.yaml`, `read-patient-in-context-id`). `make tokens`
verifies that the minted tokens carry it and fails if the two have drifted apart.

### Tokens are credentials

An access token carries the full authority of its scope until it expires.
Accordingly:

* **No token value is passed on a command line, printed, or logged.** The
  responses are written to a private temporary directory removed on exit.
* The lifetime is one hour (`AS_TOKEN_LIFESPAN=3600`) — long enough for a full
  `Connect` run plus the retries a failed run needs, short enough that a token
  left in a working copy is useless by the time anyone finds it.
* **The only lasting record of a token outside the staged test-data file is its
  SHA-256**, in `run/tiger/TOKENS.txt`, with the minting parameters beside it.
* A round's published evidence withholds the token values. The bundle *states*
  that no access token and no realm secret of this round is readable in it, and
  `assert_no_published_credentials()` in [scripts/lib.sh](../../scripts/lib.sh)
  proves it over every path three scripts write, rather than claiming it. The
  official archives are the one exempt subtree, and they stay byte-for-byte.
  See [07-reports.md](07-reports.md#what-is-withheld-and-what-is-not).

This is the exception to the rig's otherwise deliberate policy of publishing a
round's credentials in full
([09-known-limitations.md](09-known-limitations.md#credential-documentation)).
The other exception is the realm's shared client secret. Both are withheld for
the same reason: they are credentials of a kind the rig cannot make harmless by
destroying the instance, because a token is useful to whoever holds it for an
hour wherever they hold it.

### Order matters

[30-render-config.sh](../../scripts/30-render-config.sh) re-stages `run/testdata/`
from the official contract and therefore **restores the placeholders**. Always
render first, then mint. `make tokens` depends on the rendered configuration for
exactly this reason, and `60-run-tests.sh` refuses to start a Connect run whose
staged file still carries a placeholder, or a token with less than
`CONNECT_TOKEN_MIN_REMAINING` seconds of life left. A token that expires mid-run
would turn product behaviour into 401s that look like refusals.

`run/tiger/TESTDATA-PARITY.txt` reports `connect.yaml` as **PARAMETERISED** rather
than IDENTICAL, and lists exactly which keys were given a value — so the one
test-data file a round modifies is named, and the modification is bounded to six
keys ([05-testdata.md](05-testdata.md#contract-parity)).

## Why `Connect` is a separate invocation

`make test-connect` runs a **second** Tiger configuration,
[tiger/v3/tiger-isik-stufe5-connect.yaml.tmpl](../../tiger/v3/tiger-isik-stufe5-connect.yaml.tmpl),
with the tag expression `@Stufe5 and @Connect and (not @Optional)`. One
configuration cannot serve both kinds of module, for two independently sufficient
reasons.

**1. A route-level `authentication:` block replaces the feature file's own
header.** Measured, 2026-11-13: the `Connect` features were run against the
ordinary Stufe 5 configuration with placeholder tokens. The recorded traffic
contained **no `Bearer` at all** — only the route's Basic credential — and the SUT
answered 200 to every request, including `GET /Encounter?_count=10`, which the
feature requires to be refused with 40x. Four of that feature's six scenarios
"passed". A conformance statement obtained that way would be an artefact of the
test harness, not a property of the system under test. The Connect route therefore
carries **no `authentication:` block**, deliberately, and that is the whole point
of the second file.

**2. The module has to address the HTTPS listener,** for the `PrelimTokenCheck`
reason above, while the other nine keep the `:80` listener their previous results
were taken on.

The Connect configuration differs from the ordinary Stufe 5 one in three further
respects, each annotated `[DEVIATION-CONNECT]` at its site so an inspector can
diff them:

| Difference | Why |
|---|---|
| `servers.fhirserver.source` is the HTTPS URL | Reason 2 above |
| `healthcheckUrl` is over HTTPS | So the check also proves the TLS listener is up before the run starts. Measured: Tiger performs it against a certificate issued by the round's local CA without further configuration |
| One `additionalConfigurationFiles` entry, not eleven | The Connect feature files reference `${connect.*}` and nothing else, and this configuration runs no other module |

The rendered file is mounted at `/app/tiger-isik-stufe5.yaml`, which is the path
the upstream `stufe5` Maven profile reads; the distinct name exists only on the
host.

## It does not narrow the official run

`make test-connect` does **not** replace or narrow `make test-stufe5`. The
complete official scope `@Stufe5 and (not @Optional)` keeps running in full —
including these same 32 `Connect` cases — because that archive is the one the
certification process asks for: the suite's own README requires "the artifact
`target/test-report.zip`", in the singular, and a round that submitted a
partitioned scope would be submitting something the certificate does not
describe. The procedure itself is in
[../de/02-bestaetigungsverfahren.md](../de/02-bestaetigungsverfahren.md).

So the `Connect` module runs **twice** in a round with `CONNECT_ENABLE=true`, and
a round holds two Stufe 5 archives:

| Archive | `scopePartition` in its `run.json` | What it is |
|---|---|---|
| `stufe5/` | `complete` | The complete official scope. **The submission artefact.** Its `Connect` cases ran without a bearer token |
| `connect/` | `connect-only` | The same level's `Connect` module a second time, on real access tokens. **The evidence that the tokens were validated** |

**The supersede rule.** For the 32 `Connect` cases the variant's verdicts
*supersede* the complete run's. They are never summed, and never averaged: the
complete run measured those cases without the credential they are about, so its
verdicts for them are not a statement about token handling. Everything the
pipeline derives says which archive it read:

* the merged level report records `scopePartition: complete+connect-superseded`;
* every case record names its `measuredIn` archive;
* `measurements[]` keeps each archive's own figures, unmerged, so the two can
  still be read separately.

`CASE-EVIDENCE.md` reads each case from the archive its `measuredIn` names, which
is why a `Connect` clause quotes an exchange that carries a bearer token while a
`Basis` clause quotes one that carries the route's Basic credential
([07-reports.md](07-reports.md#the-bundle)).

## Running it

Set `CONNECT_ENABLE=true` in `.env` before `make resolve`, then:

```
make up                    # also issues run/tls/* and starts the authorization server
make seed
make tokens                # mint the six tokens into the staged test data
make test-stufe5           # the complete official scope, unchanged
make test-connect          # the Connect module again, on real tokens
make report
```

`make test` does all of it in the right order by itself: the levels first, then —
only if `connect_enabled` — the mint and the Connect invocation, last, so that the
tokens are obtained as late as possible and their one-hour life is not spent
re-walking the preparation chain.

**One `RUN_ID` for all of it.** The two invocations and the report must land in
one bundle, so the run identity is given once and honoured by each step:

```
RUN_ID=run-20261116-1200 make test-stufe5
RUN_ID=run-20261116-1200 make test-connect
RUN_ID=run-20261116-1200 make report
```

Without it each invocation would mint its own run id and the supersede rule would
have nothing to apply ([06-execution.md](06-execution.md#the-run-identity)).

`scripts/55-mint-tokens.sh --verify` reports what is staged now — each key, its
scope, and the remaining life of the token in it — and changes nothing. There is
no `make` target for it, deliberately: it is a read-only check, and a target
whose name resembled `make tokens` would be too easy to invoke by accident
immediately before a run.

Both token targets refuse to do anything with `CONNECT_ENABLE=false`: there is no
authorization server to mint against, and failing loudly is the only outcome that
cannot be mistaken for a measurement.

### Additional hardware

A round with `CONNECT_ENABLE=true` needs about **1.1 GiB of host memory more**
than one without — the authorization server's limit plus the Web Gateway's TLS
listener. IRIS's own default of 25 % of host memory is unchanged by it, so the
extra memory has to be real and not borrowed from what IRIS already claims. **A
host sized for a round without this overlay is not automatically large enough for
one with it.** The sizing table is in
[../../INSTALL.md](../../INSTALL.md#hardware).

Note also that this rig does not configure HugePages, which InterSystems
recommends for every host running IRIS. Without them IRIS's real memory
consumption is materially higher than its configured shared-memory size suggests,
and the 1.1 GiB above is on top of that. Configuring HugePages is a host-level
decision made outside this repository, like the firewall rules
([09-known-limitations.md](09-known-limitations.md#isolation-is-not-enforced-by-this-repository)).

## What a `Connect` result does and does not prove

| Does prove | Because |
|---|---|
| That the SUT validated a real access token issued by an independent authorization server, and enforced its scope | Validation, scope evaluation and compartment filtering run in the product, on the request path, as shipped. The rig configures the trust relationship and nothing else |
| That the endpoint's advertised SMART configuration matches what the deployment provides | The document is assembled by the product from the authorization server's metadata plus the configured capability list |
| That the measurement is reproducible | Six scopes, one client each, a recorded SHA-256 per token, a recorded CA fingerprint, and a `curl` form per case in `CASE-EVIDENCE.md` |

| Does not prove | Why |
|---|---|
| Anything about the SMART App Launch flow | The suite states it does not evaluate the flow; it requires a token. No authorization-code exchange, no app launch, no consent step is exercised here |
| Anything about a customer's own identity provider | The result is specific to tokens of this shape from this server. A different provider's token differs in claims, signing algorithm and discovery metadata |
| That the default `SearchFiltering` posture is the right one for a customer | It is the installer's default and it is not changed here. Which of the two postures a deployment wants is a deployment decision, and the measured consequence of each is recorded rather than recommended |
| Conformance of the `Connect` module in the submitted archive | The submitted `stufe5/official/test-report.zip` measured these cases *without* a bearer token. The `connect/` archive is the one that measured them with; it is evidence for a reader, not a substitute submission |

The last row is the one most easily misread, and it is the reason the supersede
rule is machine-recorded rather than left to prose.
