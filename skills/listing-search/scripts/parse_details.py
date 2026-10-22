#!/usr/bin/env python
"""Parse saved Airbnb listing-page text into the fields merge.py reads.

  python parse_details.py --raw detail_raw.json --out detail_parsed.json
  python parse_details.py selftest

detail_raw.json    {listing id: the page's visible text}   (may be a JSON-encoded string, as the capture tool writes it)
detail_parsed.json {listing id: {id, max_guests, guests_plus, bedrooms, beds, baths, kind, title, host, superhost, hosting,
                   desc, sleep, amen_top, amen_n, rating, n_reviews, guest_fav, cats, star_dist, mentions, reviews [{who,
                   stars, when, text}], loc_area, rules, cancel, price_total, price_currency, pet_sentences,
                   pool_sentences, full_len, flags}}
A field the page doesn't show is absent or empty, never guessed. The patterns match the page's English text.
"""
import argparse, json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'tools'))   # <plugin_root>/tools/jsonfile.py
from jsonfile import load_json  # noqa: E402
import numfmt  # noqa: E402

def _plain(v):
    return None if v is None else str(int(v)) if v == int(v) else str(v)


PRICE = re.compile(r"(US\$|CA\$|AU\$|MX\$|HK\$|NZ\$|(?<![A-Za-z])[ARCS]\$|Rs\.?|[€$£₹฿₩]|[A-Z]{3})\s?(\d[\d.,  ’' ]*\d|\d) for (\d+) nights?")
SEARCH_SUMMARY = re.compile(r'\d+ guests?, \d+ pets?')   # a search-page summary line, not a pet rule
FLAG_WORDS = ['not allowed', 'no pets', 'extra mattress', 'mattress', 'party', 'event', 'loud', 'music', 'shared', 'other guests',
              'common', 'caretaker', 'no alcohol', 'drugs']


import sys as _sys
if _sys.stdout.encoding and _sys.stdout.encoding.lower() != 'utf-8':   # names in other scripts must print on a Windows pipe too
    _sys.stdout.reconfigure(encoding='utf-8')


def between(t, a, b):
    i = t.find(a)
    if i < 0:
        return ''
    j = t.find(b, i + len(a))
    return t[i + len(a): j if j > 0 else i + 3000].strip()


def parse_page(id_, t):
    r = {'id': id_}
    # "4 guests · 2 bedrooms · 3 beds · 1 private bath", parts in any combination. A title such as "Lakeside villa for 12 guests"
    # has "N guests" too, so take the first such line whose parts actually read as guests, bedrooms, beds or baths.
    head = next((m for m in re.finditer(r'^[^\n]*\d\+? guests?[^\n]*$', t, re.M)
                 if any(re.fullmatch(r'(\d+)(\+?) guests?', x.strip()) for x in m.group(0).split('·'))), None)
    if head:
        for part in (x.strip() for x in head.group(0).split('·')):
            g = re.fullmatch(r'(\d+)(\+?) guests?', part)
            if g: r['max_guests'] = float(g.group(1)); r['guests_plus'] = bool(g.group(2))
            br = re.fullmatch(r'(\d+) bedrooms?', part)
            if br: r['bedrooms'] = float(br.group(1))
            elif part.lower() == 'studio': r['bedrooms'] = 0.0
            bd = re.fullmatch(r'(\d+) beds?', part)
            if bd: r['beds'] = float(bd.group(1))
            ba = re.fullmatch(r'(\d+(?:\.\d+)?)\s*(?:private |shared |dedicated )?baths?', part, re.I)
            if ba: r['baths'] = float(ba.group(1))
            elif re.fullmatch(r'(?:private |shared )?half[- ]bath', part, re.I): r['baths'] = 0.5
        r.setdefault('beds', None)
    r['kind'] = (re.search(r'^\n?([^\n]* in [^\n]*)\n', t) or [None, ''])[1]
    r['title'] = t.split('\n')[0].strip()
    r['host'] = (re.search(r'Hosted by ([^\n]+)', t) or [None, ''])[1]
    r['superhost'] = 'Superhost' in t[:1500]
    m = re.search(r'(\d+) years? hosting|(\d+) months? hosting', t)
    r['hosting'] = m.group(0) if m else ''
    r['desc'] = between(t, 'Listing highlights', 'What this place offers')[:2500] or between(t, 'hosting\n', 'What this place offers')[:2500]
    sl = re.search(r"Where you[’']ll sleep\n(.*?)\nWhat this place offers", t, re.S)
    r['sleep'] = sl.group(1).replace('\n', ' | ') if sl else ''
    am = re.search(r"What this place offers\n(.*?)\nShow all (\d+) amenities", t, re.S)
    r['amen_top'] = am.group(1).replace('\n', ' | ') if am else ''
    r['amen_n'] = int(am.group(2)) if am else None
    rt = re.search(r'Rated ([\d.]+) out of 5 from ([\d,]+) reviews?', t)
    if rt:
        r['rating'], r['n_reviews'] = float(rt.group(1)), int(rt.group(2).replace(',', ''))
    else:
        rt = re.search(r'Rated ([\d.]+) out of 5 stars\.\n[\d.]+\n([\d,]+) reviews', t)
        if rt:
            r['rating'], r['n_reviews'] = float(rt.group(1)), int(rt.group(2).replace(',', ''))
    r['guest_fav'] = 'Guest\nfavourite' in t[:600] or 'guest favourite' in t[:800].lower()
    r['cats'] = {c.lower(): float(v) for v, c in re.findall(r'Rated ([\d.]+) out of 5 stars for (\w[\w-]*)', t)}
    dist = re.search(r'5 stars, (\d+)% of reviews.*?4 stars, (\d+)% of reviews.*?3 stars, (\d+)% of reviews.*?2 stars, (\d+)% of reviews.*?1 stars, (\d+)% of reviews', t, re.S)
    r['star_dist'] = list(map(int, dist.groups())) if dist else None
    mn = re.search(r'Guest reviews mention\n(.*?)\n(?:[^\n]+\n[^\n]*(?:years?|months?) on Airbnb|Show all)', t, re.S)
    r['mentions'] = mn.group(1).replace('\n', ' ') if mn else ''
    revs = []
    for rm in re.finditer(r'\n([^\n]+)\n((?:New to Airbnb|\d+ (?:years?|months?) on Airbnb))\nRating, (\d) stars?\n,\n·\n([^\n]+)\n(.*?)(?=\nShow more\n|\nShow all \d+ reviews)', t, re.S):
        revs.append({'who': rm.group(1), 'stars': int(rm.group(3)), 'when': rm.group(4), 'text': rm.group(5).strip().replace('\n', ' ')})
    r['reviews'] = revs
    r['loc_area'] = (re.search(r"Where you[’']ll be\n([^\n]+)", t) or [None, ''])[1]
    hr = re.search(r'House rules\n(.*?)\n(?:Learn more|Safety)', t, re.S)
    r['rules'] = hr.group(1).replace('\n', ' | ') if hr else ''
    r['cancel'] = between(t, 'Cancellation policy\n', '\nReview this host')
    pm = PRICE.search(t)
    r['price_total'], r['price_currency'] = (_plain(numfmt.first_amount(pm.group(2), money=True)), pm.group(1)) if pm else (None, None)
    r['pet_sentences'] = list(dict.fromkeys(s.strip() for s in re.findall(r'[^.\n]*\b(?:pets?|dogs?|puppy|puppies)\b[^.\n]*', t, re.I)
                               if 'Pet-friendly holiday' not in s and not SEARCH_SUMMARY.search(s)))[:6]
    r['pool_sentences'] = list(dict.fromkeys(s.strip() for s in re.findall(r'[^.\n]*\b(?:pool|swimming)\b[^.\n]*', t, re.I) if len(s) < 250))[:5]
    r['full_len'] = len(t)
    r['flags'] = [w for w in FLAG_WORDS if w in t.lower()]
    return r


def parse_all(det):
    return {i: parse_page(i, t) for i, t in det.items()}


def selftest():
    page = ("Farm A\nHosted by Sam\n4 guests · 2 bedrooms · 3 beds · 1 bath\nSuperhost\n"
            "Where you’ll sleep\nBedroom 1 | 1 king bed\nBedroom 2 | 2 single beds\nWhat this place offers\nPool\nWifi\nShow all 24 amenities\n"
            "Rated 4.8 out of 5 from 40 reviews\n€250 for 2 nights\nSearch: 9 guests, 1 pet\nDogs are welcome with prior request.\n")
    r = parse_page('1', page)
    cases = [("guests, bedrooms, beds and baths parse", (r.get('max_guests'), r.get('bedrooms'), r.get('beds'), r.get('baths')) == (4.0, 2.0, 3.0, 1.0)),
             ("rating and review count parse", (r.get('rating'), r.get('n_reviews')) == (4.8, 40)),
             ("the price and its currency parse in any currency", (r.get('price_total'), r.get('price_currency')) == ('250', '€')),
             ("the sleeping arrangement is kept", 'king bed' in r.get('sleep', '')),
             ("a search-summary line is not a pet rule", not any('9 guests, 1 pet' in x for x in r.get('pet_sentences', [])) and r.get('pet_sentences')),
             ("a thousands separator in the review count is read: 1,041 reviews", (lambda q: (q.get('rating'), q.get('n_reviews')))(parse_page('3', 'Rated 4.7 out of 5 from 1,041 reviews')) == (4.7, 1041)
              and parse_page('3', 'Rated 4.9 out of 5 stars.\n4.9\n2,310 reviews').get('n_reviews') == 2310),
             ("a title that says 'for 12 guests' does not hide the real header line", (lambda q: (q.get('max_guests'), q.get('bedrooms'), q.get('beds'), q.get('baths')))(
                 parse_page('4', 'Lakeside villa for 12 guests\nHosted by Sam\n10 guests · 4 bedrooms · 6 beds · 3 baths\n')) == (10.0, 4.0, 6.0, 3.0)),
             ("an empty page yields an id and no invented fields", parse_page('2', '').get('rating') is None and parse_page('2', '')['id'] == '2')]
    for name, ok in cases:
        print(('PASS ' if ok else 'FAIL ') + name)
    ok = all(x for _, x in cases)
    print(f"SELFTEST {'PASS' if ok else 'FAIL'} ({len(cases)} cases)")
    return 0 if ok else 1


def main():
    if sys.argv[1:] == ['selftest']:
        return selftest()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--raw', required=True); ap.add_argument('--out', required=True)
    a = ap.parse_args()
    out = parse_all(load_json(a.raw))
    if not out:
        print('FAIL: the raw file holds no pages; nothing was written')
        return 1
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    print(len(out), 'pages;', sum(1 for v in out.values() if 'max_guests' in v), 'with guests and bedrooms;',
          sum(1 for v in out.values() if v.get('reviews')), 'with reviews;', sum(1 for v in out.values() if v.get('rating') is not None), 'with a rating')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
