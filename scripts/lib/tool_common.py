#!/usr/bin/env python3
"""Helpers shared by the standalone tools in this directory.

Admission rule: a helper belongs here only when more than one tool in
`scripts/lib/` needs it *and* it carries no domain knowledge. Anything that
knows what a FHIR package, a test-data key or a scenario is belongs in the
module that owns that subject, which its siblings then import from - as
`order_packages.py` imports `version_key` from `plan_packages.py` and
`reconcile_testdata.py` imports `REFERENCE_KEYS` from `derive_testdata.py`.

Why the name is this specific, and must stay unique: `gap-analysis/`'s tools
put this directory on sys.path, so module names here share one namespace with
module names there. `gap-analysis/generator_common.py` is that directory's
counterpart of this file, and had both been called `common.py` an
`import common` would have resolved to whichever directory came first.

The two are deliberately not merged. `fail()` here exits 1 and `die()` there
exits 2, and an exit code is part of a tool's contract with the shell script
that runs it.
"""

from __future__ import annotations

import sys
from pathlib import Path


def fail(msg: str) -> None:
    """Abort with a message prefixed by the tool that is running.

    The prefix is read from argv rather than passed in or hard-coded, so that
    no module has to keep a constant naming itself - a constant that survives
    being copied into the next module and then names the wrong tool.
    """
    print(f"{Path(sys.argv[0]).name}: {msg}", file=sys.stderr)
    raise SystemExit(1)
