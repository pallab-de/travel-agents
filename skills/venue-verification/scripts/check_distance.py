#!/usr/bin/env python
"""Quick driving distance/time between two named places -- no traffic modeling, two provider choices.

Usage (non-interactive -- safe for the agent to run directly):

    python "<plugin root>/skills/venue-verification/scripts/check_distance.py" --provider google "<Place A>, <City>" "<Place B>, <City>"
    python "<plugin root>/skills/venue-verification/scripts/check_distance.py" --provider ors "<Place A>, <City>" "<Place B>, <City>"

Only the parsed distance/duration is ever printed. The API key is loaded once (tools/travel_secrets.py),
used to build a request, and never printed, logged, or included in any error message.

This is the light, single-pair, no-traffic-model tool, for the many casual "how far is X from Y / is this walkable"
questions during research. For a time-critical transit leg (a specific departure time, traffic matters) use
`transport-options`' `compare_modes.py drive`, which writes a capture the fact-guard gate accepts.
"""
from __future__ import annotations
import argparse, json, sys, urllib.error, urllib.parse, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'tools'))
from travel_secrets import load_key  # noqa: E402
import google_maps as gm  # noqa: E402


import sys as _sys
if _sys.stdout.encoding and _sys.stdout.encoding.lower() != 'utf-8':   # names in other scripts must print on a Windows pipe too
    _sys.stdout.reconfigure(encoding='utf-8')


def _ors_json(url: str, headers: dict, data: bytes | None = None) -> dict:
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Request failed: {exc.reason}") from None


def check_ors(origin: str, destination: str, api_key: str) -> str:
    headers = {"Authorization": api_key}  # header auth -- keeps the key out of the URL
    o = _ors_json("https://api.openrouteservice.org/geocode/search?" + urllib.parse.urlencode({"text": origin, "size": 1}), headers)
    d = _ors_json("https://api.openrouteservice.org/geocode/search?" + urllib.parse.urlencode({"text": destination, "size": 1}), headers)
    try:
        o_ll = o["features"][0]["geometry"]["coordinates"]
        d_ll = d["features"][0]["geometry"]["coordinates"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("Could not geocode one of the two places.") from None
    body = json.dumps({"locations": [o_ll, d_ll], "metrics": ["distance", "duration"]}).encode("utf-8")
    matrix = _ors_json("https://api.openrouteservice.org/v2/matrix/driving-car", {**headers, "Content-Type": "application/json"}, body)
    try:
        dist, dur = matrix['distances'][0][1], matrix['durations'][0][1]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("OpenRouteService returned no matrix for these two places.") from None
    if not isinstance(dist, (int, float)) or not isinstance(dur, (int, float)):
        raise RuntimeError("OpenRouteService found no driving route between these two places.")
    return f"{dist / 1000:.1f} km, {dur / 60:.0f} min (OpenRouteService, driving-car)"


def check_google(origin: str, destination: str, api_key: str) -> str:
    result = gm.distance_matrix([origin], [destination], api_key)
    element = gm.matrix_element(result, "Distance Matrix")
    if element.get("status") != "OK":
        raise RuntimeError(f"No route found: {element.get('status')}")
    return f"{element['distance']['text']}, {element['duration']['text']} (Google Distance Matrix, driving, no traffic model)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("origin")
    parser.add_argument("destination")
    parser.add_argument("--provider", choices=["ors", "google"], default="google")
    args = parser.parse_args()

    key_name = "OPENROUTESERVICE_API_KEY" if args.provider == "ors" else "GOOGLE_MAPS_API_KEY"
    try:
        api_key = load_key(key_name)
    except (FileNotFoundError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        result = check_ors(args.origin, args.destination, api_key) if args.provider == "ors" else check_google(args.origin, args.destination, api_key)
    except RuntimeError as exc:
        print(f"Lookup failed: {exc}", file=sys.stderr)
        return 1

    print(f"{args.origin} -> {args.destination}: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
