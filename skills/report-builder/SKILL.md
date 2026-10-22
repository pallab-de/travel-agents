---
name: report-builder
description: Builds every report and itinerary from one generic block engine: a JSON spec rendered into one designed, self-contained HTML page (hero, notice, ranked cards with details, an explorer table, chart, day plans, transport legs, booking ledger, method). The build refuses a spec without a design or one fact-guard's gate rejects. stay_spec.py turns a stay search's ranked rows into a spec. Use after review-synthesis (and fit-scoring for a ranked list), before the review panel.
user-invocable: true
---

# Report building

## One engine, data not markup

Every deliverable uses `template/generic/`, whatever the subject: a ranked stay list, hotels, venues, a day-by-day
trip, or a mix. The page is an ordered list of blocks rendered from one JSON spec. The engine knows nothing about
the subject; the project supplies only data. Build with:

```
python "<plugin root>/skills/report-builder/template/generic/build_page.py" spec.json out.html --review review.json --spot spot.json [--store captures]
```
(`--store` defaults to `captures/` beside the spec; the build refuses without a capture store. `fact-guard`'s `pipeline.py build` does all of this.)

The build runs fact-guard's gate first and refuses on any failure. **Read this skill before writing any report.**
Never hand-write report HTML, and never fork the engine for a subject: a missing capability becomes a generic block
or block option, with its schema in `page.html`'s header comment.

## Blocks

| Block | Shape (full schema in `page.html`) |
|---|---|
| `hero` (first) | `{eyebrow, title, lead, kpis:[{value,label}], image, image_alt, image_credit}`; image is https or a local file beside the spec |
| `notice` | `{title, items:[text]}`: an editorial ledger of the caveats that change how a number reads |
| `cards` | `{id, kicker, title, intro, items}`; an item: `name, id, url, area, rank, score, score_basis, price, priceNote, unavailable, images, badges, facts, pros, watch, verdict, dossier` |
| `table` | `{id, kicker, title, intro, columns:[{key,label,type:'num'\|'text'\|'cat',filter,hidden,link}], group_by:[key], compare:bool, items}` |
| `chart` | `{id, kicker, title, intro, source:<table id>, x:{key,label}, y:{key,label}}`: a scatter of two numeric table columns |
| `method` | `{id, title, items:[text]}` |
| `day_plan` | `{id, title, days:[{date, label, city, stops:[{time, name, note, url, type, fixed, price, source, leg}]}]}` |
| `transport_leg` | `{id, title, legs:[{label, from, to, modes:[{mode, time_minutes, distance_km, cost, source, captured_at, segments}], recommendation:{mode, reasons}}]}` (see `transport-options`) |
| `booking_ledger` | `{id, title, bookings:[{name, category, amount, currency, status, cancel_by, check_in, check_out, url, source}], summary:{<currency>: total}}` |
| `decision_log` | `{id, title, entries:[{date, title, decision, why, status:'decided'\|'open'\|'superseded'}]}`: what was decided and why; a superseded decision stays, marked |
| `research_notes` | `{id, title, notes:[{topic, finding, sources:[{name, url, read_at}], not_verified}]}`: every finding names where and when it was read |
| `cost_comparison` | `{id, title, options:[{label, cash:{amount, currency}, points:{amount, programme}, mechanic, url, source, captured_at}], recommendation:{option, reasons}}`: two or more options and how the money actually moves |

`spec.locale` (a language tag such as `en-GB`) sets how amounts are formatted. A stop in a day plan, and a booking, may
carry a `status` badge (`booked`, `held`, `open`, `cancelled`, `rejected`).

**The explorer.** A table's reader tools are all switched on by data:
- `filter: true` on a column: a menu of its values, tick any number; on a `num` column, a min-max range.
- `hidden: true`: the column starts hidden, and the Columns menu shows it.
- `group_by`: the reader can group rows by those columns, with a header and a count per group.
- `compare: true`: tick up to 4 rows and open them side by side.
- A row (or a chart point) whose name matches a card opens that card's details panel. Previous and Next step
  through what's on screen, in its current order. Arrow keys work too.

**Every figure published is checked.** A card's figures are fact-guard ledger rows. A table row with no card behind
it is checked the same way: each classifiable column becomes a ledger row, with its source in the row's
`sources: {<column key>: url}` (else the row's url). A chart holds no data of its own; it draws from its table, so it
can't disagree with it.

**`watch` items** are plain strings (practical caveats: steep stairs, no lift) or
`{text, review_ids, isolated}` for a review-derived complaint. Those pass fact-guard's overindexing rule, or are
marked `isolated: true`, which the page labels "(one report)".

**The spec is the source of truth for an itinerary:** there is no way to patch rendered HTML, and a multi-session itinerary is edited by editing its spec and
rebuilding. A **stay's `spec.json` is generated** by `stay_spec.py build` from `report_data.json`, `stay_report.json`, `themes.json` and the review files;
change those inputs (extra facts go in `stay_report.json` under `extra_facts`), rebuild, and apply `fit_overlays.json` again with `apply_overlays.py`.

## Stays: `scripts/stay_spec.py`

A stay search (listing-search → place-matching → review-synthesis) produces `merged.json` (a hotel search: `raw_booking.json` and `google.json`, turned into rows by
`scripts/hotel_rows.py`); `scripts/stay_rows.py PROJECT --config config.json`
turns it into the rows in `report_data.json` (direct-booking leads go in `beyond_data.json`). `stay_spec.py reviews` writes the review files
the synthesis agents cite, and `stay_spec.py themes` collects their `synthesis/<id>.json` into `themes.json`. `stay_spec.py build <project> --store captures` turns them into a
spec:
- cards for the shortlist and for the direct-booking leads, with a details panel per place (map listing, guest
  ratings, reviews read, photos, sleeping, amenities, house rules, pets, what to ask);
- an explorer table of every place;
- a cost-against-drive chart;
- review files with provenance, for the rank score.

The author writes `stay_report.json` per trip: title, eyebrow, lead, hero image, notice and method rows, price
note, currency symbol, how many cards, and the design. Then:
1. It seeds the capture store only with figures shown verbatim in saved evidence (a listing page's text, a
   Places API response). The gate names every other figure as uncaptured; capture those with fact-guard's
   `pipeline.py capture`.
2. Rank scores appear once reviews are read and themed (`themes.json` from review-synthesis). The fit scorer's
   overlays are then added by `fit-scoring/scripts/apply_overlays.py`, which re-ranks.
3. Photos must be https URLs the listing actually serves. Save them while the listing page is read, as `photos.json` (`{row id: [https urls]}`) in the
   project folder; `stay_rows.py` and `hotel_rows.py` read it into each row's images, and each photo then needs a capture that answered HTTP 200. A photo with no
   recorded source can't pass the photo rule, so a row without `photos.json` entries shows no photo and the details panel says why.
4. `stay_spec.py build` writes a `RESEARCH.md` skeleton beside the spec if there is none: a section per place with its
   link. Write what was read, from where, and the read date under each; `audit_report.py` fails a place with no section, no
   link or no date.

## Itineraries

A multi-day, multi-place trip is a spec like any other, built from `day_plan`, `transport_leg`, `booking_ledger`,
`cost_comparison`, `decision_log` and `research_notes` blocks, with `cards` for the stays when they are ranked. It can
sit in one document with a stay search: a mixed trip is one spec. `fact-guard/examples/itinerary/` is a complete,
fictional one; `pipeline.py init PROJECT --shape itinerary` starts a spec with one day per date of the trip, read from
`trip_intake.json`.

**Multi-session work is editing the spec and rebuilding.** There is no patching built HTML, and no `_v2` copy of
anything: keep the trip folder under git so the spec and its history travel together. Rebuild after each change; the
gate runs every time, and a changed spec invalidates the review, the spot check and the built page.

**Rules for the words in an itinerary** (the build enforces the limits; these are the rest):
- *State the trip, not the document.* A sentence about the plan or the page itself ("everything here is scannable",
  "Part 7 covers this") tells the reader nothing about the trip. Cut it. It never becomes shorter copy; it goes.
- *Current state only.* No date stamps on facts, no "revised from", no "now confirmed as". When the history of a
  decision would change what the reader does, it is a `decision_log` entry, with its reason. The test: if this
  sentence were deleted, would the traveller be missing something they need, or only something about how the document
  came to be? Delete the second kind.
- *One canonical value per fact.* A fact restated in a second form is a redundancy bug, and the build refuses a
  repeated phrase. Say it once and let the other places point at it.
- *A rejected option stays, with its reason.* Give the stop `status: "rejected"` and a note saying why; never delete
  it and never strike it through. It keeps a later session from re-arguing what is settled. A rejected or cancelled
  stop is neither timed nor ledgered.
- *Currency follows the place.* Show a price in the currency it is charged in, formatted by the spec's `locale`; give
  the traveller's home currency as one parenthetical conversion at first mention, then use whichever is primary for
  the section. A conversion is a figure and needs a source like any other. Number formatting follows the locale; the
  report's language stays one language.
- *Show the mechanic, not just the sticker price.* Two costs with different headline prices can differ in how money
  moves: a cheaper cover that is reimbursed afterwards, a points price with a minimum transfer, a "refundable" fare
  with a rebooking fee. Use a `cost_comparison` block and say when and how each is collected.
- *Verify before writing.* Any checkable claim (hours, price, whether a place still exists, a distance, a showtime) is
  verified with a tool first (`venue-verification`). A time that people act on (a train, a reservation, a ticket) is
  checked from two independent sources by the gate.

## Design is required (the build refuses a spec without it)

Every report carries a `design` object written for its subject:
- **`brand` and `ethos`:** the wordmark in the top bar, and one or two sentences on what the report is for and how
  it should feel (40+ characters).
- **`palette` and `palette_dark`:** the colour tokens (`bg surface ink mute line accent accent_ink chip good warn
  bad eyebrow on_image image_overlay`).
  - Derive them from the hero photo with `visual-identity/scripts/extract_palette.py`, then curate by hand.
  - The build checks contrast: body text 4.5:1, the accent (used as link and rank text) 4.5:1, text over the photo overlay 4.5:1.
- **`fonts`:** a display family and a body family (Google Fonts names, letters and spaces only).

Motion is part of the design: a hero parallax, counters that count up, notice rows that slide in, cards that rise.
Under `prefers-reduced-motion` everything is visible at once and nothing moves.

## Principles

- The hero states the decision the reader came for, with 3-4 KPIs.
- A notice block comes before the data and holds every caveat that changes how a number reads.
- Cards carry judgement for ranked items (rank, verdict, why it ranks, watch-outs). The table carries comparison.
  Use both.
- Each item says what was checked and how. Unverified facts say "not checked", never a guessed value.
- Unavailable items stay in the list, marked.
- Photos: a cover per card that hides itself on error. Never an image whose source you haven't confirmed belongs to
  the item.
- The method block lists sources, read dates, scope and limits, briefly.
- Layout: one column of cards on phones; tables scroll inside their own container; the page never scrolls
  sideways.

**Copy.** Word limits are enforced by the build: eyebrow 12, title 8, subtitle/lead 32, ledger row 26, card verdict
28, watch-out 14, method item 32. The header is three short lines: eyebrow (who, when, how long), title (the
subject), subtitle (for whom, in one sentence). Fictional examples of the target:
- eyebrow "2 adults + a grandparent, 3 nights in March" · title "Lakeside stays near Valmora" · subtitle "For a family
  that wants to unwind: whole homes with a pool, ranked on fit, guest reviews and drive time."
- eyebrow "Six friends, one weekend" · title "Party villas near Harrowgate" · subtitle "For a group that wants music
  and a pool: villas that allow both, ranked on fit and reviews."
- eyebrow "Two weeks, five towns" · title "Selmere, coast to hills" · subtitle "For a couple who likes it slow: the
  route, the stays and each day's plan, checked."

The title is the subject, never a sentence; the subtitle never repeats the title or the eyebrow; a fact appears
once (header, row or card); a long explanation belongs in the method block.

**Sorting is checked, not assumed.** A `num` column sorts by value even with a currency or unit in the text. The
build refuses a `text` column whose values are mostly amounts, and a `num` column that is mostly text.

## Direct-booking leads get the same treatment

If the search found leads outside the booking platforms, they get real photos, their reviews read where a platform carries them, and the same
cards-plus-table structure, labelled by their actual source. Their order comes from `direct_booking_score.py`'s own formula (drive time, price, beds, a
shaded site rating), not from the rank score, and the method block says so.
- **Multi-source, not one site wearing the section's name.** Searching one direct-booking site gives that site's
  inventory, not "beyond the platforms".
- **A hard-filter check is visible per row.** Say how each lead passed it (the site's own pet-friendly listing; an
  article's claim cross-checked against the map listing).

`scripts/direct_booking_pipeline.py` and `scripts/direct_booking_score.py` are the pattern for this (match,
drive time, filter, score, package into `beyond_data.json`); adapt them per trip.

## Gotchas

- **Run every stage in order.** Re-running a later stage after skipping an earlier one builds from stale data that
  looks fine; `pipeline.py status` and `check` show what is stale.
- **Never run two copies of one long job against the same output.** They corrupt it and waste paid API calls; check
  nothing is still running before restarting.
- **A flag states its actual reason, from the row's own data**, never one blanket string ("No independent Google
  rating" must not cover four different reasons).
- **After improving a matcher, re-run everything that consumed its output** (`direct_booking_pipeline.py
  --rematch-only` re-does just the matching).
- **A lookup by id fails loudly** ("property X has no score") rather than with a bare `KeyError`.

## Done when

- `build_page.py` builds without a refusal (the spec validates and fact-guard's gate passes).
- `pipeline.py selftest` builds and gates the bundled itinerary example, a mixed page of route, day plan, bookings,
  fares, decisions, notes and stay cards.
- Opened over http (the browser tool blocks `file:`), the page shows no console errors other than a favicon 404.
  Every card, table row and chart point renders. A numeric column sorts; a filter menu and a range filter narrow
  the rows; group-by and compare work; a row opens its details and Previous/Next step through them; the theme
  toggles. At 390px width the page doesn't scroll sideways.
- `audit_report.py <project> <report.html>` passes.
- `stay_spec.py selftest`, `guard.py selftest` and `pipeline.py selftest` print `SELFTEST PASS`.
