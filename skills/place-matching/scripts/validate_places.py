#!/usr/bin/env python
"""Check that matched Google places agree with ground-truth feature IDs supplied by the user.

Ground truth is never stored in this repo. Supply it per project as a JSON file:
  {"<listing id>": {"name": "<place name>", "feature_id": "0x...:0x..."}, ...}

Usage (run from the trip folder):
  python "<plugin root>/skills/place-matching/scripts/validate_places.py" --truth truth.json --matches places.json

Exit status is 1 when any listing's matched place does not carry the expected feature ID.
"""
import argparse, base64, json, struct, sys


import sys as _sys
if _sys.stdout.encoding and _sys.stdout.encoding.lower() != 'utf-8':   # names in other scripts must print on a Windows pipe too
    _sys.stdout.reconfigure(encoding='utf-8')


def cid(place_id):
    """ChIJ... place id -> '0x<hex>:0x<hex>' (Google feature id)."""
    raw = base64.urlsafe_b64decode(place_id + '=' * (-len(place_id) % 4))
    a = struct.unpack('<Q', raw[3:11])[0]
    b = struct.unpack('<Q', raw[12:20])[0]
    return f'0x{a:x}:0x{b:x}'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--truth', required=True, help='JSON: listing id -> {name, feature_id}')
    ap.add_argument('--matches', required=True, help='JSON: listing id -> matched place (g_place_id, g_name, g_rating, g_n)')
    a = ap.parse_args()
    truth = json.load(open(a.truth, encoding='utf-8'))
    matches = json.load(open(a.matches, encoding='utf-8'))
    if not truth:
        print('FAIL: no ground truth supplied; nothing was checked')
        return 1
    ok = True
    for listing_id, t in truth.items():
        m = matches.get(listing_id)
        try:
            got = cid(m['g_place_id']) if m else None
        except (struct.error, ValueError, KeyError, TypeError):
            got = 'undecodable place id'
        passed = got == t['feature_id']
        print(('PASS' if passed else 'FAIL'), t.get('name', listing_id), '->',
              (m['g_name'], m.get('g_rating'), m.get('g_n')) if m else None, got)
        ok &= passed
    print('ALL PASS' if ok else 'SOME FAIL')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
