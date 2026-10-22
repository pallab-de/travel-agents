#!/usr/bin/env python
"""Merge one search's data into merged.json: search-card candidates (with drive times), parsed listing pages, saved page text
and Google place matches, plus the group's real-bed arithmetic.

  python merge.py --config config.json --booking-url "https://www.airbnb.<country>/rooms/{id}?adults=3&check_in=...&check_out=..." \
      [--cands cands2_drive.json] [--parsed detail_parsed.json] [--raw detail_raw.json] [--places places4.json] \
      [--keep keep_ids.json] [--out merged.json]
  python merge.py selftest

config.json is the project's filled requirements-intake config: it supplies the adults and the real-bed rule
(`real_bed_rule.adults_need_beds`, the fewest real beds the group will accept; the rule itself is the user's, confirmed in
intake). The script refuses to run without them. `--booking-url` is the listing link with `{id}`; the dates and guests in it
belong to this trip.

Inputs, as written by the earlier stages:
  cands2_drive.json  {id: {name, type, ll, total, price, drive_out_min, drive_back_min, km, [landmark_km], [gf], [added_by]}}
  detail_parsed.json {id: parse_details.py output}      detail_raw.json {id: page text}
  places4.json       {id: place_match.py output | null}  keep_ids.json  [ids that passed the hard filters]
"""
import argparse, collections, json, math, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'tools'))   # <plugin_root>/tools/jsonfile.py
from jsonfile import load_json  # noqa: E402
import numfmt  # noqa: E402

MARK = r'(?:US\$|CA\$|AU\$|MX\$|NT\$|HK\$|NZ\$|(?<![A-Za-z])[ARCS]\$|Rs\.?|[€$£₹฿₩])'   # a currency mark, the multi-letter ones first
MONEY = r'(?:' + MARK + r'|[A-Z]{3})'
CURRENCY = r'(?:' + MARK + r'|\b(?:INR|USD|EUR|GBP|AUD|CAD|AED|THB|JPY|SGD|CHF|NZD)\b|\bRs\.?)'   # a mark, a known code or Rs; never any three letters


import sys as _sys
if _sys.stdout.encoding and _sys.stdout.encoding.lower() != 'utf-8':   # names in other scripts must print on a Windows pipe too
    _sys.stdout.reconfigure(encoding='utf-8')


def bed_spots(sleep):
    """-> (real sleeping spots, mattress spots, beds) or (None, None, None) when there is no bedroom breakdown."""
    if not sleep or 'Bedroom' not in sleep:
        return None, None, None
    real = mat = beds = 0
    for n, kind in re.findall(r'(\d+) ((?:small )?(?:king|queen|double|single|sofa|floor|bunk|toddler|cot|crib|air)[a-z ]*?)(?= \||,|$)', sleep.lower()):
        n = int(n)
        if 'floor mattress' in kind or 'air ' in kind or kind.startswith('air'): mat += n
        elif kind.startswith('sofa') and 'bed' in kind: mat += n            # sofa bed: counts once, not a real bed
        elif kind.startswith('sofa'): pass                                    # plain sofa: no
        elif 'cot' in kind or 'toddler' in kind or 'crib' in kind: pass       # kid beds: small children share a parent's bed
        elif 'single' in kind: real += n; beds += n
        elif 'bunk' in kind: real += 2 * n; beds += 2 * n
        else: real += 2 * n; beds += n                                        # king/queen/double hold a couple
    said = len(re.findall(r'\d+ [a-z ]*?(?:beds?|bunk|mattress|cots?|cribs?|sofa|futon|hammock)\b', sleep.lower()))
    read = len(re.findall(r'(\d+) ((?:small )?(?:king|queen|double|single|sofa|floor|bunk|toddler|cot|crib|air)[a-z ]*?)(?= \||,|$)', sleep.lower()))
    if (real == 0 and mat == 0 and beds == 0) or read < said:   # a bed kind it cannot read ("twin", "hammock"...): the breakdown is unknown, never "fewer beds"
        return None, None, None
    return real, mat, beds


def bed_class(adults, min_beds, real, mat, beds_n):
    """The group's rule: at least `min_beds` real beds AND a real sleeping spot for every adult; short of that, mattresses
    make up the difference; otherwise the place is SHORT."""
    if beds_n is None: return 'UNKNOWN'          # the listing shows neither a bedroom breakdown nor a bed count: not "0 beds"
    if beds_n >= min_beds and real >= adults: return 'REAL_BEDS_OK'
    if real + mat >= adults: return 'NEEDS_MATTRESSES'
    return 'SHORT'


def availability(t):
    if re.search(r'Those dates (are|aren.t) not available|dates (are|aren.t) not available', t, re.I): return 'unavailable_dates'
    if re.search(r'not yet available to all guests', t, re.I): return 'not_yet_available'
    if re.search(MONEY + r'\s?[\d,]+ for \d+ nights?', t): return 'available'
    if 'Reserve' in t: return 'available_price_not_shown'
    return 'unknown'


def _says(rx, tl):
    """The pattern appears and is not negated in its own clause ("no club pool", "not near the community pool", "without a shared pool"); a negation
    before a comma, a semicolon, a full stop or 'but' belongs to another clause ("no private pool but a shared pool" still says shared)."""
    for m in re.finditer(rx, tl):
        clause = re.split(r"[.;,\n]|\bbut\b|\bhowever\b|\balthough\b", tl[:m.start()])[-1]
        if not re.search(r"\b(?:no|not|without|neither|nor|away from|instead of)\b", clause):
            return True
    return False


def pool_kind(t):
    tl = t.lower()
    if _says(r'shared pool|common pool|community pool|society pool', tl): return 'shared'
    if _says(r"club(?:.s)? (?:swimming )?pool|access to the (?:club|society|resort).{0,3}s? (?:swimming )?pool", tl): return 'club_only'
    if _says(r'plunge pool', tl): return 'plunge'
    if _says(r'private (swimming )?pool|own (private )?pool|private outdoor pool|private infinity|pvt pool', tl): return 'private'
    if re.search(r'\bpool\b', tl): return 'pool_unspecified'
    return 'none_found'


PET_KEYS = re.compile(r'(per pet|pet fee|pet charge|cleaning charge|on leash|leash|pets? (?:are )?not allowed|no pets|prior request|upon request|max(?:imum)? \d+ pets?|only (?:small|one|1)|resident (?:dog|pet)s?|(?:farm|guard|house) dogs?|stray|dogs? (?:on|in) the (?:property|premises))', re.I)


def sentences(t):
    return [s.strip() for s in re.split(r'(?<=[.!?\n])\s*', t)]


def pet_info(t):
    out = []
    for s in sentences(t):
        if 12 < len(s) < 260 and re.search(r'\b(pet|pets|dog|dogs)\b', s, re.I) and PET_KEYS.search(s) and not re.search(r'\d+ guests?, \d+ pets?', s):
            if s not in out: out.append(s)
    return out[:3]


EXTRA = re.compile(r'(extra (?:guest|head|person|mattress)|per (?:extra )?(?:person|head|guest)|kitchen (?:use|usage) (?:charge|fee)|kitchen (?:charge|fee)|cooking charge|generator|electricity (?:charge|bill)|security deposit|refundable deposit|additional (?:charge|guest))', re.I)


def extras(t):
    out = []
    for s in sentences(t):
        if 10 < len(s) < 240 and EXTRA.search(s) and re.search(CURRENCY + r'|\d{3,}', s, re.I):
            if s not in out: out.append(s)
    return out[:3]


def amount(s):
    """'1,200' -> 1200.0, '1.234,50' -> 1234.5, '1.000' -> 1000.0 (a dot followed by exactly three digits is a thousands mark), '12.50' -> 12.5; None if unreadable."""
    return numfmt.amount(s)


def refund_prices(t):
    nr = re.search(r'Non-refundable · ' + MONEY + r'\s?(\d[\d.,\u00a0\u202f\u2019\' ]*)', t)
    rf = re.search(r'(?<!Non-)[Rr]efundable · ' + MONEY + r'\s?(\d[\d.,\u00a0\u202f\u2019\' ]*)', t)
    f = lambda m: (lambda v: round(v) if v is not None else None)(numfmt.first_amount(m.group(1), money=True)) if m else None   # the first number only: "$210 1 night" is 210
    return f(nr), f(rf)


def _money(s):
    return numfmt.first_amount(s, money=True)


def pet_fee(t):
    """The declared pet cost in the listing's own currency and words (e.g. '300 per pet (max 2 pets)'), or None if not declared.
    A fee declared with no currency is returned as such, with that said, never dropped."""
    cur = per = None
    NUM = r'(\d[\d.,\u00a0\u202f\u2019\' ]*\d|\d)'
    m = (re.search(r'(' + CURRENCY + r')\s?' + NUM + r'\s*(?:/-)?\s*(?:per|/|each)\s*pet(?:\s*(?:per|/)\s*(day|night|stay))?', t, re.I)
         or re.search(r'Fees?\s*:\s*(' + CURRENCY + r')?\s?' + NUM + r'\s*per\s*pet', t, re.I)
         or re.search(r'pet[^.\n]{0,40}(?:fee|charge)[^.\n]{0,30}(' + CURRENCY + r')\s?' + NUM, t, re.I))
    if m:
        cur, amt = m.group(1), _money(m.group(2))
        per = m.group(3) if m.lastindex and m.lastindex >= 3 else None
    else:
        m = re.search(NUM + r'\s*(' + CURRENCY + r')\s*(?:/-)?\s*(?:per|/|each)\s*pet', t, re.I)   # "500 INR per pet"
        if m:
            amt, cur = _money(m.group(1)), m.group(2)
        else:
            m = re.search(r'pet\s*(?:fee|charge)s?\s*:?\s*' + NUM + r'\s*(?:/-)?\s*(?:per|/|each)\s*pet', t, re.I)   # "Pet fee 500 per pet"
            if not m:
                return None
            amt = _money(m.group(1))
    if amt is None:
        return None
    if cur and cur.isalpha() and len(cur) == 3:
        cur = cur.upper()
    fee = f"{cur or ''}{(f'{int(amt):,}' if float(amt).is_integer() else f'{amt:,.2f}')} per pet" + (f" per {per.lower()}" if per else '') + ('' if cur else ' (currency not stated)')
    if re.search(r'cleaning charge[^.\n]{0,25}pet|pet[^.\n]{0,30}cleaning charge', t, re.I): fee += ' (cleaning)'
    mm = re.search(r'max(?:imum)?\s*(\d+)\s*pets?', t, re.I)
    if mm: fee += f' (max {mm.group(1)} pets)'
    return fee


def pet_pool_rule(t):
    """A sentence saying pets may not use the pool, if any."""
    for s in sentences(t):
        if (re.search(r'pets?|dogs?', s, re.I) and re.search(r'pool', s, re.I)
                and re.search(r'not (?:be )?allowed|no |prohibit|not permitted|restrict|strictly|are not', s, re.I)
                and not re.search(r'pool access is allowed|allowed in (?:the )?pool', s, re.I) and len(s) < 240):
            return s
    return None


def cancel_summary(cancel, t):
    """The refund policy in the listing's own words (a fragment copied from the page, so a capture of the page can confirm it), or 'Not shown'."""
    c = re.sub(r'\s+', ' ', cancel or '')
    both = c + ' ' + re.sub(r'\s+', ' ', t or '')
    for rx in (r'Free cancellation before [^.]{1,30}?(?=\.|$| \w+ refund)', r'Cancel (?:before|by) [^.]{1,40}? (?:partial|full) refund',
               r'(?:(?<=\s)|^)(?:[^.]{0,60}?\s)?(?:non-?refundable|not refundable|no refund)(?:\s[^.]{0,40}(?=[\s.]|$))?',
               r'Free cancellation (?:for|within) [^.]{1,30}?(?=\.|,|$)'):
        m = re.search(rx, c, re.I) or re.search(rx, both, re.I)
        if m:
            return m.group(0).strip()
    return c[:80] if c else 'Not shown'


def merge_row(i, cand, parsed, text, place, cfg):
    """One listing's merged record."""
    adults, min_beds, booking_url = cfg['adults'], cfg['min_beds'], cfg['booking_url']
    cluster = bool(place and place.get('cluster'))
    real, mat, beds_n = bed_spots(parsed['sleep'])
    header_beds = parsed.get('beds')
    estimated = real is None
    if estimated and header_beds is None:   # neither a breakdown nor a bed count: unknown, never "0 beds"
        beds_n, real_e, mat_e = None, None, None
        spots_note = "no bedroom breakdown or bed count on the listing"
    elif estimated:   # no bedroom breakdown: assume each bed sleeps 2 (flagged as an estimate everywhere it is shown)
        beds_n = int(header_beds)
        real_e, mat_e = beds_n * 2, 0
        spots_note = f"assumed 2 per bed x {beds_n} beds (no bedroom breakdown on listing)"
    else:
        real_e, mat_e, spots_note = real, mat, 'from listing'
    nr, rf = refund_prices(text)
    few = re.search(r'(\d+) reviews?\s*\n(?:\d+ reviews?\s*\n)?Average rating will appear', text)
    n_few = int(few.group(1)) if few else 0      # 1-2 reviews: the platform shows no average yet
    typ = cand.get('type') or ''                  # the search result can omit its type label
    return {
        'id': i, 'url': booking_url.format(id=i),
        'name': cand['name'], 'type': typ or None, 'area': typ.split(' in ', 1)[-1] if ' in ' in typ else None,
        'll': cand['ll'], 'll_approx': cand.get('ll_approx', False), 'total': cand['total'], 'price_shown': cand['price'],
        'nonrefundable_total': nr, 'refundable_total': rf, 'beds_estimated': estimated,
        'pet_fee': pet_fee(text), 'pet_pool_rule': pet_pool_rule(text), 'refund_policy': cancel_summary(parsed['cancel'], text),
        'availability': availability(text), 'pool_kind': pool_kind(text), 'pet_info': pet_info(text), 'extras': extras(text),
        'drive_out_min': cand.get('drive_out_min'), 'drive_back_min': cand.get('drive_back_min'), 'drive_back_status': cand.get('drive_back_status'), 'km': cand.get('km'), 'drive_status': cand.get('drive_status'),
        'a_rating': parsed.get('rating'), 'a_n': parsed.get('n_reviews') or n_few, 'guest_fav': cand.get('gf'), 'superhost': parsed['superhost'],
        'beds_n': beds_n, 'bedrooms': parsed.get('bedrooms'), 'beds_hdr': header_beds, 'max_guests': parsed.get('max_guests'), 'guests_plus': parsed.get('guests_plus'),
        'sleep': parsed['sleep'], 'real_spots': real_e, 'mat_spots': mat_e, 'bed_class': bed_class(adults, min_beds, real_e, mat_e, beds_n), 'spots_note': spots_note,
        'cats': parsed['cats'], 'star_dist': parsed['star_dist'], 'mentions': parsed['mentions'], 'reviews': parsed['reviews'],
        'amen_top': parsed['amen_top'], 'desc': parsed['desc'], 'rules': parsed['rules'], 'cancel': parsed['cancel'], 'host': parsed['host'], 'hosting': parsed['hosting'],
        'flags': parsed['flags'], 'pool_sentences': parsed['pool_sentences'],
        'g': ({'name': place['g_name'], 'rating': place['g_rating'], 'n': place['g_n'], 'place_id': place['g_place_id'], 'dist_km': place['g_dist_km'], 'cluster': cluster,
               'renamed': bool(place.get('renamed')), 'evidence': place.get('evidence')} if place else None),
        'landmark_km': round(cand['landmark_km'], 1) if cand.get('landmark_km') is not None else None, 'added_by': cand.get('added_by', 'filtered search'),
    }


def settings(config, booking_url):
    """The group's numbers, from the filled config; refuses to guess any of them."""
    adults = (config.get('group') or {}).get('adults')
    rule = config.get('real_bed_rule') or {}
    min_beds = rule.get('adults_need_beds')
    if not isinstance(adults, int) or isinstance(adults, bool) or adults < 1:
        raise SystemExit('merge.py: config.group.adults must be a whole number (run requirements-intake and validate_config.py first)')
    if not isinstance(min_beds, int) or isinstance(min_beds, bool) or min_beds < 1:
        raise SystemExit('merge.py: config.real_bed_rule.adults_need_beds must be a whole number: the user\'s own real-bed rule')
    if '{id}' not in booking_url or '<' in booking_url:
        raise SystemExit('merge.py: --booking-url needs {id} and this trip\'s real dates and guests (no <placeholders>)')
    return {'adults': adults, 'min_beds': min_beds, 'booking_url': booking_url}


def merge(cands, parsed, raw, places, keep, cfg):
    return {i: merge_row(i, cands[i], parsed[i], raw.get(i, ''), places.get(i), cfg) for i in keep}


def selftest():
    cfg = {'adults': 4, 'min_beds': 2, 'booking_url': 'https://x.test/rooms/{id}'}
    cases = []
    cases.append(("two doubles sleep four adults on real beds", bed_class(4, 2, *bed_spots('Bedroom 1 | 1 queen bed | Bedroom 2 | 1 double bed')[:2], 2) == 'REAL_BEDS_OK'))
    real, mat, beds = bed_spots('Bedroom 1 | 1 queen bed | Living room | 1 sofa bed')
    cases.append(("a sofa bed is a mattress, not a real bed", (real, mat) == (2, 1) and bed_class(4, 2, real, mat, beds) == 'SHORT'
                  and bed_class(3, 1, real, mat, beds) == 'NEEDS_MATTRESSES'))
    cases.append(("a stricter rule changes the class", bed_class(4, 3, 4, 0, 2) == 'NEEDS_MATTRESSES' and bed_class(4, 2, 4, 0, 2) == 'REAL_BEDS_OK'))
    cases.append(("availability and pool kinds read the page", availability('€300 for 2 nights') == 'available' and pool_kind('A private pool') == 'private'))
    cases.append(("'Pet fee 500 per pet' is not read as the currency 'fee'; its missing currency is said", pet_fee("Pet fee 500 per pet") == "500 per pet (currency not stated)" and pet_fee("INR 1,000 per pet") == "INR1,000 per pet"))
    cases.append(("a fee written amount-first keeps its currency", pet_fee("Pets welcome, 500 INR per pet") == "INR500 per pet"))
    cases.append(("no bedroom breakdown and no bed count is UNKNOWN, not 0 beds; a missing type label does not crash", (lambda r: (r['bed_class'], r['type'], r['area']))(
        merge_row('9', {'name': 'X', 'type': None, 'll': [1, 2], 'total': 100, 'price': '100'}, {'sleep': '', 'superhost': False, 'cancel': '', 'cats': {}, 'star_dist': None, 'mentions': '',
                  'reviews': [], 'amen_top': '', 'desc': '', 'rules': '', 'host': '', 'hosting': '', 'flags': [], 'pool_sentences': []}, '', None, cfg)) == ('UNKNOWN', None, None)))
    cases.append(("a pet fee keeps its own currency", pet_fee('Fee: £20 per pet per night') == '£20 per pet per night' and pet_fee('no pet fee stated') is None))
    cases.append(("amounts read European and spaced separators; a bad fee never crashes; a negated pool is not the pool",
                  amount('1.234,50') == 1234.5 and amount('1,200') == 1200 and amount('1.000') == 1000 and amount('12.50') == 12.5
                  and pet_fee('Pets: INR , per pet') is None and pet_fee('Pet fee 1.000 per pet') == '1,000 per pet (currency not stated)'
                  and refund_prices('Non-refundable · \u20b9 1,200 and Refundable · \u20b9 1,500') == (1200, 1500)
                  and pool_kind('private pool, no club pool') == 'private' and pool_kind('private pool, not near the community pool') == 'private'
                  and bed_spots('Bedroom 1 | 1 twin bed') == (None, None, None)))
    cases.append(("the refund label is a fragment of the page's own words", cancel_summary('Free cancellation before 10 Dec. Partial refund after that.', '') == 'Free cancellation before 10 Dec'
                  and cancel_summary('', 'Free cancellation for 48 hours after booking.') == 'Free cancellation for 48 hours after booking' and cancel_summary('Non-refundable', '') == 'Non-refundable'
                  and cancel_summary('', '') == 'Not shown'))
    cases.append(("a refund price reads in any currency", refund_prices('Non-refundable · $210 and Refundable · $260') == (210, 260)))
    cand = {'name': 'Farm A', 'type': 'Farm stay in Valmora', 'll': [1, 2], 'total': 300, 'price': '300', 'drive_out_min': 60, 'drive_back_min': 62, 'km': 40.0, 'landmark_km': 12.34}
    parsed = {'sleep': 'Bedroom 1 | 1 king bed', 'superhost': False, 'cancel': '', 'cats': {}, 'star_dist': None, 'mentions': '', 'reviews': [], 'amen_top': '',
              'desc': '', 'rules': '', 'host': '', 'hosting': '', 'flags': [], 'pool_sentences': [], 'rating': 4.8, 'n_reviews': 12}
    row = merge_row('1', cand, parsed, '€300 for 2 nights. Pool', None, cfg)
    cases.append(("a merged row carries the rule's class, the landmark distance and its link", row['bed_class'] == 'SHORT' and row['landmark_km'] == 12.3
                  and row['url'] == 'https://x.test/rooms/1' and row['total'] == 300 and 'total_inr' not in row))
    for bad, why in (({}, "a config with no adults is refused"), ({'group': {'adults': 4}}, "a config with no real-bed rule is refused")):
        try:
            settings(bad, 'https://x.test/{id}'); cases.append((why, False))
        except SystemExit:
            cases.append((why, True))
    try:
        settings({'group': {'adults': 4}, 'real_bed_rule': {'adults_need_beds': 2}}, 'https://x.test/<placeholder>'); cases.append(("a placeholder booking url is refused", False))
    except SystemExit:
        cases.append(("a placeholder booking url is refused", True))
    for name, ok in cases:
        print(('PASS ' if ok else 'FAIL ') + name)
    ok = all(x for _, x in cases)
    print(f"SELFTEST {'PASS' if ok else 'FAIL'} ({len(cases)} cases)")
    return 0 if ok else 1


def main():
    if sys.argv[1:] == ['selftest']:
        return selftest()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--config', required=True); ap.add_argument('--booking-url', required=True)
    ap.add_argument('--cands', default='cands2_drive.json'); ap.add_argument('--parsed', default='detail_parsed.json')
    ap.add_argument('--raw', default='detail_raw.json'); ap.add_argument('--places', default='places4.json')
    ap.add_argument('--keep', help='optional: a JSON list of the ids to merge. Without it every candidate with a parsed page is merged'); ap.add_argument('--out', default='merged.json')
    a = ap.parse_args()
    cfg = settings(json.loads(Path(a.config).read_text(encoding='utf-8')), a.booking_url)
    cands, parsed = json.loads(Path(a.cands).read_text(encoding='utf-8')), json.loads(Path(a.parsed).read_text(encoding='utf-8'))
    raw, places = load_json(a.raw), json.loads(Path(a.places).read_text(encoding='utf-8'))
    keep = json.loads(Path(a.keep).read_text(encoding='utf-8')) if a.keep else [i for i in cands if i in parsed]
    left_out = [i for i in cands if i not in keep]
    if left_out:
        print(f'NOTE {len(left_out)} of {len(cands)} candidate(s) are not merged ({"not in --keep" if a.keep else "no parsed page"}): {left_out[:5]}')
    missing = [i for i in keep if i not in cands or i not in parsed]
    if missing:
        print(f'FAIL: {len(missing)} kept id(s) have no candidate or parsed page ({missing[:5]}); nothing was written')
        return 1
    rows = merge(cands, parsed, raw, places, keep, cfg)
    Path(a.out).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding='utf-8')
    print(len(rows), collections.Counter(r['bed_class'] for r in rows.values()), collections.Counter(r['availability'] for r in rows.values()),
          collections.Counter(r['pool_kind'] for r in rows.values()), 'google', sum(1 for r in rows.values() if r['g']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
