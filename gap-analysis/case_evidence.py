#!/usr/bin/env python3
"""Generate the per-case evidence annex of the developer gap analysis.

Audience: an InterSystems IRIS for Health product developer, the same reader
`GAP-ANALYSIS.md` is written for. That document is a work list: one entry per
distinct defect, with the affected scenarios listed but not shown. This document
is its evidence annex and answers, for every failing test case separately, the
four questions a developer asks when they sit down to fix one:

  1. **What input data was used** - the Scenario Outline row, the fixture the step
     read, the request as it reached the SUT, and the request body verbatim.
  2. **What output was expected** - the assertion that failed, word for word, and
     the expectation in FHIR terms as `GAP-ANALYSIS.md` states it.
  3. **What output was received** - the SUT's own response: status line, headers,
     body, plus the FHIRPath evaluation and the assertion error the suite
     recorded.
  4. **How to reproduce it** - the request replayed against the round's own
     endpoint, and the exact commands that recover the full exchange from the
     archived bundle.

Where the material comes from
-----------------------------
Nothing here is authored and nothing is inferred. Every entry is a join over
three files of the round's own bundle:

  * `analysis/results.json`   - which cases failed, and where each one's outcome
                                record lies inside the official archive;
  * `gap-analysis.json`       - what each failure was attributed to, so an entry
                                carries the same gap id, category and expectation
                                as `GAP-ANALYSIS.md` rather than a second opinion;
  * `<stufe>/official/test-report.zip` and `<stufe>/official/debug-report.zip` -
                                the suite's own record of the exchange.

The archives are read directly, not through an extraction, so this document can be
generated from a slim bundle on a machine that has never run the suite. Where an
`official/extracted/` tree is present it is used in preference, because it is the
tree the citations in `analysis/results.md` name.

Which exchange belongs to a case
--------------------------------
A Scenario Outline writes one outcome record for all its examples, so the record
is first narrowed to the example's own step subtree by the label the suite gave it
(`1: <scenario> ({...})`). Inside that subtree the suite attaches an RBelLog - its
traffic dump - to the step that was executing when the log was written. The log is
scoped to the example, and the exchange the assertion was made about is therefore
the last response in it, together with the request that preceded it. Four classes
of case have no exchange at all, because their assertion is made about a response
obtained in an *earlier scenario* of the same feature (Tiger's *current response*
is feature-scoped state); they are reported in section 5 rather than silently
dropped.

Response bodies are decoded, not re-encoded
-------------------------------------------
The suite renders a message as hex when it did not parse it, which is what happens
to every gzip-encoded response. Such a block is unhexed, split at the header
boundary and the body inflated, so what the entry shows is what the SUT sent. The
headers are left exactly as they arrived, including their original casing.

The one thing that is withheld
------------------------------
The `Authorization: Basic ...` header. This laboratory's credentials are generated
per deployment and are documented on purpose in `provenance/CREDENTIALS.md` and in
section 1 of `analysis/results.md`, so withholding them protects nothing - but this
document is the one that gets forwarded to a product organisation, and a document
that travels should not carry a credential in a copy-pasteable form. The
reproduction commands name the two environment variables instead. Nothing else is
filtered: the request line, every other header and both bodies are verbatim.

Diffing two rounds
------------------
Entries are keyed and ordered by level, module, feature file, feature line and
example index - never by a sequence number - so an entry that is unchanged between
two rounds is byte-identical between the two documents apart from the per-run
values a response carries anyway (`Date`, `Content-Length`, generated resource
ids). A `diff` of two rounds' `CASE-EVIDENCE.md` therefore shows the cases that
changed and nothing else, which is what makes it usable as a record of what a
release did and did not move.
"""

from __future__ import annotations

import argparse
import binascii
import gzip
import hashlib
import html
import json
import re
import sys
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "gap-analysis"))

# Imported rather than restated. The category titles and the list of categories
# that are not the product's are properties of the gap analysis, and a second copy
# of either is how two documents of one round come to disagree - which has happened
# in this project once already (see NOT_SUT_CATEGORIES in gap_report.py).
from gap_report import (CAT_TITLE, NOT_SUT_CATEGORIES,          # noqa: E402
                        UNRESOLVED_TOKEN)

# Characters of a body quoted in full. Above this the quote is cut, the number of
# omitted characters and the sha256 of the complete body are stated, and the entry
# names the command that recovers it. Response bodies in this corpus run from a few
# hundred bytes to 1.6 MB (a CapabilityStatement), so an uncapped document would be
# some 20 MB of which 95 % is one resource repeated.
MAX_BODY = 20000

GENERATOR = "gap-analysis/case_evidence.py"

# What the entries say instead of the credential. Not a placeholder the reader has
# to decode: the two names are the variables the reproduction commands use.
REDACTED = "<redacted: see provenance/CREDENTIALS.md>"


def die(msg: str) -> None:
    print(f"case_evidence.py: {msg}", file=sys.stderr)
    raise SystemExit(2)


def load_json(path: Path, what: str, required: bool = True):
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        if required:
            die(f"{what} not found at {path}")
        return None
    except json.JSONDecodeError as exc:
        die(f"{what} at {path} is not valid JSON: {exc}")


# =============================================================================
# Reading a bundle, extracted or archived
# =============================================================================

class Bundle:
    """The round's official archives, addressed by the paths results.json cites.

    results.json cites a case as
    `<stufe>/official/extracted/test-report/unresolvedReports/<sha>.json`, which
    exists only where `make report` extracted the archive. A slim bundle - the
    form that travels, and the form in this project's `archives/` - carries only
    `test-report.zip` and `debug-report.zip`. Both are served here from the same
    citation, so an entry reads identically either way and the citation printed in
    the document stays the one the other documents of the round use.
    """

    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self._zips: dict[tuple[str, str], zipfile.ZipFile] = {}

    def _zip(self, level_dir: str, which: str) -> zipfile.ZipFile | None:
        key = (level_dir, which)
        if key not in self._zips:
            path = self.run_dir / level_dir / "official" / f"{which}.zip"
            try:
                self._zips[key] = zipfile.ZipFile(path)
            except (FileNotFoundError, zipfile.BadZipFile):
                return None
        return self._zips[key]

    def outcome(self, cited: str) -> dict | None:
        """The Serenity outcome record of one case, by the path results.json cites."""
        if not cited:
            return None
        direct = self.run_dir / cited
        if direct.is_file():
            return load_json(direct, "outcome record", required=False)
        level_dir = cited.split("/", 1)[0]
        z = self._zip(level_dir, "test-report")
        if z is None:
            return None
        member = "unresolvedReports/" + cited.rsplit("/", 1)[-1]
        try:
            return json.loads(z.read(member))
        except (KeyError, json.JSONDecodeError):
            return None

    def traffic(self, level_dir: str, rel: str) -> tuple[str, str] | None:
        """One RBelLog, as (html, the path to cite for it).

        `rel` is what the outcome record records: a path relative to the Serenity
        directory, e.g. `downloadable/downloadable-....html`.
        """
        cited = (f"{level_dir}/official/extracted/debug-report/serenity/{rel}")
        direct = self.run_dir / cited
        if direct.is_file():
            return direct.read_text(encoding="utf-8", errors="replace"), cited
        z = self._zip(level_dir, "debug-report")
        if z is None:
            return None
        try:
            raw = z.read("serenity/" + rel)
        except KeyError:
            return None
        return raw.decode("utf-8", "replace"), cited


# =============================================================================
# The traffic dump
# =============================================================================

_CARD = re.compile(r'<header class="card-header test-card-header">(.*?)</header>',
                   re.S)
_SEQ = re.compile(r'msg-sequence[^>]*>(\d+)<')
_KIND = re.compile(r'title="(Request|Response)"')
_TITLE = re.compile(r'<span class="font-monospace title[^"]*"\s*>(.*?)</span>', re.S)
_PRE = re.compile(r'<pre id="text-dialog[^"]*"[^>]*>(.*?)</pre>', re.S)
_HEX = re.compile(r'[0-9a-fA-F\s]+\Z')


def _raw_bytes(text: str) -> tuple[bytes, bool]:
    """The message as bytes, unhexing the form the suite uses for binary content.

    Tiger renders a message's raw content as hex whenever its parsers did not
    claim it, which in practice means every gzip-encoded response. The test is
    deliberately narrow - an even number of hex digits and nothing else - because
    a short all-hex *text* body would otherwise be mangled; 32 digits is above
    anything an HTTP message can be.
    """
    compact = re.sub(r"\s+", "", text)
    if len(compact) >= 32 and len(compact) % 2 == 0 and _HEX.fullmatch(text):
        try:
            return binascii.unhexlify(compact), True
        except binascii.Error:
            pass
    return text.encode("utf-8", "replace"), False


def _split(buf: bytes) -> tuple[str, bytes]:
    for sep in (b"\r\n\r\n", b"\n\n"):
        i = buf.find(sep)
        if i >= 0:
            head = buf[:i].decode("utf-8", "replace").replace("\r\n", "\n")
            return head, buf[i + len(sep):]
    return buf.decode("utf-8", "replace").replace("\r\n", "\n"), b""


def _inflate(head: str, body: bytes) -> tuple[str, str]:
    """The body as text, and the transfer encoding it arrived under."""
    enc = ""
    for line in head.split("\n")[1:]:
        if line.lower().startswith("content-encoding:"):
            enc = line.split(":", 1)[1].strip().lower()
    if "gzip" in enc:
        for attempt in (lambda b: gzip.decompress(b),
                        lambda b: zlib.decompress(b, 16 + zlib.MAX_WBITS)):
            try:
                return attempt(body).decode("utf-8", "replace").replace("\r\n", "\n"), "gzip"
            except Exception:                                   # noqa: BLE001
                continue
        return "", "gzip, not decodable"
    if "deflate" in enc:
        try:
            return zlib.decompress(body).decode("utf-8", "replace"), "deflate"
        except Exception:                                       # noqa: BLE001
            return "", "deflate, not decodable"
    return body.decode("utf-8", "replace").replace("\r\n", "\n"), ""


def messages(doc: str) -> list[dict]:
    """Every HTTP message in one RBelLog, in the order the suite recorded them."""
    out: list[dict] = []
    for card in _CARD.finditer(doc):
        head_html = card.group(1)
        kind = _KIND.search(head_html)
        pre = _PRE.search(head_html)
        if not kind or not pre:
            continue
        seq = _SEQ.search(head_html)
        title = _TITLE.search(head_html)
        buf, was_hex = _raw_bytes(html.unescape(pre.group(1)))
        head, body = _split(buf)
        text, enc = _inflate(head, body)
        out.append({
            "seq": int(seq.group(1)) if seq else None,
            "kind": kind.group(1).lower(),
            "title": html.unescape(title.group(1)).strip() if title else "",
            "head": head.strip("\n"),
            "body": text.strip("\n"),
            "bodyBytes": len(body),
            "transferEncoding": enc,
            "renderedAsHex": was_hex,
        })
    return out


def last_exchange(msgs: list[dict]) -> tuple[dict | None, dict | None]:
    """The exchange the failing assertion was made about.

    The last response in the log, with the nearest request before it. Taking the
    first pair instead would quote a request that passed; the log is written at the
    point of the step it is attached to, so its last response is the one the step's
    *current response* refers to.
    """
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i]["kind"] == "response":
            req = next((msgs[j] for j in range(i - 1, -1, -1)
                        if msgs[j]["kind"] == "request"), None)
            return req, msgs[i]
    reqs = [m for m in msgs if m["kind"] == "request"]
    return (reqs[-1] if reqs else None), None


# =============================================================================
# Reading one case out of its outcome record
# =============================================================================

def example_subtree(doc: dict, example: dict | None) -> list[dict]:
    """The steps of this case, and of no other example of the same outline.

    Serenity writes one record per scenario, with one top-level step per example,
    whose description is the label results.json also records. Matched on that
    label, and on the example index only as a fallback, so a label the suite
    renders differently in a later release degrades to a positional match rather
    than to the whole outline.
    """
    steps = doc.get("testSteps") or []
    if not example:
        return steps
    label = (example.get("label") or "").strip()
    if label:
        for st in steps:
            if isinstance(st, dict) and (st.get("description") or "").strip() == label:
                return [st]
    idx = example.get("index")
    if isinstance(idx, int) and 1 <= idx <= len(steps):
        return [steps[idx - 1]]
    return steps


# The test suite writes its resolved step descriptions with one level of
# backslash escaping above the JSON encoding, so a step that reads
# `matches "40\d"` in the feature file arrives here as `matches \"40\\d\"`. That
# level is removed, so a step quoted in this document reads as it does in the
# feature file and as `GAP-ANALYSIS.md` prints it. Only `\\` and `\"` are
# unescaped, plus the `\n` the suite writes between the rows of a step's data
# table; a lone backslash before anything else is left alone, so a regular
# expression that was not escaped in the first place cannot be corrupted by this.
_ESCAPED = re.compile(r'\\([\\"nt])')
_ESCAPES = {"\\": "\\", '"': '"', "n": "\n", "t": "\t"}


def _unescape(text: str) -> str:
    return _ESCAPED.sub(lambda m: _ESCAPES[m.group(1)], text)


def _report_data(step: dict, prefix: str) -> list[dict]:
    return [rd for rd in (step.get("reportData") or [])
            if isinstance(rd, dict) and str(rd.get("title") or "").startswith(prefix)]


def read_steps(subtree: list[dict]) -> dict:
    """Everything the outcome record holds about this case's execution.

    One walk, in Serenity's own order, collecting:

      * `executed`  - every leaf step with its verdict, as the resolved Gherkin
                      text the suite logged rather than the template text, so the
                      list reads as what actually ran;
      * `request`   - the resolved description and the cURL of the last request
                      step before the failure;
      * `evaluations` - the FHIRPath expressions the failing step evaluated and
                      the values it got, as the suite recorded them;
      * `rbel`      - the traffic dump attached last in the subtree.
    """
    executed: list[dict] = []
    request = {"resolved": "", "curl": ""}
    evaluations: list[dict] = []
    rbel: str | None = None
    failing: dict | None = None

    def resolved(step: dict) -> str:
        rows = _report_data(step, "Tiger Resolved Step Description")
        if rows and rows[0].get("contents"):
            return _unescape(str(rows[0]["contents"]))
        return str(step.get("description") or "")

    def walk(steps) -> None:
        nonlocal request, rbel, failing
        for st in steps or []:
            if not isinstance(st, dict):
                continue
            children = st.get("children") or []
            curls = [str(rd.get("contents") or "") for rd in _report_data(st, "cURL")]
            if curls:
                # A request step, not an assertion step: only those carry a cURL.
                request = {"resolved": resolved(st), "curl": curls[-1]}
            for rd in _report_data(st, "RBellog"):
                if rd.get("path"):
                    rbel = str(rd["path"])
            if st.get("result") in ("FAILURE", "ERROR"):
                for rd in _report_data(st, "ERROR"):
                    evaluations.append({
                        "title": str(rd.get("title") or ""),
                        "values": str(rd.get("contents") or ""),
                    })
                if not children and failing is None:
                    failing = {"resolved": resolved(st),
                               "description": str(st.get("description") or ""),
                               "exception": st.get("exception")}
            if not children:
                executed.append({"step": resolved(st),
                                 "result": str(st.get("result") or "")})
            walk(children)

    walk(subtree)
    return {"executed": executed, "request": request, "evaluations": evaluations,
            "rbel": rbel, "failing": failing}


# =============================================================================
# Deriving the four fields of an entry
# =============================================================================

_FIXTURE = re.compile(r"!\{file\('([^']+)'\)\}")
_MATCHES = re.compile(r'matches\s+"(.*?)"\s*$')
_ELEMENT = re.compile(r"Element value:\s*\n(.*?)\n\s*Expected:\s*\n(.*)", re.S)
_STATUS = re.compile(r"^HTTP/\d\.\d (\d{3})")
_REQLINE = re.compile(r"^([A-Z]+) (\S+) HTTP/\d\.\d")


def fixture_of(resolved_request: str) -> str:
    m = _FIXTURE.search(resolved_request or "")
    return m.group(1) if m else ""


def expected_received(failure: dict, failing: dict | None,
                      response: dict | None) -> dict:
    """The one-line statement of what was expected against what came back.

    Read from the suite's own assertion error where it states both - Tiger's
    `Element value: <actual> Expected: <expected>` form, which every status-code
    assertion produces - and otherwise from the assertion text and the response
    status line separately. Nothing is computed: where the record does not state an
    expectation in comparable terms, the field says so.
    """
    msg = (failure or {}).get("message") or ""
    m = _ELEMENT.search(msg)
    if m:
        actual = m.group(1).strip()
        expected = m.group(2).strip().split("\n")[0].strip()
        return {"expected": expected, "received": actual,
                "comparable": True,
                "line": f"expected `{expected}`, received `{actual}`"}

    step = (failing or {}).get("resolved") or (failure or {}).get("step") or ""
    want = _MATCHES.search(step.strip())
    status = ""
    if response:
        sm = _STATUS.match(response.get("head") or "")
        status = sm.group(1) if sm else (response.get("title") or "")
    if want and status:
        return {"expected": want.group(1), "received": status, "comparable": True,
                "line": f"expected `{want.group(1)}`, received HTTP `{status}`"}
    if status:
        return {"expected": "", "received": status, "comparable": False,
                "line": f"the assertion is not about the status code; the SUT "
                        f"answered HTTP `{status}`"}
    return {"expected": "", "received": "", "comparable": False,
            "line": "the official report records no response for this case"}


def curl_for(request: dict | None, level_run: dict, level_dir: str) -> list[str]:
    """The request replayed against the round's own endpoint, as shell.

    Built from the raw request the SUT received rather than from the suite's own
    cURL, because the suite's cURL names its proxy alias (`http://fhirserver/`) and
    not a reachable URL. Four headers are dropped: the two the proxy added
    (`Proxy-Connection`, `Host`), the one curl must compute itself
    (`Content-Length`), and the credential. `Accept-Encoding` is dropped as well,
    so the reply comes back readable.
    """
    if not request:
        return []
    head = (request.get("head") or "").split("\n")
    if not head:
        return []
    m = _REQLINE.match(head[0])
    if not m:
        return []
    method, path = m.group(1), m.group(2)
    base = (level_run.get("sut") or {}).get("baseUrlFromHost") or ""
    prefix = ""
    if base:
        # The path on the wire carries the endpoint's own prefix; the base URL
        # recorded for the host carries it too. Join them without doubling it.
        tail = "/" + base.split("://", 1)[-1].split("/", 1)[-1] if "/" in \
            base.split("://", 1)[-1] else ""
        if tail and tail != "/" and path.startswith(tail):
            prefix, path = tail, path[len(tail):] or "/"
    drop = ("proxy-connection:", "host:", "content-length:", "authorization:",
            "accept-encoding:", "user-agent:")
    headers = [h for h in head[1:]
               if h.strip() and not h.lower().startswith(drop)]
    body = request.get("body") or ""

    lines = [f'BASE={base}' + (f"   # {level_dir}/run.json, sut.baseUrlFromHost"
                               if base else "")]
    cmd = ['curl -i -u "$SUT_USER:$SUT_PASSWORD"']
    for h in headers:
        k, _, v = h.partition(":")
        cmd.append(f"     -H '{k.strip()}: {v.strip()}'")
    if method != "GET":
        cmd.append(f"     -X {method}")
    if body:
        cmd.append("     --data-binary @request-body.json")
    cmd.append(f'     "$BASE{path}"')
    lines.append(" \\\n".join(cmd))
    return lines


# =============================================================================
# Assembling
# =============================================================================

def gap_lookup(gap_doc: dict | None) -> dict:
    """Case key -> the gap entry that case was attributed to, with its id.

    The id is the one gap-analysis.json assigns, which is the one
    `GAP-ANALYSIS.md` prints, so an entry here and an entry there can be read side
    by side. It is read, not recomputed: the ids are assigned per run and shift
    with the requirement index, so a second numbering would silently disagree.
    """
    out: dict[tuple, dict] = {}
    for n, gap in enumerate((gap_doc or {}).get("gaps") or [], start=1):
        for occ in gap.get("occurrences") or []:
            key = (occ.get("level"), occ.get("feature"), occ.get("featureLine"),
                   occ.get("scenario"), (occ.get("example") or "").strip() or None)
            out[key] = {
                "id": gap.get("id") or f"GAP-{n:03d}",
                "category": gap.get("category"),
                "expected": gap.get("expected"),
                "fhirFeature": gap.get("fhirFeature"),
                "mechanism": gap.get("mechanism"),
                "irisLocus": gap.get("irisLocus"),
                "reason": gap.get("reason"),
                "requestContext": gap.get("requestContext"),
                "requirements": gap.get("requirements") or [],
            }
    return out


def case_key(case: dict) -> tuple:
    ex = case.get("example") or {}
    return (case.get("level"), case.get("feature"), case.get("featureLine"),
            case.get("scenario"), (ex.get("label") or "").strip() or None)


def sort_key(case: dict) -> tuple:
    ex = case.get("example") or {}
    return (case.get("module") or "", case.get("feature") or "",
            case.get("featureLine") or 0, ex.get("index") or 0,
            case.get("scenario") or "")


def build(results: dict, gap_doc: dict | None, bundle: Bundle) -> dict:
    gaps = gap_lookup(gap_doc)
    entries: list[dict] = []
    for level, L in (results.get("levels") or {}).items():
        level_dir = L.get("directory") or level.lower()
        run = L.get("run") or {}
        failing = [c for c in (L.get("cases") or [])
                   if c.get("result") not in ("SUCCESS", None)]
        for case in sorted(failing, key=sort_key):
            entries.append(entry_for(case, level, level_dir, run, gaps, bundle))
    return {"entries": entries}


def entry_for(case: dict, level: str, level_dir: str, run: dict,
              gaps: dict, bundle: Bundle) -> dict:
    report = case.get("report") or {}
    doc = bundle.outcome(report.get("outcomeJson") or "")
    steps = read_steps(example_subtree(doc, case.get("example"))) if doc else \
        {"executed": [], "request": {"resolved": "", "curl": ""},
         "evaluations": [], "rbel": None, "failing": None}

    request = response = None
    traffic_path = ""
    traffic_note = ""
    if steps["rbel"]:
        got = bundle.traffic(level_dir, steps["rbel"])
        if got:
            msgs = messages(got[0])
            traffic_path = got[1]
            request, response = last_exchange(msgs)
            if not msgs:
                traffic_note = ("the traffic log is present and records no HTTP "
                                "message: this case made no request of its own")
        else:
            traffic_note = "the traffic log this case names is not in the bundle"
    else:
        traffic_note = "the official report attaches no traffic log to this case"

    failure = case.get("failure") or {}
    ex = case.get("example") or {}
    gap = gaps.get(case_key(case)) or {}
    verdict = expected_received(failure, steps["failing"], response)

    return {
        "level": level,
        "levelDir": level_dir,
        "baseUrl": (run.get("sut") or {}).get("baseUrlFromHost") or "",
        "module": case.get("module"),
        "feature": case.get("feature"),
        "featureLine": case.get("featureLine"),
        "scenario": case.get("scenario"),
        "example": {"index": ex.get("index"), "line": ex.get("line"),
                    "data": ex.get("data"), "label": ex.get("label")} if ex else None,
        "result": case.get("result"),
        "gap": gap,
        "attributedTo": ("not the system under test"
                         if gap.get("category") in NOT_SUT_CATEGORIES
                         else "the system under test") if gap else "not attributed",
        "citations": {
            "outcomeJson": report.get("outcomeJson"),
            "outcomeHtml": report.get("outcomeHtml"),
            "trafficLog": traffic_path,
        },
        "input": {
            "exampleData": ex.get("data"),
            "fixture": fixture_of(steps["request"].get("resolved", "")),
            "requestStep": steps["request"].get("resolved", ""),
            "suiteCurl": steps["request"].get("curl", ""),
            "stepsExecuted": steps["executed"],
            "request": request,
        },
        "expected": {
            "assertion": (steps["failing"] or {}).get("resolved")
                         or failure.get("step") or "",
            "inFhirTerms": gap.get("expected") or "",
            "statement": verdict["expected"],
        },
        "received": {
            "response": response,
            "statement": verdict["received"],
            "errorType": failure.get("errorType"),
            "message": failure.get("message"),
            "evaluations": steps["evaluations"],
            "note": traffic_note,
        },
        "outcomeLine": verdict["line"],
        "reproduce": curl_for(request, run, level_dir),
    }


# =============================================================================
# Rendering
# =============================================================================

ISIK_EXPLAINER = (
    "**ISiK** (*Informationstechnische Systeme im Krankenhaus*) is Gematik's "
    "mandatory FHIR interoperability specification for German hospital information "
    "systems. It is a set of FHIR R4 implementation guides plus a conformance test "
    "suite; *Stufe 3* and *Stufe 5* are two successive stages of it, each with its "
    "own package set, tested independently. Conformance is certified through "
    "Gematik's *Bestätigungsverfahren*, for which the authoritative artefact is the "
    "test suite's own `test-report.zip` — not this document."
)


def md_escape(text: str) -> str:
    return (text or "").replace("|", "\\|")


def fence(text: str, lang: str = "") -> list[str]:
    """A fenced block whose fence is longer than any run of backticks inside it."""
    longest = max((len(m) for m in re.findall(r"`+", text or "")), default=0)
    bar = "`" * max(3, longest + 1)
    return [f"{bar}{lang}", text or "", bar, ""]


def body_block(body: str, nbytes: int, lang: str, what: str,
               level_dir: str, traffic: str, limit: int) -> list[str]:
    """One body, quoted in full or cut with a statement of what was cut.

    A cut quote names the sha256 of the complete body and the command that reads
    it back out of the archive, so the entry stays verifiable without carrying a
    megabyte of one resource.
    """
    out: list[str] = []
    digest = hashlib.sha256((body or "").encode("utf-8")).hexdigest()
    if len(body or "") <= limit:
        out.append(f"{what} ({len(body or '')} characters, "
                   f"{nbytes} bytes on the wire, sha256 `{digest}`):")
        out.append("")
        out += fence(body, lang)
        return out
    omitted = len(body) - limit
    out.append(f"{what} ({len(body)} characters, {nbytes} bytes on the wire, "
               f"sha256 `{digest}`) — the first {limit} characters:")
    out.append("")
    out += fence(body[:limit], lang)
    out.append(f"The remaining {omitted} character(s) are not quoted. The "
               f"complete message is in this round's own archive, in the test "
               f"suite's traffic view for this case — the sha256 above is of the "
               f"whole message, so a recovered copy can be checked against it:")
    out.append("")
    rel = traffic.split("official/extracted/debug-report/")[-1] if traffic else ""
    out += fence(f"unzip -p {level_dir}/official/debug-report.zip \\\n"
                 f"  '{rel}' > /tmp/exchange.html", "sh")
    return out


def front_matter(results: dict, gap_doc: dict | None, generated: str) -> list[str]:
    rows = [("Run", f"`{results.get('runId')}`"),
            ("Generated", generated),
            ("Generated by", f"`{GENERATOR}`"),
            ("Companion documents",
             "`GAP-ANALYSIS.md` (one entry per distinct defect), "
             "`analysis/results.md` (the cited results)")]
    for level, L in (results.get("levels") or {}).items():
        run = L.get("run") or {}
        sut = run.get("sut") or {}
        tool = run.get("testTool") or {}
        rows.append((f"{level} SUT",
                     f"`{sut.get('image')}` ({sut.get('irisBuild')}), namespace "
                     f"`{sut.get('namespace')}`, endpoint `{sut.get('baseUrl')}`"))
        rows.append((f"{level} SUT image digest", f"`{sut.get('imageDigest')}`"))
        rows.append((f"{level} test suite",
                     f"`{tool.get('image')}` (`{tool.get('imageDigest')}`)"))
    src = ((gap_doc or {}).get("index") or {}).get("source") or {}
    if src.get("suite"):
        rows.append(("Test suite feature corpus", src["suite"]))
    if (gap_doc or {}).get("index", {}).get("generatedAt"):
        rows.append(("Attribution index",
                     f"`gap-analysis/index.json`, built "
                     f"{gap_doc['index']['generatedAt']}"))
    out = ["| | |", "|---|---|"]
    out += [f"| {k} | {md_escape(v)} |" for k, v in rows]
    out.append("")
    return out


def render(results: dict, gap_doc: dict | None, analysis: dict, generated: str,
           limit: int) -> str:
    entries = analysis["entries"]
    w: list[str] = []

    w.append(f"# ISiK conformance case evidence — {results.get('runId')}")
    w.append("")
    w.append("Audience: InterSystems IRIS for Health developers. This is a work "
             "list, not a conformance statement.")
    w.append("")
    w.append(ISIK_EXPLAINER)
    w.append("")
    w += front_matter(results, gap_doc, generated)
    w.append("Every claim below is a join over three files and nothing else: this "
             "run's `analysis/results.json` (which cases failed), this run's "
             "`gap-analysis.json` (what each failure was attributed to) and the "
             "official archives `<stufe>/official/test-report.zip` and "
             "`<stufe>/official/debug-report.zip` (what went over the wire). "
             "Re-running `make case-evidence` on an archived bundle reproduces "
             "this document exactly.")
    w.append("")

    # ---- 1 Summary ----------------------------------------------------------
    with_exchange = [e for e in entries if e["received"]["response"]]
    with_body = [e for e in entries if (e["input"]["request"] or {}).get("body")]
    sut = [e for e in entries if e["attributedTo"] == "the system under test"]
    w.append("## 1 Summary")
    w.append("")
    w.append(f"**{len(entries)} failing test case(s), each with the exact input "
             f"data used, the exact output expected and received, and the steps to "
             f"reproduce it.**")
    if len(sut) == len(entries):
        w.append(f"`gap-analysis.json` attributes all {len(entries)} of them to "
                 f"the system under test.")
    else:
        w.append(f"`gap-analysis.json` attributes {len(sut)} of them to the system "
                 f"under test and the remaining {len(entries) - len(sut)} "
                 f"elsewhere; each of those entries says so and names the category "
                 f"`GAP-ANALYSIS.md` puts it in.")
    w.append("")
    w.append("| Level | Failing cases | HTTP exchange recorded | Request body "
             "recorded | No exchange recorded |")
    w.append("|---|---:|---:|---:|---:|")
    for level in (results.get("levels") or {}):
        lv = [e for e in entries if e["level"] == level]
        ex = [e for e in lv if e["received"]["response"]]
        bd = [e for e in lv if (e["input"]["request"] or {}).get("body")]
        w.append(f"| {level} | {len(lv)} | {len(ex)} | {len(bd)} | "
                 f"{len(lv) - len(ex)} |")
    w.append(f"| **Total** | **{len(entries)}** | **{len(with_exchange)}** | "
             f"**{len(with_body)}** | **{len(entries) - len(with_exchange)}** |")
    w.append("")

    # ---- 2 Coverage ---------------------------------------------------------
    w.append("## 2 Coverage of this run")
    w.append("")
    w.append("A case list is only as complete as the run behind it. This table "
             "states what was actually executed, so an incomplete run cannot be "
             "mistaken for a clean one.")
    w.append("")
    w.append("| Level | Cases executed | Expected in scope | Passed | Failed | "
             "Features not executed |")
    w.append("|---|---:|---:|---:|---:|---:|")
    for level, L in (results.get("levels") or {}).items():
        t = L.get("totals") or {}
        e = L.get("expected") or {}
        w.append(f"| {level} | {t.get('cases', 0)} | "
                 f"{e.get('casesExecuted', t.get('cases', 0))} | "
                 f"{t.get('passed', 0)} | {t.get('failed', 0)} | "
                 f"{len(L.get('notExecuted') or [])} |")
    w.append("")

    # ---- 3 Derivation -------------------------------------------------------
    w += derivation_clause()

    # ---- 4 Cases ------------------------------------------------------------
    w.append("## 4 Cases")
    w.append("")
    w.append(f"{len(with_exchange)} case(s), one subclause each, ordered by level, "
             "module, feature file and feature line. A case whose exchange the "
             "official report does not record is in section 5 instead.")
    w.append("")
    for e in entries:
        if e["received"]["response"]:
            w += render_entry(e, limit)

    # ---- 5 Cases without an exchange ---------------------------------------
    rest = [e for e in entries if not e["received"]["response"]]
    w.append("## 5 Cases for which the official report records no exchange")
    w.append("")
    if not rest:
        w.append("None. Every failing case of this run carries a recorded request "
                 "and response.")
        w.append("")
    else:
        w.append(f"{len(rest)} case(s). Each one's assertion is made about a "
                 "response obtained in an earlier scenario of the same feature: "
                 "Tiger's *current response* is feature-scoped state, so a "
                 "scenario can assert over a response it did not itself request. "
                 "The input, the expectation and the recorded error are stated as "
                 "for every other case; the exchange has to be read in the "
                 "scenario that made it.")
        w.append("")
        for e in rest:
            w += render_entry(e, limit)

    # ---- 6 Reconciliation ---------------------------------------------------
    w += reconciliation_clause(results, gap_doc, entries)
    return "\n".join(w).rstrip() + "\n"


def derivation_clause() -> list[str]:
    w: list[str] = []
    w.append("## 3 How each entry was derived")
    w.append("")
    w.append("Each of the four fields of an entry has one source, and no field is "
             "authored:")
    w.append("")
    w.append("| Field | Read from |")
    w.append("|---|---|")
    w.append("| Input data used | the Scenario Outline row as `analysis/results.json` "
             "records it; the fixture path out of the resolved Gherkin step; the "
             "request as the system under test received it, out of the traffic log "
             "in `debug-report.zip` |")
    w.append("| Output expected | the assertion that failed, as the test suite "
             "resolved it; and the same expectation in FHIR terms as "
             "`GAP-ANALYSIS.md` states it for the gap this case was attributed to |")
    w.append("| Output received | the response out of the same traffic log, with "
             "the suite's own FHIRPath evaluation and assertion error beside it |")
    w.append("| How to reproduce | the request, rewritten onto the endpoint "
             "`<stufe>/run.json` records for host access |")
    w.append("")
    w.append("**Which exchange belongs to a case.** A Scenario Outline writes one "
             "outcome record for all of its examples, so the record is first "
             "narrowed to the example's own steps by the label the suite gave it. "
             "Inside those steps the suite attaches its traffic log to the step "
             "that was executing when the log was written; the log is scoped to "
             "the example, and the exchange the assertion was made about is "
             "therefore its last response together with the request before it. "
             "Where that rule finds nothing, the case is in section 5 and says so.")
    w.append("")
    w.append("**Bodies are decoded, not re-encoded.** The suite renders a message "
             "as hex whenever its own parsers did not claim it, which is what "
             "happens to every gzip-encoded response. Such a message is unhexed "
             "and inflated here, so what an entry shows is what the system under "
             "test sent. Headers are reproduced as they arrived, including their "
             "original casing, and a body is quoted in full with its sha256 unless "
             "it exceeds the quoting limit, in which case the entry states how "
             "much was cut and names the command that reads the rest out of the "
             "archive.")
    w.append("")
    w.append("**One thing is withheld.** The `Authorization: Basic …` header is "
             f"replaced by `{REDACTED}`. This laboratory's credentials are "
             "generated per deployment and are recorded on purpose in "
             "`provenance/CREDENTIALS.md` and in section 1 of "
             "`analysis/results.md`, so withholding them protects nothing — but a "
             "document that is forwarded outside the laboratory should not carry a "
             "credential in a form that can be pasted into a shell. Nothing else "
             "is filtered.")
    w.append("")
    w.append(f"One `Authorization` value is **not** withheld: the literal "
             f"`{UNRESOLVED_TOKEN}`, which is the test suite's own placeholder "
             "for a Bearer token it was never given. That the suite ran the "
             "SMART-on-FHIR scenarios with its placeholder unresolved is the "
             "evidence those scenarios rest on, and is what `GAP-ANALYSIS.md` "
             "cites to place them outside the product, so withholding it would "
             "remove a finding rather than a secret. Where such a step appears "
             "below, the entry also states which `Authorization` scheme the "
             "request the system under test received actually carried, because "
             "the two are not the same.")
    w.append("")
    w.append("**The requirement is not restated per case.** Which ISiK "
             "CapabilityStatement or StructureDefinition requires the behaviour, "
             "which FHIR package supplies it, and which IRIS subsystem to open are "
             "properties of the defect, not of the case, and are stated once per "
             "defect in `GAP-ANALYSIS.md`. Every entry below names its gap id so "
             "the two documents can be read side by side.")
    w.append("")
    w.append("### 3.1 Reproducing a case")
    w.append("")
    w.append("The per-case commands assume the round's own deployment, and the two "
             "variables this laboratory keeps its endpoint credentials in:")
    w.append("")
    w += fence("# in the repository root, with the round's .env in place\n"
               "make clean && make all            # the SUT, cold: see the warning below\n"
               "export SUT_USER=\"$(grep -E '^SUT_USER=' .env.resolved | cut -d= -f2-)\"\n"
               "export SUT_PASSWORD=\"$(grep -E '^SUT_PASSWORD=' .env.resolved | cut -d= -f2-)\"",
               "sh")
    w.append("Take the deployment cold. A `docker compose` recreate leaves the "
             "endpoints uninstalled while the bootstrap marker still claims they "
             "are there, and a case replayed against such a deployment fails for "
             "that reason rather than for the one documented here. Read back "
             "`provenance/iris-provenance.json` before trusting a reproduction.")
    w.append("")
    w.append("A reproduction needs the round's staged corpus, not only the "
             "request: every read, search and update case names a resource this "
             "laboratory seeded before the run. `provenance/corpus/` holds the "
             "instances as they were staged and `provenance/seed/<Level>.json` the "
             "per-resource write and read-back records, so a case that depends on "
             "one can be checked against the instance the run actually had.")
    w.append("")
    w.append("Where an entry's request carries a body, the reproduction reads it "
             "from `request-body.json`: save the quoted request body to that name, "
             "unchanged, in the directory the command runs in.")
    w.append("")
    w.append("### 3.2 Reading the exchange in the suite's own report")
    w.append("")
    w.append("Every entry cites three paths into the round's bundle: the Serenity "
             "outcome record inside the submitted archive, the browsable page for "
             "that case, and the traffic log the exchange was read from. All three "
             "are also addressable inside the archives of a slim bundle, which is "
             "the form that travels:")
    w.append("")
    w += fence("unzip -p stufe5/official/test-report.zip "
               "'unresolvedReports/<sha>.json' | python3 -m json.tool\n"
               "unzip -p stufe5/official/debug-report.zip "
               "'serenity/downloadable/<file>' > /tmp/exchange.html", "sh")
    w.append("### 3.3 Diffing two rounds")
    w.append("")
    w.append("Entries are keyed and ordered by level, module, feature file, "
             "feature line and example index, never by a sequence number, so an "
             "entry does not move when a different entry is added or removed. A "
             "`diff` of two rounds' `CASE-EVIDENCE.md` therefore shows the cases "
             "that changed. It is the intended way to read what a release did and "
             "did not move, and it is what makes this document comparable with "
             "the same document of the 2026-08-07 reference round.")
    w.append("")
    w.append("Four classes of line differ between two rounds without anything "
             "having changed in the system under test, and a diff has to be read "
             "past them:")
    w.append("")
    w.append("a) the `Date` response header, and `Server` where the base image "
             "moved;")
    w.append("")
    w.append("b) the `User-Agent` request header, which carries the test suite "
             "container's own JRE version, and the `Host` header, which carries "
             "the address the container network assigned;")
    w.append("")
    w.append("c) resource ids, `Bundle.id`, `meta.versionId`, `meta.lastUpdated` "
             "and search `self` links in a response body, all of which the "
             "endpoint generates per run;")
    w.append("")
    w.append("d) every `sha256` and character count that follows from a) to c).")
    w.append("")
    w.append("A request body is not in that list: it comes from a fixture inside "
             "the pinned test suite image, so two rounds under the same suite "
             "version send the same bytes, and a difference in a request body "
             "means the suite version differs. Two rounds under different suite "
             "versions are not case-comparable at all; `DELIVERABLES.md` states "
             "which rounds may be compared with which.")
    w.append("")
    return w


def reconciliation_clause(results: dict, gap_doc: dict | None,
                          entries: list[dict]) -> list[str]:
    w: list[str] = []
    w.append("## 6 Reconciliation")
    w.append("")
    official = sum((L.get("totals") or {}).get("failed", 0)
                   for L in (results.get("levels") or {}).values())
    attributed = len([e for e in entries if e["gap"]])
    w.append("| | |")
    w.append("|---|---:|")
    w.append(f"| Failing cases in the official report | {official} |")
    w.append(f"| Cases documented in this annex | {len(entries)} |")
    w.append(f"| Of those, joined to an entry of `GAP-ANALYSIS.md` | {attributed} |")
    w.append(f"| With a recorded request and response | "
             f"{len([e for e in entries if e['received']['response']])} |")
    w.append("")
    if len(entries) != official:
        w.append("**The two counts differ.** This annex documents the cases "
                 "`analysis/results.json` records as failing; a difference against "
                 "the official report's own count means the bundle is incomplete. "
                 "Treat this document as incomplete and re-generate it from the "
                 "official archives before quoting it.")
        w.append("")
    if attributed != len(entries):
        w.append(f"**{len(entries) - attributed} case(s) carry no gap id.** Their "
                 "entry states the input, the expectation and the received "
                 "response as for every other case, but `gap-analysis.json` does "
                 "not attribute them, so nothing here says what subsystem they "
                 "belong to. Re-generate the gap analysis for this round before "
                 "reading an absent attribution as an absent defect.")
        w.append("")
    recon = ((gap_doc or {}).get("reconciliation") or {})
    if recon:
        line = (f"`GAP-ANALYSIS.md` collapses these cases to "
                f"{recon.get('entries', '?')} entries, of which "
                f"{recon.get('irisGapEntries', '?')} are the product's and account "
                f"for {recon.get('irisGapCases', '?')} of the failing cases.")
        if recon.get("entries") != recon.get("irisGapEntries"):
            line += (" The remainder are the categories that document states are "
                     "not the product's.")
        line += (" Neither count is recomputed here: this annex shows cases, that "
                 "document counts defects.")
        w.append(line)
        w.append("")
    return w


def heading_of(e: dict) -> str:
    h = f"{e['feature']}:{e['featureLine']} · {e['scenario']}"
    if e.get("example") and e["example"].get("index"):
        h += f" · example {e['example']['index']}"
    return h


_SCHEME = re.compile(r'(?im)^\s*authorization\s*:\s*(\S+)')


def token_note(e: dict, req: dict | None) -> list[str]:
    """What a replay of a scope scenario can and cannot establish.

    The SMART-on-FHIR scenarios name a Bearer token the test suite was never given
    and sends as its own placeholder. Two facts are stated, both read rather than
    inferred: what the step named, and what scheme the request the system under
    test received actually carried. Where the two differ, a replay measures the
    endpoint's behaviour for an authenticated caller without an access token - the
    same thing the run measured - and not the scope enforcement the scenario is
    about, so the entry says so rather than letting the command look complete.
    """
    named = any(UNRESOLVED_TOKEN in (st.get("step") or "")
                for st in e["input"]["stepsExecuted"])
    if not named:
        return []
    m = _SCHEME.search((req or {}).get("head") or "")
    scheme = m.group(1) if m else ""
    w = [f"The step named `Bearer {UNRESOLVED_TOKEN}`: the test suite's own "
         "placeholder for a token, which this laboratory has no authorisation "
         "server to issue."]
    if scheme and scheme.lower() != "bearer":
        w[0] += (f" The request the system under test received carries "
                 f"`Authorization: {scheme} …` instead — this laboratory's own "
                 f"endpoint credential — so no access token and no scope reached "
                 f"the endpoint in this run.")
    w[0] += (" A replay therefore establishes what the run established: how the "
             "endpoint answers a caller who presents no scope. It does not "
             "establish whether the endpoint enforces the scope the scenario "
             "names; that requires a token issued for it.")
    return w + [""]


def render_entry(e: dict, limit: int) -> list[str]:
    w: list[str] = []
    w.append(f"### {heading_of(e)}")
    w.append("")

    gap = e["gap"]
    gap_cell = (f"{gap['id']} · {CAT_TITLE.get(gap.get('category'), gap.get('category'))}"
                if gap else "not attributed by `gap-analysis.json`")
    rows = [("Level, module", f"{e['level']} / {e['module']}"),
            ("Gap", gap_cell),
            ("Attributed to", e["attributedTo"]),
            ("Verdict", f"`{e['result']}`"),
            ("Outcome", e["outcomeLine"]),
            ("Official report", f"`{e['citations']['outcomeJson']}`")]
    if e["citations"].get("outcomeHtml"):
        rows.append(("Browsable", f"`{e['citations']['outcomeHtml']}`"))
    if e["citations"].get("trafficLog"):
        rows.append(("Traffic log", f"`{e['citations']['trafficLog']}`"))
    w.append("| | |")
    w.append("|---|---|")
    w += [f"| {k} | {md_escape(v)} |" for k, v in rows]
    w.append("")

    # ---- input --------------------------------------------------------------
    w.append("**Input data used**")
    w.append("")
    data = (e["example"] or {}).get("data") if e["example"] else None
    if data:
        w.append("Scenario Outline row: " + ", ".join(
            f"`{k}` = `{v}`" for k, v in data.items()) + ".")
        w.append("")
    if e["input"]["fixture"]:
        w.append(f"Fixture the step read, from inside the test suite image: "
                 f"`{e['input']['fixture']}`.")
        w.append("")
    if e["input"]["stepsExecuted"]:
        w.append("Steps executed, as the test suite resolved them:")
        w.append("")
        for n, st in enumerate(e["input"]["stepsExecuted"], start=1):
            step = (st["step"] or "").strip().replace("\n", "\n   ")
            w.append(f"{n}. `{st['result']}` — {step}")
        w.append("")
    req = e["input"]["request"]
    if req:
        w.append("Request as the system under test received it:")
        w.append("")
        w += fence(redact(req["head"]), "http")
        if req.get("body"):
            w += body_block(req["body"], req.get("bodyBytes") or 0, "json",
                            "Request body", e["levelDir"],
                            e["citations"].get("trafficLog") or "", limit)
    elif e["input"]["suiteCurl"]:
        w.append("The traffic log records no request for this case. The request "
                 "step the suite logged was:")
        w.append("")
        w += fence(redact(e["input"]["suiteCurl"]), "sh")
    else:
        w.append("No request of this case's own: see the note under *Output "
                 "received*.")
        w.append("")

    # ---- expected -----------------------------------------------------------
    w.append("**Output expected**")
    w.append("")
    if e["expected"]["assertion"]:
        w.append("The assertion that was made:")
        w.append("")
        w += fence(e["expected"]["assertion"].strip(), "gherkin")
    # `GAP-ANALYSIS.md` records "see the quoted step" where the assertion is already
    # its own statement of the expectation. Repeating that here would add a line
    # that tells the reader nothing.
    if e["expected"]["inFhirTerms"] and \
            e["expected"]["inFhirTerms"].strip() != "see the quoted step":
        w.append(f"In FHIR terms: {e['expected']['inFhirTerms']}")
        w.append("")
    if gap.get("reason"):
        w.append(f"Attributed to {gap['id']} because {gap['reason']}.")
        w.append("")

    # ---- received -----------------------------------------------------------
    w.append("**Output received**")
    w.append("")
    rsp = e["received"]["response"]
    if rsp:
        w.append("Response as the system under test sent it"
                 + (f", body {rsp['transferEncoding']}-encoded on the wire and "
                    f"decoded here" if rsp.get("transferEncoding") else "") + ":")
        w.append("")
        w += fence(redact(rsp["head"]), "http")
        if rsp.get("body"):
            w += body_block(rsp["body"], rsp.get("bodyBytes") or 0, "json",
                            "Response body", e["levelDir"],
                            e["citations"].get("trafficLog") or "", limit)
    elif e["received"]["note"]:
        note = e["received"]["note"]
        w.append(note[:1].upper() + note[1:] + ".")
        w.append("")
    for ev in e["received"]["evaluations"]:
        w.append("FHIRPath evaluation the test suite recorded:")
        w.append("")
        w += fence(f"{ev['title'].strip()}\n-> {ev['values'].strip()}")
    if e["received"]["errorType"]:
        w.append(f"The error the test suite raised, `{e['received']['errorType']}`:")
        w.append("")
        w += fence((e["received"]["message"] or "").strip())

    # ---- reproduce ----------------------------------------------------------
    w.append("**How to reproduce**")
    w.append("")
    if e["reproduce"]:
        w.append("With the round's deployment up and the two variables of clause "
                 "3.1 exported:")
        w.append("")
        w += fence("\n".join(e["reproduce"]), "sh")
        if (req or {}).get("body"):
            w.append("`request-body.json` is the request body quoted above, saved "
                     "unchanged.")
            w.append("")
        w += token_note(e, req)
        if e["outcomeLine"]:
            w.append(f"In this round: {e['outcomeLine']}.")
            w.append("")
    elif gap.get("requestContext") == "capability" and \
            e["received"]["evaluations"]:
        # The assertion is over the CapabilityStatement, and the suite recorded
        # both the FHIRPath it evaluated and the value it got. Fetching the
        # statement and evaluating that same expression reproduces the finding
        # exactly, without running the feature, which is what a developer wants.
        base = e.get("baseUrl") or ""
        w.append("This case asserts over the CapabilityStatement and makes no "
                 "request of its own. Fetch the statement and evaluate the "
                 "expression the test suite evaluated:")
        w.append("")
        paths = "\n".join(f"#   {ev['title'].split(' for: ', 1)[-1].strip()}"
                          for ev in e["received"]["evaluations"])
        w += fence(f'BASE={base}\n'
                   f'curl -s -u "$SUT_USER:$SUT_PASSWORD" \\\n'
                   f'     -H \'Accept: application/fhir+json\' \\\n'
                   f'     "$BASE/metadata" > /tmp/capabilitystatement.json\n'
                   f'# the expression(s) the suite evaluated, and what it got,\n'
                   f'# are quoted under *Output received* above:\n'
                   f'{paths}', "sh")
        w.append("The full scenario, if the step sequence itself is in question:")
        w.append("")
        w += fence(f"make test-{e['level'].lower()}   "
                   f"# the whole level; the case is {e['feature']}:"
                   f"{e['featureLine']}", "sh")
    else:
        w.append("This case makes no request of its own, so there is nothing to "
                 "replay in isolation. Reproduce it by running the feature it "
                 "belongs to:")
        w.append("")
        w += fence(f"make test-{e['level'].lower()}   "
                   f"# the whole level; the case is {e['feature']}:"
                   f"{e['featureLine']}", "sh")
    return w


_AUTH = re.compile(r'(?im)^(\s*authorization\s*:\s*)(.*)$')
_AUTH_INLINE = re.compile(r'(?i)(-H\s+["\']?Authorization:\s*)([^"\'\n]*)')
_BASIC = re.compile(r'(?i)\b(Basic|Bearer)\s+[A-Za-z0-9+/=._\-]{8,}')


def _hide(value: str) -> str:
    """Whether this header value is a credential at all.

    The test suite sends its own unresolved placeholder as a Bearer token in the
    SMART-on-FHIR scenarios, and that it did so is the evidence those cases rest
    on - it is what `GAP-ANALYSIS.md` cites to place them outside the product.
    Replacing it would remove the finding rather than a secret, so a value that is
    the placeholder is kept.
    """
    return UNRESOLVED_TOKEN not in (value or "")


def redact(text: str) -> str:
    """The credential out, and nothing else - see clause 3 of the document."""
    out = _AUTH.sub(lambda m: m.group(1) + (REDACTED if _hide(m.group(2))
                                            else m.group(2)), text or "")
    out = _AUTH_INLINE.sub(lambda m: m.group(1) + (REDACTED if _hide(m.group(2))
                                                   else m.group(2)), out)
    return _BASIC.sub(lambda m: m.group(0) if not _hide(m.group(0))
                      else f"{m.group(1)} {REDACTED}", out)


# =============================================================================
# Entry point
# =============================================================================

def cap_bodies(entry: dict, limit: int) -> dict:
    """The same body quoting limit, applied to the machine-readable form.

    Without this the JSON carries one 900 KB CapabilityStatement per case that
    asserted over it - some six megabytes of the same resource in a round. The
    cap costs nothing a diff needs: `bodySha256` is of the complete body, so two
    rounds whose bodies differ still differ in the JSON, and the complete message
    is recoverable from the archive by the path in `citations.trafficLog`.
    """
    out = json.loads(json.dumps(entry))                 # never mutate the source
    for msg in (out["input"].get("request"),
                out["received"].get("response")):
        if not isinstance(msg, dict):
            continue
        body = msg.get("body") or ""
        msg["bodySha256"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
        msg["bodyCharacters"] = len(body)
        msg["bodyTruncated"] = len(body) > limit
        if msg["bodyTruncated"]:
            msg["body"] = body[:limit]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run-dir", type=Path, required=True,
                    help="the round's report bundle")
    ap.add_argument("--out-md", type=Path,
                    help="default: <run-dir>/CASE-EVIDENCE.md")
    ap.add_argument("--out-json", type=Path,
                    help="default: <run-dir>/case-evidence.json")
    ap.add_argument("--generated-at",
                    help="override the timestamp, so two rounds diff cleanly")
    ap.add_argument("--max-body", type=int, default=MAX_BODY,
                    help=f"characters of a body quoted in full (default {MAX_BODY})")
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero when a failing case could not be documented")
    args = ap.parse_args()

    run_dir = args.run_dir.resolve()
    if not run_dir.is_dir():
        die(f"no run directory at {run_dir}")

    results = load_json(run_dir / "analysis" / "results.json",
                        "analysis/results.json")
    gap_doc = load_json(run_dir / "gap-analysis.json", "gap-analysis.json",
                        required=False)
    if gap_doc is None:
        print("case_evidence.py: gap-analysis.json is absent; entries will carry "
              "no gap id and no expectation in FHIR terms. Run "
              "'make gap-report' for this round first.", file=sys.stderr)

    generated = args.generated_at or datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")

    bundle = Bundle(run_dir)
    analysis = build(results, gap_doc, bundle)

    out_md = args.out_md or (run_dir / "CASE-EVIDENCE.md")
    out_json = args.out_json or (run_dir / "case-evidence.json")
    out_md.write_text(render(results, gap_doc, analysis, generated,
                             args.max_body), encoding="utf-8")
    out_json.write_text(json.dumps(
        {"runId": results.get("runId"), "generatedAt": generated,
         "generator": GENERATOR, "audience": "InterSystems IRIS for Health developers",
         "authoritativeSource": "<stufe>/official/test-report.zip",
         "maxBodyCharacters": args.max_body,
         "entries": [cap_bodies(e, args.max_body)
                     for e in analysis["entries"]]},
        indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    entries = analysis["entries"]
    missing = [e for e in entries if not e["received"]["response"]]
    print(f"  {len(entries)} failing case(s) documented, "
          f"{len(entries) - len(missing)} with a recorded exchange", file=sys.stderr)
    for e in missing:
        print(f"  no exchange: {heading_of(e)} — {e['received']['note']}",
              file=sys.stderr)
    if args.strict and missing:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
