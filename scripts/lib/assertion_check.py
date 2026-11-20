#!/usr/bin/env python3
"""Check authored test data against the assertions the suite makes about it.

Why this exists
---------------
Every fabricated resource has to satisfy assertions that live in the feature
files, and a resource that misses one produces a failing scenario that is
indistinguishable, in the report, from an IRIS conformance gap. That is the one
error this project must not make, so the authored JSON is checked against the
extracted assertions *before* a run, on the host, with no containers involved.

What it does and does not verify
--------------------------------
The suite evaluates real FHIRPath - 494 `exists()`, 455 `where()`, plus `~`,
`implies`, `ofType()`, `replaceMatches()` across 566 expressions. Implementing a
subset of that grammar here would produce a checker that is right about most
expressions and quietly wrong about the rest, which is worse than no checker: a
false pass is exactly the failure mode we are trying to prevent. So checks are
applied at the strength each assertion kind actually supports, and every
assertion is reported under the strength it received:

  EXACT     `TGR current response with attribute "$..gender.value" matches "male"`
            and `element "X" references resource with ID "Y"`. Both are literal:
            a path (or element) and a required value. Resolved against the JSON
            and compared. A mismatch is an error.

  ELEMENT   FHIRPath expressions, reduced to the root elements they name. An
            assertion of the form `address.where(...).exists()` unambiguously
            requires `address` to be present, whatever the predicate does; an
            absent root element fails the assertion for certain. Present root
            element, predicate unverified.

  LITERAL   FHIRPath expressions additionally yield their quoted string literals
            ('Berlin', 'http://fhir.de/sid/gkv/kvid-10'). Each must appear
            somewhere in the resource. This is deliberately a weak check - it
            says nothing about *where* the value sits - but it catches the
            common authoring miss of a wrong code system or a typo'd code.

  UNVERIFIED  expressions from which nothing checkable could be derived, and
            negative assertions (`.empty()`, `not(...)`) where a present element
            is not evidence either way. Counted and listed, never hidden: the
            count is the honest measure of how much of the specification this
            check does not cover, and IRIS `$validate` plus the run itself are
            what cover the rest.

Usage
-----
    scripts/lib/assertion_check.py --level Stufe3            # all resources
    scripts/lib/assertion_check.py --level Stufe3 --id Patient-Read-Example -v
    scripts/lib/assertion_check.py --level Stufe5 --json-report out.json
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
from pathlib import Path

# Invoked as a standalone script, so sys.path[0] is already this directory; the
# insert is for the case where a sibling imports this module instead -
# search_reference_check.py does.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from derive_testdata import staged_file_name  # noqa: E402

REPO = Path(__file__).resolve().parents[2]

# FHIRPath function names, operators and literals that are not FHIR elements.
# Anything matching this is stripped before a token is treated as an element name.
# Kept explicit rather than "any known FHIRPath keyword" because the list is short
# and a silent misclassification here turns into a bogus ELEMENT requirement.
_NOT_ELEMENTS = {
    "where", "exists", "not", "empty", "and", "or", "xor", "implies", "true",
    "false", "matches", "first", "last", "all", "any", "count", "toString",
    "toInteger", "toDecimal", "contains", "hasValue", "replaceMatches", "replace",
    "as", "is", "select", "startsWith", "endsWith", "allTrue", "anyTrue",
    "ofType", "single", "tail", "skip", "take", "distinct", "union", "combine",
    "iif", "trace", "length", "substring", "upper", "lower", "children",
    "descendants", "resolve", "extension", "in", "div", "mod", "now", "today",
}

# A dotted chain of lowerCamelCase identifiers, at a position where an element
# reference can start: line start, "(", ",", whitespace, or after "." following a
# closing paren (`where(...).given`).
RE_CHAIN = re.compile(r"(?:^|[(\s,]|\)\.)([a-z]\w*(?:\.[a-z]\w*)*)")
# Both quote styles are string literals here. FHIRPath reserves double quotes for
# delimited *identifiers*, but the suite uses them interchangeably with single
# quotes for values - `note.text = "Test note"` and `note.where(text = 'Testnotiz')`
# occur side by side, and no expression in 2.7.0 uses a double-quoted identifier
# (checked: zero occurrences of a quote in element position). Recognising only
# single quotes had two effects, both wrong in the same direction: the value was
# never checked, and its words leaked into the element derivation - "Illness
# resolved" produced a demand for an element named `resolved`.
RE_LITERAL = re.compile(r"'((?:[^'\\]|\\.)*)'|\"((?:[^\"\\]|\\.)*)\"")
# Every quoted run, so quoted text can be blanked before element names are read
# out of what remains.
RE_QUOTED = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")
# A literal that is the argument of a FHIRPath substring predicate. There the
# requirement is that the value *contains* the literal, not that it equals it:
# `effective.toString().contains('2021-07-01')` is satisfied by
# "2021-07-01T08:00:00+02:00", and the suite writes it that way precisely because
# it expects a full timestamp. Requiring the literal to appear as a whole string
# would have pushed the seed data towards date-only values to please the checker.
RE_SUBSTRING_ARG = re.compile(
    r"\.(?:contains|startsWith|endsWith|indexOf|matches)\s*\(\s*"
    r"(?:'((?:[^'\\]|\\.)*)'|\"((?:[^\"\\]|\\.)*)\")")
# `$..a.b.value` -> a.b ; the suite writes response attributes as JSONPath.
RE_ATTR_PATH = re.compile(r"^\$\.\.(.+?)\.value$")
# A leading resource type in a JSONPath (`$..Appointment.status.value`). Present
# in 47 of the 174 attribute assertions and must be dropped before resolving,
# since the JSON root has no such wrapper.
RE_TYPE_HEAD = re.compile(r"^[A-Z]\w*\.")
# Expressions where element presence proves nothing: the assertion is satisfied
# precisely when the element is absent or fails a predicate. `not()` is a FHIRPath
# function and is normally *invoked on* something, so it is nearly always preceded
# by a dot - `period.exists().not()`. An earlier lookbehind excluded `.not(` in an
# attempt to skip identifiers ending in "not", with the effect that the whole
# expression was read as positive and `period` was demanded to be present when the
# assertion says the opposite.
RE_NEGATIVE = re.compile(r"\.empty\(\s*\)|(?:^|[.\s(,])not\s*\(")
# The whole expression is "this element must be absent", with nothing else in it.
# Narrow on purpose: it is the one negative shape whose meaning is unambiguous
# without evaluating FHIRPath, and it is worth checking because the mistake it
# catches - authoring the element anyway - is one an author actually makes.
RE_ABSENT_ONLY = re.compile(
    r"^\s*([a-z]\w*(?:\.[a-z]\w*)*)\s*\.\s*(?:exists\(\s*\)\s*\.\s*not|empty)\(\s*\)\s*$")
# The operand of the FHIRPath type operators, which is a type name and not an
# element: in `onset is dateTime` the token `dateTime` is the *type* of the choice
# element, and FHIR primitive type names are lowerCamelCase - indistinguishable
# from an element name by shape alone. Without this, `onset is dateTime` demanded
# an element called `dateTime` and failed correct data. The `.is(Quantity)` /
# `(value as string)` function and parenthesised forms are already removed by
# strip_parens; this covers the infix spelling.
RE_TYPE_OPERAND = re.compile(r"(?:^|[\s(])(?:is|as)\s+[A-Za-z]\w*")


def strip_parens(expr: str) -> str:
    """Remove every parenthesised group, innermost first.

    Names inside a predicate are relative to the element being filtered, not to the
    resource root: in `identifier.where(system = '...' and value = '...')` the tokens
    `system` and `value` are children of `identifier`, and treating them as roots
    would demand a top-level `Practitioner.system`. Similarly `(value as string)`
    would contribute a root called `string`. Only what remains outside the
    parentheses is rooted at the resource.
    """
    prev = None
    while prev != expr:
        prev = expr
        expr = re.sub(r"\([^()]*\)", " ", expr)
    return expr


def _branch_roots(expr: str) -> set[str]:
    out: set[str] = set()
    cleaned = RE_TYPE_OPERAND.sub(" ", strip_parens(RE_QUOTED.sub(" ", expr)))
    for chain in RE_CHAIN.findall(cleaned):
        head = chain.split(".")[0]
        if head in _NOT_ELEMENTS:
            continue
        out.add(head)
    return out


def root_elements(expr: str) -> set[str]:
    """The FHIR element names an expression cannot be satisfied without.

    Only the *root* of each chain is returned. `name.where(use='official').given`
    yields `name`: that the resource must have a `name` is certain, whereas whether
    it must have a `given` under some *other* `name` entry depends on the
    predicate, and asserting the deeper path would invent a requirement.

    Across a top-level `or` only the *intersection* is certain, since satisfying
    either branch satisfies the whole: `performed.toString().contains('2026-01-05')
    or (performed.start... and performed.end...)` still requires `performed`,
    whereas `($this is Appointment) or ($this is Parameters)` requires nothing.
    """
    masked, lits = _mask(expr)
    branches = split_or(masked)
    if len(branches) == 1:
        return _branch_roots(expr)
    sets = [_branch_roots(RE_PLACEHOLDER.sub(lambda m: f"'{lits[int(m.group(1))]}'", b))
            for b in branches]
    return set.intersection(*sets) if sets else set()


def _mask(expr: str) -> tuple[str, list[str]]:
    """Replace every quoted run with a placeholder.

    Parentheses inside a string literal - `matches('^booked|pending$')` is harmless
    but `contains('(1)')` would not be - must not be counted when nesting depth is
    measured, and a literal must stay one atom while the expression is split.
    """
    lits: list[str] = []

    def sub(m: re.Match) -> str:
        lits.append(m.group(0)[1:-1])
        return f"\x00{len(lits) - 1}\x00"

    return RE_QUOTED.sub(sub, expr), lits


def _depths(s: str) -> list[int]:
    """Per-character nesting depth, with both parens themselves at the outer depth."""
    out: list[int] = []
    d = 0
    for ch in s:
        if ch == "(":
            out.append(d)
            d += 1
        elif ch == ")":
            d -= 1
            out.append(d)
        else:
            out.append(d)
    return out


RE_OR = re.compile(r"\bor\b")
RE_PLACEHOLDER = re.compile(r"\x00(\d+)\x00")


def split_or(masked: str) -> list[str]:
    """Split a masked expression on the `or` operators at its own nesting level."""
    dep = _depths(masked)
    parts: list[str] = []
    last = 0
    for m in RE_OR.finditer(masked):
        if dep[m.start()] == 0:
            parts.append(masked[last:m.start()])
            last = m.end()
    parts.append(masked[last:])
    return [p for p in (x.strip() for x in parts) if p]


def _keep_literal(lit: str) -> bool:
    """Regex fragments are excluded: `matches('1\\. Etage.+Hinterhaus|...')` is a
    pattern, not a value, and demanding it verbatim would fail correct data."""
    return bool(lit) and len(lit) >= 2 and not any(c in lit for c in "\\|+*?[]()^$")


# Above this many combinations a nested group stops contributing its literals at
# all. The cap has never been reached by the 2.7.0 corpus (the widest expression
# yields four), and dropping literals only ever makes the check weaker, never
# wrong - the direction this module always errs in.
_MAX_ALTERNATIVES = 32


def literal_alternatives(expr: str) -> list[dict[str, bool]]:
    """Ways to satisfy an expression's literals: one dict per alternative.

    A disjunction requires *one* of its branches, not all of them.
    `criticality.where($this = 'low' or $this = 'high' or $this = 'unable-to-access')`
    states the value set the element is drawn from; an instance carries exactly one
    of the three, and demanding all three present at once fails every conformant
    resource. 19 distinct expressions in 2.7.0 are disjunctive, including the whole
    of the AllergyIntolerance value-domain block and the `rateQuantity`-or-`rateRatio`
    choice in MedicationAdministration.

    Each dict maps literal -> whether a substring hit suffices (see
    RE_SUBSTRING_ARG). The assertion passes if any one dict is fully present.
    """
    substr = {m.group(1) or m.group(2) for m in RE_SUBSTRING_ARG.finditer(expr)}
    masked, lits = _mask(expr)

    def walk(s: str) -> list[dict[str, bool]]:
        branches = split_or(s)
        if len(branches) > 1:
            out: list[dict[str, bool]] = []
            for b in branches:
                out.extend(walk(b))
            return out
        s = branches[0] if branches else ""
        dep = _depths(s)

        # Literals at this level are required by this branch outright.
        base: dict[str, bool] = {}
        for m in RE_PLACEHOLDER.finditer(s):
            if dep[m.start()] != 0:
                continue
            lit = lits[int(m.group(1))]
            if _keep_literal(lit):
                base[lit] = lit in substr

        # Each parenthesised group at this level contributes its own alternatives -
        # a predicate's literals still have to appear somewhere in the resource,
        # which is what the LITERAL strength claims and all it claims.
        alts = [base]
        start = None
        for i, ch in enumerate(s):
            if ch == "(" and dep[i] == 0:
                start = i + 1
            elif ch == ")" and dep[i] == 0 and start is not None:
                group_alts = walk(s[start:i])
                start = None
                group_alts = [g for g in group_alts if g]
                if not group_alts:
                    continue
                if len(alts) * len(group_alts) > _MAX_ALTERNATIVES:
                    continue
                alts = [{**a, **g} for a in alts for g in group_alts]
        return alts

    return [a for a in walk(masked) if a] or [{}]


def resolve(doc, path: str) -> list:
    """Every value reachable by a dotted path, flattening lists as FHIRPath does.

    A path segment also matches a FHIR choice element: `performed` resolves against
    `performedDateTime`, because FHIRPath names a `[x]` element without its type
    suffix - `Procedure.performed` is the valid expression and
    `Procedure.performedDateTime` is not. Without this, a correct
    `"performedDateTime": "2021-02-13"` reads as a missing `performed`.
    """
    nodes = [doc]
    for part in path.split("."):
        nxt = []
        for n in nodes:
            n_items = n if isinstance(n, list) else [n]
            for item in n_items:
                if not isinstance(item, dict):
                    continue
                for key in _choice_keys(item, part):
                    v = item[key]
                    nxt.extend(v if isinstance(v, list) else [v])
        nodes = nxt
        if not nodes:
            return []
    return nodes


def _choice_keys(obj: dict, part: str) -> list[str]:
    """Every JSON key that spells the FHIR element `part`.

    Three spellings, all of which FHIRPath reaches by the same bare name:

    `part` itself; the `part<Type>` form of a choice element (see `resolve`); and
    `_part`, the representation of extensions on a *primitive* element. FHIR JSON
    has no place to hang a child object off a primitive, so `Appointment.priority`
    - an `unsignedInt` - carries its ISiK priority extension as:

        "_priority": {"extension": [{"url": ".../ISiKTerminPriorityExtension", ...}]}

    which is exactly what the suite's own `Appointment-Book` fixtures send. The
    assertion is written `priority.extension.where(url = ...)`, so without `_part`
    the requirement reads as an absent `priority` on data that satisfies it. Both
    spellings are returned rather than one preferred, because an element may carry
    a value *and* an extension - `Patient.family` does, in `"family"` and
    `"_family"` - and a check anchored on either alone would miss the other.
    """
    return [k for k in (part, f"_{part}") if k in obj] or [
        k for k in obj
        if k.startswith(part) and len(k) > len(part) and k[len(part)].isupper()]


def all_nodes(node):
    """Every dict in the tree, root first - the domain of a `$..` descent."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from all_nodes(v)
    elif isinstance(node, list):
        for v in node:
            yield from all_nodes(v)


def resolve_anywhere(doc, path: str) -> list:
    """`resolve` at every depth, i.e. JSONPath `$..` semantics.

    The suite writes `$..code.value`, and Tiger evaluates that as a recursive
    descent: for a Coverage the match is `type.coding[0].code`, not a top-level
    `Coverage.code`, which does not exist in the resource at all. Anchoring the
    path at the root would report correct data as a failure.
    """
    out = []
    for node in all_nodes(doc):
        out.extend(resolve(node, path))
    return out


def walk_strings(node) -> list[str]:
    """Every string in the resource, for the LITERAL check."""
    if isinstance(node, dict):
        return [s for v in node.values() for s in walk_strings(v)]
    if isinstance(node, list):
        return [s for v in node for s in walk_strings(v)]
    if isinstance(node, str):
        return [node]
    if isinstance(node, bool):
        return ["true" if node else "false"]
    if isinstance(node, (int, float)):
        return [str(node)]
    return []


def check_resource(doc: dict, assertions: list[dict]) -> list[dict]:
    """One result row per assertion: strength, verdict, and why."""
    strings = set(walk_strings(doc))
    results: list[dict] = []

    for a in assertions:
        cite = f"{a.get('feature', '?')}:{a.get('line', '?')}"

        if a["kind"] == "raw-body":
            # Binary-Read asserts on the undecoded payload. `Binary.data` is
            # base64, so the requirement is real but is not a path into the JSON
            # tree; it is satisfied by construction when the base64 is authored
            # from the right plaintext, and is reported rather than guessed at.
            results.append({"strength": "UNVERIFIED", "ok": None, "cite": cite,
                            "detail": f"raw payload must match {a['matches']!r} "
                                      "(base64-encoded in the resource)"})
            continue

        if a["kind"] == "attribute":
            path = RE_ATTR_PATH.match(a["path"])
            if not path:
                # Not the `$..x.y.value` shape - e.g. an index or a function. Only
                # 47 of 174 deviate and they are reported rather than guessed at.
                results.append({"strength": "UNVERIFIED", "ok": None, "cite": cite,
                                "detail": f"attribute path not resolvable here: {a['path']}"})
                continue
            p = RE_TYPE_HEAD.sub("", path.group(1))
            found = [str(v).lower() if isinstance(v, bool) else str(v)
                     for v in resolve_anywhere(doc, p)]
            want = a["matches"]
            ok = any(v == want for v in found)
            results.append({
                "strength": "EXACT", "ok": ok, "cite": cite,
                "detail": f"{p} = {want!r}" + ("" if ok else f"; found {found or 'nothing'}"),
            })
            continue

        if a["kind"] == "reference":
            element = a["element"]
            found = [v for v in resolve(doc, f"{element}.reference")]
            # The suite writes the target either bare or as Type/id, and a
            # reference may legitimately be stored either way.
            want = a["target"]
            tail = want.rsplit("/", 1)[-1]
            ok = any(r == want or r.rsplit("/", 1)[-1] == tail for r in found)
            results.append({
                "strength": "EXACT", "ok": ok, "cite": cite,
                "detail": f"{element} -> {want}" + ("" if ok else f"; found {found or 'nothing'}"),
            })
            continue

        expr = a["expr"]
        absent = RE_ABSENT_ONLY.match(expr)
        if absent:
            path = absent.group(1)
            found = resolve(doc, path)
            results.append({
                "strength": "EXACT", "ok": not found, "cite": cite,
                "detail": f"{path} must be absent"
                          + ("" if not found else f"; found {found}"),
                "expr": expr,
            })
            continue
        if RE_NEGATIVE.search(expr):
            results.append({"strength": "UNVERIFIED", "ok": None, "cite": cite,
                            "detail": f"negative assertion, presence proves nothing: {expr[:90]}"})
            continue

        roots = root_elements(expr)
        alternatives = literal_alternatives(expr)
        if not roots and not any(alternatives):
            results.append({"strength": "UNVERIFIED", "ok": None, "cite": cite,
                            "detail": f"nothing checkable derived: {expr[:90]}"})
            continue

        def unmatched_literals(alt: dict[str, bool]) -> list[str]:
            """The literals of one alternative the resource does not carry."""
            return sorted(
                l for l, is_substr in alt.items()
                if not (any(l in s for s in strings) if is_substr else l in strings))

        # One satisfied alternative satisfies the assertion. Where none is, the
        # closest miss is reported - the one an author is most likely to have meant.
        misses = sorted((unmatched_literals(alt) for alt in alternatives), key=len)
        missing_lits = misses[0] if misses else []
        lits = (min(alternatives, key=lambda a: len(unmatched_literals(a)))
                if alternatives else {})

        missing_roots = sorted(r for r in roots if not resolve(doc, r))
        if roots:
            results.append({
                "strength": "ELEMENT", "ok": not missing_roots, "cite": cite,
                "detail": f"requires element(s) {', '.join(sorted(roots))}"
                          + (f"; MISSING {', '.join(missing_roots)}" if missing_roots else ""),
                "expr": expr,
            })
        if lits:
            results.append({
                "strength": "LITERAL", "ok": not missing_lits, "cite": cite,
                "detail": f"requires value(s) {', '.join(repr(l) for l in sorted(lits))}"
                          + (f"; MISSING {', '.join(repr(l) for l in missing_lits)}" if missing_lits else ""),
                "expr": expr,
            })

    return results


def load_inventory(level: str, req_path: Path) -> list[dict]:
    """One row per resource the level's features assert something about."""
    data = json.loads(req_path.read_text(encoding="utf-8"))
    return data["levels"][level]["resources"]


def _rel(path: Path) -> str:
    """Repo-relative where possible; --data-root may point outside the repo."""
    try:
        return str(path.resolve().relative_to(REPO))
    except ValueError:
        return str(path)


@dataclasses.dataclass(frozen=True)
class StagedLevel:
    """The authored resources of one level, under one data root.

    Both are settled by the command line before the first inventory row is
    read, so a row is all there is left to say to get at its file.
    """

    root: Path
    level: str

    def path_of(self, row: dict) -> Path:
        """Where the inventory row's resource is authored."""
        return self.root / self.level / staged_file_name(row)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--level", choices=("Stufe3", "Stufe5"), required=True)
    ap.add_argument("--id", help="check only this resource id")
    ap.add_argument("--data-root", type=Path, default=REPO / "testdata" / "fhir")
    ap.add_argument("--requirements", type=Path,
                    default=REPO / "vendor" / "requirements.json")
    ap.add_argument("--json-report", type=Path)
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="list passing assertions too, not just failures")
    ap.add_argument("--allow-missing", action="store_true",
                    help="do not fail on resources that have no authored file yet")
    args = ap.parse_args()

    if not args.requirements.is_file():
        print(f"{args.requirements} not found - run: make packages", file=sys.stderr)
        return 2

    rows = load_inventory(args.level, args.requirements)
    if args.id:
        rows = [r for r in rows if r["id"] == args.id]
        if not rows:
            print(f"no resource with id {args.id!r} in {args.level}", file=sys.stderr)
            return 2

    being = StagedLevel(root=args.data_root, level=args.level)
    report = {"level": args.level, "resources": [], "totals": {}}
    tot = {"EXACT": [0, 0], "ELEMENT": [0, 0], "LITERAL": [0, 0], "UNVERIFIED": [0, 0]}
    missing_files: list[str] = []
    failed = 0

    for r in rows:
        path = being.path_of(r)
        if not path.is_file():
            missing_files.append(f"{r['resourceType']}/{r['id']}")
            report["resources"].append({
                "resourceType": r["resourceType"], "id": r["id"],
                "file": _rel(path), "present": False,
                "assertions": len(r["assertions"]),
            })
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        if doc.get("resourceType") != r["resourceType"] or doc.get("id") != r["id"]:
            print(f"FAIL {path}: declares {doc.get('resourceType')}/{doc.get('id')}, "
                  f"inventory expects {r['resourceType']}/{r['id']}", file=sys.stderr)
            failed += 1
            continue

        results = check_resource(doc, r["assertions"])
        bad = [x for x in results if x["ok"] is False]
        for x in results:
            tot[x["strength"]][1] += 1
            if x["ok"] is not False:
                tot[x["strength"]][0] += 1
        if bad:
            failed += 1

        report["resources"].append({
            "resourceType": r["resourceType"], "id": r["id"],
            "file": _rel(path), "present": True,
            "profiles": r["profiles"], "results": results,
        })

        if bad or args.verbose:
            head = f"{r['resourceType']}/{r['id']}"
            print(f"\n{head}\n{'-' * len(head)}")
            for x in results if args.verbose else bad:
                mark = {True: "ok  ", False: "FAIL", None: "n/a "}[x["ok"]]
                print(f"  {mark} [{x['strength']:<10}] {x['detail']}")
                print(f"       {x['cite']}")

    report["totals"] = {k: {"passed": v[0], "checked": v[1]} for k, v in tot.items()}
    report["missingFiles"] = missing_files

    print("\n" + "=" * 70)
    print(f"{args.level}: {len(rows)} resources in scope of this check")
    for k in ("EXACT", "ELEMENT", "LITERAL", "UNVERIFIED"):
        p, c = tot[k]
        if c:
            print(f"  {k:<10} {p}/{c} satisfied"
                  + ("   (not a verification - see module docstring)"
                     if k == "UNVERIFIED" else ""))
    if missing_files:
        print(f"  no authored file yet: {len(missing_files)}")
        for m in missing_files:
            print(f"      {m}")
    print("=" * 70)

    if args.json_report:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                                    encoding="utf-8")

    if missing_files and not args.allow_missing:
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
