---
name: review-synthesis
description: Gets review text from at least two independent sources, then runs parallel synthesis agents and an independent check to produce the themes and concerns (each citing review ids) that fact-guard's guest score and overindexing rule are built on. Use after place-matching and before report-builder to read many properties against each other; for one venue mid-research use venue-verification.
user-invocable: true
---

# Review synthesis

Score places from the review text, not the headline rating: 5.0 from 3 reviews differs from 4.9 from 200, and only the
text shows whether a complaint is a pattern or an outlier. **Nobody types a score here.** The guest score is computed
by fact-guard from the stored review files (verified-stay sites weigh 1.5, review platforms 1.0). A guest score needs two
counted sources (20 or more rated reviews each), or one plus a second source declared unavailable with its reason (`stay_spec.py` declares the
listing-review source unavailable on its own for a rental row that carries browser-read listing reviews (those feed themes and quotes, never the score); a hotel has no such row, so it needs two counted sources); without that the place has no guest score and no fit ranking.
This skill produces the reading behind it: themes and concerns that cite the reviews they come from.

**Inputs:** `merged.json` (listing-search), the place matches, `report_data.json` (from `report-builder/scripts/stay_rows.py`). **Hotels** have no `merged.json`,
`detail_parsed.json` or `review_corpus.json`: skip step 3, fetch Google reviews with `scripts/serpapi_reviews.py fetch <the place_id in google.json under h<propertyId>> reviews/h<propertyId>.serpapi.json`,
add the second platform with `raw_reviews_to_evidence.py --id h<propertyId>`, and run steps 2 and 4 to 6 on the `h<propertyId>` ids.
**Outputs:** `reviews/<row id>.<source>.json` review files, `review_corpus.json`, `synthesis/<row id>.json`, `themes.json`.

## Steps

1. **Get review text from two sources.** The Google Places API returns about 5 reviews per call (about 10 with two calls);
   that is a platform limit, and scraping Google Maps' web UI is off the table. Use a licensed provider the user
   authorised, and keep each platform in its own file (two files from one platform count as one source, and a review
   copied between files counts once):
   - **Google, one source per place.** Either `scripts/serpapi_reviews.py fetch PLACE_ID reviews/<row id>.serpapi.json` (Google Maps via SerpApi), or
     Apify's Google reviews: `scripts/apify_reviews.py --max-places N --max-reviews M` (both required: the volume the user approved; it runs the Google Maps
     reviews actor only, picks the top places from `review_corpus.json` (run step 3 once first, then this, then step 3 again), saves each raw download under
     `raw/apify-<row id>.json` and writes `apify_reviews.json`; step 2's `stay_spec.py reviews` turns that into `reviews/<row id>.google.json`, so Google needs no converter).
     SerpApi and Apify are both Google: use one of them, not both.
   - **A second platform (Booking.com, TripAdvisor or Airbnb).** No script in this plugin fetches these. Tell the user the actor and the estimated
     review volume, get their go-ahead, run the platform's reviews actor with the provider they authorised, save the raw download under `raw/`, then
     `scripts/raw_reviews_to_evidence.py --raw raw/<file> --id <row id> --method apify-booking-reviews|apify-tripadvisor-reviews|apify-airbnb-reviews --source-url <the property's page on that site> --out reviews/<row id>.booking.json`
     (the converter reads each platform's field names, keeps Booking's half-star scores, and refuses a download where no review maps). A verified-stay site is the usual second source.
   - `scripts/google_reviews10.py` is the free Places path: below the minimum for a score, still usable for themes.
   A provider file carries `method`, `retrieved_at`, `source_url` and reviews with ids and text hashes; `stay_spec.py`
   picks up every `reviews/<row id>.*.json` as a source. The property's own site never counts.
2. **List the files the agents will cite:** `python "<plugin root>/skills/report-builder/scripts/stay_spec.py" reviews PROJECT`
   writes the Google and listing review files from the inputs and prints each file's review ids.
3. **Build the corpus:** `scripts/build_corpus.py --merged merged.json --parsed detail_parsed.json --top N --currency CUR [--gr10 gr10.json] [--apify apify_reviews.json]`
   (with `scripts/review_analysis.py`; add `--reviews reviews_raw.json` only if you saved each listing's full review page as {id: text}). It deduplicates repeated reviews, drops host replies, and computes recency (last
   12 months against all time).
4. **Spawn 2 to 3 synthesis agents**, each on a disjoint slice, with this rule set in the prompt verbatim:
   - Cite review ids from the `reviews/<row id>.*.json` files for every theme and concern; an id that was not read is
     refused later.
   - A concern is **recurring** only if two independent sources raise it (a review platform, a verified-stay site, or
     a community thread whose posts are stored), or if at least 20 reviews were read and at least 15% raise it, with
     at least one mention in the last ~12 months (a recency condition this skill adds; the gate does not check it). Anything less is
     **isolated**: it goes in the `isolated` list and is shown as a watch-out labelled one report; it moves no verdict or score. The
     rest is fact-guard's overindexing rule.
   - Same-day bursts of near-identical reviews, templated or promotional reviews, and reviews naming a different
     property (common in shared complexes) are excluded from concern counts and noted as a data-integrity flag.
   - Recency weighs roughly double for the last 12 months and half beyond ~2 years.
   - Write `synthesis/<row id>.json`: `{"themes": [{"text", "sentiment", "review_ids"}], "concerns": [{"text", "review_ids"}],
     "isolated": [{"text", "review_ids"}], "pros": ["..."], "verdict": "...", "web": [{"name", "url", "read_at"}], "evidence_quality": "..."}`.
     Themes are what guests say (pool, beds, pets, cleanliness, noise), each citing at least one review from every counted source; `concerns`
     hold only recurring complaints and a complaint raised once goes in `isolated` (shown labelled as one report); `pros` are up to three short strengths
     (14 words each); `verdict` is one sentence (28 words) saying why this place for this group, following from the themes. `stay_spec.py` takes the card's
     pros, verdict and one-report watch-outs from these fields, so a row without them ships with an empty verdict. `web` is at least one web
     research source (an https url and the date read). No score, no score adjustment.
5. **Independent check.** Spawn 2 to 3 fresh agents on disjoint slices. Give them only the raw evidence (review files,
   listing text) and not the synthesis. Each lists the themes and concerns it finds, each with review ids. Compare the
   two sets: a concern only one side found, or a theme the other contradicts, is re-read against the raw reviews by
   you and the synthesis corrected. Say what you did in the method section (`report-review` checks the description).
6. **Collect:** `stay_spec.py themes PROJECT` merges `synthesis/*.json` into `themes.json` and refuses a theme or concern
   citing a review that was not read, a row with no themes, or one with no web source. Then `stay_spec.py build` adds the
   score basis, and `fit-scoring` adds contextual overlays.

## Gotchas

- **An agent told to "verify an earlier conclusion" is not blind.** Feed the checkers the raw evidence only.
- **Evidence depth scales confidence, not only the score.** Ten properties with 2 to 3 short reviews each do not merit
  the confidence one property with 200 detailed reviews does; say so, and the card shows no guest score until two
  sources reach the minimum.
- **One dramatic review (a payment dispute, an extreme claim everything else contradicts) is an outlier**, which the
  recurring-versus-isolated rule keeps from setting a reputation.
- **Reviews of a different property** (a Google rating shared across a small complex of similar names) must be excluded
  before counting.

## Done when

- Every property that shows a guest score has two counted sources with at least 20 rated reviews each, or one plus a declared, reasoned
  unavailable (the gate checks).
- Every row has a verdict and pros, and its isolated list is filled where a complaint failed the rule.
- Every recurring concern meets the rule above and has a recent mention; re-derive a sample by hand.
- Every concern that does not meet it is labelled isolated and drives no verdict.
- Same-day bursts, templated text and cross-property contamination were checked (grep the raw corpus for date
  clustering and other property names).
- `stay_spec.py themes` wrote `themes.json` with no refusals.
