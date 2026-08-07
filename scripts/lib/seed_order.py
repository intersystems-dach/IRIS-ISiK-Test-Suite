#!/usr/bin/env python3
"""Order the rendered test-data corpus so referenced resources are written first.

Why an ordering is derived rather than hardcoded
------------------------------------------------
The plan's ordering - Organization/Location/Practitioner -> Patient ->
Encounter/Coverage/Account -> clinical -> Appointment/Slot/Schedule - is right
about the shape of the graph but says nothing about the 130 individual resources,
and it goes stale the moment a reference is added. So the order is computed from
the corpus's own references: an edge `A -> B` exists whenever A's JSON contains a
literal `"reference": "<Type>/<id>"` naming another resource in the same level,
and B is written before A.

The static type ranking below survives only as the tiebreaker. Kahn's algorithm
leaves many orders valid; picking the smallest `(type rank, filename)` among the
ready nodes makes the output deterministic - the same corpus always seeds in the
same sequence, which is what makes two rounds' seed logs diffable - and makes it
read the way an operator expects.

The Condition/Encounter cycle
-----------------------------
`Encounter.diagnosis.condition` points at a Condition and `Condition.encounter`
points back at the Encounter. In Stufe 5 that is a genuine three-node cycle:

    Encounter-Read-In-Progress-Example -> Condition-Read-Active-Example
    Condition-Read-Active-Example      -> Condition-Read-Resolved-Example
                                       -> Encounter-Read-In-Progress-Example
    Condition-Read-Resolved-Example    -> Encounter-Read-In-Progress-Example

Both directions are required by the suite's own precondition prose, so neither
reference can be dropped. No total order satisfies it, and that is stated rather
than hidden: the node is emitted anyway with `cycleBroken` naming the edges that
are unsatisfied at the moment it is written, so the seeder can report exactly
which resources it wrote ahead of a reference target and re-attempt them in a
second pass.

Nothing here assumes the server rejects a forward reference. FHIR permits a
reference to a resource that does not exist, and the seeder's read-back pass is
what actually proves the corpus landed. The ordering exists so that a server that
*does* enforce integrity - or a future stricter endpoint configuration - does not
turn a correct corpus into a seeding failure.

Usage
-----
    scripts/lib/seed_order.py --level Stufe3
    scripts/lib/seed_order.py --level Stufe5 --data-root build/fhir --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# A literal relative reference. Anything else - a contained `#ref`, an absolute
# URL, a logical reference carrying only `identifier` - names nothing in this
# corpus and so constrains no ordering.
RE_REF = re.compile(r"^([A-Za-z]+)/([A-Za-z0-9._-]{1,64})$")

# Tiebreaker only; see the module docstring. Grouped by the role a type plays in
# the graph rather than alphabetically, so that a corpus with no references at all
# would still seed in an order a reader recognises.
TYPE_RANK = {
    # 1. Infrastructure and terminology: referenced by everything, reference little
    "Organization": 10, "Location": 11, "Practitioner": 12, "PractitionerRole": 13,
    "HealthcareService": 14, "Device": 15, "Endpoint": 16,
    "CodeSystem": 20, "ValueSet": 21, "ConceptMap": 22, "Binary": 23,
    # 2. Subjects of care
    "Patient": 30, "RelatedPerson": 31, "Group": 32,
    # 3. Administrative context
    "Encounter": 40, "EpisodeOfCare": 41, "Coverage": 42, "Account": 43,
    # 4. Clinical content
    "Condition": 50, "AllergyIntolerance": 51, "Procedure": 52, "Observation": 53,
    "DiagnosticReport": 54, "RiskAssessment": 55, "CarePlan": 56,
    "Medication": 60, "MedicationRequest": 61, "MedicationStatement": 62,
    "MedicationAdministration": 63, "MedicationDispense": 64,
    "DocumentReference": 70, "Composition": 71, "List": 72, "Communication": 73,
    # 5. Scheduling: references practitioners, patients and each other
    "Schedule": 80, "Slot": 81, "Appointment": 82, "AppointmentResponse": 83,
}
# An unlisted type sorts between the clinical content and scheduling groups: late
# enough that its references are likely already present, early enough not to be
# mistaken for a scheduling resource.
DEFAULT_RANK = 75


def load(data_root: Path, level: str) -> dict[str, dict]:
    """`Type/id` -> {file, resourceType, id, refs}."""
    root = data_root / level
    if not root.is_dir():
        raise SystemExit(f"{root} does not exist - run: scripts/lib/build_testdata.py")

    nodes: dict[str, dict] = {}
    for path in sorted(root.glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        rtype, rid = doc.get("resourceType"), doc.get("id")
        if not rtype or not rid:
            raise SystemExit(f"{path}: no resourceType/id - not a seedable resource")
        key = f"{rtype}/{rid}"
        if key in nodes:
            raise SystemExit(f"{path}: {key} is also declared by {nodes[key]['file']}")
        nodes[key] = {"file": path.name, "resourceType": rtype, "id": rid,
                      "refs": sorted(collect(doc))}
    return nodes


def collect(doc) -> set[str]:
    """Every literal `Type/id` reference string anywhere in the body."""
    out: set[str] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            ref = node.get("reference")
            if isinstance(ref, str) and RE_REF.match(ref):
                out.add(ref)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(doc)
    return out


def order(nodes: dict[str, dict]) -> list[dict]:
    """Dependencies first, deterministic, cycles broken explicitly.

    Kahn's algorithm over the reference graph. A node is ready when every resource
    it references either has been written already or is not part of this corpus -
    an external or dangling reference cannot be ordered around and is reported by
    the seeder's read-back pass instead. When nothing is ready and nodes remain,
    the graph has a cycle: the smallest remaining node is emitted with the edges
    that are still unsatisfied recorded on it.
    """
    pending = {k: {r for r in v["refs"] if r in nodes and r != k}
               for k, v in nodes.items()}
    written: set[str] = set()
    out: list[dict] = []

    def rank(key: str) -> tuple[int, str]:
        return (TYPE_RANK.get(nodes[key]["resourceType"], DEFAULT_RANK),
                nodes[key]["file"])

    while pending:
        ready = sorted((k for k, deps in pending.items() if deps <= written), key=rank)
        broken: list[str] = []
        if not ready:
            # Cycle. Take one node and say which of its references are not yet met.
            head = min(pending, key=rank)
            broken = sorted(pending[head] - written)
            ready = [head]
        for key in ready:
            out.append({**nodes[key], "key": key,
                        "cycleBroken": broken if key == ready[0] else []})
            written.add(key)
            del pending[key]
            if broken:
                # Re-evaluate readiness after a forced write rather than draining
                # the rest of this batch, which was computed before it.
                break
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--level", choices=("Stufe3", "Stufe5"), required=True)
    ap.add_argument("--data-root", type=Path, default=REPO / "build" / "fhir")
    ap.add_argument("--json", action="store_true",
                    help="emit the full ordering as JSON instead of TSV")
    args = ap.parse_args()

    nodes = load(args.data_root, args.level)
    seq = order(nodes)

    if args.json:
        print(json.dumps(seq, indent=2, ensure_ascii=False))
        return 0

    # TSV, because the consumer is a bash `while read` loop. Field 4 is empty for
    # the overwhelming majority; when set it names the references that were not yet
    # satisfied, which is what the seeder retries.
    for n in seq:
        print(f"{n['file']}\t{n['resourceType']}\t{n['id']}\t{','.join(n['cycleBroken'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
