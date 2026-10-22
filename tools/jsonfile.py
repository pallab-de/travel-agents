"""Read a JSON file that may hold the JSON itself or that JSON encoded once more as a string.

Browser tools (Playwright's evaluate, saved by hand) often return a JSON string of a JSON document; the scrapers' raw
files come in either form. One loader, so no script carries its own copy.
"""
import json
from pathlib import Path


def load_json(path):
    raw = Path(path).read_text(encoding="utf-8").strip()
    return json.loads(json.loads(raw)) if raw.startswith('"') else json.loads(raw)
