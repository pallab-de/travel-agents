#!/usr/bin/env python
"""Validates the SCHEMA of a reviewer's claim ledger (the JSON in ../brief-core.md): every claim has a test, evidence and a result; every
contradicted claim has a finding; every unverifiable claim is declared; every finding names a general fix; a `yes` has no open findings.

  python check_ledger.py ledger.json

Coverage (did the reviewer examine every card, day, leg and booking, and the items the gate sampled?) is computed by review_gate.py, which
imports `schema` from here; this command only checks the ledger's own consistency. Items are identified by `row_id` (the id of the card,
day, stop, booking, entry or note), never by free-text name.
"""
import argparse, json, re, sys
from pathlib import Path

SK = Path(__file__).resolve().parents[2]
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

RESULTS = {'holds', 'contradicted', 'unverifiable'}
CODES = {'C1', 'C2', 'C3', 'C4', 'L1', 'L2', 'L3', 'L4', 'L5', 'L6', 'L7', 'L8', 'L9'}
KINDS = {'flag', 'number', 'verdict', 'score_reason', 'source_label', 'link', 'status', 'time', 'narrative'}




def schema(L, errs):
    for key in ('claims', 'findings', 'unverified', 'verdict'):
        if key not in L: errs.append(f'ledger has no "{key}"')
    claims, findings = L.get('claims', []), L.get('findings', [])
    if not claims: errs.append('ledger has zero claims: nothing was tested, so there is nothing to trust')
    if L.get('verdict') not in ('yes', 'no'): errs.append(f'verdict must be "yes" or "no", not {L.get("verdict")!r}')
    if L.get('verdict') == 'no' and not findings: errs.append('verdict is "no" but no finding says what is wrong')
    ids = set()
    for c in claims:
        cid = c.get('id')
        if not cid or cid in ids: errs.append(f'claim id missing or duplicated: {cid!r}')
        ids.add(cid)
        for k in ('where', 'row_id', 'text', 'test', 'evidence', 'result'):
            if not str(c.get(k) or '').strip(): errs.append(f'claim {cid}: empty "{k}" (a claim without a test and evidence is not a tested claim)')
        if c.get('result') not in RESULTS: errs.append(f'claim {cid}: result must be one of {sorted(RESULTS)}')
        if c.get('kind') not in KINDS: errs.append(f'claim {cid}: kind must be one of {sorted(KINDS)}')
    fclaims = {f.get('claim_id') for f in findings}
    for c in claims:
        if c.get('result') == 'contradicted' and c.get('id') not in fclaims: errs.append(f'claim {c.get("id")} is contradicted but has no finding')
    for f in findings:
        if f.get('code') not in CODES: errs.append(f'finding on {f.get("row_id")!r}: code must be one of {sorted(CODES)}')
        if not str(f.get('fix') or '').strip(): errs.append(f'finding on {f.get("row_id")!r}: no "fix" (state the general rule to change)')
        if re.search(r'\b(paste|hard-?code|manually (set|add|override))\b', str(f.get('fix') or ''), re.I):
            errs.append(f'finding on {f.get("row_id")!r}: fix looks like a one-off data edit; state the rule that is wrong for the whole class')
    declared = {u.get('claim_id') for u in L.get('unverified', [])}
    for c in claims:
        if c.get('result') == 'unverifiable' and c.get('id') not in declared: errs.append(f'claim {c.get("id")} is unverifiable but not listed in "unverified"')
    if L.get('verdict') == 'yes' and findings: errs.append(f'verdict is "yes" but {len(findings)} finding(s) are open')
    return claims, findings


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('ledger')
    a = ap.parse_args()
    L = json.loads(Path(a.ledger).read_text(encoding='utf-8'))
    errs = []
    claims, findings = schema(L, errs)
    tally = {r: sum(1 for c in claims if c.get('result') == r) for r in RESULTS}
    print(f'claims: {len(claims)} ({tally}); findings: {len(findings)}; unverified: {len(L.get("unverified", []))}; verdict: {L.get("verdict")}')
    for e in errs: print('REJECTED:', e)
    print(chr(10) + ('LEDGER OK' if not errs else f'{len(errs)} problem(s): this review does not count'))
    return 1 if errs else 0


if __name__ == '__main__':
    raise SystemExit(main())
