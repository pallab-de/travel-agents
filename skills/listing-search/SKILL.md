---
name: listing-search
description: "Collect every rental or hotel candidate (search, grid sweep, no-filter sweep), parse details, drive times, beyond-platform leads. Use after requirements-intake and environment-setup."
user-invocable: true
---

# Listing search

Collect every viable candidate, not a first page. One keyword search misses real candidates, and a platform's amenity
tags (pool, pets) are host-entered and often wrong or missing. Two extraction paths: the Airbnb/rental path and the hotel
path (Google Hotels / Booking.com). The sweeps, beyond-the-platform research and coverage checks apply to both.

**Inputs:** `config.json` from `requirements-intake` (dates, guests, filters, home base). **Output:** one row per
candidate in `merged.json`, with raw listing text kept beside it.

## Rental path (Airbnb)

File flow, in order (all in the project folder): `cands_raw*.json` (one list per extraction batch) -> `dedupe_cands.py cands_raw*.json --out cands.json`
(`{id: record}`, one per listing, in the shape the next steps read) -> `drive_times.py cands.json cands2_drive.json` (adds the drive times) ->
`places4.json` (place-matching, `{id: match}`) -> `detail_raw.json` / `detail_parsed.json` (step 4) -> `merge.py` -> `merged.json`. `merge.py` merges every candidate
with a parsed page; pass `--keep ids.json` only to leave listed ids out, and it says how many it left out.

1. **Platform search** with every hard filter from `config.json` applied (dates, guests, pets, entire place, bedroom
   minimum). Run `scripts/extract_airbnb.js` through Playwright's `browser_evaluate` on a loaded search-results page
   (it returns id, name, coordinates, price, rating, badges per result; its header lists the field paths and the date
   it was last verified). Then **run `scripts/check_airbnb_coverage.py` on every batch**: a failing batch means
   Airbnb's page schema moved (`fetch-integrity`), not that the city has thin inventory.
2. **Map-grid sweep.** Repeat the search over adjoining map areas around the home base out to the drive-time cap.
3. **No-amenity-filter sweep.** Repeat without the pool filter, then check listing text and photos for a private pool
   the host never tagged: hosts under-tag more often than they over-claim.
4. **Parse detail pages.** Save each page's visible text as `detail_raw.json` (`{id: text}`) and run
   `python "<plugin root>/skills/listing-search/scripts/parse_details.py" --raw detail_raw.json --out detail_parsed.json`.
   It extracts the bedroom and bed breakdown (the literal "where you'll sleep" text), amenities, house rules,
   cancellation policy, description and host tenure. Keep the raw text: later reviews grep it for facts nobody
   structured the first time. While each page is open, also save its photo urls (https image urls the page serves) into `photos.json`
   (`{id: [urls]}`); `stay_rows.py` reads that file into the cards.
5. **Drive times:** `scripts/drive_times.py --home "<home_base_address from config.json: an area, station or landmark>" --out-departure <iso> --back-departure <iso> --tz <offset> --cap-min <n> <in>.json <out>.json`,
   with the trip's actual departure times, never straight-line distance.
6. **Merge** (after `place-matching`): `python "<plugin root>/skills/listing-search/scripts/merge.py" --config config.json --booking-url "<listing URL with {id}>"`
   (more flags in its docstring). It combines cards, parsed pages, raw text and Google matches into `merged.json` and
   applies the real-bed rule from `config.json` (`group.adults`, `real_bed_rule.adults_need_beds`); it refuses to run
   without those or without a real booking URL.
7. **Beyond the platform.** Web-search local articles and roundups, and established managed-villa or direct-booking
   brands for the destination. For each promising site use its API or structured data, or a Playwright pass over its
   listing cards; confirm coordinates and match each lead to Google (`place-matching`) so it gets the same checks.
   `report-builder` has the pipeline (`direct_booking_pipeline.py`).

## Hotel path (Google Hotels / Booking.com)

The filters differ (rooms, guests per room, a star floor; `requirements-intake` sets the rental filters to `n/a`), but
the discipline is the same: a sweep, a second independent source, visible exclusion flags, a coverage check per batch.

1. **Check Playwright MCP availability first** (`environment-setup`). If it is registered, drive Google Hotels or
   Booking.com's results page: it gives live prices for the dates and rooms. If it is not, use step 2 and say in the
   report that prices are indicative.
2. **Booking.com landmark pages, no browser needed.** `https://www.booking.com/landmark/<cc>/<slug>.en-gb.html` (find
   slugs with `site:booking.com/landmark/<cc>/ <city> <landmark>`) is server-rendered with the property data in
   `"accommodations":[...]` arrays. `scripts/extract_booking_landmark.py <urls...> --out raw_booking.json` writes one set of candidates deduplicated by
   property id; pass several landmarks across the area as the equivalent of a grid sweep. **Read its docstring before
   using a price:** the embedded "price from" is a cached 1-night, 2-adult rate that does not move with dates or
   rooms, so use it to shortlist and never quote it as live.
3. **Run `scripts/check_booking_coverage.py` on every batch.** It checks each record kind (full
   `LxAccSearchResultSnippetProperty` entries against sparser carousel kinds) against its own expected coverage.
4. **Add a second source.** The landmark carousels under-sample very close budget options; a general aggregator's
   "hotels near <landmark>" page (via `WebFetch`) shows hotels the sweep missed.
5. **Match every candidate to a Google place.** Hotels go through `hotel-dossier`'s `google_places.py` (matched by name above a similarity threshold, inside the required
   city); there is no `places4.json` on this path. `hotel_rows.py --write-hotels` lists every hotel, including one with no pin (it is geocoded by name and area for the drive time
   and flagged as approximate), rather than dropping it.
6. **Rows and the report:** `hotel-dossier` builds the Google dossier (`google_places.py`, `rail_distance.py`), then `report-builder`'s `hotel_rows.py` turns
   `raw_booking.json` and `google.json` into `report_data.json`; the rest of the stay pipeline is the same.
7. **Photos from the hotel's own site or a booking site** (an https image url; the build downloads and embeds it, and the photo
   rule captures that url answering HTTP 200). Do not use Google Places photos: their urls carry the API key and Google's terms
   restrict storing them. **Review text** comes from Place Details `reviews` (up to 5 per place) and the providers in
   `review-synthesis`; this depth is a sample, not a review corpus: say so in the report.

## Gotchas

- **One direct-booking site is not the non-platform market.** A "pet-friendly collection" page may be one company's
  inventory. Search for other brands and city roundups, and check a lead's own Google Business listing rather than the
  source site's numbers.
- **A hard filter (pets, pool, whole property) must be confirmed at the lead's own source.** If the source does not
  state it, mark the filter unconfirmed and say so.
- **Playwright hygiene:** restart the browser periodically on a long sweep (memory climbs over hundreds of pages), never
  run two scripts against one shared session, and leave it on a blank page.
- **Airbnb's schema changes without warning.** Trust `extract_airbnb.js` only as far as its "last verified" date;
  re-verify on a live page if that is old or a coverage check fails.

## Done when

- The coverage check passed on this session's batches.
- The grid sweep and the no-filter sweep ran, and each one's new-candidate count is recorded (a sweep adding zero
  candidates needs its grid and parameters checked).
- Every hard filter applied at search time is also a visible flag on each row, not a silent drop.
- At least one non-platform source beyond a single direct-booking site was searched, and its queries are noted for the
  report's method section.
- Raw listing text is saved per candidate.
- Hotel path: a cached "price from" is never presented as a live quote; the fallback to HTTP fetch was a stated choice.
