#!/usr/bin/env python3
"""Helpers shared by the document generators in this directory.

Admission rule: a helper belongs here only when more than one tool in
`gap-analysis/` needs it *and* it carries no knowledge of the gap analysis
itself. Anything that knows what a category, a gap or a case is belongs in
`build_index.py` or `gap_report.py`, where the other tools already import it
from - see NOT_SUT_CATEGORIES there for why a second copy of such a thing is a
defect waiting to happen.

Before this module existed, `gap_report.py` and `case_evidence.py` each carried
their own copy of all three functions below. The copies of `md_escape` were
character-identical, and the two copies of the JSON loader had drifted: one
treated an unreadable optional file as absent, the other aborted on it.

Why the name is this specific, and must stay unique: `build_index.py` puts
`scripts/lib/` on sys.path *ahead* of this directory, so every module name here
shares one namespace with every module name there. `scripts/lib/tool_common.py`
is that directory's counterpart of this file. Had both been called `common.py`,
an `import common` in this directory would have silently resolved to the other
one.

The two are deliberately not merged. `die()` here exits 2 and `fail()` there
exits 1, and an exit code is part of a tool's contract with the shell script
that runs it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def die(msg: str) -> None:
    """Abort with a message prefixed by the tool that is running.

    The prefix is read from argv rather than passed in, so that the two
    generators do not have to agree on a constant to say their own name.
    """
    print(f"{Path(sys.argv[0]).name}: {msg}", file=sys.stderr)
    raise SystemExit(2)


def load_json(path: Path, what: str):
    """Read a JSON input the caller cannot proceed without.

    Absent or unparseable is fatal: every caller of this form reads something
    the round is supposed to contain, and a report generated around a missing
    input would be a report that quietly says less than it appears to.
    """
    if not path.is_file():
        die(f"{what} not found at {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        die(f"{what} at {path} is not valid JSON: {exc}")


def load_json_if_readable(path: Path, what: str):
    """Read a JSON input the caller can do without, or return None.

    Both absent and unparseable return None, because to every caller of this
    form the two are the same event: the bundle does not yield this evidence,
    so the document says so for that one case and the rest is still generated.
    `--strict` is what turns an incompletely documented round into a non-zero
    exit; aborting here would lose the other several hundred cases as well.
    """
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        print(f"{Path(sys.argv[0]).name}: {what} at {path} is not valid JSON "
              f"({exc}) - treated as absent", file=sys.stderr)
        return None


def md_escape(text: str) -> str:
    """Make a value safe to put in a Markdown table cell."""
    return (text or "").replace("|", "\\|")
