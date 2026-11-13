# Obtaining the unit under test

This repository contains **no product images and no installation kits**, and it
never will. It contains the machinery to obtain them with your own credentials,
and — for the case where only a kit is available to you — to build an image from
one. This file is the whole of that subject.

Three routes exist. They are not equivalent, and the order is the order of
preference.

| | Route | What a round on it can claim |
|---|---|---|
| 1 | Pull a published image from `containers.intersystems.com` | the registry digest identifies the exact product build; fully citable |
| 2 | Load an image you already hold as a local file | the image's content ID identifies it; citable, provided the file's own provenance is stated |
| 3 | Build an image from an installation kit, here | identifies the **kit**, not a published product image; see [What a self-built image is not](#what-a-self-built-image-is-not) |

---

## 1 Pull a published image (the normal route)

Images of InterSystems IRIS, IRIS for Health and the Web Gateway are
distributed through the InterSystems Container Registry at
**`containers.intersystems.com`**. Access is by your own InterSystems
credentials; the registry requires a WRC account and a **registry token**
rather than your account password. `secrets/README.md` says what that token is
and where it comes from.

```sh
docker login containers.intersystems.com     # username + registry token
cp /path/to/iris.key secrets/iris.key        # see secrets/README.md
cp .env.example .env                         # set IRIS_VERSION / MODE
make resolve                                 # pulls, records the digest
```

`make resolve` does the fetching, for every image the stack needs:

- the SUT and the Web Gateway, from `containers.intersystems.com`, per
  `IRIS_IMAGE` / `WEBGATEWAY_IMAGE` in `.env`;
- the gematik ISiK test suite, from Docker Hub as
  `gematik1/isik-testsuite:<version>`, which needs no credential. That image is
  gematik's, is not redistributed here, and is pinned by
  `TESTSUITE_VERSION` — see `docs/en/04-testtool.md`.

It then writes `.env.resolved`, which records the registry digest of everything
it pulled. That file is the round's provenance record and is **not** committed.

## 2 Side-load an image you already hold

A product image supplied to you as a file on the local file system is fully
supported, and the rig needs no special configuration for it. Load it, then
name the tag it carries:

```sh
docker load -i /path/to/image.tar
docker images --digests          # note the tag and the digest or image ID
```

```ini
# .env
MODE=pinned
IRIS_IMAGE=<the repository part of the loaded tag>
IRIS_VERSION=<the tag part>
```

`make resolve` tries the registry first and falls back to the local copy, so a
side-loaded image needs no flag: `scripts/10-resolve-versions.sh` records the
image's `source` as `side-loaded` and writes its content ID where a registry
digest would otherwise go. `make status` and every round bundle then carry that
instead, and a report reading `side-loaded` is telling the truth — a local
image file is only as traceable as whoever produced it says it is, so **state
in the report where the file came from.** The rig cannot establish that for
you.

Note the one trap: a *floating* tag such as a `…-preview` tag can be
overwritten by any later `docker pull`, including one a compose pull policy
triggers. If a round must stay re-measurable, retag the loaded image under a
name nothing will pull:

```sh
docker tag <loaded tag> local/irishealth:<something unambiguous>
```

## 3 Build an image from an installation kit

Use this only when the build you have to test is not available to you as a
container image but is available as an installation kit.

### Getting a kit

Installation kits are distributed through **`downloads.intersystems.com`**,
using your own InterSystems credentials. Which kits a given licence entitles
you to is a question for your InterSystems account team or the WRC. A kit you
already hold locally works identically; the build never goes to the network for
it.

You need a **container** kit — the platform segment of the file name begins with
`docker`, e.g. `…-dockerubuntux64.tar.gz`. A plain server kit ships no
`dist/dev/Container/imageBuildSteps.sh`, the step that quiesces the databases so
the `IRIS.DAT` files in the image are consistent, and the build refuses to
proceed without it rather than produce an image whose databases IRIS considers
to have been shut down uncleanly.

### Building

```sh
make image-from-kit \
    KIT=/path/to/IRISHealth-<version>-dockerubuntux64.tar.gz \
    SUPERVISOR_REF=containers.intersystems.com/intersystems/irishealth:<tag>
```

`scripts/02-build-image-from-kit.sh` reads everything it needs rather than
taking it on faith:

| | Read from |
|---|---|
| the build string, which becomes the image tag | `<kit>/dist/csp/buildver` |
| the product name (`IRISHealth`, `IRIS`) | `<kit>/.product` |
| the platform | the last segment of the kit directory's name |
| the base image | `ID` and `VERSION_ID` in `/etc/os-release` inside `SUPERVISOR_REF` |
| the kit's sha256, recorded in the image labels | the tarball itself |

It extracts the kit under `run/kit-build/` (several GB; `.gitignore` excludes
`run/`, and `make clean` leaves it alone), builds, and removes the extraction
again unless `KEEP_EXTRACT=1`. The IRIS build takes 10 to 20 minutes. Tags are
`local/<product>:<build string>` and `local/webgateway:<build string>`;
`IMAGE_PREFIX` and `IMAGE_TAG` override that. `COMPONENT=iris` or
`COMPONENT=webgateway` builds one of the two instead of both. The script ends
by printing the `.env` lines to set and the content ID of each image it built.

### What `SUPERVISOR_REF` is for, and why it is mandatory

**An installation kit ships no container supervisor.** `/iris-main`, `/tini`
and `/irisHealth.sh` exist only inside InterSystems' own images, and this rig
requires `iris-main` specifically, because it passes
`--check-caps false -a <bootstrap>` as the container command. `SUPERVISOR_REF`
names a published image of the **same release line** to copy those three files
from.

What that substitutes is bounded and worth stating precisely: `iris-main`
performs process supervision, startup and shutdown orchestration, durable
`%SYS` setup and password-file handling. It implements no FHIR behaviour and no
HTTP behaviour, which is what a conformance round measures. It is not nothing,
though — see the next section.

`SUPERVISOR_REF` also determines the base image, which is not cosmetic: a kit
installs onto the distribution it was built for, and the wrong one is not a
build failure but a subtly different product, with a different libc, a different
OpenSSL and a different Apache. Reading it out of a published image of the same
line is a measurement; a hard-coded mapping would be a guess that goes stale.
`BASE_IMAGE=<image>` overrides it when you know better.

### The Web Gateway

`kit/Dockerfile.webgateway` builds the gateway from the same kit, through the
kit's own unattended installer (`dist/csp/GatewayInstall`). The rig does not
need it — it defaults to the published `webgateway` image, which is the better
front end for a round of record. Build it either because no published gateway
image is available to you for the line under test, or deliberately, to
establish by measurement whether the front end's provenance affects the verdict.
Where both are available, let the published image carry the round of record and
the self-built one carry a comparison.

Before trusting such a comparison, check that the four binaries which implement
the routing and the IRIS connection are byte-identical between kit and published
image:

```sh
for f in CSPa24.so CSPa24Sys.so CSPnsd CSPcgi; do
  sha256sum "<kit>/dist/csp/$f"
  docker run --rm --entrypoint sha256sum <published-webgateway> "/opt/webgateway/bin/$f"
done
```

Expect the *designation strings* to differ even where the binaries do not: a kit
records its own build string in `dist/csp/buildver`, a published image records
that image's build string in `/opt/webgateway/buildver`. Differing designations
with identical binaries are comparable. **Differing binaries are not**, and must
be reported as a difference rather than explained away.

---

## What a self-built image is not

It is not a published InterSystems product image, and no round taken on one may
be reported as though it were. Both Dockerfiles label it
`de.intersystems.isik.not-published-by-intersystems="true"` so that the claim
cannot be made by accident, and the build script prints the two sentences a
report has to carry.

Three specific limits:

1. **The digest differs, and will differ again.** A self-built image's content
   ID depends on the base image, the Ubuntu package versions resolved on the
   build day, and the build order. Rebuilding the same kit tomorrow yields a
   different ID. A round of record taken on a self-built image is therefore
   reproducible only for as long as that image is retained: `docker save` it,
   and keep it.
2. **The container supervisor is a different build from the rest.** See
   `SUPERVISOR_REF` above. The DEVIATION note at the head of
   `kit/Dockerfile.iris` states it in full and should be read before the result
   is quoted anywhere.
3. **The third-party package set is reconstructed, not inherited.** The apt list
   in `kit/Dockerfile.iris` is `apt-mark showmanual` taken from a published
   image, minus what the base already carries. `temurin-11-jdk` is load-bearing:
   IRIS's FHIR profile validator runs on the JVM, and a round on an image
   without it reports validator failures as conformance gaps. If a future
   release line changes its prerequisites, this list is where it will show.

Every other choice in the two Dockerfiles comes from one of three sources, each
named at the point of use, and none from inference:

1. *Running InterSystems Products in Containers*, section "Required Environment
   Variables" — the six `ISC_PACKAGE_*` variables an image installed from a kit
   must carry, and the note on `IRISSYS`.
2. `/usr/irissys/parameters.isc` read out of a published image of the same
   release line — InterSystems' own recorded answer set for an image build of
   that line, which the install reproduces.
3. `docker inspect` of that same published image — user, environment, exposed
   ports, working directory, entrypoint and health check.

---

## Credentials

None are in this repository and none may be added to it. What the routes above
need, and where each comes from, is in **`secrets/README.md`**: the IRIS licence
key, the container registry token, and the per-deployment passwords the rig
generates for itself. `make check` verifies mechanically that no key, no
password and no `.env` has been committed.
