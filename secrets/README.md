# `secrets/`

Operator-supplied credentials. **Nothing in this directory is tracked by git**
(see `.gitignore`).

## Required

### `secrets/iris.key`

The InterSystems IRIS for Health licence key. Obtain it from your InterSystems
account team or the WRC. It is mounted read-only into the container at
`/usr/irissys/mgr/iris.key`.

```sh
cp /path/to/your/iris.key secrets/iris.key
```

Any file name that reads `iris.key` case-insensitively is accepted.

**Do not tighten the mode by hand.** `make up` sets the key's ownership and mode
itself, because a bind mount preserves the host inode's owner and mode while IRIS
in the container runs as uid 51773 — so a `0400` key owned by your host user is
invisible to it, and the resulting failure is silent and expensive: IRIS starts,
logs `LMF Error: No valid license key`, installs everything successfully over 30
minutes, and then fails every FHIR request with `ERROR #5915: Unable to allocate a
license` — recorded only in the audit log. See
[docs/en/02-setup.md](../docs/en/02-setup.md#the-licence-files-permissions-matter-more-than-they-look).

`make preflight` fails if this file is missing, and the bootstrap fails early —
before the 30-minute install — if IRIS does not report the licence as activated.

## Required: registry access

The official IRIS for Health image lives in the InterSystems container registry,
which requires authentication. Log in once on the deployment host:

```sh
docker login containers.intersystems.com
```

Use your WRC username and the **registry token** (not your WRC password) as the
password. `make preflight` verifies that a credential for
`containers.intersystems.com` is present in the Docker config.

## Generated: the two passwords

You do not have to supply these. `make resolve` generates each one it does not
already find, writes it here mode 0600, and reuses it on every later run of the
same deployment — so the rendered Tiger configuration, a manual `curl` and the
Management Portal all agree on one value.

Precedence for each: the value in `.env` wins, then the file here, then a freshly
generated one. Generated passwords look like `Kmrf-7xtq-Vhn3-Pd2s` — four groups
of four, no glyphs that can be misread (`O 0 I l 1`). The form is deliberate: it
satisfies IRIS's default `PasswordPattern` by construction, so the instance's own
password policy is left at the product default rather than being weakened, and it
is short enough to read off a screen and type into a browser. See `gen_password()`
in `scripts/lib.sh`.

### `secrets/sut.password`

The FHIR API user the test suite authenticates as (`SUT_USER`, default
`isikapi`), over HTTP Basic.

### `secrets/iris-mgmt.password`

The IRIS Management Portal account (`IRIS_MGMT_USER`, default `_SYSTEM`), for the
human tester. The bootstrap sets this password on the instance's predefined
accounts and marks them non-expiring; the official image ships them with expired
passwords, so without this the portal answers every login with a forced
password-change page.

## What is and is not protected here

These two passwords are **documented on purpose** — in `run/CREDENTIALS.md`, in
`make credentials` and `make status`, in section 1 of every generated
`analysis/results.md`, and verbatim in the `Authorization` headers the gap
analysis quotes. The rig is a lab instance brought up for one round of testing and
destroyed once the results have been verified; access to it is controlled
physically and at the network perimeter, not by these values. They are kept in
place — never empty, never absent — only so that the system under test remains a
faithful stand-in for a production deployment.

The one rule that does apply: **none of it reaches source control.** This whole
directory, `.env`, `.env.resolved`, `run/` and `reports/` are gitignored, and
`make check` verifies that mechanically. The files here are mode 0600 for the same
reason the passwords exist at all — production-like file modes on a rig that is
meant to behave like production.

`make clean` deliberately does not delete this directory, so a tester can still
run `make credentials` after tearing the environment down.
