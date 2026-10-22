"""Shared field-coverage checker: used by BOTH agents wherever code parses an external page or API response whose structure
isn't under this plugin's control and can silently change (a booking platform's search-results JSON can change shape: field names that
worked start returning nothing, with no error, no crash, just quietly-wrong data). A parser that goes silent when its target changes is worse than one that crashes: nothing prompts a fix.

This module knows nothing about any specific site or API. A caller (e.g. listing-search's check_airbnb_coverage.py,
venue-verification's check_fetch.py) supplies the extracted records and a FIELD MANIFEST describing what "healthy" looks
like for its own source; this module just measures the batch against it and reports failures in a form that names the
likely-broken field, not just "something's wrong."
"""
from __future__ import annotations
import re
import statistics
from dataclasses import dataclass, field


@dataclass
class FieldSpec:
    name: str                      # human label, e.g. "listing name"
    getter: callable                # record -> value or None; wrap the current field path here
    min_coverage: float = 0.9       # fraction of records that must have a non-null value
    numeric_range: tuple | None = None   # optional (lo, hi): flag if the MEDIAN of present numeric values falls outside it.
                                          # Only catches a GROSS scale/unit error (cents instead of dollars, the wrong
                                          # order of magnitude) -- it CANNOT tell a field swapped for a different one
                                          # whose values happen to fall in a similar range (verified: swapping a price
                                          # for a review-count in a real batch slipped straight past a (5, 100000) range
                                          # check, since both are small positive numbers). Use `cross_check` for that.
    cross_check: callable | None = None  # optional record -> bool: True if this field's value is consistent with some
                                          # OTHER field on the same record (e.g. "the parsed price actually appears as a
                                          # number inside the raw price-text field"). Reported as its own coverage-style
                                          # measure; this is what actually catches a same-shape field swap, because it
                                          # checks agreement between two independently-sourced values, not a value's
                                          # plausibility in isolation.


@dataclass
class CoverageResult:
    field: str
    coverage: float
    n_present: int
    n_total: int
    ok: bool
    detail: str = ''


def _number(v):
    """A scraped value as a number: 120, '4.8' and '1,234' all count; None when it isn't numeric."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r'[,\s]', '', str(v))
    s = re.sub(r'^[^\d.\-]+', '', s)
    try:
        return float(s)
    except ValueError:
        return None


def _safe(fn, r):
    try:
        return bool(fn(r))
    except Exception:   # a value that makes the cross-check raise is a value that disagrees
        return False


def check(records: list, specs: list[FieldSpec]) -> tuple[bool, list[CoverageResult]]:
    """-> (all_ok, results). Empty `records` is itself a failure (reported as a synthetic result), since an extraction
    that silently returns zero rows is exactly the failure mode this exists to catch, not a vacuous pass."""
    if not records:
        return False, [CoverageResult('(batch)', 0.0, 0, 0, False, 'zero records extracted - the whole batch is empty, not just one field')]
    n = len(records)
    results = []
    all_ok = True
    for spec in specs:
        values = []
        for r in records:
            try:
                v = spec.getter(r)
            except Exception:
                v = None
            values.append(v)
        present = [v for v in values if v is not None and v != '']
        cov = len(present) / n
        ok = cov >= spec.min_coverage
        detail = ''
        if spec.numeric_range and present:
            nums = [n for n in (_number(v) for v in present) if n is not None]
            if not nums:
                ok = False
                detail = f'a numeric range {spec.numeric_range} is set but none of the {len(present)} present values is a number'
            if nums:
                med = statistics.median(nums)
                lo, hi = spec.numeric_range
                if not (lo <= med <= hi):
                    ok = False
                    detail = f'present in {len(present)}/{n} but median value {med} is outside the expected range {spec.numeric_range} - likely reading the wrong field, not a missing one'
        if not ok and not detail:
            detail = f'only {len(present)}/{n} records had a value (need >= {spec.min_coverage:.0%}) - the extraction path for this field may no longer match the source'
        results.append(CoverageResult(spec.name, round(cov, 3), len(present), n, ok, detail))
        all_ok = all_ok and ok
        if spec.cross_check:
            checked = [r for r, v in zip(records, values) if v not in (None, '')]
            agree = sum(1 for r in checked if _safe(spec.cross_check, r))
            xcov = agree / len(checked) if checked else 1.0
            xok = xcov >= spec.min_coverage
            xdetail = '' if xok else f'{agree}/{len(checked)} agreed with the field it should be consistent with - likely reading a different, similarly-shaped field (a swap, not a miss)'
            results.append(CoverageResult(spec.name + ' (cross-check)', round(xcov, 3), agree, len(checked), xok, xdetail))
            all_ok = all_ok and xok
    return all_ok, results


def report(all_ok: bool, results: list[CoverageResult], source_label: str) -> str:
    lines = [f"Coverage check for {source_label}: {'PASS' if all_ok else 'FAIL - possible schema drift, do not trust this batch'}"]
    for r in results:
        mark = 'ok  ' if r.ok else 'FAIL'
        lines.append(f"  [{mark}] {r.field}: {r.n_present}/{r.n_total} ({r.coverage:.0%})" + (f" - {r.detail}" if r.detail else ''))
    return '\n'.join(lines)


def selftest():
    import sys
    cases = []
    spec_ = [FieldSpec('p', lambda r: r.get('p'), min_coverage=0.9, numeric_range=(5, 1000))]
    cases.append(('a string-typed value outside the numeric range fails', not check([{'p': '120000'}] * 10, spec_)[0]))
    cases.append(('string-typed values inside the range pass', check([{'p': '1,200'.replace('1,200', '120')}] * 10, spec_)[0]))
    cases.append(('a range with no numeric value fails', not check([{'p': 'n/a'}] * 10, spec_)[0]))
    cross = [FieldSpec('p', lambda r: r.get('p'), min_coverage=0.9, cross_check=lambda r: int(r['p']) > 0)]
    cases.append(('a cross-check that raises is a disagreement, not a crash', not check([{'p': 'abc'}] * 10, cross)[0]))
    cases.append(('an empty batch fails', not check([], spec_)[0]))
    for name, ok in cases:
        print(('PASS ' if ok else 'FAIL ') + name)
    ok = all(x for _, x in cases)
    print(f"SELFTEST {'PASS' if ok else 'FAIL'} ({len(cases)} cases)")
    return 0 if ok else 1


if __name__ == '__main__':
    import sys
    raise SystemExit(selftest() if sys.argv[1:] == ['selftest'] else 0)
