"""Shared SerpApi client (https://serpapi.com): Google Flights fares and schedules, and Google Maps reviews, read
through a licensed API instead of scraping the pages. Same rules as google_maps.py:

- Never loads a credential itself; every function takes `api_key`. Loading it is tools/travel_secrets.py's job
  (the key is SERPAPI_API_KEY in the encrypted env that environment-setup creates).
- Never prints, logs or puts a request URL in an error (SerpApi takes the key as a query parameter).
- Fails closed: any error SerpApi reports (bad key, quota used up, no such airport) raises SerpApiError rather than
  coming back as an empty result, so "no flights" is never confused with "the request failed".

Returns SerpApi's own parsed JSON; picking flights, building captures and scoring stay in the skill that owns them.
Stdlib only.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://serpapi.com/search.json"


class SerpApiError(RuntimeError):
    """SerpApi refused or failed the request. The message never contains the key or the URL."""


def get_json(params: dict, base: str = BASE) -> dict:
    url = base + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=40) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        # SerpApi puts the reason in a JSON body even on 4xx (401 bad key, 429 out of searches).
        try:
            reason = json.loads(e.read().decode()).get("error", "")
        except Exception:
            reason = ""
        raise SerpApiError(f"HTTP {e.code} {reason}".strip()) from None
    except urllib.error.URLError as e:
        raise SerpApiError(f"request failed: {e.reason}") from None
    except json.JSONDecodeError:
        raise SerpApiError("response was not JSON") from None
    if data.get("error"):
        raise SerpApiError(str(data["error"]))
    status = (data.get("search_metadata") or {}).get("status")
    if status and status != "Success":
        raise SerpApiError(f"search status {status!r}")
    return data


def flights(departure_id: str, arrival_id: str, outbound_date: str, api_key: str, return_date: str = None,
            adults: int = 1, children: int = 0, currency: str = "USD", travel_class: int = 1, hl: str = "en",
            infants_in_seat: int = 0, infants_on_lap: int = 0) -> dict:
    """Google Flights search. departure_id/arrival_id are IATA airport codes (or comma-separated lists of them).
    Dates are YYYY-MM-DD. One-way unless return_date is given. travel_class: 1 economy, 2 premium economy,
    3 business, 4 first. Results are in best_flights / other_flights; each has flights[] (the legs), layovers,
    total_duration (minutes) and price, and search_metadata.google_flights_url links the same search on Google."""
    params = {"engine": "google_flights", "departure_id": departure_id, "arrival_id": arrival_id,
              "outbound_date": outbound_date, "type": 1 if return_date else 2, "adults": adults,
              "children": children, "currency": currency, "travel_class": travel_class, "hl": hl, "api_key": api_key}
    if infants_in_seat:
        params["infants_in_seat"] = infants_in_seat   # under 2 with their own seat: priced as an infant, never as a child
    if infants_on_lap:
        params["infants_on_lap"] = infants_on_lap
    if return_date:
        params["return_date"] = return_date
    return get_json(params)


def maps_reviews(place_id: str, api_key: str, sort_by: str = "newestFirst", next_page_token: str = None,
                 hl: str = "en") -> dict:
    """One page of Google Maps reviews for a place (place_id from Places/place-matching). Pass the previous page's
    serpapi_pagination.next_page_token to read further. sort_by: qualityScore, newestFirst, ratingHigh, ratingLow."""
    params = {"engine": "google_maps_reviews", "place_id": place_id, "sort_by": sort_by, "hl": hl, "api_key": api_key}
    if next_page_token:
        params["next_page_token"] = next_page_token
    return get_json(params)


def account(api_key: str) -> dict:
    """Plan and remaining searches this month. Free: SerpApi doesn't charge a search for it. The setup walkthrough
    uses it to prove the key works without spending a search."""
    return get_json({"api_key": api_key}, base="https://serpapi.com/account.json")
