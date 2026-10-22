---
name: trip-constraints-intake
description: "Itineraries only: settle route order, what the group can handle, transit modes, budget shape and meal coverage beyond trip_intake.json. Use right after trip-intake."
user-invocable: true
---

# Trip constraints intake

Everything downstream (which places get researched, how legs are compared, what a good hotel is, how a meal slot is
filled) depends on a few constraints that are expensive to get wrong after research starts. `trip-intake` has already
collected the group, dates, budget, fit profile and context; read `trip_intake.json` first and **ask only what it
doesn't answer**. Never ask the same thing twice.

## How to ask

The same pattern as `trip-intake`:
1. **One free-form question first**, for what an itinerary needs that the intake didn't capture: "What has to be true
   about the route and the days for this trip to work, and what would ruin it?" Keep their words.
2. **Then multiple choice for the gaps**, with `AskUserQuestion`: up to 4 questions per call, 2-4 options each, the
   best inference first and marked "(Recommended)". Echo back anything the free-form answer already settled for
   confirmation instead of asking.
3. **Free text, never multiple choice, for a list where each item carries its own quantity** (item 9). A forced
   select can't hold "which programmes, and how many points in each" without losing the amounts.
4. Exploration stays open: "what should Tuesday afternoon look like" is a conversation, not a form. Reserve a forced
   choice for a real decision (book this or not).

## What to settle

1. **Route**: the places, the order, and why that order (which flights or trains it protects, what backtracking it
   avoids). Is the order fixed, or does it flex if a booking falls through?
2. **What this group can handle**: ages, mobility, dietary needs, pets, and what they can actually do compared with
   a stereotype. Ask for concrete precedent (has this person done something comparable?). The real constraints are
   usually narrower than the generic worry (heat and food hygiene, not stamina).
3. **A "stay accessible" rule**, if relevant: must remote or rural bases be ruled out (medical access, reliable
   food) in favour of urban bases with day trips? Or is remote lodging fine? Don't assume either way.
4. **Transit-mode order** for legs inside the trip (for example rail, then coach, then private car, then flight),
   and what would justify dropping to a lower tier for one leg.
5. **Budget shape**: a baseline for most nights, whether one or two splurge nights are earmarked, and whether any
   place is excluded from splurging (a bookend city where the room barely matters).
6. **Meal coverage**: does every meal slot need a preferred pick plus one or two alternates, with hours, rating and
   distance from wherever they will be at that hour? Should dishes available everywhere give way to what is
   hyper-regional to each stop?
7. **Verification standard**: say plainly that every checkable fact (hours, price, existence, distance, showtime)
   is verified with a tool before it is written (`venue-verification`). Almost everyone wants this; make it an
   explicit shared standard.
8. **Deliverable**: one itinerary spec built by `report-builder` into a single HTML page, versioned in git and
   rebuilt after every change. Confirm that is the expected format.
9. **Loyalty and payment resources**, if flights or hotels are a meaningful cost: which airline and hotel programmes
   and transferable-points programmes the traveller holds, and roughly how many points in each. Also whether they
   want cash fares, a points redemption, or a comparison of both. This changes which options are worth researching.
   Ask it as free text.

## Output

Write the answers into `trip_intake.json`'s `itinerary` block (`route_reason`, `group_limits`, `stay_accessible`,
`mode_order`, `budget_shape`, `meal_coverage`, `payment_preference`, `loyalty`) and run
`python "<plugin root>/skills/trip-intake/scripts/trip_intake.py" validate trip_intake.json`: it must pass before research
starts. Keep a short human-readable copy at the top of `RESEARCH.md`: fixed constraints, never trip content like dates and
prices, which live in the spec. `pipeline.py init PROJECT --shape itinerary` then starts a spec from the dates and route.

## Gotchas

- **A general principle is not a ruling on the case in front of you.** "If we reach the hotel at 2pm, we'd go out"
  applied to a specific late-arrival day it doesn't describe restructures a plan backwards. When a stated principle
  and a specific instance seem to conflict, ask which one governs.
- **Don't infer a "reasonable default"** for any item above from context. A guessed constraint that goes
  unquestioned for a few sessions becomes load-bearing, and unwinding it later costs more than asking once.
- **A user who prefers to brainstorm in prose gets to.** If they answer a multiple-choice question with a paragraph,
  take the paragraph; don't re-ask it as options.
