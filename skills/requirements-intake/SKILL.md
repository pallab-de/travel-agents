---
name: requirements-intake
description: The stay extension to trip-intake. For a rental or hotel search it settles the hard filters and constraints trip_intake.json doesn't hold (drive-time cap, the real-bed rule, bedrooms, pool, pets, whether unavailable listings are dropped or kept and flagged, deliverable, paid-provider spend), asking only the gaps and writing a validated config.json. Use at the start of a staycation-finder run after trip-intake (or on its own, which opens with the same free-form question), and again whenever a requirement turns out to be undefined partway through.
user-invocable: true
---

# Requirements intake

`trip-intake` has already collected the group, dates, budget, fit profile and context. This skill adds what a stay
search needs on top, and fills `config.json`. A constraint decided implicitly, mid-task, becomes load-bearing and
produces a wrong ranking three stages later, so every value here is the user's own answer, never a default.

## How to ask

1. **If `trip_intake.json` exists**, load it and pre-fill group, dates and budget into `config.json`. Read back what
   you pre-filled; don't ask it again.
2. **If it doesn't, open with one free-form question** ("Tell me about this stay: who is going, when, where from,
   and what would make it work or ruin it"), extract what the answer settles, and carry on as below. (Or run
   `trip-intake` first, which does this and also agrees the fit profile.)
3. **Ask the remaining gaps with `AskUserQuestion`**: up to 4 per call, 2-4 options each, the best inference first
   and marked "(Recommended)", with the automatic free-text "Other". Use free text for an open value (the starting
   area, station or landmark) and for a list where each item carries its own quantity.
4. Do not proceed until `validate_config.py` passes (below).

## What to settle

1. **Home base**: the starting point as the report should show it, an area, a station or a landmark that Google Maps can find
   (never a street address: the report publishes it in each place's drive-time link, and drive times are measured from it), and the drive-time cap (soft or hard, one way, and
   whether a longer drive is acceptable "with a strong reason" and what counts as one). For a hotel stay in another
   city, the area to search (`trip_intake.json` `destination.stay_area`).
2. **Dates to search**, plus whether to search a wider range later (then unavailable-on-the-date listings are KEPT and
   flagged, not dropped) and any windows to avoid.
3. **Group details the intake left open**: children's ages (they change the real-bed rule and pool-safety relevance),
   pets (species, weight: pet fees and pool rules).
4. **Hard filters**: whole property or shared; minimum bedrooms; pool required (private only, or shared acceptable);
   pet-friendly required. For a **hotel** search the rental filters (pool, pets, bedrooms) are `n/a, user confirmed` unless the user wants
   them, and `hard_filters.hotel_rooms`, `hotel_guests_per_room` and `hotel_min_stars` are filled; for a rental search those three are `n/a, user confirmed`.
5. **The real-bed rule, in the user's own words**: how many REAL beds (not header bed-counts that include sofa beds
   and mattresses) for how many people, and whether children can share an adult's bed. Read the rule back and get it
   confirmed; don't turn it into your own arithmetic.
6. **Deliverable**: one designed HTML page built by `report-builder`, and where to save it. Confirm that is wanted.
7. **Paid providers**: whether a licensed review provider (beyond the 5 reviews per call (about 10 per place) the free Google
   Places API returns) is authorised. If so, show the estimated call volume and cost and get a yes to that number.
   Credentials themselves are not asked about: run `credential_status.py` (see `environment-setup`).

What makes one place rank above another is not asked here: it is the fit profile from `trip-intake`.

## Output

Copy `config.template.json` into the PROJECT's working folder as `config.json` and fill it from the answers. Every
later skill reads it instead of re-deriving anything. Every value starts null. Run
`python "<plugin root>/skills/requirements-intake/scripts/validate_config.py" config.json`: it exits non-zero while
any answer is null or empty, so no later stage runs on a default nobody chose. A question that doesn't apply is
answered `n/a, user confirmed`.

## Gotchas

- **Don't infer a "reasonable default" for the real-bed rule.** A headline rule such as "N adults need about half as many
  real beds" sounds obvious until the user's actual rule turns out to be different (for example, whether children may share an
  adult's bed, and whether any two adults may share), which is different arithmetic from "2 people per bed".
- **"Drop unavailable listings" and "keep and flag them" give different lists.** Ask; don't assume.
- **An undeclared pet fee is not unverified pet PERMISSION.** If the platform has a "pets allowed" filter, using it
  guarantees permission and the fee was simply not stated (say so). A platform with no such filter (a direct-booking
  site) needs its own evidence for permission.
- **"Beyond the platform" leads pass the same hard filters**, pet-friendliness especially: a lead is listed only if
  its OWN source confirms it (the platform's pet-friendly collection, a specific claim in the article it came from).

## Done when

- `validate_config.py` prints `config ok`.
- You can restate the real-bed rule in one sentence and the user has confirmed it, before any scoring.
- If a paid provider is involved, the user said yes to the estimated number of calls and the cost, not to the search
  in general.
