#!/usr/bin/env python3
"""Flag authored test data that writes a FHIR choice element by its bare name.

Why this check exists
---------------------
`Annotation.author[x]` is a choice element: valid R4 JSON spells it
`authorReference` or `authorString`, never `author`. One authored body wrote

    "note": [{"author": {"reference": "Practitioner/..."}, ...}]

which cost a full deployment round trip to discover, because nothing before the
seeder looks at element names. `make seed` did catch it - IRIS answered the PUT
with `HTTP 400 ... Property 'author' of Type 'Annotation' cannot be an Object`,
the read-back 404'd, `$validate` reported it, and SEED_VALIDATE_STRICT stopped the
round rather than letting an incomplete corpus reach a conformance run. That is
the behaviour the seeder is designed for and it worked. But the diagnosis needed a
licensed image, a 5-minute bootstrap and a seeding pass to reach a verdict about a
misspelled key, and the same class of defect in a *reference target* would have
been reported as a search failure in the official report - our data defect wearing
an IRIS gap's clothing.

So the class is moved forward into `make check`, alongside the ObjectScript
macro check, for the same reason: a defect that has actually cost a deployment
here gets a static check so it cannot cost a second one.

Why it is exact rather than heuristic
-------------------------------------
The naive form of this check - "flag any key named `author`" - is wrong:
`DocumentReference.author` is a perfectly ordinary element, and 4 of the authored
bodies use it. So the check does not work from a list of suspicious names. It
resolves each JSON key to its element path against the R4 base definitions in
`vendor/packages/hl7.fhir.r4.core-*`, and flags a key only when

    <path>      does not exist   AND   <path>[x]      does
                                        ^ i.e. the only definition of that element
                                          in the specification is a choice

which is precisely the condition that makes the bare spelling invalid. The R4
package is the same one the endpoint under test loads, so the check and the SUT
agree about what R4 says by construction. The valid spellings printed in the
message are generated from the `[x]` element's own type list, so the remedy is
read out of the specification rather than guessed.

Unknown keys are ignored, deliberately. A key this walker cannot resolve is not
evidence of anything: extensions, primitive-type extension pairs (`_birthDate`),
and elements inside a type this scan does not model all read as unknown, and
failing on them would make the check unusable. The scan proves the presence of a
defect, never its absence - `$validate` in the seeder remains the authority.

Usage
-----
    scripts/lib/choice_type_check.py                      # testdata/fhir, vendor/packages
    scripts/lib/choice_type_check.py --src build/fhir      # rendered output instead
    scripts/lib/choice_type_check.py --packages /tmp/pkgs

Exit status: 0 clean or prerequisites absent (reported), 1 a bare choice element
was found, 2 the R4 core package could not be read.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Keys that never name an element: FHIR machinery, or our own annotation.
# `extension` / `modifierExtension` are skipped because their children are
# extension slices, whose paths are not in the base definitions at all.
SKIP_KEYS = {"resourceType", "id", "extension", "modifierExtension", "fhir_comments",
             "_provenance"}

# Types whose children are defined inline on the containing element's own path
# rather than under the type's name, so the path prefix must not be rewritten to
# them. (`BackboneElement.id` exists, but `Patient.contact.name` is what defines
# the child of Patient.contact - not `BackboneElement.name`.)
INLINE_TYPES = {"BackboneElement", "Element"}


def load_r4_elements(packages_dir: Path) -> tuple[dict, dict]:
    """(element path -> (type codes, contentReference), type name -> kind).

    Only base definitions are read: `derivation == "constraint"` marks a profile,
    whose snapshot restates the same paths with narrowed types, and a profile's view
    of an element is not what makes a spelling valid R4.
    """
    roots = sorted(packages_dir.glob("hl7.fhir.r4.core*")) if packages_dir.is_dir() else []
    if not roots:
        return {}, {}
    root = roots[0]
    root = root / "package" if (root / "package").is_dir() else root

    elements: dict[str, tuple[list[str], str | None]] = {}
    kinds: dict[str, str] = {}
    for path in sorted(root.glob("StructureDefinition-*.json")):
        try:
            sd = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if sd.get("resourceType") != "StructureDefinition":
            continue
        if sd.get("derivation") == "constraint":
            continue
        if sd.get("type"):
            kinds[sd["type"]] = sd.get("kind", "")
        for el in (sd.get("snapshot") or {}).get("element", []):
            p = el.get("path")
            if not p:
                continue
            types = [t["code"] for t in el.get("type", []) if t.get("code")]
            elements.setdefault(p, (types, el.get("contentReference")))
    return elements, kinds


def spellings(bare: str, types: list[str]) -> list[str]:
    """The valid names for a `[x]` element, from its own declared types.

    R4 forms them as the bare name plus the type code with an upper-case initial:
    `author` + `Reference` -> `authorReference`.
    """
    return [f"{bare}{t[0].upper()}{t[1:]}" for t in types]


class Scanner:
    def __init__(self, elements: dict, kinds: dict):
        self.elements = elements
        self.kinds = kinds

    def _child_prefix(self, path: str, types: list[str], cref: str | None) -> str:
        """Which path children of this element are defined under."""
        if cref:
            # e.g. "#Questionnaire.item" - the element reuses another element's children.
            return cref.lstrip("#")
        if (len(types) == 1 and types[0] not in INLINE_TYPES
                and self.kinds.get(types[0]) == "complex-type"):
            # A named complex type: children are defined under the type, so
            # Annotation's children live at Annotation.*, not at <resource>.note.*.
            return types[0]
        return path

    def scan(self, node, path: str, found: list[tuple[str, list[str]]]) -> None:
        if isinstance(node, list):
            for item in node:
                self.scan(item, path, found)
            return
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            if key in SKIP_KEYS or key.startswith("_"):
                continue
            candidate = f"{path}.{key}"
            if candidate in self.elements:
                types, cref = self.elements[candidate]
                self.scan(value, self._child_prefix(candidate, types, cref), found)
            elif f"{candidate}[x]" in self.elements:
                found.append((candidate, spellings(key, self.elements[f"{candidate}[x]"][0])))
            # else: unresolvable - see the module docstring, deliberately ignored.


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", type=Path, default=REPO / "testdata" / "fhir",
                    help="tree of FHIR JSON to scan (default: the authored sources)")
    ap.add_argument("--packages", type=Path, default=REPO / "vendor" / "packages",
                    help="unpacked FHIR packages, for the R4 base definitions")
    args = ap.parse_args()

    elements, kinds = load_r4_elements(args.packages)
    if not elements:
        print(f"no hl7.fhir.r4.core package under {args.packages} - "
              f"run: make packages", file=sys.stderr)
        return 2

    scanner = Scanner(elements, kinds)
    files = sorted(p for p in args.src.rglob("*.json"))
    findings = 0
    for f in files:
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"  {f}: not readable as JSON: {e}")
            findings += 1
            continue
        rt = doc.get("resourceType")
        if not rt or rt not in kinds:
            continue
        found: list[tuple[str, list[str]]] = []
        scanner.scan(doc, rt, found)
        for elem_path, valid in found:
            # Repo-relative where that is meaningful, absolute otherwise: --src
            # legitimately points outside the repo (a scratch render, /tmp fixture),
            # and relative_to() raises rather than falling back on such a path.
            try:
                rel: Path | str = f.relative_to(REPO)
            except ValueError:
                rel = f
            print(f"  FAIL {rel}: writes {elem_path} by its bare name, but R4 defines "
                  f"only {elem_path}[x] - use one of: {', '.join(valid)}")
            findings += 1

    print(f"{len(files)} resource(s) scanned against "
          f"{len(elements)} R4 element paths, {findings} bare choice element(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
