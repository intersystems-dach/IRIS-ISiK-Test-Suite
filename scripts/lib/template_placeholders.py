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

Admission rule: this module owns the placeholder grammar and how a renderer
refuses what the grammar rejects. It holds no substitution table and no
knowledge of any template family. The refusals are here for the same reason the
patterns are: each of the three renderers wrote the same two messages out in
full, so the wording of a refusal - and which of the two paths it named - was
three independent copies of one decision.
"""

from __future__ import annotations

import dataclasses
import re
import sys

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


@dataclasses.dataclass(frozen=True)
class Rendering:
    """The two paths one rendering lies between: a template and its output.

    Both are held because the two refusals below name different ones, and that
    difference is the whole value of the message. An unknown placeholder is a
    defect of the *template* - someone wrote `@@SUT_PORT@@` where the table
    offers `@@SUT_BASE_URL@@` - and the reader has to open the template. A
    marker that survived substitution is a defect of the *output*, and the
    reader has to look at the rendered file to see what is still unresolved.
    Naming the wrong file sends them to the wrong place.
    """

    template: str
    output: str

    @staticmethod
    def of(argv: list[str]) -> "Rendering":
        """The rendering a renderer was invoked for: its first two arguments."""
        return Rendering(template=argv[1], output=argv[2])

    def refuse_unknown(self, text: str, subst: dict[str, str]) -> None:
        """Stop unless every placeholder the template names can be filled."""
        unknown = unknown_placeholders(text, subst)
        if unknown:
            sys.exit(f"30-render-config.sh: {self.template}: unknown "
                     f"placeholder(s): {', '.join(unknown)}")

    def refuse_leftover(self, text: str) -> None:
        """Stop if any marker at all survived into what is about to be written."""
        left = leftover_placeholders(text)
        if left:
            sys.exit(f"30-render-config.sh: {self.output}: unresolved "
                     f"placeholder(s): {', '.join(left)}")
