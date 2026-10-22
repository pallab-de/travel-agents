#!/usr/bin/env python
"""Turn a stay search's ranked rows into a report spec for the generic engine, so a stay report is built, gated and
reviewed exactly like every other report (fact-guard, then the review panel).

  stay_spec.py reviews PROJECT      write every row's review files (reviews/<id>.<source>.json) and list their review ids,
                                    so the synthesis agents can cite them; run before the agents
  stay_spec.py scorer-view PROJECT  write scorer_view.json for the fit-scorer agent: the cards without ranks, scores or overlays
  stay_spec.py themes PROJECT       collect the agents' synthesis/<id>.json into themes.json, refusing a theme or concern that
                                    cites a review id that was not read
  stay_spec.py build PROJECT [--report stay_report.json] [--out spec.json] [--store captures] [--merge-into spec.json]
  stay_spec.py selftest

--merge-into adds the stay blocks (cards, explorer table, chart) to an existing spec, such as an itinerary started with
pipeline.py init --shape itinerary, instead of writing a new one: a mixed trip is one spec. Blocks with the same id are replaced.

Reads from PROJECT (the trip's working folder):
  report_data.json     {"rows": [...]} from listing-search/place-matching/review-synthesis (the stay row schema)
  beyond_data.json     optional: {"rows": [...]} direct-booking leads (report-builder's direct-booking pipeline)
  stay_report.json     what the author writes per trip (see STAY_REPORT_KEYS): title, eyebrow, lead, hero image,
                       notice and method rows, price note, currency symbol, how many cards, and the design (or the
                       design file visual-identity wrote)
  apify_reviews.json / gr10.json    optional: Google reviews read (licensed provider / Places API)
  themes.json          optional: {row id: {"themes": [{text, review_ids}], "concerns": [...]}}, review-synthesis's
                       reading. Contextual overlays are added afterwards by fit-scoring (apply_overlays.py). A card gets a rank score only when its reviews were read and
                       themed; until then it shows no score and the method says the order is the search's.
  detail_raw.json, google_details.json   optional raw evidence used to seed captures (below)

Writes spec.json, and reviews/<row id>.<source>.json review files with provenance (method, retrieved_at, each
review's text and hash) that fact-guard's score gate reads.

--store seeds fact-guard's capture store, but only with figures found verbatim in raw evidence the search saved
(a listing page's text, a Places API response), each with its excerpt and the evidence file's time. Nothing is
captured that isn't in saved evidence: every other figure the gate then names as uncaptured, for capture in the
normal way (fact-guard's pipeline.py capture).
"""
import argparse, datetime as dt, hashlib, json, re, sys, tempfile
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILLS / "fact-guard" / "scripts"))
import guard  # noqa: E402

# stay_report.json may also carry: extra_facts {row id: [{label, value, source}]}, notice_title, cards_title, cards_intro, table_title, table_intro,
# beyond_top, beyond_title, beyond_intro, footer, as_of, policy, design_file, hero_image_credit
STAY_REPORT_KEYS = ["title", "eyebrow", "lead", "hero_image", "hero_image_alt", "notice", "method", "price_note",
                    "currency_symbol", "top"]
REVIEW_LINE = re.compile(r"^\[(\d(?:\.\d)?)★\s*([^\]]*)\]\s*(.*)$", re.S)


def load(p, default=None):
    p = Path(p)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def mtime_iso(p):
    return dt.datetime.fromtimestamp(Path(p).stat().st_mtime, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_unavailable(avail):
    """Only a listing that says it is unavailable (or not yet bookable) is marked so; 'not checked' and 'price not shown' are not."""
    a = str(avail or "").strip().lower()
    return a.startswith("unavailable") or a in ("sold out", "not yet available")


def money(sym, v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return f"{sym}{v:,.0f}" if float(v).is_integer() else f"{sym}{v:,.2f}"   # a price with pennies keeps them: the captures have them
    return str(v)


def split_pairs(text):
    """'Bedroom 1 | 1 king bed | Bedroom 2 | 2 beds' -> [['Bedroom 1', '1 king bed'], ...]; odd tails kept whole."""
    parts = [p.strip() for p in str(text or "").split("|") if p.strip()]
    return [[parts[i], parts[i + 1]] if i + 1 < len(parts) else ["Note", parts[i]] for i in range(0, len(parts), 2)]


def review_items(lines):
    out = []
    for s in lines or []:
        m = REVIEW_LINE.match(str(s))
        out.append({"rating": float(m.group(1)), "when": m.group(2).strip(), "text": m.group(3).strip()} if m
                   else {"text": str(s)})
    return out


def review_file(project, rid, source, method, url, retrieved_at, reviews):
    """Write reviews/<id>.<source>.json; returns the score_basis source entry, or None if no review had text."""
    kept = []
    for r in reviews:
        text = str(r.get("text") or "").strip()
        if not text:
            continue   # a star with no words wasn't read; it isn't counted as a reading
        raw_stars = r.get("stars") if r.get("stars") not in (None, "") else r.get("rating")
        try:
            stars = int(round(float(raw_stars)))
        except (TypeError, ValueError):
            stars = None
        kept.append({"id": hashlib.sha1(f"{source}|{r.get('when')}|{text}".encode("utf-8")).hexdigest()[:12],
                     "stars": stars if stars is not None and 1 <= stars <= 5 else None, "text": text,
                     "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "date": r.get("when")})
    if not kept:
        return None
    rel = f"reviews/{rid}.{source}.json"
    (Path(project) / "reviews").mkdir(exist_ok=True)
    (Path(project) / rel).write_text(json.dumps({"method": method, "retrieved_at": retrieved_at, "source_url": url,
                                                 "reviews": kept}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"name": f"{source.capitalize()} reviews", "url": url, "read": len(kept), "reviews_file": rel}


def gather_sources(project, row):
    """Write this row's review files and list every review source for it: the Google reviews (licensed provider or Places
    API) and listing reviews from the row's inputs, and any provider file already dropped in reviews/ as
    <row id>.<source>.json (serpapi_reviews.py, raw_reviews_to_evidence.py: a verified-stay site's file is the second source)."""
    sources, rid = [], str(row.get("id"))
    apify, gr10 = load(Path(project) / "apify_reviews.json", {}), load(Path(project) / "gr10.json", {})
    if apify.get(rid):
        sources.append(review_file(project, rid, "google", "apify-google-maps-reviews-scraper", row.get("gurl"),
                                   mtime_iso(Path(project) / "apify_reviews.json"), apify[rid]))
    elif gr10.get(rid):
        sources.append(review_file(project, rid, "google", "google-places-api", row.get("gurl"),
                                   mtime_iso(Path(project) / "gr10.json"), gr10[rid].get("reviews", [])))
    if row.get("recent"):
        # Listing reviews read in the browser: usable for themes and concerns, but not a method the score may use.
        listing = review_file(project, rid, "listing", "browser-read", row.get("url"),
                              mtime_iso(Path(project) / "report_data.json"),
                              [{"stars": i.get("rating"), "when": i.get("when"), "text": i.get("text")}
                               for i in review_items(row["recent"])])
        if listing:
            listing["for_score"] = False   # themes and quotes only: a browser read is not a method the score may use
        sources.append(listing)
        sources.append({"name": "Listing reviews (for the score)",
                        "unavailable": "read in a browser, not through a licensed review provider"})
    have = {x.get("reviews_file") for x in sources if x}
    for f in sorted((Path(project) / "reviews").glob(f"{rid}.*.json")):
        rel = f"reviews/{f.name}"
        doc = load(f, {})
        if rel in have or not isinstance(doc, dict) or not doc.get("reviews"):
            continue
        source = f.name[len(rid) + 1:-len(".json")]
        sources.append({"name": doc.get("source_name") or f"{source.capitalize()} reviews", "url": doc.get("source_url") or "",
                        "read": len(doc["reviews"]), "reviews_file": rel})
    return [s for s in sources if s]


def score_basis(project, row, themes, pol):
    """A rank-score-v2 basis from the review files, or None when the reading (themes) isn't done yet."""
    t = (themes or {}).get(str(row.get("id")))
    if not t or not t.get("themes"):
        return None
    sources = gather_sources(project, row)
    basis = {"method": "read-reviews", "formula": "rank-score-v2",
             "rubric": "Weighted mean of the stars on the reviews read: verified-stay sites 1.5, review platforms 1.0.",
             "sources": sources + (t.get("web") or []), "themes": t["themes"], "concerns": t.get("concerns", []),
             "group_fit": []}   # contextual overlays come from the fit scorer (fit-scoring/apply_overlays.py)
    owner = guard.own_hosts([row.get("url")], [], pol["source_classes"])
    problems = []   # the gate reports these (a source that can't count); here they only mean there is no guest score yet
    guest, _counted, _ids = guard.guest_score_v2(sources, project, pol, dt.datetime.now(dt.timezone.utc), owner, row.get("name"), problems)
    if guest is None:
        return None
    basis["guest_score"] = guest
    basis["_score"] = guest   # until the fit scorer's overlays are applied, the rank score is the guest score
    return basis


def google_rating(row):
    """(rating, count) when Google really rated this place: a matched place (search rows carry grat/gn; a direct lead
    carries rating/n with rating_src 'Google...' and gplace). A booking site's own rating is not Google's."""
    if row.get("grat") is not None:
        return row["grat"], row.get("gn")
    if row.get("rating_src") == "Google" and row.get("gplace") and row.get("rating") is not None:
        return row["rating"], row.get("n")
    if row.get("rating_src") is None and row.get("rating") is not None and row.get("gplace"):
        return row["rating"], row.get("n")
    return None, None


def merge_into(stay, target):
    """The stay spec's cards, table and chart added to the spec in `target` (before its method block); same-id blocks replaced."""
    base = json.loads(Path(target).read_text(encoding="utf-8"))
    new = [b for b in stay["blocks"] if b["type"] in ("cards", "table", "chart")]
    ids = {b.get("id") for b in new}
    blocks = [b for b in base["blocks"] if b.get("id") not in ids or b["type"] not in ("cards", "table", "chart")]
    at = next((i for i, b in enumerate(blocks) if b["type"] == "method"), len(blocks))
    base["blocks"] = blocks[:at] + new + blocks[at:]
    pol = base.setdefault("policy", {})
    for key, val in (stay.get("policy") or {}).items():   # every stay policy key carries over, not only label_kinds
        if isinstance(val, list):
            first = [list(x) if isinstance(x, (list, tuple)) else x for x in val]   # the stay's rules first
            pol[key] = first + [x for x in pol.get(key, []) if x not in first]
        elif isinstance(val, dict):
            merged = dict(pol.get(key) or {})
            for k2, v2 in val.items():
                merged[k2] = list(dict.fromkeys(list(merged.get(k2, [])) + list(v2))) if isinstance(v2, list) else v2
            pol[key] = merged
        else:
            pol.setdefault(key, val)
    for key in ("fit_criteria", "group_traits", "trip"):
        if key in stay and key not in base:
            base[key] = stay[key]
    method = next((b for b in base["blocks"] if b["type"] == "method"), None)
    stay_method = next((b for b in stay["blocks"] if b["type"] == "method"), None)
    if method and stay_method:
        method["items"] = list(method.get("items", [])) + [x for x in stay_method.get("items", []) if x not in method.get("items", [])]
    return base


def rows_of(project):
    """Every row that can be read and scored: the platform search's and the direct-booking leads'."""
    out = []
    for name in ("report_data.json", "beyond_data.json"):
        data = load(Path(project) / name, None)
        if data is not None:
            out += (data["rows"] if isinstance(data, dict) else data) or []
    return out


def cmd_reviews(project):
    if not rows_of(project):
        print(f"FAIL no rows: write report_data.json first (stay_rows.py {project} --config config.json)")
        return 1
    n = 0
    for row in rows_of(project):
        srcs = gather_sources(project, row)
        for sc in srcs:
            if sc.get("reviews_file"):
                ids = [r["id"] for r in load(Path(project) / sc["reviews_file"], {}).get("reviews", [])]
                print(f"{row.get('id')}: {sc['reviews_file']}: {len(ids)} review(s), ids {ids[:3]}...")
                n += 1
        if not any(sc.get("reviews_file") for sc in srcs):
            print(f"{row.get('id')}: no review file yet (fetch reviews first: review-synthesis step 1)")
    print(f"{n} review file(s) written or found; give each synthesis agent the rows' files and tell it to cite these ids")
    return 0


def cmd_scorer_view(project):
    """scorer_view.json for the fit-scorer agent: the spec's cards in alphabetical order, with ranks, scores and overlays removed, so
    the scorer cannot be steered by the order it is trying to change."""
    spec = load(Path(project) / "spec.json", None)
    if not spec:
        print(f"FAIL no spec.json in {project}: run stay_spec.py build first")
        return 1
    view = {"fit_criteria": spec.get("fit_criteria"), "group_traits": spec.get("group_traits"), "cards": []}
    for b in spec["blocks"]:
        if b["type"] != "cards":
            continue
        for it in sorted(b["items"], key=lambda x: str(x.get("name"))):
            c = {k: v for k, v in it.items() if k not in ("rank", "score")}
            if c.get("score_basis"):
                c["score_basis"] = {k: v for k, v in c["score_basis"].items() if k not in ("guest_score", "group_fit")}
            view["cards"].append(dict(c, block=b.get("id")))
    (Path(project) / "scorer_view.json").write_text(json.dumps(view, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"scorer_view.json: {len(view['cards'])} cards, no ranks or scores")
    return 0


def cmd_themes(project):
    """synthesis/<id>.json: {"themes": [{text, sentiment, review_ids}], "concerns": [{text, review_ids}],
    "web": [{name, url, read_at}], "evidence_quality": text}. Every cited id must be in one of the row's review files."""
    out, bad = {}, []
    known = {str(r.get("id")) for r in rows_of(project)}
    for f in sorted((Path(project) / "synthesis").glob("*.json")):
        rid, doc = f.stem, load(f, None)
        if not isinstance(doc, dict):
            bad.append(f"{f.name}: not a JSON object")
            continue
        ids = {str(r.get("id")) for g in Path(project, "reviews").glob(f"{rid}.*.json") for r in load(g, {}).get("reviews", [])}
        if not ids:
            bad.append(f"{rid}: no review file in reviews/ (run stay_spec.py reviews)")
            continue
        if known and rid not in known:
            bad.append(f"{rid}: no such row in report_data.json or beyond_data.json")
            continue
        for kind in ("themes", "concerns", "isolated"):
            for i, t in enumerate(doc.get(kind) or []):
                cited = [str(x) for x in (t.get("review_ids") or [])]
                if not str(t.get("text", "")).strip() or not cited:
                    bad.append(f"{rid}: {kind}[{i}] needs text and review_ids")
                elif [c for c in cited if c not in ids]:
                    bad.append(f"{rid}: {kind}[{i}] cites review ids that were not read: {[c for c in cited if c not in ids][:3]}")
        if not doc.get("themes"):
            bad.append(f"{rid}: no themes")
        web = [w for w in doc.get("web") or [] if str(w.get("url", "")).startswith("https://") and w.get("read_at")]
        if not web:
            bad.append(f"{rid}: no web research source with an https url and a read_at date")
        out[rid] = {"themes": doc.get("themes") or [], "concerns": doc.get("concerns") or [], "isolated": doc.get("isolated") or [],
                    "pros": [x for x in doc.get("pros") or [] if isinstance(x, str)], "verdict": doc.get("verdict") if isinstance(doc.get("verdict"), str) else "",
                    "web": [{"name": w.get("name") or w["url"], "url": w["url"], "read_at": w["read_at"]} for w in web]}
    for b in bad:
        print("FAIL", b)
    if bad or not out:
        print("themes.json was not written" + ("" if bad else ": no synthesis/<id>.json found"))
        return 1
    (Path(project) / "themes.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"themes.json written for {len(out)} row(s)")
    return 0


def card(row, rep, project, themes, pol, dossier_photos=True):
    sym = rep["currency_symbol"]
    imgs = [u for u in row.get("images") or [] if str(u).startswith("https://")]
    grat, gn = google_rating(row)
    ratings = [e for e in (
        {"label": "Listing", "rating": row.get("arat"), "count": row.get("an"), "url": row.get("url"), **({"scale": row["ascale"]} if row.get("ascale") else {})},
        {"label": "Google", "rating": grat, "count": gn, "url": row.get("gurl")})
        if e["rating"] is not None and e["url"]]
    no_rating = ("the booking site's own rating is self-reported and isn't used" if row.get("rating_src") == "Site rating"
                 else "no platform rating found")
    for f in row.get("flags") or []:
        if len(str(f).split()) > 14:
            print(f"WARN {row['name']}: flag not shown, over 14 words: {str(f)[:70]!r}; shorten it or move it into the row's watch list", file=sys.stderr)
    c = {"name": row["name"], "id": str(row.get("id") or row["name"]), "url": row.get("url"), "area": row.get("area", ""),
         "rank": int(row.get("rank") or 0),
         **({"price": money(sym, row["cost"] if row.get("cost") is not None else row["total"]), "priceNote": rep["price_note"]}
            if row.get("cost") is not None or row.get("total") is not None else {}),   # no price found: the card shows none rather than "None"
         "images": imgs[:4],
         "badges": [b for b in ({"text": row["vchip"], "tone": "good"} if row.get("vchip") else None,
                               {"text": row["pool"]} if row.get("pool") else None) if b],
         "facts": [f for f in (
             {"label": "Drive from home", "value": f"{row['drive']} min", "source": row.get("dir")} if row.get("drive") is not None else None,
             {"label": "Drive back", "value": f"{row['drive_back']} min", "source": row.get("dir")} if row.get("drive_back") is not None else None,
             {"label": "Cancellation", "value": row["refund"]} if row.get("refund") else None,
             {"label": "Sleeping for this group", "value": row["beds"]} if row.get("beds") else None) if f],
         "pros": list(row.get("pros") or (themes or {}).get(str(row.get("id")), {}).get("pros") or []),
         "watch": list(row.get("watch") or []) + [v for v in row.get("vcav") or [] if v not in (row.get("watch") or [])]
                  + [f for f in (row.get("flags") or []) if len(str(f).split()) <= 14]   # a flag becomes a watch-out, which the build limits to 14 words (longer ones are named below)
         + [{"text": i["text"], "review_ids": i.get("review_ids", []), "isolated": True}
            for i in (themes or {}).get(str(row.get("id")), {}).get("isolated", [])],   # a complaint raised once: shown, labelled as one report
         "verdict": row.get("verdict") or (themes or {}).get(str(row.get("id")), {}).get("verdict", ""),
         "dossier": [
             {"title": "Map listing", "kind": "facts", "source": row.get("gurl"),
              "rows": [r for r in (["Name on the map", row.get("gname")], ["Area", row.get("area")]) if r[1]]}
             if row.get("gurl") else {"title": "Map listing", "not_found": "no map place matched this listing"},
             {"title": "Guest ratings", "kind": "ratings", "items": ratings} if ratings else
             {"title": "Guest ratings", "not_found": no_rating},
             {"title": "Guest reviews", "kind": "reviews", "caveat": row.get("summary", ""),
              "items": review_items(list(row.get("recent") or []) + list(row.get("gsnips") or []))[:12]}
             if (row.get("recent") or row.get("gsnips")) else {"title": "Guest reviews", "not_found": "no reviews read yet"},
             {"title": "Photos", "kind": "photos", "images": imgs} if imgs else
             {"title": "Photos", "not_found": "no photo with a recorded source URL"},
         ]}
    c["facts"] = c["facts"] + list((rep.get("extra_facts") or {}).get(str(row.get("id")), []))   # facts the fit scorer needed, kept across rebuilds
    for title, text, label in (("Sleeping arrangements", row.get("sleep"), None), ("Amenities listed", row.get("amen"), "Amenity"), ("House rules", row.get("rules"), "House rule")):
        if isinstance(text, (list, tuple)):
            text = " | ".join(str(x) for x in text)
        if not text:
            continue
        if label is None:   # label|value pairs: "Bedroom 1 | 1 king bed" -> ["Beds, Bedroom 1", "1 king bed"], a claim the listing page must show
            rows = [[f"Beds, {a}", b] for a, b in split_pairs(text)]
        else:               # a flat list: one item per row, never paired with its neighbour
            rows = [[label, x.strip()] for x in str(text).split("|") if x.strip()]
        c["dossier"].append({"title": title, "kind": "facts", "rows": rows, "source": row.get("url")})
    if row.get("pet_fee"):   # terms in the listing's words ("₹1,000 per pet"), shown in the details, not as a card figure
        c["dossier"].append({"title": "Pets", "kind": "facts", "rows": [["Pet terms", row["pet_fee"]]], "source": row.get("url")})
    if row.get("ask"):
        c["dossier"].append({"title": "Ask before booking", "kind": "facts", "rows": [["Ask the host", q if str(q).strip().endswith("?") else str(q).strip() + "?"] for q in row["ask"]]})
    if is_unavailable(row.get("avail")):
        c["unavailable"] = True
    basis = score_basis(project, row, themes, pol)
    if basis:
        c["score"] = basis.pop("_score")
        c["score_basis"] = basis
    # A quoted review is shown only if it is one of the reviews stored for the score, with its own stars.
    stored = guard.stored_reviews(c, project)
    for sec in c["dossier"]:
        if sec.get("kind") == "reviews":
            kept = [dict(q, **({"rating": stored[q["text"].strip().lower()]} if stored.get(q["text"].strip().lower()) is not None else {}))
                    for q in sec["items"] if q.get("text", "").strip().lower() in stored]
            if kept:
                sec["items"] = kept
            else:
                sec.clear(); sec.update({"title": "Guest reviews", "not_found": "no review text is stored for this place yet"})
    return c


def table_row(row, rep):
    return {"name": row["name"], "id": str(row.get("id") or row["name"]), "url": row.get("url"), "area": row.get("area", ""), "drive": row.get("drive"),
            "cost": row.get("cost") if row.get("cost") is not None else row.get("total"),
            "refund": row.get("refund", ""), "pool": row.get("pool", ""), "beds": row.get("beds", ""),
            "listing_rating": row.get("arat"), "google_rating": google_rating(row)[0],
            "avail": row.get("avail", ""), "rank": int(row.get("rank") or 0),
            "sources": {k: v for k, v in (("drive", row.get("dir")), ("google_rating", row.get("gurl")),
                                          ("cost", row.get("url")), ("listing_rating", row.get("url"))) if v},
            **({"unavailable": True} if is_unavailable(row.get("avail")) else {})}


COLUMNS = [
    {"key": "rank", "label": "Rank", "type": "num"},
    {"key": "name", "label": "Name", "type": "text", "link": True},
    {"key": "area", "label": "Area", "type": "cat", "filter": True},
    {"key": "drive", "label": "Drive (min)", "type": "num", "filter": True},
    {"key": "cost", "label": "Price", "type": "num", "filter": True},
    {"key": "pool", "label": "Pool", "type": "cat", "filter": True},
    {"key": "refund", "label": "Cancellation", "type": "cat", "filter": True, "hidden": True},
    {"key": "beds", "label": "Sleeping fit", "type": "cat", "filter": True, "hidden": True},
    {"key": "listing_rating", "label": "Listing rating", "type": "num"},
    {"key": "google_rating", "label": "Google rating", "type": "num", "hidden": True},
    {"key": "avail", "label": "Available", "type": "cat", "filter": True},
]


def build(project, rep_path=None, base_spec=None):
    """base_spec: the spec these cards will be merged into; its policy (allowed review methods, banned words) is the one they are scored under."""
    project = Path(project)
    rep = load(rep_path or project / "stay_report.json")
    if rep is None:
        raise SystemExit(f"no stay_report.json in {project}: write it first (keys: {STAY_REPORT_KEYS} and design or design_file)")
    missing = [k for k in STAY_REPORT_KEYS if k not in rep]
    design = rep.get("design") or (load(project / rep["design_file"]) if rep.get("design_file") else None)
    if missing or not design:
        raise SystemExit(f"stay_report.json is missing {missing + ([] if design else ['design or design_file'])}")
    data = load(project / "report_data.json")
    rows = sorted(data["rows"] if isinstance(data, dict) else data, key=lambda r: int(r.get("rank") or 10 ** 6))
    beyond = (load(project / "beyond_data.json", {}) or {}).get("rows", [])
    themes = load(project / "themes.json", {})
    pol = guard.policy(base_spec or {})
    cards = [card(r, rep, project, themes, pol) for r in rows[:int(rep["top"])]]
    scored = [c for c in cards if c.get("score") is not None]
    if scored and len(scored) == len(cards):   # one rank score for all: order by it, the search's order breaks ties
        cards.sort(key=lambda c: (-c["score"], c["rank"]))
        for i, c in enumerate(cards, 1):
            c["rank"] = i
    rank_of = {c["id"]: c["rank"] for c in cards}
    trs = [table_row(r, rep) for r in rows]
    rest = sorted((t for t in trs if str(t["id"]) not in rank_of), key=lambda t: t["rank"])   # places outside the cards keep the search's order, after them
    for t in trs:
        if str(t["id"]) in rank_of:
            t["rank"] = rank_of[str(t["id"])]
    for n, t in enumerate(rest, len(rank_of) + 1):
        t["rank"] = n
    open_rows = [t for t in trs if not t.get("unavailable")]   # a headline never advertises a place that can't be booked
    drives = [t["drive"] for t in open_rows if t.get("drive") is not None]
    costs = [t["cost"] for t in open_rows if t.get("cost") is not None]
    kpis = [{"value": str(len(rows)), "label": "places compared", "derived": {"count": "all"}}]   # the gate recomputes each from the table
    if drives:
        kpis.append({"value": f"{min(drives)} min", "label": "shortest drive (available)", "derived": {"min": "all.drive"}})
    if costs:
        kpis.append({"value": money(rep["currency_symbol"], min(costs)), "label": "lowest cost (available)", "derived": {"min": "all.cost"}})
    method = list(rep["method"])
    if cards and len(scored) < len(cards):
        method.append(f"{len(cards) - len(scored)} of {len(cards)} ranked places have no rank score yet (their reviews "
                      f"haven't been read and themed), so the order shown is the search's own.")
    blocks = [{"type": "hero", "eyebrow": rep["eyebrow"], "title": rep["title"], "lead": rep["lead"], "kpis": kpis,
               "image": rep["hero_image"], "image_alt": rep["hero_image_alt"],
               **({"image_credit": rep["hero_image_credit"]} if rep.get("hero_image_credit") else {})},
              {"type": "notice", "title": rep.get("notice_title", "Read this first"), "items": rep["notice"]},
              {"type": "cards", "id": "ranked", "kicker": "Ranked", "title": rep.get("cards_title", "The shortlist"),
               "intro": rep.get("cards_intro", ""), "items": cards},
              *([{"type": "chart", "id": "cost-drive", "kicker": "At a glance", "title": "Cost against drive time",
                  "source": "all", "x": {"key": "drive", "label": "Drive (min)"}, "y": {"key": "cost", "label": f"Cost ({rep['currency_symbol']})"}}]
                if drives and costs else []),   # a hotel search has no live price to plot
              {"type": "table", "id": "all", "kicker": "Every place", "title": rep.get("table_title", "Everything compared"),
               "intro": rep.get("table_intro", ""), "group_by": ["area", "pool", "refund"], "compare": True, "cards": "ranked",
               "columns": COLUMNS, "items": trs}]
    if beyond:
        bcards = [card(r, rep, project, themes, pol) for r in beyond[:int(rep.get("beyond_top", 6))]]
        for i, c in enumerate(bcards, 1):
            c["rank"] = c["rank"] or i
        blocks += [{"type": "cards", "id": "direct", "kicker": "Book direct", "title": rep.get("beyond_title", "Beyond the booking platforms"),
                    "intro": rep.get("beyond_intro", ""), "items": bcards},
                   {"type": "table", "id": "direct-all", "kicker": "Book direct", "title": "Every direct lead",
                    "group_by": ["area"], "compare": True, "cards": "direct",
                    "columns": [c for c in COLUMNS if c["key"] not in ("refund", "listing_rating")],
                    "items": [dict(table_row(r, rep), rank=i) for i, r in enumerate(beyond, 1)]}]
    blocks.append({"type": "method", "id": "method", "title": "How this was made", "items": method})
    spec = {"design": design, "blocks": blocks, "policy": {}}   # the stay labels (Beds, Amenity, House rule, Available) are the gate's own defaults
    for k in ("footer", "as_of"):
        if rep.get(k):
            spec[k] = rep[k]
    spec["policy"].update(rep.get("policy") or {})   # the trip's own policy keys add to these, they don't drop them
    intake = load(project / "trip_intake.json")
    if intake:
        sys.path.insert(0, str(SKILLS / "trip-intake" / "scripts"))
        import trip_intake
        spec = trip_intake.sync(intake, spec)   # traits, fit criteria and the trip dates (a flexible window's outer limits), exactly as the gate reads them
    return spec


CONTEXTS = {
    "price": [re.compile(r"(?P<v>[\u20ac$\u00a3\u20b9]\s?\d[\d,]*(?:\.\d+)?)\s+for\s+\d+\s+nights?", re.I)],
    "score": [re.compile(r"(?P<v>\d\.\d{1,2})\s*[\u00b7\u2022]\s*\d[\d,]*\s+reviews?", re.I),
              re.compile(r"Rated\s+(?P<v>\d\.\d{1,2})\s+out of\s+5", re.I)],
    "count": [re.compile(r"\d\.\d{1,2}\s*[\u00b7\u2022]\s*(?P<v>\d[\d,]*)\s+reviews?", re.I),
              re.compile(r"Rated\s+\d\.\d{1,2}\s+out of\s+5\s+from\s+(?P<v>\d[\d,]*)\s+reviews?", re.I)],
}


def excerpt(text, i, j):
    return text[max(0, i - 160): j + 160]


def labelled(kind, text):
    """Every figure of this kind that the page states in its labelled form ('<amount> for N nights', '4.9 · 23
    reviews', 'Rated 4.9 out of 5'), with where it was found. A bare number elsewhere on the page proves nothing."""
    out = []
    for rx in CONTEXTS.get(kind, []):
        for m in rx.finditer(text or ""):
            out.append((m.group("v"), m.start("v"), m.end("v")))
    return out


def seed(spec, project, store):
    """Capture only what saved raw evidence shows verbatim, in a labelled context, and unambiguously: when the page
    states two different figures of that kind, nothing is seeded. Returns (seeded rows, rows left for the gate)."""
    import capture_store as cs
    project = Path(project)
    raw = load(project / "detail_raw.json", {}) or {}
    gdet = load(project / "google_details.json", {}) or {}
    raw_at = mtime_iso(project / "detail_raw.json") if raw else None
    g_at = mtime_iso(project / "google_details.json") if gdet else None
    by_name = {c["name"]: c for b in spec["blocks"] if b["type"] == "cards" for c in b["items"]}
    seeded, left = [], []
    for r in guard.ledger(spec):
        c = by_name.get(r["item"])
        hit = None
        if c and r["kind"] in ("price", "score", "count"):
            want = guard.claim_key(r["kind"], r["value"])
            text = raw.get(str(c.get("id")), "") if r["src"] == c.get("url") else ""
            found = labelled(r["kind"], text)
            distinct = {guard.claim_key(r["kind"], v) for v, _, _ in found}
            if found and len(distinct) == 1 and guard.figures_agree(r["kind"], next(iter(distinct)), want):
                v, i, j = found[0]
                hit = (v, r["src"], raw_at, excerpt(text, i, j))
            if hit is None and gdet:
                for place in gdet.values():
                    if place.get("url") and place["url"] == r["src"]:
                        v = place.get("rating") if r["kind"] == "score" else place.get("n") if r["kind"] == "count" else None
                        if v is not None and guard.figures_agree(r["kind"], guard.claim_key(r["kind"], v), want):
                            hit = (str(v), r["src"], g_at, json.dumps({k: place.get(k) for k in ("name", "rating", "n", "url")}, ensure_ascii=False))
        if hit is None and c and r["kind"] in ("feature", "policy") and r["src"] == c.get("url") and raw_at:
            text = raw.get(str(c.get("id")), "")
            val = str(r["value"]).strip()
            m = re.search(r"\s+".join(re.escape(w) for w in val.split()), text, re.I) if val else None   # the listing page shows this text, whatever its spacing
            if m:
                hit = (val, r["src"], raw_at, excerpt(text, m.start(), m.end()))
        if hit:
            cs.put(store, r["row"], hit[1], hit[0], raw=hit[3], when=hit[2])
            seeded.append(r["row"])
        else:
            left.append(r["row"])
    return seeded, left


def selftest():
    cases = []
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        rows = [{"id": "11", "name": "Farm A", "rank": 2, "url": "https://www.airbnb.example/rooms/11", "area": "East",
                 "gurl": "https://maps.google.com/?cid=1", "dir": "https://www.google.com/maps/dir/?api=1&x", "cost": 30000,
                 "drive": 60, "drive_back": 65, "refund": "Non-refundable", "pet_fee": "None declared", "beds": "Enough beds",
                 "pool": "Private pool", "arat": 4.9, "an": 23, "grat": 4.6, "gn": 120, "avail": "Available",
                 "pros": ["Quiet"], "watch": ["Steep stairs"], "vcav": ["Pool depth not stated"], "verdict": "Fits.",
                 "recent": ["[5★ June 2030] Lovely pool and a helpful caretaker."], "sleep": "Bedroom 1 | 1 king bed", "ask": ["Pool depth?"]},
                {"id": "12", "name": "Farm B", "rank": 1, "url": "https://www.airbnb.example/rooms/12", "area": "West",
                 "cost": 25000, "drive": 45, "avail": "Unavailable"}]
        (p / "report_data.json").write_text(json.dumps({"rows": rows}), encoding="utf-8")
        rep = {"title": "Two farms", "eyebrow": "Test", "lead": "A test.", "hero_image": "hero.jpg", "hero_image_alt": "x",
               "notice": ["Synthetic data."], "method": ["Synthetic."], "price_note": "for 2 nights", "currency_symbol": "₹", "top": 2,
               "design": {"brand": "T"}}
        (p / "stay_report.json").write_text(json.dumps(rep), encoding="utf-8")
        texts = [f"Review {i} words" for i in range(25)]
        (p / "apify_reviews.json").write_text(json.dumps({"11": [{"stars": 5 if i % 2 else 4, "when": f"2030-0{1 + i % 9}-01", "text": t} for i, t in enumerate(texts)]}), encoding="utf-8")
        spec = build(p)
        kinds = [b["type"] for b in spec["blocks"]]
        cases.append(("a stay spec has hero, notice, cards, chart, table, method", kinds == ["hero", "notice", "cards", "chart", "table", "method"]))
        a = next(c for c in spec["blocks"][2]["items"] if c["name"] == "Farm A")
        cases.append(("drive facts cite the map directions (a map measurement)", all(f.get("source", "").startswith("https://www.google.com/maps/dir") for f in a["facts"] if "Drive" in f["label"])))
        cases.append(("required dossier sections are all present", [d["title"] for d in a["dossier"][:4]] == guard.REQUIRED_DOSSIER))
        cases.append(("no reading yet means no score, and the method says so", "score" not in a and any("no rank score yet" in m for m in spec["blocks"][-1]["items"])))
        cases.append(("an unavailable place stays in the list, marked", next(c for c in spec["blocks"][2]["items"] if c["name"] == "Farm B").get("unavailable") is True))
        (p / "themes.json").write_text(json.dumps({"11": {"themes": [{"text": "pool", "review_ids": []}]}}), encoding="utf-8")
        spec2 = build(p)
        a2 = next(c for c in spec2["blocks"][2]["items"] if c["name"] == "Farm A")
        g = json.loads((p / "reviews/11.google.json").read_text(encoding="utf-8"))
        cases.append(("read and themed reviews give a rank score from the stored file", a2.get("score") == round(sum(r["stars"] for r in g["reviews"]) / 25, 1)
                      and g["method"] == "apify-google-maps-reviews-scraper"))
        cases.append(("browser-read listing reviews are declared unavailable for the score", any(s.get("unavailable") for s in a2["score_basis"]["sources"])))
        cases.append(("a stay spec passes fact-guard's policy and classifies every row it writes", guard.policy_fails(spec2) == []
                      and not [r for r in guard.ledger(spec2) if r["kind"] == "unclassified"]))
        sg_ = guard.score_gate(spec2, str(p), guard.policy(spec2))
        cases.append(("the listing's own reviews feed themes without being refused as a scored source", not any("browser-read" in f or "policy allows" in f for f in sg_)))
        (p / "spec.json").write_text(json.dumps(spec2), encoding="utf-8")
        import contextlib as _cl, io as _io
        with _cl.redirect_stdout(_io.StringIO()):
            cmd_scorer_view(p)
        view = json.loads((p / "scorer_view.json").read_text(encoding="utf-8"))
        cases.append(("the scorer's view has the cards in name order and no ranks or scores", [c["name"] for c in view["cards"]] == sorted(c["name"] for c in view["cards"])
                      and not any("rank" in c or "score" in c for c in view["cards"])))
        (p / "detail_raw.json").write_text(json.dumps({"11": "Farm A ... ₹30,000 for 2 nights ... 4.9 · 23 reviews"}), encoding="utf-8")
        seeded, left = seed(spec, p, str(p / "captures"))
        cases.append(("a price shown verbatim in the saved page is seeded", any(r.endswith("|price") and ":11|" in r for r in seeded)))
        cases.append(("a figure not in saved evidence is left for the gate", any(":12|" in r and r.endswith("|price") for r in left)))
        (p / "detail_raw.json").write_text(json.dumps({"11": "Where you'll sleep\nBedroom 1\n1 king bed\n"}), encoding="utf-8")
        seeded_f, _ = seed(spec2, p, str(p / "captures_f"))
        cases.append(("a sleeping arrangement shown verbatim in the saved page is seeded as a feature capture", any("Sleeping arrangements" in r for r in seeded_f)))
        (p / "detail_raw.json").write_text(json.dumps({"11": "Farm A ... posted 23 Jan ... 4.7 \u00b7 40 reviews ... Similar: \u20b930,000 for 2 nights, \u20b932,000 for 2 nights"}), encoding="utf-8")
        seeded2, left2 = seed(spec, p, str(p / "captures2"))
        cases.append(("a bare '23 Jan' or two differing prices never seed a count or price",
                      not any(r.endswith("|count") or r.endswith("|price") for r in seeded2 if ":11|" in r)))
        lead = {"id": "21", "name": "Lead C", "rank": 3, "url": "https://www.direct.example/c", "area": "North", "total": 20000, "drive": 70,
                "rating": 4.4, "n": 12, "rating_src": "Site rating", "gurl": "https://www.google.com/maps/search/?api=1&query=x", "gplace": False}
        lc = card(lead, rep, p, {}, guard.policy({}))
        cases.append(("a booking site's own rating is never labelled Google", not any(e.get("label") == "Google" for d in lc["dossier"] if d.get("kind") == "ratings" for e in d["items"])
                      and next(d for d in lc["dossier"] if d["title"] == "Guest ratings").get("not_found")
                      and table_row(lead, rep)["google_rating"] is None))
        mixed = review_file(p, "31", "google", "apify-google-maps-reviews-scraper", "https://www.google.com/maps/place/x", "2030-01-01T00:00:00Z",
                            [{"stars": 5, "text": f"A long five-star review number {i} about the pool and the caretaker"} for i in range(24)]
                            + [{"text": "A long text-only review with no star rating at all, about the garden"}])
        doc31 = json.loads((p / "reviews/31.google.json").read_text(encoding="utf-8"))
        cases.append(("a text-only review never drags the score down (24 fives + 1 unrated = 5.0)", guard.mean_stars(doc31["reviews"]) == 5.0
                      and sum(r["stars"] is None for r in doc31["reviews"]) == 1))
        am = card(dict(lead, amen=["Pool", "Wifi"]), rep, p, {}, guard.policy({}))
        cases.append(("a list of amenities renders as rows, not as a Python list", not any("[" in str(x) or "'" in str(x)
                      for d in am["dossier"] if d["title"] == "Amenities listed" for r in d["rows"] for x in r)
                      and any(d["title"] == "Amenities listed" for d in am["dossier"])))
        del rep["design"]
        (p / "stay_report.json").write_text(json.dumps(rep), encoding="utf-8")
        try:
            build(p); cases.append(("a stay report with no design is refused", False))
        except SystemExit:
            cases.append(("a stay report with no design is refused", True))
    import hotel_rows
    with tempfile.TemporaryDirectory() as td:
        hp = Path(td)
        (hp / "report_data.json").write_text(json.dumps(hotel_rows.convert({"candidates": {"1": {"name": "Hotel A", "hotel_page_url": "https://www.booking.com/hotel/x/a.html", "review_score_10": 8.6, "review_count": 40}}}, area="Valmora")), encoding="utf-8")
        (hp / "stay_report.json").write_text(json.dumps({"title": "Hotels", "eyebrow": "e", "lead": "l", "hero_image": "h.jpg", "hero_image_alt": "x", "notice": ["a", "b", "c"], "method": ["m"],
                                                          "price_note": "", "currency_symbol": "EUR", "top": 1, "design": {"brand": "T"}}), encoding="utf-8")
        hs = build(hp)
        cases.append(("a hotel search builds: cards and a table, no price, no cost chart", [b["type"] for b in hs["blocks"]] == ["hero", "notice", "cards", "table", "method"]
                      and "price" not in hs["blocks"][2]["items"][0]))
    stay = {"blocks": [{"type": "hero"}, {"type": "cards", "id": "ranked", "items": []}, {"type": "table", "id": "all", "items": []}, {"type": "chart", "id": "c"}]}
    with tempfile.TemporaryDirectory() as td:
        tp = Path(td) / "spec.json"
        tp.write_text(json.dumps({"blocks": [{"type": "hero"}, {"type": "day_plan", "id": "plan", "days": []}, {"type": "method", "id": "method"}]}), encoding="utf-8")
        merged = merge_into(stay, tp)
        order = [b["type"] for b in merged["blocks"]]
        cases.append(("a mixed trip is one spec: stay blocks go in before the method, the itinerary stays", order == ["hero", "day_plan", "cards", "table", "chart", "method"]))
        tp.write_text(json.dumps(merged), encoding="utf-8")
        again = [b["type"] for b in merge_into(stay, tp)["blocks"]]
        cases.append(("merging again replaces the stay blocks, it doesn't repeat them", again == order))
        both = merge_into(dict(stay, policy={"banned": ["foo"], "label_kinds": [["fee", "price"]], "source_classes": {"rail": ["rail.example.test"]}}), tp)["policy"]
        cases.append(("every stay policy key carries into the mixed spec, not only label_kinds", both["banned"] == ["foo"] and ["fee", "price"] in both["label_kinds"]
                      and both["source_classes"]["rail"] == ["rail.example.test"]))
    # The reading steps: review files for the agents, a provider file as the second source, then themes that cite real ids.
    import contextlib, io
    with tempfile.TemporaryDirectory() as td:
        pj = Path(td)
        row = {"id": "7", "name": "Farm Z", "url": "https://www.airbnb.example/rooms/7", "gurl": "https://www.google.com/maps/place/?q=place_id:a"}
        (pj / "report_data.json").write_text(json.dumps({"rows": [row]}), encoding="utf-8")
        (pj / "apify_reviews.json").write_text(json.dumps({"7": [{"stars": 5, "when": "2030-01-01", "text": f"Review number {i} is long enough to count"} for i in range(20)]}), encoding="utf-8")
        (pj / "reviews").mkdir()
        bk = [{"id": f"k{i}", "stars": 4, "text": f"Booking review {i}", "sha256": hashlib.sha256(f"Booking review {i}".encode()).hexdigest()} for i in range(20)]
        (pj / "reviews" / "7.booking.json").write_text(json.dumps({"method": "apify-booking-reviews", "retrieved_at": "2030-01-01T00:00:00Z",
                                                                  "source_url": "https://www.booking.com/hotel/z.html", "reviews": bk}), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            rc = cmd_reviews(pj)
        names = [x.get("name") for x in gather_sources(pj, row)]
        cases.append(("a provider file in reviews/ becomes a second review source", rc == 0 and "Booking reviews" in names and len(names) == 2))
        gid = load(pj / "reviews" / "7.google.json", {})["reviews"][0]["id"]
        (pj / "synthesis").mkdir()
        good = {"themes": [{"text": "quiet", "sentiment": "pos", "review_ids": [gid, "k0"]}], "concerns": [],
                "web": [{"name": "local guide", "url": "https://guide.example/z", "read_at": "2030-01-02"}]}
        (pj / "synthesis" / "7.json").write_text(json.dumps(good), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            rc_ok = cmd_themes(pj)
        cases.append(("themes that cite read review ids are collected into themes.json", rc_ok == 0 and (pj / "themes.json").exists()))
        (pj / "themes.json").unlink()
        good["themes"][0]["review_ids"] = ["never-read"]
        (pj / "synthesis" / "7.json").write_text(json.dumps(good), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            rc_bad = cmd_themes(pj)
        cases.append(("a theme citing a review that was not read is refused and nothing is written", rc_bad == 1 and not (pj / "themes.json").exists()))
    ok = all(x for _, x in cases)
    for name, x in cases:
        print("PASS" if x else "FAIL", name)
    print(f"SELFTEST {'PASS' if ok else 'FAIL'} ({len(cases)} cases)")
    return 0 if ok else 1


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    for name in ("reviews", "themes", "scorer-view"):
        sp.add_parser(name).add_argument("project")
    p = sp.add_parser("build"); p.add_argument("project"); p.add_argument("--report"); p.add_argument("--out"); p.add_argument("--merge-into")
    p.add_argument("--store")
    sp.add_parser("selftest")
    a = ap.parse_args()
    if a.cmd == "selftest":
        return selftest()
    if a.cmd == "reviews":
        return cmd_reviews(a.project)
    if a.cmd == "themes":
        return cmd_themes(a.project)
    if a.cmd == "scorer-view":
        return cmd_scorer_view(a.project)
    inside = lambda v: str(Path(a.project) / v) if v and not Path(v).is_absolute() else v
    a.report, a.store, a.merge_into = inside(a.report), inside(a.store), inside(a.merge_into)
    if a.merge_into and not Path(a.merge_into).exists():
        print(f"FAIL {a.merge_into} does not exist: start the itinerary first (pipeline.py init . --shape itinerary), then merge the stays into it")
        return 1
    base = json.loads(Path(a.merge_into).read_text(encoding="utf-8")) if a.merge_into else None
    spec = build(a.project, a.report, base)
    out = Path(inside(a.out)) if a.out else Path(a.project) / "spec.json"
    ITINERARY_BLOCKS = {"day_plan", "transport_leg", "booking_ledger", "decision_log", "research_notes", "cost_comparison"}
    if not a.merge_into and out.exists():
        try:
            held = {b.get("type") for b in json.loads(out.read_text(encoding="utf-8")).get("blocks", [])}
        except (OSError, ValueError, AttributeError):
            held = set()
        if held & ITINERARY_BLOCKS:
            print(f"FAIL {out} holds an itinerary ({sorted(held & ITINERARY_BLOCKS)}); a plain build would overwrite it. For a mixed trip rebuild the stays with "
                  f"--merge-into {out.name}, or give --out a different file")
            return 1
    if a.merge_into:
        if a.out:
            print("FAIL --out cannot be combined with --merge-into: the merged spec is written to the --merge-into file")
            return 1
        spec, out = merge_into(spec, Path(a.merge_into)), Path(a.merge_into)
    out.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out}: {sum(len(b.get('items', [])) for b in spec['blocks'] if b['type'] == 'cards')} cards")
    kb = Path(a.project) / "RESEARCH.md"
    if kb.exists():   # a mixed trip's RESEARCH.md already exists: add a stub section for any stay card that has none
        have = kb.read_text(encoding="utf-8")
        nl = chr(10)
        add = "".join(f"## {it['name']}{nl}{nl}{it.get('url') or ''}{nl}{nl}Read: (date){nl}{nl}" for _, it in guard.items_of(spec) if f"## {it['name']}" not in have)
        if add:
            kb.write_text(have.rstrip() + nl + nl + add, encoding="utf-8")
            print(f"added stub sections to {kb} for the new cards; fill in what was read and when")
    if not kb.exists():   # the audit needs a section per place with its link and a read date; the dates are the author's to fill in
        nl = chr(10)
        kb.write_text("# Knowledge base" + nl + nl + "".join(f"## {it['name']}{nl}{nl}{it.get('url') or ''}{nl}{nl}Read: (date){nl}{nl}" for _, it in guard.items_of(spec)), encoding="utf-8")
        print(f"wrote {kb}: a section per place; fill in what was read and when (audit_report.py checks each has its link and a date)")
    if a.store:
        seeded, left = seed(spec, a.project, a.store)
        print(f"seeded {len(seeded)} capture(s) from saved evidence; {len(left)} claim(s) still need capturing "
              f"(fact-guard's pipeline.py capture), which the gate will name")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, TypeError, AttributeError, KeyError, IndexError) as e:   # json.JSONDecodeError is a ValueError
        raise SystemExit(f"refused: an input file is malformed or not in the shape this step expects ({type(e).__name__}: {e})")
