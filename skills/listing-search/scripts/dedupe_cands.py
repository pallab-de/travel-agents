#!/usr/bin/env python
"""Merge the search-result batches into one candidate file: the step between extract_airbnb.js and drive_times.py.

  python dedupe_cands.py cands_raw_1.json cands_raw_2.json ... --out cands.json
  python dedupe_cands.py selftest

Each input is the list extract_airbnb.js returned for one page or sweep:
  [{id, name, type_label, ll, price_text, price_amount, price_currency, rating, review_count, badges}]
The output is {id: record}, one record per listing however many batches found it, in the shape drive_times.py and merge.py
read: {name, type, ll, total, price, price_currency, rating, review_count, gf, added_by}. Where batches disagree the first
batch that has a value wins; `added_by` names the first batch that found the listing, so a sweep's new finds can be counted.
A record with no id or no coordinates is reported, not silently dropped.
"""
import argparse, json, sys
from pathlib import Path

import sys as _sys
if _sys.stdout.encoding and _sys.stdout.encoding.lower() != 'utf-8':   # names in other scripts must print on a Windows pipe too
    _sys.stdout.reconfigure(encoding='utf-8')


def to_cand(r, batch):
    return {"name": r.get("name"), "type": r.get("type_label"), "ll": r.get("ll"), "total": r.get("price_amount"),
            "price": r.get("price_text"), "price_currency": r.get("price_currency"), "rating": r.get("rating"),
            "review_count": r.get("review_count"), "gf": "GUEST_FAVORITE" in (r.get("badges") or []), "added_by": batch}


def dedupe(batches):
    """batches: [(name, [records])] -> ({id: candidate}, [problems])"""
    out, problems = {}, []
    for batch, records in batches:
        for r in records:
            i = str(r.get("id") or "").strip()
            if not i:
                problems.append(f"{batch}: a record has no id ({str(r.get('name'))[:40]!r})")
                continue
            c = to_cand(r, batch)
            if i not in out:
                out[i] = c
                continue
            for k, v in c.items():   # fill what the first batch lacked; never overwrite what it had
                if out[i].get(k) in (None, "") and v not in (None, ""):
                    out[i][k] = v
            out[i]["gf"] = out[i]["gf"] or c["gf"]
    for i, c in out.items():
        if not c.get("ll"):
            problems.append(f"{i}: no coordinates; drive_times.py geocodes it from its name and area, and marks it NO_COORDINATES if that fails")
    return out, problems


def selftest():
    a = [{"id": "1", "name": "Farm A", "type_label": "Farm stay in Valmora", "ll": [1.0, 2.0], "price_text": "€300", "price_amount": 300, "badges": ["GUEST_FAVORITE"]},
         {"id": "2", "name": "Farm B", "type_label": "Villa in Valmora", "ll": None, "price_amount": 250}]
    b = [{"id": "1", "name": "Farm A", "ll": [9.0, 9.0], "rating": 4.8, "review_count": 20}, {"name": "no id"}]
    out, probs = dedupe([("batch1.json", a), ("batch2.json", b)])
    cases = [("one record per id, in the shape merge reads", set(out) == {"1", "2"} and out["1"]["type"] == "Farm stay in Valmora" and out["1"]["total"] == 300),
             ("the first batch's value wins, a later batch fills the gaps", out["1"]["ll"] == [1.0, 2.0] and out["1"]["rating"] == 4.8),
             ("the guest-favourite badge becomes gf", out["1"]["gf"] is True and out["2"]["gf"] is False),
             ("the first finder is recorded", out["1"]["added_by"] == "batch1.json"),
             ("a record with no id, and one with no coordinates, are reported", any("no id" in p for p in probs) and any(p.startswith("2:") for p in probs))]
    for name, ok in cases:
        print(("PASS " if ok else "FAIL ") + name)
    ok = all(x for _, x in cases)
    print(f"SELFTEST {'PASS' if ok else 'FAIL'} ({len(cases)} cases)")
    return 0 if ok else 1


def main():
    if sys.argv[1:] == ["selftest"]:
        return selftest()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("batches", nargs="+"); ap.add_argument("--out", default="cands.json")
    a = ap.parse_args()
    batches = [(Path(p).name, json.loads(Path(p).read_text(encoding="utf-8"))) for p in a.batches]
    out, problems = dedupe(batches)
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for pr in problems:
        print("NOTE", pr)
    print(f"{len(out)} listings from {len(batches)} batch(es) -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
