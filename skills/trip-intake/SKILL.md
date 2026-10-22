---
name: trip-intake
description: "ALWAYS FIRST for any trip: list back what the user already stated, ask only the gaps, confirm the shape (stay, itinerary, mixed) and the fit profile. Writes trip_intake.json."
user-invocable: true
---

# Trip intake

The same three hotels rank differently for a history lover with one day in a walled town, a couple who wants an
intimate stay, and a family in winter. This skill captures that: the user talks once in their own words, then answers
quick multiple-choice questions only where something is missing or ambiguous. Every answer is theirs, never a silent
default. **Output:** a validated `trip_intake.json`.

## 1. One free-form question first

Ask a single open question and let them talk:

> "Tell me about this trip: who's going, where and when, what kind of trip it is, and what would make it great
> (or ruin it) for you."

Don't interrupt with a form. A rich answer can cover most of what follows. Keep their own words: they go into
`group_profile.notes` and into each fit criterion's `why`.

## 2. Extract, then ask only for the gaps, as multiple choice

Fill in what the free-form answer gave you, then list what's missing or unclear. Ask the gaps with
`AskUserQuestion`, up to 4 questions per call, each with 2-4 options plus the automatic "Other":
- put your best inference first, marked "(Recommended)";
- use multi-select where answers combine (interests, deal-breakers);
- never ask something the free-form answer already settled; echo it back for confirmation instead.

| Field | Ask when missing (typical options) |
|---|---|
| Shape | Stay / Itinerary across places / Both |
| Destination | `home_base` (where they start), `stay_area` (the city or area to stay in) and, for an itinerary, the ordered `route`. Free-form if unknown; multiple choice only to disambiguate ("the old town or the beach resorts?") |
| Dates | Exact dates (`start`, `end`) / A flexible window (`flexible: true`, `window` earliest, latest and nights) / Not decided (ask which month and how many nights, then record a window) |
| Group | Adults, children with ages, pets (each a short question; ages decide cribs, kids' clubs, fares) |
| Budget | Ranges suited to the destination and currency, total or per night |
| Trip nature | Unwind / Explore / Celebrate / Mixed; pace: relaxed / balanced / packed |
| Time in each place | Mostly at the stay / Out all day / Both. A day out all day makes location beat the hotel itself. |
| Splurge appetite | Value first / Splurge on one special stay / Splurge throughout |
| Stay style | Intimate and small / Grand and iconic / Design-led / Nature retreat / Lively (multi-select) |
| Season | From the dates; ask only how it should shape the stay (warm stays in winter, pools in summer) |
| Affinities | Ask whether anything personal draws them here: a book, a film, heritage, food, family roots |

Stop asking when the fit profile (next step) can be drafted with confidence. A short trip may need two follow-up
questions; a complex one, eight.

## 3. Agree the fit profile: what will move the ranking

From everything gathered, draft the criteria that should make a place rank higher or lower **for this trip**.
Each criterion has:
- **id**: snake_case;
- **why**: the user's own reason, quoted or close to it;
- **weight**: how much it can move a place's score, at most 0.5 on the 1-5 scale;
- **polarity**: `+` for a quality, `-` for a drawback;
- **evidence**: `maps` if it's about distance or walkability, else `fact`.

How it plays out (illustrations, not defaults):
- **A group that wants to unwind and stay in:** +lake, hill or forest view; +boating or things to do on site;
  +games room, heated pool or play area (they compensate for a plainer setting); -beside a highway.
  A place with these extras outranks a stylish, highly rated one that has none.
- **A bachelor group out to party:** +sound system, +pool, +DJ allowed; -strict noise rules.
- **A reader of a novel set in a hill town:** +a room inside its old quarter (heavy); a celebrated boutique
  stay outside it ranks lower *for them*.
- **One day in a city, out from morning to night:** +walkable to the sights (`maps`, heavy); a palace hotel's
  grandeur barely counts.
- **Someone who prefers intimate stays:** +small, personal property; -large resort.
- **Winter travel:** +warm or heated stays, +winter-only experiences; a place that shines in winter can outrank
  its summer favourite.

Show the draft as an `AskUserQuestion`:
1. A multi-select of the criteria to keep. Each option's description gives the why, the weight as
   low (0.15) / medium (0.3) / high (0.5), and the sign.
2. A question for anything to add, or any weight to change. Two criteria can carry the same weight; say which
   matters more if they ask.

Record the result as `fit_profile.criteria` and set `fit_profile.confirmed: true` only after they've answered.
`group_profile.traits` (older, unweighted) stays an empty list when a fit profile exists; set `group_profile.traits_confirmed: true` in the same step, because the validator wants it either way.

## 4. Credentials: check, don't ask

Run `environment-setup`'s `credential_status.py` (it reports which keys exist, never a value). Nothing set: run
that skill's credential walkthrough. Some set: mention only the missing ones.

## Output: trip_intake.json

Copy `trip_intake.template.json` (next to this file) into the project folder as `trip_intake.json` and fill it in.
Then run `python "<plugin root>/skills/trip-intake/scripts/trip_intake.py" validate trip_intake.json` (for an itinerary or mixed trip add `--stage intake`: the itinerary block is collected next, by `trip-constraints-intake`, and the plain `validate` must pass once that has run). It fails on any
blank, placeholder or unconfirmed answer, and words each failure as the question still to ask. Ask it as a
multiple-choice question; never fill a default.

When the report spec exists, `trip_intake.py sync trip_intake.json spec.json` copies the trip dates, traits and fit
criteria into it. fact-guard then refuses:
- a spec whose criteria or dates drift from what the user agreed;
- any ranking overlay that answers no agreed criterion and isn't an evidenced universal quality.

Every later skill reads this file instead of re-deriving answers from conversation.

## Handing off without re-asking

- **`shape: "stay"`** → `requirements-intake`, pre-filled from `trip_intake.json`; it asks only its stay-specific
  questions (the real-bed rule, hard filters, deliverable).
- **`shape: "itinerary"`** → `trip-constraints-intake`, same pre-fill; only its route, transit, meal and loyalty
  questions.
- **`shape: "mixed"`** → both, each pre-filled. Neither re-asks group, dates, budget or the fit profile.

## Gotchas

- **A rich opening message is still a set of assumptions.** Echo back what you understood and get confirmation
  before writing the file.
- **The fit profile is the step most likely to be skipped.** It has no hard-filter consequence, so it feels
  optional. It decides the ranking; ask it every time. "Nothing in particular" is a valid, recorded answer.
- **A criterion is a promise to look, not a bump.** "Wants a lake view" makes the fit scorer look for a captured
  lake-view fact for each place; it never invents one.

## Done when

- The run started with one free-form question; follow-ups were multiple choice and only for real gaps.
- `trip_intake.py validate` prints `INTAKE OK` (`--stage intake` for an itinerary or mixed trip, until trip-constraints-intake has added its block).
- The fit profile was shown to the user and confirmed (`fit_profile.confirmed: true`), with the user's own reason
  on every criterion.
- Group, dates, budget and the fit profile are never asked again by a later intake skill.
