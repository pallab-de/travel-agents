---
name: flight-research
description: "Flight fares and points redemptions for a route from captured sources (SerpApi Google Flights, flightclaw, the airline site). Use for any flight price; never quote one from memory."
user-invocable: true
---

# Flight research

A flight time or fare is a **critical fact**: a traveller acts on it at a fixed moment, so `fact-guard` needs it from two
independent sources, each captured with its source and time. The first source is SerpApi's Google Flights engine; the second is
flightclaw (`mcp:flightclaw`) or the airline's own site.

## Sources

1. **SerpApi / Google Flights** (first source). One direction at a time:
   `python "<plugin root>/skills/transport-options/scripts/compare_modes.py" air FROM TO DATE out.json --adults N --children N --currency CUR`.
   Each option keeps its flight numbers, times, stops and fare, with the currency, the fare basis (`one_way`) and the party size. Needs
   `SERPAPI_API_KEY` (see `environment-setup`).
2. **flightclaw MCP** (second source). `claude mcp add flightclaw` needs a server command or URL: follow `environment-setup`'s
   "flightclaw, step by step", which covers the hosted option (signs in through OAuth) and the local one. Tools:
   - `search_dates`: a date-grid price scan; use it first to find the cheapest days.
   - `search_flights`: a specific date pair, with `stops`, `airlines`, `sort_by`, and the passenger params below. It returns Outbound,
     Return and Total as separate figures; **the Total is the round-trip price, not either leg alone.**
   - `track_flight`, `check_prices`, `list_tracked`: price tracking.
   A newly added server doesn't hot-load into a running session: `/mcp` reconnect or restart before assuming it needs reinstalling.
3. **The airline's own site**, read with WebFetch or the browser, when flightclaw isn't available. A search result is a live quote,
   not a bookable cart total: say so wherever the figure lands.

Capture each figure with `fact-guard/scripts/pipeline.py capture` (the URL, or `mcp:flightclaw`, and the value as shown).

## If a search returns "No flights found"

A broken search library returns "No flights found" for every route, including the busiest, and it looks like a real empty result.
Run a **control search on a known busy trunk route** for the same dates. If the control is also empty, the tool is broken: treat the
result as a failure, never as "no flights". For flightclaw the usual cause is an out-of-date `flights` library: upgrade it inside
flightclaw's venv (`<that venv's python> -m pip install --upgrade flights`; `.venv\Scripts\python.exe` on Windows, `.venv/bin/python`
elsewhere), then reconnect the MCP session. If the released version still fails, install a **reviewed commit** of `punitarani/fli`
(`git+https://github.com/punitarani/fli.git@<commit-sha>`; read what the commit changes first, and never use an unpinned branch, which
runs whatever lands next with the user's privileges). flightclaw's search needs no credentials; only its profile and booking tools do.

## Methodology

1. **Scan the date grid first** (`search_dates`), not one guessed date pair: fares vary by 10-20% or more by day of week. Note the
   pattern, so the reader can shift dates within their own constraints.
2. **Before writing "X is the cheapest", check the alternatives**: other airlines on the same city pair and nearby airports, on the
   identical date pair, sorted by price. Name what was checked.
3. **Compute every round-trip or itinerary total yourself** from the outbound and return figures, whatever a summary line claims.
   Never double an old figure to "correct" it: re-search.
4. **Passengers**: ask each child's age, and for a child under 2 whether they sit on a lap or in their own seat. Use
   `infants_on_lap` or `infants_in_seat`, not `children`, for those (`transport-options`' `compare_modes.py air` takes
   `--infants-on-lap N` and `--infants-in-seat N` and passes them to SerpApi). Run the search with the real passenger params and back the
   per-person split out of the party total. A rail operator's "under 5 free" and an airline's child discount are different facts.
5. **Cash and points, both, when the traveller holds points**: look up the actual transfer ratio from their own country's version of
   the card programme to the airline alliance, then the redemption cost on the airline's own award finder for the specific route (an
   example route on the same airline is not a stand-in: awards are priced by distance band). Say plainly if the route's redemption
   cost is unconfirmed. flightclaw stores a points balance but searches no awards; for a real award search, seats.aero has a Partner
   API (the traveller's own account and key; don't sign up on their behalf). On a route with no useful full-service option, skip to cash.

## Gotchas

- `search_flights` with `stops=ONE_STOP` and an `airlines` filter can throw a server-side error in some flightclaw builds. Retry with
  only `stops`; don't conclude the airline has no one-stop option.
- For a date-range sweep without the heavy date-grid UI, navigate to
  `google.com/travel/flights/search?q=Flights%20from%20<CITY>%20to%20<CITY>%20on%20<YYYY-MM-DD>%20through%20<YYYY-MM-DD>` and read
  `[aria-label*="round trip total"]` with `browser_evaluate`: far cheaper than a full snapshot per date pair.

## Done when

- Every flight price in the document traces to a captured search or page, never a remembered or estimated figure, and each flight
  time and fare has two independent sources.
- Any "cheapest" claim names the alternatives that were checked and their prices.
- Round-trip and itinerary totals are explicit sums (outbound + return, or per person times travellers).
- Child and infant fares come from a search with the right passenger params, not an assumed discount.
- A points comparison states the transfer ratio used (with its source) and flags a redemption cost that is still unconfirmed.
