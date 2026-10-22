---
name: place-matching
description: Matches a rental listing (name plus approximate coordinates) to its real Google Maps place with no manual per-listing overrides. Use for every candidate from listing-search before scoring, and re-validate on any new city before trusting a match.
user-invocable: true
---

# Google-place matching

A wrong match attaches one property's rating to another business, or leaves a real, well-reviewed place looking
unreviewed. `scripts/place_match.py` avoids both: it never hard-codes a city's place names, and it abstains rather than
guesses. **Never fix a wrong match by writing the correct place ID into a data file.** A wrong match means the matcher's
logic is wrong for a class of listings: fix the logic, using that listing as the test case, then re-run the regression
test below.

## How it decides (the exact algorithm is in `scripts/place_match.py`'s docstring)

Generic lodging words (villa, farmhouse, apartment, in several languages) and bed-count tokens (3bhk, 2br) are stripped
from the listing name and each Google candidate's name. The rest is scored by overlap, weighted so a word shared by many
candidates in the search counts for less than one only this candidate has. A word in a candidate's Google address but
not in its name is a place name, not part of the business name. A candidate is accepted only above a similarity
threshold and only if its Google place type is lodging-like; otherwise `match` returns `None` and the report shows a
name-search link instead of a pin.

## Steps

1. `place_match.py` is a library with no command line. Import it and call `pm.match(name, [lat, lng])` per candidate:
   ```python
   import json, sys; sys.path.insert(0, '<plugin root>/skills/place-matching/scripts')
   import place_match as pm
   candidates = json.load(open('cands2_drive.json', encoding='utf-8'))   # {id: record} with name and ll (rentals; hotels are matched by hotel-dossier's google_places.py instead)
   CITY_LL = [0.0, 0.0]   # set to the [lat, lng] of the city centre
   # a candidate with no coordinates is matched against the city anchor with a wider max_km
   out = {i: pm.match(v['name'], v['ll'] or CITY_LL, max_km=3.0 if v['ll'] else 25.0) for i, v in candidates.items()}
   pm.mark_shared_places(m for m in out.values() if m)   # one Google place claimed by two listings is a shared complex: demoted to cluster
   pm.save_cache()   # writes gp_cache.json in the calling project's folder, so repeat calls in the same city are free
   json.dump(out, open('places4.json', 'w', encoding='utf-8'), ensure_ascii=False)   # {id: match or null}: merge.py and the review scripts read this file
   ```
2. **On a new city or platform, run `scripts/live_match_check.py [seed]` first.** It builds ground truth live from
   Google (real lodgings in a dozen cities, given a fake Airbnb-style title and jittered coordinates) and checks the
   matcher returns the same place id, plus negative cases (a listing titled like one lodging, pinned at a different
   one) that must not match. Exit 1 on any wrong match. Require 0 wrong matches; a few correct abstains are expected,
   because a missed match is safer than a wrong one.
3. If the user supplied known-correct place IDs, check them with `scripts/validate_places.py`, which decodes a `ChIJ...`
   id into its `0x...:0x...` feature id for comparison with what Google Maps shows.
4. For a listing the matcher abstains on, the report shows a Google name-search link, and its flag says "no *confident*
   Google match", never "no Google listing": abstaining is not the place not existing.
5. **Run `scripts/audit_abstains.py <rows>.json --pins jittered` (Airbnb's moved pins) or `--pins exact` (a direct-booking site's own) on every matched row set** (the platform's `merged.json` and, if there are direct-booking leads, `mb_final.json`; rows need `name`, `ll`, `g`). For every unmatched row it asks Google
   what lodging sits within 150 m (exact pins) or 2 km (jittered pins) of the pin and lists those whose Google name is related to the listing's (a shared
   distinctive word, or a similar spelling). The matcher's worst failure is silent: it once abstained on "Valmora
   Springs", which Google lists as "Valmora Exotics" 30 m from the pin with hundreds of reviews. Decide each pair as
   same or different and record the reason in a decisions file (`--decisions`). The exit code is 0 only when every strong
   pair is decided `different`; rows with no coordinates need `--allow-no-ll`. For SAME, fix `place_match.py`, then
   re-run the whole matching stage (for the direct-booking list, `direct_booking_pipeline.py --rematch-only`) and
   everything after it.

## Source-aware matching

`match(name, ll, max_km=3.0)`: the 3 km default suits Airbnb, which jitters pins by 1 to 2 km on purpose. **A source
with exact coordinates (a direct-booking site's own pin) must pass a tight `max_km` (about 0.5):** at 2 km or more a
generic one-word name is a different business ("Kestrel Farm Stay" matched "The Kestrel House" 2.4 km away). A property
listed on Google under a slightly different name is matched by two paths, both only when the pin is within 150 m of the
place, the place is lodging-typed, and it is the only lodging that close: a shared distinctive word ("Valmora Springs" /
"Valmora Exotics"), or one word spelled differently ("Aro" / "Arro"). These return `renamed=True`; the rating is used,
and the report must say Google's name differs. After matching a batch call `mark_shared_places()`: a Google place
claimed by two listings is a shared complex page and is demoted to `cluster=True` for both.

## Gotchas

- **A wrong match is much worse than a missed one.** Tune toward abstaining.
- **Branches of one brand near each other** are the classic false positive; the token weighting exists because
  "closest result" gets them wrong.
- **Source coordinates are approximate.** Do not tighten the radius so far that the real match falls outside it.
- **A short, generic name** (one common word) is where rarity weighting matters most; spot-check marginal matches.
- **A Google rating may belong to a wider complex or brand**, flagged `cluster`; surface that flag in the report
  instead of presenting a cluster rating as this unit's.

## Done when

- `live_match_check.py` ran this session with 0 wrong matches, including its renamed-at-exact-pin block (results
  shift with geography and the API, so a past pass does not count).
- Any user-supplied place IDs for this city pass `validate_places.py`.
- `audit_abstains.py` ran on the current matches and each related-name pair has a written same or different decision.
- No manual per-listing override exists anywhere in the pipeline; one is a matcher bug to fix.
- Every `cluster` flag is visible in the final report.
