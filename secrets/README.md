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

**What is expected.** A single small plain-text file, as InterSystems issues it —
typically distributed with a `.ISCkey` extension and a long descriptive file name.
Copy it here under the name `iris.key`; do not edit the contents, and do not
concatenate two keys into one file. Four properties of the key have to match the
round you intend to take, and the issued file name normally states all four:

- **Product** — an *IRIS for Health* key for an `irishealth` image. The key names
  the product it was issued for.
- **Platform family** — the container's architecture. Keys are issued per family;
  a `Container(Ubuntu-x64)` key and a `Container(Ubuntu-ARM64)` key are two
  different files, and on a mixed-architecture estate it is easy to copy the
  wrong one.
- **Capacity** — the core count the key licenses. The rig does not require any
  particular figure, but a round is only a performance statement about as many
  cores as the key licensed, so record it alongside the result.
- **Expiry date** — a date after the day you intend to run. **Check this before a
  round rather than during one:** a full round takes around an hour, and the rig
  has no way to distinguish a licence that lapsed mid-round from the product
  failing.

There is no evaluation or built-in key that makes this rig work. If a key has to
be replaced, that is a file copy into `secrets/iris.key` followed by `make up`;
nothing else in the rig depends on it.

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
password. The token is issued through the WRC; if you do not have one, your
InterSystems account team can tell you how to obtain it for your account.

`docker login` stores that credential in the host's own `~/.docker/config.json`,
which is outside this repository and must stay there — **do not** copy it into
`secrets/`, and do not put a registry token in `.env`. `make preflight` only
verifies that *some* credential for `containers.intersystems.com` is present in
the Docker config; it cannot and does not read it.

The gematik ISiK test suite image needs **no** credential: it is public on Docker
Hub and `make resolve` pulls it directly. See
[kit/README.md](../kit/README.md) for all three ways of obtaining the unit under
test, including side-loading an image you already hold as a file.

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
