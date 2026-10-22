"""Geography-independent Google Maps matcher for an Airbnb listing (name + approximate coordinates). No manual overrides, no local place lists.

How it decides what is generic (instead of hard-coded lists):
  1. UNIVERSAL: a small multilingual list of lodging words (villa, casa, ferienhaus, apartment ...) and bed-count tokens (3bhk, 2br).
  2. LOCALE: tokens in many candidates' addresses (city, district, country) are learned per search, and a word that is in a candidate's address
     but NOT in its own name is treated as a place name for that comparison (titles often end with the town).
  3. COMMON: a word shared by several DIFFERENT candidate names in the same search gets a lower weight; a word seen in at most one name is fully distinctive.
A candidate is accepted only when a distinctive token is shared and its type is lodging-like; otherwise the result is None (the report then shows a
name-search link instead of a wrong pin). Every accepted match carries its evidence for audit. Key comes from tools/travel_secrets.py, never printed.
"""
import json, re, sys, math, difflib, unicodedata
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'tools'))   # <plugin_root>/tools, shared across skills and agents
from travel_secrets import load_key  # noqa: E402
import google_maps as gm  # noqa: E402  -- shared Google Maps HTTP client (also used by listing-search's drive_times.py)
N = Path.cwd()   # cache lives in the CALLING project's folder, not this shared scripts folder
KEY = None

UNIVERSAL = set("""villa villas house houses home homes stay stays farm farms farmhouse farmhouses farmstay farmstays homestay cottage cottages cabin cabins cabana resort resorts
hotel hotels retreat retreats lodge lodges apartment apartments apt flat flats studio studios suite suites room rooms bnb guesthouse guest bungalow bungalows chalet chalets loft
penthouse condo condos townhouse holiday vacation rental rentals private pool swimming luxury luxurious luxe beautiful cozy cosy modern spacious entire whole place bhk br bedroom bedrooms
bed beds and the a an of in at by with for to near from on or de la le el los las du des di del da do das dos casa casas maison maisons ferienhaus ferienwohnung wohnung haus
appartamento apartamento appartement chambre zimmer alojamiento piso quinta finca pension superhost new best great lovely charming stunning perfect amazing resorts nature""".split())
BEDTOK = re.compile(r'^\d+(bhk|br|bd|bed|beds|bedroom|bedrooms|pax|p|guests?)$')
NONLODGING = {'store', 'home_goods_store', 'hardware_store', 'real_estate_agency', 'general_contractor', 'school', 'bank', 'doctor', 'hospital',
              'finance', 'insurance_agency', 'lawyer', 'furniture_store', 'clothing_store', 'electronics_store', 'car_repair', 'gas_station', 'restaurant', 'cafe', 'bar'}
SEPS = ('|', ' - ', ' \u2013 ', ',', ' by ', ' @ ', ':', ' near ', ' in ', ' at ', ' with ', '/', '(')
TAILSEPS = (' at ', ' @ ', ' in ')

from textnorm import fold  # noqa: E402  (shared with the hotel dossier; keeps non-Latin names apart)

import sys as _sys
if _sys.stdout.encoding and _sys.stdout.encoding.lower() != 'utf-8':   # names in other scripts must print on a Windows pipe too
    _sys.stdout.reconfigure(encoding='utf-8')


def toks(s):
    return [t for t in fold(s).split() if t]

def variants(name):
    """The full title, the head before a separator, the tail after 'at/in/@' (titles like 'Brookline Homes at Valmora' name the business after 'at'),
    and a punctuation-stripped join of every segment (helps the Places Text Search API when separators like '|' return nothing)."""
    v = [name]
    for sep in SEPS:
        if sep in name:
            head = name.split(sep)[0].strip()
            if len(head) >= 4 and head not in v: v.append(head)
    for sep in TAILSEPS:
        if sep in name:
            tail = re.split(r'[|,:(]| - ', name.split(sep, 1)[1])[0].strip()
            if len(tail) >= 4 and tail not in v: v.append(tail)
    joined = re.sub(r'[|:/]', ' ', name); joined = re.sub(r'\s+', ' ', joined).strip()
    if joined not in v: v.append(joined)
    segs = [seg.strip() for seg in re.split(r'\||\s-\s|:', name) if len(seg.strip()) >= 3]
    if len(segs) >= 2:
        tail = re.sub(r'^(by|@|in|near|at)\s+', '', segs[-1], flags=re.I)
        ht = segs[0] + ' ' + tail                      # head + tail, skipping over-specific middle segments and a leading 'by/@/in' (helps Google's text search)
        if ht not in v: v.append(ht)
    return v[:6]

def hv(a, b):
    p = math.pi / 180
    x = math.sin((b[0] - a[0]) * p / 2) ** 2 + math.cos(a[0] * p) * math.cos(b[0] * p) * math.sin((b[1] - a[1]) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(x))

CACHE_F = N / 'gp_cache.json'
CACHE = {}          # loaded on first use, not at import: an unreadable cache file must not break `import place_match`
_LOADED = [False]


def _load_cache():
    if _LOADED[0]:
        return
    _LOADED[0] = True
    try:
        with open(CACHE_F, encoding='utf-8') as f:
            CACHE.update(json.load(f))
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        print(f'{CACHE_F.name} could not be read; starting with an empty cache')
CALLS = {'api': 0, 'cached': 0}

CACHE_MAX_AGE_DAYS = 90   # the same window the gate gives a guest rating: a cached rating older than this is fetched again

def _fresh(entry, now=None):
    import datetime as _dt
    try:
        t = _dt.datetime.fromisoformat(str(entry['fetched_at']).replace('Z', '+00:00'))
    except (TypeError, KeyError, ValueError):
        return False   # an entry with no timestamp (an older cache file) can't be trusted as current
    return ((now or _dt.datetime.now(_dt.timezone.utc)) - t) <= _dt.timedelta(days=CACHE_MAX_AGE_DAYS)

def search(q, lat, lng, radius=3000):
    _load_cache()
    global KEY
    import datetime as _dt
    ck = f'{q}|{lat:.3f}|{lng:.3f}'
    if ck in CACHE and _fresh(CACHE[ck]): CALLS['cached'] += 1; return CACHE[ck]['results']
    if KEY is None: KEY = load_key("GOOGLE_MAPS_API_KEY")
    r = gm.text_search(q, KEY, lat, lng, radius)
    if r.get("status") not in ("OK", "ZERO_RESULTS"): raise RuntimeError("Places status " + str(r.get("status")))
    CALLS['api'] += 1
    res = [{k: p.get(k) for k in ('place_id', 'name', 'rating', 'user_ratings_total', 'formatted_address', 'geometry', 'types')} for p in r.get('results', [])]
    CACHE[ck] = {'fetched_at': _dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'results': res}
    return res

def save_cache():
    _load_cache()
    tmp = CACHE_F.with_name(CACHE_F.name + '.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(CACHE, f)
    tmp.replace(CACHE_F)   # a half-written file never replaces the good one

def learn(pool):
    """Per-search vocabulary: locale tokens (in many addresses) and name-token document frequencies."""
    M = max(len(pool), 1); addr_df = {}; name_df = {}
    for p in pool:
        for t in set(toks(p.get('formatted_address', ''))): addr_df[t] = addr_df.get(t, 0) + 1
        for t in set(toks(p.get('name', ''))): name_df[t] = name_df.get(t, 0) + 1
    locale = {t for t, c in addr_df.items() if c >= max(3, 0.25 * M) and not t.isdigit()}
    return locale, name_df, M

def weight(t, locale, name_df):
    """0 for universal/locale words; otherwise 1 for a word seen in at most one candidate name, shrinking as more different names share it."""
    if t in UNIVERSAL or BEDTOK.match(t) or t in locale or len(t) < 3 or t.isdigit(): return 0.0
    return 1.0 / (1 + max(0, name_df.get(t, 0) - 1))

def tokmatch(t, u):
    if t == u: return 1.0
    if len(t) >= 4 and len(u) >= 4:
        if t.startswith(u) or u.startswith(t): return 0.9
        if len(t) == len(u) and sorted(t) == sorted(u): return 0.85   # letter-swap spelling (abode/adobe, farmstay/framstay)
        r = difflib.SequenceMatcher(None, t, u).ratio()
        if r >= (0.8 if min(len(t), len(u)) >= 5 else 0.85): return r
    return 0.0

def _sim(listing_variant, cand_variant, full_cand_name, vocab, cand_addr):
    locale, name_df, _ = vocab
    locale = locale | (set(toks(cand_addr)) - set(toks(full_cand_name)))     # in the candidate's address but not its own name: a place name
    fa, fb = fold(listing_variant), fold(cand_variant)
    if fa and fa == fb: return 1.0, ['exact name']   # two empty names are not the same name
    A = toks(listing_variant); B = toks(cand_variant)
    wA = {t: weight(t, locale, name_df) for t in A}; wB = {t: weight(t, locale, name_df) for t in B}
    sA, sB = sum(wA.values()), sum(wB.values())
    matched = []
    for t, wt in wB.items():
        if wt <= 0: continue
        m = max(((tokmatch(t, u) * min(wt, wA[u]) if wA[u] > 0 else 0.0, u) for u in A), default=(0.0, ''))
        if m[0] > 0: matched.append((t, m[1], m[0]))
    mw = sum(x[2] for x in matched)
    ratio = difflib.SequenceMatcher(None, fold(listing_variant), fold(cand_variant)).ratio()
    if sA > 0 and sB > 0 and mw >= 0.25:
        coverB, coverA = mw / sB, mw / sA
        s = 0.5 * coverA + 0.5 * coverB
        if coverB >= 0.999 and len([t for t in wB if wB[t] > 0]) >= 2: s = max(s, 0.85)   # every distinctive word of a 2+ word business name is in the listing title
        if coverB >= 0.999 and coverA >= 0.999: s = max(s, 0.95)
        if ratio > 0.7: s = max(s, 0.6 * s + 0.4 * ratio)
    else:
        s = ratio * 0.7 if (sA == 0 and sB == 0) else min(ratio, 0.6)
    return s, [f'{a}~{b}' for a, b, _ in matched] or [f'string ratio {ratio:.2f}']

def sim_score(listing_name, cand_name, vocab, cand_addr=''):
    best = (0.0, [])
    for lv in variants(listing_name):
        for cv in variants(cand_name)[:3]:
            r = _sim(lv, cv, cand_name, vocab, cand_addr)
            if r[0] > best[0]: best = r
    return best

ANCHOR_KM = 0.15   # only sources with near-exact coordinates (a direct-booking site's own pin) can reach this; Airbnb's jittered pins never will

def _anchor_match(name, pool, vocab):
    """Fallback for a RENAMED / re-branded property ("Valmora Springs" on the booking site, "Valmora Exotics" on Google): the ordinary
    name score is only ~0.5 because half the words differ, so the matcher used to abstain on a place sitting 30 m from the pin. Accepted only
    when ALL hold: exactly one Google lodging within ANCHOR_KM shares a fully distinctive (>=5 letters, unique in this search, not generic/locale)
    word with the listing name. Two same-brand lodgings that close -> ambiguous -> None. The result is flagged `renamed` so the report says the
    Google name differs, instead of presenting it as an exact match."""
    locale, name_df, _ = vocab
    A = {t for t in toks(name) if len(t) >= 5 and weight(t, locale, name_df) >= 1.0}
    if not A: return None
    hits = []
    for p, d in pool.values():
        types = set(p.get('types') or [])
        if d > ANCHOR_KM or 'lodging' not in types or types & NONLODGING: continue
        loc = locale | (set(toks(p.get('formatted_address', ''))) - set(toks(p.get('name', ''))))
        B = {t for t in toks(p.get('name', '')) if len(t) >= 5 and weight(t, loc, name_df) >= 1.0}
        shared = sorted({a for a in A for b in B if tokmatch(a, b) >= 0.9})
        if shared: hits.append((p, d, shared))
    if not hits:
        # Second tier, only when exactly ONE lodging is that close at all: the two names are the same word spelled differently
        # ("Oru"/"Ouru", "Selmera"/"Kselmera", "Harrow"/"Harrowe"). Needs the distinctive words as a whole to be near-identical strings.
        close = [(p, d) for p, d in pool.values() if d <= ANCHOR_KM and 'lodging' in (p.get('types') or []) and not set(p.get('types') or []) & NONLODGING]
        mine = ' '.join(t for t in toks(name) if t not in UNIVERSAL and not BEDTOK.match(t))
        if len(close) == 1 and len(mine) >= 3:
            p, d = close[0]
            theirs = ' '.join(t for t in toks(p.get('name', '')) if t not in UNIVERSAL and not BEDTOK.match(t))
            r = difflib.SequenceMatcher(None, mine, theirs).ratio()
            if len(theirs) >= 3 and r >= (0.8 if min(len(mine), len(theirs)) < 5 else 0.75):
                hits.append((p, d, [f'spelling {mine!r}~{theirs!r} ({r:.2f})']))
    if len(hits) != 1: return None
    p, d, shared = hits[0]
    s = sim_score(name, p.get('name', ''), vocab, p.get('formatted_address', ''))[0]
    return {'g_name': p['name'], 'g_rating': p.get('rating'), 'g_n': p.get('user_ratings_total'), 'g_place_id': p['place_id'], 'g_addr': p.get('formatted_address'),
            'g_dist_km': round(d, 2), 'sim': round(s, 2), 'cluster': False, 'renamed': True,
            'evidence': [f"{'/'.join(shared)} - shared distinctive word/spelling; only lodging within {d:.2f} km; names differ"], 'n_candidates': len(pool)}

def match(name, ll, threshold=0.75, max_km=3.0):
    """-> dict with g_name, g_rating, g_n, g_place_id, g_addr, g_dist_km, sim, cluster, evidence, n_candidates; or None. Uses only the name and coordinates.
    A dict with renamed=True means the Google name differs from `name` (see _anchor_match) - surface that in the report.
    max_km: how far from `ll` a Google place may sit. 3.0 suits Airbnb (it jitters pins up to ~1-2 km on purpose); a source whose pin is exact
    (a direct-booking site's own coordinates) should pass ~0.5 - at 2+ km a generic one-word name is a different business, not a jittered pin."""
    lat, lng = ll; pool = {}
    for k, q in enumerate(variants(name)):
        for p in search(q, lat, lng):
            g = (p.get('geometry') or {}).get('location') or {}
            d = hv((lat, lng), (g.get('lat', 0), g.get('lng', 0)))
            if d <= max_km: pool[p['place_id']] = (p, d)
        if k == 0 and pool:
            vocab = learn([p for p, _ in pool.values()])
            if any(sim_score(name, p['name'], vocab, p.get('formatted_address', ''))[0] >= 0.9 for p, _ in pool.values()): break
        if k >= 1 and pool:
            vocab = learn([p for p, _ in pool.values()])
            if any(sim_score(name, p['name'], vocab, p.get('formatted_address', ''))[0] >= 0.95 for p, _ in pool.values()): break
    if not pool: return None
    vocab = learn([p for p, _ in pool.values()])
    cands = []
    for p, d in pool.values():
        s, ev = sim_score(name, p.get('name', ''), vocab, p.get('formatted_address', ''))
        types = set(p.get('types') or [])
        bad = types & NONLODGING
        # Require POSITIVE evidence of a lodging-type place, not just absence from the NONLODGING blocklist: a
        # same-named non-lodging place (typed only 'establishment', 'point_of_interest') would otherwise match on an
        # exact name score. This keeps the main path as strict as the anchor fallback it defers to.
        is_lodging = 'lodging' in types
        if s >= threshold and is_lodging and not (bad and s < 1.0): cands.append((s, p, d, ev))
    if not cands: return _anchor_match(name, pool, vocab)
    best = max(c[0] for c in cands); near = [c for c in cands if c[0] >= best - 0.12]
    s, p, d, ev = max(near, key=lambda c: ((c[1].get('user_ratings_total') or 0) > 0, c[0] >= 0.85, c[1].get('user_ratings_total') or 0, -c[2]))
    # A partial-name match ("cluster": the rating might belong to a wider complex) stops being one when the pin is essentially ON the place and it is the
    # only lodging that close: that is the same place under a slightly different name, so its rating is this property's rating. The name difference is
    # still reported (`renamed`). Callers that see the same place_id claimed by two listings must set cluster=True again (see mark_shared_places).
    lodgings_near = [q for q, dq in pool.values() if dq <= ANCHOR_KM and 'lodging' in (q.get('types') or []) and not set(q.get('types') or []) & NONLODGING]
    near_exact = d <= ANCHOR_KM and len(lodgings_near) == 1 and lodgings_near[0]['place_id'] == p['place_id']
    return {'g_name': p['name'], 'g_rating': p.get('rating'), 'g_n': p.get('user_ratings_total'), 'g_place_id': p['place_id'], 'g_addr': p.get('formatted_address'),
            'g_dist_km': round(d, 2), 'sim': round(s, 2), 'cluster': s < 0.85 and not near_exact, 'renamed': (s < 0.85 and near_exact) or None, 'evidence': ev, 'n_candidates': len(pool)}

def mark_shared_places(matches):
    """matches: iterable of match() results (None allowed). One Google place claimed by 2+ different listings is a shared complex/brand page, not any single
    listing's own rating -> cluster=True on every claimant. Call this once after matching a whole batch."""
    ms = [m for m in matches if m]
    count = {}
    for m in ms: count[m['g_place_id']] = count.get(m['g_place_id'], 0) + 1
    for m in ms:
        if count[m['g_place_id']] > 1: m['cluster'] = True; m['shared_with'] = count[m['g_place_id']]; m['renamed'] = None
    return ms


def selftest():
    import datetime as _dt
    global KEY
    _LOADED[0] = True   # the cache under test is set by hand
    cases = []
    real = gm.text_search
    calls = []
    gm.text_search = lambda *a, **k: (calls.append(1), {'status': 'OK', 'results': [{'place_id': 'p', 'name': 'N'}]})[1]
    KEY = 'stub'
    try:
        old = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=120)).strftime('%Y-%m-%dT%H:%M:%SZ')
        CACHE.clear()
        CACHE['q|1.000|2.000'] = {'fetched_at': old, 'results': [{'place_id': 'old'}]}
        got = search('q', 1.0, 2.0)
        cases.append(('a cache entry older than 90 days triggers a fresh call', got[0]['place_id'] == 'p' and len(calls) == 1))
        search('q', 1.0, 2.0)
        cases.append(('the fresh entry is then served from the cache', len(calls) == 1))
        CACHE['r|1.000|2.000'] = [{'place_id': 'legacy'}]
        search('r', 1.0, 2.0)
        cases.append(('a legacy entry with no timestamp is fetched again', len(calls) == 2))
    finally:
        gm.text_search = real
        CACHE.clear()
    cases.append(('non-Latin names keep their letters', bool(fold('\u4eac\u90fd\u306e\u5bbf \u753a\u5bb6')) and fold('\u65c5\u9928\u3055\u304f\u3089') != fold('\u4eac\u90fd\u306e\u5bbf \u753a\u5bb6')))
    cases.append(('two different non-Latin names are not an exact match', _sim('\u4eac\u90fd\u306e\u5bbf \u753a\u5bb6', '\u65c5\u9928\u3055\u304f\u3089', '\u65c5\u9928\u3055\u304f\u3089', (set(), {}, None), '')[0] < 1.0))
    cases.append(('accents fold on Latin letters only', fold('H\u00f4tel \u00c9lys\u00e9e') == 'hotel elysee'))
    for name, ok in cases:
        print(('PASS ' if ok else 'FAIL ') + name)
    ok = all(x for _, x in cases)
    print(f"SELFTEST {'PASS' if ok else 'FAIL'} ({len(cases)} cases)")
    return 0 if ok else 1


if __name__ == '__main__' and sys.argv[1:] == ['selftest']:
    raise SystemExit(selftest())
