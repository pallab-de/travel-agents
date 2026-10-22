"""Synthetic ranked-stay report (a report-builder spec, its built page and its evidence) for calibrating the facts and the
judgement reviewers (used by build_fixtures.py; maintainers only).

A fictional region (Valmora) and twelve stays for one group (two adults and two teenagers who want a quiet weekend with
things to do on site), written as the generic engine's blocks: cards with facts, a dossier, a rank-score-v2 basis (two review
sources of 20 reviews each, themes, a corroborated concern, group-fit overlays against the agreed fit profile) and an
explorer table. The evidence is everything the figures came from: the listing pages' text, the Maps measurements, the stored
review files and the trip brief. The CLEAN spec passes fact-guard's score, concern, table and ranking checks (asserted);
`plant` seeds known defects into eight cards and the caller discards the clean version. Four cards (p09-p12) are decoys:
correct, but each looks like it could be wrong.

A reviewer cites a card by its id (`p01`..`p12`). A second defect on the same card has a hyphenated id (`p03-quiet`), and a
finding on the card itself counts for it.
"""
import copy, hashlib, random

CODE_A = "https://stays.platform-a.test/"
CODE_B = "https://reviews.platform-b.test/"
MAPS = "https://maps.example-travel.test/route/"
POS = ["Spotless rooms and a lovely terrace", "The host was welcoming and breakfast was generous", "Peaceful, easy to unwind here",
       "Clean, comfortable, a well kept garden", "The caretaker was very helpful with everything", "Beautiful setting and very clean"]
NEG = ["The wifi dropped twice during our stay", "Hot water took a while to arrive", "Check-in was slower than we expected"]
NOISE = "The road outside is loud at night and the windows let it in"
NOISE_A, NOISE_B = (1, 4, 7, 9, 12), (2, 5, 8)
CRITERIA = [{"id": "lake_view", "weight": 0.3, "polarity": "+", "evidence": "fact"},
            {"id": "on_site_games", "weight": 0.2, "polarity": "+", "evidence": "fact"},
            {"id": "near_main_road", "weight": 0.2, "polarity": "-", "evidence": "fact"}]
# id, name, area, price, drive, platform A stars (n of 5s, n of 4s, n of 3s), B likewise, view, games, flags
PLACES = [
    ("p01", "Larkspur Lakehouse", "Northfold", 540, 52, (7, 10, 3), (6, 11, 3), "Lake view from the terrace", "Games room with a table-tennis table", ""),
    ("p02", "Willow Brook Farm", "Eastmere", 460, 61, (6, 11, 3), (6, 10, 4), "Garden and orchard view", "Board games and a pool table in the barn", "road"),
    ("p03", "Amber Hollow Lodge", "Kellby", 395, 44, (5, 11, 4), (7, 9, 4), "Hill view from two bedrooms", "Garden games and a hammock area", ""),
    ("p04", "Juniper Ridge Retreat", "Ravenmoor", 610, 78, (12, 8, 0), (9, 10, 1), "Lake view from every bedroom", "Games room and a heated pool", ""),
    ("p05", "Corvin Meadow Villa", "Northfold", 365, 38, (4, 9, 7), (3, 10, 7), "Courtyard view", "Two bicycles to borrow", ""),
    ("p06", "Hollis Grove Stay", "Eastmere", 430, 57, (5, 10, 5), (4, 11, 5), "Fields all around", "Outdoor table tennis", ""),
    ("p07", "Kestrel Orchard House", "Kellby", 500, 49, (7, 9, 4), (5, 11, 4), "Orchard view", "Games room", ""),
    ("p08", "Merrow Stone Cottage", "Ravenmoor", 340, 70, (2, 8, 10), (2, 10, 8), "Village view", "None listed", ""),
    ("p09", "Alder Cove Cottage", "Northfold", 480, 55, (18, 2, 0), (16, 4, 0), "Cove view from the garden", "Kayaks to borrow", ""),
    ("p10", "Redmere Brook House", "Eastmere", 445, 63, (6, 11, 3), (5, 11, 4), "Brook view", "Games cupboard", "renamed"),
    ("p11", "Stonecroft Roadside Lodge", "Kellby", 330, 41, (5, 10, 5), (4, 11, 5), "Hill view", "Games room", "road"),
    ("p12", "Tarrant Glen Farm", "Ravenmoor", 515, 66, (8, 9, 3), (6, 11, 3), "Lake view from the terrace", "Pool table in the barn", "unavailable"),
]


def stars_of(counts):
    five, four, three = counts
    return [5] * five + [4] * four + [3] * three


def review_doc(pid, src, stars, method, noise_idx=()):
    rng = random.Random(f"{pid}{src}")
    revs = []
    for i, st in enumerate(stars):
        base = NOISE if i in noise_idx else (rng.choice(POS) if st >= 4 else rng.choice(NEG))
        text = f"[synthetic {src}] {base} (guest {i + 1} of {len(stars)}, platform {src.upper()})."
        revs.append({"id": f"{src}{i}", "stars": st, "when": "2030-04-01", "text": text, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()})
    return {"method": method, "retrieved_at": "2030-05-06T00:00:00Z", "note": "synthetic example data", "reviews": revs}


def world():
    """Everything the clean report is built from."""
    T = {"places": [], "reviews": {}, "listings": {}, "maps": {}}
    mean = lambda xs: sum(xs) / len(xs)
    for pid, name, area, price, drive, a, b, view, games, flags in PLACES:
        sa, sb = stars_of(a), stars_of(b)
        noisy = "road" in flags
        T["reviews"][f"{pid}.a"] = review_doc(pid, "a", sa, "apify-booking-reviews", noise_idx=NOISE_A if noisy else ())
        T["reviews"][f"{pid}.b"] = review_doc(pid, "b", sb, "google-places-api", noise_idx=NOISE_B if noisy else ())
        guest = round((1.5 * mean(sa) + 1.0 * mean(sb)) / 2.5, 1)
        T["places"].append({"id": pid, "name": name, "area": area, "price": price, "drive": drive, "guest": guest, "ra": round(mean(sa), 1),
                            "rb": round(mean(sb), 1), "view": view, "games": games, "noisy": noisy, "renamed": "renamed" in flags,
                            "unavailable": "unavailable" in flags})
        extra = " Also listed as Brook House before its rename." if "renamed" in flags else ""
        extra += " Not available 17-19 May." if "unavailable" in flags else ""
        T["listings"][pid] = {"url": f"{CODE_A}{pid}", "price_for_2_nights": price, "currency": "EUR", "rating": round(mean(sa), 1), "reviews": 20,
                              "captured_at": "2030-05-04", "page_text": f"{name}: EUR {price} for 2 nights. {round(mean(sa), 1)} from 20 reviews. {view}. {games}.{extra}"}
        T["maps"][pid] = {"url": f"{MAPS}{pid}", "drive_minutes": drive, "captured_at": "2030-05-04"}
    return T


def card(T, p, rank):
    pid = p["id"]
    facts = [{"label": "Drive from home", "value": f"{p['drive']} min", "source": T["maps"][pid]["url"]},
             {"label": "View", "value": p["view"], "source": T["listings"][pid]["url"]},
             {"label": "On-site extras", "value": p["games"], "source": T["listings"][pid]["url"]}]
    if p["noisy"]:
        facts.append({"label": "Setting", "value": "Fronts the state road", "source": T["listings"][pid]["url"]})
    noise_ids = [f"a{i}" for i in NOISE_A] + [f"b{i}" for i in NOISE_B]
    concerns = [{"text": "Road noise at night", "review_ids": noise_ids}] if p["noisy"] else []
    overlays = []
    if "lake" in p["view"].lower():
        overlays.append({"signal": "lake view", "criterion": "lake_view", "bump": 0.3, "fact": {"facts": 1}, "reason": "they want to unwind by the water"})
    if any(w in p["games"].lower() for w in ("games room", "table-tennis", "pool table")):
        overlays.append({"signal": "games on site", "criterion": "on_site_games", "bump": 0.2, "fact": {"facts": 2}, "reason": "something to do without driving"})
    if p["noisy"]:
        overlays.append({"signal": "road noise", "criterion": "near_main_road", "bump": -0.2, "concern": 0, "reason": "two of the group sleep lightly"})
    score = max(1.0, min(5.0, round(p["guest"] + sum(o["bump"] for o in overlays), 1)))
    p["score"] = score
    c = {"id": pid, "name": p["name"], "url": T["listings"][pid]["url"], "area": p["area"], "rank": rank, "price": float(p["price"]), "priceNote": "for 2 nights, EUR",
         "score": score, "facts": facts, "pros": [p["view"]], "watch": [{"text": "Road noise at night", "review_ids": noise_ids}] if p["noisy"] else [],
         "verdict": f"{p['name']}: guests rate it {p['guest']}, {p['drive']} min away.",
         "dossier": [{"title": "Map listing", "kind": "facts", "source": T["maps"][pid]["url"], "rows": [["Area", p["area"]]]},
                     {"title": "Guest ratings", "kind": "ratings", "items": [{"label": "Platform A", "rating": p["ra"], "count": 20, "url": CODE_A + pid},
                                                                           {"label": "Platform B", "rating": p["rb"], "count": 20, "url": CODE_B + pid}]},
                     {"title": "Guest reviews", "not_found": "the reviews are in the stored review files"},
                     {"title": "Photos", "not_found": "synthetic example: no photos"}],
         "score_basis": {"method": "read-reviews", "formula": "rank-score-v2",
                         "rubric": "Weighted mean of the stars on the reviews read (Platform A 1.5, Platform B 1.0), plus overlays.", "guest_score": p["guest"],
                         "sources": [{"name": "Platform A reviews", "url": CODE_A + pid, "read": 20, "reviews_file": f"reviews/{pid}.a.json"},
                                     {"name": "Platform B reviews", "url": CODE_B + pid, "read": 20, "reviews_file": f"reviews/{pid}.b.json"},
                                     {"name": "web research", "url": f"https://climate.example.test/{pid}", "read_at": "2030-05-04"}],
                         "themes": [{"text": "calm, clean stays", "review_ids": ["a0", "b0"]}], "concerns": concerns, "group_fit": overlays}}
    if p["unavailable"]:
        c["unavailable"] = True
    return c


def build_spec(T, design):
    for p in T["places"]:
        card(T, p, 0)   # computes each rank score
    ranked = sorted(T["places"], key=lambda p: (-p["score"], p["id"]))
    cards = [card(T, p, i) for i, p in enumerate(ranked, 1)]
    rows = [{"name": c["name"], "id": c["id"], "rank": c["rank"], "price": c["price"], "score": c["score"], "url": c["url"],
             "drive": next(int(f["value"].split()[0]) for f in c["facts"] if f["label"] == "Drive from home"),
             **({"unavailable": True} if c.get("unavailable") else {})} for c in cards]
    blocks = [{"type": "hero", "eyebrow": "Four people, one weekend", "title": "Stays near Valmora",
               "lead": "Two adults and two teenagers who want a quiet weekend with things to do on site: twelve stays ranked on fit, guest reviews and drive time.",
               "kpis": [{"value": "12", "label": "stays compared", "derived": {"count": "ranked"}}, {"value": f"EUR {min(r['price'] for r in rows if not r.get('unavailable'))}", "label": "lowest price (available)", "derived": {"min": "all.price"}}], "image": "hero-placeholder.png", "image_alt": "Abstract placeholder"},
              {"type": "notice", "title": "Read this first", "items": ["Guest scores come from 20 reviews on each of two sites.", "Overlays reward what this group asked for and mark what it avoids.", "Prices are for two nights, in euros, read on 4 May."]},
              {"type": "cards", "id": "ranked", "title": "The shortlist", "intro": "Ranked by rank score, then price.", "items": cards},
              {"type": "table", "id": "all", "title": "Everything compared", "cards": "ranked", "columns": [
                  {"key": "rank", "label": "Rank", "type": "num"}, {"key": "name", "label": "Name", "type": "text", "link": True},
                  {"key": "price", "label": "Price (EUR)", "type": "num"}, {"key": "score", "label": "Rank score", "type": "num"},
                  {"key": "drive", "label": "Drive (min)", "type": "num"}], "items": rows},
              {"type": "method", "id": "method", "title": "How this was checked", "items": ["Each figure was read from the listing or a Maps measurement.", "Reviews were read in full and stored."]}]
    return {"policy": {"source_classes": {"verified_ota": ["stays.platform-a.test"], "review_platform": ["reviews.platform-b.test"], "maps_measure": ["maps.example-travel.test"]}},
            "trip": {"start": "2030-05-17", "end": "2030-05-19"}, "group_traits": [], "fit_criteria": copy.deepcopy(CRITERIA), "blocks": blocks,
            "footer": "Synthetic stays for calibration. Not a real report.", "design": design}


def trip_intake():
    whys = ["we want to unwind by the water", "the teenagers need something to do", "two of us sleep lightly"]
    return {"shape": "stay", "destination": {"home_base": "Home city", "route": []}, "dates": {"start": "2030-05-17", "end": "2030-05-19", "flexible": False},
            "group": {"adults": 2, "children": [{"age": 15}, {"age": 16}], "pets": []},
            "group_profile": {"traits": [], "traits_confirmed": True, "notes": "A quiet weekend, a view, and something to do on site; two light sleepers."},
            "fit_profile": {"confirmed": True, "criteria": [dict(c, why=w) for c, w in zip(CRITERIA, whys)]},
            "context": {"trip_nature": "unwind", "pace": "relaxed", "time_in_place": "mostly at the stay", "splurge": "value first", "stay_style": ["nature retreat"], "affinities": "not_applicable"},
            "budget": {"amount": 700, "currency": "EUR", "period": "total"}, "created_at": "2030-05-01T09:00:00Z"}


def plant(spec, T, rng):
    """Seed defects into COPIES of the spec and the evidence. Returns (spec, evidence, defects)."""
    spec, T = copy.deepcopy(spec), copy.deepcopy(T)
    D = []

    def rec(cell, code, klass, what, easy=False):
        D.append({"list": "doc", "id": cell, "code": code, "klass": klass, "what": what, "easy": easy})
    cards = {c["id"]: c for b in spec["blocks"] if b["type"] == "cards" for c in b["items"]}
    tbl = next(b for b in spec["blocks"] if b["type"] == "table")
    row = lambda pid: next(r for r in tbl["items"] if r["id"] == pid)
    fact = lambda pid, label: next(f for f in cards[pid]["facts"] if f["label"] == label)
    # ---- consistency with the evidence (C)
    cards["p03"]["price"] += 60; row("p03")["price"] = cards["p03"]["price"]
    rec("p03", "C3", "price", "the card and table show EUR 60 more than the listing page's price")
    cards["p05"]["dossier"][1]["items"][0]["count"] = 31
    rec("p05", "C3", "count", "the Platform A review count is 31 here; the review file, the basis and the listing say 20")
    fact("p06", "Drive from home")["value"] = "75 min"; row("p06")["drive"] = 75
    rec("p06", "C3", "number", "the drive is shown as 75 min; the Maps measurement says 57")
    row("p07")["drive"] = 35
    rec("p07", "C3", "number", "the table's drive time for this stay differs from its card", easy=True)
    cards["p07"]["dossier"][1]["items"][1]["url"] = CODE_A + "p07"
    rec("p07-label", "C2", "source_label", "the Platform B rating links to Platform A's page")
    cards["p04"]["verdict"] = f"Juniper Ridge Retreat: guests rate it {cards['p04']['score_basis']['guest_score']}; price checked this week."
    T["listings"]["p04"]["captured_at"] = "2029-12-02"
    rec("p04", "C4", "stale", "the card says the price was checked this week; the listing evidence was captured on 2 Dec 2029")
    # ---- reasoning (L)
    fact("p01", "View")["value"] = "Garden view with a pond in the distance"
    rec("p01", "L3", "overlay", "the lake-view overlay rests on a garden view that is not a lake view")
    cards["p04"]["verdict"] += " Best for this group because it ranks first."
    rec("p04-circ", "L4", "verdict", "the verdict is circular: it is best because it ranks first")
    cards["p03"]["verdict"] = "Peaceful: no complaints found online, so it is quiet at night."
    rec("p03-quiet", "L1", "text", "absence of complaints presented as proof it is quiet")
    cards["p06"]["verdict"] = "Shortest drive of all twelve stays."
    rec("p06-short", "L9", "text", "57 min is not the shortest: p05 is 38 min and p11 is 41")
    cards["p05"]["pros"].append("Pet policy verified by phone.")
    rec("p05-verified", "L5", "text", "claims a phone-verified pet policy; nothing in the evidence shows a call")
    c2 = cards["p02"]
    c2["watch"] = []; c2["score_basis"]["concerns"] = []
    c2["score_basis"]["group_fit"] = [o for o in c2["score_basis"]["group_fit"] if "concern" not in o]
    c2["facts"] = [f for f in c2["facts"] if f["label"] != "Setting"]
    c2["score"] = round(c2["score_basis"]["guest_score"] + sum(o["bump"] for o in c2["score_basis"]["group_fit"]), 1)
    row("p02")["score"] = c2["score"]
    rec("p02", "L7", "buried", "8 of 40 reviews raise road noise; the card drops the concern, its overlay, its watch-out and the setting fact")
    cards["p08"]["score_basis"]["group_fit"] = [{"signal": "games", "criterion": "on_site_games", "bump": 0.5, "fact": {"facts": 2}, "reason": "something to do on site"}]
    cards["p08"]["score"] = round(cards["p08"]["score_basis"]["guest_score"] + 0.5, 1); row("p08")["score"] = cards["p08"]["score"]
    rec("p08", "L3", "overlay", "a +0.5 overlay on a criterion the group weighted 0.2, resting on 'None listed'", easy=True)
    return spec, T, D


DECOYS = ("p09", "p10", "p11", "p12")


def evidence_files(T):
    return {"listings.json": T["listings"], "maps.json": T["maps"]}
