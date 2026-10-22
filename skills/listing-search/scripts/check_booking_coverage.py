#!/usr/bin/env python
"""Booking.com-landmark-page coverage check: wraps the shared tools/coverage_check.py with a field
manifest for extract_booking_landmark.py's output. Run it on every batch before handing it to
place-matching or review-synthesis (fetch-integrity's SKILL.md). Booking.com's embedded JSON can change without
warning; this check is what catches it.

Run from the project folder, after extract_booking_landmark.py has written its candidates file:
  python "<plugin root>/skills/listing-search/scripts/check_booking_coverage.py" raw_booking.json
"""
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'tools'))
from coverage_check import FieldSpec, check, report  # noqa: E402

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# Booking.com's landmark pages embed (at least) three distinct record shapes under the SAME
# "accommodations" key, with genuinely different field availability by design, not by drift --
# verified by hand 2026-10-03 on a real batch: LxAccSearchResultSnippetProperty (the
# "featured"/value-pick carousels) is ~100% complete on every field below; LxAccMostBookedItem
# carries a rating but never a pin or price; LxAccTargetedHotelSegmentItem (often the single
# largest group) is a sparse, ad-like placement that reliably has only a name and a price, nothing
# else. Checking ALL records against one blanket threshold makes a perfectly healthy batch look
# like it failed, simply because the sparse kind happened to dominate the count that run -- so this
# checks each kind against ITS OWN expectation instead. If a NEW kind shows up, or an existing
# kind's field availability drops well below what's recorded here, that's the real drift signal.
KIND_SPECS = {
    'LxAccSearchResultSnippetProperty': [
        FieldSpec('property_id', lambda r: r.get('property_id'), min_coverage=0.98),
        FieldSpec('name', lambda r: r.get('name'), min_coverage=0.98),
        FieldSpec('coordinates', lambda r: r.get('ll') and tuple(r['ll']), min_coverage=0.9),
        FieldSpec('review_score', lambda r: r.get('review_score_10'), min_coverage=0.9, numeric_range=(0, 10)),
        FieldSpec('review_count', lambda r: r.get('review_count'), min_coverage=0.9, numeric_range=(1, 50000)),
        FieldSpec('price_amount', lambda r: r.get('price_amount'), min_coverage=0.9, numeric_range=(5, 1000000)),
    ],
    'LxAccMostBookedItem': [
        FieldSpec('property_id', lambda r: r.get('property_id'), min_coverage=0.98),
        FieldSpec('name', lambda r: r.get('name'), min_coverage=0.98),
        FieldSpec('review_score', lambda r: r.get('review_score_10'), min_coverage=0.85, numeric_range=(0, 10)),
        FieldSpec('review_count', lambda r: r.get('review_count'), min_coverage=0.85, numeric_range=(1, 50000)),
        # coordinates and price are KNOWN absent for this kind -- not checked, that's the point.
    ],
    'LxAccTargetedHotelSegmentItem': [
        FieldSpec('property_id', lambda r: r.get('property_id'), min_coverage=0.98),
        FieldSpec('name', lambda r: r.get('name'), min_coverage=0.98),
        FieldSpec('price_amount', lambda r: r.get('price_amount'), min_coverage=0.8, numeric_range=(5, 1000000)),
        # coordinates and review data are KNOWN sparse/absent for this kind -- recovered later via
        # place-matching against Google Places, not expected here.
    ],
}


REQUIRED_KINDS = {'LxAccSearchResultSnippetProperty'}   # the only kind with pin, price and score together


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('batch', help='JSON file written by extract_booking_landmark.py (the {"candidates": {...}} object)')
    a = ap.parse_args()
    data = json.loads(Path(a.batch).read_text(encoding='utf-8'))
    records = list(data['candidates'].values()) if 'candidates' in data else list(data.values())

    # Fail-closed: an empty batch, or a record whose kind this check does not know, is not a pass. Booking.com
    # may have added a placement type; that must be inspected and given a KIND_SPEC before the batch is used.
    if not records:
        print(f'FAIL: {a.batch} holds zero records. An empty extraction is an extraction failure, not a clean batch.')
        return 1

    fetched = data.get('fetched', []) if isinstance(data, dict) else []
    bad_pages = [f for f in fetched if 'error' in f or not f.get('n_raw')]
    if bad_pages:
        for f in bad_pages:
            print(f"FAIL: {f.get('url')}: {f.get('error') or 'no accommodations extracted'}")
        print('A landmark page that failed or came back empty makes the sweep incomplete.')
        return 1

    present = {r.get('source_list_kind') for r in records} | {k for r in records for k in r.get('source_list_kinds') or []}
    gone = sorted(REQUIRED_KINDS - present)
    if gone:
        print(f'FAIL: no {gone} records in the batch. That kind carries the pin, price and score together; its absence means the '
              f'page\'s shape has moved or the fetch was cut short.')
        return 1

    by_kind = {}
    for r in records:
        by_kind.setdefault(r.get('source_list_kind', 'unknown'), []).append(r)

    overall_ok = True
    for kind, kind_records in by_kind.items():
        specs = KIND_SPECS.get(kind)
        if specs is None:
            print(f'[FAIL] Unrecognized source_list_kind "{kind}" ({len(kind_records)} records). Booking.com has likely '
                  f'added a placement type since this was last verified. Inspect a sample record by hand, then add a KIND_SPEC.')
            overall_ok = False
            continue
        ok, results = check(kind_records, specs)
        print(report(ok, results, f'Booking.com landmark-page results, kind={kind} ({a.batch})'))
        overall_ok = overall_ok and ok

    if not overall_ok:
        print('\nDo not hand this batch to place-matching or review-synthesis. Re-verify the "accommodations" '
              'JSON shape against a live landmark page (see extract_booking_landmark.py\'s own header) and '
              're-extract before continuing.')
    return 0 if overall_ok else 1


def selftest() -> int:
    import tempfile
    rec = lambda kind, **k: dict({'property_id': k.pop('pid', 1), 'name': 'n', 'source_list_kind': kind}, **k)
    snip = [rec('LxAccSearchResultSnippetProperty', pid=i, ll=[1, 2], review_score_10=8.5, review_count=100, price_amount=5000) for i in range(10)]
    cases = []
    with tempfile.TemporaryDirectory() as td:
        def run(data):
            p = Path(td) / 'b.json'; p.write_text(json.dumps(data), encoding='utf-8')
            sys.argv = ['x', str(p)]
            import io, contextlib
            with contextlib.redirect_stdout(io.StringIO()):
                return main()
        cases.append(('a healthy batch passes', run({'fetched': [{'url': 'u', 'n_raw': 10}], 'candidates': {str(i): r for i, r in enumerate(snip)}}) == 0))
        cases.append(('an empty batch fails', run({'candidates': {}}) == 1))
        cases.append(('an unknown kind fails', run({'candidates': {'1': rec('SomethingNew')}}) == 1))
        sparse = {str(i): rec('LxAccTargetedHotelSegmentItem', pid=i, price_amount=5000) for i in range(10)}
        cases.append(('a batch with no snippet-property records fails', run({'candidates': sparse}) == 1))
        cases.append(('a failed landmark page fails the batch', run({'fetched': [{'url': 'u', 'error': 'x'}], 'candidates': {str(i): r for i, r in enumerate(snip)}}) == 1))
    for name, ok in cases:
        print(('PASS ' if ok else 'FAIL ') + name)
    ok = all(x for _, x in cases)
    print(f"SELFTEST {'PASS' if ok else 'FAIL'} ({len(cases)} cases)")
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(selftest() if sys.argv[1:] == ['selftest'] else main())
