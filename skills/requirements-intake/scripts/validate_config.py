#!/usr/bin/env python
"""Check a staycation config.json before any search starts: every value must be the user's own answer.

The template ships every user-decided value as null. A null, an empty string, a zero where a real answer is
needed, or a value left from the template fails; a question that doesn't apply is answered with the exact text
"n/a, user confirmed". That keeps an agent from carrying a default through as if the user chose it.

  validate_config.py config.json       exit 0 only if every answer is filled in
  validate_config.py selftest
"""
import json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'tools'))   # <plugin_root>/tools/intake_checks.py
from intake_checks import is_placeholder, looks_like_street_address  # noqa: E402

NA = "n/a, user confirmed"
ALLOWED_EMPTY_LISTS = {"children", "pets", "avoid_date_windows"}   # an empty list is a real answer: none
SKIP = {"_comment"}


ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}$")
ISO_DATETIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:\d{2})?$")
TZ = re.compile(r"[+-]\d{2}:\d{2}$")
CURRENCY = re.compile(r"[A-Z]{3}$")


NEVER_NA = {"city", "home_base_address", "group.adults", "budget.amount", "budget.currency", "search_weekend.check_in", "search_weekend.check_out",
            "search_weekend.out_departure_iso", "search_weekend.back_departure_iso", "search_weekend.tz", "budget.total_or_per_night", "deliverable.format"}


import sys as _sys
if _sys.stdout.encoding and _sys.stdout.encoding.lower() != 'utf-8':   # names in other scripts must print on a Windows pipe too
    _sys.stdout.reconfigure(encoding='utf-8')


def positive(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0


# What each answer must look like beyond "filled in". A path maps to (test, what it wants); the exact text NA is
# always accepted for an answer that doesn't apply.
RULES = {
    "drive_time_cap_min": (positive, "a number of minutes above 0"),
    "drive_time_cap_extended_min": (positive, "a number of minutes above 0"),
    "search_weekend.check_in": (lambda v: isinstance(v, str) and bool(ISO_DATE.match(v)), "a date as YYYY-MM-DD"),
    "search_weekend.check_out": (lambda v: isinstance(v, str) and bool(ISO_DATE.match(v)), "a date as YYYY-MM-DD"),
    "search_weekend.out_departure_iso": (lambda v: isinstance(v, str) and bool(ISO_DATETIME.match(v)), "a date and time such as 2030-05-03T09:30+01:00"),
    "search_weekend.back_departure_iso": (lambda v: isinstance(v, str) and bool(ISO_DATETIME.match(v)), "a date and time such as 2030-05-05T11:00+01:00"),
    "search_weekend.tz": (lambda v: isinstance(v, str) and bool(TZ.match(v)), "a UTC offset such as +01:00"),
    "group.adults": (lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 1, "a whole number of at least 1"),
    "hard_filters.min_bedrooms": (lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 1, "a whole number of at least 1"),
    "home_base_address": (lambda v: isinstance(v, str) and not looks_like_street_address(v), "an area, station or landmark: the report publishes it in its drive-time links, so not a street address"),
    "real_bed_rule.adults_need_beds": (lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 1, "the fewest real beds the group accepts, a whole number of at least 1"),
    "budget.total_or_per_night": (lambda v: v in ("total", "per_night"), "'total' or 'per_night'"),
    "deliverable.format": (lambda v: v == "html", "'html'"),
    "hard_filters.hotel_rooms": (lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 1, "a whole number of rooms"),
    "hard_filters.hotel_guests_per_room": (lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 1, "a whole number of guests"),
    "hard_filters.hotel_min_stars": (lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 5, "a star floor from 0 to 5"),
    "budget.amount": (positive, "an amount above 0"),
    "budget.currency": (lambda v: isinstance(v, str) and bool(CURRENCY.match(v)), "a 3-letter code such as EUR"),
    "apis.review_provider.budget_calls": (positive, "a number of calls above 0"),
}
BOOLEANS = {"drop_for_date_unavailability", "drive_time_extended_needs_reason", "hard_filters.whole_property_only", "hard_filters.pool_required",
            "hard_filters.pool_private_only", "hard_filters.pet_friendly_required",
            "real_bed_rule.children_can_share_adult_bed", "apis.review_provider.authorized"}


def walk(tpl, cfg, path, fails):
    """Walk the template, not the config: every key the template has must be answered, so deleting a section fails."""
    for k, t in tpl.items():
        if k in SKIP:
            continue
        here = path + [k]
        dotted = ".".join(here)
        if not isinstance(cfg, dict) or k not in cfg:
            fails.append(f"{dotted}: missing; ask the user (or write '{NA}')")
            continue
        v = cfg[k]
        if isinstance(t, dict):
            if not isinstance(v, dict):
                fails.append(f"{dotted}: must be an object")
            else:
                walk(t, v, here, fails)
            continue
        if isinstance(t, list):
            if not isinstance(v, list):
                fails.append(f"{dotted}: must be a list")
            elif not v and k not in ALLOWED_EMPTY_LISTS:
                fails.append(f"{dotted}: empty list; answer it or write '{NA}'")
            continue
        if v is None or (isinstance(v, str) and not v.strip()):
            fails.append(f"{dotted}: not answered (use '{NA}' if it does not apply)")
        elif v == NA:
            if dotted in NEVER_NA:
                fails.append(f"{dotted}: always applies, so it can't be '{NA}'")
            continue
        elif isinstance(v, str) and is_placeholder(v, strict=True):
            fails.append(f"{dotted}: {v!r} is a placeholder, not an answer")
        elif dotted in BOOLEANS and not isinstance(v, bool):
            fails.append(f"{dotted}: must be true or false (or '{NA}')")
        elif dotted in RULES and not RULES[dotted][0](v):
            fails.append(f"{dotted}: {v!r} is not {RULES[dotted][1]}")


def validate(cfg, template=None):
    fails = []
    tpl = template if template is not None else json.loads((Path(__file__).resolve().parents[1] / "config.template.json").read_text(encoding="utf-8"))
    walk(tpl, cfg, [], fails)
    return fails


def selftest():
    blank = json.loads((Path(__file__).resolve().parents[1] / "config.template.json").read_text(encoding="utf-8"))
    filled = {"city": "Valmora", "home_base_address": "Valmora old town", "drive_time_cap_min": 120, "drive_time_cap_extended_min": 150,
              "drive_time_extended_needs_reason": True,
              "search_weekend": {"check_in": "2030-05-03", "check_out": "2030-05-05", "out_departure_iso": "2030-05-03T09:30+01:00",
                                 "back_departure_iso": "2030-05-05T11:00+01:00", "tz": "+01:00"},
              "avoid_date_windows": [], "drop_for_date_unavailability": True, "group": {"adults": 4, "children": [], "pets": []},
              "hard_filters": {"whole_property_only": True, "min_bedrooms": 3, "pool_required": True, "pool_private_only": True, "pet_friendly_required": False,
                              "hotel_rooms": NA, "hotel_guests_per_room": NA, "hotel_min_stars": NA},
              "real_bed_rule": {"adults_need_beds": 4, "children_can_share_adult_bed": True, "notes": "teenagers need their own beds"},
              "budget": {"total_or_per_night": "total", "amount": 900, "currency": "EUR"},
              "deliverable": {"format": "html", "save_path": "report.html"},
              "apis": {"review_provider": {"authorized": False, "which": NA, "budget_calls": NA}}}
    partial = json.loads(json.dumps(filled)); partial["budget"]["amount"] = None
    cases = [("the shipped template is all blank, so it fails", len(validate(blank)) > 10),
             ("no default is pre-filled: pool, pets, caps and tz are null",
              blank["hard_filters"]["pool_required"] is None and blank["group"]["pets"] == []
              and blank["drive_time_cap_min"] is None and blank["search_weekend"]["tz"] is None),
             ("a fully answered config passes", validate(filled) == []),
             ("one null answer fails and names it", any("budget.amount" in f for f in validate(partial))),
             ("'n/a, user confirmed' counts as an answer where the question can not apply", validate(dict(filled, hard_filters=dict(filled["hard_filters"], pool_required=NA))) == []),
             ("a street address as the starting point fails; an area passes", validate(dict(filled, home_base_address="12 Example Street, Valmora")) != [] and validate(dict(filled, home_base_address="Valmora old town")) == []),
             ("flat and unit addresses fail; stations and landmarks pass", all(validate(dict(filled, home_base_address=x)) != [] for x in ("Flat 4B, Rose Apartments, MG Road", "Apt 5, 12 Rue de Rivoli", "Hauptstrasse 5"))
              and all(validate(dict(filled, home_base_address=x)) == [] for x in ("42nd Street station", "Central Chowk metro station"))),
             ("TBC, null and {city} are not answers", all(validate(dict(filled, city=x)) != [] for x in ("TBC", "null", "{city}", "[city]", "N/A.", "None"))),
             ("the real-bed rule is a count: a boolean is refused", validate(dict(filled, real_bed_rule=dict(filled["real_bed_rule"], adults_need_beds=True))) != []),
             ("a question that always applies can't be answered 'n/a'", validate(dict(filled, city=NA)) != []),
             ("an empty config fails", len(validate({})) > 10),
             ("a deleted section fails", any("group" in f for f in validate({k: v for k, v in filled.items() if k != "group"}))),
             ("zero adults, a TBD city and a bad date all fail", len(validate(dict(filled, city="TBD", group={"adults": 0, "children": [], "pets": []},
                                                                               search_weekend=dict(filled["search_weekend"], check_in="next Friday")))) == 3)]
    bad = 0
    for name, ok in cases:
        print(("PASS " if ok else "FAIL ") + name)
        bad += not ok
    print("SELFTEST PASS (%d cases)" % len(cases) if not bad else "SELFTEST FAIL")
    return 1 if bad else 0


def main():
    if len(sys.argv) == 2 and sys.argv[1] == "selftest":
        return selftest()
    if len(sys.argv) != 2 or sys.argv[1].startswith("-"):
        print(__doc__)
        return 0 if sys.argv[1:] in (["-h"], ["--help"]) else 1
    fails = validate(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
    for f in fails:
        print("FAIL", f)
    print("config ok" if not fails else f"{len(fails)} unanswered")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
