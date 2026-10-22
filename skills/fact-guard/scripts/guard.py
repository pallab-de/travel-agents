#!/usr/bin/env python
"""fact-guard: fail-closed verification gate for any ranked report spec.

A report spec is only publishable when every structured claim in it (price, score, distance, room size,
availability, photo, link) has a recorded verdict from an independent reviewer, backed by captures, and
the review was made against exactly this spec (sha256). Prose is covered by banned-phrase rules.

Subcommands:
  ledger  spec.json                          -> writes ledger.json beside the spec (one row per checkable claim)
  sample  ledger.json --rate 0.2 --min 8     -> writes spot.plan.json beside the ledger (deterministic spot-check sample)
  gate    spec.json review.json [spot.json]  -> exit 0 only if every gate passes; prints every failure
  evals   spec.json golden.json              -> regression facts that must / must not appear
  selftest                                   -> proves the gate rejects bad inputs (run in CI)

Stdlib only. Paths are taken from the arguments (or sit beside the file they come from), never from the current directory.
"""
import argparse, os, datetime as dt, hashlib, json, math, random, re, sys, tempfile
from pathlib import Path
from urllib.parse import urlparse

# Where a claim may come from depends on what kind of claim it is. A hotel's own site is the authority on its own
# price, policies and photos, but its testimonials are self-curated, so it can never back a rating or a concern.
# Sites are grouped into classes; a pattern is a domain matched by suffix ("booking.com" covers www.booking.com),
# "name.*" for any country domain (tripadvisor.co.uk), optionally with a path prefix ("google.*/maps"). Two
# classes are not lists: own_site is worked out per card (its own url's domain, plus any own_domains it declares),
# and evidence is a file saved in the project's evidence/ folder (a booking confirmation, a ticket). A spec may add
# patterns to a class with policy.source_classes, e.g. the airline flying a leg.
SOURCE_CLASSES = {
    # Independent review platforms: anyone can review, nobody is paid by the property for the rating.
    "review_platform": ["google.*/maps", "maps.google.*", "maps.app.goo.gl", "tripadvisor.*", "yelp.*"],
    # Booking platforms whose reviews come only from verified stays; their CDNs serve listing photos.
    "verified_ota": ["booking.*", "expedia.*", "hotels.com", "agoda.*", "airbnb.*", "vrbo.com",
                     "bstatic.com", "muscache.com", "trvl-media.com", "agoda.net"],
    # Candid but unverified: good for spotting a problem early, never for a number.
    "community": ["reddit.com", "x.com", "twitter.com", "flyertalk.com", "tripadvisor.*/ShowTopic",
                  "lonelyplanet.com/thorntree", "youtube.com", "youtu.be"],
    "maps_measure": ["google.*/maps/dir", "maps.google.*/maps/dir", "openrouteservice.org"],   # a measurement is a directions link, never a place page
    "flight_engine": ["google.*/travel/flights", "serpapi.com", "mcp:flightclaw"],
    "airline": [],
    "rail": ["nationalrail.co.uk", "trainline.com", "raileurope.com", "eurostar.com", "sncf-connect.com", "bahn.de",
             "trenitalia.com", "renfe.com", "amtrak.com", "jreast.co.jp", "irctc.co.in", "indianrail.gov.in", "12go.asia"],
    "official": ["gov"],   # government and tourism-authority sites (*.gov, *.gov.xx)
}
KIND_SOURCES = {
    "score": ["review_platform", "verified_ota"], "count": ["review_platform", "verified_ota"],
    "price": ["own_site", "verified_ota", "flight_engine", "airline", "rail", "evidence"],
    "availability": ["own_site", "verified_ota", "flight_engine", "airline", "rail"],
    "link": ["own_site", "verified_ota", "review_platform", "official", "airline", "rail"],
    "photo": ["own_site", "verified_ota", "review_platform"],
    "feature": ["own_site", "verified_ota", "review_platform", "official"],
    "hours": ["own_site", "verified_ota", "review_platform", "official"],
    "policy": ["own_site", "verified_ota", "official"],
    "size": ["own_site", "verified_ota"], "stars": ["own_site", "verified_ota", "review_platform", "official"],
    "distance": ["maps_measure"], "km": ["maps_measure"],
    "duration": ["maps_measure", "flight_engine", "airline", "rail"],
    "time": ["own_site", "official", "flight_engine", "airline", "rail", "evidence"],
    "date": ["own_site", "verified_ota", "airline", "rail", "evidence"],
    "booking": ["own_site", "verified_ota", "airline", "rail", "evidence"],
    "points": ["own_site", "airline", "rail", "official", "evidence"],   # a points price is quoted from the operator's own page
}
CONCERN_CLASSES = ["review_platform", "verified_ota", "community"]
SCORE_WEIGHTS = {"verified_ota": 1.5, "review_platform": 1.0}   # rank-score-v2's guest score; others never count
MIN_SCORE_SOURCES = 2   # rank-score-v2: at least this many, or the missing ones declared unavailable with a reason


# "name.*" means the brand's real country domains (tripadvisor.co.uk), never any suffix someone can buy (booking.xyz).
_COUNTRY_LIST = ["com", "net", "org", "co.uk", "uk", "co.in", "in", "co.jp", "jp", "com.au", "au", "co.nz", "nz", "com.sg", "sg", "com.hk", "hk",
     "com.my", "my", "co.th", "th", "com.vn", "vn", "co.id", "id", "com.ph", "ph", "co.kr", "kr", "com.tw", "tw", "cn", "com.cn",
     "ae", "tr", "com.tr", "sa", "il", "co.il", "za", "co.za", "eg", "ma", "ke", "ng",
     "de", "fr", "it", "es", "pt", "nl", "be", "ch", "at", "ie", "se", "no", "dk", "fi", "pl", "cz", "sk", "hu", "ro", "bg", "gr", "hr",
     "si", "rs", "ru", "ua", "lt", "lv", "ee", "is", "ca", "us", "mx", "com.mx", "br", "com.br", "ar", "com.ar", "cl", "co", "com.co", "pe"]
COUNTRY_SUFFIXES = "|".join(sorted(_COUNTRY_LIST, key=len, reverse=True)).replace(".", r"\.")


import sys as _sys
if _sys.stdout.encoding and _sys.stdout.encoding.lower() != 'utf-8':   # names in other scripts must print on a Windows pipe too
    _sys.stdout.reconfigure(encoding='utf-8')
_sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))   # <plugin root>/tools/numfmt.py: one reader for 1,299.50 / 1.299,50 / 1 299,50 / 1,20,000
import numfmt  # noqa: E402


def _pattern_match(pattern, host, path):
    dom, _, pre = pattern.partition("/")
    if dom == "gov":
        hit = bool(re.search(r"(^|\.)gov(\.[a-z]{2})?$", host))
    elif dom.endswith(".*"):
        hit = bool(re.search(r"(^|\.)" + re.escape(dom[:-2]) + r"\.(" + COUNTRY_SUFFIXES + r")$", host))
    else:
        hit = host == dom or host.endswith("." + dom)
    return hit and (not pre or path.startswith("/" + pre))


def classes_of(url, classes, own=()):
    """Every source class a URL belongs to. own is the set of hosts that count as the owner's own site here."""
    s = str(url or "").strip()
    if s.startswith("evidence/"):
        return {"evidence"}
    if s.startswith("mcp:"):
        return {c for c, pats in classes.items() if s in pats}
    try:
        u = urlparse(s)
    except Exception:
        return set()
    if u.scheme != "https" or not u.hostname:
        return set()
    host, path = u.hostname.lower(), u.path or "/"
    out = {c for c, pats in classes.items() if any(_pattern_match(p, host, path) for p in pats)}
    # A path-specific pattern beats the site-wide one: a TripAdvisor forum thread is a community post, not a review platform.
    if "community" in out and any("/" in p and _pattern_match(p, host, path) for p in classes.get("community", [])):
        out.discard("review_platform")
    if any(host == h or host.endswith("." + h) for h in own):
        out.add("own_site")
    return out


def own_hosts(urls, declared, classes):
    """The owner's own site: the hosts of its own urls that aren't a platform, plus any domains it declares
    (a hotel group's photo CDN, say)."""
    out = {str(d).lower() for d in declared or [] if real_domain(d)}
    for u in urls:
        h = urlparse(str(u or "")).hostname
        if h and real_domain(h) and not classes_of(u, classes):
            out.add(h.lower())
    return out


def real_domain(host):
    """A full registrable domain (name.tld), never a bare or public suffix such as 'com' or 'co.uk': an owner set built
    from one would make every site under that suffix the property's own."""
    h = str(host or "").strip().lower().lstrip(".")
    return bool(re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", h)) and (h not in _BARE_SUFFIXES or reserved_host(h))


BANNED =["not captured", "sold out", "smallest", "cheapest", "the only", "tbd", "todo", "lorem"]
LABEL_KIND = [  # most specific first: "Family room rate" is a price, not a room size
    ("beds", "feature"), ("amenity", "feature"), ("house rule", "policy"), ("pet terms", "feature"), ("ask the host", "note"),   # a stay card's sleeping arrangement, amenity and rule rows: claims the listing page must show
    ("rate", "price"), ("price", "price"), ("rail", "distance"), ("distance", "distance"),
    ("drive", "duration"), ("walk", "duration"),
    ("guest", "score"), ("score", "score"), ("star", "stars"), ("rating", "score"), ("room", "size"),
    ("opening", "hours"), ("hours", "hours"), ("check-in", "policy"), ("check-out", "policy"),
    ("cancel", "policy"), ("policy", "policy"), ("available", "availability"),
]

# How old a capture may be, per claim kind, before the gate refuses it and it must be captured again. Days.
# Prices move fastest; static facts (address, amenities, distances) slowest. A spec may TIGHTEN any of these
# with policy.freshness_days, never loosen them.
FRESHNESS_DAYS = {
    "price": 14, "availability": 14, "points": 14,
    "score": 90, "count": 90,
    "hours": 180, "policy": 180, "duration": 180, "time": 180, "date": 180, "booking": 180,
    "distance": 730, "km": 730, "size": 730, "stars": 730, "photo": 730, "link": 730, "feature": 730,
}
FRESHNESS_FALLBACK_DAYS = 14   # an unknown kind gets the shortest window, never a free pass

# Critical facts are the ones a traveller acts on at a fixed moment: flight and train times and fares, booking
# amounts and cancellation dates, ticketed or reserved events. Getting one wrong costs a missed flight or a lost
# deposit, so each needs captures from at least this many independent sources (distinct hosts or evidence files).
CRITICAL_MIN_SOURCES = 2
CRITICAL_MODES = {"air", "rail"}
MODES = {"air", "rail", "road", "car", "bus", "coach", "ferry", "boat", "walk", "metro", "transit", "taxi", "bike"}


def mode_of(m):
    """A transport mode as the gate reads it: trimmed and lower-case, so 'Air' is air."""
    return str((m or {}).get("mode") or "").strip().lower()


def transport_mode_fails(spec):
    fails = []
    for b in spec.get("blocks", []):
        if b.get("type") == "transport_leg":
            for leg in b.get("legs", []):
                for m in leg.get("modes", []):
                    if mode_of(m) not in MODES:
                        fails.append(f"{leg.get('label')}: transport mode {m.get('mode')!r} is not one of {sorted(MODES)}; an unknown mode would skip the two-source and door-to-door rules")
    return fails
CRITICAL_STOP_TYPES = {"flight", "train", "reservation", "ticket", "event"}
OFF_STATUS = {"rejected", "cancelled"}   # stops and bookings left in a plan only to record why they were dropped
BOOKING_STATUSES = {"booked", "upcoming", "paid", "held", "open", "cancelled", "rejected"}
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

# A quality outside the agreed fit profile that the fit scorer argues anyone would value here (a striking view, a
# heritage building in an old town). Open, not a list: it is named, evidenced and challenged like any overlay.
UNIVERSAL = re.compile(r"^universal:[a-z][a-z0-9_]*$")

# A fact that says something is absent can never earn a bump for having it ("No pool" is not a pool).
NEGATED = re.compile(r"^\s*(no|not|none|without|unavailable|closed|n/?a|false)\b|\b(?:there (?:is|are) no|has no|have no|with no|does not (?:have|offer)|doesn't (?:have|offer)|(?:is|are|was|were) not|isn't|aren't|out of order|no longer|not (?:available|included|allowed|open|offered)|closed (?:for|until|permanently)|:\s*(?:none|no|closed|unavailable))\b", re.I)


REQUIRED_DOSSIER = ["Map listing", "Guest ratings", "Guest reviews", "Photos"]
COUNT_TOLERANCE = 0.01  # review counts drift between page loads; ratings must match exactly
TEXT_KINDS = {"hours", "policy"}           # compared as normalised text, not as a number
FIGURE_KINDS = {"price", "points", "score", "distance", "size", "stars", "duration", "km", "booking", "time", "date"} | TEXT_KINDS

# fact-guard's own rule vocabulary is versioned: bump this only when a field this gate already checks changes
# MEANING for specs that already set it (adding a new, separately-named formula value is not such a change). A spec
# may optionally carry a top-level "policy_version" recording what version it was built against; a mismatch is a
# WARN, not a FAIL, since old specs are not retroactively invalid, just worth a fresh look.
POLICY_VERSION = 1

# Recognised score_basis.formula values. A formula never changes meaning; a new rule gets a new name.
# "rank-score-v2" is contextual: a baseline guest score plus overlays for what makes a place right or wrong for THIS
# group and trip (a lake view for a group that wants to unwind, a sound system for a party, a room inside the old
# quarter for a reader of a novel set there). Each overlay answers a criterion of the fit profile the user agreed
# (spec.fit_criteria: id, weight, polarity, evidence), or an open universal:<quality>; points at one captured fact
# (or, for a drawback, a corroborated concern); and stays within its criterion's weight and sign.
KNOWN_FORMULAS = {"rank-score-v2"}
MAX_SIGNAL_BUMP = 0.5           # one overlay moves the score by at most this much, either way
MAX_TOTAL_GROUP_FIT_BUMP = 1.0  # all of a card's overlays together, either way
RANK_SCORE_CAP = 5.0            # the rank score stays on the guest score's 1-5 scale
RANK_SCORE_FLOOR = 1.0
POLARITIES = {"+": 1, "-": -1}

# The policy is DATA, not code: a spec may carry its own "policy" object, but only to make the gate STRICTER. Keys it
# omits keep these defaults (a travel stay). Every key is tighten-only: freshness windows shorter, the critical-source
# count higher, banned phrases and required dossier sections added, more reviews read, classes removed from a kind,
# review methods removed, source patterns added to a class. A key that would loosen a rule, or an unknown key, fails
# the gate (policy_fails) and is ignored by policy(). The builder of a spec is who the gate polices.
DEFAULT_POLICY = {
    "source_classes": SOURCE_CLASSES,
    "kind_sources": KIND_SOURCES,
    "banned": BANNED,
    "label_kinds": [list(x) for x in LABEL_KIND],
    "required_sections": REQUIRED_DOSSIER,
    "min_reviews_read": 20,                      # per source, each stored with its text hash
    "review_methods": ["google-places-api", "apify-google-maps-reviews-scraper", "serpapi-google-maps-reviews",
                       "apify-booking-reviews", "apify-tripadvisor-reviews", "apify-airbnb-reviews"],
    "web_sources_min": 1,                        # plus web research, recorded with a read date
    "freshness_days": FRESHNESS_DAYS,
    "critical_min_sources": CRITICAL_MIN_SOURCES,
    "policy_version": POLICY_VERSION,
}
POLICY_KEYS = set(DEFAULT_POLICY) | {"policy_version"}
CHECKED_KINDS = set(KIND_SOURCES)   # a spec may classify its own labels, but only into kinds that are checked
LABEL_KINDS_ALLOWED = FIGURE_KINDS   # ...and only into figure kinds, whose numbers are compared; availability, link, photo and feature don't compare figures


PUBLIC_SUFFIX_2 = {"co.uk", "org.uk", "ac.uk", "gov.uk", "co.in", "org.in", "gov.in", "co.jp", "or.jp", "com.au", "net.au",
                    "co.nz", "com.br", "co.za", "com.sg", "com.hk", "com.tr", "com.mx", "com.cn", "co.kr", "co.th", "com.my"}

ADDABLE_CLASSES = {"airline", "rail", "official", "flight_engine"}   # per-trip operators; review, booking and map classes are closed
PUBLIC_SUFFIX_2 |= {c for c in _COUNTRY_LIST if "." in c}   # every two-level country suffix we match is also one registrable domains sit under
_BARE_SUFFIXES = {"com", "net", "org", "co", "uk", "in", "de", "fr", "it", "es", "gov", "edu", "io"} | PUBLIC_SUFFIX_2


def reserved(pat):
    return str(pat).partition("/")[0].lower().rsplit(".", 1)[-1] in ("test", "example", "invalid")


def pattern_ok(pat):
    """A source pattern a spec may add: a full domain (name.tld, optional path), never a bare suffix such as 'com'."""
    dom = str(pat).partition("/")[0].lower()
    if dom.endswith(".*") or not re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", dom):
        return False
    if dom.rsplit(".", 1)[-1] in ("test", "example", "invalid"):
        return True   # reserved names that never resolve: what the bundled examples use for their fictional platforms
    return dom not in _BARE_SUFFIXES


def policy_type_fails(own):
    """A policy whose values are the wrong shape (a string where a list belongs). The gate names these and falls back
    to the defaults rather than crashing on them."""
    if not isinstance(own, dict):
        return ["policy must be an object"]
    fails = []
    isnum = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
    f = own.get("freshness_days")
    if f is not None and not (isinstance(f, dict) and all(isnum(v) for v in f.values())):
        fails.append("policy.freshness_days must map claim kinds to a number of days")
    for key in ("kind_sources", "source_classes"):
        v = own.get(key)
        if v is not None and not (isinstance(v, dict) and all(isinstance(x, list) for x in v.values())):
            fails.append(f"policy.{key} must map names to lists")
    for key in ("banned", "required_sections", "review_methods", "label_kinds"):
        if own.get(key) is not None and not isinstance(own[key], list):
            fails.append(f"policy.{key} must be a list")
    for key in ("critical_min_sources", "min_reviews_read", "web_sources_min"):
        if own.get(key) is not None and not (isinstance(own[key], int) and not isinstance(own[key], bool)):
            fails.append(f"policy.{key} must be a whole number")
    for key in ("banned", "required_sections", "review_methods"):
        if isinstance(own.get(key), list) and not all(isinstance(x, str) for x in own[key]):
            fails.append(f"policy.{key} must be a list of text")
    for key in ("kind_sources", "source_classes"):
        if isinstance(own.get(key), dict) and not all(isinstance(x, str) for v in own[key].values() if isinstance(v, list) for x in v):
            fails.append(f"policy.{key} must map names to lists of text")
    if isinstance(own.get("label_kinds"), list) and not all(isinstance(x, (list, tuple)) and len(x) == 2 and all(isinstance(y, str) for y in x) for x in own["label_kinds"]):
        fails.append("policy.label_kinds must be a list of [label word, kind] pairs of text")
    return fails


def policy(spec):
    """The effective policy: the defaults, tightened by whatever the spec asks for. Anything that would loosen a
    default is ignored here and named by policy_fails."""
    p = dict(DEFAULT_POLICY)
    own = spec.get("policy") or {}
    own = {} if policy_type_fails(own) else dict(own)
    tighter = own.get("freshness_days") or {}
    p["freshness_days"] = {k: min(v, tighter.get(k, v)) for k, v in FRESHNESS_DAYS.items()}
    p["critical_min_sources"] = max(CRITICAL_MIN_SOURCES, int(own.get("critical_min_sources", 0) or 0))
    added = {c: [x for x in v if pattern_ok(x) and (c in ADDABLE_CLASSES or reserved(x))] for c, v in (own.get("source_classes") or {}).items()}
    p["source_classes"] = {c: list(SOURCE_CLASSES.get(c, [])) + list(added.get(c, [])) for c in set(SOURCE_CLASSES) | set(added)}
    p["card_classes"] = {c: list(SOURCE_CLASSES.get(c, [])) + [x for x in added.get(c, []) if reserved(x)] for c in set(SOURCE_CLASSES) | set(added)}
    given = own.get("kind_sources") or {}
    p["kind_sources"] = {k: [c for c in v if c in given[k]] if k in given else list(v) for k, v in KIND_SOURCES.items()}
    p["banned"] = list(BANNED) + [str(x).lower() for x in own.get("banned") or [] if str(x).lower() not in BANNED]
    p["required_sections"] = list(REQUIRED_DOSSIER) + [x for x in own.get("required_sections") or [] if x not in REQUIRED_DOSSIER]
    p["min_reviews_read"] = max(DEFAULT_POLICY["min_reviews_read"], int(own.get("min_reviews_read", 0) or 0))
    p["web_sources_min"] = max(DEFAULT_POLICY["web_sources_min"], int(own.get("web_sources_min", 0) or 0))
    methods = own.get("review_methods")
    p["review_methods"] = [m for m in DEFAULT_POLICY["review_methods"] if m in methods] if methods else list(DEFAULT_POLICY["review_methods"])
    extra = [tuple(x) for x in own.get("label_kinds") or [] if len(x) == 2 and x[1] in LABEL_KINDS_ALLOWED]
    p["label_kinds"] = [list(x) for x in LABEL_KIND] + [list(x) for x in extra]   # the defaults win; a spec classifies only what they leave unclassified
    return p


def policy_fails(spec):
    """What the spec's own policy tried to change that it may not: an unknown key, a loosened rule."""
    own = spec.get("policy") or {}
    fails = policy_type_fails(own)
    if fails:
        return fails
    for k in own:
        if k not in POLICY_KEYS:
            fails.append(f"policy.{k} is not a policy key; the gate's rules can't be replaced by the spec (known: {sorted(POLICY_KEYS)})")
    f = own.get("freshness_days") or {}
    fails += [f"policy.freshness_days.{k} {v} is looser than the default {FRESHNESS_DAYS[k]}; a spec can only tighten it"
              for k, v in f.items() if k in FRESHNESS_DAYS and v > FRESHNESS_DAYS[k]]
    if int(own.get("critical_min_sources", CRITICAL_MIN_SOURCES) or 0) < CRITICAL_MIN_SOURCES:
        fails.append(f"policy.critical_min_sources is below {CRITICAL_MIN_SOURCES}")
    if "banned" in own and not set(BANNED) <= {str(x).lower() for x in own["banned"]}:
        fails.append("policy.banned drops default banned phrases; a spec can only add to the list")
    if "required_sections" in own and not set(REQUIRED_DOSSIER) <= set(own["required_sections"]):
        fails.append("policy.required_sections drops a required dossier section; a spec can only add sections")
    if int(own.get("min_reviews_read", DEFAULT_POLICY["min_reviews_read"]) or 0) < DEFAULT_POLICY["min_reviews_read"]:
        fails.append(f"policy.min_reviews_read is below {DEFAULT_POLICY['min_reviews_read']}")
    if int(own.get("web_sources_min", DEFAULT_POLICY["web_sources_min"]) or 0) < DEFAULT_POLICY["web_sources_min"]:
        fails.append(f"policy.web_sources_min is below {DEFAULT_POLICY['web_sources_min']}")
    for k, classes in (own.get("kind_sources") or {}).items():
        wider = [c for c in classes if c not in KIND_SOURCES.get(k, [])]
        if wider:
            fails.append(f"policy.kind_sources.{k} adds {wider}; a spec can only remove source classes from a kind")
    wide = [m for m in own.get("review_methods") or [] if m not in DEFAULT_POLICY["review_methods"]]
    if wide:
        fails.append(f"policy.review_methods adds {wide}; a spec can only remove methods")
    bad = [x for x in own.get("label_kinds") or [] if len(x) != 2 or x[1] not in LABEL_KINDS_ALLOWED]
    if bad:
        fails.append(f"policy.label_kinds {bad} classify a label as something other than a figure kind ({sorted(LABEL_KINDS_ALLOWED)}); a number can't be carried past the comparison under another kind")
    for k, pats in (own.get("source_classes") or {}).items():
        fails += [f"policy.source_classes.{k}: {x!r} is not a full domain such as 'airline.example'" for x in pats if not pattern_ok(x)]
        if k not in ADDABLE_CLASSES:
            fails += [f"policy.source_classes.{k}: {x!r} can't be added; a spec may add sources only to {sorted(ADDABLE_CLASSES)} (the review, booking, map and community classes are fixed)" for x in pats if pattern_ok(x) and not reserved(x)]
            if any(pattern_ok(x) and reserved(x) for x in pats) and "synthetic" not in str(spec.get("footer") or "").lower():
                fails.append(f"policy.source_classes.{k}: reserved example hosts (.test, .example, .invalid) in a fixed class are for synthetic examples only; "
                             f"the page's footer must say it is a synthetic example, or use real sites")
    return fails


def row_classes(row, pol):
    """A card's claims are judged against the fixed classes (plus reserved example hosts): operators a spec adds are for
    transport, plan and booking rows, so a deal site declared an 'airline' cannot back a hotel's price."""
    return pol["card_classes"] if row.get("card") else pol["source_classes"]


def row_owner(row):
    return row.get("owner_photo") if row.get("kind") == "photo" and row.get("owner_photo") else row.get("owner", ())


def source_fail(row, pol, base_dir):
    """Why this row's source isn't allowed for its kind of claim, or None."""
    src = row.get("src") or ""
    allowed = pol["kind_sources"].get(row["kind"], [])
    got = classes_of(src, row_classes(row, pol), row_owner(row))
    if "evidence" in got and not evidence_ok(src, base_dir):
        got.discard("evidence")
    if got & set(allowed):
        return None
    return (f"{row['row']}: a {row['kind']} claim may come from {allowed}; source {src!r} is "
            f"{sorted(got) or 'none of these'}")


def max_age_days(kind, pol=None):
    return (pol or DEFAULT_POLICY)["freshness_days"].get(kind, FRESHNESS_FALLBACK_DAYS)


def parse_time(s):
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    except Exception:
        return None


def capture_age_fail(row, c, pol, at):
    """A capture with no time, or older than its kind's window, is not evidence any more."""
    t = parse_time(c.get("at") or c.get("captured_at") or "")
    if t is None:
        return f"{row['row']}: a capture has no time (at/captured_at), so its freshness can't be checked"
    limit = max_age_days(row["kind"], pol)
    if t - at > dt.timedelta(days=1):
        return f"{row['row']}: capture is dated {t.date()}, in the future; a capture can't be newer than the gate's clock"
    if at - t > dt.timedelta(days=limit):
        return (f"{row['row']}: capture from {t.date()} is older than {limit} days for a {row['kind']} claim; "
                f"capture it again")
    return None


def registrable(host):
    """The site a host belongs to: www.booking.com and secure.booking.com are one source, and so are
    booking.co.uk and www.booking.co.uk."""
    labels = str(host).lower().split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in PUBLIC_SUFFIX_2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:]) if len(labels) >= 2 else str(host).lower()


def source_key(c):
    """What counts as one independent source: the capture's company (booking.com and booking.co.uk are one), an MCP
    server by name, or 'evidence' (all saved confirmations together)."""
    url = str(c.get("url") or c.get("source") or "")
    if url.startswith("mcp:"):
        return url
    if url.startswith("evidence/"):
        return "evidence"   # the builder writes every file in evidence/, so they are one source however many there are
    host = urlparse(url).hostname
    return brand_key(host) if host else None


def policy_version_warns(spec):
    """A spec may declare which policy_version it was built under (top-level, sibling of 'policy' — distinct from
    a spec's own policy overrides). A mismatch is a WARN: it flags that this spec predates a meaning change, without
    retroactively failing it."""
    declared = spec.get("policy_version")
    if declared is not None and declared != POLICY_VERSION:
        return [f"spec declares policy_version {declared}, gate is at {POLICY_VERSION}; re-review recommended"]
    return []


def resolve_fact(it, ptr):
    """A group-fit signal's `fact` points at exactly one fact on its own card: {"facts": j} (the j-th entry of the
    item's facts list) or {"dossier": "<section title>", "index": i} (the i-th entry of that section). Returns
    (ledger field name, the fact entry) or (None, None). A section title alone is not a fact."""
    if not isinstance(ptr, dict):
        return None, None
    if "facts" in ptr:
        facts, j = it.get("facts", []), ptr["facts"]
        if isinstance(j, int) and 0 <= j < len(facts):
            return f"fact{j}:{facts[j].get('label')}", facts[j]
        return None, None
    if "dossier" in ptr:
        sec = next((s for s in it.get("dossier", []) if s.get("title") == ptr["dossier"]), None)
        entries = (sec or {}).get("items") or (sec or {}).get("rows") or []
        i = ptr.get("index")
        e = entries[i] if isinstance(i, int) and 0 <= i < len(entries) else None
        if isinstance(e, (list, tuple)) and len(e) == 2:   # a facts section's [label, value] row
            e = {"label": str(e[0]), "value": e[1]}
        if isinstance(e, dict):
            return f"dossier:{ptr['dossier']}|{i}", e
    return None, None


def fact_words(entry):
    return " ".join(str(entry.get(k, "")) for k in ("label", "value", "text", "name") if entry.get(k) is not None).strip()


def cited_facts(it):
    """Facts a rank-score-v2 signal points at. They become ledger rows (kind 'feature' if they carry no figure) so
    a bump can only rest on a fact that was captured, never on text the builder typed."""
    basis = it.get("score_basis") or {}
    if basis.get("formula") != "rank-score-v2":
        return []
    out = []
    for sig in basis.get("group_fit", []):
        field, entry = resolve_fact(it, sig.get("fact"))
        if field:
            out.append((field, entry))
    return out


CURRENCY_MARKS = (("USD", "US$"), ("CAD", "CA$"), ("MXN", "MX$"), ("AUD", "AU$"), ("AUD", "A$"), ("CAD", "C$"), ("TWD", "NT$"), ("BRL", "R$"), ("HKD", "HK$"),
                  ("NZD", "NZ$"), ("SGD", "S$"), ("EUR", "\u20ac"), ("GBP", "\u00a3"), ("INR", "\u20b9"), ("USD", "$"), ("JPY", "\u00a5"), ("KRW", "\u20a9"),
                  ("THB", "\u0e3f"), ("VND", "\u20ab"), ("PHP", "\u20b1"))
CURRENCY_CODES = ("EUR GBP INR USD JPY CHF AUD CAD AED THB SGD NZD HKD CNY BRL MXN ZAR SEK NOK DKK PLN CZK HUF TRY KRW IDR MYR PHP VND SAR QAR KWD OMR BHD "
                  "EGP MAD LKR NPR PKR BDT ILS ISK RON BGN TWD").split()


LOCAL_MARKS = (("IDR", r"(?<![A-Za-z])Rp\b"), ("MYR", r"(?<![A-Za-z])RM(?=\s?\d)"), ("PLN", "z\u0142"), ("CZK", "K\u010d"), ("ZAR", r"(?<![A-Za-z])R(?=\s?\d)"))


def currency_of(s):
    """The currency a text names: an ISO code (any of the common ones), a mark such as US$, R$ or Rs., or a symbol. When it names several,
    the first one written wins ('$120 (Rs 10,000)' is dollars)."""
    s = str(s)
    found = []   # (position, currency)
    for m in re.finditer(r"(?<![A-Za-z])Rs\b\.?|\bRupees?\b", s, re.I):
        found.append((m.start(), "INR"))
    for code, rx in LOCAL_MARKS:
        for m in re.finditer(rx, s):
            found.append((m.start(), code))
    for code, sym in CURRENCY_MARKS:
        for m in re.finditer(r"(?<![A-Za-z])" + re.escape(sym) if sym[0].isalpha() else re.escape(sym), s):
            found.append((m.start(), code))
    for m in re.finditer(r"(?<![A-Za-z])(EUR|GBP|INR|USD|JPY)(?![A-Za-z])", s, re.I):   # the rarer codes in capitals only: "try", "mad" are words
        found.append((m.start(), m.group(1).upper()))
    for m in re.finditer(r"(?<![A-Za-z])(" + "|".join(CURRENCY_CODES) + r")(?![A-Za-z])", s):
        found.append((m.start(), m.group(1)))
    return min(found)[1] if found else None


def duration_minutes(s):
    """'2 h 15 min', '1h30', '135 min', 'PT1H30M' or a bare number (minutes) as total minutes."""
    iso = re.fullmatch(r"\s*PT(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?\s*", s, re.I)
    if iso and (iso.group(1) or iso.group(2)):
        return float(iso.group(1) or 0) * 60 + float(iso.group(2) or 0)
    h = re.search(r"(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours)\b", s, re.I)
    h2 = re.search(r"(\d+)\s*h(?=\s*\d)", s, re.I)   # '1h30'
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:m|min|mins|minute|minutes)\b", s, re.I)
    if h or h2 or m:
        hours = float((h or h2).group(1)) if (h or h2) else 0.0
        mins = float(m.group(1)) if m else 0.0
        if h2 and not m:
            tail = re.search(r"h\s*(\d+)", s, re.I)
            mins = float(tail.group(1)) if tail else 0.0
        return hours * 60 + mins
    if re.search(r"[A-Za-z]", s):
        return None   # words we don't read ('1 day') are not a number of minutes
    n = re.search(r"\d+(?:\.\d+)?", s)
    return float(n.group()) if n else None


def clock_time(s):
    """HH:MM as (hour, minute) on the 24-hour clock; 'am'/'pm' are honoured, so 9:05 PM is (21, 5)."""
    m = re.search(r"(\d{1,2}):(\d{2})\s*([ap])\.?m\b", s, re.I) or re.search(r"(\d{1,2}):(\d{2})()", s)
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2)), m.group(3).lower()
    if ap == "p" and h < 12:
        h += 12
    if ap == "a" and h == 12:
        h = 0
    return (h, mi) if h <= 23 and mi <= 59 else None


_CODES = "EUR|GBP|INR|USD|JPY|AUD|CAD|CHF|SGD|THB|AED|NZD|HKD|CNY|KRW|MXN|BRL|ZAR|TRY|SEK|NOK|DKK|PLN|CZK"
CUR_NUM = r"(?:[\u20ac$\u00a3\u20b9\u00a5]|\b(?:" + _CODES + r")\b)\s?(\d+(?:\.\d+)?)"
NUM_CUR = r"(\d+(?:\.\d+)?)\s?(?:[\u20ac$\u00a3\u20b9\u00a5]|\b(?:" + _CODES + r")\b)"


MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def date_of(s):
    """An ISO date from a date as a page writes it: 2030-05-07, 7 May 2030, Tue, 7th May 2030, May 7, 2030. None if there is none."""
    s = str(s)
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})(?!\d)", s)
    if m:
        return m.group()
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([A-Za-z]{3})[a-z]*\.?,?\s+(\d{4})\b", s)
    mo = MONTHS.get(m.group(2).lower()) if m else None
    if m and mo:
        return f"{m.group(3)}-{mo:02d}-{int(m.group(1)):02d}"
    m = re.search(r"\b([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", s)
    mo = MONTHS.get(m.group(1).lower()) if m else None
    if m and mo:
        return f"{m.group(3)}-{mo:02d}-{int(m.group(2)):02d}"
    return None


def claim_key(kind, value):
    """The figure a claim is about: the amount (with its currency when one is written) for prices and bookings; total
    minutes for durations; the 24-hour clock time for times; the walking metres and minutes for distances. Text
    around the figure is ignored, so a sentence 'From EUR 890.18, 4 nights' matches a captured '890.18'."""
    s = numfmt.canon(value, money=kind in ("price", "booking"))   # numbers in plain digits whatever the page's locale
    if kind == "time":
        return clock_time(s)
    if kind == "date":
        return date_of(s)
    if kind in TEXT_KINDS:
        return re.sub(r"\s+", " ", s).strip().lower() or None
    if kind == "duration":
        return duration_minutes(s)
    if kind == "distance":
        # metres (from 'N m' or 'N km') and, when stated, the minutes; 'Exit 2' or other digits aren't the figure
        km = re.search(r"(\d+(?:\.\d+)?)\s*km\b", s, re.I); metres = re.search(r"(\d+(?:\.\d+)?)\s*m(?![A-Za-z])", s)
        minutes = re.search(r"(\d+)\s*min(?![A-Za-z])", s)
        dist = float(km.group(1)) * 1000 if km else float(metres.group(1)) if metres else None
        return (round(dist), int(minutes.group(1)) if minutes else None) if dist is not None else None
    if kind in ("price", "booking"):
        near = re.search(CUR_NUM, s) or re.search(NUM_CUR, s)   # '2 nights for EUR 999' is 999, not 2
        m = near or re.search(r"\d+(?:\.\d+)?", s)
        if not m:
            return None
        return (float(m.group(1) if near else m.group()), currency_of(s))
    m = re.search(r"(\d+(?:\.\d+)?)\s*/\s*5\b", s) if kind == "score" else None   # 'Rated 4.5/5, 2 stars off' is 4.5
    m = m or re.search(r"\d+(?:\.\d+)?", s)
    if not m:
        return None
    return float(m.group(1) if m.re.groups else m.group())


def figures_agree(kind, a, b):
    """Two claim keys describe the same figure. A price written with a currency must match the other's currency; one
    side with no currency written (a bare spec number) is compared on the amount alone."""
    if a is None or b is None:
        return False
    if kind in ("price", "booking"):
        return a[0] == b[0] and (b[1] is None or a[1] == b[1])   # a spec amount that names a currency needs a capture that names the same one
    if kind == "distance":
        return a[0] == b[0] and (a[1] is None or b[1] is None or a[1] == b[1])
    return a == b


def sha(path_or_bytes):
    data = path_or_bytes if isinstance(path_or_bytes, bytes) else Path(path_or_bytes).read_bytes()
    return hashlib.sha256(data).hexdigest()


def norm(v):
    """Normalise a claim value for comparison: strip currency symbols, separators, spaces."""
    if v is None:
        return None
    s = re.sub(r"[^\d.\-]", "", str(v).replace(",", "")) if re.search(r"\d", str(v)) and not re.search(r"[A-Za-z]{4,}", str(v)) else None
    try:
        return round(float(s), 2)
    except (TypeError, ValueError):
        return str(v).replace(" ", "").lower()


def items_of(spec):
    for b in spec.get("blocks", []):
        if b.get("type") == "cards":
            for it in b.get("items", []):
                yield b.get("id", "cards"), it


def nonfinite_fails(obj, where="spec"):
    """NaN and Infinity (which json.loads accepts) defeat every comparison the gate makes, so they fail outright."""
    out = []
    if isinstance(obj, float) and not math.isfinite(obj):
        out.append(f"{where} holds {obj}; a number must be finite")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            out += nonfinite_fails(v, f"{where}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out += nonfinite_fails(v, f"{where}[{i}]")
    return out[:5]


def finite(x):
    """float(x), or ValueError for NaN / Infinity / a non-number string."""
    v = float(x)
    if not math.isfinite(v):
        raise ValueError(f"{x!r} is not finite")
    return v


NUMBER_WORDS = re.compile(r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|dozen|half|several|many)\b", re.I)
LIST_KEYS = ("items", "days", "legs", "bookings", "entries", "notes", "options")


def first_number(v):
    """The first number in a value ('€1,299.50' -> 1299.5), or None."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(v) else None
    m = re.search(r"\d+(?:\.\d+)?", re.sub(r"(?<=\d)[,  ](?=\d{3}\b)", "", str(v)))
    return float(m.group()) if m else None


def kpi_value(spec, d):
    """What a hero KPI's `derived` says it is, recomputed from the spec: {"count": "<block id>"} (rows in that block),
    {"distinct": "<block id>.<key>"}, {"min"|"max": "<block id>.<key>"} (numeric, places marked unavailable left out).
    None when the reference does not resolve."""
    if not isinstance(d, dict):
        return None
    fn = next((f for f in ("count", "distinct", "min", "max") if f in d), None)
    if fn is None:
        return None
    bid, _, key = str(d[fn]).partition(".")
    block = next((b for b in spec.get("blocks", []) if b.get("id") == bid), None)
    rows = next((block[k] for k in LIST_KEYS if block and isinstance(block.get(k), list)), None)
    if rows is None or block.get("type") in ("method", "notice", "hero", "research_notes", "decision_log"):
        return None   # only rows the page ranks, plans or books; a count of method or notice lines is not a number about the trip
    if fn == "count":
        return float(len(rows))
    if not derivable_key(block, key, fn):
        return None
    rows = [r for r in rows if isinstance(r, dict) and not r.get("unavailable")]
    vals = [r.get(key) for r in rows]
    if fn == "distinct":
        return float(len({str(v).strip().lower() for v in vals if v not in (None, "")}))
    nums = [n for n in (first_number(v) for v in vals) if n is not None]
    return (min(nums) if fn == "min" else max(nums)) if nums else None


def derivable_key(block, key, fn):
    """A KPI may be derived only from a field the gate itself checks: a card's price or score, or a table column that
    is a ledger figure. 'distinct' may also read a plain label column (the area). Any other key could carry a number typed
    in with no capture behind it."""
    if key in KEY_KINDS and block.get("type") in ("cards", "table"):
        return True
    if block.get("type") == "booking_ledger" and key == "amount":
        return fn in ("min", "max")
    if block.get("type") == "table":
        col = next((c for c in block.get("columns") or [] if isinstance(c, dict) and c.get("key") == key), None)
        if col is None:
            return False
        return fn == "distinct" or kind_of_fact(str(col.get("label", "")), "1") not in ("text", "unclassified")
    return fn == "distinct" and key in ("area", "name", "type", "kind", "currency", "city", "status", "category", "label", "mode", "from", "to")


UNIT_DIGITS = re.compile(r"\b\d+\s*(?:nights?|guests?|adults?|people|persons?|days?|kids?|children)\b", re.I)


def unscored_review_file_fails(spec, base_dir, pol, at):
    """A card with no guest score can still list review files that back quoted reviews and concerns: they get the same
    provenance, hash, duplicate and own-site checks as a scored card's."""
    fails = []
    for _, it in items_of(spec):
        if it.get("score") is not None or base_dir is None:
            continue
        owner = own_hosts([it.get("url"), it.get("home_url")], [], pol["source_classes"])
        for src in (it.get("score_basis") or {}).get("sources", []):
            if not src.get("reviews_file"):
                continue
            if "own_site" in classes_of(src.get("url"), pol["source_classes"], owner):
                fails.append(f"{it.get('name')}: {src.get('name')!r} is the property's own site; its reviews can't back a quote or a concern")
                continue
            load_reviews(base_dir, src, pol, at, it.get("name"), fails, scored=False)
    return fails


def stored_reviews(card, base_dir):
    """{review text, lower-cased: stars} over the review files this card's score_basis lists."""
    out = {}
    owner = own_hosts([card.get("url"), card.get("home_url")], [], SOURCE_CLASSES)
    for s in (card.get("score_basis") or {}).get("sources", []):
        if not s.get("reviews_file") or base_dir is None or "own_site" in classes_of(s.get("url"), SOURCE_CLASSES, owner):
            continue
        try:
            doc = json.loads((Path(base_dir) / str(s["reviews_file"])).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for r in doc.get("reviews", []) if isinstance(doc, dict) else []:
            out[str(r.get("text", "")).strip().lower()] = r.get("stars")
    return out


def display_fails(spec, base_dir):
    """Figures the page shows that no ledger row checks, so they must be derivable or absent: a hero KPI with a digit
    is recomputed from the spec; a badge, price note or area carries no figure; a quoted review is one of the stored
    review files' reviews with its own stars; a table row agrees with its card on availability."""
    fails = []
    for b in spec.get("blocks", []):
        if b.get("type") == "hero":
            for k in b.get("kpis") or []:
                if not isinstance(k, dict):
                    fails.append(f"hero KPI {k!r} is not an object with a label and a value")
                    continue
                v = str(k.get("value", ""))
                if NUMBER_WORDS.search(v) and not re.search(r"\d", v):
                    fails.append(f"hero KPI {k.get('label')!r} shows {v!r}: a KPI states exactly one figure, written in digits, so it can be checked")
                    continue
                if not re.search(r"\d", v):
                    continue
                want = kpi_value(spec, k.get("derived"))
                nums = re.findall(r"\d+(?:\.\d+)?", re.sub(r"(?<=\d)[,\u00a0\u202f](?=\d{3}\b)", "", v))
                if want is None:
                    fails.append(f"hero KPI {k.get('label')!r} shows {v!r} but is not derived from the spec: add "
                                 f"derived {{count|distinct|min|max: '<block id>[.<key>]'}} so the gate can recompute it")
                elif len(nums) != 1 or NUMBER_WORDS.search(v):
                    fails.append(f"hero KPI {k.get('label')!r} shows {v!r}: a KPI states exactly one figure, written in digits, so it can be checked")
                else:
                    decimals = len(nums[0].split(".")[1]) if "." in nums[0] else 0
                    if abs(float(nums[0]) - want) > 0.5 * 10 ** -decimals + 1e-9:   # half a unit of the precision shown
                        fails.append(f"hero KPI {k.get('label')!r} shows {v!r} but the spec gives {want:g}")
        if b.get("type") == "table" and b.get("cards"):
            cards = next((c for c in spec.get("blocks", []) if c.get("id") == b["cards"] and c.get("type") == "cards"), None)
            by_name = {c.get("name"): c for c in (cards or {}).get("items", [])}
            for r in b.get("items", []):
                c = by_name.get(r.get("name"))
                if c is not None and bool(c.get("unavailable")) != bool(r.get("unavailable")):
                    fails.append(f"{r.get('name')}: the table says unavailable={bool(r.get('unavailable'))} but its card says "
                                 f"{bool(c.get('unavailable'))}")
    for _, it in items_of(spec):
        name = it.get("name")
        for bd in it.get("badges") or []:
            if re.search(r"\d", str(bd.get("text", "") if isinstance(bd, dict) else bd)):
                fails.append(f"{name}: badge {bd!r} carries a figure; badges are labels, put a number in a checked fact")
        for fld in ("priceNote", "area"):
            if re.search(r"\d", UNIT_DIGITS.sub("", str(it.get(fld) or ""))):
                fails.append(f"{name}: {fld} {it.get(fld)!r} carries a figure no capture checks; put it in a fact or the price")
        stored = None
        for sec in it.get("dossier") or []:
            if sec.get("kind") == "ratings":
                for e in sec.get("items") or []:
                    if e.get("rank"):
                        fails.append(f"{name}: a rating entry's rank {e.get('rank')!r} is shown but not checked; remove it")
            if sec.get("kind") != "reviews":
                continue
            if sec.get("categories"):
                fails.append(f"{name}: review categories are shown but not checked; remove them or state them as sourced facts")
            stored = stored if stored is not None else stored_reviews(it, base_dir)
            for q in sec.get("items") or []:
                text = str(q.get("text", "")).strip().lower()
                if text not in stored:
                    fails.append(f"{name}: a quoted review is not in any stored review file ({text[:50]!r}); quote only reviews that were read")
                elif q.get("rating") is not None and stored[text] is not None and float(q["rating"]) != float(stored[text]):
                    fails.append(f"{name}: a quoted review shows {q['rating']} stars but the stored review has {stored[text]}")
    return fails


def own_domain_fails(spec):
    """own_domains is the card's own CDN or booking engine; declaring a review platform, booking platform or map host as
    'own' would let it back the property's price and policy claims as if it were the owner."""
    fails = []
    for _, it in items_of(spec):
        for field in ("url", "home_url"):
            h = urlparse(str(it.get(field) or "")).hostname
            if h and not real_domain(h):
                fails.append(f"{it.get('name')}: {field} {it.get(field)!r} is not on a full domain (name.tld); it would widen the property's own site")
        for d in it.get("own_domains") or []:
            if not real_domain(d):
                fails.append(f"{it.get('name')}: own_domains lists {d!r}, which is not a full domain (name.tld); a bare suffix would make every site under it the property's own")
                continue
            hit =classes_of("https://" + str(d).lower().lstrip("."), SOURCE_CLASSES) & {"review_platform", "verified_ota", "maps_measure", "community"}
            if hit:
                fails.append(f"{it.get('name')}: own_domains lists {d!r}, which is a {sorted(hit)[0]} site; a platform can't be the property's own site")
    return fails


BRAND_ALIASES = {"goo": "google"}   # maps.app.goo.gl is Google
# What each review method reads: a file's platform is the method's, so a Google file declared at a Booking url is caught.
REVIEW_METHOD_PLATFORM = {"google-places-api": "google", "apify-google-maps-reviews-scraper": "google", "serpapi-google-maps-reviews": "google",
                          "apify-booking-reviews": "booking", "apify-tripadvisor-reviews": "tripadvisor", "apify-airbnb-reviews": "airbnb"}


def brand_key(host):
    """One company across its country domains: booking.com and www.booking.co.uk are 'booking'."""
    b = registrable(host).split(".")[0]
    return BRAND_ALIASES.get(b, b)


def reserved_host(host):
    return str(host or "").rsplit(".", 1)[-1] in ("test", "example", "invalid")


BARE_NEGATION = {"no", "not", "none", "is", "are", "unavailable", "available", "included", "allowed", "offered", "open", "permitted", "possible", "applicable"}
NEGATORS = {"no", "not", "none", "without", "never", "cannot", "excluded", "except", "lacks", "lacking", "closed", "unavailable"}
STATE_WORDS = {"allowed", "available", "included", "offered", "open", "permitted", "possible", "provided", "applicable", "free"}   # say whether, not what: "Smoking not allowed" is not about "Pets allowed"


def _predicate_negation(plain):
    """A list item or clause that denies the item before it: 'closed for renovation', 'not included in the rate', 'is not available'. 'No prepayment
    needed' and 'no smoking' start a new subject instead."""
    while plain and plain[0] in {"is", "are", "was", "were", "temporarily", "currently", "permanently", "presently", "now", "but", "and", "however", "sadly", "unfortunately",
                                 "also", "still", "usually", "often", "normally", "sometimes", "seasonally"}:
        plain = plain[1:]   # "temporarily closed", "but not available"
    return bool(plain) and (plain[0] in DENIAL_AFTER or bool(DENIAL_AFTER_PHRASES.match(" ".join(plain)))
                            or (plain[0] in {"not", "isn't", "aren't", "cannot", "can't"} and len(plain) > 1 and plain[1] in STATE_WORDS)
                            or set(plain) <= BARE_NEGATION and bool(set(plain) & NEGATORS))


DENIAL_AFTER = {"closed", "shut", "suspended", "renovation", "renovations", "refurbishment", "maintenance", "excluded", "unavailable", "broken", "discontinued", "cancelled",
                "canceled", "decommissioned"}   # within six words after the feature, in its clause
ADJECTIVE_BEFORE = {"a", "an", "the", "in", "on", "with", "this", "our", "its", "their", "your", "any", "each"}   # "a closed courtyard": closed describes a noun
DENIAL_AFTER_PHRASES = re.compile(r"\bout of (?:order|service|use)\b|\bnot (?:working|operational|functioning)\b|\bno longer\b|\bunder (?:renovation|repair|construction|maintenance)\b")


def head_token(value):
    """The last word of 3+ letters of a claim: its head noun ('Heated indoor pool' -> 'pool')."""
    words = re.findall(r"[a-z0-9]{3,}", str(value).lower())
    return words[-1] if words else None


def negated_near(text, tokens, head=None):
    """True when one of `tokens` is denied: a negation within a few words in the same clause ('there is no heated pool', 'pool closed for
    renovation', 'sauna is not available', 'Spa is temporarily closed', 'Pool and gym under renovation'), or a following list item that only
    denies it ('Pool, closed', 'Breakfast, not included in the rate', 'Pool · Not available'). A negation that starts the next sentence or item with
    a new subject ('Pets allowed. No smoking.', 'Free cancellation, no prepayment needed') is not about it. With `head` (the claim's head noun) a clause
    that holds only a minority of the claim's words denies it only through that head noun ('No late check-out' is not about 'Check-in after 3 PM')."""
    clauses = [re.findall(r"[a-z0-9']+", c) for c in re.split(r"\n+|[.!?;·•|]+(?:\s|$)|\s[·•|]\s|,\s+|[()]|\s[-\u2013\u2014]\s", str(text).lower())]
    content = set(tokens) - STATE_WORDS
    for k, words in enumerate(clauses):
        if k and _predicate_negation(words) and any(w in tokens for w in clauses[k - 1]):
            return True
        phrase_denied = bool(DENIAL_AFTER_PHRASES.search(" ".join(words)))
        held = {x for x in words if x in tokens}
        for i, w in enumerate(words):
            if w not in tokens or (content and w in STATE_WORDS):
                continue   # a state word alone ("allowed") is not what is denied: "Smoking not allowed, pets allowed" does not deny pets
            if head and len(held) * 2 <= len(tokens) and w != head:
                continue   # only a minority of the claim is in this clause, and not its head noun
            before, after = words[max(0, i - 4):i], words[i + 1:i + 4]
            if any(x in NEGATORS or x.endswith("n't") for x in before):
                return True
            if any(x in {"closed", "unavailable", "excluded", "none", "no", "not", "false", "never"} or x.endswith("n't") for x in after[:2]) or (after[:1] == ["not"] and after[1:2] in (["available"], ["included"], ["allowed"], ["open"], ["offered"])):
                return True
            if any(x in DENIAL_AFTER and (words[i + n] not in ADJECTIVE_BEFORE) for n, x in enumerate(words[i + 1:i + 7])) or phrase_denied:
                return True
    return False


def feature_agrees(spec_value, capture_value):
    """The capture states what the spec claims: every word of 3+ letters of the claim is in it, or, for a claim with no such
    word ('Ja', '4K'), the two texts match once normalised. Words the capture negates are not looked for here: a
    negation in the capture shows up as a missing token on the claim side, and the reviewer judges the rest."""
    need = text_tokens(spec_value)
    if need:
        return need <= text_tokens(capture_value) and (negated_near(spec_value, need, head_token(spec_value)) or not negated_near(capture_value, need, head_token(spec_value)))
    flat = lambda v: re.sub(r"\W+", " ", str(v).lower()).strip()
    return bool(flat(spec_value)) and flat(spec_value) == flat(capture_value)


def kb_fails(spec, kb_text):
    """The knowledge base (RESEARCH.md) needs a section per card, headed by its name, citing its link and giving a read
    date. Returns the problems; pipeline.py check and audit_report.py both call this."""
    sections = re.split(r"^#{2,3} ", kb_text or "", flags=re.M)[1:]
    fails = []
    for _, it in items_of(spec):
        name = it.get("name", "")
        sec = next((s for s in sections if s.split(chr(10), 1)[0].strip() == name), None)
        if sec is None:
            fails.append(f'RESEARCH.md has no section headed "## {name}"')
        elif it.get("url") and it["url"] not in sec:
            fails.append(f"the RESEARCH.md section for {name} does not cite its link {it['url']}")
        elif not re.search(r"\b(\d{1,2} [A-Z][a-z]{2,8} \d{4}|\d{4}-\d{2}-\d{2})\b", re.sub(r"https?://\S+", " ", sec)):   # a date inside a link is not a read date
            fails.append(f"the RESEARCH.md section for {name} has no read date")
    return fails


def kind_of_fact(label, value="", label_kinds=LABEL_KIND):
    """Classify a fact by its label. A value that carries a number must be classifiable, otherwise the claim
    would silently escape checking: it is returned as 'unclassified', which the gate fails."""
    l = str(label or "").lower()
    for key, kind in label_kinds:
        if kind == "note":   # a question put to the host is not a claim: only a label that STARTS with the key, and only a question with no figure in it
            if l.lstrip().startswith(key) and str(value).strip().endswith("?") and not re.search(r"\d|[\u20a0-\u20bf$\u00a3\u00a5]", str(value)):
                return "note"
            continue
        if re.search(r"\b" + re.escape(key), l):   # the key starts a word: 'rate' is not in 'Separate', 'rail' not in 'trail'
            return kind
    return "unclassified" if re.search(r"\d", str(value)) else "text"


def ledger(spec):
    rows = []
    pol = policy(spec)
    label_kinds = [tuple(x) for x in pol["label_kinds"]]
    for block, it in items_of(spec):
        rank = it.get("rank")
        base = f"{block}:{it.get('id') or it.get('name')}"   # no rank in the id: re-ranking must not orphan the captures
        bk = it.get("url", "")                 # where it is booked (its own site or a platform listing)
        hp = it.get("home_url") or bk          # its own home page, when the booking link is a platform
        owner = own_hosts([bk, it.get("home_url")], [], pol["source_classes"])
        owner_photo = own_hosts([bk, it.get("home_url")], it.get("own_domains"), pol["source_classes"])   # a declared CDN backs photos only
        def add(field, kind, value, src, critical=False):
            rows.append({"row": f"{base}|{field}", "item": it.get("name"), "field": field, "kind": kind,
                         "value": value, "src": src, "critical": critical, "owner": owner, "owner_photo": owner_photo, "card": True})
        add("url", "link", it.get("url"), it.get("url"))
        # A guest score with a score_basis is a judgement made from read reviews: it is checked by score_gate, not by
        # captured figures (no page shows the judgement). A bare score has no basis, so it stays a checked row and fails.
        if it.get("score") is not None and not it.get("score_basis"):
            add("score", "score", it.get("score"), hp)
        if it.get("price") is not None:
            add("price", "price", with_currency(it.get("price"), it.get("currency") or currency_of(str(it.get("priceNote") or ""))), bk)
        for sec in it.get("dossier", []):
            if sec.get("kind") == "photos":
                for i, img in enumerate(sec.get("images") or []):   # the details panel shows these, so they are checked like the card's own
                    add(f"dossier:{sec.get('title')}|photo{i}", "photo", img, img)
            entries = sec.get("items", []) if sec.get("kind") == "ratings" else []
            for k, e in enumerate(entries):
                # One rating in a section keeps its original row id; several (one per platform) each get their own,
                # or every platform's rating would be checked against the others' captures.
                tag = f"dossier:{sec['title']}" if len(entries) == 1 else f"dossier:{sec['title']}#{k}"
                add(f"{tag}|rating", "score", e.get("rating"), e.get("url", ""))
                add(f"{tag}|count", "count", e.get("count"), e.get("url", ""))
            if sec.get("kind") == "facts":
                for k, pair in enumerate(sec.get("rows") or []):
                    if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
                        continue
                    kind = kind_of_fact(str(pair[0]), pair[1], label_kinds)
                    if kind not in ("text", "note"):
                        src = sec.get("source") or next(iter(sec.get("sources") or []), None) or hp
                        add(f"dossier:{sec['title']}|row{k}", kind, pair[1], src)
        for i, img in enumerate(it.get("images", [])):
            add(f"photo{i}", "photo", img, img)
        if it.get("unavailable"):
            add("availability", "availability", "unavailable", bk)
        cited = {field for field, _ in cited_facts(it)}
        for j, f in enumerate(it.get("facts", [])):
            field = f"fact{j}:{f.get('label')}"
            kind = kind_of_fact(f.get("label", ""), f.get("value", ""), label_kinds)
            if kind in ("text", "note"):
                if field not in cited and not f.get("critical"):
                    continue
                kind = "feature"   # a text fact a bump rests on (or marked critical) still needs a capture
            src = f.get("source") or (bk if kind == "price" else hp)
            add(field, kind, f.get("value"), src, critical=bool(f.get("critical")))
        for field, entry in cited_facts(it):
            if field.startswith("dossier:"):
                add(field, "feature", entry.get("value") or entry.get("text") or fact_words(entry), entry.get("url") or hp)
    rows += transport_leg_rows(spec)
    rows += day_plan_rows(spec)
    rows += booking_rows(spec)
    rows += cost_rows(spec)
    rows += table_only_rows(spec, label_kinds)
    rows += table_link_rows(spec)
    return rows


KEY_KINDS = {"price": "price", "cost": "price", "score": "score", "rating": "score"}


def card_value(card, key, label):
    """The card's own figure for a table column, or None when the card doesn't state one."""
    if key in ("price", "cost"):
        return card.get("price")
    if key in ("score", "rating"):
        return card.get("score")
    lab, k = str(label or "").lower(), str(key).lower()
    for f in card.get("facts", []):
        fl = str(f.get("label", "")).lower()
        if fl and (fl == lab or fl == k or lab.startswith(fl) or fl.startswith(k)):
            return f.get("value")
    return None


def table_cells(spec, label_kinds=LABEL_KIND):
    """Every classifiable cell of every table: (block, row, column key, value, kind, card, the card's value or None)."""
    cards = {it.get("name"): it for _, it in items_of(spec)}
    for b in spec.get("blocks", []):
        if b.get("type") != "table":
            continue
        for t in b.get("items", []):
            card = cards.get(t.get("name"))
            for c in b.get("columns", []):
                key, v = c.get("key"), t.get(c.get("key"))
                if key in ("name", "rank") or v in (None, ""):
                    continue
                kind = KEY_KINDS.get(key) or kind_of_fact(c.get("label", ""), v, label_kinds)
                if kind in ("text", "note"):
                    continue
                yield b, t, key, v, kind, card, (card_value(card, key, c.get("label")) if card else None)


def table_only_rows(spec, label_kinds=LABEL_KIND):
    """A table cell the card doesn't state is published unchecked unless it is a ledger row: each such cell (every
    cell of a row with no card, and any column of a carded row the card doesn't carry) becomes a row, sourced from
    the row's own `sources[<column key>]`, else its url. A cell the card does state is compared with it
    (table_drift) instead. The row's name, link and plain-text columns aren't figures."""
    rows, seen = [], set()
    for b, t, key, v, kind, card, cv in table_cells(spec, label_kinds):
        if cv is not None:
            continue
        srcs = t.get("sources") or {}
        rows.append(_row(f"{b.get('id', 'table')}:{t.get('name')}|{key}", t.get("name"), key, kind, v,
                         srcs.get(key) or t.get("url", ""), owner_url=t.get("url")))
    for b in spec.get("blocks", []):
        if b.get("type") != "table":
            continue
        cards = {it.get("name") for _, it in items_of(spec)}
        for t in b.get("items", []):
            if t.get("unavailable") and t.get("name") not in cards:
                srcs = t.get("sources") or {}
                rows.append(_row(f"{b.get('id', 'table')}:{t.get('name')}|availability", t.get("name"), "availability",
                                 "availability", "unavailable", srcs.get("avail") or t.get("url", ""), owner_url=t.get("url")))
    return rows


def table_link_rows(spec):
    """A table row with no card still links somewhere: that link is a claim, so it is a ledger row."""
    rows, cards = [], {it.get("name") for _, it in items_of(spec)}
    for b in spec.get("blocks", []):
        if b.get("type") != "table" or not any(c.get("link") for c in b.get("columns", [])):
            continue
        for t in b.get("items", []):
            if t.get("name") not in cards and t.get("url"):
                rows.append(_row(f"{b.get('id', 'table')}:{t.get('name')}|link", t.get("name"), "link", "link", t["url"], t["url"]))
    return rows


def table_drift(spec, pol):
    """A table row that has a card must agree with it on every figure the card states, and on its rank."""
    fails = []
    for b, t, key, v, kind, card, cv in table_cells(spec, [tuple(x) for x in pol["label_kinds"]]):
        if cv is None:
            continue
        if kind in FIGURE_KINDS:
            if kind in ("price", "booking") and currency_of(str(cv)) and not currency_of(str(v)):
                v = with_currency(v, currency_of(str(cv)))   # a bare table amount is in the card's currency
            ok = figures_agree(kind, claim_key(kind, v), claim_key(kind, cv))
        else:
            ok = str(v).strip().lower() == str(cv).strip().lower()
        if not ok:
            fails.append(f"table {key} for {t.get('name')!r} ({v}) != card ({cv})")
    cards = {it.get("name"): it for _, it in items_of(spec)}
    for b in spec.get("blocks", []):
        if b.get("type") == "table":
            for t in b.get("items", []):
                c = cards.get(t.get("name"))
                if c is not None and t.get("rank") != c.get("rank"):
                    fails.append(f"table rank for {t['name']!r} != card rank")
    return fails


def _row(row, item, field, kind, value, src, critical=False, owner_url=None):
    # owner_url: the venue's or booking's own page, whose site counts as own_site for this row only
    owner = own_hosts([owner_url], [], SOURCE_CLASSES) if owner_url else set()
    return {"row": row, "item": item, "field": field, "kind": kind, "value": value, "src": src, "critical": critical,
            "owner": owner}


def transport_leg_rows(spec):
    """Every figure a transport_leg block states becomes a checkable ledger row, the same way a hotel price does,
    so the gate's host allow-list, capture count, freshness and figure match apply to it.

    Air and rail are door-to-door: the mode's time_minutes is the whole journey and must equal the sum of its
    `segments` (getting to the airport or station, the flight or train itself, getting from it), each captured.
    Air and rail figures are critical (a schedule a traveller acts on), so they need two independent sources."""
    rows = []
    for b in spec.get("blocks", []):
        if b.get("type") != "transport_leg":
            continue
        for leg in b.get("legs", []):
            lid, base = leg.get("label", "leg"), f"{b.get('id', 'transport')}:{leg.get('label', 'leg')}"
            for m in leg.get("modes", []):
                mode = mode_of(m) or "?"
                mbase = f"{base}:{mode}"
                crit, item, src = mode in CRITICAL_MODES, f"{lid} ({mode})", m.get("source", "")
                if m.get("time_minutes") is not None and not (m.get("segments") and mode in CRITICAL_MODES):
                    rows.append(_row(f"{mbase}|time_minutes", item, "time_minutes", "duration", m["time_minutes"], src, crit))
                if m.get("distance_km") is not None:
                    rows.append(_row(f"{mbase}|distance_km", item, "distance_km", "km", m["distance_km"], src))
                if m.get("cost") is not None:
                    rows.append(_row(f"{mbase}|cost", item, "cost", "price", with_currency(m["cost"], m.get("currency")), src, crit))
                for k, seg in enumerate(m.get("segments") or []):
                    if seg.get("time_minutes") is not None:
                        rows.append(_row(f"{mbase}|segment{k}:{seg.get('label', k)}", f"{item} {seg.get('label', k)}",
                                         f"segment{k}", "duration", seg["time_minutes"], seg.get("source", ""),
                                         crit and seg.get("main", False)))
    return rows


def door_to_door_gate(spec):
    """Air and rail must state door-to-door time as the sum of captured segments: a 1 h flight with a 90 min
    airport run each side is a 4 h journey, and comparing it to a 3 h drive on the flight time alone is wrong."""
    fails = []
    for b in spec.get("blocks", []):
        if b.get("type") != "transport_leg":
            continue
        for leg in b.get("legs", []):
            for m in leg.get("modes", []):
                if mode_of(m) not in CRITICAL_MODES or m.get("time_minutes") is None:
                    continue
                where = f"{leg.get('label')} ({m.get('mode')})"
                segs = m.get("segments") or []
                if len(segs) < 3 or sum(1 for s in segs if s.get("main")) != 1:
                    fails.append(f"{where}: {m['mode']} needs door-to-door segments - getting there, the main "
                                 f"journey (marked main: true), getting away - each with a captured time")
                    continue
                try:
                    total = sum(float(s["time_minutes"]) for s in segs)
                except (KeyError, TypeError, ValueError):
                    fails.append(f"{where}: every segment needs a numeric time_minutes")
                    continue
                if abs(total - float(m["time_minutes"])) > 1:
                    fails.append(f"{where}: time_minutes {m['time_minutes']} is not the sum of its segments ({total:g})")
    return fails


def day_plan_rows(spec):
    """A stop at a fixed time (a flight, a train, a reservation, a ticketed event, or any stop marked fixed) states
    a fact the traveller acts on; its time and any price become checked rows. A loose plan ('lunch around 1') is
    not a fact and is not checked here - the Plan reviewer judges whether it is feasible."""
    rows = []
    for b in spec.get("blocks", []):
        if b.get("type") != "day_plan":
            continue
        for d in b.get("days", []):
            day = d.get("date") or d.get("label", "?")
            for k, s in enumerate(d.get("stops", [])):
                if s.get("status") in OFF_STATUS:
                    continue   # a rejected or cancelled option is listed with its reason, not acted on
                crit = s.get("type") in CRITICAL_STOP_TYPES or bool(s.get("fixed"))
                base, src, item = f"{b.get('id', 'days')}:{day}:{k}:{s.get('name')}", s.get("source") or s.get("url", ""), f"{day} {s.get('name')}"
                if crit and s.get("time"):
                    rows.append(_row(f"{base}|time", item, "time", "time", s["time"], src, True, s.get("url")))
                if s.get("price") is not None:
                    rows.append(_row(f"{base}|price", item, "price", "price", s["price"], src, crit, s.get("url")))
    return rows


def with_currency(amount, currency):
    """'189 EUR' for a bare number and a stated currency, so the currency is part of what is checked."""
    if amount is None or not currency or re.search(r"[A-Za-z\u20a0-\u20bf$]", str(amount)):
        return amount
    return f"{amount} {str(currency).upper()}"


def booking_rows(spec):
    """A booking's amount and cancellation date are critical facts: each is checked against captured evidence (the
    booking page, or a confirmation saved under evidence/), not only summed."""
    rows = []
    for b in spec.get("blocks", []):
        if b.get("type") != "booking_ledger":
            continue
        for bk in b.get("bookings", []):
            base, src = f"{b.get('id', 'bookings')}:{bk.get('name')}", bk.get("source") or bk.get("url", "")
            if bk.get("amount") is not None:
                rows.append(_row(f"{base}|amount", bk.get("name"), "amount", "booking", with_currency(bk["amount"], bk.get("currency")), src, True, bk.get("url")))
            for field in ("cancel_by", "check_in", "check_out", "date"):   # every date the page shows for a booking is a fact someone acts on
                if bk.get(field):
                    rows.append(_row(f"{base}|{field}", bk.get("name"), field, "date", bk[field], src, True, bk.get("url")))
    return rows


def cost_rows(spec):
    """A cost_comparison option's cash price is a checked price; its points price is quoted from the operator's own
    page. The mechanic (when and how the money is collected) is prose, judged by the facts reviewer."""
    rows = []
    for b in spec.get("blocks", []):
        if b.get("type") != "cost_comparison":
            continue
        for o in b.get("options", []):
            base, src, item = f"{b.get('id', 'cost')}:{o.get('label')}", o.get("source") or o.get("url", ""), o.get("label")
            cash = o.get("cash") or {}
            if cash.get("amount") is not None:
                rows.append(_row(f"{base}|cash", item, "cash", "price", with_currency(cash["amount"], cash.get("currency")), src, bool(o.get("critical")), o.get("url")))
            pts = o.get("points") or {}
            if pts.get("amount") is not None:
                rows.append(_row(f"{base}|points", item, "points", "points", pts["amount"], src, bool(o.get("critical")), o.get("url")))
    return rows


ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ].*)?")


def iso_date(v):
    """A date as written in ISO form (YYYY-MM-DD, optionally with a time), or None."""
    s_ = str(v or "")
    if not ISO_DATE.fullmatch(s_):
        return None
    try:
        return dt.date.fromisoformat(s_[:10])
    except ValueError:
        return None


def date_fails(spec):
    """Every date the gate compares must be an ISO date; '5 Nov 2030' would compare as text and pass or fail at random."""
    fails = []
    def check(where, v):
        if v not in (None, "") and iso_date(v) is None:
            fails.append(f"{where}: {v!r} is not an ISO date (YYYY-MM-DD)")
    check("spec.as_of", spec.get("as_of"))
    for k in ("start", "end"):
        check(f"spec.trip.{k}", (spec.get("trip") or {}).get(k))
    for b in spec.get("blocks", []):
        for d in b.get("days", []) if b.get("type") == "day_plan" else []:
            check(f"day {d.get('label', '?')}", d.get("date"))
        for bk in b.get("bookings", []) if b.get("type") == "booking_ledger" else []:
            for k in ("check_in", "check_out", "date", "cancel_by"):
                check(f"booking {bk.get('name')!r} {k}", bk.get(k))
    return fails


def booking_ledger_gate(spec):
    """Financial-arithmetic checks for a booking_ledger block: do the booking rows sum to the stated total per
    currency, and is a booking's cancellation status still consistent with the spec's own 'as_of' date. build_page's
    validate_spec catches the sum mismatch structurally; this runs it again so it also holds for any caller that
    reaches the gate directly."""
    fails = []
    as_of = spec.get("as_of")
    for b in spec.get("blocks", []):
        if b.get("type") != "booking_ledger":
            continue
        if not as_of and any(bk.get("status") == "upcoming" and bk.get("cancel_by") for bk in b.get("bookings", [])):
            fails.append(f"{b.get('id')}: bookings with a cancel_by date need the spec's as_of date, or the "
                         f"cancellation check cannot run (it is never skipped silently)")
        sums = {}
        for bk in b.get("bookings", []):
            cur = bk.get("currency", "")
            if bk.get("status") not in BOOKING_STATUSES:
                fails.append(f"{b.get('id')}: booking {bk.get('name')!r} has status {bk.get('status')!r}; use one of {sorted(BOOKING_STATUSES)}")
            try:
                amount = finite(bk.get("amount", 0))
                if bk.get("status") not in OFF_STATUS:   # a cancelled or rejected booking is on record but costs nothing
                    sums[cur] = sums.get(cur, 0.0) + amount
            except (TypeError, ValueError):
                fails.append(f"{b.get('id')}: booking {bk.get('name')!r} has a non-numeric amount {bk.get('amount')!r}")
            if bk.get("cancel_by") and bk.get("check_in") and str(bk["cancel_by"])[:10] > str(bk["check_in"])[:10]:
                fails.append(f"{b.get('id')}: booking {bk.get('name')!r} has cancel_by {bk['cancel_by']} after check_in "
                             f"{bk['check_in']}: free cancellation cannot outlast the stay's start")
            if bk.get("status") == "upcoming" and bk.get("cancel_by") and as_of and str(bk["cancel_by"]) < str(as_of):
                fails.append(f"{b.get('id')}: booking {bk.get('name')!r} has cancel_by {bk['cancel_by']} in the past "
                             f"(as_of {as_of}) but status is still 'upcoming' - update status or cancel_by")
        summary = b.get("summary") or {}
        for cur, total in summary.items():
            try:
                want = round(float(total), 2)
            except (TypeError, ValueError):
                fails.append(f"{b.get('id')}: summary[{cur!r}] {total!r} is not numeric")
                continue
            got = round(sums.get(cur, 0.0), 2)
            if abs(got - want) > 0.01:
                fails.append(f"{b.get('id')}: summary total {want} {cur} does not match the sum of booking amounts ({got} {cur})")
        for cur in sums:
            if cur not in summary:
                fails.append(f"{b.get('id')}: bookings sum to {round(sums[cur], 2)} {cur} but summary has no total for {cur!r}")
    return fails


NOT_PROSE_KEYS = {"url", "home_url", "source", "sources", "id", "images", "image", "hero_image", "photo", "own_domains", "reviews_file",
                  "score_basis", "mode", "type", "status", "kind", "currency",
                  "derived", "key", "cards", "link", "href", "icon", "rank", "policy", "brief", "manifest", "sha256", "raw_sha256"}
URLISH = re.compile(r"^\s*(https?://|evidence/|mcp:)", re.I)
DATEY_KEYS = {"date", "time", "check_in", "check_out", "cancel_by"}   # skipped only when the value is a clock time or an ISO date; free text there is prose
DATEY = re.compile(r"^\s*(\d{4}-\d{2}-\d{2}([T ][\d:.+\-Z]*)?|\d{1,2}[:.]\d{2}\s*([ap]\.?m\.?)?)\s*$", re.I)


def prose_fields(spec):
    """Every string the page can show, with where it sits: any text in a block (a card's verdict, a fact's value, a table
    cell, a KPI label, a booking note, a recommendation's reasons...), the footer and the design's brand and ethos. Links,
    ids, hashes, dates and the policy are not prose. A banned phrase is refused wherever the reader would meet it."""
    out = []

    def walk(node, owner, path):
        if isinstance(node, str):
            if node.strip() and not URLISH.match(node):
                out.append((owner, path, node))
        elif isinstance(node, dict):
            name = node.get("name") if isinstance(node.get("name"), str) else owner
            for k, v in node.items():
                if k in DATEY_KEYS and isinstance(v, str) and DATEY.match(v):
                    continue
                if k not in NOT_PROSE_KEYS and not str(k).endswith("_sha256"):
                    walk(v, name, f"{path}.{k}" if path else str(k))
        elif isinstance(node, (list, tuple)):
            for i, v in enumerate(node):
                walk(v, owner, f"{path}[{i}]")

    for i, b in enumerate(spec.get("blocks", []) if isinstance(spec.get("blocks"), list) else []):
        walk(b, b.get("title", b.get("type")) if isinstance(b, dict) else None, f"blocks[{i}]")
    for k in ("title", "subtitle", "footer", "description"):
        if isinstance(spec.get(k), str):
            out.append(("page", k, spec[k]))
    design = spec.get("design") if isinstance(spec.get("design"), dict) else {}
    for k in ("brand", "ethos", "tagline"):
        if isinstance(design.get(k), str):
            out.append(("design", k, design[k]))
    return out


def rail_gate(spec, rail):
    """Every 'rail' fact must be derived from a Google Maps measurement (rail_distance.py output), not typed from
    an operator page. rail is {item id: record}, keyed by the card's own `id` (or its name). Returns failures."""
    fails = []
    if rail is None:
        for _, it in items_of(spec):
            for f in it.get('facts', []):
                if re.search(r"\brail", f.get('label', '').lower()):
                    fails.append(f"{it.get('name')}: rail fact {f.get('value')!r} has no Maps validation (run rail_distance.py)")
        return fails
    for _, it in items_of(spec):
        rec = rail.get(str(it.get('id') or it.get('name')))
        for f in it.get('facts', []):
            if not re.search(r"\brail", f.get('label', '').lower()):
                continue
            if not rec or 'nearest' not in rec or not rec['nearest']:
                fails.append(f"{it.get('name')}: no Maps rail measurement ({(rec or {}).get('not_found', 'missing')})")
                continue
            n = rec['nearest']; val = str(f.get('value', ''))
            got_ = claim_key('distance', val)
            if got_ is None or got_ != (int(n['walk_m']), int(n['walk_min'])) or 'Google Maps' not in val:
                fails.append(f"{it.get('name')}: rail fact {val!r} does not match Maps ({n['walk_m']} m, {n['walk_min']} min, {n['name']})")
    return fails


def cap_agrees(kind, spec_value, c):
    """One stored capture states what the spec claims, by the rules of the claim's kind."""
    if kind == "availability":
        return ("unavailable" in str(c["value"]).lower()) == ("unavailable" in str(spec_value).lower())
    if kind == "photo":
        # the photo is the claim: the capture is of that image and it answered HTTP 200
        return str(c.get("url", "")).strip() == str(spec_value).strip() and c.get("http_status") == 200
    if kind == "link":
        # the link is the claim: the capture is of that very page, and the page answered (a stored 404 is a dead link)
        st = c.get("http_status")
        return str(c.get("url", "")).strip() == str(spec_value).strip() and isinstance(st, int) and not isinstance(st, bool) and 200 <= st < 300
    if kind == "count":
        base = claim_key("count", spec_value)
        got = claim_key("count", c["value"])
        return base is not None and got is not None and abs(got - base) <= max(1, COUNT_TOLERANCE * base)
    if kind == "feature":
        return feature_agrees(spec_value, c["value"])
    want = claim_key(kind, spec_value)
    return want is not None and figures_agree(kind, claim_key(kind, c["value"]), want)


def store_agrees(row, kind, spec_value, store):
    """Recompute agreement from stored, usable, fresh captures; the spot file's own flag is not trusted."""
    import capture_store as cs
    caps = [c for c in cs.load(store, row) if cs.usable(c) and cs.fresh(c, kind)]
    if kind == "distance":
        # distances are measured by Google Maps only; operator-page readings do not count
        caps = [c for c in caps if "google" in c.get("url", "") or "maps" in c.get("url", "")]
    return bool(caps) and all(cap_agrees(kind, spec_value, c) for c in caps)


def load_reviews(base_dir, s, pol, at, name, fails, scored, meta=None):
    """Read one source's stored review file and check it: provenance (method, retrieved_at), freshness, the read
    count, and every review's text against its hash. A scored source must also come by a method the policy allows.
    Returns the reviews, or None if the file can't be used."""
    label = s.get("name")
    path = Path(base_dir) / str(s.get("reviews_file", ""))
    if not s.get("reviews_file") or not path.exists():
        fails.append(f"{name}: {label!r} has no review file ({s.get('reviews_file')!r})")
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        fails.append(f"{name}: {label!r} review file can't be read ({type(e).__name__}); it must be a JSON object with method, retrieved_at and reviews")
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("reviews"), list) or not doc.get("method") or not all(isinstance(r, dict) for r in doc["reviews"]):
        fails.append(f"{name}: {label!r} review file has no provenance block (method, retrieved_at, reviews)")
        return None
    if meta is not None:
        meta["method"] = doc.get("method")
    if scored and doc.get("method") not in pol["review_methods"]:
        fails.append(f"{name}: {label!r} reviews were obtained by {doc.get('method')!r}; the policy allows {pol['review_methods']}")
        return None
    got_at = parse_time(doc.get("retrieved_at") or "")
    if got_at is None:
        fails.append(f"{name}: {label!r} review file has no retrieved_at, so its freshness can't be checked")
    elif got_at - at > dt.timedelta(days=1):
        fails.append(f"{name}: {label!r} review file's retrieved_at {got_at.date()} is in the future")
    elif at - got_at > dt.timedelta(days=max_age_days("score", pol)):
        fails.append(f"{name}: {label!r} reviews were read on {got_at.date()}, older than "
                     f"{max_age_days('score', pol)} days; read them again")
    revs = doc["reviews"]
    if not isinstance(s.get("read"), int) or isinstance(s.get("read"), bool) or s.get("read") != len(revs):
        fails.append(f"{name}: {label!r} says read={s.get('read')} but the file holds {len(revs)}")
    ids = [str(r.get("id")) for r in revs]
    dup_ids = sorted({i for i in ids if ids.count(i) > 1})
    texts = [str(r.get("text", "")).strip().lower() for r in revs if len(str(r.get("text", "")).strip()) >= 40]
    dup_text = len(texts) - len(set(texts))
    if dup_ids or dup_text:
        fails.append(f"{name}: {label!r} review file repeats reviews (duplicate ids {dup_ids[:3]}, {dup_text} repeated text(s)); "
                     f"a review counts once")
        return None
    bad = [r.get("id") for r in revs if hashlib.sha256(str(r.get("text", "")).encode("utf-8")).hexdigest() != r.get("sha256")]
    if bad:
        fails.append(f"{name}: {label!r} reviews {bad[:3]} do not match their stored text hash (edited after reading?)")
    return revs


def rated(revs):
    """The reviews that carry a 1-5 star rating; a review with only text counts for themes, not for the mean."""
    return [r for r in revs if isinstance(r.get("stars"), (int, float)) and not isinstance(r.get("stars"), bool) and 1 <= r["stars"] <= 5]


def mean_stars(revs):
    got = rated(revs)
    return sum(r["stars"] for r in got) / len(got) if got else None


def guest_score_v2(sources, base_dir, pol, at, owner, name, fails):
    """rank-score-v2's guest score from a card's review sources: the weighted mean over the sources that count (a review
    platform or verified-stay site, never the property's own site, read through an allowed method, with at least
    min_reviews_read rated reviews). Returns (guest score or None, the names of the counted sources, {source: review ids});
    problems are appended to `fails`. The one implementation: the gate and stay_spec both call it."""
    classes, mn = pol["source_classes"], pol["min_reviews_read"]
    ids_by_source, counted = {}, []
    weighted, unavailable, seen_files = [], [s for s in sources if s.get("unavailable")], set()
    counted_brands, counted_hashes = {}, {}
    for s in sources:
        if not s.get("reviews_file"):
            continue
        key_ = str((Path(base_dir) / str(s["reviews_file"])).resolve())
        if key_ in seen_files:
            fails.append(f"{name}: {s.get('name')!r} lists review file {s['reviews_file']!r} again; one file is one source")
            continue
        seen_files.add(key_)
        got = classes_of(s.get("url"), classes, owner)
        if "own_site" in got:
            fails.append(f"{name}: {s.get('name')!r} is the property's own site; its reviews are self-curated "
                         f"and can't feed a score or a concern")
            continue
        w = max((SCORE_WEIGHTS[c] for c in got if c in SCORE_WEIGHTS), default=0)
        if s.get("for_score") is False:
            w = 0   # read for themes and quotes only (a browser read of the listing's own reviews): never a scored source
        meta = {}
        revs = load_reviews(base_dir, s, pol, at, name, fails, scored=bool(w), meta=meta)
        if revs is None:
            continue
        ids_by_source[s.get("name")] = {str(r.get("id")) for r in revs}
        if w and len(rated(revs)) >= mn:   # the minimum counts reviews that carry stars
            host = urlparse(str(s.get("url") or "")).hostname
            expected = REVIEW_METHOD_PLATFORM.get(meta.get("method"))
            brand = brand_key(host) if host else str(s.get("name"))
            if expected and host and not reserved_host(host) and brand != expected:
                fails.append(f"{name}: {s.get('name')!r} was read by {meta.get('method')} (a {expected} reader) but its url is {brand}; "
                             f"a source's file must come from the platform its url names")
                continue
            brand = expected or brand   # independence is by the platform the reviews were read from
            hashes = {hashlib.sha256(str(r.get("text", "")).strip().lower().encode("utf-8")).hexdigest() for r in revs}
            if brand in counted_brands:
                fails.append(f"{name}: {s.get('name')!r} and {counted_brands[brand]!r} are the same platform ({brand}); two files "
                             f"from one site are one source")
                continue
            twin = next((n for n, h in counted_hashes.items() if len(h & hashes) >= max(3, len(hashes) // 5)), None)
            if twin:
                fails.append(f"{name}: {s.get('name')!r} repeats reviews already counted from {twin!r}; a review counts once")
                continue
            counted_brands[brand] = s.get("name")
            counted_hashes[s.get("name")] = hashes
            weighted.append((w, mean_stars(revs)))
            counted.append(s.get("name"))
    if not counted:
        fails.append(f"{name}: no review source counts toward the score (needs a review platform or verified-stay "
                     f"booking platform with at least {mn} reviews read)")
        return None, counted, ids_by_source
    if len(counted) + len(unavailable) < MIN_SCORE_SOURCES:
        fails.append(f"{name}: the score rests on {len(counted)} source(s); read at least {MIN_SCORE_SOURCES}, "
                     f"or declare the missing one {{'name', 'unavailable': reason}}")
        return None, counted, ids_by_source
    return round(sum(w * m for w, m in weighted) / sum(w for w, _ in weighted), 1), counted, ids_by_source


def shared_id_fails(name, ids_by_source):
    """A review id must belong to one review file of the card: with the same id in two files, one complaint would count
    for both sources. Ids are unique within a file only, so cross-file repeats are refused."""
    seen, out = {}, []
    for src, ids in ids_by_source.items():
        for i in ids:
            if i in seen and seen[i] != src:
                out.append(i)
            seen.setdefault(i, src)
    return [f"{name}: review id(s) {sorted(set(out))[:3]} appear in more than one review file; ids must be unique across a card's files "
            f"(prefix them with the source, e.g. 'google-12'), or one review would count for two sources"] if out else []


def score_gate(spec, base_dir, pol, at=None):
    """A guest score is a judgement made from reviews that were read, never a headline rating copied across.

    rank-score-v2 (current): the guest score is drawn from independent review platforms and verified-stay booking
    platforms only - never the property's own site, never community posts. Verified-stay sources weigh 1.5, other
    review platforms 1.0, and a source counts once at least min_reviews_read of its reviews were read (a thinner
    source can still feed themes and concerns). At least MIN_SCORE_SOURCES sources must count, or the missing ones
    are declared {"name", "unavailable": reason}. Then bounded group-fit bumps, each pointing at one captured fact
    and naming a trait the group stated or a universal signal.

    Always: a score_basis with method and rubric, web research with an https url and a read date, review files with
    provenance, fresh, hash-checked, and themes citing review ids that were read, drawing on every counted source."""
    fails = []
    mn = pol["min_reviews_read"]
    at = at or dt.datetime.now(dt.timezone.utc)
    criteria = fit_criteria(spec)
    classes = pol["source_classes"]
    for _, it in items_of(spec):
        if it.get("score") is None:
            continue
        name, basis = it.get("name"), it.get("score_basis")
        if base_dir is None:
            fails.append(f"{name}: score check needs the project folder (review files are read from it); none was given")
            continue
        if not basis:
            fails.append(f"{name}: score {it['score']} has no score_basis. A guest score must be read from reviews; "
                         f"a headline rating is not a guest score.")
            continue
        if basis.get("method") != "read-reviews":
            fails.append(f"{name}: score_basis method {basis.get('method')!r} is not 'read-reviews'")
        if not str(basis.get("rubric", "")).strip():
            fails.append(f"{name}: score_basis has no rubric (say how the number was reached)")
        formula = basis.get("formula")
        if formula is None:
            fails.append(f"{name}: score_basis has no formula; state one of {sorted(KNOWN_FORMULAS)} so the score can be recomputed")
            continue
        if formula not in KNOWN_FORMULAS:
            fails.append(f"{name}: score_basis formula {formula!r} is not recognised (known: {sorted(KNOWN_FORMULAS)})")
            continue
        sources = basis.get("sources", [])
        owner = own_hosts([it.get("url"), it.get("home_url")], it.get("own_domains"), classes)
        want_guest, counted, ids_by_source = guest_score_v2(sources, base_dir, pol, at, owner, name, fails)
        web = [s for s in sources if not s.get("reviews_file") and not s.get("unavailable")
               and str(s.get("url", "")).startswith("https://") and s.get("read_at")]
        if len(web) < pol["web_sources_min"]:
            fails.append(f"{name}: needs {pol['web_sources_min']} web research source(s) with an https url and a read_at date")
        if want_guest is not None:
            fails += rank_fails(it, basis, formula, want_guest, criteria, classes)
        themes = basis.get("themes", [])
        if not themes:
            fails.append(f"{name}: score_basis lists no themes drawn from the reviews")
        fails += shared_id_fails(name, ids_by_source)
        all_ids = set().union(*ids_by_source.values()) if ids_by_source else set()
        drawn = set()
        for t in themes:
            cited = [str(c) for c in t.get("review_ids", [])]
            if not cited:
                fails.append(f"{name}: theme {t.get('text', '')[:40]!r} cites no review ids")
                continue
            unknown = [c for c in cited if c not in all_ids]
            if unknown:
                fails.append(f"{name}: theme {t.get('text', '')[:40]!r} cites review ids not in the read files: {unknown[:3]}")
            drawn |= {src for src, ids in ids_by_source.items() if ids & set(cited)}
        for n in counted:
            if n not in drawn:
                fails.append(f"{name}: no theme draws on {n!r}")
    return fails


def fit_criteria(spec):
    """The fit profile the user agreed, as {id: {weight, sign, evidence}}. A confirmed group trait (older specs) is a
    + criterion at the full weight that needs a captured fact."""
    out = {t: {"weight": MAX_SIGNAL_BUMP, "sign": 1, "evidence": "fact"} for t in spec.get("group_traits") or []}
    for c in spec.get("fit_criteria") or []:
        out[c.get("id")] = {"weight": float(c.get("weight", 0)), "sign": POLARITIES.get(c.get("polarity"), 0),
                            "evidence": c.get("evidence", "fact")}
    return out


def rank_fails(it, basis, formula, want_guest, criteria, classes):
    """Rank score = guest score + contextual overlays, clamped to 1-5. basis.guest_score keeps the reviews-only
    figure visible and must match the recomputed guest score. Each overlay in basis.group_fit:
      {"signal": what, "criterion": <fit criterion id> | "universal:<quality>", "bump": signed number,
       "fact": {"facts": j} | {"dossier": title, "index": i}, or for a drawback "concern": <index into concerns>,
       "reason": one line}
    The code checks structure. Whether the fact really delivers what the criterion wants, and whether the size is
    fair, is the judgement reviewer's call."""
    fails, name = [], it.get("name")
    guest_val = basis.get("guest_score")
    if guest_val is None:
        fails.append(f"{name}: {formula} basis has no guest_score (the reviews-only figure must stay visible)")
    elif not isinstance(guest_val, (int, float)) or not math.isfinite(guest_val) or abs(float(guest_val) - want_guest) > 0.05:
        fails.append(f"{name}: basis guest_score {guest_val} is not the guest score recomputed from the stored reviews ({want_guest})")
    signals = basis.get("group_fit", [])
    concerns = basis.get("concerns", [])
    total = 0.0
    per_criterion, used = {}, {}
    for sig in signals:
        label = sig.get("signal", "?")
        try:
            bump = finite(sig.get("bump", 0))
        except (TypeError, ValueError):
            fails.append(f"{name}: overlay {label!r} has a non-numeric bump {sig.get('bump')!r}")
            continue
        crit_id = sig.get("criterion") or sig.get("trait")
        crit = criteria.get(crit_id)
        if crit is None and UNIVERSAL.match(str(crit_id or "")):
            crit = {"weight": MAX_SIGNAL_BUMP, "sign": 0, "evidence": "fact"}
        if crit is None:
            fails.append(f"{name}: overlay {label!r} answers {crit_id!r}, which is neither a criterion of the agreed fit "
                         f"profile {sorted(criteria)} nor a universal:<quality>")
            continue
        if not str(sig.get("reason", "")).strip():
            fails.append(f"{name}: overlay {label!r} gives no reason")
            continue
        limit = min(crit["weight"], MAX_SIGNAL_BUMP)
        if bump == 0 or abs(bump) > limit + 1e-9:
            fails.append(f"{name}: overlay {label!r} is {bump:+g}; it must be non-zero and at most {limit:g} either "
                         f"way for {crit_id!r}")
            continue
        if crit["sign"] and (bump > 0) != (crit["sign"] > 0):
            fails.append(f"{name}: overlay {label!r} is {bump:+g} but {crit_id!r} is a "
                         f"{'quality (+)' if crit['sign'] > 0 else 'drawback (-)'}")
            continue
        field = None
        if bump < 0 and "concern" in sig:
            i = sig.get("concern")
            if not (isinstance(i, int) and 0 <= i < len(concerns)):
                fails.append(f"{name}: drawback {label!r} points at concern {i!r}, which isn't in this card's concerns")
                continue
        else:
            field, entry = resolve_fact(it, sig.get("fact"))
            if not field:
                fails.append(f"{name}: overlay {label!r} points at {sig.get('fact')!r}, which is not one fact on this "
                             f"card (use {{'facts': j}} or {{'dossier': title, 'index': i}})")
                continue
            words_ = fact_words(entry)
            if not words_ or (bump > 0 and NEGATED.search(str(entry.get("value", "") or entry.get("text", "")))):
                fails.append(f"{name}: overlay {label!r} rests on {words_!r}, which says the thing is absent or says "
                             f"nothing - it cannot earn a bump")
                continue
            if crit["evidence"] == "maps" and "maps_measure" not in classes_of(entry.get("source") or entry.get("url"), classes):
                fails.append(f"{name}: overlay {label!r} answers {crit_id!r}, which must be measured on a map; the fact "
                             f"it points at isn't a map measurement")
                continue
        point = ("concern", sig.get("concern")) if (bump < 0 and "concern" in sig) else ("fact", field)
        if point in used:
            fails.append(f"{name}: overlays {used[point]!r} and {label!r} rest on the same {point[0]}; one fact earns one overlay")
            continue
        used[point] = label
        per_criterion[crit_id] = per_criterion.get(crit_id, 0.0) + bump
        total += bump
    for cid, got in per_criterion.items():
        cap_ = min(criteria.get(cid, {"weight": MAX_SIGNAL_BUMP})["weight"], MAX_SIGNAL_BUMP)
        if abs(got) > cap_ + 1e-9:
            fails.append(f"{name}: overlays on {cid!r} add up to {got:+g}; the criterion's weight is {cap_:g} in total, "
                         f"however many overlays it is split into")
    total = max(-MAX_TOTAL_GROUP_FIT_BUMP, min(MAX_TOTAL_GROUP_FIT_BUMP, total))
    want_rank = max(RANK_SCORE_FLOOR, min(RANK_SCORE_CAP, round(want_guest + total, 1)))
    if not isinstance(it['score'], (int, float)) or not math.isfinite(it['score']) or abs(float(it['score']) - want_rank) > 0.05:
        fails.append(f"{name}: score {it['score']} is not guest_score {want_guest} + overlays ({total:+g}, capped at "
                     f"+/-{MAX_TOTAL_GROUP_FIT_BUMP:g}) = {want_rank}")
    return fails


MIN_READ_FOR_PERCENT_BRANCH = 20   # the percentage corroboration branch never fires below this many reviews read
CONCERN_PERCENT_FLOOR = 0.15       # ...and even then needs this share of the reviews actually read to cite it


def concern_gate(spec, base_dir, pol):
    """A concern isn't surfaced on the strength of one isolated, unrepresentative review. Each entry in a card's
    score_basis.concerns must be corroborated across >=2 independent sources, OR (only once at least
    MIN_READ_FOR_PERCENT_BRANCH reviews have been read) cited by >=15% of the reviews read. The percentage branch
    needs the floor: on a small read set, one reviewer's repeated complaint could clear 15% without independent
    corroboration."""
    fails = []
    for _, it in items_of(spec):
        basis = it.get("score_basis") or {}
        # A review-derived watch-out ({"text", "review_ids"}) is a concern shown in another place: same rule. One
        # marked isolated: true is allowed through, because the page labels it as a single report. A plain-string
        # watch-out is a practical caveat (steep stairs), not a review complaint; the Judgement reviewer checks that
        # no complaint is hiding in one.
        concerns = list(basis.get("concerns", []))
        concerns += [w for w in it.get("watch", []) if isinstance(w, dict) and not w.get("isolated")]
        if not concerns:
            continue
        name = it.get("name")
        # Every review source with a stored, provenanced file can corroborate - Google, TripAdvisor, Reddit, X,
        # forums - not only the sources the guest score is computed from. Ids must be ones actually read.
        ids_by_source, platform_of = {}, {}
        owner = own_hosts([it.get("url"), it.get("home_url")], [], pol["source_classes"])
        for s in basis.get("sources", []):
            if not s.get("reviews_file"):
                continue
            got = classes_of(s.get("url"), pol["source_classes"], owner)
            if "own_site" in got or not got & set(CONCERN_CLASSES):
                continue   # self-curated or not a review/community source: it can't corroborate a concern
            path = Path(base_dir) / str(s["reviews_file"]) if base_dir else None
            if not path or not path.exists():
                continue
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                doc = None
            if not isinstance(doc, dict) or not doc.get("method") or not doc.get("retrieved_at") or not isinstance(doc.get("reviews", []), list):
                fails.append(f"{name}: source {s.get('name')!r} has no provenance (method, retrieved_at), so it can't corroborate")
                continue
            ids_by_source[s["name"]] = {str(r.get("id")) for r in doc.get("reviews", [])}
            host_ = urlparse(str(s.get("url") or "")).hostname
            platform_of[s["name"]] = REVIEW_METHOD_PLATFORM.get(doc.get("method")) or (brand_key(host_) if host_ else s["name"])
        shared = shared_id_fails(name, ids_by_source)
        if shared:
            fails += shared
            continue
        all_ids = set().union(*ids_by_source.values()) if ids_by_source else set()
        total_read = sum(len(v) for v in ids_by_source.values())
        for c in concerns:
            text, cited = c.get("text", "")[:60], [str(x) for x in c.get("review_ids", [])]
            if not cited:
                fails.append(f"{name}: concern {text!r} cites no review ids")
                continue
            unknown = [x for x in cited if x not in all_ids]
            if unknown:
                fails.append(f"{name}: concern {text!r} cites review ids not in the read files: {unknown[:3]}")
                continue
            distinct_sources = len({platform_of.get(n, n) for n, ids in ids_by_source.items() if ids & set(cited)})
            cited_n = len(set(cited) & all_ids)
            percent_ok = total_read >= MIN_READ_FOR_PERCENT_BRANCH and cited_n >= math.ceil(CONCERN_PERCENT_FLOOR * total_read)
            if distinct_sources < 2 and not percent_ok:
                fails.append(f"{name}: concern {text!r} is not corroborated enough to surface (needs >=2 "
                             f"independent sources, or >={MIN_READ_FOR_PERCENT_BRANCH} reviews read AND "
                             f">={CONCERN_PERCENT_FLOOR:.0%} of them citing it; found {distinct_sources} "
                             f"source(s), {cited_n}/{total_read} reviews read citing it) - mark it "
                             f"isolated: true so the page labels it a single report, or drop it")
    return fails


def plan_gate(spec):
    """Inconsistencies a plan can carry: a day outside the trip, days out of order, a stop timed before the one
    before it, a stop that leaves too little time after a transfer leg, a change of city with no leg between, a
    booking outside the trip, two stays on the same night. Feasibility beyond these (is 40 min enough for this
    museum?) is the Plan reviewer's call, not a rule."""
    fails = []
    trip = spec.get("trip") or {}
    start, end = trip.get("start"), trip.get("end")
    has_plan = any(b.get("type") in ("day_plan", "booking_ledger") for b in spec.get("blocks", []))
    if has_plan and not (start and end):
        return ["spec.trip {start, end} is required for a plan (copy it from trip_intake.json with trip_intake.py sync)"]
    if not has_plan:
        return fails
    legs = {}
    for b in spec.get("blocks", []):
        if b.get("type") == "transport_leg":
            for leg in b.get("legs", []):
                legs[str(leg.get("label", "")).lower()] = leg

    def leg_minutes(leg):
        rec = str((leg.get("recommendation") or {}).get("mode") or "").strip().lower()
        m = next((m for m in leg.get("modes", []) if mode_of(m) == rec), None)
        return float(m["time_minutes"]) if m and m.get("time_minutes") is not None else None

    def leg_between(a, b):
        a, b = a.lower(), b.lower()
        return any((str(l.get("from", "")).lower() == a and str(l.get("to", "")).lower() == b) or (a in k and b in k)
                   for k, l in legs.items())

    for b in spec.get("blocks", []):
        if b.get("type") == "day_plan":
            prev_date, prev_city = None, None
            for d in b.get("days", []):
                date, city = d.get("date"), d.get("city")
                if date:
                    if not (start <= date <= end):
                        fails.append(f"day {date} is outside the trip ({start} to {end})")
                    if prev_date and date <= prev_date:
                        fails.append(f"day {date} comes after {prev_date} in the plan; days must be in order, once each")
                    prev_date = date
                if city and prev_city and city.lower() != prev_city.lower() and not leg_between(prev_city, city):
                    fails.append(f"day {date or d.get('label')}: moves from {prev_city} to {city} with no transport leg between them")
                prev_city = city or prev_city
                prev_t = None
                label_day = re.match(r"\s*(mon|tue|wed|thu|fri|sat|sun)", str(d.get("label", "")).lower())
                if date and label_day:
                    try:
                        real = WEEKDAYS[dt.date.fromisoformat(date[:10]).weekday()]
                    except ValueError:
                        real = None
                    if real and real != label_day.group(1):
                        fails.append(f"day {date}: labelled {d.get('label')!r} but {date} is a {real.capitalize()}")
                stops = [s for s in d.get("stops", []) if s.get("status") not in OFF_STATUS]
                for k, s in enumerate(stops):
                    t = claim_key("time", s.get("time", ""))
                    mins = t[0] * 60 + t[1] if t else None
                    if mins is not None and prev_t is not None and mins < prev_t:
                        fails.append(f"day {date or d.get('label')}: {s.get('name')!r} at {s.get('time')} is before the stop listed ahead of it")
                    if s.get("leg"):
                        leg = legs.get(str(s["leg"]).lower())
                        if leg is None:
                            fails.append(f"day {date or d.get('label')}: stop {s.get('name')!r} names leg {s['leg']!r}, which is not in any transport block")
                        elif mins is not None and k + 1 < len(stops):
                            nt = claim_key("time", stops[k + 1].get("time", ""))
                            need = leg_minutes(leg)
                            if nt and need is not None and nt[0] * 60 + nt[1] < mins + need:
                                fails.append(f"day {date or d.get('label')}: {stops[k + 1].get('name')!r} at "
                                             f"{stops[k + 1].get('time')} leaves less than the {need:g} min the "
                                             f"{s['leg']!r} leg takes after {s.get('time')}")
                    prev_t = mins if mins is not None else prev_t
        if b.get("type") == "booking_ledger":
            nights = {}
            for bk in b.get("bookings", []):
                for key in ("date", "check_in", "check_out"):
                    v = bk.get(key)
                    if v and not (start <= str(v)[:10] <= end):
                        fails.append(f"booking {bk.get('name')!r}: {key} {v} is outside the trip ({start} to {end})")
                ci, co = bk.get("check_in"), bk.get("check_out")
                if bk.get("category") == "stay" and ci and co:
                    if co <= ci:
                        fails.append(f"booking {bk.get('name')!r}: check_out {co} is not after check_in {ci}")
                        continue
                    d0 = iso_date(ci)
                    if d0 is None or iso_date(co) is None:
                        continue   # date_fails reports it
                    while d0.isoformat() < co[:10]:
                        other = nights.get(d0.isoformat())
                        if other and bk.get("status") != "cancelled":
                            fails.append(f"bookings {other!r} and {bk.get('name')!r} both cover the night of {d0}")
                        if bk.get("status") != "cancelled":
                            nights[d0.isoformat()] = bk.get("name")
                        d0 += dt.timedelta(days=1)
    return fails


def criteria_fails(spec):
    """A fit criterion must say whether it is a quality (+) or a drawback (-); without a sign any bump would pass."""
    crit = spec.get("fit_criteria") or []
    if not isinstance(crit, list) or not all(isinstance(c, dict) for c in crit):
        return ["fit_criteria must be a list of objects with an id, a weight and a polarity"]
    isnum = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    return ([f"fit criterion {c.get('id')!r} has polarity {c.get('polarity')!r}; it must be '+' or '-'" for c in crit if c.get("polarity") not in POLARITIES]
            + [f"fit criterion {c.get('id')!r} has weight {c.get('weight')!r}; it must be a number" for c in crit if not isnum(c.get("weight", 0))])


def intake_gate(spec, base_dir):
    """When the project has a trip_intake.json, the spec must carry the group's traits and the trip dates exactly
    as the user gave them - no trait invented, none dropped, no dates drifted."""
    p = Path(base_dir) / "trip_intake.json" if base_dir is not None else None
    if p is None or not p.exists():
        carries = (spec.get("group_traits") or spec.get("fit_criteria")
                   or any((it.get("score_basis") or {}).get("group_fit") for _, it in items_of(spec)))
        return ["the spec carries group traits, a fit profile or ranking overlays but there is no trip_intake.json "
                "beside it: what the group agreed can't be checked"] if carries else []
    intake = json.loads(p.read_text(encoding="utf-8"))
    fails = []
    said = set((intake.get("group_profile") or {}).get("traits") or [])
    if set(spec.get("group_traits") or []) != said:
        fails.append(f"spec.group_traits {sorted(spec.get('group_traits') or [])} differ from what the group said in "
                     f"trip_intake.json {sorted(said)}; run trip_intake.py sync")
    def key(c):
        w = c.get("weight", 0) if isinstance(c, dict) else 0
        return (str((c or {}).get("id")) if isinstance(c, dict) else str(c), float(w) if isinstance(w, (int, float)) and not isinstance(w, bool) else -1.0,
                str((c or {}).get("polarity")) if isinstance(c, dict) else "", str((c or {}).get("evidence", "fact")) if isinstance(c, dict) else "")
    said_fit = sorted(key(c) for c in (intake.get("fit_profile") or {}).get("criteria") or [])
    if sorted(key(c) for c in spec.get("fit_criteria") or []) != said_fit:
        fails.append("spec.fit_criteria differ from the fit profile the user agreed in trip_intake.json; run trip_intake.py sync")
    dates = intake.get("dates") or {}
    w = dates.get("window") or {}
    want = (w.get("earliest"), w.get("latest")) if dates.get("flexible") and w else (dates.get("start"), dates.get("end"))
    trip = spec.get("trip") or {}
    if trip and (trip.get("start"), trip.get("end")) != want:
        fails.append(f"spec.trip {trip.get('start')}..{trip.get('end')} differs from trip_intake.json {want[0]}..{want[1]}")
    return fails


def evidence_ok(src, base_dir):
    """A booking confirmation or ticket saved in the project's evidence/ folder is a source too - it has no host,
    but the file is there to open. The path must resolve inside evidence/: no `..`, no other project file."""
    s_ = str(src or "")
    if not s_.startswith("evidence/") or base_dir is None:
        return False
    root = (Path(base_dir) / "evidence").resolve()
    try:
        target = (Path(base_dir) / s_).resolve()
        return target.is_file() and target.is_relative_to(root) if hasattr(target, "is_relative_to") else \
            target.is_file() and str(target).startswith(str(root) + os.sep)
    except (OSError, ValueError):
        return False


def text_tokens(v):
    return set(re.findall(r"[a-z0-9]{3,}", str(v).lower()))


def capture_source_fail(row, caps, pol, base_dir):
    """Where each capture itself came from must be a source class its kind may cite. The row's declared source is
    only what the builder typed; the captures are the evidence."""
    allowed = set(pol["kind_sources"].get(row["kind"], []))
    for c in caps:
        url = c.get("url") or c.get("source") or ""
        got = classes_of(url, row_classes(row, pol), row_owner(row))
        if "evidence" in got and not evidence_ok(url, base_dir):
            got.discard("evidence")
        if not got & allowed:
            return (f"{row['row']}: a capture came from {url!r} ({sorted(got) or 'no known class'}); a "
                    f"{row['kind']} claim may come from {sorted(allowed)}")
    return None


SPOT_RATE, SPOT_MIN = 0.2, 8   # the spot-check sample: this share of the ledger's rows, at least this many


def spot_seed(rows):
    """The spot-check sample is fixed by the ledger itself, so a builder can't choose which rows get a second look.
    The values are read the way the gate reads them (trimmed, case and spacing folded), so an edit the gate ignores
    does not reroll the sample."""
    norm_ = lambda v: re.sub(r"\s+", " ", str(v).strip().lower())
    key = json.dumps([[r["row"], norm_(r["value"])] for r in rows], sort_keys=True)
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16)


def second_looks(store, row, review_time, pol, at, base_dir=None):
    """The captures of a sampled row that count as its second look: usable, not future-dated and fresh, from a source its
    kind may cite, with the figure on the page it was read from, and taken after the review was recorded."""
    import capture_store as cs
    out = []
    for c in cs.load(store, row["row"]):
        t = parse_time(c.get("captured_at") or "")
        if not (cs.usable(c) and cs.fresh(c, row["kind"], at) and t is not None and review_time is not None and t > review_time):
            continue
        if t - at > dt.timedelta(minutes=5):
            continue   # stamped ahead of the gate's clock: ordinary freshness tolerates a day of skew, a second look does not
        if capture_source_fail(row, [c], pol, base_dir):
            continue
        if not cs.value_in_raw(row["kind"], c.get("value"), c.get("raw_text")):
            continue
        if row["kind"] == "photo" and c.get("http_status") != 200:
            continue
        if row["kind"] == "link" and not (isinstance(c.get("http_status"), int) and 200 <= c["http_status"] < 300):
            continue
        out.append(c)
    return out


def second_look_missing(store, rows, review_time, pol, at=None, base_dir=None):
    """The sampled rows ({row id: ledger row}) with no second look: no qualifying capture taken after the review, or no
    capture beyond the ones the review itself used."""
    import capture_store as cs
    at = at or dt.datetime.now(dt.timezone.utc)
    out = []
    for k, row in rows.items():
        need = 2 if row["kind"] in ("price", "score", "count") else 1
        total = [c for c in cs.load(store, k) if cs.usable(c)]
        if review_time is None or len(total) <= need or not second_looks(store, row, review_time, pol, at, base_dir):
            out.append(k)
    return out


TYPED_REVIEW_FOR_TESTS = False   # the selftest sets this; a real gate always takes its evidence from the capture store


def gate(spec, review, spot=None, spec_bytes=None, rail=None, spot_review_sha=None, spot_store=None, base_dir=None, at=None):
    """Run every check. A spec with a field of the wrong type (a number where a list belongs) is refused with a named failure,
    never a traceback: build_page.shape_errors names the common ones first, and this is the last net."""
    try:
        return _gate(spec, review, spot, spec_bytes, rail, spot_review_sha, spot_store, base_dir, at)
    except (TypeError, AttributeError, KeyError, ValueError, IndexError) as e:
        tb = sys.exc_info()[2]
        while tb.tb_next:
            tb = tb.tb_next
        return [f"the gate could not read the spec: a field has the wrong type ({type(e).__name__}: {e} at guard.py line {tb.tb_lineno}); fix the field and run it again"], [], []


def _gate(spec, review, spot, spec_bytes, rail, spot_review_sha, spot_store, base_dir, at):
    fails, warns = [], []
    at = at or dt.datetime.now(dt.timezone.utc)
    pol = policy(spec)
    fails += policy_fails(spec)
    if not spot_store and not TYPED_REVIEW_FOR_TESTS:
        fails.append("no capture store: the gate takes its evidence from stored captures, never from a typed review.json")
    fails += transport_mode_fails(spec)
    fails += unscored_review_file_fails(spec, base_dir, pol, at)
    fails += nonfinite_fails(spec) + nonfinite_fails(review, "review")
    fails += display_fails(spec, base_dir) + own_domain_fails(spec)
    warns += policy_version_warns(spec)
    fails += rail_gate(spec, rail)
    fails += score_gate(spec, base_dir, pol, at)
    fails += concern_gate(spec, base_dir, pol)
    fails += booking_ledger_gate(spec)
    fails += door_to_door_gate(spec)
    fails += plan_gate(spec)
    fails += intake_gate(spec, base_dir)
    fails += criteria_fails(spec)
    fails += date_fails(spec)
    rows = ledger(spec)
    digest = sha(spec_bytes) if spec_bytes is not None else None
    if digest is None:
        fails.append("spec bytes required: the review can't be tied to this spec without them")
    elif str(review.get("spec_sha256") or "") != digest:
        fails.append(f"review is stale: spec_sha256 {str(review.get('spec_sha256') or '')[:12]} != spec {digest[:12]}; re-review")
    review_time = None
    if spot_store:
        mark = Path(spot_store) / "_review.json"
        try:
            mk = json.loads(mark.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            mk = {}
        if digest is not None and mk.get("spec_sha256") == digest:
            review_time = parse_time(mk.get("derived_at") or "")
            if review_time is None:
                fails.append("the review time recorded in the capture store is missing or unreadable, so no second look can be tied to it: run pipeline.py review")
        else:
            fails.append("no review is recorded in the capture store for this spec: run pipeline.py review (it records the time the review was taken)")
        # Verdicts come from the capture store, never from rows typed into review.json: the file only marks when the
        # review was taken (the spot check must be later) and which spec it was for.
        import capture_store as cs
        review, _missing = cs.derive_review(spec, spot_store, at=at, spec_bytes=spec_bytes, base_dir=base_dir)
    verdicts = review.get("rows", {})
    for r in rows:
        bad_src = source_fail(r, pol, base_dir)
        if bad_src:
            fails.append(bad_src)
        v = verdicts.get(r["row"])
        if not v:
            fails.append(f"{r['row']}: no reviewer verdict")
            continue
        if v.get("verdict") != "CONFIRMED":
            fails.append(f"{r['row']}: verdict {v.get('verdict')} ({v.get('note', 'no note')})")
            continue
        if r["kind"] == "unclassified":
            fails.append(f"{r['row']}: fact {r['field']!r} has a number but no known claim kind; label it so it can be checked")
            continue
        if r["value"] in (None, ""):
            fails.append(f"{r['row']}: the spec states no {r['kind']} value ({r['field']}), so there is nothing to check; "
                         f"state it or leave the line out")
            continue
        caps = v.get("captures", [])
        need = 2 if r["kind"] in ("price", "score", "count") else 1
        if len(caps) < need:
            fails.append(f"{r['row']}: {len(caps)} capture(s), {r['kind']} needs {need} independent captures")
            continue
        if any(c.get("value") in (None, "") for c in caps):
            fails.append(f"{r['row']}: a capture has no value")
            continue
        if spot_store and need == 2 and len({(c.get("at"), c.get("raw_sha256")) for c in caps}) < 2:
            fails.append(f"{r['row']}: the {need} captures are one load of the page (same time and same text); take a second, separate look")
            continue
        wrong = capture_source_fail(r, caps, pol, base_dir)
        if wrong:
            fails.append(wrong)
            continue
        aged = [m for m in (capture_age_fail(r, c, pol, at) for c in caps) if m]
        if aged:
            fails.append(aged[0])
            continue
        if r.get("critical"):
            srcs = {k for k in (source_key(c) for c in caps) if k}
            if len(srcs) < pol["critical_min_sources"]:
                fails.append(f"{r['row']}: critical fact ({r['kind']}) is backed by {len(srcs)} independent source(s); "
                             f"it needs {pol['critical_min_sources']} (different sites, or a saved confirmation plus the site)")
                continue
        if r["kind"] == "availability":
            spec_unavail = "unavailable" in str(r["value"]).lower()
            for c in caps:
                if ("unavailable" in str(c.get("value")).lower()) != spec_unavail:
                    fails.append(f"{r['row']}: capture {c.get('value')!r} contradicts spec {r['value']!r}")
                    break
        elif r["kind"] == "count":
            base = claim_key("count", r["value"])
            if base is None:
                fails.append(f"{r['row']}: the spec states no review count ({r['value']!r}), so the captures can't be compared")
                continue
            bad = [c.get("value") for c in caps if claim_key("count", c.get("value")) is None or abs(claim_key("count", c.get("value")) - base) > max(1, COUNT_TOLERANCE * base)]
            if bad:
                fails.append(f"{r['row']}: count captures {bad} differ from spec {r['value']!r} by more than {COUNT_TOLERANCE:.0%}")
        elif r["kind"] in FIGURE_KINDS:
            vals = [claim_key(r["kind"], c.get("value")) for c in caps]  # every capture is compared
            want = claim_key(r["kind"], r["value"])
            if want is None or not all(figures_agree(r["kind"], v, want) for v in vals):
                fails.append(f"{r['row']}: captures {sorted(map(str, vals))} disagree with spec figure {want!r} ({r['value']!r})")
        elif r["kind"] == "feature":
            bad = [c.get("value") for c in caps if not feature_agrees(r["value"], c.get("value"))]
            if bad:
                fails.append(f"{r['row']}: captures {bad} do not state {r['value']!r}; a fact a ranking rests on must be on the page")
        elif r["kind"] == "link":
            bad = [c.get("url") for c in caps if str(c.get("url", "")).strip() != str(r["value"]).strip()]
            if bad:
                fails.append(f"{r['row']}: capture came from {bad[0]!r}, not the linked page {r['value']!r}")
            dead = [c.get("http_status") for c in caps if not (isinstance(c.get("http_status"), int) and not isinstance(c.get("http_status"), bool) and 200 <= c["http_status"] < 300)]
            if dead:
                fails.append(f"{r['row']}: the linked page's HTTP status is {dead[0]!r}; a link capture must record a 2xx answer (pipeline.py capture --http-status 200)")
        if r["kind"] == "photo" and not all(c.get("http_status") == 200 and str(c.get("url", "")).strip() == str(r["value"]).strip() for c in caps):
            fails.append(f"{r['row']}: every capture of a photo must be of that image's url and answer HTTP 200")
    # every card carries the full dossier: each section present, or an explicit reason it could not be collected
    for _, it in items_of(spec):
        titles = {sec.get('title'): sec for sec in it.get('dossier', [])}
        for need_title in pol["required_sections"]:
            sec = titles.get(need_title)
            if sec is None:
                fails.append(f"{it.get('name')}: dossier section {need_title!r} missing")
            elif not (sec.get('items') or sec.get('rows') or sec.get('images') or sec.get('not_found')):
                fails.append(f"{it.get('name')}: dossier section {need_title!r} is empty and has no reason")
    # a table row shows the same facts as its card: every classifiable column is compared with the card's value
    fails += table_drift(spec, pol)
    # banned phrases in prose and labels
    for owner, field, text in prose_fields(spec):
        low = str(text).lower()
        for ban in pol["banned"]:
            if re.search(r"(?<![a-z0-9])" + re.escape(ban) + r"(?![a-z0-9])", low):
                fails.append(f"{owner} / {field}: banned phrase {ban!r} in {text!r}")
    # spot-check outcome: a fresh second look at the gate's own sample of rows
    if rows and spot is None:
        fails.append("no spot check: a fresh second look at a sample of rows is required (pipeline.py sample, then spot)")
    if spot is not None:
        ids = {r["row"] for r in rows}
        spot_rows = set(spot.get("rows", {}))
        strange = sorted(spot_rows - ids)
        if strange:
            fails.append(f"spot check names rows that are not in the ledger ({strange[:3]}...)")
        want_rows = set(sample(rows, SPOT_RATE, SPOT_MIN, spot_seed(rows))["rows"])
        if rows and spot_rows != want_rows:
            missing = sorted(want_rows - spot_rows)
            fails.append(f"spot check is not the gate's sample: {len(missing)} sampled row(s) were not re-checked "
                         f"({missing[:3]}) or other rows were swapped in; re-run pipeline.py sample")
        if spot_review_sha is not None and not spot_store and spot.get("review_sha256") != spot_review_sha:
            fails.append("spot check was made against a different review.json (sha256 of the file bytes does not match)")
        if spot_store:
            import capture_store as cs
            by_row = {r['row']: r for r in rows}
            sampled = [k for k in spot.get('rows', {}) if k in by_row]
            bad = [k for k in sampled if not store_agrees(k, by_row[k]['kind'], by_row[k]['value'], spot_store)]
            # a real second look: a capture newer than the review it checks (compared as times, not strings), fresh, from a
            # source the kind may cite, and read from a page that shows the figure. With no readable review time, none counts.
            stale = second_look_missing(spot_store, {k: by_row[k] for k in sampled}, review_time, pol, at, base_dir)
            if stale:
                fails.append(f"no second look: {len(stale)} sampled row(s) have no qualifying extra capture taken after the review "
                             f"({mk.get('derived_at')}): {stale[:3]}")
            # the second looks themselves must agree with the spec, not only the earlier captures
            for k in sampled:
                looks = second_looks(spot_store, by_row[k], review_time, pol, at, base_dir)
                if looks and not all(cap_agrees(by_row[k]['kind'], by_row[k]['value'], c) for c in looks):
                    bad.append(k)
        else:
            bad = [k for k, s_ in spot.get('rows', {}).items() if not s_.get('agrees')]
        if bad:
            fails.append(f"spot check disagreed on {len(bad)} row(s) ({bad[:3]}...): a disagreement is a finding. Fix the spec "
                         f"or exclude the wrong capture with a reason (pipeline.py exclude), take new captures, and re-run; "
                         f"typing a retest reason into review.json does not clear it")
    return fails, warns, rows


def sample(rows, rate, minimum, seed):
    """Every critical row (a flight time, a booking amount or date: the facts a traveller acts on) is always re-checked; the remaining
    places in the sample are drawn from the other rows by the ledger's seed."""
    crit = [r for r in rows if r.get("critical")]
    rest = [r for r in rows if not r.get("critical")]
    n = min(len(rest), max(minimum, math.ceil(len(rest) * rate)))   # the other rows are sampled in their own right, however many rows are critical
    rnd = random.Random(seed)
    picked = crit + rnd.sample(rest, n)
    return {"seed": seed, "rate": rate, "rows": [p["row"] for p in picked]}


def cmd_gate(a):
    spec_path = Path(a.spec)
    spec_bytes = spec_path.read_bytes()
    spec = json.loads(spec_bytes)
    review = json.loads(Path(a.review).read_text(encoding="utf-8"))
    spot = json.loads(Path(a.spot).read_text(encoding="utf-8")) if a.spot else None
    base_dir = Path(a.base_dir) if a.base_dir else spec_path.resolve().parent
    rail = json.loads(Path(a.rail).read_text(encoding='utf-8')) if a.rail else (json.loads((base_dir / "rail.json").read_text(encoding="utf-8")) if (base_dir / "rail.json").exists() else None)
    store = a.store or (str(base_dir / "captures") if (base_dir / "captures").is_dir() else None)
    if not store:
        print("FAIL no capture store: pass --store (the gate takes its evidence from stored captures, not from a typed review.json)")
        return 1
    fails, warns, rows = gate(spec, review, spot, spec_bytes, rail, sha(a.review), spot_store=store, base_dir=base_dir)
    print(f"ledger rows: {len(rows)}")
    for w in warns:
        print("WARN", w)
    for f in fails:
        print("FAIL", f)
    print("GATE " + ("PASS" if not fails else f"FAIL ({len(fails)})"))
    return 0 if not fails else 1


def cmd_evals(a):
    spec = json.loads(Path(a.spec).read_text(encoding="utf-8"))
    golden = json.loads(Path(a.golden).read_text(encoding="utf-8"))
    rows = {r["row"]: r for r in ledger(spec)}
    bad = 0
    for g in golden["expect"]:
        r = next((x for x in rows.values() if x["item"] == g["item"] and x["field"] == g["field"]), None)
        got = r["value"] if r else None
        ok = r is not None and norm(got) == norm(g["value"])
        bad += 0 if ok else 1
        print(("PASS" if ok else "FAIL"), f"expect {g['item']} {g['field']} == {g['value']!r} (got {got!r})")
    blob = json.dumps(spec, ensure_ascii=False)
    for f in golden.get("forbid", []):
        hit = f["text"] in blob
        bad += 1 if hit else 0
        print(("FAIL" if hit else "PASS"), f"forbid {f['text']!r}: {f.get('why', '')}")
    print("EVALS " + ("PASS" if bad == 0 else f"FAIL ({bad})"))
    return 0 if bad == 0 else 1


def cmd_ledger(a):
    spec = json.loads(Path(a.spec).read_text(encoding="utf-8"))
    rows = ledger(spec)
    a.out = a.out or str(Path(a.spec).resolve().parent / "ledger.json")   # beside the spec, never the current folder
    Path(a.out).write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=lambda o: sorted(o) if isinstance(o, (set, frozenset)) else str(o)), encoding="utf-8")
    print(f"wrote {len(rows)} rows to {a.out}")
    return 0


def cmd_sample(a):
    rows = json.loads(Path(a.ledger).read_text(encoding="utf-8"))
    plan = sample(rows, a.rate, a.min, a.seed if a.seed is not None else spot_seed(rows))   # default: the sample the gate expects
    a.out = a.out or str(Path(a.ledger).resolve().parent / "spot.plan.json")
    Path(a.out).write_text(json.dumps(plan, indent=2), encoding="utf-8")
    print(f"spot-check {len(plan['rows'])} of {len(rows)} rows -> {a.out}")
    return 0


def selftest():
    global TYPED_REVIEW_FOR_TESTS
    TYPED_REVIEW_FOR_TESTS = True
    """Known-answer tests: the gate must pass a clean spec and fail each planted defect. Every case runs at a fixed
    moment (AT), so captures don't age out of their window as the calendar moves on."""
    AT = dt.datetime(2026, 10, 10, tzinfo=dt.timezone.utc)
    WHEN = "2026-10-09T00:00:00Z"
    OWN = "https://www.examplehotel.test"
    clone = lambda x: json.loads(json.dumps(x))
    spec = {"blocks": [{"type": "cards", "id": "c", "items": [
        {"name": "A", "id": "1", "url": f"{OWN}/book", "rank": 1, "price": "€100.00", "images": [f"{OWN}/img/1.jpg"],
         "facts": [{"label": "Stars", "value": "4"}],
         "dossier": [{"title": t, "not_found": "selftest stub"} for t in REQUIRED_DOSSIER]}]}]}

    def cap(v, url=f"{OWN}/book", at=WHEN, **extra):
        return dict({"value": v, "url": url, "at": at, "http_status": 200}, **extra)

    def verdicts_for(s, two=("price", "score", "count")):
        out = {}
        for r in ledger(s):
            caps = [cap(r["value"], r["src"])]
            if r["kind"] in two or r.get("critical"):
                caps.append(cap(r["value"], r["src"]))
            out[r["row"]] = {"verdict": "CONFIRMED", "captures": caps}
        return out

    def auto_spot(s):
        rs = ledger(s)
        return {"rows": {k: {"agrees": True} for k in sample(rs, SPOT_RATE, SPOT_MIN, spot_seed(rs))["rows"]}}

    def run(s, verdicts, spot="auto", base_dir=None, **review_extra):
        if spot == "auto":
            spot = auto_spot(s)
        raw = json.dumps(s).encode()
        review = dict({"spec_sha256": sha(raw), "scope": "full", "rows": verdicts}, **review_extra)
        return gate(s, review, spot, raw, base_dir=base_dir, at=AT)[0]

    cases = []
    good = verdicts_for(spec)
    rows = ledger(spec)
    cases.append(("clean spec passes", run(spec, good) == []))
    wrong = clone(good); wrong[rows[0]["row"]]["verdict"] = "WRONG"
    cases.append(("WRONG verdict fails", bool(run(spec, wrong))))
    pr = next(r for r in rows if r["kind"] == "price")
    one_cap = clone(good); one_cap[pr["row"]]["captures"] = one_cap[pr["row"]]["captures"][:1]
    cases.append(("single price capture fails", bool(run(spec, one_cap))))
    raw = json.dumps(spec).encode()
    cases.append(("stale review fails", bool(gate(spec, {"spec_sha256": "0" * 64, "scope": "full", "rows": good}, None, raw, at=AT)[0])))
    banned = clone(spec); banned["blocks"][0]["items"][0]["verdict"] = "Smallest room in the set."
    cases.append(("banned phrase fails", bool(run(banned, good))))
    mism = clone(good); mism[pr["row"]]["captures"] = [cap("€200.00"), cap("€200.00")]
    cases.append(("captures disagreeing with spec fail", bool(run(spec, mism))))
    dis = auto_spot(spec); dis["rows"][next(iter(dis["rows"]))]["agrees"] = False
    cases.append(("a spot disagreement fails", any("spot check disagreed" in f for f in run(spec, good, dis, scope="partial"))))
    cases.append(("a typed retest reason does not clear it", any("spot check disagreed" in f for f in run(spec, good, dis, scope="full"))))
    nan = clone(spec); nan["blocks"][0]["items"][0]["score"] = float("nan")
    cases.append(("a NaN score fails", any("must be finite" in f for f in run(nan, verdicts_for(nan)))))
    cases.append(("a spec can't reclassify a price label as a photo", [k for k in policy({"policy": {"label_kinds": [["nightly", "photo"]]}})["label_kinds"]][0][1] != "photo"
                  and kind_of_fact("Nightly price", "EUR 100", policy({"policy": {"label_kinds": [["nightly", "photo"]]}})["label_kinds"]) == "price"))
    cases.append(("a spec can't add a bare suffix as a review platform", bool(policy_fails({"policy": {"source_classes": {"review_platform": ["com"]}}}))
                  and not pattern_ok("com") and not pattern_ok("co.uk") and pattern_ok("airline.example")))
    cases.append(("name.* matches real country domains only", classes_of("https://www.tripadvisor.co.uk/x", SOURCE_CLASSES) >= {"review_platform"}
                  and not classes_of("https://booking.xyz/x", SOURCE_CLASSES) and not classes_of("https://www.tripadvisor.biz/x", SOURCE_CLASSES)))
    # Source classes: a claim's source must be allowed for its kind.
    off = clone(spec); off["blocks"][0]["items"][0]["images"] = ["https://evil.example/x.jpg"]
    cases.append(("photo from an unrelated site fails", any("photo claim may come from" in f for f in run(off, verdicts_for(off)))))
    rated = clone(spec); rated["blocks"][0]["items"][0]["dossier"][1] = {"title": REQUIRED_DOSSIER[1], "kind": "ratings",
        "items": [{"rating": "4.9", "count": "120", "url": f"{OWN}/testimonials"}]}
    cases.append(("a rating from the property's own site fails", any("score claim may come from" in f for f in run(rated, verdicts_for(rated)))))
    rated["blocks"][0]["items"][0]["dossier"][1]["items"][0]["url"] = "https://www.tripadvisor.co.uk/Hotel_Review-x"
    cases.append(("a rating from a regional TripAdvisor site passes", run(rated, verdicts_for(rated)) == []))
    dist = clone(spec); dist["blocks"][0]["items"][0]["facts"] = [{"label": "Distance to beach", "value": "400 m walk, 5 min",
                                                                  "source": "https://www.booking.com/x"}]
    cases.append(("a distance not measured on a map fails", any("distance claim may come from" in f for f in run(dist, verdicts_for(dist)))))
    cases.append(("a forum thread is community, not a review platform; a place page is not a map measurement",
                  "review_platform" not in classes_of("https://www.tripadvisor.com/ShowTopic-g1-i2-k3.html", SOURCE_CLASSES)
                  and "community" in classes_of("https://www.tripadvisor.com/ShowTopic-g1-i2-k3.html", SOURCE_CLASSES)
                  and "review_platform" in classes_of("https://www.tripadvisor.com/Hotel_Review-x", SOURCE_CLASSES)
                  and "maps_measure" not in classes_of("https://www.google.com/maps/place/x", SOURCE_CLASSES)
                  and "maps_measure" in classes_of("https://www.google.com/maps/dir/a/b", SOURCE_CLASSES)))
    cases.append(("a URL classes by suffix and path", classes_of("https://www.google.co.in/maps/place/x", SOURCE_CLASSES) >= {"review_platform"}
                  and "own_site" in classes_of(f"{OWN}/x", SOURCE_CLASSES, {"www.examplehotel.test"})
                  and classes_of("javascript:alert(1)", SOURCE_CLASSES) == set()))
    # Freshness: a capture with no time, or older than its kind's window, is refused; a spec can only tighten.
    old = clone(good); old[pr["row"]]["captures"] = [cap("€100.00", at="2026-08-01T00:00:00Z")] * 2
    cases.append(("a price captured 70 days ago fails", any("older than 14 days" in f for f in run(spec, old))))
    untimed = clone(good); untimed[pr["row"]]["captures"] = [{"value": "€100.00", "url": f"{OWN}/book"}] * 2
    cases.append(("a capture with no time fails", any("has no time" in f for f in run(spec, untimed))))
    loose = policy({"policy": {"freshness_days": {"price": 999}}}); tight = policy({"policy": {"freshness_days": {"price": 3}}})
    cases.append(("a spec can tighten freshness but not loosen it", loose["freshness_days"]["price"] == 14 and tight["freshness_days"]["price"] == 3))
    # Dossier completeness and unclassified numbers.
    no_sec = clone(spec); no_sec["blocks"][0]["items"][0]["dossier"] = no_sec["blocks"][0]["items"][0]["dossier"][1:]
    cases.append(("missing dossier section fails", any("missing" in f for f in run(no_sec, good))))
    empty_sec = clone(spec); empty_sec["blocks"][0]["items"][0]["dossier"][0] = {"title": REQUIRED_DOSSIER[0]}
    cases.append(("empty dossier section without a reason fails", any("empty" in f for f in run(empty_sec, good))))
    unclass = clone(spec); unclass["blocks"][0]["items"][0]["facts"] = [{"label": "Pool depth", "value": "2.5 m"}]
    cases.append(("unclassified number fact fails", any("no known claim kind" in f for f in run(unclass, verdicts_for(unclass)))))
    # Rail facts must come from a Maps measurement, keyed by the card's own id.
    rail_spec = clone(spec); rail_spec["blocks"][0]["items"][0]["facts"] = [{"label": "Nearest rail", "value": "100 m"}]
    cases.append(("rail fact without Maps fails", bool(rail_gate(rail_spec, None))))
    rail_spec["blocks"][0]["items"][0]["facts"] = [{"label": "Nearest rail", "value": "X: 128 m walk, 2 min (Google Maps)"}]
    cases.append(("rail fact matching Maps passes", rail_gate(rail_spec, {"1": {"nearest": {"walk_m": 128, "walk_min": 2, "name": "X"}}}) == []))
    # Evidence of a claim must itself come from a source its kind may cite.
    blog = {r["row"]: {"verdict": "CONFIRMED", "captures": [cap(r["value"], "https://random-blog.example/x")] * 2} for r in rows}
    cases.append(("captures from an unrelated site don't confirm a claim", any("a capture came from" in f for f in run(spec, blog))))
    testimonials = verdicts_for(rated)
    for r in ledger(rated):
        if r["kind"] in ("score", "count") and "dossier" in r["row"]:
            testimonials[r["row"]]["captures"] = [cap(r["value"], f"{OWN}/testimonials")] * 2
    cases.append(("a rating captured from the property's own pages fails", any("a capture came from" in f for f in run(rated, testimonials))))
    # A spec can only tighten the policy.
    for label, pol_ in (("an emptied banned list", {"banned": []}), ("an emptied required_sections", {"required_sections": []}),
                        ("an own-site rating rule", {"kind_sources": {"score": ["own_site"]}}), ("min_reviews_read of 1", {"min_reviews_read": 1}),
                        ("an allowed_hosts list", {"allowed_hosts": ["x.example"]}), ("a looser critical count", {"critical_min_sources": 1})):
        weak = clone(spec); weak["policy"] = pol_
        cases.append((f"{label} in the spec's policy fails the gate", any(f.startswith("policy.") for f in run(weak, verdicts_for(weak)))))
    weak = clone(spec); weak["policy"] = {"banned": [], "required_sections": [], "min_reviews_read": 1}
    weak["blocks"][0]["items"][0]["verdict"] = "The cheapest of the set."
    cases.append(("an emptied banned list still bans 'cheapest'", any("banned phrase" in f for f in run(weak, verdicts_for(weak)))))
    pw = policy(weak)
    cases.append(("a weakened policy resolves to the defaults", pw["min_reviews_read"] == 20 and "cheapest" in pw["banned"] and pw["required_sections"] == REQUIRED_DOSSIER))
    synth = {"policy": {"source_classes": {"review_platform": ["reviews.fake.test"]}}}
    cases.append(("reserved example hosts in a fixed class need a synthetic footer", bool(policy_fails(synth)) and not policy_fails(dict(synth, footer="Synthetic example, not real."))))
    texty = policy({"policy": {"label_kinds": [["pool", "text"], ["fee", "price"]]}})
    cases.append(("a spec can't classify a label as unchecked text", ["pool", "text"] not in texty["label_kinds"] and ["fee", "price"] in texty["label_kinds"]))
    # Durations, times and currencies compare as what they are.
    cases.append(("1 h 30 min and 1 h 55 min are different durations", claim_key("duration", "1 h 30 min") != claim_key("duration", "1 h 55 min")
                  and claim_key("duration", "1 h 30 min") == claim_key("duration", "90 min") == claim_key("duration", 90) == claim_key("duration", "PT1H30M")))
    cases.append(("9:05 PM is 21:05, not 09:05", claim_key("time", "9:05 PM") == claim_key("time", "21:05") != claim_key("time", "09:05")))
    cases.append(("EUR 246 does not match $246", not figures_agree("price", claim_key("price", "\u20ac246"), claim_key("price", "$246"))
                  and figures_agree("price", claim_key("price", "\u20ac246.00"), claim_key("price", "246"))
                  and not figures_agree("price", claim_key("price", "246"), claim_key("price", "\u20ac246.00"))))   # a spec amount that names a currency needs a capture that names it too
    cases.append(("a price is the figure beside the currency, not the first number", claim_key("price", "2 nights for EUR 999") == (999.0, "EUR")
                  and claim_key("score", "Rated 4.5/5, 2 stars off") == 4.5))
    cases.append(("a feature with no 3-letter word compares the whole text", feature_agrees("Ja", "Ja") and not feature_agrees("Ja", "Nein")
                  and feature_agrees("heated pool", "A heated pool and sauna") and not feature_agrees("heated pool", "sauna only")))
    cases.append(("a negation anywhere in a fact keeps it from earning a bump", all(NEGATED.search(x) for x in ("There is no pool", "Pool: none", "No pool", "Hotel has no lift"))
                  and not NEGATED.search("Heated pool, open all year")))
    cases.append(("a time of 24:99 and a duration of '1 day' are not read as figures", clock_time("24:99") is None and duration_minutes("1 day") is None
                  and clock_time("9:05 PM") == (21, 5)))
    plat = clone(spec); plat["blocks"][0]["items"][0]["own_domains"] = ["booking.com"]
    cases.append(("a card can't declare a booking platform as its own site", bool(own_domain_fails(plat)) and not own_domain_fails(spec)))
    cases.append(("a [label, value] dossier row can be cited by an overlay", resolve_fact({"dossier": [{"title": "Map listing", "rows": [["Name", "Farm A"]]}]},
                                                                                           {"dossier": "Map listing", "index": 0})[1] == {"label": "Name", "value": "Farm A"}))
    cases.append(("a policy of the wrong shape fails cleanly instead of raising", bool(policy_fails({"policy": {"freshness_days": "7", "banned": "x"}}))
                  and policy({"policy": {"freshness_days": "7"}})["freshness_days"] == FRESHNESS_DAYS))
    kp = {"blocks": [{"type": "hero", "kpis": [{"value": "3 inns from EUR 99", "label": "a", "derived": {"count": "c"}}, {"value": "3", "label": "b", "derived": {"count": "c"}},
                                                  {"value": "4.4", "label": "c2", "derived": {"min": "c.score"}}]},
                     {"type": "cards", "id": "c", "items": [{"name": "A", "score": 4.0}, {"name": "B", "score": 4.5}, {"name": "C", "score": 4.8}]}]}
    kf = display_fails(kp, None)
    cases.append(("a KPI states one figure and is checked at the precision it is written in", sum("exactly one figure" in f for f in kf) == 1
                  and sum("but the spec gives 4" in f for f in kf) == 1 and not any("'b'" in f for f in kf)))
    cases.append(("an owner can't be a bare suffix: home_url https://com widens nothing", own_hosts(["https://com/x"], ["com", "co.uk"], SOURCE_CLASSES) == set()
                  and own_hosts(["https://www.examplehotel.test/x"], [], SOURCE_CLASSES) == {"www.examplehotel.test"}
                  and bool(own_domain_fails({"blocks": [{"type": "cards", "items": [{"name": "A", "own_domains": ["com"]}]}]}))
                  and bool(own_domain_fails({"blocks": [{"type": "cards", "items": [{"name": "A", "home_url": "https://com"}]}]}))))
    lk = {"policy": {"label_kinds": [["happy", "availability"]]}}
    cases.append(("a spec can't classify a numeric label as availability", bool(policy_fails(lk)) and ["happy", "availability"] not in policy(lk)["label_kinds"]
                  and kind_of_fact("Happy visitors", "5000 a year", policy(lk)["label_kinds"]) == "unclassified"))
    kq = {"blocks": [{"type": "hero", "kpis": [{"value": "Nine stays", "label": "a"}, {"value": "5000", "label": "b", "derived": {"max": "c.guests"}}]},
                     {"type": "cards", "id": "c", "items": [{"name": "A", "guests": 5000, "score": 4.0}]}]}
    cases.append(("a KPI in words and a KPI derived from an unchecked key both fail", len(display_fails(kq, None)) == 2))
    cases.append(("one review id in two files is refused", bool(shared_id_fails("A", {"g": {"r0", "r1"}, "b": {"r0"}})) and not shared_id_fails("A", {"g": {"g0"}, "b": {"b0"}})))
    every = {"title": "T", "footer": "the cheapest stays", "design": {"brand": "B", "ethos": "the only way"},
             "blocks": [{"type": "hero", "eyebrow": "smallest week", "kpis": [{"label": "sold out", "value": "1"}]},
                        {"type": "cards", "items": [{"name": "A", "badges": ["tbd"], "facts": [{"label": "x", "value": "lorem ipsum"}], "url": "https://x.example/todo"}]},
                        {"type": "table", "items": [{"name": "A", "note": "not captured"}]}]}
    seen_ = " | ".join(t for _, _, t in prose_fields(every))
    many = [{"row": f"c{i}", "value": i, "critical": True} for i in range(11)] + [{"row": f"n{i}", "value": i, "critical": False} for i in range(10)]
    smp = sample(many, 0.2, 8, 7)["rows"]
    cases.append(("every critical row is sampled and the other rows are sampled in their own right", all(f"c{i}" in smp for i in range(11)) and sum(r.startswith("n") for r in smp) == 8))
    lbl = policy({})["label_kinds"]
    kf = lambda label, value: kind_of_fact(label, value, [tuple(x) for x in lbl])
    cases.append(("only a question that starts with 'ask the host' and holds no figure is a note; a figure under that wording is checked",
                  kf("Ask the host", "Is the pool heated?") == "note" and kf("Nightly rate (ask the host)", "\u20b99,000 per night, sleeps 12?") == "price"
                  and kf("Ask the host", "Is the pool \u20b92,000 extra?") == "unclassified" and kf("Ask the host", "It is heated.") == "text"))
    cases.append(("a link capture needs a 2xx status; none, 404 and True are refused",
                  cap_agrees("link", "https://x.example/a", {"url": "https://x.example/a", "value": "x", "http_status": 200})
                  and not cap_agrees("link", "https://x.example/a", {"url": "https://x.example/a", "value": "x", "http_status": 404})
                  and not cap_agrees("link", "https://x.example/a", {"url": "https://x.example/a", "value": "x"})
                  and not cap_agrees("link", "https://x.example/a", {"url": "https://x.example/a", "value": "x", "http_status": True})))
    cases.append(("a date is read however the page writes it", date_of("Check-in Tue, 7th May 2030") == "2030-05-07" and date_of("May 7, 2030") == "2030-05-07" and date_of("2030-05-07T09:00") == "2030-05-07"))
    cases.append(("a price is the same number in every locale", claim_key("price", "\u20ac1.299,50") == claim_key("price", "\u20ac1,299.50") == claim_key("price", "1 299,50 EUR")
                  and claim_key("price", "\u20b91,20,000") == (120000.0, "INR")))
    cases.append(("the banned-phrase scan reads footer, ethos, eyebrow, labels, badges, facts and table cells, not links",
                  all(w in seen_ for w in ("cheapest", "the only way", "smallest", "sold out", "tbd", "lorem", "not captured")) and "todo" not in seen_))
    cases.append(("a transport mode outside the closed set is refused, 'Air' is read as air", bool(transport_mode_fails({"blocks": [{"type": "transport_leg", "legs": [{"label": "l", "modes": [{"mode": "plane"}]}]}]}))
                  and not transport_mode_fails({"blocks": [{"type": "transport_leg", "legs": [{"label": "l", "modes": [{"mode": "Air"}]}]}]}) and mode_of({"mode": " Air "}) == "air"))
    own_spec = clone(spec); own_spec["blocks"][0]["items"][0]["own_domains"] = ["cdn.hotelgroup-photos.net"]
    prow = next(r for r in ledger(own_spec) if r["kind"] == "price"); prow = dict(prow, src="https://cdn.hotelgroup-photos.net/a")
    phrow = dict(prow, kind="photo", row=prow["row"] + "-photo")
    polo = policy(own_spec)
    cases.append(("a declared own domain backs photos but not a price", source_fail(prow, polo, None) is not None and source_fail(phrow, polo, None) is None))
    dealspec = clone(spec); dealspec["policy"] = {"source_classes": {"airline": ["dealsite.net"]}}
    drow = dict(next(r for r in ledger(dealspec) if r["kind"] == "price"), src="https://dealsite.net/hotel")
    cases.append(("an operator a spec adds cannot back a hotel's price", source_fail(drow, policy(dealspec), None) is not None
                  and source_fail(dict(drow, card=False, kind="price"), policy(dealspec), None) is None))
    # A capture can't be dated in the future.
    future = clone(good); future[pr["row"]]["captures"] = [cap("\u20ac100.00", at="2099-01-01T00:00:00Z")] * 2
    cases.append(("a capture dated in the future fails", any("in the future" in f for f in run(spec, future))))
    # Independence is by site, not by host.
    cases.append(("www.booking.com and secure.booking.com are one source", source_key({"url": "https://www.booking.com/a"}) == source_key({"url": "https://secure.booking.com/b"})
                  and source_key({"url": "https://www.booking.co.uk/a"}) == source_key({"url": "https://www.booking.com/a"}) == "booking"
                  and source_key({"url": "evidence/a.txt"}) == source_key({"url": "evidence/b.txt"}) == "evidence"
                  and source_key({"url": "mcp:flightclaw"}) == "mcp:flightclaw"))
    # The spot check is the gate's sample, not whatever the builder lists.
    cases.append(("no spot check fails", any("no spot check" in f for f in run(spec, good, None))))
    fake = {"rows": {f"made-up-{i}": {"agrees": True} for i in range(8)}}
    cases.append(("a spot check of made-up rows fails", any("not in the ledger" in f for f in run(spec, good, fake))))
    swap = auto_spot(spec)
    if len(rows) > len(swap["rows"]):
        out_row = next(r["row"] for r in rows if r["row"] not in swap["rows"])
        swap["rows"].pop(next(iter(swap["rows"]))); swap["rows"][out_row] = {"agrees": True}
        cases.append(("a spot check that swaps in rows outside the sample fails", any("not the gate's sample" in f for f in run(spec, good, swap))))
    cases.append(("gate without spec bytes fails", any("spec bytes required" in f for f in gate(spec, {"spec_sha256": "x", "scope": "full", "rows": good}, auto_spot(spec), None, at=AT)[0])))
    # Labels classify by word, distances in km work, unparseable figures never agree, dates must be ISO.
    cases.append(("labels classify by word, not by substring", kind_of_fact("Separate living room", "30 m2") == "size"
                  and kind_of_fact("Hiking trail", "3 stops") != "distance" and kind_of_fact("Hotel star rating", "4") == "stars"))
    cases.append(("a distance in km parses and compares", claim_key("distance", "1.2 km") == (1200, None)
                  and figures_agree("distance", claim_key("distance", "1.2 km"), claim_key("distance", "1200 m"))
                  and not figures_agree("distance", claim_key("distance", "1.2 km"), claim_key("distance", "1.5 km"))))
    with tempfile.TemporaryDirectory() as sd:
        import capture_store as cs_
        cs_.put(sd, "r|distance", "https://www.google.com/maps/dir/a/b", "n/a", raw="page text")
        cs_.put(sd, "r|count", "https://www.tripadvisor.com/x", "n/a", raw="page text")
        cases.append(("two unparseable values don't agree on a spot check", not store_agrees("r|distance", "distance", "n/a", sd) and not store_agrees("r|count", "count", "n/a", sd)))
    nocount = clone(spec); nocount["blocks"][0]["items"][0]["dossier"][1] = {"title": REQUIRED_DOSSIER[1], "kind": "ratings",
        "items": [{"rating": "4.5", "url": "https://www.tripadvisor.com/Hotel_Review-x"}]}
    cases.append(("a rating with no review count fails cleanly instead of raising", any("states no count value" in f for f in run(nocount, verdicts_for(nocount)))))
    badd = {"as_of": "1 Nov 2026", "blocks": [{"type": "booking_ledger", "id": "b", "bookings": [
        {"name": "H", "amount": 1, "currency": "EUR", "status": "paid", "check_in": "2 Dec", "check_out": "2030-05-04", "category": "stay"}], "summary": {"EUR": 1}}]}
    cases.append(("a non-ISO date fails cleanly, with no traceback", any("not an ISO date" in f for f in date_fails(badd)) and isinstance(plan_gate(dict(badd, trip={"start": "2030-05-01", "end": "2030-05-09"})), list)))
    # Dossier facts are ledger rows too.
    dos = clone(spec); dos["blocks"][0]["items"][0]["dossier"].append({"title": "Practical", "kind": "facts", "rows": [["Drive to airport", "40 min"]], "source": f"{OWN}/info"})
    cases.append(("a dossier fact with a figure becomes a ledger row", any(r["row"].endswith("Practical|row0") and r["kind"] == "duration" for r in ledger(dos))))
    # A table row agrees with its card on every figure the card states.
    twin = clone(spec); card0 = twin["blocks"][0]["items"][0]
    card0["facts"].append({"label": "Drive", "value": "45 min", "source": "https://www.google.com/maps/dir/a/b"})
    twin["blocks"].append({"type": "table", "id": "t", "columns": [{"key": "name", "label": "Name"}, {"key": "cost", "label": "Cost", "type": "num"},
                                                                  {"key": "score", "label": "Rating", "type": "num"}, {"key": "drive", "label": "Drive (min)", "type": "num"},
                                                                  {"key": "beds", "label": "Beds", "type": "num"}],
                           "items": [{"name": card0["name"], "rank": card0["rank"], "cost": card0["price"], "score": 4.0, "drive": 45, "beds": 3, "url": card0["url"]}]})
    card0["score"] = 4.0
    pol_t = policy(twin)
    cases.append(("a table row matching its card passes the drift check", table_drift(twin, pol_t) == []))
    bad_t = clone(twin); bad_t["blocks"][-1]["items"][0]["score"] = 9.9
    cases.append(("a table score that differs from the card fails", any("score" in f for f in table_drift(bad_t, pol_t))))
    bad_t = clone(twin); bad_t["blocks"][-1]["items"][0]["cost"] = 1
    cases.append(("a table cost that differs from the card price fails", any("cost" in f for f in table_drift(bad_t, pol_t))))
    cases.append(("a carded row's column the card doesn't state becomes a ledger row", any(r["row"].endswith("|beds") for r in table_only_rows(twin))
                  and not any(r["row"].endswith("|cost") for r in table_only_rows(twin))))
    # A road mode with segments still has its time checked.
    road = {"blocks": [{"type": "transport_leg", "id": "t", "title": "T", "legs": [{"label": "A to B", "modes": [
        {"mode": "road", "time_minutes": 30, "source": "https://www.google.com/maps/dir/a/b", "captured_at": WHEN, "segments": [{"label": "x"}]}],
        "recommendation": {"mode": "road", "reasons": ["r"]}}]}]}
    cases.append(("a road mode with segments still checks its time", any(r["field"] == "time_minutes" for r in transport_leg_rows(road))))
    # An agreed fit profile can't be invented: no intake file, no criteria; and every criterion has a sign.
    cases.append(("fit criteria with no trip_intake.json fail", any("no trip_intake.json" in f for f in intake_gate({"fit_criteria": [{"id": "x", "weight": 0.3, "polarity": "+"}]}, None))))
    cases.append(("a criterion with no polarity fails", bool(criteria_fails({"fit_criteria": [{"id": "x", "weight": 0.3}]}))))
    # Rail walking minutes are compared exactly, not by substring.
    rs = clone(spec); rs["blocks"][0]["items"][0]["facts"] = [{"label": "Nearest rail", "value": "X: 450 m walk, 15 min (Google Maps)"}]
    cases.append(("a rail fact with the wrong walk time fails", bool(rail_gate(rs, {"1": {"nearest": {"walk_m": 450, "walk_min": 5, "name": "X"}}}))))

    with tempfile.TemporaryDirectory() as td:
        def write(fn, pre, n=20, stars=4, method="google-places-api", retrieved="2026-10-06T00:00:00Z"):
            texts = [f"{pre} review {i}" for i in range(n)]
            Path(td, fn).write_text(json.dumps({"method": method, "retrieved_at": retrieved, "reviews": [
                {"id": f"{pre}{i}", "stars": stars, "text": t, "sha256": hashlib.sha256(t.encode()).hexdigest()}
                for i, t in enumerate(texts)]}), encoding="utf-8")
        write("g.json", "g"); write("b.json", "b", stars=5, method="apify-booking-reviews")
        write("old.json", "o", retrieved="2026-01-01T00:00:00Z"); write("small.json", "s", n=8)
        write("own.json", "w", stars=5); write("rd.json", "r", n=5, method="reddit-search")
        WEB = {"name": "web research", "url": "https://example.org/a", "read_at": "2026-10-06"}
        GMAPS = {"name": "Google Maps reviews", "url": "https://www.google.com/maps/place/x", "read": 20, "reviews_file": "g.json"}
        BOOKING = {"name": "Booking.com reviews", "url": "https://www.booking.com/hotel/x.html", "read": 20, "reviews_file": "b.json"}
        sg = lambda s: score_gate(s, td, policy(s), AT)

        # Only rank-score-v2 exists: the old formulas are refused.
        bare = clone(spec); bare["blocks"][0]["items"][0]["score"] = 4.0
        cases.append(("bare headline score fails", any("no score_basis" in f for f in sg(bare))))
        for old in ("equal-weight-mean-stars", "rank-score-v1"):
            oldf = clone(bare); oldf["blocks"][0]["items"][0]["score_basis"] = {"method": "read-reviews", "formula": old, "rubric": "r", "sources": [GMAPS, WEB], "themes": []}
            cases.append((f"the old formula {old} is refused", any("not recognised" in f for f in sg(oldf))))
        write("scraped.json", "x", method="html-scrape")
        no_formula = clone(bare); no_formula["blocks"][0]["items"][0]["score_basis"] = {"method": "read-reviews", "rubric": "r", "sources": [GMAPS, WEB], "themes": []}
        cases.append(("missing formula fails", any("has no formula" in f for f in sg(no_formula))))

        # rank-score-v2: classed, weighted sources and pointed, trait-backed bumps.
        def v2(sources, group_fit=(), guest=4.6):
            return {"method": "read-reviews", "formula": "rank-score-v2", "rubric": "weighted mean, verified stays 1.5x",
                    "guest_score": guest, "group_fit": list(group_fit), "sources": list(sources) + [WEB],
                    "themes": [{"text": "t", "review_ids": ["g0", "b0"]}]}
        card = clone(spec); it0 = card["blocks"][0]["items"][0]
        it0["facts"] = [{"label": "Kids play area", "value": "Indoor playroom with slides"}, {"label": "Pool", "value": "No pool"}]
        it0["score"] = 4.6; it0["score_basis"] = v2([GMAPS, BOOKING])
        card["group_traits"] = ["needs_kids_play_area"]
        cases.append(("v2 weights verified stays 1.5x (4 and 5 -> 4.6, not 4.5)", sg(card) == []))
        scraped = clone(card); scraped["blocks"][0]["items"][0]["score_basis"]["sources"][0] = dict(GMAPS, reviews_file="scraped.json")
        cases.append(("scraped reviews are refused by method", any("the policy allows" in f for f in sg(scraped))))
        stale = clone(card); stale["blocks"][0]["items"][0]["score_basis"]["sources"][0] = dict(GMAPS, reviews_file="old.json")
        cases.append(("reviews read 9 months ago are stale", any("read them again" in f for f in sg(stale))))
        ghost = clone(card); ghost["blocks"][0]["items"][0]["score_basis"]["themes"] = [{"text": "t", "review_ids": ["g0", "g99"]}]
        cases.append(("theme citing an unread review fails", any("not in the read files" in f for f in sg(ghost))))
        typed = clone(card); typed["blocks"][0]["items"][0]["score"] = 2.0
        cases.append(("typed score fails recomputation", bool(sg(typed))))
        one = clone(card); one["blocks"][0]["items"][0]["score_basis"] = v2([GMAPS], guest=4.0)
        one["blocks"][0]["items"][0]["score"] = 4.0; one["blocks"][0]["items"][0]["score_basis"]["themes"] = [{"text": "t", "review_ids": ["g0"]}]
        cases.append(("v2 on one source alone fails", any("rests on 1 source" in f for f in sg(one))))
        one["blocks"][0]["items"][0]["score_basis"]["sources"].append({"name": "Booking.com reviews", "unavailable": "not listed there"})
        cases.append(("v2 on one source plus a declared-unavailable one passes", sg(one) == []))
        thin = clone(one); thin["blocks"][0]["items"][0]["score_basis"]["sources"][0] = dict(GMAPS, read=8, reviews_file="small.json")
        thin["blocks"][0]["items"][0]["score_basis"]["themes"] = [{"text": "t", "review_ids": ["s0"]}]
        cases.append(("v2 doesn't count a source with under 20 reviews read", any("no review source counts" in f for f in sg(thin))))
        write("g2.json", "h")   # Google reviews declared at a Booking url
        mislabel = clone(card); mislabel["blocks"][0]["items"][0]["score_basis"]["sources"][1] = dict(BOOKING, reviews_file="g2.json")
        cases.append(("a Google review file declared at a Booking url is refused", any("google reader" in f for f in sg(mislabel))))
        selfrev = clone(card); selfrev["blocks"][0]["items"][0]["score_basis"]["sources"].append(
            {"name": "Hotel guestbook", "url": f"{OWN}/reviews", "read": 20, "reviews_file": "own.json"})
        cases.append(("v2 refuses the property's own reviews", any("self-curated" in f for f in sg(selfrev))))
        bump = clone(card); b0 = bump["blocks"][0]["items"][0]
        b0["score"] = 4.7; b0["score_basis"]["group_fit"] = [{"signal": "kids play area", "trait": "needs_kids_play_area", "fact": {"facts": 0}, "bump": 0.1,
                                                                "reason": "the group asked for a play area"}]
        cases.append(("v2 bump on a stated trait and a real fact passes", sg(bump) == []))
        noreason = clone(bump); del noreason["blocks"][0]["items"][0]["score_basis"]["group_fit"][0]["reason"]
        cases.append(("an overlay with no reason fails", any("gives no reason" in f for f in sg(noreason))))
        # The agreed fit profile: weights, signs and evidence demands per criterion.
        prof = clone(bump); prof["group_traits"] = []
        prof["fit_criteria"] = [{"id": "kids_play", "weight": 0.3, "polarity": "+", "evidence": "fact"},
                                {"id": "near_sights", "weight": 0.4, "polarity": "+", "evidence": "maps"},
                                {"id": "beside_highway", "weight": 0.3, "polarity": "-", "evidence": "fact"}]
        p0 = prof["blocks"][0]["items"][0]
        p0["facts"].append({"label": "Walk to the old town", "value": "12 min walk", "source": "https://www.google.com/maps/dir/a/b"})
        p0["facts"].append({"label": "Setting", "value": "Beside the national highway"})
        def ov(criterion, fact, bump, **kw):
            return dict({"signal": criterion, "criterion": criterion, "fact": fact, "bump": bump, "reason": "fits the agreed profile"}, **kw)
        p0["score_basis"]["group_fit"] = [ov("kids_play", {"facts": 0}, 0.3), ov("near_sights", {"facts": 2}, 0.4), ov("beside_highway", {"facts": 3}, -0.3)]
        p0["score"] = 5.0   # 4.6 + 0.3 + 0.4 - 0.3 = 5.0
        cases.append(("overlays up, up and down sum onto the guest score", sg(prof) == []))
        over = clone(prof); over["blocks"][0]["items"][0]["score_basis"]["group_fit"][0]["bump"] = 0.4
        cases.append(("an overlay above its criterion's agreed weight fails", any("at most 0.3" in f for f in sg(over))))
        wrongsign = clone(prof); wrongsign["blocks"][0]["items"][0]["score_basis"]["group_fit"][2]["bump"] = 0.2
        cases.append(("a drawback criterion can't add points", any("drawback (-)" in f for f in sg(wrongsign))))
        unmeasured = clone(prof); unmeasured["blocks"][0]["items"][0]["score_basis"]["group_fit"][1]["fact"] = {"facts": 0}
        cases.append(("a 'near the sights' overlay must rest on a map measurement", any("measured on a map" in f for f in sg(unmeasured))))
        viaconcern = clone(prof); vc = viaconcern["blocks"][0]["items"][0]
        vc["score_basis"]["concerns"] = [{"text": "traffic noise at night", "review_ids": ["g0", "b0"]}]
        vc["score_basis"]["group_fit"][2] = ov("beside_highway", None, -0.3, concern=0)
        cases.append(("a drawback may rest on a corroborated concern", sg(viaconcern) == []))
        vc["score_basis"]["group_fit"][2]["concern"] = 5
        cases.append(("a drawback pointing at no concern fails", any("isn't in this card's concerns" in f for f in sg(viaconcern))))
        capped = clone(prof); c0 = capped["blocks"][0]["items"][0]
        capped["fit_criteria"] = [{"id": f"q{i}", "weight": 0.5, "polarity": "+"} for i in range(3)]
        c0["facts"] += [{"label": "View", "value": "Lake view"}, {"label": "Extras", "value": "Projector room"}]
        c0["score_basis"]["group_fit"] = [ov("q0", {"facts": 0}, 0.5), ov("q1", {"facts": 4}, 0.5), ov("q2", {"facts": 5}, 0.5)]
        c0["score"] = 5.0   # 4.6 + min(1.5, 1.0) -> 5.6 -> capped at 5.0
        cases.append(("overlays together are capped at +1.0 and the score at 5", sg(capped) == []))
        opened = clone(bump); opened["blocks"][0]["items"][0]["score_basis"]["group_fit"][0].update(trait=None, criterion="universal:forest_canopy")
        cases.append(("an open universal quality is allowed when evidenced", sg(opened) == []))
        cases.append(("a fact a bump rests on becomes a checked row", any(r["kind"] == "feature" and "Kids play area" in r["row"] for r in ledger(bump))))
        neg = clone(bump); neg["blocks"][0]["items"][0]["score_basis"]["group_fit"][0]["fact"] = {"facts": 1}
        cases.append(("v2 bump resting on 'No pool' fails", any("absent or says nothing" in f for f in sg(neg))))
        dangling = clone(bump); dangling["blocks"][0]["items"][0]["score_basis"]["group_fit"][0]["fact"] = {"facts": 9}
        cases.append(("v2 bump pointing at no fact fails", any("is not one fact" in f for f in sg(dangling))))
        title_only = clone(bump); title_only["blocks"][0]["items"][0]["score_basis"]["group_fit"][0]["fact"] = {"dossier": REQUIRED_DOSSIER[0]}
        cases.append(("v2 bump pointing at a section title fails", any("is not one fact" in f for f in sg(title_only))))
        unsaid = clone(bump); unsaid["blocks"][0]["items"][0]["score_basis"]["group_fit"][0]["trait"] = "wants_nightlife"
        cases.append(("v2 bump on a trait the group never stated fails", any("neither a criterion" in f for f in sg(unsaid))))
        univ = clone(bump); univ["group_traits"] = []; univ["blocks"][0]["items"][0]["score_basis"]["group_fit"][0]["trait"] = "universal:unique_view"
        cases.append(("v2 bump on a universal signal needs no stated trait", sg(univ) == []))

        # Concerns: corroboration across classed sources, watch-outs included, own site excluded.
        REDDIT = {"name": "Reddit", "url": "https://www.reddit.com/r/travel/x", "read": 5, "reviews_file": "rd.json"}
        def with_concern(concern, extra=()):
            s = clone(card); s["blocks"][0]["items"][0]["score_basis"]["sources"] += list(extra)
            s["blocks"][0]["items"][0]["score_basis"]["concerns"] = [concern]
            return s
        cg = lambda s: concern_gate(s, td, policy(s))
        cases.append(("an isolated single-review concern fails", any("not corroborated" in f for f in cg(with_concern({"text": "noisy aircon", "review_ids": ["g0"]})))))
        cases.append(("Google + Reddit corroborate a concern", cg(with_concern({"text": "construction next door", "review_ids": ["g0", "r0"]}, [REDDIT])) == []))
        cases.append(("Google + verified stays corroborate a concern", cg(with_concern({"text": "thin walls", "review_ids": ["g0", "b0"]})) == []))
        cases.append(("a concern cited by >=15% of >=20 reviews read passes", cg(with_concern({"text": "slow lifts", "review_ids": [f"g{i}" for i in range(7)]})) == []))
        small_c = clone(card); small_c["blocks"][0]["items"][0]["score_basis"]["sources"] = [dict(GMAPS, read=8, reviews_file="small.json")]
        small_c["blocks"][0]["items"][0]["score_basis"]["concerns"] = [{"text": "within a tiny sample", "review_ids": ["s0", "s1"]}]
        cases.append(("the percentage branch can't fire below 20 reviews read", any("not corroborated" in f for f in cg(small_c))))
        own_c = with_concern({"text": "x", "review_ids": ["g0", "w0"]}, [{"name": "Hotel guestbook", "url": f"{OWN}/reviews", "read": 20, "reviews_file": "own.json"}])
        cases.append(("the property's own reviews can't corroborate a concern", any("not in the read files" in f for f in cg(own_c))))
        watch = clone(card); watch["blocks"][0]["items"][0]["watch"] = [{"text": "one guest reported bedbugs", "review_ids": ["g3"]}]
        cases.append(("an uncorroborated review complaint in watch fails", any("not corroborated" in f for f in cg(watch))))
        watch["blocks"][0]["items"][0]["watch"][0]["isolated"] = True
        cases.append(("the same watch-out marked isolated passes (shown as a single report)", cg(watch) == []))

        # Evidence folder: only files inside evidence/ count as saved confirmations.
        Path(td, "evidence").mkdir(exist_ok=True); Path(td, "evidence", "conf.pdf").write_text("confirmation", encoding="utf-8")
        Path(td, "spec.json").write_text("{}", encoding="utf-8")
        cases.append(("evidence/../spec.json is not evidence", not evidence_ok("evidence/../spec.json", td) and evidence_ok("evidence/conf.pdf", td)))
        # A rating source can't be one file listed twice, or a file of repeats.
        long_ = [f"A long review text number {i} about the quiet rooms and the breakfast" for i in range(10)]
        dd = {"method": "google-places-api", "retrieved_at": "2026-10-06T00:00:00Z", "reviews": [
            {"id": f"d{i}", "stars": 4, "text": long_[i % 10], "sha256": hashlib.sha256(long_[i % 10].encode()).hexdigest()} for i in range(20)]}
        Path(td, "dup.json").write_text(json.dumps(dd), encoding="utf-8")
        rep_ = clone(card); rep_["blocks"][0]["items"][0]["score_basis"]["sources"][0] = dict(GMAPS, reviews_file="dup.json")
        cases.append(("a review file that repeats its reviews fails", any("repeats reviews" in f for f in sg(rep_))))
        twice = clone(card); twice["blocks"][0]["items"][0]["score_basis"]["sources"][1] = dict(GMAPS, name="Google Maps reviews (again)")
        cases.append(("one review file listed as two sources fails", any("again" in f for f in sg(twice))))
        # One criterion's weight caps all of its overlays together; one fact earns one overlay.
        split = clone(capped); s0 = split["blocks"][0]["items"][0]
        s0["score_basis"]["group_fit"] = [ov("q0", {"facts": 0}, 0.3), ov("q0", {"facts": 4}, 0.3)]
        s0["score"] = 5.0
        cases.append(("two overlays on one criterion can't exceed its weight together", any("add up to" in f for f in sg(split))))
        twin_fact = clone(capped); t0 = twin_fact["blocks"][0]["items"][0]
        t0["score_basis"]["group_fit"] = [ov("q0", {"facts": 0}, 0.2), ov("q1", {"facts": 0}, 0.2)]
        t0["score"] = 5.0
        cases.append(("one fact can't earn two overlays", any("same fact" in f for f in sg(twin_fact))))
        # A fact a ranking rests on must be stated by its captures.
        lakeless = clone(bump)
        mism = {r["row"]: {"verdict": "CONFIRMED", "captures": [cap("Garden view" if "fact0" in r["row"] else r["value"], r["src"])] * 2} for r in ledger(lakeless)}
        cases.append(("a cited fact whose captures say something else fails", any("do not state" in f for f in run(lakeless, mism, base_dir=td))))

        # Intake: the spec carries what the group said, nothing invented, nothing dropped.
        Path(td, "trip_intake.json").write_text(json.dumps({"group_profile": {"traits": ["needs_kids_play_area"]},
                                                            "dates": {"start": "2030-05-02", "end": "2030-05-06"}}), encoding="utf-8")
        same = {"group_traits": ["needs_kids_play_area"], "trip": {"start": "2030-05-02", "end": "2030-05-06"}}
        cases.append(("spec matching trip_intake.json passes", intake_gate(same, td) == []))
        cases.append(("an invented trait fails against trip_intake.json", bool(intake_gate(dict(same, group_traits=["needs_kids_play_area", "foodie"]), td))))
        cases.append(("drifted trip dates fail against trip_intake.json", bool(intake_gate(dict(same, trip={"start": "2030-05-03", "end": "2030-05-06"}), td))))
        Path(td, "trip_intake.json").unlink()

        # Critical facts need two independent sources: two sites, or a saved confirmation plus the site.
        Path(td, "evidence").mkdir(exist_ok=True); Path(td, "evidence", "conf.pdf").write_text("confirmation", encoding="utf-8")
        bl = {"as_of": "2026-10-09", "trip": {"start": "2030-05-02", "end": "2030-05-06"}, "blocks": [{"type": "booking_ledger", "id": "b", "title": "Bookings",
              "bookings": [{"name": "Hotel A", "amount": 100, "currency": "EUR", "status": "upcoming", "cancel_by": "2026-11-01",
                            "url": "https://www.booking.com/x", "category": "stay", "check_in": "2030-05-02", "check_out": "2030-05-04"}],
              "summary": {"EUR": 100}}]}
        bl_rows = ledger(bl)
        same_site = {r["row"]: {"verdict": "CONFIRMED", "captures": [cap(r["value"], "https://www.booking.com/x")] * 2} for r in bl_rows}
        cases.append(("a booking amount backed twice by one site fails", any("independent source" in f for f in run(bl, same_site, base_dir=td))))
        two_src = {r["row"]: {"verdict": "CONFIRMED", "captures": [cap(r["value"], "https://www.booking.com/x"), cap(r["value"], "evidence/conf.pdf")]} for r in bl_rows}
        cases.append(("a booking backed by the site plus a saved confirmation passes", run(bl, two_src, base_dir=td) == []))
        cases.append(("booking amounts and every booking date are critical rows", {r["field"] for r in bl_rows if r["critical"]} == {"amount", "cancel_by", "check_in", "check_out"}))

        # The CLI gate passes the project folder through, so a scored spec can pass from the command line.
        cli = clone(card); cli["blocks"][0]["items"][0]["score_basis"]["concerns"] = []
        now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for fn in ("g.json", "b.json"):
            d = json.loads(Path(td, fn).read_text(encoding="utf-8")); d["retrieved_at"] = now
            Path(td, fn).write_text(json.dumps(d), encoding="utf-8")
        cli_raw = json.dumps(cli).encode(); Path(td, "spec.json").write_bytes(cli_raw)
        cli_rows = {r["row"]: {"verdict": "CONFIRMED", "captures": [cap(r["value"], r["src"], at=now)] * 2} for r in ledger(cli)}
        Path(td, "review.json").write_text(json.dumps({"spec_sha256": sha(cli_raw), "scope": "full", "rows": cli_rows}), encoding="utf-8")
        Path(td, "trip_intake.json").write_text(json.dumps({"group_profile": {"traits": ["needs_kids_play_area"]}}), encoding="utf-8")
        Path(td, "spot.json").write_text(json.dumps(dict(auto_spot(cli), review_sha256=sha(Path(td, "review.json")))), encoding="utf-8")
        ns = argparse.Namespace(spec=str(Path(td, "spec.json")), review=str(Path(td, "review.json")), spot=str(Path(td, "spot.json")), rail=None, store=None, base_dir=None)
        import contextlib, io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli_ok = cmd_gate(ns) == 1
        cases.append(("guard.py gate (CLI) refuses to run without a capture store", cmd_gate(ns) == 1))
        if not cli_ok:
            print(out.getvalue())

    # Transport: every stated figure is a row; air and rail are door-to-door.
    leg_spec = {"blocks": [{"type": "transport_leg", "id": "t", "title": "Transport", "legs": [
        {"label": "Town A to Town B", "modes": [
            {"mode": "road", "time_minutes": 300, "distance_km": 320, "source": "https://www.google.com/maps/dir/a/b", "captured_at": WHEN},
            {"mode": "air", "note": "no commercial route"}],
         "recommendation": {"mode": "road", "reasons": ["only viable mode"]}}]}]}
    leg_rows = transport_leg_rows(leg_spec)
    cases.append(("transport_leg checks time and distance", sorted(r["kind"] for r in leg_rows) == ["duration", "km"]))
    tbl = {"blocks": [{"type": "table", "id": "t", "columns": [{"key": "name", "label": "Name"}, {"key": "price", "label": "Price", "type": "num"},
                                                         {"key": "drive", "label": "Drive (min)", "type": "num"}, {"key": "pool", "label": "Pool", "type": "cat"}],
                       "items": [{"name": "No card", "url": "https://www.booking.com/x", "price": 120, "drive": 45, "pool": "Shared",
                                  "sources": {"drive": "https://www.google.com/maps/dir/a/b"}}]}]}
    trows = {r["field"]: r for r in table_only_rows(tbl)}
    cases.append(("a table row with no card has its figures checked, with per-column sources",
                  set(trows) == {"price", "drive"} and trows["drive"]["src"].startswith("https://www.google.com/maps")))
    cases.append(("an unchecked table-only figure fails the gate", any("No card|price" in f and "no reviewer verdict" in f for f in run(tbl, {}))))
    cases.append(("an uncaptured transport figure fails the gate", any("no reviewer verdict" in f for f in run(leg_spec, {}))))
    cases.append(("a captured transport figure passes", run(leg_spec, verdicts_for(leg_spec)) == []))
    air = {"blocks": [{"type": "transport_leg", "id": "t", "legs": [{"label": "A to B", "modes": [
        {"mode": "air", "time_minutes": 70, "source": "https://www.google.com/travel/flights/x", "captured_at": WHEN}]}]}]}
    cases.append(("air without door-to-door segments fails", any("door-to-door" in f for f in door_to_door_gate(air))))
    air["blocks"][0]["legs"][0]["modes"][0].update(time_minutes=250, segments=[
        {"label": "to airport", "time_minutes": 60, "source": "https://www.google.com/maps/dir/x"},
        {"label": "flight", "time_minutes": 70, "main": True, "source": "https://www.google.com/travel/flights/x"},
        {"label": "from airport", "time_minutes": 45, "source": "https://www.google.com/maps/dir/y"}])
    cases.append(("air time that isn't the sum of its segments fails", any("not the sum" in f for f in door_to_door_gate(air))))
    air["blocks"][0]["legs"][0]["modes"][0]["time_minutes"] = 175
    cases.append(("air time equal to its segments' sum passes", door_to_door_gate(air) == []))
    cases.append(("the flight segment is critical, the airport runs are not",
                  [r["critical"] for r in transport_leg_rows(air)] == [False, True, False]))

    # Plan consistency.
    plan = {"trip": {"start": "2030-05-02", "end": "2030-05-04"}, "blocks": [
        {"type": "transport_leg", "id": "t", "legs": [{"label": "Town A to Town B", "from": "Town A", "to": "Town B",
         "modes": [{"mode": "road", "time_minutes": 120}], "recommendation": {"mode": "road"}}]},
        {"type": "day_plan", "id": "d", "days": [
            {"date": "2030-05-02", "city": "Town A", "stops": [{"time": "09:00", "name": "Leave", "leg": "Town A to Town B"},
                                                                {"time": "11:30", "name": "Lunch"}]},
            {"date": "2030-05-03", "city": "Town B", "stops": [{"name": "Free day"}]}]}]}
    cases.append(("a consistent plan passes", plan_gate(plan) == []))
    late = clone(plan); late["blocks"][1]["days"][0]["stops"][1]["time"] = "10:00"
    cases.append(("a stop sooner than the leg before it allows fails", any("leaves less than" in f for f in plan_gate(late))))
    order = clone(plan); order["blocks"][1]["days"][0]["stops"][1]["time"] = "08:00"
    cases.append(("a stop timed before the one ahead of it fails", any("before the stop listed ahead" in f for f in plan_gate(order))))
    outside = clone(plan); outside["blocks"][1]["days"][1]["date"] = "2030-05-09"
    cases.append(("a day outside the trip fails", any("outside the trip" in f for f in plan_gate(outside))))
    jump = clone(plan); jump["blocks"][1]["days"][1]["city"] = "Town C"
    cases.append(("a change of city with no leg fails", any("no transport leg" in f for f in plan_gate(jump))))
    cases.append(("a plan with no trip dates fails", bool(plan_gate({"blocks": plan["blocks"]}))))
    stays = {"trip": {"start": "2030-05-02", "end": "2030-05-06"}, "blocks": [{"type": "booking_ledger", "id": "b", "bookings": [
        {"name": "H1", "category": "stay", "check_in": "2030-05-02", "check_out": "2030-05-04", "status": "paid"},
        {"name": "H2", "category": "stay", "check_in": "2030-05-03", "check_out": "2030-05-05", "status": "paid"}]}]}
    cases.append(("two stays on the same night fail", any("both cover the night" in f for f in plan_gate(stays))))
    cases.append(("a fixed-time stop becomes a critical time row", any(r["kind"] == "time" and r["critical"] for r in day_plan_rows(
        {"blocks": [{"type": "day_plan", "id": "d", "days": [{"date": "2030-05-02", "stops": [{"time": "07:10", "name": "Flight", "type": "flight"}]}]}]}))))

    real_wd = WEEKDAYS[dt.date(2030, 5, 2).weekday()]
    wrong_wd = WEEKDAYS[(dt.date(2030, 5, 2).weekday() + 1) % 7]
    ok_label, bad_label = clone(plan), clone(plan)
    ok_label["blocks"][1]["days"][0]["label"] = f"{real_wd.capitalize()} 2 May"
    bad_label["blocks"][1]["days"][0]["label"] = f"{wrong_wd.capitalize()} 2 May"
    cases.append(("a day label with the right weekday passes", plan_gate(ok_label) == []))
    cases.append(("a day label whose weekday does not match its date fails", any("is a " in f for f in plan_gate(bad_label))))
    off = clone(plan)
    off["blocks"][1]["days"][0]["stops"].insert(0, {"time": "23:00", "name": "First idea", "type": "flight", "status": "rejected", "note": "closed that day"})
    cases.append(("a rejected stop is neither ordered nor ledgered", plan_gate(off) == [] and not [r for r in day_plan_rows(off) if "First idea" in r["item"]]))
    cost = {"blocks": [{"type": "cost_comparison", "id": "cc", "options": [
        {"label": "Pay cash", "cash": {"amount": 420, "currency": "EUR"}, "source": "https://www.airexample.test/fare", "captured_at": "2030-04-01T00:00:00Z"},
        {"label": "Use points", "cash": {"amount": 60, "currency": "EUR"}, "points": {"amount": 30000, "programme": "Plan A"},
         "source": "https://www.airexample.test/award", "captured_at": "2030-04-01T00:00:00Z"}]}]}
    kinds = sorted((r["field"], r["kind"]) for r in cost_rows(cost))
    cases.append(("a cost comparison's cash and points prices become checked rows", kinds == [("cash", "price"), ("cash", "price"), ("points", "points")]))
    cases.append(("a points price is never sourced from a review platform", "review_platform" not in KIND_SOURCES["points"] and "points" in FRESHNESS_DAYS))

    # Booking arithmetic.
    bl_clean = {"as_of": "2026-10-09", "blocks": [{"type": "booking_ledger", "id": "b", "title": "Bookings", "bookings": [
        {"name": "Hotel A", "amount": 100, "currency": "EUR", "status": "upcoming", "cancel_by": "2026-11-01"},
        {"name": "Hotel B", "amount": 50, "currency": "EUR", "status": "paid"}], "summary": {"EUR": 150}}]}
    cases.append(("clean booking_ledger passes", booking_ledger_gate(bl_clean) == []))
    bl_mismatch = clone(bl_clean); bl_mismatch["blocks"][0]["summary"]["EUR"] = 999
    cases.append(("booking_ledger sum mismatch fails", any("does not match the sum" in f for f in booking_ledger_gate(bl_mismatch))))
    bl_late = clone(bl_clean); bl_late["blocks"][0]["bookings"][0].update(check_in="2026-10-20", cancel_by="2026-11-01")
    cases.append(("a cancel_by after the check_in fails", any("after check_in" in f for f in booking_ledger_gate(bl_late))))
    bl_expired = clone(bl_clean); bl_expired["blocks"][0]["bookings"][0]["cancel_by"] = "2026-01-01"
    cases.append(("expired cancel_by still 'upcoming' fails", any("still 'upcoming'" in f for f in booking_ledger_gate(bl_expired))))
    bl_no_date = clone(bl_clean); del bl_no_date["as_of"]
    cases.append(("bookings with cancel_by but no as_of fail (never skipped)", any("as_of" in f for f in booking_ledger_gate(bl_no_date))))

    # Policy version: a mismatch warns, never fails.
    cases.append(("no policy_version declared: no warn", policy_version_warns(spec) == []))
    cases.append(("matching policy_version: no warn", policy_version_warns({**spec, "policy_version": POLICY_VERSION}) == []))
    cases.append(("mismatched policy_version: warns, does not fail", policy_version_warns({**spec, "policy_version": POLICY_VERSION + 1}) != []))
    ok_all = all(p for _, p in cases)
    for name, p in cases:
        print(("PASS" if p else "FAIL"), name)
    print(f"SELFTEST {'PASS' if ok_all else 'FAIL'} ({len(cases)} cases)")
    return 0 if ok_all else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("ledger"); p.add_argument("spec"); p.add_argument("out", nargs="?", default=None, help="default: ledger.json beside the spec"); p.set_defaults(f=cmd_ledger)
    p = sp.add_parser("sample"); p.add_argument("ledger"); p.add_argument("--rate", type=float, default=SPOT_RATE)
    p.add_argument("--min", type=int, default=SPOT_MIN); p.add_argument("--seed", type=int, default=None, help="selftests only: the gate expects the seed derived from the ledger")
    p.add_argument("--out", default=None, help="default: spot.plan.json beside the ledger"); p.set_defaults(f=cmd_sample)
    p = sp.add_parser("gate"); p.add_argument("spec"); p.add_argument("review"); p.add_argument("spot", nargs="?", default=None); p.add_argument("--rail", default=None, help="rail_distance.py output (Maps)"); p.add_argument("--store", default=None, help="capture store: spot agreement is recomputed from it"); p.add_argument("--base-dir", default=None, help="project folder holding review files, trip_intake.json and evidence/ (default: the spec's folder)"); p.set_defaults(f=cmd_gate)
    p = sp.add_parser("evals"); p.add_argument("spec"); p.add_argument("golden"); p.set_defaults(f=cmd_evals)
    p = sp.add_parser("selftest"); p.set_defaults(f=lambda a: selftest())
    a = ap.parse_args()
    sys.exit(a.f(a))


if __name__ == "__main__":
    main()
