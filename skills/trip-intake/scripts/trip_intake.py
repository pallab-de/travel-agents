#!/usr/bin/env python
"""Check a trip_intake.json before any research starts, and carry what the group said into the report spec.

"Ask, don't assume" is checked here, not only asked for: every answer the plan depends on must be filled in by the
user's own answer. A blank field, a placeholder, or a trait list nobody confirmed fails, so the agent goes back and
asks rather than guessing a default.

  trip_intake.py validate trip_intake.json             exit 0 only if every required answer is present and sane
  trip_intake.py sync trip_intake.json spec.json       copy group traits, fit criteria and trip dates into the spec (fact-guard
                                                       then fails a spec whose traits or dates drift from these)
  trip_intake.py selftest
"""
import argparse, datetime as dt, json, re, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'tools'))   # <plugin_root>/tools/intake_checks.py
from intake_checks import is_placeholder, looks_like_street_address  # noqa: E402

SHAPES = {"stay", "itinerary", "mixed"}
PERIODS = {"total", "per_night", "per_day"}
TRAIT = re.compile(r"^[a-z][a-z0-9_]*$")
MAX_WEIGHT = 0.5   # one criterion moves a place's rank score by at most this much (fact-guard's MAX_SIGNAL_BUMP)


def iso_date(s):
    try:
        return dt.date.fromisoformat(str(s))
    except ValueError:
        return None


def _template_notes():
    try:
        t = json.loads((Path(__file__).resolve().parents[1] / "trip_intake.template.json").read_text(encoding="utf-8"))
        return str((t.get("group_profile") or {}).get("notes") or "").strip()
    except (OSError, ValueError):
        return ""


TEMPLATE_NOTES = _template_notes()


def trip_window(dates):
    """(start, end) of the trip as the plan checks should read it: the exact dates, or the flexible window's outer limits."""
    w = dates.get("window") or {}
    if dates.get("flexible") and w:
        return w.get("earliest"), w.get("latest")
    return dates.get("start"), dates.get("end")


def date_errors(dates):
    errs = []
    if dates.get("flexible") is True:
        w = dates.get("window") or {}
        lo, hi = iso_date(w.get("earliest")), iso_date(w.get("latest"))
        if not lo or not hi:
            errs.append("dates.flexible is true but dates.window needs earliest and latest (YYYY-MM-DD): ask for the window")
        elif hi < lo:
            errs.append(f"dates.window.latest {hi} is before earliest {lo}: confirm the window")
        if not isinstance(w.get("nights"), int) or w.get("nights", 0) < 1:
            errs.append("dates.window.nights must be a whole number of nights: ask how long the stay is")
        return errs
    start, end = iso_date(dates.get("start")), iso_date(dates.get("end"))
    if not start or not end:
        errs.append("dates.start/end must be YYYY-MM-DD: ask for the dates, or record a flexible window (flexible: true, window {earliest, latest, nights})")
    elif end <= start:
        errs.append(f"dates.end {end} is not after dates.start {start}: confirm the dates (a stay needs at least one night)")
    return errs


ITINERARY_FIELDS = ("route_reason", "group_limits", "mode_order", "budget_shape", "meal_coverage", "payment_preference")
PAYMENT = {"cash", "points", "compare"}


def itinerary_errors(it):
    """The fixed constraints trip-constraints-intake settles, held here so the gates can read them."""
    errs = []
    for k in ("route_reason", "group_limits", "budget_shape", "meal_coverage"):
        if not str(it.get(k) or "").strip():
            errs.append(f"itinerary.{k} is blank: ask it, or record 'not_applicable' if the user says it doesn't apply")
    if not it.get("mode_order"):
        errs.append("itinerary.mode_order is empty: ask the order to evaluate rail, coach, car and air for legs inside the trip")
    if it.get("payment_preference") not in PAYMENT:
        errs.append(f"itinerary.payment_preference must be one of {sorted(PAYMENT)}: ask cash, points or a comparison")
    if it.get("stay_accessible") not in ("yes", "no", "not_applicable"):
        errs.append("itinerary.stay_accessible must be yes, no or not_applicable: ask whether remote bases are acceptable")
    for i, p in enumerate(it.get("loyalty") or []):
        if not str(p.get("programme") or "").strip() or not isinstance(p.get("points"), int) or p["points"] < 0:
            errs.append(f"itinerary.loyalty[{i}] needs a programme and a whole number of points")
    return errs




def placeholder_errors(intake):
    """A free-text answer that is a stand-in ('TBD', '<city>', 'x') is not an answer."""
    ctx, dest, prof = intake.get("context") or {}, intake.get("destination") or {}, intake.get("group_profile") or {}
    fields = {"destination.home_base": dest.get("home_base"), "destination.stay_area": dest.get("stay_area"),
              "group_profile.notes": prof.get("notes"), **{f"context.{k}": v for k, v in ctx.items() if isinstance(v, str)}}
    fields.update({f"destination.route[{i}]": p for i, p in enumerate(dest.get("route") or [])})
    fields.update({f"context.stay_style[{i}]": v for i, v in enumerate(ctx.get("stay_style") or []) if isinstance(v, str)})
    it = intake.get("itinerary") or {}
    fields.update({f"itinerary.{k}": v for k, v in it.items() if isinstance(v, str)})
    fields.update({f"itinerary.mode_order[{i}]": v for i, v in enumerate(it.get("mode_order") or []) if isinstance(v, str)})
    fields.update({f"fit_profile.criteria[{i}].why": c.get("why") for i, c in enumerate((intake.get("fit_profile") or {}).get("criteria") or []) if isinstance(c, dict)})
    fields.update({f"group.pets[{i}].species": c.get("species") for i, c in enumerate((intake.get("group") or {}).get("pets") or []) if isinstance(c, dict)})
    return [f"{k} is {v!r}, a placeholder: ask the question and record the user's answer" for k, v in fields.items()
            if isinstance(v, str) and v.strip() and is_placeholder(v)]


def validate(intake, stage="full"):
    """Every problem with the intake, worded as the question still to ask the user. stage="intake" leaves out the itinerary
    block, which trip-constraints-intake collects next; the full check (and sync) include it."""
    errs = []
    shape = intake.get("shape")
    if shape not in SHAPES:
        errs.append(f"shape is {shape!r}: ask whether this is a stay, an itinerary, or both ({sorted(SHAPES)})")
    dest = intake.get("destination") or {}
    if shape in ("stay", "mixed") and not str(dest.get("home_base", "")).strip():
        errs.append("destination.home_base is empty: ask where they're starting from (the stay is ranked by distance from it)")
    if looks_like_street_address(dest.get("home_base", "")):
        errs.append("destination.home_base looks like a street address: the report publishes the starting point in its links, so ask for an area, station or landmark")
    if shape in ("stay", "mixed") and not str(dest.get("stay_area", "")).strip():
        errs.append("destination.stay_area is empty: ask which city or area the stay should be in (it can be the home area for a staycation)")
    if shape in ("itinerary", "mixed") and not [p for p in dest.get("route") or [] if str(p).strip()]:
        errs.append("destination.route is empty: ask which places the trip covers, in order")
    errs += date_errors(intake.get("dates") or {})
    group = intake.get("group") or {}
    if not isinstance(group.get("adults"), int) or isinstance(group.get("adults"), bool) or group["adults"] < 1:
        errs.append("group.adults must be at least 1: ask who is travelling")
    for i, c in enumerate(group.get("children") or []):
        if not isinstance(c, dict) or not isinstance(c.get("age"), int) or isinstance(c.get("age"), bool) or not 0 <= c["age"] <= 17:
            errs.append(f"group.children[{i}] needs an age in whole years from 0 to 17: ask (it decides cribs, kids' clubs, fares)")
    for i, p in enumerate(group.get("pets") or []):
        if not isinstance(p, dict) or not str(p.get("species", "")).strip() or is_placeholder(p.get("species")):
            errs.append(f"group.pets[{i}] has no species: ask, or remove the entry if there's no pet")
    budget = intake.get("budget") or {}
    if not isinstance(budget.get("amount"), (int, float)) or isinstance(budget.get("amount"), bool) or not budget["amount"] > 0 or budget["amount"] == float("inf"):
        errs.append("budget.amount must be a positive number: ask for the budget")
    if not re.fullmatch(r"[A-Z]{3}", str(budget.get("currency", ""))):
        errs.append("budget.currency must be a 3-letter code (EUR, INR, USD): ask which currency")
    if budget.get("period") not in PERIODS:
        errs.append(f"budget.period must be one of {sorted(PERIODS)}: ask whether the budget is total, per night or per day")
    prof = intake.get("group_profile") or {}
    traits = prof.get("traits")
    if not isinstance(traits, list):
        errs.append("group_profile.traits must be a list (empty if they care about nothing in particular)")
    else:
        bad = [t for t in traits if not TRAIT.fullmatch(str(t))]
        if bad:
            errs.append(f"group_profile.traits {bad} must be snake_case ids (e.g. needs_kids_play_area)")
    if prof.get("traits_confirmed") is not True:
        errs.append("group_profile.traits_confirmed is not true: read the trait list back to the user and have them "
                    "confirm it (ranking bumps may only answer traits they confirmed)")
    notes = str(prof.get("notes") or "").strip()
    if not notes or notes == TEMPLATE_NOTES:
        errs.append("group_profile.notes is empty or still the template's instruction: record what the group said, in their own words")
    errs += placeholder_errors(intake)
    ctx = intake.get("context") or {}
    for k in ("trip_nature", "pace", "time_in_place", "splurge", "affinities"):
        if not str(ctx.get(k) or "").strip():
            errs.append(f"context.{k} is blank: ask it, or record 'not_applicable' if the user says it doesn't apply")
    if not ctx.get("stay_style"):
        errs.append("context.stay_style is blank: ask it, or record ['not_applicable']")
    if shape in ("itinerary", "mixed") and stage == "full":
        errs += itinerary_errors(intake.get("itinerary") or {})
    fit = intake.get("fit_profile") or {}
    crits = fit.get("criteria")
    if not isinstance(crits, list):
        errs.append("fit_profile.criteria must be a list: agree with the user what makes a place right (or wrong) for this trip")
    else:
        seen = set()
        for i, c in enumerate(crits):
            if not isinstance(c, dict):
                errs.append(f"fit_profile.criteria[{i}] must be an object with an id, a why, a weight and a polarity (got {c!r})")
                continue
            cid = str(c.get("id", ""))
            if not TRAIT.fullmatch(cid) or cid in seen:
                errs.append(f"fit_profile.criteria[{i}] needs a unique snake_case id (got {cid!r})")
            seen.add(cid)
            if not str(c.get("why", "")).strip():
                errs.append(f"fit_profile.criteria[{i}] ({cid}) has no 'why' in the user's words: ask why it matters to them")
            w = c.get("weight")
            if not isinstance(w, (int, float)) or isinstance(w, bool) or not 0 < w <= MAX_WEIGHT:
                errs.append(f"fit_profile.criteria[{i}] ({cid}) weight must be above 0 and at most {MAX_WEIGHT}")
            if c.get("polarity") not in ("+", "-"):
                errs.append(f"fit_profile.criteria[{i}] ({cid}) polarity must be '+' (a quality) or '-' (a drawback)")
            if c.get("evidence", "fact") not in ("fact", "maps"):
                errs.append(f"fit_profile.criteria[{i}] ({cid}) evidence must be 'fact' or 'maps' (anything about distance or walkability is 'maps')")
    if fit.get("confirmed") is not True:
        errs.append("fit_profile.confirmed is not true: show the user the criteria with their weights and have them confirm or edit them")
    if not iso_date(str(intake.get("created_at", ""))[:10]):
        errs.append("created_at must be an ISO date-time")
    return errs


def sync(intake, spec):
    """Copy what the group said into the spec, exactly. fact-guard's intake check compares the two."""
    out = dict(spec)
    out["group_traits"] = list((intake.get("group_profile") or {}).get("traits") or [])
    start, end = trip_window(intake.get("dates") or {})
    out["trip"] = {"start": start, "end": end}
    out["fit_criteria"] = [{k: c[k] for k in ("id", "weight", "polarity", "evidence") if k in c}
                           for c in (intake.get("fit_profile") or {}).get("criteria") or [] if isinstance(c, dict)]
    return out


def selftest():
    good = {"shape": "mixed", "destination": {"home_base": "Home city", "stay_area": "Town A", "route": ["Town A", "Town B"]},
            "dates": {"start": "2030-03-01", "end": "2030-03-05", "flexible": False},
            "group": {"adults": 2, "children": [{"age": 3}], "pets": []},
            "group_profile": {"traits": ["needs_kids_play_area"], "traits_confirmed": True, "notes": "Two adults and a toddler who want slow days and an early dinner."},
            "context": {"trip_nature": "unwind", "pace": "relaxed", "time_in_place": "mostly at the stay", "splurge": "value first", "stay_style": ["intimate"], "affinities": "not_applicable"},
            "budget": {"amount": 2000, "currency": "EUR", "period": "total"}, "created_at": "2030-01-01T10:00:00Z",
            "itinerary": {"route_reason": "the train is the fixed point", "group_limits": "not_applicable", "stay_accessible": "no", "mode_order": ["rail", "road", "air"],
                          "budget_shape": "one splurge night", "meal_coverage": "preferred plus one alternate", "payment_preference": "cash", "loyalty": []},
            "fit_profile": {"confirmed": True, "criteria": [
                {"id": "kids_play", "why": "the group needs somewhere to burn energy", "weight": 0.4, "polarity": "+", "evidence": "fact"},
                {"id": "near_sights", "why": "only one day in town", "weight": 0.5, "polarity": "+", "evidence": "maps"},
                {"id": "highway_noise", "why": "light sleepers", "weight": 0.3, "polarity": "-"}]}}
    clone = lambda x: json.loads(json.dumps(x))
    ph = clone(good); ph["destination"]["home_base"] = "TBD"; ph["context"]["pace"] = "<pace>"
    same_day = clone(good); same_day["dates"] = {"start": "2030-03-01", "end": "2030-03-01", "flexible": False}
    flex = clone(good); flex["dates"] = {"flexible": True, "window": {"earliest": "2030-03-01", "latest": "2030-03-20", "nights": 2}}
    flex_bad = clone(good); flex_bad["dates"] = {"flexible": True}
    no_area = clone(good); no_area["destination"].pop("stay_area")
    no_mode = clone(good); no_mode["itinerary"]["mode_order"] = []
    blank_ctx = clone(good); blank_ctx["context"]["pace"] = ""
    tmpl_notes = clone(good); tmpl_notes["group_profile"]["notes"] = TEMPLATE_NOTES
    cases = [("a complete intake passes", validate(good) == []),
             ("a placeholder answer ('TBD', '<pace>') fails and names the field", sum("placeholder" in e for e in validate(ph)) == 2),
             ("a zero-night stay fails", any("not after" in e for e in validate(same_day))),
             ("a flexible window with earliest, latest and nights passes", validate(flex) == [] and trip_window(flex["dates"]) == ("2030-03-01", "2030-03-20")),
             ("flexible dates with no window fail", any("dates.window" in e for e in validate(flex_bad))),
             ("a stay with a home base but no stay area fails", any("stay_area" in e for e in validate(no_area))),
             ("an itinerary with no mode order fails", any("mode_order" in e for e in validate(no_mode))),
             ("a blank context field fails", any("context.pace" in e for e in validate(blank_ctx))),
             ("notes left as the template's instruction fail", any("group_profile.notes" in e for e in validate(tmpl_notes)))] + []
    def broken(path, value):
        d = clone(good); node = d
        for k in path[:-1]:
            node = node[k]
        node[path[-1]] = value
        return validate(d)
    cases += [
        ("an empty shape fails", bool(broken(["shape"], ""))),
        ("a stay with no home base fails", bool(broken(["destination", "home_base"], ""))),
        ("dates out of order fail", bool(broken(["dates", "end"], "2030-02-01"))),
        ("zero adults fails", bool(broken(["group", "adults"], 0))),
        ("a child with no age fails", bool(broken(["group", "children"], [{}]))),
        ("a placeholder pet fails", bool(broken(["group", "pets"], [{"species": "", "weight_kg": None}]))),
        ("a zero budget fails", bool(broken(["budget", "amount"], 0))),
        ("a currency name instead of a code fails", bool(broken(["budget", "currency"], "euros"))),
        ("unconfirmed traits fail", bool(broken(["group_profile", "traits_confirmed"], False))),
        ("a free-text trait fails", bool(broken(["group_profile", "traits"], ["likes the beach"]))),
        ("an unconfirmed fit profile fails", bool(broken(["fit_profile", "confirmed"], False))),
        ("a criterion over the 0.5 weight cap fails", bool(broken(["fit_profile", "criteria"], [{"id": "x", "why": "y", "weight": 0.8, "polarity": "+"}]))),
        ("a criterion with no reason in the user's words fails", bool(broken(["fit_profile", "criteria"], [{"id": "x", "weight": 0.2, "polarity": "+"}]))),
        ("a criterion with no sign fails", bool(broken(["fit_profile", "criteria"], [{"id": "x", "why": "y", "weight": 0.2}]))),
        ("a boolean for adults, a negative child age and a string criterion fail without crashing",
         bool(broken(["group", "adults"], True)) and bool(broken(["group", "children"], [{"age": -3}])) and bool(broken(["fit_profile", "criteria"], ["quiet"]))),
        ("a flat address and a placeholder species fail; a station passes", bool(broken(["destination", "home_base"], "Flat 4B, Rose Apartments, MG Road"))
         and bool(broken(["group", "pets"], [{"species": "TBD"}])) and not any("street address" in e for e in broken(["destination", "home_base"], "42nd Street station"))),
        ("TBC, null and {city} are placeholders", all(bool(broken(["context", "pace"], v)) for v in ("TBC", "null", "{city}", "to be decided"))),
        ("an empty but confirmed trait list passes", validate(dict(clone(good), group_profile={"traits": [], "traits_confirmed": True, "notes": "they care about nothing in particular"})) == []),
    ]
    synced = sync(good, {"blocks": []})
    cases.append(("sync copies the fit criteria the gate checks", [c["id"] for c in synced["fit_criteria"]] == ["kids_play", "near_sights", "highway_noise"]
                  and "why" not in synced["fit_criteria"][0]))
    cases.append(("sync copies traits and dates", synced["group_traits"] == ["needs_kids_play_area"]
                  and synced["trip"] == {"start": "2030-03-01", "end": "2030-03-05"} and synced["blocks"] == []))
    template = json.loads((Path(__file__).resolve().parents[1] / "trip_intake.template.json").read_text(encoding="utf-8"))
    cases.append(("the blank template fails (it must be filled from answers)", bool(validate(template))))
    ok = all(p for _, p in cases)
    for name, p in cases:
        print("PASS" if p else "FAIL", name)
    print(f"SELFTEST {'PASS' if ok else 'FAIL'} ({len(cases)} cases)")
    return 0 if ok else 1


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("validate"); p.add_argument("intake"); p.add_argument("--stage", choices=["intake", "full"], default="full",
                                                              help="intake: before trip-constraints-intake has added the itinerary block")
    p = sp.add_parser("sync"); p.add_argument("intake"); p.add_argument("spec")
    sp.add_parser("selftest")
    a = ap.parse_args()
    if a.cmd == "selftest":
        return selftest()
    intake = json.loads(Path(a.intake).read_text(encoding="utf-8"))
    if a.cmd == "validate":
        errs = validate(intake, a.stage)
        for e in errs:
            print("ASK", e)
        print("INTAKE " + ("OK" if not errs else f"INCOMPLETE ({len(errs)})") + (" (itinerary block still to come)" if a.stage == "intake" and not errs and intake.get("shape") in ("itinerary", "mixed") else ""))
        return 0 if not errs else 1
    errs = validate(intake)
    if errs:
        for e in errs:
            print("ASK", e)
        print("sync refused: the intake is incomplete")
        return 1
    spec_path = Path(a.spec)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec_path.write_text(json.dumps(sync(intake, spec), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"synced group_traits and trip dates into {spec_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
