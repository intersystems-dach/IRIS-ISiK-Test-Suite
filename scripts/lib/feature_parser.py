#!/usr/bin/env python3
"""A small Gherkin reader for the ISiK test suite's own feature files.

Why not the real Gherkin parser
-------------------------------
``pip install gherkin-official`` would be the obvious answer, and it is the wrong
one here. This repository is deployed to a remote host whose only guaranteed
Python is the system interpreter, and every other tool in ``scripts/`` sticks to
the standard library for exactly that reason. Adding a pip dependency would mean
either an internet round trip inside the isolated subnet or a vendored wheel whose
provenance we would then have to document. The subset of Gherkin these 220 files
use is small and entirely regular, so reading it directly is cheaper than either.

What is deliberately NOT supported: ``Rule:``, ``Background:``, ``@`` tags on
``Examples`` header rows, and internationalised keywords. The first two do not
occur in the suite (asserted by :func:`parse_feature`, which raises on an
unhandled keyword rather than skipping it - a silently ignored ``Background``
would mean silently miscounting scenarios). The third does not occur either,
which is worth stating because it is surprising: the 96 Stufe 3 files are written
in German but use the *English* keywords, and only the 124 Stufe 5 files carry a
``# language: en`` header. So a single keyword set covers both, and the language
header is recorded rather than acted upon.

Tag scope is the part worth being careful about
-----------------------------------------------
Cucumber inherits tags downwards: a tag before ``Feature:`` applies to every
scenario in the file, and a tag before an ``Examples:`` block applies to the rows
of that block only. The second rule is a genuine trap in this suite -
``Stufe5/Medikation/RiskAssessment-Search.feature`` has an ``@Optional`` tag that
reads, to the eye, as if it belonged to the scenario below it, when it actually
belongs to the ``Examples`` block above. Getting that wrong changes whether a
scenario is in the certification scope. :func:`effective_tags` and
:func:`count_asserting_cases` therefore model the inheritance explicitly instead
of grepping for tags per file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# The keyword set actually used by the suite. `Example:` is Gherkin 6's synonym
# for `Examples:` and is accepted because it costs one word to accept and would
# otherwise be read as a scenario name.
_KEYWORDS = ("Feature:", "Scenario Outline:", "Scenario:", "Examples:", "Example:")

# Keywords we know about but refuse to guess at. Hitting one means the suite grew
# a construct this reader does not model, and the honest response is to stop:
# every consumer of this module counts scenarios or attributes requirements, and
# both are wrong if a block silently vanishes.
_UNSUPPORTED = ("Background:", "Rule:", "Scenario Template:")

_STEP_KEYWORDS = ("Given ", "When ", "Then ", "And ", "But ", "* ")


# The escapes Gherkin defines inside a table cell. `\|` is the interesting one:
# it is how a cell carries the pipe that otherwise ends it, and the suite writes
# it in all ten of its token criteria of the form `system|code` - e.g.
# `| ${data.tag-system}\|${data.tag-value} |`. Splitting on every pipe turns that
# one cell into two and leaves the backslash on the first, which reads as a tag
# system with a trailing `\` and a code that has wandered into the next column.
# `\d` appears too (`| 4\d\d |`, a status-code regex) and is not an escape, so an
# unknown escape is kept verbatim rather than having its backslash eaten.
_CELL_ESCAPES = {"|": "|", "n": "\n", "\\": "\\"}


def _split_row(line: str) -> list[str]:
    """The cells of one Gherkin table row, honouring the cell escapes."""
    cells: list[str] = []
    buf: list[str] = []
    line = line.strip()
    i = 1 if line.startswith("|") else 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line):
            nxt = line[i + 1]
            buf.append(_CELL_ESCAPES.get(nxt, "\\" + nxt))
            i += 2
            continue
        if ch == "|":
            cells.append("".join(buf).strip())
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        # A row whose final cell is not closed by a delimiter. The suite always
        # closes its rows; the cell is kept rather than dropped so that a
        # malformed row shows up as its contents and not as a column short.
        cells.append(tail)
    return cells


@dataclass
class Step:
    keyword: str
    text: str
    line: int
    docstring: str | None = None
    docstring_line: int = 0
    table: list[list[str]] = field(default_factory=list)

    @property
    def full(self) -> str:
        return f"{self.keyword}{self.text}"


@dataclass
class Examples:
    tags: list[str]
    name: str
    line: int
    header: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)


@dataclass
class Scenario:
    tags: list[str]
    keyword: str          # "Scenario" or "Scenario Outline"
    name: str
    line: int
    steps: list[Step] = field(default_factory=list)
    examples: list[Examples] = field(default_factory=list)

    @property
    def is_outline(self) -> bool:
        return self.keyword == "Scenario Outline"

    @property
    def is_precondition(self) -> bool:
        # Two spellings for one thing: Stufe 3 says @vorbedingung, Stufe 5 says
        # @Precondition. Both mark a block that documents required test data
        # rather than exercising the SUT - its steps only record prose.
        #
        # A trailing ":" is tolerated because four Stufe 5 features spell the tag
        # "@Precondition:" (Basis/{Organization-Read,Location-Read,
        # Location-Room-Read,Location-Bed-Placement-Read}). To Cucumber that is a
        # *different* tag - a tag name is "@" plus everything up to whitespace -
        # but it changes nothing at run time, because neither profile's tag
        # expression mentions @Precondition and the blocks execute either way.
        # It matters only here: read literally, those four blocks would be
        # counted as asserting test cases and their prose - which is the field
        # spec for organization-read-id and the three Location keys - would be
        # left out of the requirement tables. Normalising is the choice that
        # keeps the extraction complete; the four spellings are reported as an
        # upstream defect in docs/en/05-testdata.md rather than silently healed.
        lowered = {t.lower().rstrip(":") for t in self.tags}
        return "@vorbedingung" in lowered or "@precondition" in lowered


@dataclass
class Feature:
    path: Path
    rel: str              # path relative to the features root, used in citations
    tags: list[str]
    name: str
    line: int
    language: str | None
    description: str
    scenarios: list[Scenario] = field(default_factory=list)
    text: str = ""

    # --- Derived identity ----------------------------------------------------
    # `rel` is "Stufe5/Medikation/Medication-Read.feature". The level and module
    # come from the directory layout rather than from tags, because the Maven
    # profile selects features by folder (`features.folder` in pom.xml) and the
    # folder is therefore the authoritative grouping.
    @property
    def level(self) -> str:
        return self.rel.split("/", 1)[0]

    @property
    def module(self) -> str:
        parts = self.rel.split("/")
        return parts[1] if len(parts) > 2 else "(root)"

    @property
    def precondition_scenarios(self) -> list[Scenario]:
        return [s for s in self.scenarios if s.is_precondition]

    @property
    def test_scenarios(self) -> list[Scenario]:
        return [s for s in self.scenarios if not s.is_precondition]


class FeatureParseError(RuntimeError):
    pass


def _tags_on_line(stripped: str) -> list[str] | None:
    """The tags on a tag line, or None if this is not a tag line.

    A leading "#" makes the whole line a comment and is handled by the caller
    before this is reached - which matters, because
    Stufe5/Medikation/MedicationAdministration-Read-Request.feature line 3 is
    "#@Medikation": a commented-out tag that must NOT be treated as applying, and
    that is precisely why that feature reports no module.

    A *trailing* comment is a different case and does occur once:
    Stufe5/Dokumentenaustausch/DocumentReference-UpdateMetadata.feature line 3 is
    "@Disabled # This Testcase is still under development ...". Gherkin accepts
    that, so the tag applies and the remainder is dropped. Refusing to parse it
    would mean refusing the only @Disabled feature in the suite.
    """
    if not stripped.startswith("@"):
        return None
    head = stripped.split("#", 1)[0].strip()
    tokens = head.split()
    if not tokens or not all(tok.startswith("@") for tok in tokens):
        return None
    return tokens


def parse_feature(path: Path, root: Path) -> Feature:
    """Parse one .feature file. Raises FeatureParseError on anything unmodelled."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    feature: Feature | None = None
    language: str | None = None
    pending_tags: list[str] = []
    current: Scenario | None = None
    current_examples: Examples | None = None
    current_step: Step | None = None
    description: list[str] = []

    i = 0
    while i < len(lines):
        raw = lines[i]
        i += 1
        lineno = i
        stripped = raw.strip()

        # --- Doc strings ----------------------------------------------------
        # Consumed here in full rather than by a state flag, because everything
        # inside one is opaque: a `"""` block in a Precondition scenario contains
        # lines beginning with "* Status: active", and "* " is a step keyword.
        if stripped == '"""' or stripped == "'''":
            fence = stripped
            indent = len(raw) - len(raw.lstrip())
            body: list[str] = []
            start = lineno
            while i < len(lines):
                inner = lines[i]
                i += 1
                if inner.strip() == fence:
                    break
                # Gherkin strips the opening delimiter's indentation from every
                # line, and nothing more. Preserving the rest matters: the
                # Precondition prose is quoted verbatim into the requirement
                # tables and the gap report, where its bullet structure is the
                # readable part.
                body.append(inner[indent:] if inner[:indent].strip() == "" else inner.lstrip())
            else:
                raise FeatureParseError(f"{path}:{start}: unterminated doc string")
            if current_step is not None:
                current_step.docstring = "\n".join(body)
                current_step.docstring_line = start
            continue

        if not stripped:
            continue

        if stripped.startswith("#"):
            m = re.match(r"#\s*language:\s*([A-Za-z-]+)", stripped)
            if m and feature is None:
                language = m.group(1)
            continue

        tags_here = _tags_on_line(stripped)
        if tags_here is not None:
            pending_tags.extend(tags_here)
            continue

        for bad in _UNSUPPORTED:
            if stripped.startswith(bad):
                raise FeatureParseError(
                    f"{path}:{lineno}: '{bad}' is not modelled by feature_parser. "
                    "Scenario counts and requirement attribution would be wrong; "
                    "teach the parser rather than ignoring it."
                )

        matched = next((k for k in _KEYWORDS if stripped.startswith(k)), None)

        if matched == "Feature:":
            if feature is not None:
                raise FeatureParseError(f"{path}:{lineno}: second Feature: in one file")
            feature = Feature(
                path=path,
                rel=path.relative_to(root).as_posix(),
                tags=pending_tags,
                name=stripped[len("Feature:"):].strip(),
                line=lineno,
                language=language,
                description="",
                text=text,
            )
            pending_tags = []
            continue

        if feature is None:
            raise FeatureParseError(f"{path}:{lineno}: content before Feature:")

        if matched in ("Scenario:", "Scenario Outline:"):
            keyword = matched[:-1]
            current = Scenario(
                tags=pending_tags,
                keyword=keyword,
                name=stripped[len(matched):].strip(),
                line=lineno,
            )
            feature.scenarios.append(current)
            pending_tags = []
            current_examples = None
            current_step = None
            continue

        if matched in ("Examples:", "Example:"):
            if current is None:
                raise FeatureParseError(f"{path}:{lineno}: Examples: outside a scenario")
            current_examples = Examples(
                tags=pending_tags,
                name=stripped[len(matched):].strip(),
                line=lineno,
            )
            current.examples.append(current_examples)
            pending_tags = []
            current_step = None
            continue

        if stripped.startswith("|"):
            cells = _split_row(stripped)
            if current_examples is not None:
                if not current_examples.header:
                    current_examples.header = cells
                else:
                    current_examples.rows.append(cells)
            elif current_step is not None:
                current_step.table.append(cells)
            else:
                raise FeatureParseError(f"{path}:{lineno}: table row with no owner")
            continue

        step_kw = next((k for k in _STEP_KEYWORDS if stripped.startswith(k)), None)
        if step_kw is not None:
            if current is None:
                raise FeatureParseError(f"{path}:{lineno}: step outside a scenario")
            current_step = Step(
                keyword=step_kw,
                text=stripped[len(step_kw):].strip(),
                line=lineno,
            )
            current.steps.append(current_step)
            continue

        # Free text directly under Feature: is the feature description. Anywhere
        # else it is a line this parser does not understand, which is a bug rather
        # than something to skip.
        if current is None:
            description.append(stripped)
            continue

        raise FeatureParseError(f"{path}:{lineno}: unrecognised line: {stripped[:80]!r}")

    if feature is None:
        raise FeatureParseError(f"{path}: no Feature: keyword")
    if pending_tags:
        raise FeatureParseError(
            f"{path}: trailing tags {' '.join(pending_tags)} with nothing to apply to"
        )

    feature.description = "\n".join(description)
    return feature


def load_features(root: Path) -> list[Feature]:
    """Parse every .feature file under root, sorted by path for stable output."""
    root = Path(root)
    if not root.is_dir():
        raise FeatureParseError(f"features root does not exist: {root}")
    features = [parse_feature(p, root) for p in sorted(root.rglob("*.feature"))]
    if not features:
        raise FeatureParseError(f"no .feature files under {root}")
    return features


# -----------------------------------------------------------------------------
# Tag expressions
# -----------------------------------------------------------------------------
# TESTS_TO_RUN is operator-configurable (.env), so the expression is evaluated
# properly rather than pattern-matched against the two defaults. The grammar
# Cucumber accepts is: tag | not E | E and E | E or E | ( E ).


def _tokenise(expr: str) -> list[str]:
    return re.findall(r"\(|\)|@[\w:-]+|and|or|not", expr)


def parse_tag_expression(expr: str):
    """Compile a Cucumber tag expression to a predicate over a set of tags."""
    tokens = _tokenise(expr)
    pos = 0

    def peek() -> str | None:
        return tokens[pos] if pos < len(tokens) else None

    def take() -> str:
        nonlocal pos
        tok = tokens[pos]
        pos += 1
        return tok

    def primary():
        tok = peek()
        if tok is None:
            raise FeatureParseError(f"tag expression ended early: {expr!r}")
        if tok == "(":
            take()
            node = or_expr()
            if peek() != ")":
                raise FeatureParseError(f"unbalanced parentheses in {expr!r}")
            take()
            return node
        if tok == "not":
            take()
            inner = primary()
            return lambda tags: not inner(tags)
        if tok.startswith("@"):
            take()
            # Case-insensitive on purpose: the suite writes @vorbedingung and
            # @Precondition for the same concept, and an operator typing
            # "@stufe5" should not silently select nothing.
            want = tok.lower()
            return lambda tags: want in {t.lower() for t in tags}
        raise FeatureParseError(f"unexpected token {tok!r} in tag expression {expr!r}")

    def and_expr():
        node = primary()
        while peek() == "and":
            take()
            rhs = primary()
            prev = node
            node = lambda tags, a=prev, b=rhs: a(tags) and b(tags)
        return node

    def or_expr():
        node = and_expr()
        while peek() == "or":
            take()
            rhs = and_expr()
            prev = node
            node = lambda tags, a=prev, b=rhs: a(tags) or b(tags)
        return node

    root = or_expr()
    if pos != len(tokens):
        raise FeatureParseError(f"trailing tokens in tag expression {expr!r}")
    return root


def effective_tags(feature: Feature, scenario: Scenario,
                   examples: Examples | None = None) -> list[str]:
    """Tags as Cucumber sees them for one scenario, or one Examples row set."""
    tags = list(feature.tags) + list(scenario.tags)
    if examples is not None:
        tags += list(examples.tags)
    return tags


def _selected_per_scenario(feature: Feature, predicate):
    """Each scenario of one feature, with the number of cases `predicate` selects.

    One plain Scenario is one case; one Scenario Outline is one case per Examples
    *row* - which is how Cucumber and therefore the Serenity report count, and
    the reason the scope figures in the documentation are larger than the
    scenario-keyword count.

    Yields the scenario alongside its count so that the two counts below differ
    only in which scenarios they admit, and not in how a case is counted.
    """
    for scenario in feature.scenarios:
        if scenario.is_outline:
            selected = sum(len(ex.rows) for ex in scenario.examples
                           if predicate(effective_tags(feature, scenario, ex)))
        else:
            selected = 1 if predicate(effective_tags(feature, scenario)) else 0
        yield scenario, selected


def count_asserting_cases(feature: Feature, predicate) -> int:
    """Cases the tag predicate selects that state something about the SUT.

    This is the conformance denominator. Precondition blocks are left out: they
    are real Cucumber scenarios and they do execute, but their steps only record
    prose for the report, so counting them would inflate the conformance scope by
    one per feature file.
    """
    return sum(n for scenario, n in _selected_per_scenario(feature, predicate)
               if not scenario.is_precondition)


def count_executed_cases(feature: Feature, predicate) -> int:
    """Cases the tag predicate selects that the suite will run, preconditions included.

    This is the figure to compare against a report's own case count, which makes
    no distinction between a precondition block and an asserting scenario.
    """
    return sum(n for _, n in _selected_per_scenario(feature, predicate))


# -----------------------------------------------------------------------------
# Cross-cutting extraction helpers, shared by the requirement extractor and the
# gap-analysis index so that both attribute a finding to the same key and the
# same profile.
# -----------------------------------------------------------------------------

# A test datum is identified by its QUALIFIED name, `<namespace>.<key>`, in every
# artefact this rig produces. For suite 2.x that is always `data.<key>`, the one
# namespace it has, so the qualified form is what its feature files already
# spell. For 3.x the namespace is the module's, and qualification is not
# cosmetic: three key names are defined with different values in different
# modules, so a bare key does not identify a resource. See suite_namespaces() in
# scripts/lib.sh.
#
# The namespace is matched by shape rather than against a fixed list, so that a
# namespace this rig has not been told about still yields a key rather than
# unrecognised text that seeds nothing. Whether a namespace is one the test
# environment actually loads is checked where that is knowable - against the
# baseKeys of the Tiger configuration, in extract_requirements.analyse.
#
# Safe because it is measured, not assumed: every one of the 2107 `${...}`
# occurrences in the 2.7.0 corpus and the 2323 in 3.0.9 is a test-data
# reference. The suite has no other `${}` interpolation.
#
# One capture group, deliberately: the pattern is embedded in the resource-type
# inference rules of extract_requirements.py, which index their own groups
# around it.
DATA_KEY_PATTERN = r"\$\{([A-Za-z0-9_]+\.[A-Za-z0-9_.-]+)\}"

RE_DATA_KEY = re.compile(DATA_KEY_PATTERN)
RE_STORE_VAR = re.compile(r'in variable "([A-Za-z0-9_]+\.[A-Za-z0-9_.-]+)"')

# `is a valid <module> resource and conforms to profile "<canonical>"` and its
# variant without the article. <module> is the refv-plugin id, which is what makes
# scenario -> FHIR package an automated lookup.
RE_PROFILE_FULL = re.compile(
    r"is (?:a )?valid ([A-Za-z0-9-]+) resource and conforms to profile "
    r"[\"']([^\"']+)[\"']"
)
# Two short forms that name a profile by its resource-level name instead of its
# canonical: `referenced Patient resource with id "X" conforms to ISiKPatient
# profile` and `... conforms to a valid v5 "ISiKPatient" profile`. Both are
# resolved to a canonical by the caller, which knows the package set; here they
# are only recognised.
RE_PROFILE_SHORT = re.compile(r"conforms to (?:a valid v5 )?[\"']?(ISiK[A-Za-z0-9]+)[\"']? profile")

RE_ENDPOINT_PATH = re.compile(r"http://fhirserver/([A-Za-z]+)")


def data_keys_in(text: str) -> set[str]:
    """The qualified test-data keys `text` references."""
    return set(RE_DATA_KEY.findall(text))


def runtime_keys_in(text: str) -> set[str]:
    """Keys the suite writes itself at run time; these need no seeded value."""
    return set(RE_STORE_VAR.findall(text))
