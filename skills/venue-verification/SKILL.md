---
name: venue-verification
description: Verifies one named venue or transit leg (existence, business status, hours, rating, distance) with a tool before any claim about it is written into a trip document, and weighs its reviews in proportion. Use for vacation-planner's fact-by-fact research; use place-matching instead to fuzzy-match many scraped candidates in bulk.
user-invocable: true
---

# Venue verification

**Rule: every specific, checkable claim (opening hours, ticket price, whether a place still exists, the distance or
drive time between two points, a showtime) is verified with a tool before it is written down.** Background knowledge
is for judgement calls (suitable for a young child? worth the detour?), never for a fact with a findable answer.

## Tools, in order of preference

1. **`scripts/check_place.py "Name, City" [--reviews]`**: existence, `business_status` (catches a permanently or
   temporarily closed place that a blog still lists), rating, hours, phone, website, top review snippets. Run it
   before recommending any named venue.
2. **`scripts/check_distance.py --provider google|ors "Origin" "Destination"`**: distance and time between two named
   places, without a traffic model. For a time-critical leg (a set departure, traffic matters) use
   `transport-options`' `compare_modes.py drive ORIGIN DEST out.json --depart <ISO time with UTC offset>`: it uses
   the trip's departure time and writes a JSON with its source and `captured_at`; copy the mode into the spec's `transport_leg` block and store the figure with fact-guard's `pipeline.py capture`.
3. **`scripts/find_hotels_nearby.py "Landmark, City" --radius 4000 [--type lodging]`**: a radius sweep of Google
   Places Nearby Search for candidates not yet named, sorted by rating. It prints `TRUNCATED` when Google had more
   results than it read.
4. **A browser (Playwright), last resort**, for content a script cannot reach: a page whose text is not in the HTML,
   or a site blocked at the network level for a plain fetch (Reddit often is; a browser navigating directly and
   reading `document.body.innerText` works). Space out navigations: rapid requests trigger blocks. Run
   `scripts/check_fetch.py <text file> --expect <place>` on the text before trusting it: a navigation that
   succeeds can still return a block page (`fetch-integrity`).

The scripts read `GOOGLE_MAPS_API_KEY` (and `OPENROUTESERVICE_API_KEY` for `--provider ors`) through
`tools/travel_secrets.py`; `environment-setup` covers getting and encrypting them. Never print or echo a key, and never
build a shell command that interpolates one.

## Weighing what you find

- **One dramatic review does not sink a pick with a large, otherwise clean aggregate.** A pattern does, by fact-guard's
  overindexing rule (two independent sources, or enough reviews read with a real share raising it). Recency and vivid
  writing carry no extra weight.
- **Structural and safety complaints count from one credible account** (theft, no lift, sealed windows). Service
  complaints (rude staff, cold food) need a second independent voice before they are representative.
- **Group differences too small to matter into tiers.** A 1-minute drive difference or a 0.1-star gap is noise.
- **Read past Google's curated top reviews** for anything you will recommend as a top pick: the first 3 to 5 skew
  glowing, and the independent complaints sit deeper.
- **Look beyond SEO listicles** for what matters: `site:reddit.com <city> itinerary`, `<city> avoid tourist trap`,
  and a city-specific award list or two. Independent convergence means sources that do not cite one original list.

## "Confirmed" must trace to a source

Do not present an inferred, remembered or assumed fact as confirmed. "Confirmed" in a document must trace to a tool
call, a page you fetched, or a screenshot the user shared, never to an earlier draft (after a context compaction a
tentative note reads as certain). Re-verify anything with stakes (money, cancellation deadlines, whether a place
exists) rather than trusting a document's own confident wording.

## Gotchas

- **A cited URL is not verified until it has been fetched and read.** A claim "per the hotel's room page" from a
  search summary, with the page never opened, is unverified.
- **Two businesses can share a name** (a hotel and a same-named restaurant, two branches of a brand), and a large
  review count may blend them. If a rating looks too large or small for the venue's size, check for a name collision.
- **A price is ambiguous until pinned:** per night or for the stay, which room tier, for these dates (a festival
  weekend prices far above a midweek reference rate).
- **A cheap price is often a real cheapest room category**, not a scam: read the tier breakdown first.

## Done when

- Every checkable claim in the document traces to a tool call or a source the user supplied.
- Every top-pick venue had its reviews read past the curated first page, not only its star rating.
- Every verified fact is logged with its source in the spec's `research_notes` and in `RESEARCH.md`, not left in chat.
- Nothing marked "confirmed" is an inference: spot-check a sample before calling a section done.
