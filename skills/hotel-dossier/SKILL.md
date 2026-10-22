---
name: hotel-dossier
description: "Per-hotel dossier for a hotel shortlist: Google match, ratings and reviews, rail distance, photos, booking facts. Use on the hotel path, after listing-search."
user-invocable: true
---

# Hotel dossier

A hotel card in a report carries a dossier: one section per source, each with its figures, its link and its
capture date. The fact-guard gate refuses a card whose required section is missing or empty without a reason.
This skill says what each section must contain and which script produces it.

## The contract (what the gate checks)

Required sections (`policy.required_sections`; a spec may add sections, never drop these):

| Section title | Must contain | Produced by |
|---|---|---|
| Map listing | the matched place name, address and place ID | `scripts/google_places.py` |
| Guest ratings | each platform's headline rating and count (Google, TripAdvisor, verified-stay booking sites), two captures agreeing, each labelled with its platform | `scripts/google_places.py` (run twice on separate loads); manual capture for the others |
| Guest reviews | the reviews actually read, per source, with stars, date and text (these are the review files the guest score is computed from) | `review-synthesis` (`serpapi_reviews.py`, `apify_reviews.py`, `google_reviews10.py`) |
| Photos | image URLs from the property's own site or a booking site, each of which answered HTTP 200 | manual capture, one capture per image; a hotel group's photo CDN goes in the card's `own_domains` |

Add sections a trip needs, for example a rail or landmark distance measured on Google Maps
(`scripts/rail_distance.py`, `tools/place_distance.py`).

A section a source could not supply is written as `{"not_found": "<reason>"}`, never left empty. The reason is shown
to the reader in the panel, so "no TripAdvisor page matched under this name" is an acceptable answer and a blank
is not.

## Guest score (the enforced rule)

A card's guest score is a judgement made by reading reviews, not a number copied from a rating. fact-guard's
`rank-score-v2` recomputes it from the stored review files:
- verified-stay booking sites count 1.5 and review platforms 1.0;
- a source counts once 20 of its reviews were read;
- at least two sources count, or the missing one is declared unavailable with a reason;
- the property's own site never counts.

Each file records its method and retrieval time, and each review its text hash. Headline ratings stay in the
"Guest ratings" section, labelled with their platform. Until the reading is done, the card shows no guest score.

## Rules

- **Rail distances come from Google Maps, never from the operator's page.** The hotel's own figure can be recorded,
  but the report shows the Maps walking distance and labels it. `rail_distance.py` flags a disagreement; it does
  not choose a side.
- **Google's Places API returns 5 reviews per call (about 10 per place with two calls)** (plus its rating and count). Say so in the panel.
  Do not present the five as the whole review set.
- **TripAdvisor snippets are extractor output.** Quote them as "review summary", not as verbatim reviews, unless
  the text was captured from the page with its raw excerpt.
- **Names are matched, not assumed.** `google_places.py` accepts a match only above a name-similarity threshold
  (0.75) and records `not_found` otherwise. Check a match by its address before trusting its rating.
- **Counts drift.** Review counts change between page loads. The gate allows 1%; ratings must agree exactly.
- **Photos must answer HTTP 200 on capture.** A photo that loads only in a browser is not evidence; record the status.

## Steps

1. **Hotel list.** `python "<plugin root>/skills/report-builder/scripts/hotel_rows.py" . --area "<City>" --write-hotels` writes `hotels.json`
   (`[{"id", "name", "query", "lat", "lng", "require_in": ["<City>"]}]`) and `hotel_cands.json` from `raw_booking.json`, with ids `h<propertyId>`: the key
   `hotel_rows.py` joins every other file on, so do not key by name or by another id (a Google match under any other key is ignored, and a WARN names it). `require_in` lists words the matched place's address must contain (the city, and its local name if different): a hotel with neither this nor `--require-in` is refused, so a same-named place in another city cannot be matched.
2. **Google.** `python -B "<plugin root>/skills/hotel-dossier/scripts/google_places.py" hotels.json google.json [--require-in "<City>,<local name>"]`.
   The API key comes from the encrypted environment (`tools/travel_secrets.py`); it is never printed. It writes `google.json` (each run overwrites the last) and
   never touches the capture store: save each run's response, and capture each Google rating and count twice, on separate loads, with
   `pipeline.py capture PROJECT ROW <the Maps url> VALUE --raw-file <the saved response>`.
3. **Rail.** `python -B "<plugin root>/skills/hotel-dossier/scripts/rail_distance.py" hotels.json rail.json --city "<City>,<City local name>"` (add `--line "Label=search term"` per rail system, e.g. a city's metro and suburban rail). Each
   record names the stations it found (by place types, not by name, so a shop named after a station is not one).
4. **TripAdvisor and photos.** Capture from the pages with the browser tools, two loads for each headline figure.
   Check the page title and URL before recording a value: a redirect to another hotel is a wrong capture. Record
   each source's URL and the capture time.
5. **Assemble and gate.** For a stay report the spec is generated: do not edit `spec.json`. `hotel_rows.py . --area "<City>" --home "<starting point>"` (without `--home` no drive time has a directions link to cite, so they are dropped) turns `raw_booking.json` and
   `google.json` (and `drives.json`, the output of `listing-search/scripts/drive_times.py hotel_cands.json drives.json ...`, and `photos.json`, `{id: [https urls]}` of the
   hotel's own or a booking site's photos) into `report_data.json`; `stay_spec.py build` makes the dossier. A rail distance goes in `stay_report.json` `extra_facts`
   as `{"h<propertyId>": [{"label": "Nearest rail", "value": "<station>: <m> m walk, <min> min (Google Maps)", "source": <the Maps link>}]}`; the gate checks it
   against `rail.json` beside the spec. Other sections are captured as facts the same way. Store every figure with `pipeline.py capture`, then run fact-guard's
   pipeline steps (status, review, sample, second look, spot, gate, build, check). A hotel row shows no price (the booking site's cached rate is not for the dates).

## What this skill does not do

- It does not write the spec: `report-builder`'s `stay_spec.py` generates it from the files this skill produces.
- It does not rank hotels: `fit-scoring` and fact-guard's `rank-score-v2` do, from captured facts.
- It does not measure landmark distances itself. Use `tools/place_distance.py`, the generic Maps distance tool that rail also rests on: any origins, any named places, walking or driving, with the same city guard. A named destination must come back under (nearly) that name: a different place Google offers for an obscure name is reported `not_found` with the closest result named, unless the destination sets `accept_first`; give `name` when the query is longer than the place's name.

## Validation

- `google_places.py` and `rail_distance.py` need a key for their live calls; their offline selftests run in `tools/run_selftests.py`. Their matching and
  disagreement rules are also covered by the fact-guard gate's rail rule.
- The completeness rule is tested by `guard.py selftest`: a card with a missing section must fail.
