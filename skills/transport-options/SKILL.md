---
name: transport-options
description: "Compare air, road and rail for one leg door to door from captured times and fares, and recommend one. Use for any leg between places, or when asked how to get there."
user-invocable: true
---

# Transport options

One leg: an origin, a destination, a date window and a group. Compare the modes that are viable for this leg and
recommend one with reasons a reader can check. Never assert "fly" or "drive" from habit, and never force a mode that
doesn't apply (a road between two islands, rail where no line exists).

**Compare door to door.** A 70-minute flight with an hour to the airport and 45 minutes from it is a 175-minute
journey, before check-in time. Air and rail are always the sum of three captured segments: getting there, the
main journey, getting away. The gate refuses an air or rail time that isn't.

## Inputs

Read `trip_intake.json` (group, dates, `itinerary.mode_order`); every agent writes it first. Never re-ask what it holds.

## Capturing each mode (skip a mode outright if it genuinely doesn't apply)

All commands are `python "<plugin root>/skills/transport-options/scripts/compare_modes.py" ...`, run from the
project folder.

1. **Road.** `drive ORIGIN DESTINATION road.json --depart 2030-03-01T08:00+01:00 [--public-origin "<area>"] [--public-destination "<area>"]` (for a leg that starts or ends at home, `--public-origin` / `--public-destination` keep the street address out of the stored label and source link). The departure is the trip's
   own date and time, with its UTC offset, so traffic is estimated for when they'll actually drive; a past time
   is refused. Needs `GOOGLE_MAPS_API_KEY`. For a multi-stop road leg, or endpoints that aren't simple
   addresses, use `tools/place_distance.py`.
2. **Air.**
   - `air FROM TO DATE flights.json --adults 2 --children 1 --currency EUR` (IATA codes) lists the best options
     from SerpApi's Google Flights engine: flight numbers, departure and arrival times, stops, fare. Needs
     `SERPAPI_API_KEY` (setup: `environment-setup`, "SerpApi, step by step").
   - For date flexibility, cash versus miles and the round-trip-total trap, use `flight-research`.
   - **A flight's time and fare are critical facts**: before they go in the report, capture them a second time
     from the airline's own site or flightclaw (`mcp:flightclaw` as the source).
   - Capture the airport runs with `drive` (home to airport, airport to destination). Then run
     `assemble air.json --mode air --main flights.json --pick 0 --access to_airport.json --egress from_airport.json`.
3. **Rail.** There's no rail API here.
   - Read the fare and timetable on the operator's own page (or a rail aggregator), save the relevant excerpt,
     and record it: `rail train.json --label "IC 123" --time-minutes 195 --cost "EUR 49" --source <operator URL> --raw-file excerpt.txt`.
     A source that isn't a rail or official site is refused.
   - Capture the station transfers with `drive` (or walking time from Maps), then run `assemble --mode rail`.
   - Rail times and fares are critical too: confirm them on a second source (an aggregator against the operator).

## Output: a comparison, with a recommendation and stated reasons

The comparison goes into the report spec as a `transport_leg` block (report-builder renders it, fact-guard checks
every figure). `compare_modes.py validate comparison.json` runs the same checks on a standalone comparison:

```json
{"legs": [{"label": "Town A to Town B", "from": "Town A", "to": "Town B", "modes": [
  {"mode": "road", "time_minutes": 300, "distance_km": 340, "source": "https://www.google.com/maps/dir/...", "captured_at": "2030-01-10T00:00:00Z"},
  {"mode": "air", "time_minutes": 175, "cost": 90, "currency": "EUR", "fare_basis": "one_way", "passengers": 1, "source": "https://www.google.com/travel/flights?...", "captured_at": "2030-01-10T00:00:00Z",
   "segments": [
     {"label": "to airport", "time_minutes": 60, "source": "https://www.google.com/maps/dir/...", "captured_at": "2030-01-10T00:00:00Z"},
     {"label": "XY 123", "time_minutes": 70, "main": true, "source": "https://www.google.com/travel/flights?...", "captured_at": "2030-01-10T00:00:00Z"},
     {"label": "from airport", "time_minutes": 45, "source": "https://www.google.com/maps/dir/...", "captured_at": "2030-01-10T00:00:00Z"}]},
  {"mode": "rail", "note": "no line between these towns"}
], "recommendation": {"mode": "road", "reasons": ["under an hour slower door to door than flying, no airport queues with luggage, half the cost"]}}]}
```

`from` and `to` let fact-guard's plan check confirm that a day-plan's change of city has a leg behind it.

Group needs (a nap window, luggage, motion sickness, mobility) belong in `reasons`, stated plainly and tied to
what the group said, never invented to justify a mode chosen for other reasons. A leg usually has one clear winner,
so legs aren't run through the rank-score formula.

## Fail-closed rules

- **Never state a time, distance or cost with no capture behind it.** Every figure carries `source` and
  `captured_at`, every segment too. `validate` checks this; fact-guard's gate checks it again in the report.
- **A mode that doesn't apply is marked `{"mode": ..., "note": "..."}` with no figures**, never forced.
- **An API error is never "no result".** `drive` and `air` stop and write nothing when Google or SerpApi refuse
  the request; "no flights found" is reported only when the search itself succeeded.

## Done when

- `compare_modes.py validate` passes; in a report, `pipeline.py gate PROJECT` prints `GATE PASS`.
- Air and rail are door to door; their main journey has two independent sources.
- A mode that doesn't apply says so explicitly.
- The recommendation's reasons let a reader see why this mode won for this group.
- `compare_modes.py selftest` prints `SELFTEST PASS`.
