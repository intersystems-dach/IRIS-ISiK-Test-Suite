#!/usr/bin/env python3
"""Make a staged test-data body satisfy the assertions the feature files make about it.

Why this exists
---------------
``derive_testdata.py`` takes a body from the ISiK package's own ``examples/``, which
is the best available provenance for its clinical content. But a package example is
written to illustrate a profile, not to satisfy a test: the ICU and Labor features
additionally require a terminology ``version`` on each coding, particular measured
values, particular components, and references to the *rig's* patient, encounter and
practitioner. A derived body therefore satisfies most of a feature's assertions and
fails a handful, every one of them for a reason the feature itself states.

Those remaining differences are not judgement calls. ``vendor/requirements.json``
records each one as a structured assertion extracted from the Gherkin, so the body
can be brought into line mechanically and the result verified by
``assertion_check.py``, which is a different program reading the same assertions
from the rendered tree. This tool performs the adjustment; it is not the thing that
proves it correct.

How it works
------------
An ISiK feature constrains a resource in a small, regular subset of FHIRPath -
chains of ``where(...)``, ``exists()``, ``count()``, ``toString().contains(...)``
over scalar equalities. This module parses that subset (``parse_chain``) and
interprets each expression not as a question but as a requirement: ``enforce``
walks the body along the chain, selecting the element a ``where`` designates or
creating it if absent, and writes the asserted scalars.

The subset is enforced strictly. Any construct outside it raises ``Unsupported``
and is reported verbatim with the resource it belongs to; nothing is guessed
around. That is the property that matters here, because a body quietly bent to
satisfy an assertion this tool misread would turn the rig's own test data into the
most likely cause of a failure in a round whose entire product is a statement about
the system under test.

Grammar accepted
----------------
    expr     := segment ( '.' segment )*
    segment  := NAME | 'where(' cond ')' | 'exists()' | 'empty()' | 'not()'
              | 'count()' | 'toString()' | "contains(' LIT ')" | 'first()'
    expr     may be followed by  '=' NUMBER        (count comparison)
    cond     := term ( ' and ' term )*
    term     := NAME ('=' | '~') LITERAL
              | path '.where(' cond ').exists()'
              | path '.exists()'
              | path '.empty().not()'
    LITERAL  := ' ... ' | " ... " | NUMBER

Choice elements
---------------
``effective``, ``value`` and ``issued`` are FHIR choice elements: a feature names
them without the type suffix the body carries. A name not present as a key is
resolved to the single key that extends it with a type suffix - ``effective`` to
``effectivePeriod``, ``value`` to ``valueQuantity``. Where no such key exists and
one would have to be created, the requirement is reported instead: the choice type
is a statement about the profile and is not determined by the feature text.

Displays
--------
Where a feature requires a coding to carry a display without saying which, the
display is taken from the specification itself: every ``code``/``system`` pair
occurring in the pinned packages - in their example instances and in the
``concept`` lists of the ``CodeSystem`` resources they ship - is indexed with the
display given there. The two sources are not of equal standing: a CodeSystem
*defines* a display and outranks any number of example instances *using* one, which
matters because the example packages are not always careful with them. A pair two
CodeSystems display differently, or that only usages display and they disagree, is
treated as unknown. A code the packages do not display is reported. No display is
composed here. See ``build_display_index``.

Selecting versus creating
-------------------------
A ``where(...)`` that selects nothing is read as a requirement, but not always as a
requirement for a *new* element. Three states are distinguished: a list entry that
satisfies the selector, one that conflicts with it, and one that is merely silent
where the selector states a value. Where exactly one entry conflicts with nothing,
the asserted values are written into that entry; where several do, the choice is
ambiguous and an entry is appended. ``conflicts`` carries the full reasoning, and
the ICU Extended pressure features show why it is not cosmetic: read as
"append", three selectors turn a three-component body into a six-component one and
the feature contradicts its own ``component.count() = 3``.

The precondition prose
----------------------
Some of what a feature requires it states only in the prose of its
``@Precondition`` scenario and not in any typed step - the display of a LOINC code,
the unit of a quantity, the system of an identifier. ``extract_requirements.py``
deliberately does not translate that prose into JSON, and is right not to: the
prose is irregular and the judgement calls belong to a person reading it. This tool
therefore reads the prose only for six constructs that the suite states
formulaically in every feature that states them at all, and for nothing else:

    * Code: 2160-0                          the display of a code
      * Display: Creatinin [Masse/Volumen] in Serum oder Plasma
    * Value:                                the unit belonging to a UCUM code
      - Quantity:
        * Unit: degree Celsius
        * Code: Cel
    * Identifier: any (system: https://fhir.krankenhaus.example/sid/observation)
                                            the system an identifier must carry
    * Identifier:                           an identifier stated outright
      - System: https://fhir.krankenhaus.example/sid/observation
        Value: ICUmin-OBS-009
    * Effective date/time: 2026-03-12T08:46:00+01:00
                                            which member of a choice is meant
    * Issued: 2024-05-14T09:15:00+02:00     the whole value, where a step asserts
                                            only a substring of it

Only the features a resource's own ``usedBy`` names are read for that resource, and
a construct the resource's features state two different ways is discarded rather
than resolved. Every value taken this way is printed with the file and line it was
taken from, so that each is auditable against the suite text without rerunning the
tool; ``--prose-facts`` writes the same citations to a file.

Where the prose says an identifier may be ``any`` of a given system, the system is
written as stated and the value is the resource's own id - a value the feature
explicitly leaves open, made deterministic and traceable rather than arbitrary.

The fifth construct settles a disagreement between a package example and the
feature that uses it. A feature whose prose reads ``* Effective date/time:`` asserts
``effective.toString().contains(...)``, which a body carrying the example's
``effectivePeriod`` cannot satisfy; where the prose names the type, the body is
corrected to it. The other form, ``* Effective Period:`` with ``- Start:`` and
``- End:``, is left as a period. Nothing is converted without the prose saying so.

The sixth settles a disagreement between a step and the specification it tests.
Steps of the form ``issued.toString().contains('2024-05-14')`` assert a substring of
the value the same feature's prose states in full, ``* Issued:
2024-05-14T09:15:00+02:00``. Writing the substring satisfies the step and misstates
the specification, and where the element is an ``instant`` - ``Observation.issued``
is one - the substring is not a legal value at all: a server conforming to FHIR R4
rejects it. So where the prose states a value of which the asserted literal is a
substring, that value is written, and a value already containing the literal is left
alone.

Two narrower readings are applied, each recorded with the body it affected:

- A feature stating only ``* Identifier: any``, with no system, in a module whose
  other features state exactly one system, is given that system. This is the only
  place where one feature's prose is read for another feature's resource, and it
  applies to nothing but an identifier the reading feature itself calls ``any``.
- ``Quantity.unit`` is required to exist by several steps that state no text for it.
  Where the prose states none either, the UCUM code the step itself asserts is
  written as the unit text, that being the one text which cannot contradict the
  step. On the 3.0.9 corpus this applies once, to a feature whose prose and whose
  step name different units; see
  ``docs/en/08-known-limitations.md``.

Not written by this tool
------------------------
Any requirement whose value neither a typed step nor one of the prose constructs
above states is reported, never synthesised. Such a value would be ours
to choose, and a value of our choosing does not belong in test data that is about
to produce evidence.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from derive_testdata import REFERENCE_KEYS  # noqa: E402

# FHIR elements that are list-valued where a reference assertion names them without
# an index. Extracted from the R4 base definitions of the resources this rig seeds;
# an element not listed here is written as a single object.
LIST_VALUED = {"performer", "basedOn", "partOf", "hasMember", "derivedFrom",
               "focus", "reasonReference", "supportingInfo", "author"}

# FHIR elements that are lists, as named inside a FHIRPath chain. A `where` on one
# of these selects among its entries; a `where` on anything else constrains the
# single object. Taken from the R4 base definitions of the resources in scope.
LIST_ELEMENTS = {"coding", "component", "referenceRange", "note", "identifier",
                 "category", "interpretation", "performer", "appliesTo",
                 "basedOn", "hasMember", "derivedFrom", "result", "extension"}

# Where R4 gives the same element name a different cardinality per resource. Both
# directions are needed: `specimen` is a list on DiagnosticReport and a single
# Reference on Observation, and a chain cannot be walked correctly without knowing
# which resource it is being walked in.
LIST_ELEMENTS_BY_TYPE = {"DiagnosticReport": {"specimen"}}
SCALAR_ELEMENTS_BY_TYPE = {"Observation": {"specimen"}}

# Cardinality also depends on the element one level up, not only on the resource.
# `identifier` is 0..* on every resource in scope and 0..1 inside a `Reference`, so
# `specimen.identifier.where(...)` must create an object where `identifier.where(...)`
# creates a list entry. Writing a list produced eight bodies IRIS rejected at the
# seed phase with "Property 'identifier' of Type 'Reference' cannot be an Array".
#
# The element names below are the ones a Stufe 5 assertion navigates *into* and that
# R4 defines as `Reference` on the resource carrying them: the four locations
# `derive_testdata.REFERENCE_KEYS` rewrites, which is where this corpus's references
# are, plus `Observation.specimen`. It is deliberately not a FHIR-wide table of
# Reference-valued elements - `performer` is a Reference on Observation and a
# BackboneElement on MedicationAdministration, and a list that got such a name wrong
# would mis-shape a child element rather than fail - so it names only what is needed
# and verified. See docs/en/08-known-limitations.md on reading this from the R4 base
# definitions instead, as choice_type_check.py already does.
REFERENCE_ELEMENTS = {"specimen"} | {
    path.rsplit(".", 1)[-1].removesuffix("[]") for path in REFERENCE_KEYS}

# The children R4 gives a `Reference`. All are 0..1 or a scalar; `extension` is the
# only list a Reference carries and is handled by LIST_ELEMENTS.
REFERENCE_CHILDREN = {"reference", "type", "identifier", "display"}

# Date and time elements that are *not* FHIR choice elements, so a feature naming
# one names a key that may simply be absent and can be created. From the R4 base
# definitions; an element not listed here and not present in the body is reported.
NON_CHOICE_DATETIME = {"issued", "authoredOn", "recordedDate", "date", "created",
                       "lastUpdated", "start", "end"}

# FHIR choice elements - the ones a feature names without the type suffix the body
# must carry. From the R4 base definitions of the resources in scope. An element not
# listed here is created as a plain object, which is what `device`, `low` and `high`
# are; getting this list wrong would invent a `device[x]` that FHIR does not define.
CHOICE_ELEMENTS = {"value", "effective", "onset", "abatement", "occurrence",
                   "performed", "deceased", "multipleBirth", "started"}

# How to name a choice element the body does not yet carry. The type is read off
# the fields the feature itself asserts, so it is determined by the feature and not
# chosen here; a set of fields matching no entry is reported.
CHOICE_TYPE_BY_FIELDS = (
    ({"value", "unit", "system", "code"}, "Quantity"),
    ({"value", "code", "system"}, "Quantity"),
    ({"coding"}, "CodeableConcept"),
    ({"start", "end"}, "Period"),
)

# The code system of every Quantity the ISiK features assert.
UCUM = "http://unitsofmeasure.org"

# The choice elements the ISiK features name without their type suffix.
RE_CHOICE_SUFFIX = re.compile(r"^(?P<base>[a-z][A-Za-z0-9]*?)(?P<suffix>[A-Z][A-Za-z0-9]*)$")

RE_ATTR_FIELD = re.compile(r"^\$\.\.(?P<field>[A-Za-z][A-Za-z0-9]*)\.value$")
# A literal is a match expression with no regex metacharacter in it.
RE_HAS_META = re.compile(r"[\\^$.|?*+()\[\]{}]")

RE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
RE_CONTAINS = re.compile(r"^contains\((?P<q>['\"])(?P<lit>.*)(?P=q)\)$")
RE_TERM_EQ = re.compile(
    r"^(?P<path>[A-Za-z][A-Za-z0-9_.]*)\s*(?P<op>=|~)\s*"
    r"(?:(?P<q>['\"])(?P<text>.*)(?P=q)|(?P<num>-?\d+(?:\.\d+)?))$")
RE_COUNT_CMP = re.compile(r"^(?P<expr>.+?)\s*=\s*(?P<num>\d+)$")


class Unsupported(Exception):
    """The expression lies outside the grammar this module accepts."""


@dataclasses.dataclass
class Prose:
    """What a feature states only in the prose of its precondition scenario."""

    displays: dict[str, str] = dataclasses.field(default_factory=dict)
    units: dict[str, str] = dataclasses.field(default_factory=dict)
    identifier_systems: list[str] = dataclasses.field(default_factory=list)
    # The identifier the prose states outright, where it states one.
    identifiers: list[dict] = dataclasses.field(default_factory=list)
    # The prose says the identifier may be anything and names no system for it.
    identifier_open: bool = False
    # The identifier systems the *other* features of the same module state. Read
    # only when identifier_open holds; never used to override a resource's own prose.
    module_identifier_systems: list[str] = dataclasses.field(default_factory=list)
    # Base element -> the FHIR type the prose names for it, e.g. effective ->
    # DateTime from `* Effective date/time:`. Settles a choice the body got wrong.
    choice_types: dict[str, str] = dataclasses.field(default_factory=dict)
    # Element name -> the full instant the prose states for it, e.g. issued ->
    # 2024-05-14T09:15:00+02:00. Read only to satisfy a `.contains('<date>')` step
    # with the value the same feature states, instead of with the bare substring.
    timestamps: dict[str, str] = dataclasses.field(default_factory=dict)
    citations: list[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass(frozen=True)
class Ctx:
    """What the walker needs besides the body: which resource, and what is stated."""

    resource_type: str
    resource_id: str
    displays: dict[tuple[str, str], str]
    prose: Prose
    used: list[str] = dataclasses.field(default_factory=list)

    def display_for(self, code, system) -> str | None:
        """The display the specification gives a code, by package then by prose."""
        if not isinstance(code, str):
            return None
        found = self.displays.get((code, system))
        if found is None:
            found = self.prose.displays.get(code)
            if found is not None:
                self.used.append(f"display of {code!r} from the precondition prose")
        return found

    def is_list(self, name: str, parent: str | None = None) -> bool:
        # The element one level up decides first, where it decides at all: inside a
        # Reference every child is 0..1 (see REFERENCE_ELEMENTS).
        if parent in REFERENCE_ELEMENTS and name in REFERENCE_CHILDREN:
            return False
        if name in SCALAR_ELEMENTS_BY_TYPE.get(self.resource_type, ()):
            return False
        return (name in LIST_ELEMENTS
                or name in LIST_ELEMENTS_BY_TYPE.get(self.resource_type, ()))


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #

def split_top_level(text: str, sep: str) -> list[str]:
    """Split on `sep` at paren depth zero and outside quotes."""
    parts, depth, quote, start, i = [], 0, None, 0, 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and text.startswith(sep, i):
            parts.append(text[start:i])
            i += len(sep)
            start = i
            continue
        i += 1
    parts.append(text[start:])
    return [p.strip() for p in parts if p.strip()]


def parse_chain(expr: str) -> list[str]:
    """Segments of a FHIRPath chain, keeping each `where(...)` whole."""
    return split_top_level(expr, ".")


def parse_literal(match: re.Match):
    if match.group("text") is not None:
        return match.group("text")
    num = match.group("num")
    # Keep an integral literal integral, so `value.value = 75` is not written 75.0.
    return float(num) if "." in num else int(num)


# --------------------------------------------------------------------------- #
# navigation
# --------------------------------------------------------------------------- #

def resolve_name(node: dict, name: str) -> str | None:
    """`effective` -> `effectivePeriod`, where the body carries exactly one such key."""
    if name in node:
        return name
    candidates = [key for key in node
                  if (m := RE_CHOICE_SUFFIX.match(key)) and m.group("base") == name]
    return candidates[0] if len(candidates) == 1 else None


def satisfies(node, conds: list[str], ctx: Ctx) -> bool:
    """Whether `node` already meets every term of `conds`. Pure; never mutates."""
    if isinstance(node, list):
        return any(satisfies(item, conds, ctx) for item in node)
    if not isinstance(node, dict):
        return False
    for term in conds:
        if term.endswith(".exists()") or term.endswith(".empty().not()"):
            inner = term[:-len(".exists()")] if term.endswith(".exists()") \
                else term[:-len(".empty().not()")]
            segments = parse_chain(inner)
            if not segments:
                return False
            try:
                found = navigate(node, segments, ctx, create=False)
            except Unsupported:
                return False
            if found in (None, [], {}, ""):
                return False
            continue
        m = RE_TERM_EQ.match(term)
        if not m:
            raise Unsupported(term)
        path, want = m.group("path"), parse_literal(m)
        segments = parse_chain(path)
        try:
            have = navigate(node, segments, ctx, create=False)
        except Unsupported:
            return False
        if isinstance(have, list):
            if not any(equalish(h, want) for h in have):
                return False
        elif not equalish(have, want):
            return False
    return True


def equalish(have, want) -> bool:
    """Equality as the features use it: `=` and `~` over scalars."""
    if isinstance(have, (int, float)) and isinstance(want, (int, float)):
        return float(have) == float(want)
    return have == want


def conflicts(node, conds: list[str], ctx: Ctx) -> bool:
    """Whether `node` disagrees with anything `conds` states. Pure; never mutates.

    This is the complement of satisfies() for the undecided middle: a node that
    already satisfies the selector does not conflict with it, and neither does one
    that is merely *silent* where the selector states a value. Only a node carrying
    a different value conflicts.

    The distinction decides, in navigate(), between completing an existing list
    entry and appending a second one. Getting it wrong is not cosmetic. The ICU
    Extended pressure features assert

        component.count() = 3
        component.where(code.coding.where(code = '8480-6' and system =
            'http://loinc.org' and version = '2.81').exists()).exists()

    against a package example whose three components carry those very LOINC codes
    without a `version`. Read as "no component satisfies this, so append one", the
    three selectors produce three further components and a body with six, failing
    the count assertion in the same feature - the specification contradicting
    itself, which is never the reading to take. Read as "exactly one component
    disagrees with nothing here, so it is the component this is about", the
    `version` is written into the coding that already carries the code.

    Silence is not agreement, so this is not used to decide whether an assertion
    holds - satisfies() does that, unchanged. It is used only to choose *where* to
    write, and only when exactly one candidate is non-conflicting; two are
    ambiguous and the caller falls back to appending.
    """
    if isinstance(node, list):
        # One entry that can be completed means the selector is about that entry.
        # An empty list conflicts with nothing: its entry is still to be created.
        return bool(node) and all(conflicts(item, conds, ctx) for item in node)
    if not isinstance(node, dict):
        return True
    for term in conds:
        inner = None
        if term.endswith(".exists()"):
            inner = term[:-len(".exists()")]
        elif term.endswith(".empty().not()"):
            inner = term[:-len(".empty().not()")]
        if inner is not None:
            segments = parse_chain(inner)
            if not segments:
                return True
            # A trailing `where` has to be recursed into rather than merely looked
            # up. `code.coding.where(...)` selecting nothing says nothing about
            # agreement: it is the case where the coding exists and lacks exactly
            # what the selector states - the case this function exists for.
            if segments[-1].startswith("where(") and segments[-1].endswith(")"):
                try:
                    host = navigate(node, segments[:-1], ctx, create=False)
                except Unsupported:
                    return True
                if host in (None, [], {}, ""):
                    continue            # absent entirely: nothing to disagree with
                sub = split_top_level(segments[-1][len("where("):-1], " and ")
                if conflicts(host, sub, ctx):
                    return True
                continue
            try:
                navigate(node, segments, ctx, create=False)
            except Unsupported:
                return True
            # An absent path is what `create` would fill in, not a disagreement.
            continue
        m = RE_TERM_EQ.match(term)
        if not m:
            return True
        want = parse_literal(m)
        try:
            have = navigate(node, parse_chain(m.group("path")), ctx, create=False)
        except Unsupported:
            return True
        if have is None:
            continue
        if isinstance(have, list):
            if not any(equalish(h, want) for h in have):
                return True
        elif not equalish(have, want):
            return True
    return False


def choice_key(name: str, conds: list[str]) -> str:
    """Name a choice element from the fields the feature asserts about it."""
    fields = set()
    for term in conds:
        m = RE_TERM_EQ.match(term)
        head = (m.group("path") if m else term).split(".", 1)[0]
        fields.add(head.removesuffix("()"))
    for required, suffix in CHOICE_TYPE_BY_FIELDS:
        if required <= fields:
            return f"{name}{suffix}"
    raise Unsupported(f"{name}: the body carries no {name}[x] and the asserted "
                      f"fields ({', '.join(sorted(fields))}) name no FHIR type")


def navigate(node, segments: list[str], ctx: Ctx, create: bool, conds_ahead=(),
             parent: str | None = None):
    """Walk a chain of names and `where(...)` selectors.

    With `create` false this is a lookup returning None where the path is absent.
    With `create` true, a missing name is created and a `where` that selects
    nothing appends an element built from its own conditions - which is the sense
    in which a feature's `exists()` is read as a requirement.

    `parent` is the element `node` itself sits at, where the caller knows it. It is
    what lets the cardinality of a child be decided by its container as well as by
    the resource - see Ctx.is_list. A caller that does not know it passes nothing,
    and a chain walked from the resource root never needs to.
    """
    cur = node
    for index, segment in enumerate(segments):
        if segment.startswith("where("):
            if not segment.endswith(")"):
                raise Unsupported(segment)
            conds = split_top_level(segment[len("where("):-1], " and ")
            if isinstance(cur, list):
                match = next((item for item in cur if satisfies(item, conds, ctx)), None)
                if match is None:
                    if not create:
                        return None
                    # Complete before appending, where that choice is forced: an
                    # entry that disagrees with nothing the selector states, and the
                    # only such entry, is the entry the selector is about. See
                    # conflicts() for why appending instead makes a feature
                    # contradict its own `count()` assertion.
                    completable = [item for item in cur
                                   if not conflicts(item, conds, ctx)]
                    if len(completable) == 1:
                        match = completable[0]
                    else:
                        match = {}
                        cur.append(match)
                    enforce_conds(match, conds, ctx, at=parent)
                cur = match
            elif isinstance(cur, dict):
                if not satisfies(cur, conds, ctx):
                    if not create:
                        return None
                    enforce_conds(cur, conds, ctx, at=parent)
                # else: the single object already satisfies the selector
            else:
                return None
            continue

        if segment in ("exists()", "first()", "toString()"):
            continue
        if segment in ("empty()", "not()", "count()") or RE_CONTAINS.match(segment):
            raise Unsupported(".".join(segments))
        if not RE_NAME.match(segment):
            raise Unsupported(segment)

        if isinstance(cur, list):
            # A name after a list without a `where`: the features use this to mean
            # "of any entry". Unambiguous only for a list of one, and an empty list
            # is one this walk has just created, so its single entry belongs here.
            if not cur and create:
                cur.append({})
            if len(cur) == 1 and isinstance(cur[0], dict):
                cur = cur[0]
            elif not create:
                return None
            else:
                raise Unsupported(f"{'.'.join(segments[:index + 1])}: the body has "
                                  f"{len(cur)} entries and the assertion names no "
                                  f"`where` to choose among them")
        if not isinstance(cur, dict):
            return None

        key = resolve_name(cur, segment)
        if key is None:
            if not create:
                return None
            if segment in NON_CHOICE_DATETIME:
                key = segment
                cur[key] = None
            elif ctx.is_list(segment, parent):
                key = segment
                cur[key] = []
            elif (segment in CHOICE_ELEMENTS and index + 1 < len(segments)
                    and segments[index + 1].startswith("where(")):
                # A `where` follows, so the element's own fields name its type.
                key = choice_key(segment, split_top_level(
                    segments[index + 1][len("where("):-1], " and "))
                cur[key] = {}
            elif segment in CHOICE_ELEMENTS and conds_ahead:
                key = choice_key(segment, list(conds_ahead))
                cur[key] = {}
            else:
                key = segment
                cur[key] = {}
        parent = segment
        cur = cur[key]
    return cur


def enforce_conds(node: dict, conds: list[str], ctx: Ctx,
                  at: str | None = None) -> list[str]:
    """Write every scalar equality of `conds` into `node`; recurse into nested terms.

    `at` is the element `node` itself sits at, passed on to navigate() so a child's
    cardinality can be decided by its container. See Ctx.is_list.
    """
    written: list[str] = []
    for term in conds:
        # Before the generic `.exists()` below, which would otherwise shadow it: a
        # unit the feature requires without stating, but whose text the precondition
        # prose gives for that UCUM code.
        if term == "unit.exists()" and not node.get("unit"):
            unit = ctx.prose.units.get(node.get("code"))
            if unit is None and node.get("system") == UCUM \
                    and isinstance(node.get("code"), str):
                # The step requires a unit text but states none, and the prose
                # names none for this code either - which happens where the prose
                # and the step disagree about the code. `Quantity.unit` is the
                # human-readable rendering of `Quantity.code` and the step requires
                # nothing of it but existence, so the code the step itself asserts
                # is the one text that cannot contradict the step.
                unit = node["code"]
                ctx.used.append(f"unit text taken from the asserted UCUM code "
                                f"{unit!r}, no unit being stated for it")
            if unit is None:
                raise Unsupported(f"unit must exist; neither a step nor the "
                                  f"precondition prose states one for code "
                                  f"{node.get('code')!r}")
            node["unit"] = unit
            ctx.used.append(f"unit of {node.get('code')!r} from the precondition prose")
            written.append(f"unit = {unit!r}")
            continue
        if term.endswith(".exists()"):
            inner = term[:-len(".exists()")]
            segments = parse_chain(inner)
            target = navigate(node, segments, ctx, create=True, parent=at)
            if target in (None, {}, []) and not segments[-1].startswith("where("):
                raise Unsupported(f"{inner} must exist and the feature states no "
                                  f"value for it")
            continue
        if term.endswith(".empty().not()"):
            inner = term[:-len(".empty().not()")]
            segments = parse_chain(inner)
            if navigate(node, segments, ctx, create=False, parent=at) in (None, [], {}, ""):
                # The one such requirement the ISiK features raise is on a coding's
                # display, which the specification itself supplies (see Displays).
                if segments == ["display"]:
                    display = ctx.display_for(node.get("code"), node.get("system"))
                    if display is None:
                        raise Unsupported(
                            f"coding {node.get('code')!r} must carry a display and "
                            f"the pinned packages give this code no unambiguous one")
                    node["display"] = display
                    written.append(f"display = {display!r}")
                    continue
                raise Unsupported(f"{inner} must be non-empty and the feature states "
                                  f"no value for it")
            continue
        m = RE_TERM_EQ.match(term)
        if not m:
            raise Unsupported(term)
        want = parse_literal(m)
        segments = parse_chain(m.group("path"))
        parent = (navigate(node, segments[:-1], ctx, create=True, parent=at,
                           conds_ahead=conds) if len(segments) > 1 else node)
        if isinstance(parent, list):
            # An empty list is one this walk has just created, so the entry the
            # assertion describes is its first.
            if not parent:
                parent.append({})
            if len(parent) != 1:
                raise Unsupported(f"{'.'.join(segments[:-1])} has {len(parent)} "
                                  f"entries and the assertion names no `where` to "
                                  f"choose among them")
            parent = parent[0]
        if not isinstance(parent, dict):
            raise Unsupported(term)
        leaf = resolve_name(parent, segments[-1]) or segments[-1]
        if not equalish(parent.get(leaf), want):
            parent[leaf] = want
            written.append(f"{'.'.join(segments)} = {want!r}")
    return written


def enforce(doc: dict, expr: str, ctx: Ctx) -> list[str]:
    """Read one feature expression as a requirement and make the body meet it."""
    count = RE_COUNT_CMP.match(expr)
    if count and count.group("expr").endswith(".count()"):
        path = count.group("expr")[:-len(".count()")]
        have = navigate(doc, parse_chain(path), ctx, create=False)
        want = int(count.group("num"))
        size = len(have) if isinstance(have, list) else (0 if have is None else 1)
        if size != want:
            raise Unsupported(f"{path}.count() = {want}; the body has {size} - "
                              f"which entries are missing is not stated by this "
                              f"assertion")
        return []

    # A bare equality: `note.text = "..."`, `device.display = '...'`, `value.code = ...`
    if RE_TERM_EQ.match(expr):
        return enforce_conds(doc, [expr], ctx)

    segments = parse_chain(expr)

    # `<path>.toString().contains('<literal>')`: a literal date or time.
    if len(segments) >= 2 and RE_CONTAINS.match(segments[-1]) \
            and segments[-2] == "toString()":
        literal = RE_CONTAINS.match(segments[-1]).group("lit")
        head = segments[:-2]
        parent = navigate(doc, head[:-1], ctx, create=True) if len(head) > 1 else doc
        if isinstance(parent, list):
            if len(parent) != 1:
                raise Unsupported(expr)
            parent = parent[0]
        if not isinstance(parent, dict):
            raise Unsupported(expr)
        key = resolve_name(parent, head[-1])
        if key is None:
            if head[-1] in NON_CHOICE_DATETIME:
                key = head[-1]
            else:
                raise Unsupported(f"{head[-1]} must contain {literal!r} but the body "
                                  f"carries no such element - the choice type is not "
                                  f"determined by the feature")
        if isinstance(parent.get(key), dict):
            # The body carries the wrong member of a choice: the example has
            # `effectivePeriod`, the assertion wants a value. The feature's own
            # precondition prose names the type (`* Effective date/time:`), so
            # take the correction from there and from nowhere else.
            base = head[-1]
            stated = ctx.prose.choice_types.get(base.lower()) if base in CHOICE_ELEMENTS else None
            if stated is None or f"{base}{stated}" == key:
                raise Unsupported(f"{key} must contain {literal!r} but it is an "
                                  f"object, not a value - which of its parts is "
                                  f"meant is not determined by the feature")
            # The value, like the type, is taken from the prose where the prose
            # states one of which the asserted literal is a substring.
            value = ctx.prose.timestamps.get(base.lower())
            if value is None or literal not in value:
                value = literal
            del parent[key]
            parent[f"{base}{stated}"] = value
            ctx.used.append(f"{base} is a {stated} per the precondition prose, "
                            f"replacing the example's {key}")
            return [f"{key} -> {base}{stated} = {value}"]
        current = parent.get(key)
        # The step asserts a substring; the prose of the same feature states the
        # whole value. Writing the substring satisfies the step and misstates the
        # specification, and for an `instant` element the substring is not even a
        # legal value: `Observation.issued` is an instant, so `2024-05-14` is
        # rejected with "malformed 'instant'" while the prose's
        # `2024-05-14T09:15:00+02:00` is accepted and contains it.
        #
        # Checked before the body's current value, not after: a body already
        # carrying the bare literal satisfies the step, so a rule that stopped
        # there would leave the wrong value in place and make the outcome depend
        # on what the body happened to arrive with. As written, the tool converges
        # on the same body from any starting point and running it twice is a no-op.
        stated = ctx.prose.timestamps.get(head[-1].lower())
        if stated is not None and literal in stated:
            if current == stated:
                return []
            parent[key] = stated
            ctx.used.append(f"{head[-1]} = {stated} from the precondition prose, "
                            f"of which the asserted {literal!r} is a substring")
            return [f"{key} = {stated}"]
        if isinstance(current, str) and literal in current:
            # Already satisfied, and the prose states nothing fuller. `contains` is
            # a substring test, so narrowing the value to the bare literal would
            # change a body that passes into a different body that also passes.
            return []
        if current != literal:
            parent[key] = literal
            return [f"{key} = {literal}"]
        return []

    # `<path>.exists()` and `<path>.where(...).exists()`
    if segments[-1] == "exists()":
        before = json.dumps(doc, sort_keys=True)
        navigate(doc, segments[:-1], ctx, create=True)
        after = json.dumps(doc, sort_keys=True)
        return [expr] if before != after else []

    raise Unsupported(expr)


# --------------------------------------------------------------------------- #
# inputs
# --------------------------------------------------------------------------- #

def load_contract(contract_dir: pathlib.Path, namespaces: set[str]) -> dict[str, str]:
    """Qualified key -> value, for checking a placeholder against its target."""
    import yaml
    values: dict[str, str] = {}
    for path in sorted(contract_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            continue
        ns = next((n for n in namespaces
                   if n.replace("-", "") == path.stem.replace("-", "")), path.stem)
        for key, value in data.items():
            values[f"{ns}.{key}"] = str(value)
    return values


def build_display_index(pkg_dirs: list[pathlib.Path]) -> dict[tuple[str, str], str]:
    """(code, system) -> display, as the pinned packages themselves give it.

    Two sources, both the specification's own, and not of equal standing:

    `definitions`
        the `concept` list of a CodeSystem the packages ship. This is the
        specification *defining* the display of a code.
    `usages`
        any Coding occurring in a package resource - overwhelmingly in the example
        instances. This is the specification *using* a code, which is weaker
        evidence and not always careful: `hl7.fhir.r4.examples#4.0.1` writes
        v3-ObservationInterpretation `N` as "normal" in Observation-blood-pressure
        while the CodeSystem in `hl7.fhir.r4.core#4.0.1` defines it as "Normal".

    A definition therefore wins over any number of usages, and usages are consulted
    only for a code no shipped CodeSystem defines - which is the normal case here,
    since LOINC and SNOMED CT are not redistributable and ship as usages only.
    Within each source a pair displayed two different ways is dropped rather than
    resolved, and an ambiguous definition drops the pair outright rather than
    falling back to a usage: two CodeSystems disagreeing is not a question a usage
    answers. A display is consequently only ever written where the specification is
    unambiguous at the strongest level of evidence available for that code.
    """
    definitions: dict[tuple[str, str], set[str]] = {}
    usages: dict[tuple[str, str], set[str]] = {}

    def note(into, code, system, display) -> None:
        if isinstance(code, str) and isinstance(system, str) and isinstance(display, str):
            into.setdefault((code, system), set()).add(display)

    def walk(node) -> None:
        if isinstance(node, dict):
            note(usages, node.get("code"), node.get("system"), node.get("display"))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    def walk_codesystem(doc: dict) -> None:
        url = doc.get("url")
        if not isinstance(url, str):
            return

        def concepts(nodes) -> None:
            for concept in nodes or []:
                if not isinstance(concept, dict):
                    continue
                note(definitions, concept.get("code"), url, concept.get("display"))
                concepts(concept.get("concept"))

        concepts(doc.get("concept"))

    for pkg_dir in pkg_dirs:
        for path in sorted(pkg_dir.rglob("*.json")):
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                continue
            if not isinstance(doc, dict):
                continue
            walk(doc)
            if doc.get("resourceType") == "CodeSystem":
                walk_codesystem(doc)

    index = {pair: next(iter(displays)) for pair, displays in usages.items()
             if len(displays) == 1}
    for pair, displays in definitions.items():
        if len(displays) == 1:
            index[pair] = next(iter(displays))
        else:
            index.pop(pair, None)
    return index


# The formulaic prose constructs, and nothing else. A value in parentheses
# such as `(can be configured with the property 'loinc-version')` is not a value.
RE_PROSE_KV = re.compile(r"^[-*\s]*(?P<key>Code|Display|Unit|System|Value|Identifier)"
                         r":\s*(?P<value>.+?)\s*$")
RE_PROSE_ANY_SYSTEM = re.compile(r"^any\s*\(system:\s*(?P<system>\S+?)\)\s*$")
# Bare `any`, with no parenthetical at all: the prose declares the identifier
# unconstrained. Deliberately anchored, so the Basis form `Any (please set in the
# configuration variables ...)` - which says something quite different - does not
# match it.
RE_PROSE_ANY_BARE = re.compile(r"^any$", re.IGNORECASE)
# `* Effective date/time: 2026-03-12T08:46:00+01:00` names the choice type as well
# as the value; `* Effective Period:` names the other one. A body whose example
# carries the other form is corrected, because the prose is what the feature states.
RE_PROSE_CHOICE = re.compile(
    r"^[-*\s]*(?P<base>Effective|Value|Onset|Occurrence)\s+"
    r"(?P<kind>date/time|dateTime|Period|Quantity|Range)\s*:", re.IGNORECASE)
PROSE_CHOICE_TYPES = {"date/time": "DateTime", "datetime": "DateTime",
                      "period": "Period", "quantity": "Quantity", "range": "Range"}
RE_PROSE_VALUE_UNIT = re.compile(r"^(?P<num>-?\d+(?:[.,]\d+)?)\s+(?P<unit>\S+)$")
RE_PROSE_PARENTHETICAL = re.compile(r"^\(.*\)$")
# `* Issued: 2024-05-14T09:15:00+02:00`, and the value half of the choice-type line
# above. A date alone is deliberately not matched: the point of reading these is to
# recover the full instant the prose states where a step asserts only a substring of
# it, and a date-only statement would recover nothing.
RE_PROSE_KV_ANY = re.compile(r"^[-*\s]*(?P<key>[A-Z][A-Za-z/ ]*?)\s*:\s*(?P<value>.+?)\s*$")
RE_PROSE_INSTANT = re.compile(
    r"^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r"T(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d+)?)?"
    r"(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)?$")


def module_systems(feature_root: pathlib.Path, rel_paths: list[str]) -> list[str]:
    """The identifier systems stated anywhere in the modules a resource is used by.

    A module whose features state one system uniformly, with a single feature
    omitting the parenthetical, states that system for the module. This is the only
    place where one feature's prose is read for another's resource, and it is used
    for nothing but an identifier the reading feature itself calls `any`.
    """
    systems: set[str] = set()
    for directory in {str(pathlib.PurePosixPath(rel).parent) for rel in rel_paths}:
        for path in sorted((feature_root / directory).glob("*.feature")):
            for raw in path.read_text(encoding="utf-8").splitlines():
                m = RE_PROSE_KV.match(raw)
                if m and m.group("key") == "Identifier":
                    hit = RE_PROSE_ANY_SYSTEM.match(m.group("value").strip())
                    if hit:
                        systems.add(hit.group("system"))
    return sorted(systems)


def mine_prose(feature_root: pathlib.Path, rel_paths: list[str]) -> Prose:
    """Read the formulaic constructs out of a resource's own feature files.

    Conflicting statements are dropped: a code the resource's features display two
    different ways, or two identifier systems, is reported by its absence rather
    than resolved here.
    """
    displays: dict[str, set[str]] = {}
    units: dict[str, set[str]] = {}
    systems: set[str] = set()
    identifiers: list[dict] = []
    choice_types: dict[str, set[str]] = {}
    timestamps: dict[str, set[str]] = {}
    identifier_open = False
    citations: list[str] = []

    for rel in rel_paths:
        path = feature_root / rel
        if not path.is_file():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        current_code: str | None = None
        quantity: dict[str, str] = {}
        pending_identifier: dict[str, str] | None = None
        pending_since = 0
        for number, raw in enumerate(lines, start=1):
            choice = RE_PROSE_CHOICE.match(raw)
            if choice:
                base = choice.group("base").lower()
                kind = PROSE_CHOICE_TYPES[choice.group("kind").lower()]
                choice_types.setdefault(base, set()).add(kind)
                citations.append(f"{rel}:{number}  {base} is a {kind}")
            # Any `* <Label>: <instant>` line. Keyed by the label's first word
            # lowercased, which is the element name for both forms the features use:
            # `Issued` -> issued, `Effective date/time` -> effective.
            kv_any = RE_PROSE_KV_ANY.match(raw)
            if kv_any and RE_PROSE_INSTANT.match(kv_any.group("value")):
                element = kv_any.group("key").split()[0].lower()
                timestamps.setdefault(element, set()).add(kv_any.group("value"))
                citations.append(f"{rel}:{number}  {element} = "
                                 f"{kv_any.group('value')}")
            # `* Identifier:` on its own line introduces a stated System/Value pair.
            if re.match(r"^[-*\s]*Identifier:\s*$", raw):
                pending_identifier, pending_since = {}, number
            elif pending_identifier is not None and number - pending_since > 3:
                pending_identifier = None
            m = RE_PROSE_KV.match(raw)
            if "Quantity:" in raw:
                quantity = {}
            if not m:
                continue
            key, value = m.group("key"), m.group("value")
            if RE_PROSE_PARENTHETICAL.match(value) and key != "Identifier":
                continue
            where = f"{rel}:{number}"

            if pending_identifier is not None and key in ("System", "Value"):
                pending_identifier[key] = value
                if {"System", "Value"} <= pending_identifier.keys():
                    identifiers.append({"system": pending_identifier["System"],
                                        "value": pending_identifier["Value"]})
                    citations.append(f"{where}  identifier = "
                                     f"{pending_identifier['System']} | "
                                     f"{pending_identifier['Value']}")
                    pending_identifier = None
                continue

            if key == "Code":
                current_code = value
                quantity["Code"] = value
            elif key == "Display" and current_code:
                displays.setdefault(current_code, set()).add(value)
                citations.append(f"{where}  display of {current_code} = {value!r}")
            elif key == "Unit":
                quantity["Unit"] = value
            elif key == "Value":
                vu = RE_PROSE_VALUE_UNIT.match(value)
                if vu:
                    # `* Value: 0.7 mg/dL` - the only unit text the feature states
                    # for a quantity written this way is the token itself.
                    units.setdefault(vu.group("unit"), set()).add(vu.group("unit"))
                    citations.append(f"{where}  unit of {vu.group('unit')} = "
                                     f"{vu.group('unit')!r}")
            elif key == "Identifier":
                any_system = RE_PROSE_ANY_SYSTEM.match(value)
                if any_system:
                    systems.add(any_system.group("system"))
                    citations.append(f"{where}  identifier system = "
                                     f"{any_system.group('system')!r}, value left open")
                elif RE_PROSE_ANY_BARE.match(value):
                    identifier_open = True
                    citations.append(f"{where}  identifier unconstrained ('any')")

            if "Unit" in quantity and "Code" in quantity:
                units.setdefault(quantity["Code"], set()).add(quantity["Unit"])
                citations.append(f"{where}  unit of {quantity['Code']} = "
                                 f"{quantity['Unit']!r}")
                quantity = {}

    return Prose(
        displays={c: next(iter(v)) for c, v in displays.items() if len(v) == 1},
        units={c: next(iter(v)) for c, v in units.items() if len(v) == 1},
        identifier_systems=sorted(systems),
        identifiers=identifiers,
        identifier_open=identifier_open,
        module_identifier_systems=module_systems(feature_root, rel_paths),
        choice_types={b: next(iter(v)) for b, v in choice_types.items()
                      if len(v) == 1},
        timestamps={e: next(iter(v)) for e, v in timestamps.items()
                    if len(v) == 1},
        citations=citations,
    )


# Every element FHIR R4 gives a Coding, plus the two every element may carry. A
# Quantity also has `code` and `system`, and has no `display` at all, so matching on
# code+system alone would write an element that does not exist into every asserted
# measurement - rejected by the server as an unknown property, and rejected for the
# right reason. Membership is checked against the whole node rather than by element
# path because the walker has no schema; a node whose keys are all a Coding's keys
# is a Coding.
CODING_KEYS = frozenset({"system", "version", "code", "display", "userSelected",
                         "id", "extension"})


def is_coding(node: dict) -> bool:
    """Whether a dict carrying `code` and `system` is a Coding and not a Quantity."""
    return set(node) <= CODING_KEYS


def fill_displays(doc, ctx: Ctx) -> list[str]:
    """Give every coding in the body the display the specification gives that code."""
    written: list[str] = []

    def walk(node, where: str) -> None:
        if isinstance(node, dict):
            code, system = node.get("code"), node.get("system")
            if (isinstance(code, str) and isinstance(system, str)
                    and not node.get("display") and is_coding(node)):
                display = ctx.display_for(code, system)
                if display is not None:
                    node["display"] = display
                    written.append(f"{where}.display = {display!r}")
            for key, value in node.items():
                walk(value, f"{where}.{key}" if where else key)
        elif isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, f"{where}[{index}]")

    walk(doc, "")
    return written


# --------------------------------------------------------------------------- #
# per-resource reconciliation
# --------------------------------------------------------------------------- #

def reconcile(doc: dict, row: dict, namespace: str, contract: dict[str, str],
              displays: dict[tuple[str, str], str],
              prose: Prose) -> tuple[list[str], list[str], list[str]]:
    """Apply what the assertions determine.

    Returns (changes, items left for a human, prose facts relied on).
    """
    changes: list[str] = []
    unhandled: list[str] = []
    ctx = Ctx(resource_type=row["resourceType"], resource_id=row["id"],
              displays=displays, prose=prose)

    for a in row.get("assertions") or []:
        kind = a.get("kind")

        if kind == "reference":
            element, target = a.get("element"), a.get("target") or ""
            # REFERENCE_KEYS is keyed on the body path, where a list-valued element
            # carries a `[]` suffix; a feature names the element plainly.
            data_key = REFERENCE_KEYS.get(element) or REFERENCE_KEYS.get(f"{element}[]")
            if data_key is None:
                unhandled.append(f"reference on {element!r}: no entry in REFERENCE_KEYS")
                continue
            qualified = f"{namespace}.{data_key}"
            expected_id = target.split("/", 1)[1] if "/" in target else target
            if contract.get(qualified) != expected_id:
                unhandled.append(
                    f"reference on {element!r} asserts {target}, but {qualified} is "
                    f"{contract.get(qualified)!r} - the mapping would write the wrong id")
                continue
            placeholder = f"{target.split('/', 1)[0]}/{{{{{qualified}}}}}"
            current = doc.get(element)
            # Keep any sibling the example carries, e.g. `device.display`.
            existing = (current[0] if isinstance(current, list) and current
                        and isinstance(current[0], dict)
                        else current if isinstance(current, dict) else {})
            body = dict(existing)
            if body.get("reference") != placeholder:
                body["reference"] = placeholder
                doc[element] = [body] if element in LIST_VALUED else body
                changes.append(f"{element} -> {placeholder}")
            continue

        if kind == "attribute":
            m = RE_ATTR_FIELD.match(a.get("path") or "")
            match = a.get("matches") or ""
            if not m or RE_HAS_META.search(match):
                unhandled.append(f"attribute {a.get('path')} matches {match!r}")
                continue
            field = m.group("field")
            if doc.get(field) != match:
                doc[field] = match
                changes.append(f"{field} = {match}")
            continue

        if kind == "fhirpath":
            expr = (a.get("expr") or "").strip()
            if expr == "identifier.exists()":
                if not doc.get("identifier"):
                    # The prose states the identifier outright in some modules:
                    # `* Identifier:` / `- System: ...` / `Value: ...`.
                    if len(prose.identifiers) == 1:
                        stated = prose.identifiers[0]
                        doc["identifier"] = [dict(stated)]
                        changes.append(f"identifier += system {stated['system']}, "
                                       f"value {stated['value']}")
                        ctx.used.append("identifier stated by the precondition prose")
                        continue
                    systems = prose.identifier_systems
                    if not systems and prose.identifier_open \
                            and len(prose.module_identifier_systems) == 1:
                        # The feature says the identifier may be anything and names
                        # no system; its module names exactly one. Documented guess.
                        systems = prose.module_identifier_systems
                        ctx.used.append(
                            f"identifier system {systems[0]!r} from the other "
                            f"features of the same module, the resource's own "
                            f"prose saying only 'any'")
                    if len(systems) != 1:
                        unhandled.append(
                            f"identifier.exists() - the precondition prose names "
                            f"{len(systems)} identifier system(s), so the identifier "
                            f"cannot be written from the specification alone")
                        continue
                    # The prose says `any (system: ...)`: the system is stated, the
                    # value is left open, so it is the resource's own id.
                    doc["identifier"] = [{"system": systems[0], "value": row["id"]}]
                    changes.append(f"identifier += system {systems[0]}, "
                                   f"value {row['id']}")
                    ctx.used.append(f"identifier system {systems[0]!r} from the "
                                    f"precondition prose")
                continue
            try:
                changes += enforce(doc, expr, ctx)
            except Unsupported as exc:
                unhandled.append(f"{exc}" if str(exc) != expr else f"fhirpath {expr}")
            continue

        unhandled.append(f"{kind}: {json.dumps(a, sort_keys=True)[:120]}")

    # Displays last, so codings this run created are covered too.
    changes += fill_displays(doc, ctx)
    return changes, unhandled, ctx.used


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--requirements", required=True)
    ap.add_argument("--level", required=True)
    ap.add_argument("--contract", required=True)
    ap.add_argument("--package", action="append", required=True,
                    help="unpacked package directory supplying displays (repeatable)")
    ap.add_argument("--features",
                    help="root of the vendored feature tree, e.g. "
                         "vendor/upstream/features; without it the precondition "
                         "prose is not read and what only it states is reported")
    ap.add_argument("--prose-facts",
                    help="write every value taken from the prose, with the feature "
                         "file and line it was taken from, to this file")
    ap.add_argument("--dir", required=True, help="staged bodies to reconcile in place")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--quiet", action="store_true",
                    help="print the per-resource totals but not each change")
    args = ap.parse_args()

    reqs = json.loads(pathlib.Path(args.requirements).read_text(encoding="utf-8"))
    level = reqs.get("levels", {}).get(args.level)
    if level is None:
        print(f"ERROR: no level {args.level!r} in {args.requirements}", file=sys.stderr)
        return 2

    namespaces = {k.split(".", 1)[0] for row in level.get("resources", [])
                  for k in row.get("keys", [])}
    contract = load_contract(pathlib.Path(args.contract), namespaces)
    displays = build_display_index([pathlib.Path(p) for p in args.package])
    print(f"{len(displays)} code/system pair(s) carry an unambiguous display in the "
          f"{len(args.package)} package(s) read")

    features = pathlib.Path(args.features) if args.features else None
    staged = pathlib.Path(args.dir)
    citations: list[str] = []
    touched = left = 0
    for row in sorted(level.get("resources", []),
                      key=lambda r: (r["resourceType"], r["id"])):
        path = staged / f"{row['resourceType']}-{row['id']}.json"
        if not path.is_file():
            continue
        row_namespaces = {k.split(".", 1)[0] for k in row.get("keys", [])}
        if len(row_namespaces) != 1:
            print(f"  skip  {path.name}: keys in {len(row_namespaces)} namespaces")
            continue
        prose = (mine_prose(features, row.get("usedBy") or []) if features
                 else Prose())
        doc = json.loads(path.read_text(encoding="utf-8"))
        changes, unhandled, used = reconcile(doc, row, row_namespaces.pop(), contract,
                                            displays, prose)
        if used:
            # Only the prose facts actually relied on, and only the citations for
            # them, so the record carries no value this body does not use.
            wanted = {u.split("'")[1] for u in used if "'" in u}
            for citation in prose.citations:
                if any(w in citation for w in wanted):
                    citations.append(f"{path.name}  <- {citation}")
        if changes:
            touched += 1
            if args.quiet:
                print(f"  {path.name}: {len(changes)} change(s)")
            else:
                print(f"  {path.name}")
                for c in changes:
                    print(f"      {c}")
            if not args.dry_run:
                path.write_text(json.dumps(doc, indent=2, sort_keys=True,
                                           ensure_ascii=False) + "\n",
                                encoding="utf-8")
        if unhandled:
            left += len(unhandled)
            print(f"  {path.name}: {len(unhandled)} assertion(s) not acted on")
            for u in unhandled:
                print(f"      . {u}")

    if args.prose_facts and citations:
        out = pathlib.Path(args.prose_facts)
        out.write_text(
            "# Values taken from the suite's precondition prose\n\n"
            "Each line names the staged body, then the feature file and line the\n"
            "value was read from. Nothing here is composed by the tool; each entry\n"
            "is checkable against the suite text it cites.\n\n"
            + "\n".join(f"- {c}" for c in sorted(set(citations))) + "\n",
            encoding="utf-8")
        print(f"wrote {out} ({len(set(citations))} citation(s))")

    print(f"{args.level}: {touched} bodies changed, {left} assertion(s) left for a human")
    return 0


if __name__ == "__main__":
    sys.exit(main())
