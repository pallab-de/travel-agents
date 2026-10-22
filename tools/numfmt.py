#!/usr/bin/env python
"""One reader for the numbers a page prints, whatever its locale: 1,299.50 (US/UK), 1.299,50 (Italy, Germany), 1 299,50 (France), 1'299.50
(Switzerland), 1,20,000 (India's lakh grouping), 12,50 (a decimal comma). fact-guard's claim_key, its capture matcher and the listing parsers all
use it, so a price is the same number wherever it is read.

  canon("€1.299,50 per night")   -> "€1299.50 per night"      every number in a text rewritten to plain digits with a dot decimal
  amount("1,20,000")             -> 120000.0                   a single number, or None
  first_amount("1 299 € 2 nights") -> 1299.0                   the first number in a text, or None

  numfmt.py selftest
"""
import re
import sys

_SEP = "  '’"   # no-break space, narrow no-break space, apostrophes: thousands marks in French, Swiss and similar
_CUR = "$¢-¥₠-₿"
# A plain space is a thousands mark only beside a currency or with a decimal comma ("1 299 €", "1 299,50"): "Sleeps 4 200 m" is two numbers.
_RUN = re.compile(r"\d{1,3}(?:[" + re.escape(_SEP) + r"]\d{3})+(?:[.,]\d{1,2})?(?!\d)|\d{1,3}(?: \d{3})+(?:[.,]\d{1,2})?(?!\d)|\d[\d.,]*\d|\d")
_CODES = r"(?:eur|usd|gbp|inr|jpy|chf|aud|cad|aed|thb|sgd|nzd|hkd|cny|rs|rupees?|euros?|dollars?|pounds?)(?![a-z])"
_BESIDE_MONEY = (re.compile(r"(?:[" + _CUR + r"]|(?<![A-Za-z])[A-Z]{3})\s?$"), re.compile(r"^\s?(?:[" + _CUR + r"]|" + _CODES + ")", re.I))


UNIT_AFTER = re.compile(r"\s?(?:(?:km|mi|stars?)(?![a-z])|%|/\s?\d)", re.I)


def _one(run):
    s = re.sub("[ " + re.escape(_SEP) + "]", "", run)
    if re.fullmatch(r"\d{1,2}(?:,\d{2})+,\d{3}(?:\.\d+)?", s):          # 1,20,000 / 1,23,456.50 (lakh grouping)
        return s.replace(",", "")
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", s):                 # 1,299.50
        return s.replace(",", "")
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?", s):             # 1.299,50 (a dot then exactly three digits is a thousands mark)
        return s.replace(".", "").replace(",", ".")
    if re.fullmatch(r"\d+,\d{1,2}", s):                                  # 12,50
        return s.replace(",", ".")
    return s


def _read(m, whole=False):
    run = m.group(0)
    if " " in run and not whole and "," not in run and "." not in run:
        before, after = m.string[:m.start()], m.string[m.end():]
        if not (_BESIDE_MONEY[0].search(before) or _BESIDE_MONEY[1].search(after)):
            return run   # two separate numbers, left as they are
    if re.fullmatch(r"\d{1,3}\.\d{3}", run) and UNIT_AFTER.match(m.string[m.end():]):
        return run   # "1.234 km", "rated 4.123 stars": a decimal, not a thousands mark
    return _one(run)


def canon(text, money=False):
    """Every number rewritten to plain digits. `money=True` for a text that is a price: a plain space is then always a thousands mark."""
    return _RUN.sub(lambda m: _read(m, whole=money), str(text))


def first_amount(text, money=False):
    """The first number in a text. `money=True` when the text is an amount already cut out of a price: a plain space then is a thousands mark ('1 500')."""
    m = _RUN.search(str(text))
    if not m:
        return None
    n = re.search(r"\d+(?:\.\d+)?", _read(m, whole=money))
    return float(n.group(0)) if n else None


def amount(text):
    """The number a text is, entirely ('1.234,50'), or None if it is not one number."""
    s = str(text).strip()
    m = _RUN.fullmatch(s)
    try:
        return float(_read(m, whole=True)) if m else None
    except ValueError:
        return None


def selftest():
    cases = {
        "1,299.50": 1299.5, "1.299,50": 1299.5, "1 299,50": 1299.5, "1 299,50": 1299.5, "1'299.50": 1299.5, "1’234": 1234.0,
        "1,20,000": 120000.0, "1,23,456.50": 123456.5, "12,50": 12.5, "12.50": 12.5, "1.000": 1000.0, "1,200": 1200.0, "4.7": 4.7, "299": 299.0,
    }
    bad = [(k, amount(k), v) for k, v in cases.items() if amount(k) != v]
    ok = [canon("€1.299,50 per night") == "€1299.50 per night", canon("from 1 299 €") == "from 1299 €",
          canon("₹1,20,000 for 2 nights") == "₹120000 for 2 nights", canon("Rated 4.7 out of 5 from 1,041 reviews") == "Rated 4.7 out of 5 from 1041 reviews",
          canon("2 nights 4 guests") == "2 nights 4 guests", canon("1.234 km from the old town") == "1.234 km from the old town", canon("1.299 EUR") == "1299 EUR", canon("Sleeps 4 200 m from the beach") == "Sleeps 4 200 m from the beach", canon("1 299 EUR") == "1299 EUR", canon("9:05 PM") == "9:05 PM", canon("2030-05-06") == "2030-05-06",
          first_amount("$210 1 night") == 210.0, first_amount("no digits") is None]
    for b in bad:
        print("FAIL amount", b)
    for i, c in enumerate(ok):
        if not c:
            print("FAIL case", i)
    print("SELFTEST PASS" if not bad and all(ok) else "SELFTEST FAIL")
    return 0 if not bad and all(ok) else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["selftest"]:
        sys.exit(selftest())
    print(__doc__)
    sys.exit(0 if sys.argv[1:] in (["-h"], ["--help"]) else 1)
