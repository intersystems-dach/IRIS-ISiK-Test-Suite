#!/usr/bin/env python3
"""Reading the requirements corpus that `extract_requirements.py` writes.

`requirements.json` is the one artefact that records, per level, every scenario
the pinned suite carries and every test-data key its feature files reference.
Four modules read it, and the projection below was written out twice - once in
`build_testdata.py` as `load_contract` and once in `search_reference_check.py`
as `contract`, with identical bodies and two different names.

That is a worse duplication than it looks: the seeder resolves `${<ns>.*}`
through one copy and the checker that proves the seeded corpus satisfies the
suite's search scenarios resolves it through the other. Had the copies drifted,
the checker would have passed on values the suite never sends - which is the
single failure this project most needs not to have, because it would report a
defect of ours as a defect of the product.

Admission rule: this module owns reading `requirements.json` and nothing else.
It is deliberately not in `tool_common.py`, which may hold no knowledge of any
artefact's shape.
"""

from __future__ import annotations


def testdata_contract(level: str, req: dict) -> dict[str, str]:
    """Qualified `<ns>.<key>` -> ID, from the suite's own contract for a level.

    Taken out of `requirements.json` rather than re-read from the suite's YAML
    so that the IDs the seed data is built with are provably the same strings
    the extractor documented and the suite will send. The staged YAML files are
    the other possible source, and `reconcile_testdata.contract_from_yaml()`
    reads them - under a name that says so, because the two sources agree only
    as long as nothing has gone wrong.

    A key whose value is absent or empty is omitted rather than recorded as an
    empty string: the callers test for presence, and an empty value would
    resolve a placeholder to nothing instead of reporting it as unresolved.
    """
    out: dict[str, str] = {}
    for feature in req["features"]:
        if feature["level"] != level:
            continue
        for key, info in feature["dataKeys"].items():
            if info["value"] not in (None, ""):
                out[key] = str(info["value"])
    return out
