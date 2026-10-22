"""Shared Google Maps API client: every skill's script that needs Places, Geocoding or Distance Matrix
calls through here instead of writing its own urllib+urlencode HTTP layer: one place for the
"build a URL, open it, sanitize the error" logic.

Deliberately thin: every function returns Google's OWN parsed JSON response, unprocessed. Status-checking,
field extraction, caching and scoring are each caller's own domain logic (a fuzzy-matcher's idea of a
"good enough" candidate is not a single-venue lookup's idea of one) and stay in the skill that owns them.

Never accepts or loads a credential itself -- every function takes `api_key` as a plain argument. Loading
the key is `tools/travel_secrets.py`'s job. Never logs, prints or includes a request URL in an error
(Google's APIs take the key as a query parameter, so the URL itself is secret-bearing).
"""
import json
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://maps.googleapis.com/maps/api"


class MapsApiError(RuntimeError):
    """Google answered, but with a status other than OK (or ZERO_RESULTS where that is allowed). The message carries
    the status and Google's own error text, never the request URL."""


def require_ok(data: dict, what: str, allow=("OK", "ZERO_RESULTS")) -> dict:
    """Return `data` when its top-level status is allowed; raise MapsApiError otherwise. A quota or key error is a
    stop, never "no match": REQUEST_DENIED and OVER_QUERY_LIMIT must not become silent missing data."""
    status = (data or {}).get("status")
    if status not in allow:
        raise MapsApiError(f"{what}: Google status {status!r} ({(data or {}).get('error_message', 'no message')})")
    return data


def matrix_element(data: dict, what: str = "Distance Matrix", n: int = 0) -> dict:
    """The n-th element of a Distance Matrix answer for the first origin, after checking the top-level status is OK and
    that Google sent the element at all. The caller reads the element's own status (OK, ZERO_RESULTS, NOT_FOUND)."""
    require_ok(data, what, allow=("OK",))
    rows = (data or {}).get("rows") or []
    if not rows or len(rows[0].get("elements") or []) <= n:
        raise MapsApiError(f"{what}: Google answered OK but sent no element for a requested point")
    return rows[0]["elements"][n]


def get_json(url: str) -> dict:
    """Sanitized GET -> parsed JSON. Never surfaces the request URL (it carries the API key)."""
    try:
        with urllib.request.urlopen(url, timeout=25) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"Request failed: {e.reason}") from None


def text_search(query: str, api_key: str, lat: float = None, lng: float = None, radius: int = None) -> dict:
    """Places Text Search. lat/lng/radius are optional (a bare keyword search still works)."""
    params = {"query": query, "key": api_key}
    if lat is not None and lng is not None:
        params["location"] = f"{lat},{lng}"
    if radius is not None:
        params["radius"] = radius
    return get_json(f"{BASE}/place/textsearch/json?" + urllib.parse.urlencode(params))


def find_place_from_text(query: str, api_key: str, fields: str = "place_id,name") -> dict:
    """Places Find Place From Text -- best single match for a specific, named venue."""
    params = {"input": query, "inputtype": "textquery", "fields": fields, "key": api_key}
    return get_json(f"{BASE}/place/findplacefromtext/json?" + urllib.parse.urlencode(params))


def nearby_search(lat: float, lng: float, radius: int, api_key: str, place_type: str = None, pagetoken: str = None) -> dict:
    """Places Nearby Search around a point. place_type e.g. 'lodging'; omit for any type. pagetoken (from a previous
    answer's next_page_token, valid after about two seconds) fetches the next page."""
    params = {"location": f"{lat},{lng}", "radius": radius, "key": api_key}
    if place_type:
        params["type"] = place_type
    if pagetoken:
        params = {"pagetoken": pagetoken, "key": api_key}
    return get_json(f"{BASE}/place/nearbysearch/json?" + urllib.parse.urlencode(params))


def place_details(place_id: str, api_key: str, fields: str, reviews_sort: str = None) -> dict:
    """Place Details. `fields` is caller-specified -- different callers want different (and billed) fields.
    `reviews_sort` ("newest" or "most_relevant") picks which 5 reviews come back."""
    params = {"place_id": place_id, "fields": fields, "key": api_key}
    if reviews_sort:
        params["reviews_sort"] = reviews_sort
    return get_json(f"{BASE}/place/details/json?" + urllib.parse.urlencode(params))


def geocode(address: str, api_key: str) -> dict:
    return get_json(f"{BASE}/geocode/json?" + urllib.parse.urlencode({"address": address, "key": api_key}))


def distance_matrix(origins: list, destinations: list, api_key: str, departure_time: int = None,
                     traffic_model: str = None, mode: str = None) -> dict:
    """Distance Matrix. origins/destinations are lists of "lat,lng" or address strings -- joined with '|'.
    Pass departure_time (epoch seconds) + traffic_model='best_guess' for traffic-aware duration_in_traffic;
    omit both for a plain no-traffic estimate. mode: 'driving' (default), 'walking', 'bicycling' or 'transit'."""
    params = {"origins": "|".join(origins), "destinations": "|".join(destinations), "key": api_key}
    if departure_time is not None:
        params["departure_time"] = departure_time
    if traffic_model is not None:
        params["traffic_model"] = traffic_model
    if mode is not None:
        params["mode"] = mode
    return get_json(f"{BASE}/distancematrix/json?" + urllib.parse.urlencode(params))
