#!/usr/bin/env python3
"""Derive missing test-data bodies from the ISiK package's own published examples.

Why this exists
---------------
``build_testdata.py`` renders one FHIR body per typed id key of the suite's
test-data contract. Test suite 3.0.0 added four Level 5 modules (Connect, ICU
Minimal, ICU Extended, Labor), and their contracts name 56 resources for which
this repository has no source body. Hand-writing 56 instances against 132
``StructureDefinition``s would make the rig's own test data the most likely
source of failure in a round whose entire product is a statement about the SUT.

``de.gematik.isik#5.1.3`` - the package the 3.0.9 validator plugin validates
against - ships 199 example instances under ``examples/``. For 52 of the 56
missing bodies exactly one of those examples claims the same profile the feature
files assert for that resource. Those 52 bodies are therefore *derived from the
specification's own examples* rather than invented here, which is the strongest
provenance available for them.

Earlier ISiK packages did not ship examples: ``de.gematik.isik#5.1.2``, used by
test suite 2.7.0, contains none. This tool is consequently only applicable from
5.1.3 onwards, and it says so rather than silently producing nothing.

Matching rule
-------------
A candidate is an example whose ``meta.profile`` contains a canonical that
``vendor/requirements.json`` records as asserted for that resource, compared
without the ``|version`` suffix. The match must be *unique*: a resource with no
candidate and a resource with two candidates are both reported and skipped, so
no choice is made here that the feature files do not already determine. On the
3.0.9 corpus every one of the 52 matches is unique.

Derivation
----------
Each derived body is the example with three kinds of change, and no others:

``id``, ``meta``, ``text``
    Removed. ``id`` is assigned by the renderer from the data key; ``meta.profile``
    is stamped by the renderer from ``requirements.json`` (a body that carries its
    own profile claim is reported by ``build_testdata.py`` as unsupported by any
    assertion); ``text`` is generated narrative and carries the example's own ids.

References
    Rewritten to the contract's placeholders, so that a derived Observation points
    at the patient, practitioner and encounter the rig actually seeds rather than
    at the package's example ids. The rewrite is table-driven (``REFERENCE_KEYS``)
    and keyed on the *location* of the reference in the body, never on the value it
    happens to have. A reference whose location is not in the table is a hard
    error naming the path, because silently leaving a package example id in place
    would seed a dangling reference and the suite would report it as a SUT defect.

Namespace
    Every key of a resource's ``keys`` list must lie in one contract namespace,
    because the placeholder written into the body names a namespace. A resource
    whose keys straddle namespaces is reported and skipped; such a body has to be
    written by hand or split.

Output
------
One file per derived resource at ``<out>/<ResourceType>-<id>.json``, pretty-printed
with sorted keys so that a later re-derivation against a newer package produces a
reviewable diff rather than a reordering. Existing files are left alone unless
``--overwrite`` is given.

The tool prints one line per resource and exits non-zero if any resource that was
requested could not be derived, so it is usable in a pipeline.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

# Where a reference may appear in a derived body, and which contract key supplies
# its target. The left-hand side is a dotted path with `[]` marking a list, as
# produced by walk() below; the right-hand side is a key name that is looked up in
# the resource's own contract namespace.
#
# The table is deliberately short: it covers exactly the reference locations the
# 3.0.9 ICU and Labor examples use. Extending it is a decision about what the rig
# seeds, so it belongs here in the open and not in a fallback rule.
REFERENCE_KEYS = {
    "subject": "observation-patient-id",
    "performer[]": "observation-practitioner-id",
    "encounter": "observation-encounter-id",
    "device": "observation-device-id",
}

# Removed from every derived body. See the module docstring.
DROP_TOP_LEVEL = ("id", "meta", "text")


def fail(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(2)


def load_examples(pkg_dir: pathlib.Path) -> dict[str, list[pathlib.Path]]:
    """Index every example instance in a package by the profiles it claims."""
    ex_dir = pkg_dir / "examples"
    index: dict[str, list[pathlib.Path]] = {}
    if not ex_dir.is_dir():
        return index
    for path in sorted(ex_dir.glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(doc, dict):
            continue
        for canonical in (doc.get("meta") or {}).get("profile") or []:
            index.setdefault(str(canonical).split("|")[0], []).append(path)
    return index


def rewrite_references(doc, namespace: str, available: set[str]):
    """Replace every Reference.reference with the contract placeholder for its path.

    Returns the list of paths that REFERENCE_KEYS does not cover and the list of
    paths whose key is absent from the resource's namespace. Both are reported by
    the caller; neither is guessed around.
    """
    unknown_paths: list[str] = []
    missing_keys: list[tuple[str, str]] = []

    def walk(node, path: str) -> None:
        if isinstance(node, dict):
            for key, value in list(node.items()):
                if key == "reference" and isinstance(value, str):
                    target_type = value.split("/")[0]
                    data_key = REFERENCE_KEYS.get(path)
                    if data_key is None:
                        unknown_paths.append(f"{path}.reference = {value!r}")
                        continue
                    if f"{namespace}.{data_key}" not in available:
                        missing_keys.append((path, f"{namespace}.{data_key}"))
                        continue
                    node[key] = f"{target_type}/{{{{{namespace}.{data_key}}}}}"
                else:
                    walk(value, f"{path}.{key}" if path else key)
        elif isinstance(node, list):
            for item in node:
                walk(item, f"{path}[]")

    walk(doc, "")
    return unknown_paths, missing_keys


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--requirements", required=True,
                    help="vendor/requirements.json of the pinned suite")
    ap.add_argument("--level", required=True, help="e.g. Stufe5")
    ap.add_argument("--package", required=True,
                    help="unpacked ISiK package directory, e.g. "
                         "vendor/packages/de.gematik.isik#5.1.3")
    ap.add_argument("--contract", required=True,
                    help="directory of the level's contract YAML files, used only "
                         "to confirm that a placeholder's key exists")
    ap.add_argument("--src", action="append", default=[],
                    help="an existing test-data root; a resource that already has "
                         "a body in one of these is left alone (repeatable)")
    ap.add_argument("--out", required=True,
                    help="output directory, e.g. testdata/fhir/v3/Stufe5")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    import yaml  # late, so --help works without it

    reqs = json.loads(pathlib.Path(args.requirements).read_text(encoding="utf-8"))
    level = reqs.get("levels", {}).get(args.level)
    if level is None:
        fail(f"{args.requirements} records no level {args.level!r} "
             f"(it has {', '.join(sorted(reqs.get('levels', {}))) or 'none'})")

    pkg_dir = pathlib.Path(args.package)
    if not pkg_dir.is_dir():
        fail(f"{pkg_dir} is not a directory")
    examples = load_examples(pkg_dir)
    if not examples:
        fail(f"{pkg_dir}/examples/ holds no example instance claiming a profile - "
             f"this package cannot be a source of derived bodies (de.gematik.isik "
             f"began shipping examples at 5.1.3)")

    # Every qualified key the contract defines, so a placeholder is only written
    # when the key behind it really exists.
    available: set[str] = set()
    contract = pathlib.Path(args.contract)
    tiger_basekeys: dict[str, str] = {}
    for path in sorted(contract.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            continue
        # The namespace of a contract file is its Tiger baseKey, not its file
        # name (icu-minimal.yaml -> icuminimal). Derive it from the keys the
        # requirements already carry rather than restating the mapping here.
        ns_candidates = {k.split(".", 1)[0] for row in level.get("resources", [])
                         for k in row.get("keys", [])}
        for ns in ns_candidates:
            if ns.replace("-", "") == path.stem.replace("-", ""):
                tiger_basekeys[path.stem] = ns
        ns = tiger_basekeys.get(path.stem, path.stem)
        for key in data:
            available.add(f"{ns}.{key}")

    out_dir = pathlib.Path(args.out)
    src_roots = [pathlib.Path(s) for s in args.src]

    def has_source(resource_type: str, rid: str) -> bool:
        name = f"{resource_type}-{rid}.json"
        for root in src_roots:
            if (root / args.level / name).is_file() or (root / "common" / name).is_file():
                return True
        return False

    derived = skipped = problems = 0
    for row in sorted(level.get("resources", []),
                      key=lambda r: (r["resourceType"], r["id"])):
        rtype, rid = row["resourceType"], row["id"]
        if not row.get("inScope", True):
            continue
        if has_source(rtype, rid):
            continue
        target = out_dir / f"{rtype}-{rid}.json"
        if target.is_file() and not args.overwrite:
            continue

        candidates = sorted({p for canonical in row.get("profiles", [])
                             for p in examples.get(canonical, [])})
        if not candidates:
            print(f"  skip  {rtype}/{rid}: no example in {pkg_dir.name} claims "
                  f"{', '.join(row.get('profiles') or ['any profile'])
                     if row.get('profiles') else 'a profile the features assert'}")
            skipped += 1
            continue
        if len(candidates) > 1:
            print(f"  skip  {rtype}/{rid}: {len(candidates)} examples claim the "
                  f"same profile ({', '.join(p.name for p in candidates)}) - "
                  f"the choice is not determined by the feature files")
            skipped += 1
            continue

        namespaces = {k.split(".", 1)[0] for k in row.get("keys", [])}
        if len(namespaces) != 1:
            print(f"  skip  {rtype}/{rid}: its keys lie in "
                  f"{len(namespaces)} namespaces ({', '.join(sorted(namespaces))}), "
                  f"so no single placeholder namespace applies")
            skipped += 1
            continue
        namespace = namespaces.pop()

        source = candidates[0]
        doc = json.loads(source.read_text(encoding="utf-8"))
        for field in DROP_TOP_LEVEL:
            doc.pop(field, None)
        unknown, missing = rewrite_references(doc, namespace, available)
        if unknown or missing:
            for u in unknown:
                print(f"  FAIL  {rtype}/{rid}: reference at an unhandled location: "
                      f"{u} - extend REFERENCE_KEYS in {os.path.basename(__file__)}")
            for path, key in missing:
                print(f"  FAIL  {rtype}/{rid}: {path} needs {key}, which the "
                      f"contract does not define")
            problems += 1
            continue

        rendered = json.dumps(doc, indent=2, sort_keys=True,
                              ensure_ascii=False) + "\n"
        if args.dry_run:
            print(f"  would write {target} (from {source.name}, namespace "
                  f"{namespace})")
        else:
            out_dir.mkdir(parents=True, exist_ok=True)
            target.write_text(rendered, encoding="utf-8")
            print(f"  write {target.name}  <- {source.name}  [{namespace}]")
        derived += 1

    print(f"{args.level}: {derived} derived, {skipped} without a unique example, "
          f"{problems} with a problem")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
