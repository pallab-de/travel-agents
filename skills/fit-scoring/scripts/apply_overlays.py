#!/usr/bin/env python
"""Write the fit scorer's overlays into a report spec, recompute each rank score and re-rank.

  apply_overlays.py SPEC.json FIT_OVERLAYS.json [--out SPEC.json]
  apply_overlays.py selftest

FIT_OVERLAYS.json: {<card id or name>: [overlay, ...]} (shape in ../brief-fit-scorer.md).

For every card with a rank-score-v2 basis (a guest_score read from reviews): group_fit becomes its overlays, score
becomes guest_score + overlays (each card's total capped at +/-1.0, the score held to 1-5), and cards are re-ordered
by score, the previous order breaking ties; ranks are renumbered in the cards and in every table row that names them.
Nothing is written if any overlay breaks a rule fact-guard enforces: each is named with the reason.
"""
import argparse, json, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "fact-guard" / "scripts"))
import guard  # noqa: E402


def apply(spec, overlays):
    """Returns (new spec, problems). Problems are fact-guard's own rank-score messages, so a spec this accepts passes
    the gate's rank-score check."""
    spec = json.loads(json.dumps(spec))
    criteria = guard.fit_criteria(spec)
    classes = guard.policy(spec)["source_classes"]
    problems = []
    known = set()
    for b in spec.get("blocks", []):
        if b.get("type") != "cards":
            continue
        for it in b.get("items", []):
            key = str(it.get("id") or it.get("name"))
            known |= {key, it.get("name")}
            ovs = overlays.get(key, overlays.get(it.get("name")))
            basis = it.get("score_basis") or {}
            if ovs is None:
                continue
            if basis.get("formula") != "rank-score-v2" or basis.get("guest_score") is None:
                problems.append(f"{it.get('name')}: has overlays but no rank-score-v2 guest score to put them on "
                                f"(read and theme its reviews first)")
                continue
            basis["group_fit"] = ovs
            total = 0.0
            for o in ovs:
                try:
                    total += float(o.get("bump", 0))
                except (TypeError, ValueError):
                    pass
            total = max(-guard.MAX_TOTAL_GROUP_FIT_BUMP, min(guard.MAX_TOTAL_GROUP_FIT_BUMP, total))
            it["score"] = max(guard.RANK_SCORE_FLOOR, min(guard.RANK_SCORE_CAP, round(float(basis["guest_score"]) + total, 1)))
            problems += guard.rank_fails(it, basis, "rank-score-v2", float(basis["guest_score"]), criteria, classes)
        scored = [it for it in b.get("items", []) if it.get("score") is not None]
        if scored and len(scored) == len(b.get("items", [])):
            old = {id(it): (it["rank"] if it.get("rank") is not None else i) for i, it in enumerate(b["items"])}
            b["items"].sort(key=lambda it: (-float(it["score"]), old[id(it)]))
            for i, it in enumerate(b["items"], 1):
                it["rank"] = i
    problems += [f"overlays given for {k!r}, which is no card in the spec" for k in overlays if k not in known]
    cards = {it.get("name"): it for b in spec.get("blocks", []) if b.get("type") == "cards" for it in b["items"]}
    for b in spec.get("blocks", []):
        if b.get("type") == "table":
            for t in b.get("items", []):
                c = cards.get(t.get("name"))
                if c is None:
                    continue
                if "rank" in t:
                    t["rank"] = c.get("rank")
                for col in b.get("columns", []):   # a score column shows the card's score, so it follows the new one
                    if col.get("key") in ("score", "rating") and "score" in c and col["key"] in t:
                        t[col["key"]] = c["score"]
            if all(isinstance(t.get("rank"), int) for t in b.get("items", [])):
                b["items"].sort(key=lambda t: t["rank"])   # the table lists places in rank order too
    return spec, problems


def selftest():
    def card(name, guest, facts):
        return {"name": name, "id": name.lower(), "rank": 0, "facts": facts, "score": guest,
                "score_basis": {"formula": "rank-score-v2", "guest_score": guest}}
    spec = {"fit_criteria": [{"id": "nature_setting", "weight": 0.5, "polarity": "+"},
                             {"id": "kids_extras", "weight": 0.4, "polarity": "+"},
                             {"id": "road_noise", "weight": 0.3, "polarity": "-"}],
            "blocks": [{"type": "cards", "id": "c", "items": [
                card("Stylish Villa", 4.8, [{"label": "Design", "value": "Architect-designed interiors"}]),
                card("Lake Farm", 4.5, [{"label": "View", "value": "Lake view from every room"},
                                        {"label": "Extras", "value": "Games room and play area"},
                                        {"label": "Setting", "value": "Fronts the state highway"}])]},
                       {"type": "table", "id": "t", "items": [{"name": "Stylish Villa", "rank": 1}, {"name": "Lake Farm", "rank": 2}]}]}
    ov = {"lake farm": [
        {"signal": "lake view", "criterion": "nature_setting", "bump": 0.5, "fact": {"facts": 0}, "reason": "they want to unwind by water"},
        {"signal": "games room and play area", "criterion": "kids_extras", "bump": 0.4, "fact": {"facts": 1}, "reason": "keeps the group busy"},
        {"signal": "highway frontage", "criterion": "road_noise", "bump": -0.2, "fact": {"facts": 2}, "reason": "light sleepers"}]}
    out, probs = apply(spec, ov)
    items = out["blocks"][0]["items"]
    cases = [("the fitting place overtakes the higher-rated generic one", probs == [] and [i["name"] for i in items] == ["Lake Farm", "Stylish Villa"]
              and items[0]["score"] == 5.0 and items[0]["rank"] == 1),
             ("table ranks follow the new order", {t["name"]: t["rank"] for t in out["blocks"][1]["items"]} == {"Lake Farm": 1, "Stylish Villa": 2}),
             ("table rows are listed in rank order", [t["name"] for t in out["blocks"][1]["items"]] == ["Lake Farm", "Stylish Villa"])]
    bad = json.loads(json.dumps(ov)); bad["lake farm"][0]["bump"] = 0.9
    cases.append(("an overlay over its weight is refused with fact-guard's reason", any("at most 0.5" in p for p in apply(spec, bad)[1])))
    ghost = {"nowhere": []}
    cases.append(("overlays for a card that doesn't exist are refused", any("no card" in p for p in apply(spec, ghost)[1])))
    raw = {"blocks": [{"type": "cards", "items": [{"name": "X", "score": 4.0, "score_basis": {"formula": "equal-weight-mean-stars"}}]}]}
    cases.append(("overlays on a card with no v2 guest score are refused", any("no rank-score-v2 guest score" in p for p in apply(raw, {"X": [{}]})[1])))
    ok = all(c for _, c in cases)
    for n, c in cases:
        print("PASS" if c else "FAIL", n)
    print(f"SELFTEST {'PASS' if ok else 'FAIL'} ({len(cases)} cases)")
    return 0 if ok else 1


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    if sys.argv[1:] == ["selftest"]:
        return selftest()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec"); ap.add_argument("overlays"); ap.add_argument("--out")
    a = ap.parse_args()
    for need in (a.spec, a.overlays):
        if not Path(need).exists():
            print(f"FAIL {need} does not exist (spec.json comes from stay_spec.py build; fit_overlays.json from the fit scorer)")
            return 1
    spec = json.loads(Path(a.spec).read_text(encoding="utf-8"))
    new, problems = apply(spec, json.loads(Path(a.overlays).read_text(encoding="utf-8")))
    if problems:
        for p in problems:
            print("REFUSED", p)
        print(f"nothing written: {len(problems)} overlay problem(s)")
        return 1
    Path(a.out or a.spec).write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
    n = sum(len((it.get("score_basis") or {}).get("group_fit") or []) for b in new["blocks"] if b.get("type") == "cards" for it in b["items"])
    ranked = [it for b in new["blocks"] if b.get("type") == "cards" for it in b["items"]]
    every = bool(ranked) and all(it.get("score") is not None for it in ranked)
    print(f"applied {n} overlay(s); " + ("cards re-ranked by rank score" if every else "the order is kept because some card has no rank score yet") + f" -> {a.out or a.spec}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
