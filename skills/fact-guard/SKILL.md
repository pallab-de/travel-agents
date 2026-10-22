---
name: fact-guard
description: Fail-closed verification gate for any report or itinerary spec. Extracts every checkable claim (price, score, count, distance, time, booking, availability, photo, link) into a ledger, checks each source fits its kind of claim and is fresh, needs two independent sources for critical facts, recomputes the rank score, checks the plan for inconsistencies, stores every capture offline with its raw evidence, spot-checks a deterministic sample with a fresh second look, and blocks the build until everything passes. Use before any report is published or shared.
user-invocable: true
---

# Fact guard

"Please check it" is not a check: self-review misses a wrong hotel ID, a wrong price, a score marked "not captured"
when the page had it, a label the page never showed. This skill turns the check into gates that fail when a check is
missing, stale, single-sourced or disagreed with.

The pipeline is driven by `scripts/pipeline.py` (`python -B "<plugin root>/skills/fact-guard/scripts/pipeline.py"
--help`; `<plugin root>` is the folder the plugin is installed or cloned into): `init`, `capture`, `exclude`, `status`,
`review`, `sample`, `spot`, `gate`, `build`, `check`. This skill describes what those commands enforce and the files
they write. You can run the parts directly. A bundled fictional example (three invented inns on reserved `.test`
domains, no real hotel or price) is in `examples/`: copy it to start a project, and `pipeline.py selftest` runs it
offline. Reserved example hosts (`.test`, `.example`, `.invalid`) can be added to a fixed class only in a spec whose footer says it is synthetic.

**Project folder.** One folder holds a job: `brief.json` (the question, audience, decision, criteria, what is out of
scope, the assumptions), `spec.json`, `captures/`, `review.json`, `spot.plan.json`, `spot.json`, `RESEARCH.md` (the
knowledge base: one section per item, headed with the item's exact name, with its link, read date, claim, verdict and
what was not verified; `pipeline.py check` refuses a section with no link or no read date) and the built page.
Frame before capturing: if the question is ambiguous in a way that changes the answer, ask once. Only a figure in the
spec is checked, so a figure the decision depends on must be in the spec. Anything that can't be sourced is left out,
not guessed. If a source is blocked, say so in the knowledge base rather than substituting a figure from memory.

## Files

- `spec.json`: the report. Its `policy` object sets the rules for this report (see below).
- `captures/` (the store, `scripts/capture_store.py`): one JSON file per ledger row. Each capture holds the value,
  the URL, the UTC time, the full raw page text and its SHA-256 (a capture without them, or whose text no longer matches
  its hash, is not evidence; for a figure, the figure's digits must appear in that text). Row ids carry the item's id or name,
  never its rank, so re-ranking keeps its captures. `captures/_review.json` records when `pipeline.py review` took the review: the
  second look must come after it. Nothing is refetched to re-check a spec edit: the gate reads the store.
- `review.json`: verdicts derived from fresh, usable captures (`pipeline.py review`). Never typed by hand: the gate ignores its rows and
  derives the verdicts from the store itself.
- `spot.plan.json`, `spot.task.json` and `spot.json`: the deterministic sample; the same rows as a task for the second-look agent (row, what, kind,
  source, with no values in it; see `briefs/second-look.md`); and the record of the second look.
- `golden.json`: regression facts for the project (what must appear, and what must never reappear).

## The pipeline (each step reads what the previous one wrote)

1. **Ledger.** `guard.py ledger spec.json ledger.json`. One row per checkable claim, with its source URL. Prose
   (verdicts, pros, watch-outs) is not in the ledger; it is covered by the banned-phrase gate and a read-through.
2. **Capture.** Store each figure with its raw excerpt: `pipeline.py capture PROJECT ROW URL VALUE --raw-file F`.
   Price, score and count need two captures from separate page loads. An unknown row is refused.
3. **Review.** `pipeline.py review PROJECT` (or `capture_store.py review STORE spec.json review.json`) derives
   `review.json` from fresh captures. A row with too few fresh captures is named, not guessed.
4. **Exclude, if needed.** A capture that is wrong for a claim is excluded with a reason
   (`pipeline.py exclude PROJECT ROW INDEX REASON`). It stays on record and is never used again.
5. **Spot check.** `pipeline.py sample` picks the sample: every critical row (a flight time, a booking amount or date) always, then 20% of the rows (at least 8) drawn from the ledger's other rows and their values as the gate reads them (trimmed, case and
   spacing folded), so editing text the gate ignores does not reroll it. Adding or removing a row does change the draw, which is why the critical rows never depend on it, and it writes `spot.plan.json` and `spot.task.json`. Spawn a fresh agent (opus, never
   the builder) with `briefs/second-look.md` and `spot.task.json`; it takes new captures for those rows from their sources and never opens `spec.json`,
   `review.json` or `captures/`. `pipeline.py spot` records the sample only when every sampled row has a second look: a usable capture taken after the
   review, not dated in the future, fresh, from a source the kind may cite, with the figure in the page text, and beyond the captures the review used.
   The gate then recomputes agreement from the store, second looks included.
6. **Gate.** `pipeline.py gate PROJECT` must print `GATE PASS`. `pipeline.py build` runs the same gate before it
   writes a page, so an unverified report cannot be built.
7. **Evals.** `guard.py evals spec.json golden.json` on every rebuild, even after a small edit.
8. **Self-test** (once per change to this skill): `guard.py selftest` must print `SELFTEST PASS`, and
   `pipeline.py selftest` must print `SELFTEST PASS`.

## The policy (in spec.json, under "policy")

The defaults describe a travel report. A spec may only make the gate **stricter**: every key below is tighten-only,
and a key that would loosen a rule, or an unknown key, fails the gate (`policy.…`) and is ignored.

- **Sources, by kind of claim.** Where a figure may come from depends on what it is. Sites are grouped into
  brand-neutral classes (`SOURCE_CLASSES` in `guard.py`), matched by domain suffix, with `name.*` for any country
  domain and an optional path (`google.*/maps`):

  | Class | What it is |
  |---|---|
  | `review_platform` | Google Maps, TripAdvisor, Yelp: anyone can review, nobody is paid for the rating |
  | `verified_ota` | Booking.com, Expedia/Hotels.com, Agoda, Airbnb, Vrbo (and their photo CDNs): reviews only from verified stays |
  | `community` | Reddit, X, FlyerTalk, TripAdvisor forums, Lonely Planet's forum, YouTube: candid, unverified |
  | `maps_measure` | Google Maps directions links (`/maps/dir`), OpenRouteService; never a Maps place page |
  | `flight_engine` | Google Flights, SerpApi, flightclaw (`mcp:flightclaw`) |
  | `airline` | none by default: the spec adds the airline flying its leg |
  | `rail` | national operators and rail aggregators |
  | `official` | government sites (`*.gov`, `*.gov.xx`); a spec adds a tourism authority's domain with `policy.source_classes` |
  | `own_site` | worked out per card: its own URL's domain plus any `own_domains` it declares |
  | `evidence` | a file in the project's `evidence/` folder (a booking confirmation, a ticket) |

  `KIND_SOURCES` says which classes each kind may cite. Ratings and review counts: review platforms and
  verified-stay sites only, **never the property's own site** (its testimonials are self-curated). Price: own
  site, booking sites, flight engines, airlines, rail, saved evidence. Availability: the same, without evidence.
  Amenities and hours: own site, booking sites, review platforms, official sites; policies: not review platforms.
  Distances: map measurements only. Durations (drive and walk times included): maps, flight engines, airlines, rail. A stop's clock
  time: own site, official, flight engine, airline, rail, evidence. Dates and booking amounts: own site, booking
  sites, airlines, rail, evidence. Points prices: the operator's own page, airline, rail, official, evidence. Add an operator's domain with `policy.source_classes`, e.g.
  `{"airline": ["airexample.com"]}`; only the airline, rail, official and flight_engine classes can be extended, with a full domain (never a bare suffix such as `com`), and what is
  added backs transport, plan, booking and cost rows but never a hotel card's own claims.
- `kind_sources`: may only remove classes from a kind (never add one).
- `banned`: lowercase phrases that must not appear in prose; a spec adds to the defaults.
- `label_kinds` (spec entries are tried after the defaults, so they classify only labels the defaults leave unclassified, and only into figure kinds, whose numbers are compared): ordered `[substring, kind]` pairs. The first substring found in a fact label gives the kind. "Rate"
  and "price" are checked as prices, so "Family room rate" is a price, not a room size.
- `required_sections`: dossier section titles every item must carry, or an explicit reason it could not be
  collected. Default: Map listing, Guest ratings, Guest reviews, Photos; a spec adds sections.
- `min_reviews_read`, `web_sources_min`: may only be raised. `review_methods`: may only be narrowed.
- `freshness_days`: may only tighten the defaults (see "Freshness").
- `critical_min_sources`: may only raise the default of 2.
- `policy_version`: a top-level key (not inside `policy`) recording which version of these rules a spec was built
  under. A mismatch is a WARN, never a FAIL: an older spec is worth a fresh look, not retroactively wrong.

## Freshness

Every capture carries its time (`at` or `captured_at`), and the gate refuses one older than its kind's window. Review
files are held to the score window through their `retrieved_at`.

| Kind | Window |
|---|---|
| price, availability, points | 14 days |
| score, count | 90 days |
| hours, policy, duration, time, date, booking | 180 days |
| distance, km, size, stars, photo, link, feature | 730 days |
| any other kind | 14 days |

## Critical facts and sourcing

Some facts a traveller acts on at a fixed moment, where an error costs a missed flight or a lost deposit:
- flight and train times and fares (air and rail transport legs);
- booking amounts and every booking date it shows (cancel-by, check-in, check-out, date);
- the time of any stop typed `flight`, `train`, `reservation`, `ticket` or `event`, or marked `fixed`;
- any fact marked `"critical": true`.

Each needs captures from at least two independent sources: two different sites, or a saved confirmation in
`evidence/` plus the site. Aim for three when they disagree or the stakes are high.

Everything else is a judgement, not a count. Corroborate in proportion to how much a fact matters and how likely it
is to be wrong:
- a hotel's star rating from its own site is fine;
- "quiet at night" needs reviews from more than one place;
- a claim that decides the ranking deserves more than one that colours it.

The facts reviewer judges whether the sourcing was enough.

## The rank-score formula (`score_basis.formula`)

Every card with a `score` names its formula, and the gate recomputes the number from its stored inputs. A typed
value fails. A missing or unknown formula fails.

- **`rank-score-v2`** (current): **guest score + contextual overlays**, clamped to 1-5.
  - **Guest score** (`score_basis.guest_score`, kept visible on its own): the weighted mean star rating of the
    reviews read on each counted source.
    - Verified-stay booking sites weigh 1.5 and review platforms 1.0. The property's own site and community
      posts never count.
    - A source counts once at least `min_reviews_read` (20) of its reviews were read.
    - At least 2 sources must count, or the missing one is declared `{"name", "unavailable": "<reason>"}`.
  - **Overlays** (`score_basis.group_fit`) say how right or wrong a place is for this group and trip. The
    `fit-scoring` skill's dedicated scorer proposes them and `apply_overlays.py` writes them. Each entry is
    `{signal, criterion, bump, fact | concern, reason}`.
    - `criterion` is an id from `spec.fit_criteria` (the fit profile the user agreed in trip-intake, copied by
      `trip_intake.py sync`: id, weight, polarity, evidence), a confirmed `group_traits` entry (older specs), or an
      open `universal:<quality>`.
    - `bump` is signed: + for a quality, - for a drawback, matching the criterion's polarity. It is at most the
      criterion's weight and never more than 0.5. A card's overlays together are capped at ±1.0: the rank score adds no more than that.
    - `fact` points at exactly one fact on the card: `{"facts": j}` or `{"dossier": "<section>", "index": i}`.
      That fact becomes a ledger row and needs its own capture.
      - A section title is not a fact.
      - A fact that says the thing is absent ("No pool") can't earn a + overlay.
      - A criterion with `evidence: "maps"` (walkability, nearness) must point at a Google Maps measurement.
    - A drawback may instead point at a `concern` on the card; concerns are corroborated by the overlap rule below.
    - `reason` is required.
  - `score` = `clamp(round(guest_score + clamp(sum of bumps, -1, 1), 1), 1, 5)`. With no overlays it equals the
    guest score.
  - The code checks structure. Whether a fact really delivers what the criterion wants, and whether the size is
    fair, is the judgement reviewer's job (`claim-review/briefs/judgement.md`).

There is one formula, `rank-score-v2`. A spec that names another (`equal-weight-mean-stars`, `rank-score-v1`) fails and is re-scored under v2.

## Gates (all fail closed)

| Gate | Fails when |
|---|---|
| Stale review | `review.json` was made against a different spec hash. Any spec edit needs a new review. |
| Missing verdict | a ledger row has no verdict. |
| Non-confirmed | any verdict other than `CONFIRMED`. Fix the spec or the claim, then review again. |
| Single-sourced | a price, score or count has fewer than two captures. A link, photo or size needs one. |
| Critical sources | a critical fact's captures come from fewer than two independent sources. |
| Stale capture | a capture has no time, or is older than its kind's freshness window. |
| Figure mismatch | any capture's figure differs from the spec figure. Compared by the number in the value (price, score, size, stars, km, durations, distance metres and minutes), by `HH:MM` for times, by date for dates, and as normalised text for hours and policies. Counts may differ by 1%; ratings may not. |
| Availability | a capture says "available" where the spec says "unavailable", or the reverse. |
| Wrong source | a claim's declared source, or any of its captures, isn't a class its kind may cite (a rating from the property's own site, a distance not measured on a map, a price "confirmed" by two blogs). `javascript:` and `data:` never pass. A saved confirmation counts only from inside `evidence/`. |
| Policy | the spec's own `policy` loosens a rule or names an unknown key. |
| Photo | a photo's capture did not answer HTTP 200. |
| Unclassified fact | a fact with a number has a label no `label_kinds` entry classifies. Label it so it can be checked. |
| Rail | a fact labelled "rail" has no Google Maps measurement matching it (when `rail.json` is supplied, keyed by card `id`). |
| Dossier | a required section is missing, or empty without a reason. |
| Guest score | no `score_basis` read from reviews; a review file that repeats a review, or one file listed as two sources; a review file without provenance (method, retrieved_at), with a method the policy doesn't allow, stale, or edited after reading (text hash); the score isn't what the formula recomputes; themes cite reviews that weren't read or skip a counted source; no web research with a read date. |
| Ranking overlay | see the formula above: criterion, reason, size and sign within the criterion's weight (summed over all of that criterion's overlays), one overlay per fact, fact pointer (captured, not a negation, a map measurement where required) or a concern for a drawback. |
| Overindexing | a concern, or a review-derived watch-out, isn't corroborated (see below). |
| Door to door | an air or rail mode's time isn't the sum of its getting-there, main-journey and getting-away segments. |
| Plan | a day outside `spec.trip` or out of order; a stop timed before the one ahead of it, or sooner than the transfer before it allows; a change of city with no leg; a booking outside the trip; two stays on one night; a plan with no `spec.trip`. |
| Bookings | the summary doesn't match the sum per currency; an "upcoming" booking past its cancel-by date, or with a cancel-by and no `as_of`; a cancel-by after the check-in; a date that isn't ISO. |
| Intake | `spec.group_traits`, `spec.fit_criteria` or `spec.trip` differ from `trip_intake.json` beside the spec (for a flexible trip, the window's outer limits); or the spec carries traits, a fit profile or overlays and there is no `trip_intake.json`. |
| Table drift | a table row differs from its card on any figure the card states (price or cost, score, drive, a labelled fact) or on rank. A column the card doesn't state becomes its own ledger row, needing a capture. |
| Banned phrase | a banned phrase appears anywhere the page shows text: card prose, fact values, badges, table cells, KPI labels, eyebrow, footer, the design's brand and ethos, booking notes, recommendation reasons. Links, ids, dates and the policy are not scanned. |
| Spot disagreement | a sampled row's stored captures disagree with the spec. A disagreement is a finding and fails until the spec is fixed, or the wrong capture is excluded with a reason (`pipeline.py exclude`) and new captures are taken. Typing a retest reason anywhere does not clear it. |
| Spot check | there is none; or a sampled row has no extra capture taken after the recorded review time; or that capture is future-dated, stale, from a source the kind may not cite, or disagrees with the spec; or the recorded review time is missing or unreadable; or it names rows outside the ledger; or its rows are not exactly the gate's sample (20% of the rows, at least 8, drawn from the ledger's contents as the gate reads them, so a builder can't pick them). |

## The overindexing rule

A concern is surfaced only when it isn't one guest's isolated complaint. It applies to every entry in
`score_basis.concerns` (`[{text, review_ids}]`). It also applies to every review-derived watch-out on a card
(`watch: [{text, review_ids}]`). Each must clear one of:
- **two independent sources** cite it. Any review platform, verified-stay site or community source with a stored,
  provenanced review file counts (Google plus a Reddit thread, say), even one mention each;
- **at least 20 reviews were read, and at least 15% of them cite it.** Below 20 this branch doesn't exist, so a
  small read set can't turn one reviewer's repeated complaint into a pattern.

The property's own site never corroborates. A watch-out that clears neither may stay if it's marked
`"isolated": true`; the page then labels it "(one report)". A plain-string watch-out is a practical caveat (steep
stairs, no lift), not a review complaint. The judgement reviewer checks that no complaint is hiding in one, and that
nothing several sources raise was left out.

## Rules for the people and agents using it

- **Never write a verdict you did not capture.** A reviewer who did not load the page twice cannot mark a price
  CONFIRMED.
- **Never silence a gate by editing the review.** If the gate fails, fix the spec, the claim or the source, then
  get a new review. Editing `review.json` by hand is a defect.
- **A changed spec invalidates the review and the built page.** `pipeline.py check` names both.
- **Do not refetch to re-check.** Edit the spec and run the offline gate. Re-capture only the rows `status` names as
  missing or stale. A figure older than its freshness window (see "Freshness") must be captured again.
- **A guest score is a reading, not a rating.** Read the reviews on each source, store them with their text hashes, write the themes from them, and only then give the score. Nothing else is a guest score.
- **Sampling is a safety net, not the check.** The sample detects systematic error. Full coverage comes from the
  review of every row.
- **A spot disagreement is a finding, not noise.** One disagreement forces a full retest. Record the reason; do not
  argue the sample away.
- **Occupancy and scope matter.** A capture taken for a different group, date or product is excluded with that reason
  (a 3-person search read as a family-of-three search, say, when the family's child changes the room type).
- **Evals are project-specific.** The facts you were wrong about once stay in `golden.json` as forbidden values.
- **Points, policies and rules** must be quoted from the operator's own rule page, with the URL. A blog or forum is
  not a source for a rule.

## Golden evals

`golden.json` has two lists:
- `expect`: `{item, field, value}` rows that must match the spec (for example, the verified lowest member rate).
- `forbid`: `{text, why}` strings that must not appear anywhere in the spec (a wrong ID, a retracted price, a
  banned claim). `why` says which error the entry prevents.

## What this skill does not cover

- It does not judge taste, whether a bump is deserved, or prose quality. Those are for the review panel
  (`claim-review`: the judgement reviewer and the cold reader).
- It cannot see a page that changed after the captures. Capture again before publishing anything older than its
  freshness limit.
- Whether a source is credible, and whether the page is the right page for the claim, are judgements. The gate
  checks that the figure matches; the reviewer checks that it means what the claim says.

## Done when

- For a project: `pipeline.py gate PROJECT` prints `GATE PASS`, `pipeline.py check PROJECT` is clean, and, if the project has a
  `golden.json`, `guard.py evals spec.json golden.json` passes.
- `guard.py selftest` prints `SELFTEST PASS`. It plants each class of defect and proves the gate rejects it.
- `pipeline.py selftest` prints `SELFTEST PASS`. It runs the whole pipeline offline on the bundled example and
  checks that the refusals hold: a single capture, a source of the wrong class, a banned word, a stale report, a KB gap, a spot check with no second look.
- `guard.py selftest` includes a gate run on a spec with no verdicts, which fails and names every row (the fail-closed default).
