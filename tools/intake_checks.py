#!/usr/bin/env python
"""Checks both intake validators share, so they cannot drift apart.

  looks_like_street_address(text)   True for a house, flat or street address: the starting point is published in every
                                    drive-time link on the report, so it must be an area, a station or a landmark.
  is_placeholder(text)              True for a stand-in ('TBD', 'null', '<city>', '{city}'): not an answer.

  intake_checks.py selftest
"""
import re
import sys

# Words that name a place people go to, not a street. "Central Chowk metro station" is a landmark; "MG Road" is a street.
LANDMARK = re.compile(r"\b(station|stn|terminal|airport|metro|junction|square|plaza|bridge|market|bazaar|fort|temple|mosque|church|palace|park|beach|lake|"
                      r"mall|museum|stadium|university|campus|old town|city centre|city center|downtown|waterfront|harbou?r|port|bus stand|bus stop|bus station)\b", re.I)
# A house, flat or unit marker followed by its number or letter.
UNIT = re.compile(r"\b(?:flat|apt|apartment|house|h\.?\s?no|house\s+number|number|plot|unit|suite|room|villa|door|no\.?)\s*[#:.-]?\s*[\w-]*\d|#\s*\d", re.I)
# A number leading the text ("12 Example Street", "5, Rose Apartments"), other than an ordinal ("42nd Street station").
LEADING_NUMBER = re.compile(r"^\s*(?!\d+(?:st|nd|rd|th)\b)\d+[\w/-]*\s*[ ,]")
# A street-type word anywhere, in English or the usual local forms.
STREET_WORD = re.compile(r"\b(?:street|st\.?|road|rd\.?|avenue|ave\.?|lane|ln\.?|drive|dr\.?|boulevard|blvd|way|close|crescent|terrace|court|marg|path|gali|nagar|colony|"
                         r"rue|via|calle|avenida|carrer|strada|chaussee|allee|apartments?|residency|enclave|soi|jalan|jl\.|rua|ulica|gasse|weg|straat|laan|gatan|utca)\b|\w+(?:strasse|straße|str\.)", re.I)
# A word then the house number closing a comma segment ("Damrak 1, Amsterdam", "Rua Augusta 100"), and any Chinese, Japanese or Korean text with a number in it.
TRAILING_NUMBER = re.compile(r"(?:^|,)\s*[^\W\d_][\w' .-]*[^\W\d_]\s+\d{1,4}[a-z]?\s*(?:,|$)", re.I)
NOT_A_HOUSE = re.compile(r"\b(?:sector|block|phase|terminal|gate|platform|route|exit|district|kreis|zone|ward|pier|cluster|arrondissement|bezirk|distrito|quartier)\b", re.I)
ORDINAL_CROSS = re.compile(r"\b\d+(?:st|nd|rd|th)\s+(?:cross|main)\b", re.I)   # Bengaluru-style "4th Cross"
CJK_NUMBERED = re.compile(r"(?=.*[぀-ヿ㐀-鿿가-힯])(?=.*(?:\d+\s*[-ー−]\s*\d+|\d+\s*(?:丁目|番地|番|号|號|호|번지)))")
POSTCODE = re.compile(r"\b\d{5,6}\b|\b[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}\b")


# A landmark word right before a street word is a street's name ("Station Road", "Park Street", "Church Lane"), not a landmark.
NAMED_STREET = re.compile(r"\b(?:" + LANDMARK.pattern[3:-3] + r")\s+(?:street|st\.?|road|rd\.?|avenue|ave\.?|lane|ln\.?|drive|dr\.?|boulevard|blvd|way|close|crescent|terrace|court|marg|path|gali)\b", re.I)


def looks_like_street_address(text):
    t = str(text or "")
    if UNIT.search(t) or LEADING_NUMBER.search(t) or POSTCODE.search(t) or CJK_NUMBERED.match(t) or ORDINAL_CROSS.search(t):
        return True
    if any(not LANDMARK.search(m.group(0)) and not NOT_A_HOUSE.search(m.group(0)) for m in TRAILING_NUMBER.finditer(t)):   # judged on the comma segment that ends in the number
        return True
    # A street word makes it a street, unless that word is part of a landmark's name ("42nd Street station", "Church Street Station"): a landmark right after it.
    for m in STREET_WORD.finditer(t):
        if m.group(0).lower() in ("st", "st.") and (not t[:m.start()].strip(" ,") or t[:m.start()].rstrip().endswith(",") or re.search(r"\b(?:near|opposite|nearby|by|at|cross|next to|behind)$", t[:m.start()].rstrip(), re.I)) and re.match(r"[\s.]+[A-Z]", t[m.end():]):
            continue   # "St Pancras", "St. Mark's Square": a saint
        if not re.match(r"\W+(?:\w+\W+)?(?:" + LANDMARK.pattern[3:-3] + r")\b", t[m.end():], re.I):
            return True
    return False


# 'none' and 'no' are real answers to some questions (no pets), so the caller says when a bare 'none' is a stand-in.
_PLACEHOLDER = (r"tbd\.?|tbc\.?|tba\.?|todo|null|nil\.?|n/?a\.?|n\.a\.?|not applicable|unsure|not sure|\?+|x+|-+|\.+|…|none yet|unknown|placeholder|fill me in|to be (?:decided|confirmed|determined|announced)"
                r"|<.*>|\[.*\]|\{.*\}|undecided|not sure(?: yet)?|don'?t know|unknown(?: yet)?|lorem ipsum.*|[–—]+|(?:tbd|tba|tbc)\b(?:\s*[/,-].*)?")
BRACKETED = re.compile(r"<[^<>\s]+>|\{[^{}\s]+\}")
PLACEHOLDER = re.compile(r"^\s*(?:" + _PLACEHOLDER + r")\s*$", re.I)
PLACEHOLDER_STRICT = re.compile(r"^\s*(?:" + _PLACEHOLDER + r"|none|no answer|nothing)\s*$", re.I)


def is_placeholder(text, strict=False):
    t = str(text or "")
    return bool((PLACEHOLDER_STRICT if strict else PLACEHOLDER).match(t) or BRACKETED.search(t))


def selftest():
    bad = ["Lake View Road", "Temple View Road", "Airport Link Road", "Palace Heights Lane", "Fort View Avenue", "University Main Road", "Station Road", "Park Street", "Church Lane", "Beach Road", "Market Street", "Bridge Street", "House number 12", "4th Cross, Indiranagar", "12 Example Street, Valmora", "Flat 4B, Rose Apartments, MG Road", "Apt 5, 12 Rue de Rivoli", "Hauptstrasse 5", "House No. 12, Sector 4",
           "5, Rose Apartments", "Plot 45 Elm Hills", "221B Baker Street", "MG Road", "Calle Mayor 3", "560001 Bangalore", "SW1A 1AA", "Gali no 4", "Via Roma", "Kurfürstendamm 21, Berlin", "Damrak 1, Amsterdam", "Rua Augusta 100, Lisboa", "Nevsky Prospect 20", "Sukhumvit Soi 11", "Jalan Sudirman 5", "東京都千代田区丸の内1-9-1", "Baker St, London", "Oxford St London", "Damrak 1, opposite Centraal Station"]
    good = ["the main bus stand", "Hillcrest main area", "Church Street Station", "Valmora old town", "Central Chowk metro station", "42nd Street station", "Valmora main station", "Mirabel", "Gachibowli", "Lake Harbour waterfront",
            "Old Fort Valmora", "Brindle Cross", "Heathrow Airport", "St Pancras International", "St. Mark's Square", "St Kilda", "Sector 17 Chandigarh", "HSR Layout", "Jayanagar 4th Block", "Terminal 3", "Near St Mary's Church", "King's Cross St Pancras", "District 1, Ho Chi Minh City", "Pier 39", "Kreis 4, Zurich", "Potsdamer Platz", "Prospect Heights", "Paseo del Prado", "\u6210\u7530\u7a7a\u6e2f \u7b2c1\u30bf\u30fc\u30df\u30ca\u30eb"]
    holders = ["TBA", "not applicable", "NIL.", "TBD.", "unsure", "TBD", "TBC", "null", "None yet", "...", "placeholder", "{city}", "[city]", "<city>", "N/A.", "n.a.", "to be decided", "?", "x", "Undecided", "not sure yet", "TBD / TBA", "tbd - will decide", "City: {city}", "<city> area", "—", "Lorem ipsum dolor"]
    fine = ["Valmora", "none", "Dietary: none"]
    cases = [("street addresses are caught", all(looks_like_street_address(x) for x in bad), [x for x in bad if not looks_like_street_address(x)]),
             ("areas, stations and landmarks pass", not any(looks_like_street_address(x) for x in good), [x for x in good if looks_like_street_address(x)]),
             ("stand-ins are caught", all(is_placeholder(x) for x in holders), [x for x in holders if not is_placeholder(x)]),
             ("real answers pass; a bare 'none' is a placeholder only where asked", not any(is_placeholder(x) for x in fine) and is_placeholder("None", strict=True), [])]
    failed = 0
    for name, ok, detail in cases:
        print(("PASS " if ok else "FAIL ") + name + (f" {detail}" if not ok else ""))
        failed += not ok
    print("SELFTEST PASS (%d cases)" % len(cases) if not failed else "SELFTEST FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "selftest":
        sys.exit(selftest())
    print(__doc__)
    sys.exit(0 if sys.argv[1:] in (["-h"], ["--help"]) else 1)
