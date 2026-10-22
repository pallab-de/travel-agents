#!/usr/bin/env python
"""Capture store: capture each claim once, keep the raw evidence, and validate every spec revision against it.

Why: re-fetching live pages for every spec edit is slow and fragile, and live prices move between runs, so each
edit forced another full review. Here a capture is stored once (value, URL, time, and the full raw page text with
its hash). Any later spec revision is checked offline against those stored captures. Only captures older than the
freshness limit for their kind are re-taken.

Layout: <store>/<sha1(row id)[:16]>.json holds {"row": ..., "captures": [...]}.
Each capture: {"value", "url", "captured_at" (UTC ISO), "raw_text" (the full page text it was read from: required), "raw_sha256"
(of raw_text), "raw_excerpt" (its first 400 characters, a preview only), "http_status" (photos), "excluded" (a reason, if set)}.

Commands:
  put      capture_store.py put <store> <row id> <url> <value> --raw-file FILE --spec spec.json [--http-status N]
  status   capture_store.py status <store> <spec.json>      (what is missing or stale; exit 1 if anything is)
  review   capture_store.py review <store> <spec.json> <out review.json>   (derive a review offline; exit 1 if any row is missing)
  exclude  capture_store.py exclude <store> <row id> <index> <reason>      (a capture that is wrong for its claim, kept on record)
  selftest capture_store.py selftest
"""
import argparse, datetime as dt, hashlib, json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import guard  # noqa: E402
import numfmt  # noqa: E402  (guard puts <plugin root>/tools on sys.path)

# Freshness per claim kind, in hours: the gate's own table (guard.FRESHNESS_DAYS), so a capture the store calls fresh
# is one the gate will accept.
MAX_AGE_H = {k: d * 24 for k, d in guard.FRESHNESS_DAYS.items()}
FALLBACK_H = guard.FRESHNESS_FALLBACK_DAYS * 24


import sys as _sys
if _sys.stdout.encoding and _sys.stdout.encoding.lower() != 'utf-8':   # names in other scripts must print on a Windows pipe too
    _sys.stdout.reconfigure(encoding='utf-8')


def now():
    return dt.datetime.now(dt.timezone.utc)


parse_time = guard.parse_time   # one implementation (a time without an offset is read as UTC)


def path_for(store, row):
    return Path(store) / (hashlib.sha1(row.encode("utf-8")).hexdigest()[:16] + ".json")


def load(store, row):
    p = path_for(store, row)
    return json.loads(p.read_text(encoding="utf-8"))["captures"] if p.exists() else []


def numbers_in(text):
    """Numeric tokens of a text with thousands separators removed: '1,299.50' -> '1299.50'."""
    return set(re.findall(r"\d+(?:\.\d+)?", numfmt.canon(text)))


NEEDS_RAW_NUMBERS = guard.FIGURE_KINDS - guard.TEXT_KINDS
NUMBERED_KINDS = NEEDS_RAW_NUMBERS | guard.TEXT_KINDS | {"count"}   # every kind whose claim carries a number


def _tokens(text):
    """Words, numbers and currency marks of a text in order: numbers read in plain digits whatever the locale, case folded, punctuation gone."""
    t = numfmt.canon(text).lower()
    t = re.sub(r"(\d)\.0+(?!\d)", r"\1", t)   # 1250.00 is 1250
    t = re.sub(r"\s*[\u00b7|\u2022]\s*|\s+/\s+|,\s+", " | ", t)   # a bullet, pipe, spaced slash or comma between items only separates them
    t = t.replace("(", " ( ").replace(")", " ) ")
    t = re.sub(r"\s*[\u2013\u2014\uff5e\u301c]\s*|\s+-\s+|(?<=\w)[-/](?=\w)|(?<=\d)(?:\s*(?:h|hr|hrs|hours?|min|mins|minutes?|km|m))?\s+(?:to|through|thru)\s+(?=[$\u00a2-\u00a5\u20a0-\u20bf\d])|(?<=\d)\s*-\s*(?=[$\u00a2-\u00a5\u20a0-\u20bf])|(?<=\d)\s+and\s+(?=[$\u00a2-\u00a5\u20a0-\u20bf])", " ~ ", t)
    t = re.sub(r"(\bbetween\s+[$\u00a2-\u00a5\u20a0-\u20bf]?\s?\d[\d.]*(?:\s?[a-z]{3})?)\s+and\s+(?=[$\u00a2-\u00a5\u20a0-\u20bf\d])", r"\1 ~ ", t)   # "between 120 and 150 EUR"   # a dash between tokens, however it is spaced, or a fraction: a range mark
    t = re.sub(r"\s*\n\s*", " | ", t)   # a line break ends an item (after the range marks, so a range split over two lines is still a range)
    toks = re.findall(r"[^\W\d_]+|\d+(?:\.\d+)?|[$\u00a2-\u00a5\u20a0-\u20bf~|()]", t)
    num = lambda x: x not in ("~", "|", "(", ")") and NUMBER.fullmatch(x) is not None
    return [x for n, x in enumerate(toks) if x != "~" or (n and num(toks[n - 1])) or (n + 1 < len(toks) and num(toks[n + 1]))]   # a ~ matters only beside a figure ("120-150", "4/5")


NUMBER = re.compile(r"\d+(?:\.\d+)?")
# Words that, right after a figure, make it part of a longer or different one ("9:05 PM" is not "9:05", "2 h 35 min" is not "2 h", "289 USD" is not "289").
UNITS = {"am", "pm", "min", "mins", "minute", "minutes", "h", "hr", "hrs", "hour", "hours", "km", "m", "mi", "mile", "miles", "ft", "night", "nights",
         "review", "reviews", "guest", "guests", "star", "stars", "percent", "day", "days", "eur", "usd", "gbp", "inr", "jpy", "chf", "aud", "cad", "aed",
         "thb", "sgd", "nzd", "hkd", "cny", "rs", "rupees", "euro", "euros", "dollars", "pounds"}
BEFORE_BLOCK = {"of", "top", "over", "under", "than", "above", "below", "first", "last", "next", "upto", "about"}   # "out of 5", "Top 50", "under 100"


CODES_OF = {"$": {"usd"}, "\u20ac": {"eur", "euro", "euros"}, "\u00a3": {"gbp", "pounds"}, "\u20b9": {"inr", "rs", "rupees"}, "\u00a5": {"jpy", "cny"}}   # a symbol followed by its own code
DOLLAR_PREFIXES = {"us", "ca", "a", "au", "nz", "hk", "s", "sg", "r", "c", "cad", "mx", "nt", "aud", "nzd", "usd", "cdn"}   # letters that turn a $ into another currency
QUALIFIERS = {"night", "nights", "guest", "guests", "day", "days"}
FEATURE_QUALIFIERS = {"guest", "guests"}   # follow an amount or a capacity without extending it: "$289 night", "sleeps 12 guests"


def _runs_in(value_tokens, raw_tokens, max_gap=1, loose=None, kind=None):
    """The value's tokens appear in the page's tokens in order, each at most `max_gap` word tokens after the last (a skipped token may not be a number),
    and the match is the whole figure: the token before it is not a number or a word like 'of' / 'top', and the token after it is not a number or a unit the
    value does not itself name. So the figure and the words around it are on the page together, and a longer figure ('1,20,000', '2 h 35 min') is not mistaken for a part of it."""
    value_tokens = [x for x in value_tokens if x != "|"]   # the claim's own separators carry no meaning
    if not value_tokens:
        return False
    for i, t in enumerate(raw_tokens):
        if t != value_tokens[0]:
            continue
        at, ok = i, True
        for want in value_tokens[1:]:
            nxt, gap, j = None, 0, at + 1
            while j < len(raw_tokens) and gap <= max_gap:   # a list separator between the value's words is skipped, any other token counts toward the gap, a number ends the search
                if raw_tokens[j] == want:
                    nxt = j
                    break
                if NUMBER.fullmatch(raw_tokens[j]):
                    break
                gap += raw_tokens[j] != "|"
                j += 1
            if nxt is None:
                ok = False
                break
            at = nxt
        if not ok:
            continue
        before = raw_tokens[i - 1] if i else ""
        after = raw_tokens[at + 1] if at + 1 < len(raw_tokens) else ""
        back = i - 1   # the token before the match, looking through an opening bracket: a figure inside a bracket after a figure ("4.92 (128)", "2 h (35 min)") belongs to that figure
        if before == "(" and i > 1 and kind != "count" and len(value_tokens) == 1 and NUMBER.fullmatch(value_tokens[0]):
            back = i - 2
            before = raw_tokens[back]
        if NUMBER.fullmatch(value_tokens[0]) and (NUMBER.fullmatch(before) or before in BEFORE_BLOCK):
            continue
        if NUMBER.fullmatch(value_tokens[0]) and before in UNITS and back > 0 and NUMBER.fullmatch(raw_tokens[back - 1]):
            continue   # the tail of "2 h 35 min"
        if kind not in ("time", "hours") and before == "~" and i > 1 and NUMBER.fullmatch(raw_tokens[i - 2]):
            continue   # the end of a range or a fraction: "120-150", "4/5" (a time range's second time is a time of its own)
        hi = at + 2 + (at + 2 < len(raw_tokens) and raw_tokens[at + 2] in CODES_OF)   # the upper bound may repeat the currency sign
        if (kind not in ("time", "hours") and after == "~" and hi < len(raw_tokens) and NUMBER.fullmatch(raw_tokens[hi])
                and not (kind == "score" and raw_tokens[hi] in ("5", "10", "100"))):
            continue   # the start of a range: "120-150" is not 120 ("4.7/5" is a score of 4.7)
        if value_tokens[0] in CODES_OF and i and raw_tokens[i - 1] in DOLLAR_PREFIXES:
            continue   # "$150" is not the "CA$150" / "A$150" / "NZ$150" the page shows
        last = value_tokens[-1]
        if last.isalpha() and last not in UNITS:
            return True   # ends on a word that is not a unit (a label): nothing to extend, whatever list item follows
        if last not in UNITS and NUMBER.fullmatch(last) is None and not NUMBER.fullmatch(after):
            return True   # ends on a currency symbol: nothing to extend
        symbol = next((x for x in value_tokens if x in CODES_OF), None)
        if after in CODES_OF.get(symbol, ()):
            return True   # "$289 USD": the currency the symbol already names
        if NUMBER.fullmatch(after) or (after in UNITS and last not in UNITS and not (loose and after in loose)):
            continue
        return True
    return False


UNAVAILABLE_WORDS = re.compile(r"unavailable|not available|isn't available|sold out|fully booked|no availability|no rooms|no longer available|not yet available", re.I)


def value_in_raw(kind, value, raw):
    """The captured value is on the page text it was read from, as the page shows it. A figure (price, score, count, distance, time, date, amount...)
    appears there in order with its currency or unit beside it and as a whole figure ("180 EUR" is not "ref 180-552 ... 1,550 EUR", "9:05" is not "9:05 PM",
    "299 EUR" is not "1 299 EUR", "5" is not the 5 of "out of 5"); a date matches however the page writes it; a policy or hours text appears there in order
    and is not negated ("Pets allowed" is not "No pets", "Free cancellation isn't available" is not "Free cancellation"); a feature's words appear without a
    negation and its numbers sit beside its words ("Sleeps 12" is not "Sleeps 4 ... parking for 12 cars"); availability is the page's own words and the
    page does not say the stay is unavailable near them. A value typed in with no page behind it is not evidence."""
    raw, value = str(raw), str(value)
    if kind == "feature":   # Wi-Fi, WiFi, Wi Fi are one word
        raw, value = (re.sub(r"wi[\W_]*fi", "wifi", t, flags=re.I) for t in (raw, value))
    vt, rt = _tokens(value), _tokens(raw)
    if kind == "date":
        want = guard.date_of(value)
        return bool(want) and any(guard.date_of(raw[m.start(): m.start() + 40]) == want for m in re.finditer(r"(?<![\dA-Za-z])(?:\d|[A-Za-z]{3})", raw))
    if kind == "availability":
        if "unavailable" in value.lower():
            return bool(UNAVAILABLE_WORDS.search(raw))
        m = re.search(r"\s+".join(re.escape(w) for w in value.split()), raw, re.I) if value.strip() else None
        if not m:
            return False
        stops = ".!?\n"
        start = max(raw.rfind(c, 0, m.start()) for c in stops) + 1   # the sentence the phrase sits in: an unrelated "Breakfast not available." elsewhere does not count
        ends = [e for e in (raw.find(c, m.end()) for c in stops) if e >= 0]
        return not UNAVAILABLE_WORDS.search(raw[start: min(ends) if ends else len(raw)])
    if kind == "distance":
        want = guard.claim_key("distance", value)
        if not want:
            return False
        canon = numfmt.canon(raw)
        metres = {round(float(n) * (1000 if u.lower() == "km" else 1)) for n, u in re.findall(r"(\d+(?:\.\d+)?)\s*(km|m)\b", canon, re.I)}
        metres |= {round(float(n)) for n in re.findall(r"(?:walk_m|distance_m|metres|meters)[\"']?\s*[:=]\s*(\d+(?:\.\d+)?)", canon, re.I)}   # rail_distance.py / place_distance.py output
        minutes = {int(n) for n in re.findall(r"(\d+)\s*min(?:s|ute|utes)?\b", canon, re.I)} | {int(n) for n in re.findall(r"(?:walk_min|minutes)[\"']?\s*[:=]\s*(\d+)", canon, re.I)}
        return want[0] in metres and (want[1] is None or want[1] in minutes)
    loose = QUALIFIERS if kind in ("price", "booking") else FEATURE_QUALIFIERS if kind == "feature" else None   # words that qualify an amount or a capacity without extending it
    if kind in NUMBERED_KINDS and not _runs_in(vt, rt, loose=loose, kind=kind):
        return False
    if kind in guard.TEXT_KINDS and not _runs_in(vt, rt, kind=kind):
        return False
    if kind in guard.TEXT_KINDS or kind == "feature":
        need = guard.text_tokens(value)
        if need and guard.negated_near(raw, need, guard.head_token(value)) and not guard.negated_near(value, need):
            return False
    if kind == "feature":
        if guard.text_tokens(value):
            if not guard.feature_agrees(value, raw):
                return False
        else:   # a short feature ("Wi-Fi", "AC", "TV"): the whole word must be on the page
            flat = lambda t: re.sub(r"[\W_]+", " ", t.lower()).strip()
            want = flat(value)
            pat = r"(?<![A-Za-z0-9])" + r"[\W_]+".join(re.escape(w) for w in want.split()) + r"(?![A-Za-z0-9])"
            here = list(re.finditer(pat, raw, re.I))
            if not here:
                return False
            if all(guard.negated_near(raw[max(0, m.start() - 40): m.end() + 40], set(want.split()), None) for m in here):
                return False   # "No Wi-Fi", "AC out of order": every mention of the short feature is a denial
        if (len(vt) > 1 or any(NUMBER.fullmatch(t) for t in vt)) and not _runs_in(vt, rt, max_gap=2, loose=FEATURE_QUALIFIERS, kind=kind):
            return False   # the claim's words (and numbers) must sit together on the page, not be scattered over it
    return True


def put(store, row, url, value, raw=None, http_status=None, when=None, kind=None):
    """Store one capture. The raw page text is required: a capture with nothing behind it is a typed number. For a
    figure kind the value's numbers must appear in that text."""
    if not raw or not str(raw).strip():
        raise SystemExit(f"{row}: a capture needs the raw page text it was read from (--raw-file); a bare value is not evidence")
    if kind == "link" and not (isinstance(http_status, int) and not isinstance(http_status, bool) and 200 <= http_status < 300):
        raise SystemExit(f"{row}: a link capture must record the status the page answered with a 2xx (pass --http-status 200 after opening it)")
    if kind and not value_in_raw(kind, value, raw):
        missing = sorted(numbers_in(value) - numbers_in(raw))
        raise SystemExit(f"{row}: the value {value!r} is not shown by the raw text" + (f" ({missing} do not appear in it)" if missing else "") + "; capture the page that shows it")
    Path(store).mkdir(parents=True, exist_ok=True)
    p = path_for(store, row)
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"row": row, "captures": []}
    data["row"] = row
    data["captures"].append({
        "value": value, "url": url,
        "captured_at": when or now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "raw_text": raw, "raw_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "raw_excerpt": raw[:400],
        "http_status": http_status,
    })
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def usable(capture):
    """A capture is usable unless someone excluded it with a stated reason (wrong occupancy, wrong source...), and only
    if the raw page it was read from is on record in full and still matches its hash."""
    raw = capture.get("raw_text")
    return (not capture.get("excluded") and isinstance(raw, str) and bool(raw.strip())
            and hashlib.sha256(raw.encode("utf-8")).hexdigest() == capture.get("raw_sha256"))


def exclude(store, row, index, reason):
    p = path_for(store, row)
    if not p.exists():
        raise SystemExit(f"no captures stored for {row!r}")
    data = json.loads(p.read_text(encoding="utf-8"))
    if not (isinstance(index, int) and 0 <= index < len(data["captures"])):
        raise SystemExit(f"{row!r} has {len(data['captures'])} capture(s); index {index} is out of range")
    if not str(reason).strip():
        raise SystemExit("an exclusion needs a reason")
    data["captures"][index]["excluded"] = reason
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def fresh(capture, kind, at=None):
    t = parse_time(capture.get("captured_at") or "")
    if t is None:
        return False
    at = at or now()
    if t - at > dt.timedelta(days=1):
        return False   # dated in the future
    return (at - t) <= dt.timedelta(hours=MAX_AGE_H.get(kind, FALLBACK_H))


def mark_review(store, spec_bytes, at=None):
    """Record in the store, not in a file the builder edits, when the review of this spec was taken: the spot check's second
    look has to come after it."""
    Path(store).mkdir(parents=True, exist_ok=True)
    (Path(store) / "_review.json").write_text(json.dumps({"spec_sha256": guard.sha(spec_bytes), "derived_at": (at or now()).strftime("%Y-%m-%dT%H:%M:%SZ")}), encoding="utf-8")


def derive_review(spec, store, at=None, spec_bytes=None, base_dir=None):
    """Build a review from stored captures. A row is CONFIRMED when it has enough usable, fresh captures from sources
    its kind of claim may cite; otherwise it is left out, so the gate reports it as having no verdict. Whether the
    captures AGREE with the spec's figure is the gate's check, made again on every run."""
    rows = guard.ledger(spec)
    pol = guard.policy(spec)
    out = {"spec_sha256": guard.sha(spec_bytes), "scope": "full", "retest_reason": None,
           "derived_from": "capture store", "derived_at": (at or now()).strftime("%Y-%m-%dT%H:%M:%SZ"), "rows": {}}
    report = []
    for r in rows:
        caps = [c for c in load(store, r["row"]) if usable(c) and fresh(c, r["kind"], at)]
        barred = [c for c in caps if guard.capture_source_fail(r, [dict(c, url=c.get("url"))], pol, base_dir)]
        caps = [c for c in caps if c not in barred]
        if r["kind"] in NUMBERED_KINDS or r["kind"] == "feature":   # the value must be on the page it was read from, checked again here, not only when it was stored
            unread = [c for c in caps if not value_in_raw(r["kind"], c.get("value"), c.get("raw_text"))]
            caps = [c for c in caps if c not in unread]
            barred += unread
        need = 2 if r["kind"] in ("price", "score", "count") else 1
        if len(caps) < need:
            why = f"; {len(barred)} came from a source a {r['kind']} claim may not cite" if barred else ""
            report.append(f"{r['row']}: {len(caps)} fresh capture(s) in store, {need} needed (kind {r['kind']}, max age "
                          f"{MAX_AGE_H.get(r['kind'], FALLBACK_H)} h{why}): capture again")
            continue
        out["rows"][r["row"]] = {"verdict": "CONFIRMED", "note": "derived from stored captures",
                                 "captures": [{"value": c["value"], "url": c["url"], "at": c["captured_at"],
                                               "http_status": c.get("http_status"), "raw_sha256": c.get("raw_sha256")} for c in caps]}
    return out, report


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("put"); p.add_argument("store"); p.add_argument("row"); p.add_argument("url")
    p.add_argument("value"); p.add_argument("--raw-file", required=True); p.add_argument("--http-status", type=int)
    p.add_argument("--spec", required=True, help="the spec.json whose ledger the row belongs to")
    p = sp.add_parser("status"); p.add_argument("store"); p.add_argument("spec")
    p = sp.add_parser("review"); p.add_argument("store"); p.add_argument("spec"); p.add_argument("out")
    p = sp.add_parser("exclude"); p.add_argument("store"); p.add_argument("row"); p.add_argument("index", type=int); p.add_argument("reason")
    p = sp.add_parser("selftest")
    a = ap.parse_args()
    if a.cmd == "selftest":
        return selftest()
    if a.cmd == "put":
        raw = Path(a.raw_file).read_text(encoding="utf-8")
        kinds = {r["row"]: r["kind"] for r in guard.ledger(json.loads(Path(a.spec).read_text(encoding="utf-8")))}
        if a.row not in kinds:
            raise SystemExit(f"refused: {a.row!r} is not a row the spec creates")
        put(a.store, a.row, a.url, a.value, raw=raw, http_status=a.http_status, kind=kinds[a.row])
        print("stored")
    elif a.cmd == "exclude":
        exclude(a.store, a.row, a.index, a.reason)
        print("excluded")
    elif a.cmd in ("status", "review"):
        spec = json.loads(Path(a.spec).read_text(encoding="utf-8"))
        review, missing = derive_review(spec, a.store, spec_bytes=Path(a.spec).read_bytes(), base_dir=Path(a.spec).resolve().parent)
        if a.cmd == "review":
            mark_review(a.store, Path(a.spec).read_bytes())
            Path(a.out).write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"review written: {len(review['rows'])} rows confirmed from store, {len(missing)} need capture")
        for m in missing:
            print("MISSING", m)
        if a.cmd == "status":
            print(f"{len(review['rows'])} rows covered, {len(missing)} missing or stale")
        return 1 if missing else 0
    return 0


def selftest():
    import tempfile
    spec = {"policy": {}, "blocks": [{"type": "cards", "id": "c", "items": [{"name": "A", "rank": 1, "url": "https://www.hotel.example/a", "price": 100,
            "dossier": [{"title": t, "not_found": "stub"} for t in guard.REQUIRED_DOSSIER]}]}]}
    raw = json.dumps(spec).encode()
    row = "c:A|price"
    cases = []
    with tempfile.TemporaryDirectory() as td:
        store = str(Path(td) / "caps")
        put(store, row, "https://www.hotel.example/a", "EUR 100", raw="Room from EUR 100 per night", kind="price")
        review, missing = derive_review(spec, store, spec_bytes=raw)
        cases.append(("one price capture is not enough", any(m.startswith(row) for m in missing)))
        put(store, row, "https://www.hotel.example/a", "EUR 100", raw="Room from EUR 100 per night")
        review, missing = derive_review(spec, store, spec_bytes=raw)
        cases.append(("two captures confirm the price row", row in review["rows"]))
        put(store, row, "https://random-blog.example/x", "EUR 100", raw="EUR 100")
        put(store, row, "https://random-blog.example/y", "EUR 100", raw="EUR 100")
        s2 = str(Path(td) / "caps2")
        put(s2, row, "https://random-blog.example/x", "EUR 100", raw="EUR 100"); put(s2, row, "https://random-blog.example/y", "EUR 100", raw="EUR 100")
        review2, missing2 = derive_review(spec, s2, spec_bytes=raw)
        cases.append(("captures from a source the kind may not cite don't count", row not in review2["rows"] and any("may not cite" in m for m in missing2)))
        s3 = str(Path(td) / "caps3")
        for _ in range(2):
            put(s3, row, "https://www.hotel.example/a", "EUR 100", raw="EUR 100", when="2099-01-01T00:00:00Z")
        review3, _m = derive_review(spec, s3, spec_bytes=raw)
        cases.append(("a capture dated in the future is not fresh", row not in review3["rows"]))
        refused = []
        for kw in (dict(raw=None), dict(raw="Room from EUR 250", kind="price")):
            try:
                put(str(Path(td) / "caps4"), row, "https://www.hotel.example/a", "EUR 100", **kw); refused.append(False)
            except SystemExit:
                refused.append(True)
        cases.append(("a capture with no raw text, or a value the raw text doesn't show, is refused", all(refused)))
        bad_index = []
        for idx in (-1, 9):
            try:
                exclude(store, row, idx, "wrong page")
                bad_index.append(False)
            except SystemExit:
                bad_index.append(True)
        cases.append(("exclude refuses a negative or out-of-range index", all(bad_index)))
        exclude(store, row, 0, "wrong occupancy")
        review4, _m = derive_review(spec, store, spec_bytes=raw)
        cases.append(("an excluded capture is never used", len(review4["rows"].get(row, {}).get("captures", [])) < 3))
        specp = Path(td) / "spec.json"; specp.write_bytes(raw)
        sys.argv = ["capture_store.py", "status", store, str(specp)]
        import io, contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            rc = main()
        cases.append(("status exits 1 while rows are missing", rc == 1))
    table = [  # (kind, captured value, page text, should it be on the page?)
        ("booking", "180 EUR", "Booking ref 180-552, total 1,550 EUR", False), ("booking", "180 EUR", "total 180 EUR", True),
        ("date", "2030-05-06", "Check-in: 2030-06-05", False), ("date", "2030-05-07", "Check-in Tue, 7 May 2030", True), ("time", "09:10", "departs 10:09", False),
        ("time", "9:05", "Departs 9:05 PM", False), ("time", "9:05 PM", "Departs 9:05 PM", True), ("duration", "2 h", "2 h 35 min", False),
        ("price", "EUR 289.00", "Total 289.00 USD", False), ("price", "\u00a3289", "$289", False), ("price", "\u20ac1,299.50", "from \u20ac1,299.50 per night", True),
        ("price", "\u20ac1.299,50", "from \u20ac1.299,50 per night", True), ("price", "1 299 \u20ac", "1 299 \u20ac", True), ("price", "299 \u20ac", "1 299 \u20ac", False),
        ("price", "\u20b920,000", "\u20b91,20,000", False), ("price", "\u20b91,20,000", "\u20b91,20,000 for 2 nights", True),
        ("score", "5", "Rated 4.7 out of 5", False), ("score", "4.7", "Rated 4.7 out of 5", True), ("count", "50", "Top 50 stays", False), ("count", "1,041 reviews", "from 1,041 reviews", True),
        ("policy", "Free cancellation", "Non-refundable. No refund after booking.", False), ("policy", "Pets allowed", "House rules: No pets.", False),
        ("policy", "No pets", "House rules: No pets.", True), ("policy", "Free cancellation", "Free cancellation isn't available", False),
        ("feature", "Sleeps 12 in 6 bedrooms", "Sleeps 4 in 2 bedrooms", False), ("feature", "Sleeps 4", "Sleeps 4 in 2 bedrooms", True), ("feature", "Sleeps 12", "Sleeps 4. Parking for 12 cars", False),
        ("feature", "Private pool", "Private pool: No", False), ("feature", "Wi-Fi", "Free Wi-Fi in all rooms", True), ("feature", "TV", "no television", False),
        ("availability", "Available 3-5 Mar", "No rooms left. Fully booked.", False), ("availability", "Unavailable", "Sold out", True),
        ("availability", "Available", "Available for 3-5 Mar. Breakfast not available.", True), ("availability", "Available", "Available for these dates. Sold out for May.", True),
        ("distance", "Elmside: 450 m walk, 6 min (Google Maps)", '{"walk_m": 450, "walk_min": 6}', True), ("distance", "450 m walk, 6 min", "Walking 0.45 km, 6 mins", True),
        ("distance", "450 m", "Walking 1.2 km", False),
        ("date", "2030-05-07", "Check-in: Fri, 17 May 2030", False), ("date", "2030-05-01", "Check-in: 21 May 2030", False), ("duration", "35 min", "Drive 2 h 35 min", False),
        ("feature", "Private pool", "Shared pool. Private parking.", False), ("policy", "Pets allowed", "Pets allowed. No smoking.", True),
        ("feature", "Kitchen", "Kitchen · Dishwasher not included", True), ("price", "$289", "$289 night · $1,445 total", True), ("price", "€120", "€120 / night", True),
        ("time", "09:05", "Depart 09:05 – 11:30 Arrive", True), ("feature", "Sleeps 12", "Sleeps up to 12 guests", True), ("distance", "4.2 km", "Sleeps 4 200 m from the beach", False),
        ("score", "5", "Rated 4.7/5", False), ("score", "4.7", "Rated 4.7/5", True),
        ("score", "5 stars", "Rated 4/5 stars", False), ("price", "150 €", "120–150 € per night", False), ("count", "6 guests", "4–6 guests", False),
        ("feature", "Pool", "Pool · Not available", False), ("feature", "Breakfast", "Breakfast | not included", False), ("distance", "1.2 km", "Strand 1.200 m", True),
        ("hours", "Mon - Fri 9 AM - 5 PM", "Mon–Fri 9 AM–5 PM", True), ("time", "11:30", "Depart 09:05 – 11:30 Arrive", True), ("feature", "Parking for 2", "Free parking for 2 days", False),
        ("score", "9.2", "Superb\n9.2\n1,204 reviews", True), ("price", "₹4,500", "₹4,500\n2 nights", True), ("time", "06:40", "06:40\n2h 35m\n09:15", True),
        ("feature", "TV", "TV\nNo smoking", True), ("feature", "Free parking", "Free parking in a closed garage", True), ("feature", "Elevator", "Elevator (out of service)", False), ("feature", "Elevator", "Elevator - out of service", False), ("feature", "Sauna", "Sauna, under maintenance", False), ("feature", "Wi-Fi", "Free Wifi", True),
        ("feature", "Wi-Fi", "No Wi-Fi in rooms", False), ("feature", "AC", "AC out of order", False), ("feature", "TV", "TV room, no pets", True), ("feature", "Spa", "Spa is temporarily closed", False), ("feature", "Pool", "Pool and gym closed", False), ("feature", "Gym", "Gym under renovation", False), ("price", "$150", "$120-$150", False), ("price", "120 EUR", "between 120 and 150 EUR", False), ("policy", "Check-in after 3 PM", "Check-in after 3 PM. No late check-out.", True), ("feature", "Pool", "Pool - no diving", True),
        ("feature", "Outdoor pool", "Outdoor pool, temporarily closed", False), ("feature", "Pool", "Pool, currently unavailable", False), ("feature", "Hot tub", "Hot tub, but not available", False), ("feature", "Pool", "Pool (closed)", False), ("feature", "Free parking", "Free parking (no reservation needed)", True), ("price", "$150", "CA$150 per night", False), ("price", "$150", "A$150 per night", False), ("price", "$150", "$150 per night", True), ("price", "\u20ac145", "\u20ac145 to \u20ac180", False), ("price", "\u00a518,000", "\u00a518,000\uff5e\u00a525,000", False), ("price", "\u20ac145", "between \u20ac145 and \u20ac180", False),
        ("feature", "Private pool", "Private pool, closed for renovation", False), ("feature", "Breakfast", "Breakfast, not included in the rate", False), ("feature", "Heated indoor pool", "Heated indoor pool. Pool closed all winter.", False), ("feature", "Free airport shuttle", "Free airport shuttle. Shuttle is not available.", False), ("feature", "Pool", "Pool, closed", False), ("feature", "Kitchen", "Kitchen, dishwasher not included", True),
        ("price", "\u20ac145", "\u20ac145\u2013\u20ac180", False), ("price", "\u20b92,499", "\u20b92,499 \u2013 \u20b93,999", False), ("price", "$150", "$120 - $150", False), ("policy", "Free cancellation", "Free cancellation, no prepayment needed", True), ("policy", "Pets allowed", "Smoking not allowed, pets allowed", True),
        ("score", "128", "4.92 (128)", False), ("time", "15:00", "3:00 PM (15:00)", True), ("policy", "Check-in 3 PM, check-out 11 AM", "Check-in 3 PM check-out 11 AM", True), ("feature", "3 bedrooms", "3 bedrooms, 4 beds, 2 baths", True), ("price", "£1,250", "£1,250.00", True), ("price", "$289", "$289 USD", True), ("price", "$289", "$289 CAD", False),
        ("score", "4.92", "4.92 (87)", True), ("count", "87 reviews", "4.92 (87 reviews)", True),
        ("score", "4.92", "★ 4.92 · 87 reviews", True), ("count", "87 reviews", "★ 4.92 · 87 reviews", True), ("count", "5 beds", "4 bedrooms · 5 beds", True), ("price", "₹24,000", "₹24,000 · 2 nights", True), ("price", "£95", "£95 / 2 guests", True),
    ]
    wrong = [(k, v, r) for k, v, r, want in table if value_in_raw(k, v, r) != want]
    cases.append(("values are matched to the page as it is written: order, currency, whole figures, locale, negation, sentence-local availability, our own measurement files", not wrong))
    if wrong:
        print("  value_in_raw mismatches:", wrong[:4])
    cases.append(("every kind's value is tied to its page text: counts, hours, policies and features too",
                  value_in_raw("count", "1,204 reviews", "Rated 4.5 from 1,204 reviews") and not value_in_raw("count", "5,000 reviews", "Rated 4.5 from 1,204 reviews")
                  and not value_in_raw("hours", "9-17", "Open 8 to 12") and value_in_raw("policy", "Check-in after 1:00 pm", "Check-in after 1:00 pm")
                  and value_in_raw("feature", "heated pool", "A heated pool and sauna") and not value_in_raw("feature", "heated pool", "There is no heated pool")
                  and not value_in_raw("feature", "sauna", "a garden")))
    for name, ok in cases:
        print(("PASS " if ok else "FAIL ") + name)
    good = all(ok for _, ok in cases)
    print(f"SELFTEST {'PASS' if good else 'FAIL'} ({len(cases)} cases)")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
