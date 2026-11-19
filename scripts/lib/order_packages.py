#!/usr/bin/env python3
"""Verify the package plan against the unpacked packages and resolve load order.

Why this exists
---------------
``HS.FHIRMeta.Load.NpmLoader.importPackages(packageDirList, force)`` imports a
``%List`` of unpacked package directories. A package's dependencies must already
be present - either imported by an earlier call, or positioned earlier in the same
list. The refv plugin configs give a flat dependency *set* with no ordering, so
the order has to be derived, and the only on-disk statement of what depends on
what is each package's own ``package.json``::

    { "name": "de.basisprofil.r4", "version": "1.5.4",
      "dependencies": { "hl7.fhir.r4.core": "4.0.1" } }

This script therefore does two things, both of which need the packages unpacked:

1. Checks that every package the plan names exists on disk with a ``package.json``
   at its root. Failing here costs one line; failing inside IRIS leaves a
   half-installed namespace to clean up.
2. Topologically sorts each endpoint's import list, dependencies first, and writes
   the result back into ``package-plan.json`` as ``loadOrder``.

Cross-version dependency edges
------------------------------
``package.json`` states dependencies as ``name: version``, but the version it names
is not always the version the validator pinned - e.g. ``de.gematik.isik#5.1.2``
declares ``hl7.fhir.uv.sdc: 3.0.0`` while the plugin's dependency list may carry a
different revision of some other package. An edge is therefore drawn to the
pinned version of that *name* within the same endpoint, not to the literal
``name#version`` from ``package.json``. Dependencies on names absent from the
endpoint's set are ignored rather than fabricated: the validator's dependency list
is authoritative about what is needed, and inventing a package the plan does not
contain would silently widen the environment.

Unresolvable dependency declarations
------------------------------------
Ordering is not enough. IRIS resolves each declared dependency by *exact id* at
import time, so a declaration that names a semver range, an unpublished version,
or a package this project excludes aborts the whole import. Those declarations are
rewritten in the unpacked ``package.json`` files and every change is recorded in
the plan under ``dependencyRewrites`` - see
:meth:`UnpackedPackages.make_dependencies_resolvable`, which documents each
case and its owner.

Cycles
------
FHIR packages should form a DAG. A cycle is reported rather than worked around,
because breaking one arbitrarily would produce an import order that happens to
work on one release and fails on the next.

Example-only packages are not loaded into the SUT
-------------------------------------------------
``de.gematik.isik#5.1.2`` depends on ``hl7.fhir.r4.examples#4.0.1``, which the
reference validator legitimately needs. It must not be loaded into IRIS:

* its ``package.json`` declares ``"type": "fhir.examples"`` - it is an example
  corpus, not an implementation guide;
* it is ~198 MB across 5310 files, and every one of its 658 StructureDefinitions
  repeats a canonical already defined by ``hl7.fhir.r4.core#4.0.1``, so importing
  it adds a large duplicate-canonical set to the endpoint's definition map while
  contributing nothing the SUT must conform to;
* none of the ISiK profiles the suite asserts against are supplied by it.

Packages whose declared ``type`` is in ``EXCLUDED_PACKAGE_TYPES`` are therefore
dropped from both the import list and the endpoint package list, and recorded in
the plan under ``excludedPackages`` with the reason - a declared property of the
environment, documented in ``docs/en/03-sut-iris.md``, rather than a silent
omission. They stay in ``vendor/packages/`` and in ``MANIFEST.txt``, because they
are part of what the validator uses and thus part of the provenance record.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys

# Invoked as a standalone script, so sys.path[0] is already this directory;
# the insert is for the case where a sibling imports this module instead.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from plan_packages import version_key  # noqa: E402
from tool_common import fail  # noqa: E402


# FHIR package `type` values that must not be loaded into the SUT. See the module
# docstring. "fhir.examples" is the only one in play with the current test suite
# release; the check is on the declared type rather than on a package name so that
# a future release adding another example corpus is handled without a code change.
EXCLUDED_PACKAGE_TYPES = {
    "fhir.examples": (
        "example corpus, not an implementation guide: its StructureDefinitions "
        "duplicate hl7.fhir.r4.core canonicals and it supplies no profile the "
        "test suite asserts conformance against. Used by the reference validator, "
        "not by the SUT."
    ),
}


# Why each class of rewrite happens, carried into the plan and from there into the
# generated documentation. Worded for a reader deciding who owns the finding: none
# of the three is an ISiK conformance failure of the SUT.
REWRITE_REASONS = {
    "wildcard": (
        "The dependent package declares a semver patch range, which the FHIR NPM "
        "package specification permits. IRIS's NpmLoader performs no semver "
        "resolution - it looks the declaration up as a literal package id - so the "
        "range is rewritten to the version this endpoint actually loads. The "
        "package is correct as published; the limitation is in the loader."
    ),
    "absent-version": (
        "The dependent package declares a version that was never published (the "
        "package registry returns 404 for it). An upstream defect in that "
        "implementation guide. Rewritten to the version this endpoint loads, which "
        "is the release the declaration was evidently meant to name."
    ),
    "excluded-from-sut": (
        "The dependency is a package this project deliberately does not load into "
        "the SUT (see excludedPackages). The edge is removed rather than "
        "repointed: substituting a different package would fabricate a dependency "
        "the guide does not declare."
    ),
}


def read_package_json(pkg_dir: str) -> dict:
    path = os.path.join(pkg_dir, "package.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        fail(f"{path}: not valid JSON ({exc})")


@dataclasses.dataclass(frozen=True)
class UnpackedPackages:
    """The unpacked package tree, the plan that names it, and what it declares.

    The rewrite below needs all three, which is why they are one record rather
    than three arguments: the plan states which packages each endpoint imports
    and in which order, the directory is where each ``package.json`` is read and
    written, and ``meta`` is that metadata as it now stands on disk - kept in
    step by the rewrite, so a later step reads the tree as it is and not as it
    was found.
    """

    plan: dict
    directory: str
    meta: dict[str, dict]

    def make_dependencies_resolvable(self) -> list[dict]:
        """Make every declared dependency resolvable by IRIS, and record each change.

        Why this is necessary
        ---------------------
        ``HS.FHIRMeta.Load.NpmLoader.coreVersion()`` walks the ``dependencies`` of
        every package being imported and resolves each entry with an exact-id
        ``HS.FHIRMeta.Storage.Package.GetById(name @ version)``. There is no semver
        resolution and no tolerance for an absent entry: the first unresolvable
        declaration aborts the entire import with ``ERROR #5809: Object to Load not
        found``, after the namespace has already been created. Established by dumping
        ``coreVersion()`` from irishealth 2026.2 and reproducing each abort.

        Four declarations in the ISiK closure are unresolvable that way, and they do
        not all have the same cause. The distinction matters because it decides who
        owns the finding, so it is carried through into the plan as ``kind``:

        ``wildcard``
            ``de.gematik.isik-{medikation,terminplanung,vitalparameter,
            dokumentenaustausch}#3.1.1`` declare ``de.gematik.isik-basismodul: 3.1.x``.
            A wildcard patch range is **valid** under the FHIR NPM package spec, so
            the packages are correct and the limitation is IRIS's: it does no semver
            resolution. Rewritten to the matching version actually loaded (3.1.1).

        ``absent-version``
            ``hl7.fhir.uv.subscriptions-backport.r4#1.1.0`` declares
            ``hl7.fhir.r4.core: 4.0.0``, a version HL7 never published (the registry
            returns 404; R4 is 4.0.1). An upstream defect in that IG. Rewritten to the
            loaded 4.0.1.

        ``excluded-from-sut``
            ``hl7.fhir.uv.sdc#3.0.0`` declares ``hl7.fhir.r4.examples#4.0.1``, which
            this project deliberately does not load into the SUT (see
            EXCLUDED_PACKAGE_TYPES). The edge is removed rather than repointed:
            substituting a different package here would be a fabrication.

        A dependency naming a *name* that is not loaded at all and is not excluded is
        left alone and reported, not guessed at - it would mean the plan is missing a
        package, which is a condition for a human to look at rather than for this
        function to paper over.

        What is rewritten
        -----------------
        The unpacked ``vendor/packages/<id>/package.json`` files, in place. Those are
        what IRIS reads at import time; the ``.tgz`` payloads in ``vendor/tgz/`` are
        **not** touched, so every SHA256 in ``MANIFEST.txt`` stays valid and continues
        to attest the bytes as published. Every change is recorded in
        ``dependencyRewrites`` in the plan, which is archived as evidence, so the
        modified state of the tree is a declared property of the round.
        """
        rewrites: list[dict] = []

        for level, bucket in self.plan["endpoints"].items():
            order = list(bucket["loadOrder"])
            loaded = set(order)
            by_name: dict[str, list[str]] = {}
            for pkg_id in order:
                by_name.setdefault(pkg_id.split("#", 1)[0], []).append(pkg_id)

            excluded_names = {e["id"].split("#", 1)[0]
                              for e in self.plan.get("excludedPackages", [])}

            for pkg_id in order:
                path = os.path.join(self.directory, pkg_id, "package.json")
                with open(path, encoding="utf-8") as fh:
                    doc = json.load(fh)
                deps = doc.get("dependencies") or {}
                changed = False

                for dep_name, dep_version in list(deps.items()):
                    if f"{dep_name}#{dep_version}" in loaded:
                        continue

                    if dep_name in excluded_names:
                        del deps[dep_name]
                        kind, target = "excluded-from-sut", None
                    else:
                        candidates = by_name.get(dep_name, [])
                        if not candidates:
                            # Not resolvable and not deliberately excluded: report and
                            # leave it, so the import failure names the real problem
                            # instead of this function having hidden it.
                            print(
                                f"  WARNING {level}: {pkg_id} depends on "
                                f"{dep_name}#{dep_version}, which is neither "
                                "loaded nor excluded - the IRIS import will "
                                "fail on it",
                                file=sys.stderr,
                            )
                            continue
                        # A wildcard range keeps the loaded version only if it matches
                        # the fixed part; with one candidate per name in practice, the
                        # highest is the match. `max` also covers the collision case,
                        # where several versions of a name are loaded.
                        target = max(
                            candidates, key=lambda c: version_key(c.split("#", 1)[1])
                        ).split("#", 1)[1]
                        # A version whose components are not all numeric is a range
                        # (`3.1.x`); one that is numeric throughout but absent from the
                        # registry is an upstream mistake. Different owners, so the
                        # distinction is recorded rather than flattened.
                        is_range = not all(p.isdigit() for p in dep_version.split("."))
                        kind = "wildcard" if is_range else "absent-version"
                        deps[dep_name] = target

                    changed = True
                    rewrites.append(
                        {
                            "level": level,
                            "package": pkg_id,
                            "dependency": dep_name,
                            "from": f"{dep_name}#{dep_version}",
                            "to": f"{dep_name}#{target}" if target else None,
                            "kind": kind,
                            "reason": REWRITE_REASONS[kind],
                        }
                    )

                if changed:
                    doc["dependencies"] = deps
                    tmp = path + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as fh:
                        json.dump(doc, fh, indent=2)
                        fh.write("\n")
                    os.replace(tmp, path)
                    self.meta[pkg_id] = doc

        return rewrites


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--plan", required=True, help="vendor/package-plan.json, modified in place")
    ap.add_argument("--packages-dir", required=True, help="vendor/packages")
    args = ap.parse_args()

    with open(args.plan, encoding="utf-8") as fh:
        plan = json.load(fh)

    # --- 1. Existence check --------------------------------------------------
    missing = []
    meta: dict[str, dict] = {}
    for pkg in plan["allPackages"]:
        pkg_id = pkg["id"]
        pkg_dir = os.path.join(args.packages_dir, pkg_id)
        pj = read_package_json(pkg_dir)
        if not pj:
            missing.append(pkg_id)
            continue
        # The directory name must agree with the package's own metadata, or the
        # endpoint would be configured with an id that does not exist in IRIS.
        actual = f"{pj.get('name')}#{pj.get('version')}"
        if actual != pkg_id:
            fail(
                f"vendor/packages/{pkg_id}/package.json declares {actual!r}. "
                "The directory name and the package metadata must agree; "
                "re-run 'make packages'."
            )
        meta[pkg_id] = pj

    if missing:
        print("  missing package directories:", file=sys.stderr)
        for m in missing:
            print(f"    vendor/packages/{m}", file=sys.stderr)
        print(
            "  These are named by the test suite's refv-plugins but were not\n"
            "  unpacked. Re-run 'make packages'; if it persists with\n"
            "  PACKAGE_SOURCE=simplifier, retry with PACKAGE_SOURCE=refv, which\n"
            "  uses the copies bundled in the image.",
            file=sys.stderr,
        )
        return 1

    # --- 1b. Drop packages that must not be loaded into the SUT --------------
    # See the module docstring. Recorded in the plan so the exclusion is visible
    # in the provenance record and can be reproduced in the documentation.
    excluded: dict[str, str] = {}
    for pkg_id, pj in meta.items():
        ptype = pj.get("type")
        if ptype in EXCLUDED_PACKAGE_TYPES:
            excluded[pkg_id] = EXCLUDED_PACKAGE_TYPES[ptype]

    plan["excludedPackages"] = [
        {
            "id": pkg_id,
            "type": meta[pkg_id].get("type"),
            "reason": reason,
            "note": (
                "Present in vendor/packages/ and in MANIFEST.txt because the "
                "reference validator uses it; not imported into IRIS."
            ),
        }
        for pkg_id, reason in sorted(excluded.items())
    ]
    for pkg_id, reason in sorted(excluded.items()):
        print(f"  EXCLUDED from the SUT: {pkg_id} ({meta[pkg_id].get('type')}) - {reason}",
              file=sys.stderr)

    # --- 2. Per-endpoint topological sort ------------------------------------
    for level, bucket in plan["endpoints"].items():
        want = [p for p in bucket["importPackages"] if p not in excluded]
        want_set = set(want)
        bucket["packages"] = [p for p in bucket["packages"] if p not in excluded]
        # name -> the version pinned for this endpoint. Several versions of one
        # name can be imported (see plan_packages.py), in which case a dependency
        # edge is drawn to all of them: any of them may be the one that satisfies
        # the dependent, and importing a superfluous dependency early is harmless
        # whereas importing one late is not.
        by_name: dict[str, list[str]] = {}
        for pkg_id in want:
            by_name.setdefault(pkg_id.split("#", 1)[0], []).append(pkg_id)

        edges: dict[str, set[str]] = {p: set() for p in want}
        for pkg_id in want:
            for dep_name in (meta[pkg_id].get("dependencies") or {}):
                for candidate in by_name.get(dep_name, []):
                    if candidate != pkg_id and candidate in want_set:
                        edges[pkg_id].add(candidate)

        # Kahn's algorithm, with ties broken alphabetically so the order - and
        # therefore the provenance record and the documentation - is stable
        # across runs on the same inputs.
        ordered: list[str] = []
        remaining = dict(edges)
        while remaining:
            ready = sorted(p for p, deps in remaining.items() if not (deps - set(ordered)))
            if not ready:
                cycle = ", ".join(sorted(remaining))
                fail(
                    f"dependency cycle among the {level} packages: {cycle}. "
                    "FHIR packages are expected to form a DAG; this needs a "
                    "human decision, so no order is guessed."
                )
            for p in ready:
                ordered.append(p)
                del remaining[p]

        bucket["loadOrder"] = ordered

        # The endpoint's declared package list, in the same dependency order, so
        # InstallInstance receives a list whose order matches the import order.
        endpoint_set = set(bucket["packages"])
        bucket["endpointPackageOrder"] = [p for p in ordered if p in endpoint_set]

        roots = [p for p in ordered if not edges[p]]
        print(f"  {level}: {len(ordered)} packages ordered "
              f"(first: {ordered[0]}, roots: {len(roots)})")

    # --- 2b. Make declared dependencies resolvable by IRIS -------------------
    # The load order is settled above, which is what this step reads.
    tree = UnpackedPackages(plan, args.packages_dir, meta)
    rewrites = tree.make_dependencies_resolvable()
    plan["dependencyRewrites"] = rewrites
    for r in rewrites:
        note = f"{r['from']} -> {r['to']}" if r["to"] else f"{r['from']} -> removed"
        print(f"  REWROTE {r['level']}: {r['package']} dependency {note} "
              f"({r['kind']})", file=sys.stderr)

    # --- 3. Write back -------------------------------------------------------
    tmp = args.plan + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(plan, fh, indent=2, sort_keys=False)
        fh.write("\n")
    os.replace(tmp, args.plan)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
