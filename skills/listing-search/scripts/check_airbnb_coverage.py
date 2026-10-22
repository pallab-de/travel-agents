#!/usr/bin/env python
"""Airbnb-specific coverage check: wraps the shared tools/coverage_check.py with a field manifest for
extract_airbnb.js's output. Run this on every batch extract_airbnb.js produces, before handing it to
place-matching or review-synthesis. See fetch-integrity's SKILL.md for the discipline this is part of.

Run from the project folder, after saving extract_airbnb.js's JSON output to a file:
  python "<plugin root>/skills/listing-search/scripts/check_airbnb_coverage.py" cands_raw.json
Prints a per-field report; exits 1 if the batch looks unhealthy.
"""
import argparse, json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'tools'))
from coverage_check import FieldSpec, check, report  # noqa: E402

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# Thresholds are deliberately not 1.0 -- see fetch-integrity's gotchas. price_amount is lower than the rest because a
# real minority of listings show no price line on the search-results page itself (a small share of a real search) and need their detail page fetched instead; that's a normal gap, not drift.
SPECS = [
    FieldSpec('id', lambda r: r.get('id'), min_coverage=0.98),
    FieldSpec('name', lambda r: r.get('name'), min_coverage=0.95),
    FieldSpec('coordinates', lambda r: r.get('ll') and tuple(r['ll']), min_coverage=0.95),
    FieldSpec('price_amount', lambda r: r.get('price_amount'), min_coverage=0.75, numeric_range=(5, 100000),
              cross_check=lambda r: r.get('price_amount') is not None and float(r['price_amount']) in
              [float(x) for x in re.findall(r'\d+(?:\.\d+)?', (r.get('price_text') or '').replace(',', ''))]),
    FieldSpec('rating', lambda r: r.get('rating'), min_coverage=0.5),   # many real listings are genuinely unrated ("New")
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('batch', help='JSON file: a list of records as extract_airbnb.js returns them')
    a = ap.parse_args()
    records = json.loads(Path(a.batch).read_text(encoding='utf-8'))
    ok, results = check(records, SPECS)
    print(report(ok, results, f'Airbnb search results ({a.batch})'))
    if not ok:
        print('\nDo not hand this batch to place-matching or review-synthesis. Re-verify extract_airbnb.js\'s field '
              'paths against a live page (see the script\'s own header) and re-extract before continuing.')
    return 0 if ok else 1


def selftest() -> int:
    cases = []
    base = {'id': '1', 'name': 'n', 'll': [1, 2], 'rating': 4.5}
    cases.append(('a non-numeric price_amount fails without a traceback', not check([dict(base, price_amount='abc', price_text='\u20b91,200')] * 10, SPECS)[0]))
    cases.append(('price 12 does not agree with a text price of 1,200', not check([dict(base, price_amount=12, price_text='\u20b91,200')] * 10, SPECS)[0]))
    cases.append(('price 1200 agrees with 1,200', check([dict(base, price_amount=1200, price_text='\u20b91,200 total')] * 10, SPECS)[0]))
    for name, ok in cases:
        print(('PASS ' if ok else 'FAIL ') + name)
    ok = all(x for _, x in cases)
    print(f"SELFTEST {'PASS' if ok else 'FAIL'} ({len(cases)} cases)")
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(selftest() if sys.argv[1:] == ['selftest'] else main())
