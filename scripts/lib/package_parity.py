#!/usr/bin/env python3
"""Classify a bundled FHIR package tarball against its published counterpart.

Byte comparison alone cannot answer the question the parity table exists to
answer. Two `.tgz` files carrying the same name@version routinely differ byte for
byte for reasons that say nothing about whether the *definitions* agree:

  * gzip and tar embed timestamps, so a repack of identical content differs;
  * JSON serialisation differs (`"a" : 1` vs `"a": 1`, key order, indentation);
  * the IG publisher writes SNAPSHOT-generated StructureDefinitions into the
    package it ships, while Simplifier serves the DIFFERENTIAL-only form. That
    single difference made 342 KB vs 20 KB of the same profile on suite 2.7.0.

The last one is the important case, because it is not a discrepancy at all: a
snapshot is *derived* from the differential plus the base definition, so a
profile that agrees everywhere except for the presence of `snapshot` is the same
profile. FHIR tooling regenerates the snapshot on load either way.

What this script reports, per package:

  EQUAL      every member agrees semantically (parsed JSON equality, or bytes for
             non-JSON members). Nothing to investigate.
  SNAPSHOT   as EQUAL, except that some StructureDefinitions carry a `snapshot`
             in one copy and not the other. Still the same definitions.
  DIFFER     something else differs: a member present in one copy only, a
             non-JSON member whose bytes differ, or a JSON member whose content
             differs beyond `snapshot`. The details are printed, because this is
             the one status that needs a human decision.

Element lists inside `differential` and `snapshot` are compared as sets keyed by
element `id`, not as ordered arrays: the publisher and Simplifier emit the same
slice definitions in a different order, and an order difference in a set-like
list is not a difference in the profile.

Usage:
    package_parity.py --bundled DIR --published DIR [--json OUT]

Both directories hold `<name>-<version>.tgz` files. Only names present in both
are compared; the caller already knows about the others (they are ABSENT or
BUNDLED rows and are classified there).
"""

import argparse
import json
import sys
import tarfile
from pathlib import Path


def read_members(path):
    """Every regular file in the tarball, as name -> bytes."""
    out = {}
    with tarfile.open(path) as tf:
        for m in tf.getmembers():
            if not m.isfile():
                continue
            f = tf.extractfile(m)
            if f is not None:
                # Normalise the leading './' the two producers disagree about.
                name = m.name[2:] if m.name.startswith('./') else m.name
                out[name] = f.read()
    return out


def key_elements(obj):
    """Rewrite element arrays as id-keyed maps so order stops mattering.

    Only applied to `differential.element` and `snapshot.element`, where FHIR
    guarantees `id` is unique within the list. Anything without an `id` keeps its
    positional key, so a genuinely different list still compares unequal.
    """
    if not isinstance(obj, dict):
        return obj
    out = {}
    for k, v in obj.items():
        if k in ('differential', 'snapshot') and isinstance(v, dict) and isinstance(v.get('element'), list):
            elems = {}
            for i, e in enumerate(v['element']):
                eid = e.get('id') if isinstance(e, dict) else None
                elems[eid if eid is not None else f'#{i}'] = e
            out[k] = {**{kk: vv for kk, vv in v.items() if kk != 'element'}, 'element': elems}
        else:
            out[k] = key_elements(v) if isinstance(v, dict) else v
    return out


def compare_json(a_bytes, b_bytes):
    """(verdict, detail) for one JSON member. verdict in equal/snapshot/differ."""
    try:
        a = json.loads(a_bytes)
        b = json.loads(b_bytes)
    except (ValueError, UnicodeDecodeError):
        return 'differ', 'not parseable as JSON'

    ka, kb = key_elements(a), key_elements(b)
    if ka == kb:
        return 'equal', ''

    # Same definition, one side snapshot-generated?
    if isinstance(ka, dict) and isinstance(kb, dict):
        sa = {k: v for k, v in ka.items() if k != 'snapshot'}
        sb = {k: v for k, v in kb.items() if k != 'snapshot'}
        if sa == sb and ('snapshot' in ka) != ('snapshot' in kb):
            side = 'bundled' if 'snapshot' in ka else 'published'
            return 'snapshot', f'snapshot present only in the {side} copy'

    return 'differ', describe(ka, kb)


def describe(a, b, path='', depth=0, out=None):
    """A short, readable account of where two parsed documents part ways."""
    if out is None:
        out = []
    if len(out) >= 6 or depth > 6:
        return '; '.join(out)
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k == 'snapshot':
                continue  # reported separately; a derived artefact
            p = f'{path}.{k}' if path else k
            if k not in b:
                out.append(f'{p} only in bundled')
            elif k not in a:
                out.append(f'{p} only in published')
            elif a[k] != b[k]:
                describe(a[k], b[k], p, depth + 1, out)
            if len(out) >= 6:
                break
    elif a != b:
        sa, sb = str(a)[:60], str(b)[:60]
        out.append(f'{path or "value"}: {sa!r} vs {sb!r}')
    return '; '.join(out) or 'differs'


def compare_package(bundled, published):
    """(verdict, [detail lines]) for one package. verdict in equal/snapshot/differ."""
    A, B = read_members(bundled), read_members(published)
    details = []
    snapshot_only = 0
    verdict = 'equal'

    for name in sorted(set(A) | set(B)):
        if name not in B:
            details.append(f'{name}: present only in the bundled copy')
            verdict = 'differ'
            continue
        if name not in A:
            details.append(f'{name}: present only in the published copy')
            verdict = 'differ'
            continue
        if A[name] == B[name]:
            continue
        if name.endswith('.json'):
            v, detail = compare_json(A[name], B[name])
            if v == 'equal':
                continue          # serialisation only
            if v == 'snapshot':
                snapshot_only += 1
                if verdict == 'equal':
                    verdict = 'snapshot'
                continue
            details.append(f'{name}: {detail}')
            verdict = 'differ'
        else:
            details.append(f'{name}: bytes differ (not JSON)')
            verdict = 'differ'

    if snapshot_only:
        details.insert(0, f'{snapshot_only} StructureDefinition(s) differ only by a generated snapshot')
    return verdict, details


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bundled', required=True)
    ap.add_argument('--published', required=True)
    ap.add_argument('--json', help='write the full result here as JSON')
    args = ap.parse_args()

    bundled_dir, published_dir = Path(args.bundled), Path(args.published)
    result = {}
    for tgz in sorted(bundled_dir.glob('*.tgz')):
        counterpart = published_dir / tgz.name
        if not counterpart.exists():
            continue
        try:
            verdict, details = compare_package(tgz, counterpart)
        except (tarfile.TarError, OSError) as exc:
            verdict, details = 'differ', [f'could not be read: {exc}']
        result[tgz.name] = {'verdict': verdict, 'details': details}
        # One line per package on stdout, for the shell to read:
        #   <verdict> <TAB> <filename>
        print(f'{verdict}\t{tgz.name}')

    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')

    # Detail goes to stderr so stdout stays machine-readable.
    for name, r in sorted(result.items()):
        if r['verdict'] != 'equal':
            print(f'{name}: {r["verdict"]}', file=sys.stderr)
            for d in r['details']:
                print(f'    {d}', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
