#!/usr/bin/env python
"""Build one self-contained ranked report from the generic engine and a data spec.

The engine (page.html) knows nothing about the subject. A project supplies a spec JSON with:
  design   brand, ethos, two palettes (light and dark), two font families. Required.
  blocks   an ordered list: hero (first, with an https photo), notice, cards, table, chart, method, day_plan,
           transport_leg, booking_ledger, decision_log, research_notes, cost_comparison (schemas: top of page.html).
  footer   one line.
The build (1) refuses a spec that breaks the design, copy or sorting rules, (2) downloads every photo into an
assets folder beside the report (no hot-linking: a photo that cannot be fetched refuses the build), and (3) writes
one HTML file with the palette and fonts in its CSS.

  python build_page.py spec.json out.html --review review.json --spot spot.json [--rail rail.json] [--store captures]   (captures/ beside the spec is used when --store is omitted)

Block, design and copy rules: skills/report-builder/SKILL.md. Block schemas: top of the <script> in page.html.
"""
import copy
import hashlib
import io
import json
import re
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent

HEX = re.compile(r'#[0-9a-fA-F]{6}')
FONT_NAME = re.compile(r'[A-Za-z][A-Za-z ]{1,40}')
LOCAL_IMAGE = re.compile(r'[^:/\\]+\.(jpe?g|png|webp)', re.I)
LOOKS_NUMERIC = re.compile(r'\s*[€$£₹]?\s*-?\d[\d,]*(\.\d+)?\s*(%|€|\$|£|₹)?\s*')
PALETTE_KEYS = ['bg', 'surface', 'ink', 'mute', 'line', 'accent', 'accent_ink', 'chip', 'good', 'warn', 'bad',
                'eyebrow', 'on_image', 'image_overlay']
# (foreground, background, minimum contrast ratio). Text needs 4.5 (WCAG AA). The accent is also the colour of small link
# and rank text on the page and the cards, so it is held to 4.5 there too; the status colours are used as small text on chips.
CONTRAST = [
    ('ink', 'bg', 4.5), ('ink', 'surface', 4.5), ('ink', 'chip', 4.5), ('mute', 'bg', 4.5), ('mute', 'surface', 4.5),
    ('accent_ink', 'accent', 4.5), ('accent', 'bg', 4.5), ('accent', 'surface', 4.5), ('good', 'surface', 4.5), ('warn', 'surface', 4.5),
    ('bad', 'surface', 4.5), ('good', 'chip', 4.5), ('warn', 'chip', 4.5), ('bad', 'chip', 4.5),
    ('on_image', 'image_overlay', 4.5), ('eyebrow', 'image_overlay', 4.5),
]
# Copy limits in words. A report reads as a headline, a subtitle and short tiles, not paragraphs.
COPY_LIMITS = {
    'eyebrow': 12, 'title': 8, 'lead': 32, 'kpi_label': 10, 'notice_title': 6, 'row': 26,
    'intro': 32, 'verdict': 28, 'pro': 14, 'watch': 14, 'method_item': 32, 'footer': 30,
    'stop_note': 22, 'leg_reason': 22, 'booking_note': 18,  # itinerary blocks
    'log_entry': 30, 'note_finding': 36, 'mechanic': 26,
}
TILE_COUNT = (3, 6)
SHINGLE = 8  # a run of 8 words repeated in two places is duplicated copy


class BuildError(Exception):
    pass


def js(obj):
    # Keep the JSON safe to embed inside a <script> element: no '<' at all (so '</script>' and '<!--<script>' cannot end or
    # change the element) and none of the separators JavaScript treats as line breaks. < is valid JSON for '<'.
    out = json.dumps(obj, ensure_ascii=False)
    for ch in ('<', '>', '&', ' ', ' '):
        out = out.replace(ch, '\\u%04x' % ord(ch))
    return out


def run_gate(spec_path, review_path, spot_path, rail_path=None, store_path=None):
    """Refuse to build unless fact-guard's gate passes. Fail-closed: no review means no report."""
    sys.path.insert(0, str(HERE.parents[2] / 'fact-guard' / 'scripts'))
    import guard  # travel-agents/skills/fact-guard/scripts/guard.py
    spec_bytes = Path(spec_path).read_bytes()
    review = json.loads(Path(review_path).read_text(encoding='utf-8'))
    spot = json.loads(Path(spot_path).read_text(encoding='utf-8')) if spot_path else None
    rail = json.loads(Path(rail_path).read_text(encoding='utf-8')) if rail_path else None
    fails, warns, rows = guard.gate(json.loads(spec_bytes), review, spot, spec_bytes, rail, guard.sha(review_path),
                                    spot_store=store_path, base_dir=Path(spec_path).parent)
    for w in warns:
        print('WARN', w)
    if fails:
        for f in fails:
            print('FAIL', f)
        sys.exit(f'build refused: fact-guard gate failed ({len(fails)} problem(s))')
    print(f'fact-guard gate PASS ({len(rows)} claims)')


BLOCK_REQUIRED = {
    'hero': ['title', 'image', 'image_alt'],
    'notice': ['title', 'items'],
    'cards': ['id', 'title', 'items'],
    'table': ['id', 'title', 'columns', 'items'],
    'method': ['id', 'title', 'items'],
    # Itinerary blocks. They are additive: a spec written without them needs no change.
    'day_plan': ['id', 'title', 'days'],
    'transport_leg': ['id', 'title', 'legs'],
    'booking_ledger': ['id', 'title', 'bookings'],
    'chart': ['id', 'title', 'source', 'x', 'y'],
    'decision_log': ['id', 'title', 'entries'],
    'research_notes': ['id', 'title', 'notes'],
    'cost_comparison': ['id', 'title', 'options', 'recommendation'],
}


# A stop's type; flight/train/reservation/ticket/event are fixed-time facts fact-guard checks as critical.
STOP_TYPES = {'flight', 'train', 'reservation', 'ticket', 'event', 'transfer', 'meal', 'sight', 'stay', 'free'}
STOP_STATUS = {'booked', 'held', 'open', 'cancelled', 'rejected'}   # a rejected option stays listed, with its reason
LOG_STATUS = {'decided', 'open', 'superseded'}
LOCALE = re.compile(r'[a-z]{2,3}(-[A-Za-z0-9]{2,8})*')
CURRENCY = re.compile(r'[A-Z]{3}')


def words(text):
    return len(re.findall(r'\S+', str(text or '')))


def luminance(hex_colour):
    r, g, b = (int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lin = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def validate_design(spec):
    """The design is a required part of the spec: brand, ethos, two palettes that meet contrast, two font families."""
    errors = []
    d = spec.get('design')
    if not isinstance(d, dict):
        return ['spec.design is missing: give the report a brand, an ethos, a palette (light and dark) and two fonts']
    if not str(d.get('brand', '')).strip():
        errors.append('design.brand is empty (the wordmark in the top bar)')
    if len(str(d.get('ethos', '')).strip()) < 40:
        errors.append('design.ethos must say, in a sentence or two, what the report is for and how it should feel (40+ characters)')
    fonts = d.get('fonts') or {}
    for role in ('display', 'body'):
        name = fonts.get(role, '')
        if not FONT_NAME.fullmatch(str(name)):
            errors.append(f'design.fonts.{role} must be a Google Fonts family name (letters and spaces), got {name!r}')
    for variant in ('palette', 'palette_dark'):
        p = d.get(variant)
        if not isinstance(p, dict):
            errors.append(f'design.{variant} is missing')
            continue
        for k in p:
            if k not in PALETTE_KEYS:
                errors.append(f'design.{variant}.{k} is not a palette token (known: {PALETTE_KEYS})')
        bad_keys = [k for k in PALETTE_KEYS if not HEX.fullmatch(str(p.get(k, '')))]
        for k in bad_keys:
            errors.append(f'design.{variant}.{k} must be a #rrggbb colour, got {p.get(k)!r}')
        if bad_keys:
            continue
        for fg, bg, want in CONTRAST:
            ratio = contrast(p[fg], p[bg])
            if ratio < want:
                errors.append(f'design.{variant}: {fg} {p[fg]} on {bg} {p[bg]} has contrast {ratio:.2f}, needs {want}')
    return errors


def numberOf(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r'-?\d+(?:\.\d+)?', str(v).replace(',', ''))
    return float(m.group()) if m else None


def copy_fields(spec):
    """Every piece of reader-facing copy, with where it sits and which limit it must meet."""
    out = []
    for i, b in enumerate(spec.get('blocks', [])):
        t = b.get('type')
        where = f'blocks[{i}] ({t})'
        if t == 'hero':
            out += [(where + ' eyebrow', b.get('eyebrow', ''), 'eyebrow'), (where + ' title', b.get('title', ''), 'title'),
                    (where + ' lead', b.get('lead', ''), 'lead')]
            out += [(where + f' kpi {k}', kp.get('label', ''), 'kpi_label') for k, kp in enumerate(b.get('kpis', []))]
        elif t == 'notice':
            out.append((where + ' title', b.get('title', ''), 'notice_title'))
            out += [(where + f' row {k + 1}', x, 'row') for k, x in enumerate(b.get('items', []))]
        elif t == 'cards':
            out.append((where + ' intro', b.get('intro', ''), 'intro'))
            for k, it in enumerate(b.get('items', [])):
                n = it.get('name', k)
                out.append((f'{where} {n} verdict', it.get('verdict', ''), 'verdict'))
                out += [(f'{where} {n} why', x, 'pro') for x in it.get('pros', [])]
                out += [(f'{where} {n} watch', x.get('text', '') if isinstance(x, dict) else x, 'watch') for x in it.get('watch', [])]
        elif t in ('table', 'chart'):
            out.append((where + ' intro', b.get('intro', ''), 'intro'))
        elif t == 'method':
            out += [(where + f' item {k + 1}', x, 'method_item') for k, x in enumerate(b.get('items', []))]
        elif t == 'day_plan':
            for d in b.get('days', []):
                for s in d.get('stops', []):
                    if s.get('note'):
                        out.append((f"{where} {d.get('label', d.get('date', '?'))} / {s.get('name')}", s['note'], 'stop_note'))
        elif t == 'transport_leg':
            for leg in b.get('legs', []):
                rec = leg.get('recommendation') or {}
                out += [(f"{where} {leg.get('label')} reason", r, 'leg_reason') for r in rec.get('reasons', [])]
        elif t == 'booking_ledger':
            for bk in b.get('bookings', []):
                if bk.get('note'):
                    out.append((f"{where} {bk.get('name')}", bk['note'], 'booking_note'))
        elif t == 'decision_log':
            out.append((where + ' intro', b.get('intro', ''), 'intro'))
            for e in b.get('entries', []):
                out += [(f"{where} {e.get('title')} decision", e.get('decision', ''), 'log_entry'),
                        (f"{where} {e.get('title')} why", e.get('why', ''), 'log_entry')]
        elif t == 'research_notes':
            out.append((where + ' intro', b.get('intro', ''), 'intro'))
            out += [(f"{where} {n.get('topic')}", n.get('finding', ''), 'note_finding') for n in b.get('notes', [])]
        elif t == 'cost_comparison':
            out.append((where + ' intro', b.get('intro', ''), 'intro'))
            out += [(f"{where} {o.get('label')} mechanic", o.get('mechanic', ''), 'mechanic') for o in b.get('options', [])]
            out += [(where + ' reason', r, 'leg_reason') for r in (b.get('recommendation') or {}).get('reasons', [])]
    if spec.get('footer'):
        out.append(('footer', spec['footer'], 'footer'))
    return out


def validate_copy(spec):
    """Copy rules: short headline, short subtitle, short tiles, a sensible tile count, no duplicated sentences
    between places. The engine renders what it is given, so a long paragraph in a tile breaks the layout."""
    errors = []
    fields = copy_fields(spec)
    for where, text, kind in fields:
        if words(text) > COPY_LIMITS[kind]:
            errors.append(f'{where}: {words(text)} words; the {kind} limit is {COPY_LIMITS[kind]}. Shorten it.')
    for b in spec.get('blocks', []):
        if b.get('type') == 'notice':
            n = len(b.get('items', []))
            if not TILE_COUNT[0] <= n <= TILE_COUNT[1]:
                errors.append(f'notice has {n} rows; use {TILE_COUNT[0]} to {TILE_COUNT[1]}')
    seen, whole = {}, set()
    for where, text, kind in fields:
        ws = re.findall(r"[\w'’€£.%-]+", str(text).lower())
        if kind in ('watch', 'pro'):   # the same short caveat on two places (a flag the rows share) is repeated on purpose
            if (kind, tuple(ws)) in whole:
                continue
            whole.add((kind, tuple(ws)))
        for i in range(len(ws) - SHINGLE + 1):
            key = ' '.join(ws[i:i + SHINGLE])
            if key in seen and seen[key][0] != where:
                errors.append(f'the same {SHINGLE}-word phrase appears in {seen[key][0]} and {where}: "{key[:60]}..." '
                              f'Say it once.')
                break
            seen.setdefault(key, (where,))
    return errors


RESERVED_IDS = {'top', 'brand', 'topnav', 'spec', 'page'}   # ids the page itself uses
LIST_OF_OBJECTS = {'hero': ['kpis'], 'cards': ['items'], 'table': ['items', 'columns'], 'day_plan': ['days'], 'transport_leg': ['legs'],
                   'booking_ledger': ['bookings'], 'decision_log': ['entries'], 'research_notes': ['notes'], 'cost_comparison': ['options']}
LIST_OF_TEXT = {'notice': ['items'], 'method': ['items']}


def isnum(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float('inf')


def _texts(v):
    return isinstance(v, list) and all(isinstance(x, str) or (isinstance(x, dict) and isinstance(x.get('text', ''), str)) for x in v)


def card_shape_errors(where, it):
    errs = []
    if not isinstance(it, dict):
        return [f'{where}: a card must be an object']
    for key in ('pros', 'watch'):
        if key in it and not _texts(it[key]):
            errs.append(f'{where}: {key} must be a list of text (or {{"text": ...}} objects)')
    if 'badges' in it and not (isinstance(it['badges'], list) and all(isinstance(x, dict) and isinstance(x.get('text', ''), str) for x in it['badges'])):
        errs.append(f'{where}: badges must be a list of {{"text", "tone"}} objects')
    if 'facts' in it and not (isinstance(it['facts'], list) and all(isinstance(x, dict) and 'label' in x and 'value' in x for x in it['facts'])):
        errs.append(f'{where}: facts must be a list of {{"label", "value"}} objects')
    if 'images' in it and not (isinstance(it['images'], list) and all(isinstance(x, str) for x in it['images'])):
        errs.append(f'{where}: images must be a list of urls')
    if 'dossier' in it:
        if not (isinstance(it['dossier'], list) and all(isinstance(x, dict) for x in it['dossier'])):
            return errs + [f'{where}: dossier must be a list of section objects']
        for k, sec in enumerate(it['dossier']):
            w = f'{where} dossier[{k}] ({sec.get("title")})'
            kind = sec.get('kind')
            if not sec.get('title') or not isinstance(sec.get('title'), str):
                errs.append(f'{w}: every section needs a title')
            if not kind and not sec.get('not_found'):
                errs.append(f'{w}: a section needs a kind (facts, ratings, reviews, photos) or a not_found reason')
            if kind == 'facts':
                for r, row in enumerate(sec.get('rows') or []):
                    if not (isinstance(row, (list, tuple)) and len(row) == 2 and all(isinstance(x, (str, int, float)) and not isinstance(x, bool) for x in row)):
                        errs.append(f'{w}: row {r} must be exactly [label, value]; the page shows it and the gate checks it only in that shape')
            elif kind in ('ratings', 'reviews'):
                if not (isinstance(sec.get('items', []), list) and all(isinstance(x, dict) for x in sec.get('items', []))):
                    errs.append(f'{w}: items must be a list of objects')
            elif kind == 'photos':
                if not (isinstance(sec.get('images', []), list) and all(isinstance(x, str) for x in sec.get('images', []))):
                    errs.append(f'{w}: images must be a list of urls')
            elif kind is not None:
                errs.append(f'{w}: unknown kind {kind!r}')
    return errs


def shape_errors(spec):
    """Types before anything else reads the spec: a list where the page maps over a list, an object where it reads
    keys, a number where it does arithmetic. Without this a malformed block blanks the page or crashes the build."""
    if not isinstance(spec, dict) or not isinstance(spec.get('blocks'), list):
        return ['the spec must be an object with a list of blocks']
    errs = []
    for i, b in enumerate(spec['blocks']):
        if not isinstance(b, dict):
            errs.append(f'blocks[{i}] must be an object')
            continue
        t, where = b.get('type'), f'blocks[{i}] ({b.get("type")})'
        if not isinstance(t, str):
            errs.append(f'blocks[{i}]: type must be text')
            continue
        for key in LIST_OF_OBJECTS.get(t, []):
            v = b.get(key, [])
            if not isinstance(v, list) or not all(isinstance(x, dict) for x in v):
                errs.append(f'{where}: {key} must be a list of objects')
        for key in LIST_OF_TEXT.get(t, []):
            v = b.get(key, [])
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                errs.append(f'{where}: {key} must be a list of text lines')
        if 'id' in b and not isinstance(b['id'], str):
            errs.append(f'{where}: id must be text')
        if t == 'cards':
            for j, it in enumerate(b.get('items', []) if isinstance(b.get('items'), list) else []):
                errs += card_shape_errors(f'{where}.items[{j}]', it)
        if t == 'table':
            for c in b.get('columns', []) if isinstance(b.get('columns'), list) else []:
                if isinstance(c, dict) and (not isinstance(c.get('key'), str) or not c.get('key') or not isinstance(c.get('label'), str) or not c.get('label') or c.get('type') not in ('num', 'text', 'cat')):
                    errs.append(f'{where}: every column needs a text key, a text label and a type of num, text or cat ({c.get("key")!r})')
                elif isinstance(c, dict) and c.get('link') and c.get('key') != 'name':
                    errs.append(f'{where}: only the name column can be a link ({c.get("key")!r}): a figure in a link column would escape the checks')
        if t == 'day_plan':
            for d in b.get('days', []) if isinstance(b.get('days'), list) else []:
                if isinstance(d, dict) and (not isinstance(d.get('stops', []), list) or not all(isinstance(x, dict) for x in d.get('stops', []))):
                    errs.append(f'{where}: the stops of a day must be a list of objects')
        if t == 'transport_leg':
            for leg in b.get('legs', []) if isinstance(b.get('legs'), list) else []:
                for m in (_items(leg.get('modes')) if isinstance(leg, dict) else []):
                    if not isinstance(m, dict):
                        errs.append(f'{where}: a mode must be an object')
                        continue
                    for key in ('time_minutes', 'distance_km', 'cost'):
                        if m.get(key) is not None and not isnum(m[key]):
                            errs.append(f'{where}: {leg.get("label")} {m.get("mode")} {key} {m[key]!r} must be a number (minutes, km, an amount)')
                    for sg in _items(m.get('segments')):
                        if isinstance(sg, dict) and sg.get('time_minutes') is not None and not isnum(sg['time_minutes']):
                            errs.append(f'{where}: segment {sg.get("label")!r} time_minutes {sg["time_minutes"]!r} must be a number of minutes')
        if t == 'cost_comparison':
            for o in b.get('options', []) if isinstance(b.get('options'), list) else []:
                for part in ('cash', 'points'):
                    amt = (o.get(part) or {}).get('amount') if isinstance(o, dict) and isinstance(o.get(part) or {}, dict) else None
                    if amt is not None and not isnum(amt):
                        errs.append(f'{where}: {o.get("label")} {part}.amount {amt!r} must be a number')
    return errs + more_shape_errors(spec)


def _is_text(v):
    return isinstance(v, str)


def _list_of(v, kind):
    return isinstance(v, list) and all(isinstance(x, kind) for x in v)


def _items(v):
    return v if isinstance(v, list) else []


def more_shape_errors(spec):
    """The remaining sub-fields the page and the gate read as text, lists or objects. Each wrong type is named here so no
    later step has to guess; the catch-all in validate_spec is only the last net."""
    errs = []

    def need(ok, where, what):
        if not ok:
            errs.append(f'{where}: {what}')

    if 'trip' in spec:
        t = spec['trip']
        need(isinstance(t, dict) and all(_is_text(t.get(k)) for k in ('start', 'end') if t.get(k) is not None), 'trip', 'must be an object with text start and end dates')
    if spec.get('policy') is not None:
        need(isinstance(spec['policy'], dict), 'policy', 'must be an object')
    for key in ('footer', 'title', 'locale', 'as_of'):
        if spec.get(key) is not None:
            need(_is_text(spec[key]), key, 'must be text')
    for i, b in enumerate(spec['blocks']):
        if not isinstance(b, dict):
            continue
        t = b.get('type')
        w = f'blocks[{i}] ({t if isinstance(t, str) else "?"})'
        need(isinstance(t, str), f'blocks[{i}]', 'type must be text')
        for key in ('title', 'eyebrow', 'lead', 'intro', 'kicker', 'image', 'image_alt', 'image_credit', 'source', 'cards'):
            if b.get(key) is not None:
                need(_is_text(b[key]), w, f'{key} must be text')
        for k, kp in enumerate(_items(b.get('kpis'))):
            if isinstance(kp, dict):
                need(_is_text(kp.get('label', '')) and (_is_text(kp.get('value', '')) or isnum(kp.get('value'))), w, f'kpis[{k}] needs a text label and a text or numeric value')
                need(kp.get('derived') is None or isinstance(kp['derived'], dict), w, f'kpis[{k}].derived must be an object')
        if t == 'cards':
            for j, it in enumerate(_items(b.get('items'))):
                if not isinstance(it, dict):
                    continue
                iw = f'{w}.items[{j}]'
                for key in ('name', 'verdict', 'priceNote', 'area', 'url', 'home_url', 'currency'):
                    if it.get(key) is not None:
                        need(_is_text(it[key]), iw, f'{key} must be text')
                sb = it.get('score_basis')
                if sb is not None:
                    need(isinstance(sb, dict), iw, 'score_basis must be an object')
                if isinstance(sb, dict):
                    need(sb.get('formula') is None or _is_text(sb.get('formula')), iw, 'score_basis.formula must be text')
                    for key in ('sources', 'themes', 'group_fit', 'concerns'):
                        need(sb.get(key) is None or _list_of(sb[key], dict), iw, f'score_basis.{key} must be a list of objects')
                    for key in ('themes', 'concerns'):
                        for cn in _items(sb.get(key)):
                            if isinstance(cn, dict):
                                need(_is_text(cn.get('text', '')) and _list_of(cn.get('review_ids', []), (str, int)), iw, f'each of score_basis.{key} needs text and a list of review ids')
                    need(sb.get('guest_score') is None or isnum(sb.get('guest_score')), iw, 'score_basis.guest_score must be a number')
                need(it.get('own_domains') is None or _list_of(it.get('own_domains'), str), iw, 'own_domains must be a list of domains')
                for sec in _items(it.get('dossier')):
                    if isinstance(sec, dict) and sec.get('categories') is not None:
                        need(isinstance(sec['categories'], dict), iw, "a reviews section's categories must be an object")
        if t == 'transport_leg':
            for j, leg in enumerate(_items(b.get('legs'))):
                if not isinstance(leg, dict):
                    continue
                lw = f'{w}.legs[{j}]'
                need(leg.get('modes') is None or _list_of(leg['modes'], dict), lw, 'modes must be a list of objects')
                need(leg.get('label') is None or _is_text(leg['label']), lw, 'label must be text')
                rec = leg.get('recommendation')
                need(rec is None or (isinstance(rec, dict) and _list_of(rec.get('reasons', []), str) and (rec.get('mode') is None or _is_text(rec['mode']))), lw, 'recommendation must be an object with a text mode and a list of text reasons')
                for m in _items(leg.get('modes')):
                    if not isinstance(m, dict):
                        continue
                    need(m.get('mode') is None or _is_text(m['mode']), lw, "a mode's name must be text")
                    need(m.get('segments') is None or _list_of(m['segments'], dict), lw, 'segments must be a list of objects')
                    for key in ('source', 'captured_at', 'currency'):
                        need(m.get(key) is None or _is_text(m[key]), lw, f'{key} must be text')
        if t == 'day_plan':
            for d in _items(b.get('days')):
                if not isinstance(d, dict):
                    continue
                for key in ('date', 'city', 'label'):
                    need(d.get(key) is None or _is_text(d[key]), w, f"a day's {key} must be text")
                for st in _items(d.get('stops')):
                    if isinstance(st, dict):
                        for key in ('name', 'type', 'status', 'time', 'note', 'source', 'url'):
                            need(st.get(key) is None or _is_text(st[key]), w, f"a stop's {key} must be text")
        if t == 'booking_ledger':
            need(b.get('summary') is None or (isinstance(b['summary'], dict) and all(isnum(v) for v in b['summary'].values())), w, 'summary must map currencies to numbers')
            for bk in _items(b.get('bookings')):
                if isinstance(bk, dict):
                    for key in ('name', 'status', 'category', 'check_in', 'check_out', 'cancel_by', 'date', 'currency', 'note', 'source', 'url'):
                        need(bk.get(key) is None or _is_text(bk[key]), w, f"a booking's {key} must be text")
                    need(bk.get('amount') is None or isnum(bk['amount']), w, f"a booking's amount {bk.get('amount')!r} must be a number")
        if t == 'decision_log':
            for e in _items(b.get('entries')):
                if isinstance(e, dict):
                    for key in ('title', 'decision', 'why', 'status'):
                        need(e.get(key) is None or _is_text(e[key]), w, f"an entry's {key} must be text")
        if t == 'research_notes':
            for n in _items(b.get('notes')):
                if isinstance(n, dict):
                    need(n.get('sources') is None or _list_of(n['sources'], dict), w, "a note's sources must be a list of objects")
                    for key in ('topic', 'finding', 'not_verified'):
                        need(n.get(key) is None or _is_text(n[key]), w, f"a note's {key} must be text")
        if t == 'cost_comparison':
            rec = b.get('recommendation')
            need(rec is None or (isinstance(rec, dict) and _list_of(rec.get('reasons', []), str)), w, 'recommendation must be an object with a list of text reasons')
            for o in _items(b.get('options')):
                if isinstance(o, dict):
                    for part in ('cash', 'points'):
                        need(o.get(part) is None or isinstance(o[part], dict), w, f"an option's {part} must be an object")
                    need(o.get('label') is None or _is_text(o['label']), w, "an option's label must be text")
    return errs


def validate_spec(spec):
    """Fail before writing anything: an unknown or malformed block would otherwise render a blank page, and a
    numeric column the reader cannot sort would ship broken."""
    shape = shape_errors(spec)
    if shape:
        return shape
    try:
        return validate_spec_values(spec)
    except (TypeError, AttributeError, KeyError, ValueError, IndexError) as e:   # a type the shape check missed: a named refusal, never a traceback
        return [f"the spec has a field of the wrong type that the build could not read ({type(e).__name__}: {e}); check each block's fields against the block reference"]


def validate_spec_values(spec):
    errors = validate_design(spec) + validate_copy(spec)
    if not isinstance(spec.get('blocks'), list) or not spec['blocks']:
        errors.append('spec.blocks must be a non-empty list')
        return errors
    if spec.get('locale') is not None and not (isinstance(spec['locale'], str) and LOCALE.fullmatch(spec['locale'])):
        errors.append(f"spec.locale {spec['locale']!r} must be a language tag such as 'en-GB' or 'hi-IN'")
    if spec['blocks'][0].get('type') != 'hero':
        errors.append('the first block must be the hero (the photo and the headline)')
    ids = set()
    for i, b in enumerate(spec['blocks']):
        t = b.get('type')
        if t not in BLOCK_REQUIRED:
            errors.append(f'blocks[{i}]: unknown block type {t!r} (known: {sorted(BLOCK_REQUIRED)})')
            continue
        for k in BLOCK_REQUIRED[t]:
            if k not in b:
                errors.append(f'blocks[{i}] ({t}): missing required key {k!r}')
        if t == 'hero':
            img = str(b.get('image', ''))
            if not (img.startswith('https://') or LOCAL_IMAGE.fullmatch(img)):
                errors.append('hero.image must be an https photo URL or a local image file (jpg, png, webp) beside the spec')
        if 'id' in b:
            if not isinstance(b['id'], str) or not re.fullmatch(r'[a-z][a-z0-9-]*', b['id']) or b['id'] in RESERVED_IDS:
                errors.append(f'blocks[{i}]: id {b["id"]!r} must be lower-case letters, digits and hyphens, and not one of {sorted(RESERVED_IDS)}')
            elif b['id'] in ids:
                errors.append(f'blocks[{i}]: duplicate id {b["id"]!r}')
            else:
                ids.add(b['id'])
        for j, it in enumerate(b.get('items', []) if t in ('cards', 'table') else []):
            if not isinstance(it, dict) or not it.get('name'):
                errors.append(f'blocks[{i}].items[{j}]: every item needs a name')
            if t == 'cards' and (not isinstance(it.get('rank'), int) or isinstance(it.get('rank'), bool)):
                errors.append(f'blocks[{i}].items[{j}] ({it.get("name")}): rank must be an integer')
            if it.get('score') is not None and not isinstance(it.get('score'), (str, int, float)):
                errors.append(f'blocks[{i}].items[{j}]: score must be a number or string')
            for bd in it.get('badges', []) if t == 'cards' else []:
                if bd.get('tone') not in (None, '', 'good', 'warn', 'bad'):
                    errors.append(f'blocks[{i}].items[{j}] ({it.get("name")}): badge tone {bd.get("tone")!r} must be good, warn or bad')
            for sec in it.get('dossier', []) if t == 'cards' else []:
                for img in sec.get('images', []) if sec.get('kind') == 'photos' else []:
                    if not str(img).startswith('https://'):
                        errors.append(f'blocks[{i}].items[{j}] ({it.get("name")}): dossier photos must be https URLs (they are downloaded), got {img!r}')
            for img in it.get('images', []) if t == 'cards' else []:
                if not str(img).startswith('https://'):
                    errors.append(f'blocks[{i}].items[{j}] ({it.get("name")}): card photos must be https URLs (they are downloaded)')
        if t == 'chart':
            src = next((x for x in spec['blocks'] if x.get('type') == 'table' and x.get('id') == b.get('source')), None)
            if src is None:
                errors.append(f'blocks[{i}] (chart): source {b.get("source")!r} is not the id of a table block')
            else:
                num_cols = {c.get('key') for c in src.get('columns', []) if c.get('type') == 'num'}
                for axis in ('x', 'y'):
                    ax = b.get(axis) or {}
                    if not ax.get('label') or ax.get('key') not in num_cols:
                        errors.append(f'blocks[{i}] (chart): {axis} needs a label and the key of a num column of '
                                      f'table {b.get("source")!r} (num columns: {sorted(num_cols)})')
        if t == 'table':
            keys = {c.get('key') for c in b.get('columns', [])}
            if b.get('cards') is not None and not any(x.get('type') == 'cards' and x.get('id') == b['cards'] for x in spec['blocks']):
                errors.append(f'table {b.get("id")}: cards {b["cards"]!r} is not the id of a cards block')
            for k in b.get('group_by') or []:
                if k not in keys:
                    errors.append(f'table {b.get("id")}: group_by key {k!r} is not one of its columns')
            if 'compare' in b and not isinstance(b['compare'], bool):
                errors.append(f'table {b.get("id")}: compare must be true or false')
            if b.get('columns') and all(c.get('hidden') for c in b['columns']):
                errors.append(f'table {b.get("id")}: every column is hidden; show at least one')
            for j, it in enumerate(b.get('items', [])):
                missing = [k for k in keys if k not in it]
                if missing:
                    errors.append(f'table {b.get("id")} row {j}: missing column values {sorted(missing)}')
            for c in b.get('columns', []):
                vals = [it.get(c.get('key')) for it in b.get('items', []) if it.get(c.get('key')) not in (None, '')]
                if c.get('type') != 'num':
                    # Most values are plain amounts ("€209.28", "128 %"): the column sorts as text unless typed num.
                    amounts = sum(1 for v in vals if LOOKS_NUMERIC.fullmatch(str(v)))
                    if vals and amounts >= 0.6 * len(vals):
                        errors.append(f'table {b.get("id")}: column {c.get("key")!r} holds only amounts but is typed '
                                      f'{c.get("type")!r}; type it "num" so it sorts by value')
                    continue
                # A numeric column may hold a few entries that are not numbers ("not available"): they sort last.
                bad = [v for v in vals if numberOf(v) is None]
                if vals and len(bad) > 0.4 * len(vals):
                    errors.append(f'table {b.get("id")}: numeric column {c.get("key")!r} is mostly text ({bad[:3]}); '
                                  f'type it text, or put the numbers in it')
        if t == 'day_plan':
            if not b.get('days'):
                errors.append(f'blocks[{i}] (day_plan): days must be a non-empty list')
            for j, d in enumerate(b.get('days', [])):
                if not d.get('date') and not d.get('label'):
                    errors.append(f'blocks[{i}].days[{j}]: needs a date or a label (or both)')
                if not d.get('stops'):
                    errors.append(f'blocks[{i}].days[{j}]: stops must be a non-empty list (a day with nothing planned still says so, e.g. one stop noting "free day")')
                for k, s in enumerate(d.get('stops', [])):
                    if not s.get('name'):
                        errors.append(f'blocks[{i}].days[{j}].stops[{k}]: every stop needs a name')
                    if s.get('status') and s['status'] not in STOP_STATUS:
                        errors.append(f'blocks[{i}].days[{j}].stops[{k}]: status {s["status"]!r} is not one of {sorted(STOP_STATUS)}')
                    if s.get('status') in ('rejected', 'cancelled') and not s.get('note'):
                        errors.append(f'blocks[{i}].days[{j}].stops[{k}] ({s.get("name")}): a {s["status"]} option needs a note saying why')
                    if s.get('type') and s['type'] not in STOP_TYPES:
                        errors.append(f'blocks[{i}].days[{j}].stops[{k}]: type {s["type"]!r} is not one of {sorted(STOP_TYPES)}')
        if t == 'transport_leg':
            if not b.get('legs'):
                errors.append(f'blocks[{i}] (transport_leg): legs must be a non-empty list')
            for j, leg in enumerate(b.get('legs', [])):
                if not leg.get('label'):
                    errors.append(f'blocks[{i}].legs[{j}]: needs a label (e.g. "Town A to Town B")')
                modes = leg.get('modes') or []
                if not modes:
                    errors.append(f'blocks[{i}].legs[{j}] ({leg.get("label")}): modes must be a non-empty list')
                for k, m in enumerate(modes):
                    stated = [f for f in ('time_minutes', 'cost') if m.get(f) is not None]
                    if stated and not m.get('source'):
                        errors.append(f'blocks[{i}].legs[{j}].modes[{k}] ({m.get("mode")}): states {stated} with no source')
                    if stated and not m.get('captured_at'):
                        errors.append(f'blocks[{i}].legs[{j}].modes[{k}] ({m.get("mode")}): states {stated} with no captured_at')
                    for n, sg in enumerate(m.get('segments') or []):
                        if not sg.get('label') or sg.get('time_minutes') is None or not sg.get('source'):
                            errors.append(f'blocks[{i}].legs[{j}].modes[{k}].segments[{n}]: needs a label, time_minutes and source')
                rec = leg.get('recommendation')
                if not rec or not rec.get('mode'):
                    errors.append(f'blocks[{i}].legs[{j}] ({leg.get("label")}): needs a recommendation naming a mode')
                elif not rec.get('reasons'):
                    errors.append(f'blocks[{i}].legs[{j}] ({leg.get("label")}): recommendation has no stated reasons')
        if t == 'decision_log':
            if not b.get('entries'):
                errors.append(f'blocks[{i}] (decision_log): entries must be a non-empty list')
            for j, e in enumerate(b.get('entries', [])):
                for key in ('title', 'decision'):
                    if not e.get(key):
                        errors.append(f'blocks[{i}].entries[{j}]: missing {key!r}')
                if e.get('status') and e['status'] not in LOG_STATUS:
                    errors.append(f'blocks[{i}].entries[{j}]: status {e["status"]!r} is not one of {sorted(LOG_STATUS)}')
        if t == 'research_notes':
            if not b.get('notes'):
                errors.append(f'blocks[{i}] (research_notes): notes must be a non-empty list')
            for j, n in enumerate(b.get('notes', [])):
                if not n.get('topic') or not n.get('finding'):
                    errors.append(f'blocks[{i}].notes[{j}]: needs a topic and a finding')
                srcs = n.get('sources') or []
                if not srcs:
                    errors.append(f'blocks[{i}].notes[{j}] ({n.get("topic")}): a finding needs at least one source')
                for k, sc in enumerate(srcs):
                    if not str(sc.get('url', '')).startswith('https://') or not sc.get('read_at'):
                        errors.append(f'blocks[{i}].notes[{j}].sources[{k}]: needs an https url and the date it was read')
        if t == 'cost_comparison':
            opts = b.get('options') or []
            if len(opts) < 2:
                errors.append(f'blocks[{i}] (cost_comparison): compare at least two options')
            for j, o in enumerate(opts):
                where = f'blocks[{i}].options[{j}] ({o.get("label")})'
                cash = o.get('cash') or {}
                if not o.get('label') or not o.get('mechanic'):
                    errors.append(f'{where}: needs a label and the mechanic (when and how the money is collected)')
                if cash.get('amount') is None or not CURRENCY.fullmatch(str(cash.get('currency', ''))):
                    errors.append(f'{where}: cash needs a numeric amount and a three-letter currency code')
                if not o.get('source') or not o.get('captured_at'):
                    errors.append(f'{where}: states a price with no source or captured_at')
                if o.get('points') is not None and numberOf((o['points'] or {}).get('amount')) is None:
                    errors.append(f'{where}: points needs a numeric amount')
            rec = b.get('recommendation') or {}
            if rec.get('option') not in [o.get('label') for o in opts]:
                errors.append(f'blocks[{i}] (cost_comparison): recommendation.option must name one of the options')
            if not rec.get('reasons'):
                errors.append(f'blocks[{i}] (cost_comparison): recommendation has no stated reasons')
        if t == 'booking_ledger':
            bookings = b.get('bookings') or []
            if not bookings:
                errors.append(f'blocks[{i}] (booking_ledger): bookings must be a non-empty list')
            for j, bk in enumerate(bookings):
                for key in ('name', 'amount', 'currency', 'status'):
                    if bk.get(key) in (None, ''):
                        errors.append(f'blocks[{i}].bookings[{j}]: missing required key {key!r}')
                if bk.get('currency') and not CURRENCY.fullmatch(str(bk['currency'])):
                    errors.append(f'blocks[{i}].bookings[{j}] ({bk.get("name")}): currency {bk["currency"]!r} must be a three-letter code such as EUR')
                try:
                    float(bk.get('amount', 0))
                except (TypeError, ValueError):
                    errors.append(f'blocks[{i}].bookings[{j}] ({bk.get("name")}): amount {bk.get("amount")!r} is not numeric')
    return errors


def design_css(design):
    """The palette tokens for light (default), dark by OS preference, and dark by explicit choice, plus the fonts."""
    def block(p):
        return ';'.join(f'--{k.replace("_", "-")}:{p[k]}' for k in PALETTE_KEYS if k in p)   # known tokens only
    fonts = design['fonts']
    fam = (f'--font-display:"{fonts["display"]}", Georgia, serif;'
           f'--font-body:"{fonts["body"]}", system-ui, sans-serif;')
    light, dark = block(design['palette']), block(design['palette_dark'])
    return (f':root{{{fam}{light}}}\n'
            f'@media (prefers-color-scheme: dark){{:root:not([data-theme="light"]){{{dark}}}}}\n'
            f':root[data-theme="dark"]{{{dark}}}')


def fonts_links(design):
    names = []
    for role in ('display', 'body'):
        n = design['fonts'][role]
        if n not in names:
            names.append(n)
    fams = '&'.join('family=' + n.replace(' ', '+') + ':wght@400;500;600;700;800' for n in names)
    return ('<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
            f'<link href="https://fonts.googleapis.com/css2?{fams}&display=swap" rel="stylesheet">')


def localise_images(spec, out_path, base_dir):
    """Download (or copy, for a local file beside the spec) every photo into <out>_assets/ and point the spec at it.
    Photos are resized to 1280 px and saved as JPEG. A photo that cannot be fetched refuses the build. Returns the
    rewritten spec and a manifest of source URL, local file, sha256 and size, written beside the photos."""
    from PIL import Image
    out_path = Path(out_path)
    assets = out_path.with_name(out_path.stem + '_assets')
    assets.mkdir(exist_ok=True)
    cache, manifest = {}, []

    def take(src):
        if not src:
            return src
        if src in cache:
            return cache[src]
        if src.startswith('https://'):
            req = urllib.request.Request(src, headers={'User-Agent': 'Mozilla/5.0 (report-builder; photo download)'})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = r.read()
            except Exception as e:  # any failure is the same: the photo is missing
                raise BuildError(f'photo could not be downloaded: {src} ({type(e).__name__}). Hot-linking is not allowed; '
                                 f'fix the URL or supply a file.')
        else:
            path = (Path(base_dir) / src).resolve()
            if not path.is_relative_to(Path(base_dir).resolve()):
                raise BuildError(f'local photo {src!r} is outside the project folder; photos must sit beside the spec')
            if not path.is_file():
                raise BuildError(f'local photo not found: {path}')
            data = path.read_bytes()
        try:
            im = Image.open(io.BytesIO(data)).convert('RGB')
        except Exception as e:
            raise BuildError(f'photo is not an image: {src} ({type(e).__name__})')
        im.thumbnail((1280, 1280))
        name = f'{len(cache) + 1:03d}.jpg'
        im.save(assets / name, 'JPEG', quality=82, optimize=True)
        rel = f'./{assets.name}/{name}'
        cache[src] = rel
        # sha256 is the downloaded source; saved_sha256 is the JPEG written beside the report, which the audit
        # re-hashes so a photo swapped after the build is caught.
        manifest.append({'source': src, 'file': rel, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data),
                         'saved_sha256': hashlib.sha256((assets / name).read_bytes()).hexdigest()})
        return rel

    s = copy.deepcopy(spec)
    for b in s['blocks']:
        if b.get('type') == 'hero':
            b['image'] = take(b.get('image'))
        if b.get('type') == 'cards':
            for it in b.get('items', []):
                it['images'] = [take(u) for u in it.get('images', [])]
                for sec in it.get('dossier', []):
                    if sec.get('kind') == 'photos':
                        sec['images'] = [take(u) for u in sec.get('images', [])]
    (assets / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return s, manifest


def apply_manifest(spec, manifest):
    """The spec as embedded in a built page: every photo replaced by the local file the manifest records for it. The
    audit rebuilds the embedded data this way from spec.json, so edited page data cannot hide behind its own fingerprint."""
    by_src = {m['source']: m['file'] for m in manifest}
    take = lambda u: by_src.get(u, u) if u else u
    s = copy.deepcopy(spec)
    for b in s['blocks']:
        if b.get('type') == 'hero':
            b['image'] = take(b.get('image'))
        if b.get('type') == 'cards':
            for it in b.get('items', []):
                it['images'] = [take(u) for u in it.get('images', [])]
                for sec in it.get('dossier', []):
                    if sec.get('kind') == 'photos':
                        sec['images'] = [take(u) for u in sec.get('images', [])]
    return s


def spec_fingerprint(spec):
    """Fingerprint of the spec as written (before photos are localised). The page records it, so a stale page is detectable."""
    return hashlib.sha256(json.dumps(spec, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def assemble(spec, embedded):
    """The page as text: the engine, the design's CSS and fonts, the fingerprints, and `embedded` (the spec with its photos
    pointing at the local copies). The build and the audit both call this, so the audit can rebuild the page from spec.json
    and the photo manifest and require the built file to equal it."""
    engine = (HERE / 'page.html').read_text(encoding='utf-8')
    for ph in ('/*SPEC*/', '/*DESIGN_CSS*/', '<!--FONTS-->'):
        if ph not in engine:
            raise SystemExit(f'page.html has no {ph} placeholder')
    meta = f'<meta name="spec-fingerprint" content="{spec_fingerprint(spec)}"><meta name="embedded-fingerprint" content="{spec_fingerprint(embedded)}">'
    return (engine.replace('<!--FONTS-->', meta + fonts_links(spec['design']))
                  .replace('/*DESIGN_CSS*/', design_css(spec['design']))
                  .replace('/*SPEC*/', js(embedded)))


def render_page(spec, out_path=None, base_dir=None):
    """The one renderer: the CLI and the pipeline both use this, so a page is never built a different way.
    With out_path, photos are localised beside it; without it, the spec's own URLs are used (preview only)."""
    embedded = spec
    if out_path is not None:
        embedded, _ = localise_images(spec, out_path, base_dir or Path('.'))
    return assemble(spec, embedded)


def main():
    args = sys.argv[1:]
    review = spot = None
    if '--review' in args:
        i = args.index('--review'); review = args[i + 1]; del args[i:i + 2]
    if '--spot' in args:
        i = args.index('--spot'); spot = args[i + 1]; del args[i:i + 2]
    store = None
    if '--store' in args:
        i = args.index('--store'); store = args[i + 1]; del args[i:i + 2]
    rail = None
    if '--rail' in args:
        i = args.index('--rail'); rail = args[i + 1]; del args[i:i + 2]
    if len(args) != 2:
        sys.exit(__doc__)
    if review is None:
        sys.exit('build refused: no --review given. Run fact-guard first (skills/fact-guard/SKILL.md).')
    spec_path, out = args
    if store is None and (Path(spec_path).resolve().parent / 'captures').is_dir():
        store = str(Path(spec_path).resolve().parent / 'captures')   # the gate takes its evidence from the capture store
    if store is None:
        sys.exit('build refused: no capture store. Pass --store, or keep captures/ beside the spec (pipeline.py build does both).')
    spec = json.loads(Path(spec_path).read_text(encoding='utf-8'))
    problems = validate_spec(spec)
    if problems:
        for pr in problems:
            print('SPEC', pr)
        sys.exit(f'build refused: spec has {len(problems)} problem(s)')
    run_gate(spec_path, review, spot, rail, store)
    out = Path(out)
    try:
        html = render_page(spec, out, Path(spec_path).parent)
    except BuildError as e:
        sys.exit(f'build refused: {e}')
    out.write_text(html, encoding='utf-8')
    print('wrote', out, round(out.stat().st_size / 1024), 'KB; photos in', out.stem + '_assets/')


def selftest():
    example = json.loads((HERE.parents[2] / 'fact-guard' / 'examples' / 'spec.json').read_text(encoding='utf-8'))
    clone = lambda x: json.loads(json.dumps(x))
    cases = [('the bundled example validates', validate_spec(clone(example)) == [])]
    t = clone(example); next(b for b in t['blocks'] if b['type'] == 'cards')['items'][0]['badges'] = [{'text': 'x', 'tone': 'x" onmouseover="alert(1)'}]
    cases.append(('a badge tone outside good/warn/bad is refused', any('tone' in e for e in validate_spec(t))))
    t = clone(example); t['design']['palette']['x}</style><script>'] = '#ffffff'
    cases.append(('an unknown palette token is refused', any('not a palette token' in e for e in validate_spec(t))))
    cases.append(('the palette CSS carries known tokens only', '<script>' not in design_css(t['design'])))
    t = clone(example); t['design']['fonts']['display'] = 'A";}'
    cases.append(('a font name with quotes is refused', any('Google Fonts family name' in e for e in validate_spec(t))))
    t = clone(example); next(b for b in t['blocks'] if b['type'] == 'cards')['items'][0]['dossier'] = [{'title': 'Photos', 'kind': 'photos', 'images': ['../x.jpg']}]
    cases.append(('a local dossier photo is refused', any('dossier photos must be https' in e for e in validate_spec(t))))
    emb = js({'footer': '<!--<script>', 'x': '</script>& '})
    cases.append(('embedded JSON carries no raw <, > or & (a <!--<script> in the text cannot blank the page)', not any(c in emb for c in '<>& ') and json.loads(emb)['footer'] == '<!--<script>'))
    t = clone(example); next(b for b in t['blocks'] if b['type'] == 'method')['items'] = 'not a list'
    cases.append(('a block of the wrong shape is refused cleanly, not crashed on', any('list of text lines' in e for e in validate_spec(t))))
    t = clone(example); t['blocks'].append({'type': 'transport_leg', 'id': 'tl', 'title': 'T', 'legs': [{'label': 'A to B', 'from': 'A', 'to': 'B', 'modes': [{'mode': 'road', 'time_minutes': '2 h 45 min'}]}]})
    cases.append(('a time written as text is refused (the page would show NaN)', any('must be a number' in e for e in validate_spec(t))))
    cases.append(('apply_manifest maps a photo source to its local file', apply_manifest({'blocks': [{'type': 'hero', 'image': 'https://x.test/a.jpg'}]}, [{'source': 'https://x.test/a.jpg', 'file': './r_assets/001.jpg'}])['blocks'][0]['image'] == './r_assets/001.jpg'))
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / 'proj').mkdir(); (Path(td) / 'secret.png').write_bytes(b'x')
        try:
            localise_images(clone(example) | {'blocks': [dict(example['blocks'][0], image='../secret.png')]}, Path(td) / 'out.html', Path(td) / 'proj')
            cases.append(('a photo outside the project folder is refused', False))
        except BuildError as e:
            cases.append(('a photo outside the project folder is refused', 'outside the project folder' in str(e)))
    for name, ok in cases:
        print(('PASS ' if ok else 'FAIL ') + name)
    ok = all(x for _, x in cases)
    print(f"SELFTEST {'PASS' if ok else 'FAIL'} ({len(cases)} cases)")
    return 0 if ok else 1


if __name__ == '__main__':
    if sys.argv[1:] == ['selftest']:
        raise SystemExit(selftest())
    main()
