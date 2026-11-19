#!/usr/bin/env python3
"""Derive the per-endpoint FHIR package plan from the test suite's own validator
plugins.

Why this exists
---------------
The IRIS endpoints must be loaded with *exactly* the FHIR packages that the
Gematik reference validator embedded in the ISiK test suite validates responses
against. If they differ, the suite reports profile failures that are artefacts of
a version skew rather than genuine gaps in IRIS - the worst possible outcome for
an exercise whose entire product is a gap analysis.

The authoritative statement of those packages is the set of
``refv-plugins/valmodule-<id>-refv-plugin.zip`` archives inside the test suite
image. Each contains a ``config.yaml`` of the form::

    id: "isik3-basismodul"
    validation:
      dependencyLists:
      - fhirPackage: "de.gematik.isik-basismodul#3.1.1"
        dependencies:
        - "de.basisprofil.r4#1.4.0"
        - "hl7.fhir.r4.core#4.0.1"
    snapshotDependencies:
    - "hl7.terminology.r4#5.3.0"

``id`` is also the validation-module name that the feature files reference (steps
of the form ``... is a valid isik3-basismodul resource ...``), which is what makes
scenario -> validation module -> FHIR package an automated lookup in the gap
report rather than a hand-maintained table.

Level assignment
----------------
Plugin ids are prefixed ``isik3-`` for ISiK Stufe 3 modules and are exactly
``isik5`` for Stufe 5. That prefix is the only signal needed to decide which of
the two IRIS endpoints a package belongs to, and it is checked rather than
assumed: an unrecognised id is a hard error, because silently dropping a module
would silently narrow the conformance run.

Two versions of one package
---------------------------
Several dependency lists pin two versions of the same package - e.g.
``isik3-dokumentenaustausch`` needs both ``hl7.terminology.r4#4.0.0`` and
``#5.0.0``, and ``isik5`` needs ``hl7.terminology.r4`` at 5.0.0, 6.3.0 and 6.5.0.
That is legal for the validator, which resolves canonicals across its whole
dependency set, but it is a genuine ambiguity for an IRIS endpoint, whose package
list feeds a single canonical->definition map. Rather than guess, every such
collision is recorded in the plan under ``versionCollisions`` and reported, so
it appears in the documentation as a declared property of the environment instead
of an invisible coin flip. All versions are loaded (IRIS tolerates several
versions of a package being *imported*); the endpoint's package list keeps only
the highest, which is the version whose profiles the validator will actually
resolve for the newest content.

``snapshotDependencies`` names packages the validator uses to build snapshots but
does not ship a payload for. They are recorded for documentation and excluded from
the load plan by default - they contribute no profiles the SUT must know about.

One exception is forced by IRIS and is handled explicitly: if a *loaded* package
declares a snapshot-only package in its own ``dependencies``, it must be loaded
too, because ``HS.FHIRMeta.Load.NpmLoader`` resolves every declared dependency
with an exact-id lookup and aborts the whole import when one is missing. That
applies to ``hl7.terminology.r4#5.3.0``, declared by ``ihe.formatcode.fhir#1.2.0``.
Such packages are promoted into the load plan, their payload is fetched from the
package registry (so this one case does need network access), and the resulting
SUT/validator asymmetry is recorded under ``snapshotDependenciesPromoted``.

The base FHIR R4 package
------------------------
``hl7.fhir.r4.core#4.0.1`` is named explicitly by the ``isik3-basismodul`` plugin
but *not* by ``isik5``, whose dependency list starts at ``de.basisprofil.r4#1.5.4``.
That asymmetry is fine for the validator - it always has core R4 - but an IRIS
endpoint's package list must contain the core package, because that is what
supplies the base resource definitions the endpoint's canonical map is built on.
It is therefore added to every level as an implicit requirement and flagged
``implicit: true`` in the plan, so the addition is visible in the provenance record
rather than being an undocumented difference between the plan and the validator's
declared set.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

# Invoked as a standalone script, so sys.path[0] is already this directory;
# the insert is for the case where a sibling imports this module instead.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tool_common import fail  # noqa: E402

try:
    import yaml
except ImportError:  # pragma: no cover - preflight checks this
    sys.exit("plan_packages.py: PyYAML is required (pip install pyyaml)")


# The base FHIR R4 package. Pinned rather than discovered: FHIR R4 is 4.0.1 and
# has been since 2019, ISiK is an R4 specification, and every ISiK package's
# `fhirVersions` says "4.0.1". A discovered value here would only introduce a way
# for the two endpoints to end up on different core versions.
FHIR_CORE_NAME = "hl7.fhir.r4.core"
FHIR_CORE_VERSION = "4.0.1"


def parse_pkg_id(pkg_id: str) -> tuple[str, str]:
    """Split a FHIR package reference ``name#version``.

    The ``#`` separator is the FHIR package registry convention and is what the
    refv config files use. Names themselves contain dots but never ``#``, so the
    split is unambiguous.
    """
    if "#" not in pkg_id:
        fail(f"malformed package reference {pkg_id!r} - expected name#version")
    name, _, version = pkg_id.partition("#")
    if not name or not version:
        fail(f"malformed package reference {pkg_id!r} - expected name#version")
    return name, version


def version_key(version: str) -> tuple:
    """Sort key for FHIR package versions.

    Handles the numeric-dotted forms in play here (``1.4.0``, ``2025.0.1``,
    ``2022.1.2``) and keeps any trailing non-numeric qualifier from crashing the
    comparison. Not a full semver implementation: it does not need to be, and a
    partial one that silently mis-orders would be worse than an explicit error.
    """
    parts: list[tuple[int, Any]] = []
    for chunk in version.replace("-", ".").split("."):
        if chunk.isdigit():
            parts.append((0, int(chunk)))
        else:
            # Pre-release qualifiers sort before plain numbers of the same rank.
            parts.append((1, chunk))
    return tuple(parts)


def load_plugin(config_path: str) -> dict:
    """Read one refv plugin config.yaml into a normalised descriptor."""
    with open(config_path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)

    if not isinstance(cfg, dict):
        fail(f"{config_path}: not a YAML mapping")

    module_id = cfg.get("id")
    if not module_id:
        fail(f"{config_path}: no 'id' - cannot map this plugin to a validation module")

    dep_lists = ((cfg.get("validation") or {}).get("dependencyLists")) or []
    if not dep_lists:
        fail(f"{config_path}: validation.dependencyLists is empty")

    primary: list[str] = []
    deps: list[str] = []
    for entry in dep_lists:
        fhir_package = entry.get("fhirPackage")
        if not fhir_package:
            fail(f"{config_path}: a dependencyLists entry has no 'fhirPackage'")
        primary.append(fhir_package)
        deps.extend(entry.get("dependencies") or [])

    return {
        "module_id": module_id,
        "configFile": config_path,
        "pluginVersion": str(cfg.get("version", "")),
        "specUrl": cfg.get("specUrl", ""),
        "description": (cfg.get("description") or "").replace("\n", " ").strip(),
        # The profile-supplying package(s) of this module - what the feature files
        # assert conformance against.
        "primaryPackages": primary,
        "dependencies": deps,
        # Used by the validator for snapshot generation only; no payload shipped.
        "snapshotDependencies": cfg.get("snapshotDependencies") or [],
        # Reproduced into the documentation: they explain why certain terminology
        # bindings are not enforced, which pre-empts "why did this pass?".
        "ignoredCodeSystems": (cfg.get("validation") or {}).get("ignoredCodeSystems") or [],
        "ignoredValueSets": (cfg.get("validation") or {}).get("ignoredValueSets") or [],
        "messageTransformations": [
            t
            for entry in dep_lists
            for t in (entry.get("validationMessageTransformations") or [])
        ],
    }


def level_of(module_id: str) -> str:
    """Map a validation-module id to an ISiK level.

    Checked, not assumed: an unknown id must stop the run, because dropping a
    module would quietly reduce the conformance scope.
    """
    if module_id == "isik5" or module_id.startswith("isik5-"):
        return "stufe5"
    if module_id.startswith("isik3-"):
        return "stufe3"
    fail(
        f"cannot assign validation module {module_id!r} to an ISiK level. "
        "Expected an 'isik3-*' or 'isik5' id. The test suite's plugin naming "
        "changed; update level_of() in scripts/lib/plan_packages.py."
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--plugins-dir", required=True,
                    help="vendor/refv-plugins, containing <module>/config.yaml")
    ap.add_argument("--out", required=True, help="output path for package-plan.json")
    ap.add_argument("--stufe3-namespace")
    ap.add_argument("--stufe3-endpoint")
    ap.add_argument("--stufe5-namespace", required=True)
    ap.add_argument("--stufe5-endpoint", required=True)
    # The levels the pinned suite contains, lower case, space-separated - the
    # output of suite_levels_lc() in scripts/lib.sh. Test suite 3.0.0 removed
    # ISiK Level 3, so a plan for that generation has one endpoint, and a plugin
    # assigned to a level not named here is an error rather than a level to add:
    # it would mean the rig planned packages for an endpoint it never configures.
    ap.add_argument("--levels", default="stufe3 stufe5")
    ap.add_argument("--testsuite", required=True,
                    help="test suite image reference, recorded as the plan's provenance")
    args = ap.parse_args()

    wanted = args.levels.split()
    for lv in wanted:
        if lv not in ("stufe3", "stufe5"):
            fail(f"--levels names {lv!r}, which is not an ISiK level this rig knows")
    if "stufe3" in wanted and not (args.stufe3_namespace and args.stufe3_endpoint):
        fail("--levels includes stufe3, so --stufe3-namespace and "
             "--stufe3-endpoint are required")

    # --- Read every plugin descriptor ---------------------------------------
    plugins = []
    for entry in sorted(os.listdir(args.plugins_dir)):
        cfg_path = os.path.join(args.plugins_dir, entry, "config.yaml")
        if os.path.isfile(cfg_path):
            plugins.append(load_plugin(cfg_path))

    if not plugins:
        fail(f"no <module>/config.yaml under {args.plugins_dir}")

    # --- Group into the endpoints this suite generation has -------------------
    all_levels: dict[str, dict] = {
        "stufe3": {
            "level": "Stufe 3",
            "namespace": args.stufe3_namespace,
            "endpoint": args.stufe3_endpoint,
            "modules": [],
            "_names": {},          # package name -> {version, ...}
        },
        "stufe5": {
            "level": "Stufe 5",
            "namespace": args.stufe5_namespace,
            "endpoint": args.stufe5_endpoint,
            "modules": [],
            "_names": {},
        },
    }
    levels: dict[str, dict] = {lv: all_levels[lv] for lv in wanted}

    all_packages: dict[str, dict] = {}   # "name#version" -> record
    snapshot_only: dict[str, list[str]] = {}

    for plugin in plugins:
        level = level_of(plugin["module_id"])
        if level not in levels:
            fail(
                f"validation module {plugin['module_id']!r} belongs to {level}, "
                f"which this suite release is not expected to contain "
                f"(--levels '{args.levels}'). Either the release still ships "
                f"that level and suite_levels() in scripts/lib.sh is wrong, or "
                f"the plugin set extracted from the image is not the one this "
                f"release publishes. Both must be resolved before a round."
            )
        bucket = levels[level]
        bucket["modules"].append(
            {
                "id": plugin["module_id"],
                "pluginVersion": plugin["pluginVersion"],
                "specUrl": plugin["specUrl"],
                "description": plugin["description"],
                "primaryPackages": plugin["primaryPackages"],
                "dependencies": plugin["dependencies"],
                "snapshotDependencies": plugin["snapshotDependencies"],
                "ignoredCodeSystems": plugin["ignoredCodeSystems"],
                "ignoredValueSets": plugin["ignoredValueSets"],
                "messageTransformations": plugin["messageTransformations"],
                "configFile": os.path.relpath(plugin["configFile"]),
            }
        )

        for pkg_id in plugin["primaryPackages"] + plugin["dependencies"]:
            name, version = parse_pkg_id(pkg_id)
            rec = all_packages.setdefault(
                pkg_id,
                {"id": pkg_id, "name": name, "version": version,
                 "levels": [], "requiredBy": [], "role": "dependency"},
            )
            if pkg_id in plugin["primaryPackages"]:
                # A profile-supplying package: named as `fhirPackage` by at least
                # one validation module.
                rec["role"] = "profile"
            if level not in rec["levels"]:
                rec["levels"].append(level)
            if plugin["module_id"] not in rec["requiredBy"]:
                rec["requiredBy"].append(plugin["module_id"])

            bucket["_names"].setdefault(name, set()).add(version)

        for pkg_id in plugin["snapshotDependencies"]:
            snapshot_only.setdefault(pkg_id, []).append(plugin["module_id"])

    # `snapshotDependencies` entries that are also real dependencies elsewhere are
    # not snapshot-only; drop them from that list so the documentation does not
    # claim a package is unshipped when it is present.
    snapshot_only = {k: v for k, v in snapshot_only.items() if k not in all_packages}

    # --- Snapshot-only packages that a loaded package *declares* --------------
    # A snapshotDependencies entry is normally not loaded into the SUT: the
    # validator uses it to build snapshots and ships no payload for it. One of them
    # cannot be left out, and the reason is a property of IRIS rather than a choice.
    #
    # hl7.terminology.r4#5.3.0 is declared in the `dependencies` of
    # ihe.formatcode.fhir#1.2.0, which the isik3-dokumentenaustausch module *does*
    # pin as a real dependency. HS.FHIRMeta.Load.NpmLoader.coreVersion() walks
    # every declared dependency of every package being imported and resolves each
    # one with an exact-id HS.FHIRMeta.Storage.Package.GetById(); a declaration
    # naming a package that is not stored aborts the whole import with
    # "ERROR #5809: Object to Load not found ... ID 'hl7.terminology.r4@5.3.0'".
    # Verified against irishealth 2026.2 by dumping coreVersion() and by
    # reproducing the abort. So it is promoted to a loaded package and its payload
    # is fetched.
    #
    # The consequence is declared rather than hidden: the SUT then holds one
    # package the validator's dependencyLists do not pin for this level. It is
    # recorded in `snapshotDependenciesPromoted` and reported, because it is an
    # asymmetry between the SUT and the validator - the one thing this plan
    # otherwise guarantees cannot happen.
    promoted: list[dict] = []
    declared_by: dict[str, list[str]] = {}
    for pkg_id in list(snapshot_only):
        name, version = parse_pkg_id(pkg_id)
        for level, bucket in levels.items():
            # Only for a level that actually pins the package declaring it; the
            # module that named the snapshot dependency is the anchor.
            modules_here = {m["id"] for m in bucket["modules"]}
            if not modules_here.intersection(snapshot_only[pkg_id]):
                continue
            rec = all_packages.setdefault(
                pkg_id,
                {"id": pkg_id, "name": name, "version": version,
                 "levels": [], "requiredBy": [], "role": "dependency"},
            )
            if level not in rec["levels"]:
                rec["levels"].append(level)
            rec["promotedFromSnapshotOnly"] = True
            rec["promotionRationale"] = (
                "Declared in the `dependencies` of a package this level loads, so "
                "IRIS's NpmLoader must be able to resolve it by exact id even "
                "though the validator lists it only under snapshotDependencies."
            )
            marker = "(promoted: IRIS resolves declared dependencies by exact id)"
            if marker not in rec["requiredBy"]:
                rec["requiredBy"].append(marker)
            bucket["_names"].setdefault(name, set()).add(version)
            declared_by.setdefault(pkg_id, []).append(level)

    for pkg_id, lvls in sorted(declared_by.items()):
        promoted.append(
            {
                "id": pkg_id,
                "levels": sorted(lvls),
                "declaredSnapshotOnlyBy": snapshot_only[pkg_id],
                "reason": (
                    "Named in the dependencies of a loaded package. IRIS's "
                    "HS.FHIRMeta.Load.NpmLoader resolves every declared dependency "
                    "with an exact-id lookup and aborts the import if it is absent "
                    "(ERROR #5809), so the package must be loaded even though the "
                    "reference validator only uses it for snapshot generation."
                ),
                "deviation": (
                    "The SUT therefore holds one package the validator's "
                    "dependencyLists do not pin for this level. It supplies "
                    "terminology only and no ISiK profile the suite asserts "
                    "against; the endpoint package list is unaffected, because the "
                    "version-collision rule keeps only the highest version of a "
                    "name there."
                ),
            }
        )
        del snapshot_only[pkg_id]

    # --- Implicit base FHIR package ------------------------------------------
    # See the module docstring: isik5's dependency list omits hl7.fhir.r4.core,
    # but an IRIS endpoint needs it. Added per level, and marked so the addition
    # is auditable.
    for level, bucket in levels.items():
        have_core = any(
            rec["name"] == FHIR_CORE_NAME and level in rec["levels"]
            for rec in all_packages.values()
        )
        if have_core:
            continue
        core_id = f"{FHIR_CORE_NAME}#{FHIR_CORE_VERSION}"
        rec = all_packages.setdefault(
            core_id,
            {"id": core_id, "name": FHIR_CORE_NAME, "version": FHIR_CORE_VERSION,
             "levels": [], "requiredBy": [], "role": "base"},
        )
        if level not in rec["levels"]:
            rec["levels"].append(level)
        rec["implicit"] = True
        rec["implicitRationale"] = (
            "Not named by this level's validator plugin, but required by IRIS: "
            "the endpoint's package list must include the base FHIR R4 "
            "definitions."
        )
        if "(implicit: IRIS endpoint requirement)" not in rec["requiredBy"]:
            rec["requiredBy"].append("(implicit: IRIS endpoint requirement)")
        bucket["_names"].setdefault(FHIR_CORE_NAME, set()).add(FHIR_CORE_VERSION)

    # --- Resolve per-endpoint version collisions -----------------------------
    collisions = []
    for level, bucket in levels.items():
        if not bucket["modules"]:
            fail(
                f"no validation module was assigned to {level}, although this "
                f"suite release is expected to contain it (--levels "
                f"'{args.levels}'). The plugin set extracted from the image does "
                f"not cover every level the release publishes; check "
                f"TESTSUITE_VERSION and suite_levels() in scripts/lib.sh."
            )

        chosen: list[str] = []
        for name, versions in sorted(bucket["_names"].items()):
            if len(versions) > 1:
                ranked = sorted(versions, key=version_key)
                winner = ranked[-1]
                collisions.append(
                    {
                        "level": level,
                        "package": name,
                        "versions": ranked,
                        "loadedIntoNamespace": ranked,
                        "inEndpointPackageList": winner,
                        "rationale": (
                            "The validator's dependency set pins several versions "
                            "of this package. All are imported into the namespace "
                            "so every canonical the validator may resolve is "
                            "present, but the endpoint's package list - which "
                            "drives the generated CapabilityStatement and the "
                            "canonical-to-definition map - carries only the "
                            "highest version."
                        ),
                    }
                )
                chosen.append(f"{name}#{winner}")
            else:
                chosen.append(f"{name}#{next(iter(versions))}")

        # Packages imported into the namespace: every version, including the ones
        # not in the endpoint list.
        bucket["importPackages"] = sorted(
            pkg_id
            for pkg_id, rec in all_packages.items()
            if level in rec["levels"]
        )
        # Packages declared on the endpoint (IRIS InstallInstance package list).
        bucket["packages"] = sorted(chosen)
        del bucket["_names"]

    # --- Emit ---------------------------------------------------------------
    plan = {
        "_comment": (
            "GENERATED by scripts/lib/plan_packages.py from the refv-plugins "
            "bundled in the ISiK test suite image. This is the authoritative "
            "statement of which FHIR packages each IRIS endpoint is loaded with, "
            "and it is derived from the test tool itself so that the SUT and the "
            "validator cannot disagree. Do not edit by hand."
        ),
        "sourceImage": args.testsuite,
        "endpoints": levels,
        "allPackages": [all_packages[k] for k in sorted(all_packages)],
        "versionCollisions": collisions,
        "snapshotOnlyPackages": [
            {"id": k, "requiredBy": v} for k, v in sorted(snapshot_only.items())
        ],
        "snapshotDependenciesPromoted": promoted,
    }

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(plan, fh, indent=2, sort_keys=False)
        fh.write("\n")

    # --- Report -------------------------------------------------------------
    for level in wanted:
        b = plan["endpoints"][level]
        mods = ", ".join(m["id"] for m in b["modules"])
        print(f"  {b['level']:8} {b['namespace']:8} {b['endpoint']}")
        print(f"           modules:  {mods}")
        print(f"           packages: {len(b['packages'])} on the endpoint, "
              f"{len(b['importPackages'])} imported")

    print(f"  total distinct packages: {len(plan['allPackages'])}")

    for c in collisions:
        print(
            f"  NOTE {c['level']}: {c['package']} pinned at "
            f"{', '.join(c['versions'])}; endpoint uses "
            f"{c['inEndpointPackageList']}",
            file=sys.stderr,
        )
    for s in plan["snapshotOnlyPackages"]:
        print(
            f"  NOTE snapshot-only dependency not loaded: {s['id']} "
            f"(declared by {', '.join(s['requiredBy'])})",
            file=sys.stderr,
        )
    for p in promoted:
        print(
            f"  NOTE snapshot-only dependency PROMOTED to loaded: {p['id']} "
            f"({', '.join(p['levels'])}) - a loaded package declares it and IRIS "
            "resolves declared dependencies by exact id",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
