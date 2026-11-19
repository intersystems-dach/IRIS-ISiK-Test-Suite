#!/usr/bin/env python3
"""What a `@@PLACEHOLDER@@` is, stated once for the three renderers.

`scripts/30-render-config.sh` renders three unrelated template families - the
Tiger configuration, the Web Gateway's INI and Apache files, and the
authorization server's realm - from three deliberately disjoint substitution
tables. Those tables stay inside their own render function, and `make check`
holds each one to exactly one template directory by reading the shell source, so
that a Tiger placeholder cannot be accepted in an Apache file or the realm's
client secret in either.

What all three must nevertheless agree on is the *grammar*: what text counts as
a placeholder at all. That was written out three times, and a drift between the
copies would be silent in the worst direction - a renderer whose patterns were
one revision behind would accept a placeholder it does not substitute and write
the literal marker into a file the suite reads as a URL or a credential.

Admission rule: this module owns the placeholder grammar and nothing else. It
holds no substitution table and no knowledge of any template family.
"""

from __future__ import annotations

import re

# Narrow, because this is the naming convention for a substitutable key.
_SUBSTITUTABLE = re.compile(r"@@([A-Z0-9_]+)@@")

# Deliberately wider than the pattern above, and that asymmetry is the point:
# a key misspelt outside the convention - `@@sut_user@@` - is invisible to the
# first check, which would report nothing unknown, and is caught here instead.
# One shared pattern would let it through both.
_ANY_MARKER = re.compile(r"@@[^@]*@@")


def unknown_placeholders(text: str, subst: dict[str, str]) -> list[str]:
    """The substitutable-looking placeholders of `text` that `subst` cannot fill.

    A template bug, not a configuration error: the renderer must fail on these
    rather than let the key render as an empty string, which would reach the
    container as a blank URL, password or port.
    """
    return sorted(set(_SUBSTITUTABLE.findall(text)) - set(subst))


def leftover_placeholders(text: str) -> list[str]:
    """Everything still of the form `@@...@@` once substitution has run.

    Belt and braces. Nothing of this form may survive into a file the test
    suite, the gateway or the authorization server reads, because each of them
    would take the marker for a literal value.
    """
    return _ANY_MARKER.findall(text)
