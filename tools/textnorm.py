"""One text normaliser for matching names across scripts (place_match, google_places): case-folded, accents removed from
Latin letters, punctuation dropped, and every other script (Japanese, Thai, Hindi, Arabic...) kept as it is, so two
different non-Latin names never both reduce to the empty string."""
import re
import unicodedata


LIGATURES = str.maketrans({"\u0142": "l", "\u00f8": "o", "\u0111": "d", "\u0153": "oe", "\u00e6": "ae", "\u00df": "ss", "\u0131": "i"})   # letters NFD cannot split into base + accent


def fold(s):
    s = unicodedata.normalize("NFKC", str(s or "")).casefold().translate(LIGATURES)
    out, prev = [], ""
    for ch in unicodedata.normalize("NFD", s):
        if unicodedata.category(ch) == "Mn" and prev and (ord(prev) < 0x250 or 0x370 <= ord(prev) <= 0x3FF or 0x1F00 <= ord(prev) <= 0x1FFF):   # an accent on a Latin or Greek letter
            continue
        if 0x064B <= ord(ch) <= 0x065F or ord(ch) == 0x0670:   # Arabic vowel marks are optional in writing, so a name with and without them is one name
            continue
        out.append(ch)
        prev = ch
    s = unicodedata.normalize("NFC", "".join(out)).replace("&", " and ").replace("\u2019", "").replace("'", "")
    kept = "".join(ch if (unicodedata.category(ch)[0] in "LMN" or ch == " ") else " " for ch in s)   # letters, marks and digits stay; punctuation and symbols go
    return re.sub(r"\s+", " ", kept).strip()
