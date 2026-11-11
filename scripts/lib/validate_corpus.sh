#!/usr/bin/env bash
# =============================================================================
# validate_corpus.sh - run the suite's own FHIR reference validator over the
#                      rendered seed corpus, before anything is seeded
# =============================================================================
# Called by 50-seed.sh (stage 4 of 6); runnable by hand for the same effect:
#
#   scripts/lib/validate_corpus.sh build/fhir run/seed [vendor/requirements.json]
#
# Everything about the validation comes out of the pinned test-suite image: the
# gematik reference validator library, the ISiK validation-module plugins in
# /app/refv-plugins, and the FHIR packages those plugins declare. That is the
# point - the corpus is judged by the same validator, at the same version, that
# will judge the server's responses during the run. Nothing is installed on the
# host and nothing is downloaded; the container has no network.
#
# The classpath is resolved inside the image with `mvn -o dependency:build-classpath`
# against the image's own bundled ~/.m2 repository, rather than by globbing the
# repository: two versions of several dependencies are present there, and a glob
# would put both on the classpath.
#
# The third argument is the requirements corpus. A fabricated body carries no
# meta.profile, because the scenario that reads it passes the profile URL to the
# validation step - so the bodies most likely to be wrong are exactly the ones a
# meta.profile-driven check cannot see. The requirements corpus records, per
# fixture, which profiles the in-scope scenarios assert against it, and the map
# derived from it below lets those bodies be validated against the profile they
# will actually be judged by. It is optional only so that this script stays
# runnable before `make packages` has produced the corpus.
#
# Exit status: 0 if every body with a profile validated clean, 1 if any body did
# not, 2 if the check itself could not run. A caller that wants the check to be
# advisory should test the status rather than suppress it, so that the
# distinction between "clean" and "could not be checked" survives.
# =============================================================================
set -euo pipefail

CORPUS_DIR="${1:?usage: validate_corpus.sh <corpus-dir> <out-dir> [requirements.json]}"
OUT_DIR="${2:?usage: validate_corpus.sh <corpus-dir> <out-dir> [requirements.json]}"
REQUIREMENTS="${3:-}"

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${TESTSUITE_IMAGE:?TESTSUITE_IMAGE is not set}"
: "${TESTSUITE_VERSION:?TESTSUITE_VERSION is not set}"
SUITE_REF="${TESTSUITE_IMAGE}:${TESTSUITE_VERSION}"

CORPUS_ABS="$(cd "$CORPUS_DIR" && pwd)"
mkdir -p "$OUT_DIR"
OUT_ABS="$(cd "$OUT_DIR" && pwd)"

command -v docker >/dev/null 2>&1 || {
  printf 'validate_corpus.sh: docker is not available\n' >&2
  exit 2
}

# The asserted-profile map, derived in the host's Python rather than in the
# container, so that the container keeps its single job. Keyed
# `<Level>/<ResourceType>/<id>`, which is how the corpus is laid out, and NOT
# unioned across levels: the same fixture id is asserted against the v3 profile
# under Stufe3 and the v5 profile under Stufe5, and validating a Stufe 3 body
# against the Stufe 5 profile would manufacture a defect. Within a level the
# profiles are unioned - a fixture two scenarios assert differently must satisfy
# both, because the run will assert both against it.
ASSERTED_ARG=""
if [ -n "$REQUIREMENTS" ]; then
  [ -f "$REQUIREMENTS" ] || {
    printf 'validate_corpus.sh: %s does not exist\n' "$REQUIREMENTS" >&2
    exit 2
  }
  python3 - "$REQUIREMENTS" "${OUT_ABS}/asserted-profiles.json" <<'PY' || exit 2
import json, sys
src, dst = sys.argv[1], sys.argv[2]
reqs = json.load(open(src, encoding="utf-8"))
out = {}
for name, level in (reqs.get("levels") or {}).items():
    for res in level.get("resources") or []:
        if not res.get("inScope"):
            continue
        rtype, rid = res.get("resourceType"), res.get("id")
        if not rtype or not rid:
            continue
        have = out.setdefault(f"{name}/{rtype}/{rid}", [])
        for p in res.get("profiles") or []:
            if p not in have:
                have.append(p)
json.dump({k: v for k, v in sorted(out.items()) if v}, open(dst, "w", encoding="utf-8"),
          indent=1, ensure_ascii=False)
PY
  ASSERTED_ARG='--asserted /out/asserted-profiles.json'
fi

# --user: the report is written into a bind mount, and the image runs as a
# non-root user whose uid need not match the caller's.
docker run --rm \
  --network none \
  --user "$(id -u):$(id -g)" \
  -e "ASSERTED_ARG=${ASSERTED_ARG}" \
  -v "${CORPUS_ABS}:/corpus:ro" \
  -v "${SELF_DIR}/refv_preflight:/pf:ro" \
  -v "${OUT_ABS}:/out" \
  --entrypoint sh "$SUITE_REF" -c '
set -e
cd /app
# -Dmaven.repo.local, not $HOME/.m2: --user above replaces the image user, and
# the uid it is given has no home directory inside the container.
mvn -o -q -Dmaven.repo.local=/home/tiger-testsuite/.m2/repository \
    dependency:build-classpath -Dmdep.outputFile=/tmp/cp.txt >/dev/null
cp /pf/CorpusValidator.java /tmp/CorpusValidator.java
# $ASSERTED_ARG is deliberately unquoted: it is either empty or exactly the two
# words `--asserted <path>`, both of which this script chose.
java -Xmx3g -cp "$(cat /tmp/cp.txt)" /tmp/CorpusValidator.java \
     --plugins /app/refv-plugins \
     --corpus /corpus \
     --out /out/corpus-validation.json \
     $ASSERTED_ARG
'
