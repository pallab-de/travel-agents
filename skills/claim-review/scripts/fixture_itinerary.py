"""Synthetic itinerary (a report-builder spec and its built page) for calibrating a plan reviewer (used by build_fixtures.py; maintainers only).

A fictional two-city trip (Harrowgate, Selmere; Mon 6 - Sat 11 May 2030; two adults and a grandparent who rests 14:00-16:00) written as the generic
engine's blocks - day plan, transport leg, booking ledger, decision log, research notes - plus the raw evidence it was built from: opening hours and
status per venue, drive times, booking records, research notes and the trip brief. The CLEAN spec passes fact-guard's plan and booking checks
(asserted); `plant` seeds known defects and the caller discards the clean version. Elements that are correct but look suspicious are decoys.
"""
import copy, datetime as dt, json
from pathlib import Path

DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']


def hours(open_, close, closed=()):
    return {d: ('closed' if d in closed else f'{open_}-{close}') for d in DAYS}


def world():
    places = {
        'harrowgate-museum': {'status': 'OPERATIONAL', 'hours': hours('09:00', '17:00', ('Mon',)), 'rating': 4.5, 'n': 2140},
        'castle-hill': {'status': 'OPERATIONAL', 'hours': hours('09:00', '17:00'), 'rating': 4.6, 'n': 5300},
        'marrow-market': {'status': 'OPERATIONAL', 'hours': hours('08:00', '14:00', ('Mon', 'Sun')), 'rating': 4.4, 'n': 960},
        'riverside-park': {'status': 'OPERATIONAL', 'hours': hours('06:00', '20:00'), 'rating': 4.5, 'n': 3100},
        'kettle-crumb': {'status': 'OPERATIONAL', 'hours': hours('08:00', '22:00'), 'rating': 4.6, 'n': 812},
        'fennel-house': {'status': 'OPERATIONAL', 'hours': hours('12:00', '23:00', ('Mon',)), 'rating': 4.4, 'n': 230},
        'old-quarter': {'status': 'OPERATIONAL', 'hours': hours('00:00', '23:59'), 'rating': 4.3, 'n': 1800},
        'hotel-marlow': {'status': 'OPERATIONAL', 'hours': hours('00:00', '23:59'), 'rating': 4.4, 'n': 412},
        'selmere-grand': {'status': 'OPERATIONAL', 'hours': hours('00:00', '23:59'), 'rating': 4.5, 'n': 388},
        'harbour-aquarium': {'status': 'OPERATIONAL', 'hours': hours('10:00', '16:00', ('Mon',)), 'rating': 4.5, 'n': 4200},
        'old-fort': {'status': 'OPERATIONAL', 'hours': hours('09:00', '17:00', ('Fri',)), 'rating': 4.7, 'n': 6100},
        'botanical-house': {'status': 'OPERATIONAL', 'hours': hours('10:00', '17:00', ('Mon',)), 'rating': 4.3, 'n': 870},
        'quay-cafe': {'status': 'OPERATIONAL', 'hours': hours('08:00', '20:00'), 'rating': 4.2, 'n': 640},
        'salt-house': {'status': 'OPERATIONAL', 'hours': hours('12:00', '22:00', ('Mon',)), 'rating': 4.5, 'n': 520},
        'pier-grill': {'status': 'OPERATIONAL', 'hours': hours('12:00', '22:00'), 'rating': 4.3, 'n': 410},
        'lighthouse-tour': {'status': 'OPERATIONAL', 'hours': hours('10:00', '16:00', ('Fri', 'Sat', 'Sun')), 'rating': 4.6, 'n': 300},
        'harbour-promenade': {'status': 'OPERATIONAL', 'hours': hours('00:00', '23:59'), 'rating': 4.4, 'n': 2200},
        'hotel-brindle': {'status': 'OPERATIONAL', 'hours': hours('00:00', '23:59'), 'rating': 5.0, 'n': 4},
    }
    dist = [('airport-harrowgate', 'hotel-marlow', 45), ('hotel-marlow', 'old-quarter', 15), ('old-quarter', 'kettle-crumb', 8), ('hotel-marlow', 'castle-hill', 20),
            ('castle-hill', 'marrow-market', 45), ('marrow-market', 'riverside-park', 10), ('riverside-park', 'fennel-house', 12), ('hotel-marlow', 'kettle-crumb', 10),
            ('hotel-marlow', 'station-harrowgate', 10), ('station-harrowgate', 'station-selmere', 135), ('station-selmere', 'selmere-grand', 10),
            ('selmere-grand', 'harbour-promenade', 10), ('harbour-promenade', 'quay-cafe', 5), ('selmere-grand', 'old-fort', 25), ('old-fort', 'salt-house', 15),
            ('selmere-grand', 'botanical-house', 15), ('selmere-grand', 'harbour-aquarium', 20), ('harbour-aquarium', 'quay-cafe', 5), ('selmere-grand', 'salt-house', 12),
            ('selmere-grand', 'quay-cafe', 10), ('selmere-grand', 'pier-grill', 12), ('harbour-promenade', 'pier-grill', 6), ('selmere-grand', 'lighthouse-tour', 30),
            ('selmere-grand', 'airport-selmere', 35), ('botanical-house', 'selmere-grand', 15), ('salt-house', 'botanical-house', 20), ('quay-cafe', 'salt-house', 8)]
    hotels = [dict(id='h1', bid='dec-rename', name='Hotel Marlow', city='Harrowgate', inn='2030-05-06', out='2030-05-08', amount=140, pnr='HM-4471', cancel='2030-05-04'),
              dict(id='h2', name='Selmere Grand', city='Selmere', inn='2030-05-08', out='2030-05-11', amount=210, pnr='SG-9920', cancel='2030-05-06')]
    transit = [dict(id='t1', leg='Flight home to Harrowgate', date='2030-05-06', amount=90, pnr='FL-1101', status='booked'),
               dict(id='t2', bid='dec-confirmed', leg='Train Harrowgate to Selmere', date='2030-05-08', amount=12, pnr='TR-552', status='booked'),
               dict(id='t3', leg='Flight Selmere to home', date='2030-05-11', amount=95, pnr='', status='open')]
    I = lambda t, name, note='', typ='free', **kw: dict(t=t, name=name, note=note, type=typ, **kw)
    days = [
        dict(n=1, date='2030-05-06', city='Harrowgate', stops=[
            I('11:30', 'Land at Harrowgate airport', 'Taxi to the hotel takes about 45 minutes.', 'transfer', place='airport-harrowgate', dur=0),
            I('13:00', 'Check in at Hotel Marlow', 'Lunch in the room.', 'stay', place='hotel-marlow', dur=60),
            I('14:00', 'Rest at the hotel', 'No plans until 16:00.', 'free', place='hotel-marlow', dur=120),
            I('16:30', 'Old Quarter walk', 'A slow walk on level ground.', 'sight', venue='old-quarter', place='old-quarter', dur=90),
            I('18:30', 'Dinner at Kettle & Crumb', 'Preferred; the alternate is Fennel House on day 2.', 'meal', venue='kettle-crumb', place='kettle-crumb', dur=90, meal='dinner', role='preferred')]),
        dict(n=2, date='2030-05-07', city='Harrowgate', stops=[
            I('09:00', 'Breakfast at the hotel', '', 'meal', venue='hotel-marlow', place='hotel-marlow', dur=30, meal='breakfast', role='preferred'),
            I('10:00', 'Castle Hill', 'A gentle loop with benches.', 'sight', venue='castle-hill', place='castle-hill', dur=120),
            I('13:00', 'Lunch at Marrow Market', 'Open 08:00 to 14:00 on Tuesdays.', 'meal', venue='marrow-market', place='marrow-market', dur=60, meal='lunch', role='preferred'),
            I('14:00', 'Rest at the hotel', '', 'free', place='hotel-marlow', dur=120),
            I('16:30', 'Riverside Park', 'Level paths and a river walk.', 'sight', venue='riverside-park', place='riverside-park', dur=90),
            I('19:00', 'Dinner at Fennel House', 'Preferred; the alternate is Kettle & Crumb.', 'meal', venue='fennel-house', place='fennel-house', dur=90, meal='dinner', role='preferred')]),
        dict(n=3, date='2030-05-08', city='Selmere', stops=[
            I('08:30', 'Check out of Hotel Marlow', 'Taxi to the station takes 10 minutes.', 'transfer', place='hotel-marlow', dur=30),
            I('10:00', 'Train to Selmere', 'Takes 2 h 15 min; seats are reserved.', 'train', place='station-harrowgate', dur=0, id='dec-train', leg='Harrowgate to Selmere'),
            I('13:00', 'Check in at Selmere Grand', '', 'stay', place='selmere-grand', dur=60),
            I('14:15', 'Rest at the hotel', '', 'free', place='selmere-grand', dur=105),
            I('16:30', 'Harbour promenade stroll', '', 'sight', venue='harbour-promenade', place='harbour-promenade', dur=60),
            I('18:30', 'Dinner at Pier Grill', 'Preferred; the alternate is Quay Cafe.', 'meal', venue='pier-grill', place='pier-grill', dur=90, meal='dinner', role='preferred')]),
        dict(n=4, date='2030-05-09', city='Selmere', stops=[
            I('09:00', 'Breakfast at the hotel', '', 'meal', venue='selmere-grand', place='selmere-grand', dur=30, meal='breakfast', role='preferred'),
            I('10:00', 'Old Fort', 'Open every day except Friday, so today works.', 'sight', venue='old-fort', place='old-fort', dur=150, id='dec-fort'),
            I('13:00', 'Lunch at Salt House', '', 'meal', venue='salt-house', place='salt-house', dur=60, meal='lunch', role='preferred'),
            I('14:15', 'Rest at the hotel', '', 'free', place='selmere-grand', dur=105),
            I('16:30', 'Botanical House', 'A glasshouse loop.', 'sight', venue='botanical-house', place='botanical-house', dur=60),
            I('19:00', 'Dinner at the hotel restaurant', '', 'meal', venue='selmere-grand', place='selmere-grand', dur=90, meal='dinner', role='preferred')]),
        dict(n=5, date='2030-05-10', city='Selmere', stops=[
            I('09:00', 'Breakfast at the hotel', '', 'meal', venue='selmere-grand', place='selmere-grand', dur=30, meal='breakfast', role='preferred'),
            I('10:00', 'Harbour Aquarium', 'Open 10:00 to 16:00 on Fridays.', 'sight', venue='harbour-aquarium', place='harbour-aquarium', dur=150),
            I('12:45', 'Lunch at Quay Cafe', '', 'meal', venue='quay-cafe', place='quay-cafe', dur=60, meal='lunch', role='preferred'),
            I('14:15', 'Rest at the hotel', '', 'free', place='selmere-grand', dur=105),
            I('16:30', 'Souvenir stroll on the promenade', '', 'sight', venue='harbour-promenade', place='harbour-promenade', dur=60),
            I('19:00', 'Dinner at Salt House', 'Preferred.', 'meal', venue='salt-house', place='salt-house', dur=90, meal='dinner', role='preferred')]),
        dict(n=6, date='2030-05-11', city='Selmere', stops=[
            I('08:30', 'Check out of Selmere Grand', 'Taxi to the airport takes 35 minutes.', 'transfer', place='selmere-grand', dur=60),
            I('10:30', 'Arrive at Selmere airport', '', 'transfer', place='airport-selmere', dur=0),
            I('13:00', 'Flight home', 'Not yet booked; see the open decisions.', 'free', place='airport-selmere', dur=0)])]
    research = "\n".join([
        "# Research notes (verified facts and sources)",
        "- Kettle & Crumb: OPERATIONAL, 4.6 stars from 812 reviews, 08:00-22:00 daily (Google Places lookup, 20 Apr). The LocalEats site shows a 'verified' badge for it; that is the site's badge, we did not verify it ourselves.",
        "- Fennel House: OPERATIONAL, 4.4 from 230, Tue-Sun 12:00-23:00, closed Mondays (Google Places, 20 Apr).",
        "- Harbour Aquarium: OPERATIONAL, 10:00-16:00 Tue-Sun, closed Mondays (Google Places, 21 Apr).",
        "- Old Fort: OPERATIONAL, 09:00-17:00, closed Fridays (Google Places, 21 Apr).",
        "- Pier Grill: OPERATIONAL when checked on 12 Apr. (Re-check before the trip.)",
        "- Quay Cafe: OPERATIONAL, 08:00-20:00 daily (Google Places, 21 Apr).",
        "- Lighthouse Tour: 10:00-16:00 Mon-Thu, closed Fri-Sun (Google Places, 21 Apr).",
        "- Hotel Brindle: 5.0 stars from only 4 reviews (Google Places, 22 Apr); too few to rely on.",
        "- Climate normals for May, Harrowgate and Selmere: highs 14-17 C, showers on about one day in three. (July highs are about 33 C and dry; not relevant to this trip.)",
        "- The flight in (t1) and the train (t2) are booked (see bookings). The return flight is not booked."])
    brief = "\n".join(["# Trip brief", "Group: two adults and a grandparent.", "REST=14:00-16:00  (no activities in this window; rest at the hotel)",
                        "Every meal has a preferred venue plus an alternate. Do not use the same preferred dinner venue on consecutive nights.", "Verify every checkable fact before writing it."])
    bookings = {'h1': {'pnr': 'HM-4471', 'amount': 140, 'cancel_until': '2030-05-04', 'status': 'booked'}, 'h2': {'pnr': 'SG-9920', 'amount': 210, 'cancel_until': '2030-05-06', 'status': 'booked'},
                't1': {'pnr': 'FL-1101', 'amount': 90, 'status': 'booked'}, 't2': {'pnr': 'TR-552', 'amount': 12, 'status': 'booked'}}
    log = [dict(id='dec-museum', date='2030-04-20', title='Skip the museum', decision='Harrowgate Museum is closed on Mondays, so it is skipped; Castle Hill on day 2 covers the same period.', status='decided'),
           dict(id='dec-rating', date='2030-04-12', title='Pick the hotels', decision='Hotel Marlow (4.4, 412 reviews) for Harrowgate and Selmere Grand (4.5, 388), the higher-rated of the two, for Selmere.', status='decided')]
    notes = [dict(id='note-kettle', topic='Kettle & Crumb', finding='Open 08:00 to 22:00 daily; 4.6 stars from 812 reviews.', url='https://places.example.test/kettle-crumb', read_at='2030-04-20'),
             dict(id='note-fennel', topic='Fennel House', finding='Open Tuesday to Sunday, 12:00 to 23:00; 4.4 stars from 230 reviews.', url='https://places.example.test/fennel-house', read_at='2030-04-20'),
             dict(id='note-quay', topic='Quay Cafe', finding='Open 08:00 to 20:00 daily; 4.2 stars from 640 reviews.', url='https://places.example.test/quay-cafe', read_at='2030-04-21'),
             dict(id='note-weather', topic='Weather', finding='May in Harrowgate and Selmere: highs of 14-17 C with showers on about one day in three. Pack layers and a rain cover.', url='https://climate.example.test/normals', read_at='2030-04-22')]
    return {'hotels': hotels, 'transit': transit, 'days': days, 'places': places, 'distances': [{'from': a, 'to': b, 'minutes': m} for a, b, m in dist],
            'research': research, 'brief': brief, 'bookings': bookings, 'log': log, 'notes': notes, 'open_items': ['The return flight from Selmere is not booked yet.'], 'extra': {}}


def label(d):
    d = dt.date.fromisoformat(d)
    return f"{DAYS[d.weekday()]} {d.day} {d.strftime('%b')}"


def stop_id(day, s):
    return s.get('id') or f"day-{day['n']}-{s['t'].replace(':', '')}"


def build_spec(T, design):
    """The itinerary as engine blocks, every row carrying a stable id the reviewer cites."""
    def stop(day, s):
        out = {'id': stop_id(day, s), 'time': s['t'], 'name': s['name'], 'type': s['type']}
        if s.get('note'):
            out['note'] = s['note']
        if s.get('leg'):
            out['leg'] = s['leg']
        return out
    days = [{'id': f"day-{d['n']}", 'date': d['date'], 'label': d.get('label') or label(d['date']), 'city': d['city'],
             'stops': [stop(d, s) for s in d['stops']]} for d in T['days']]
    bookings = []
    for h in T['hotels']:
        b = {'id': h.get('bid') or f"bk-{h['id']}", 'name': h['name'], 'category': 'stay', 'amount': h['amount'], 'currency': 'EUR', 'status': h.get('status', 'booked'),
             'cancel_by': h['cancel'], 'check_in': h['inn'], 'check_out': h['out'], 'note': h.get('note', f"Confirmation {h['pnr']}."),
             'url': f"https://stays.example.test/{h['id']}"}
        bookings.append(b)
    for t in T['transit']:
        b = {'id': t.get('bid') or f"bk-{t['id']}", 'name': t['leg'], 'category': 'transit', 'amount': t['amount'], 'currency': 'EUR', 'status': t.get('badge', t['status']),
             'date': t['date'], 'note': 'Not yet booked.' if t['status'] == 'open' else f"Confirmation {t['pnr']}.", 'url': f"https://tickets.example.test/{t['id']}"}
        bookings.append(b)
    total = T.get('summary_total', sum(b['amount'] for b in bookings))
    notice = T['notice']
    blocks = [
        {'type': 'hero', 'eyebrow': 'Six days, two towns', 'title': 'Harrowgate to Selmere', 'lead': 'A slow week for two adults and a grandparent: rest every afternoon, one train, two hotels.',
         'kpis': [{'value': '6', 'label': 'days', 'derived': {'count': 'plan'}}, {'value': '2', 'label': 'towns', 'derived': {'distinct': 'plan.city'}}], 'image': 'hero-placeholder.png', 'image_alt': 'Abstract placeholder'},
        {'type': 'notice', 'title': 'Before you rely on this', 'items': notice},
        {'type': 'booking_ledger', 'id': 'bookings', 'title': 'Bookings', 'bookings': bookings,
         'summary': {'EUR': total}},
        {'type': 'transport_leg', 'id': 'legs', 'title': 'The train', 'legs': [{'label': 'Harrowgate to Selmere', 'from': 'Harrowgate', 'to': 'Selmere', 'modes': [
            {'mode': 'rail', 'time_minutes': 155, 'source': 'https://rail.example.test/h-s', 'captured_at': '2030-04-20T00:00:00Z', 'segments': [
                {'label': 'Taxi to the station', 'time_minutes': 10, 'source': 'https://maps.example.test/a', 'captured_at': '2030-04-20T00:00:00Z'},
                {'label': 'Train', 'time_minutes': 135, 'main': True, 'source': 'https://rail.example.test/h-s', 'captured_at': '2030-04-20T00:00:00Z'},
                {'label': 'Walk to the hotel', 'time_minutes': 10, 'source': 'https://maps.example.test/b', 'captured_at': '2030-04-20T00:00:00Z'}]}],
            'recommendation': {'mode': 'rail', 'reasons': ['Seats are reserved and the ride is flat.']}}]},
        {'type': 'day_plan', 'id': 'plan', 'title': 'Day by day', 'days': days},
        {'type': 'decision_log', 'id': 'decisions', 'title': 'Decisions',
         'entries': [dict(e) for e in T['log']]},
        {'type': 'research_notes', 'id': 'notes', 'title': 'Research notes', 'notes': [
            {'id': n['id'], 'topic': n['topic'], 'finding': n['finding'], 'sources': [{'name': 'Lookup', 'url': n['url'], 'read_at': n['read_at']}]} for n in T['notes']]},
        {'type': 'method', 'id': 'method', 'title': 'How this was checked', 'items': ['Hours and ratings were looked up on the dates in the notes.', 'Open items are listed under decisions.']}]
    return {'locale': 'en-GB', 'as_of': '2030-04-25', 'trip': {'start': '2030-05-06', 'end': '2030-05-11'}, 'blocks': blocks,
            'footer': 'Synthetic trip for calibration. Not a real itinerary.', 'design': design}


def finish(T):
    """Derived values, recomputed so the clean world is self-consistent."""
    T['notice'] = ['Hours and ratings were read in April 2030.', 'The return flight is not booked yet.', 'The train is the one fixed point of the week.']
    return T


def plant(T, rng):
    T = copy.deepcopy(T)
    D = []

    def rec(cell, code, klass, what, easy=False):
        D.append({'list': 'doc', 'id': cell, 'code': code, 'klass': klass, 'what': what, 'easy': easy})
    days = {d['n']: d for d in T['days']}
    item = lambda n, t: next(i for i in days[n]['stops'] if i['t'] == t)
    days[4]['label'] = 'Fri 9 May'; rec('day-4', 'C3', 'date', 'the heading says Fri 9 May; 9 May 2030 is a Thursday', easy=True)
    T['summary_total'] = 1000 + sum(h['amount'] for h in T['hotels']) + sum(t['amount'] for t in T['transit'])
    rec('bookings', 'C3', 'sum', 'the ledger total is 1,000 above the sum of the booking amounts', easy=True)
    T['transit'][2]['badge'] = 'booked'; rec('bk-t3', 'C1', 'status', 'status says booked; the note and bookings.json say the return flight is not booked')
    item(3, '08:30')['name'] = 'Check out of Marlow House'; rec('day-3-0830', 'C2', 'name', 'the hotel is called Marlow House here and Hotel Marlow everywhere else')
    item(2, '13:00')['t'] = '10:30'; rec('day-2-1030', 'C3', 'time', 'lunch at 10:30 leaves 30 min after a 120-min Castle Hill visit; the drive is 45 min (distances.json)')
    T['hotels'][1]['cancel'] = '2030-05-10'; rec('bk-h2', 'C3', 'date', 'free cancellation until 10 May is after the 8 May check-in (and bookings.json says 6 May)', easy=True)
    item(5, '16:30')['t'] = '14:30'; item(5, '14:30')['note'] = 'This respects the 14:00-16:00 rest window.'; rec('day-5-1430', 'C4', 'rest', 'an activity at 14:30 sits inside the rest window it claims to respect')
    it = item(5, '10:00'); it.update(name='Lighthouse Tour', note='', venue='lighthouse-tour', place='harbour-promenade'); it['id'] = 'day-5-1000'; rec('day-5-1000', 'C4', 'closed', 'Lighthouse Tour is closed on Fridays (places.json)')
    next(n for n in T['notes'] if n['id'] == 'note-quay')['finding'] = 'Open 08:00 to 22:00 daily; 4.2 stars from 640 reviews.'; rec('note-quay', 'C4', 'hours', 'says open until 22:00; places.json says 20:00')
    it = item(2, '19:00'); it.update(name='Dinner at Kettle & Crumb', note='Preferred; the alternate is Fennel House.', venue='kettle-crumb', place='kettle-crumb'); it['id'] = 'day-2-1900'; rec('day-2-1900', 'C2', 'repeat', 'same preferred dinner as day 1 (the brief forbids consecutive nights)')
    item(2, '16:30')['note'] = 'Level paths and a river walk. No complaints found online, so it is safe for a grandparent.'; rec('day-2-1630', 'L1', 'text', 'absence of complaints online presented as proof the park is safe')
    T['log'].append(dict(id='log-brindle', date='2030-04-22', title='Fallback hotel', decision='Hotel Brindle (5.0 from 4 reviews) is our top pick for Selmere if Selmere Grand falls through.', status='open')); rec('log-brindle', 'L2', 'text', 'a 5.0 from 4 reviews called the top pick (places.json / RESEARCH.md)')
    next(n for n in T['notes'] if n['id'] == 'note-kettle')['finding'] = 'Verified; open 08:00 to 22:00 daily; 4.6 stars from 812 reviews.'; rec('note-kettle', 'L5', 'text', "'verified' is the LocalEats site's badge; RESEARCH.md says we did not verify it")
    item(1, '18:30')['note'] += ' We confirmed a table with the restaurant.'; rec('day-1-1830', 'L6', 'text', 'claims a confirmed table; bookings.json and RESEARCH.md have no such booking')
    next(n for n in T['notes'] if n['id'] == 'note-weather')['finding'] = 'May in Harrowgate and Selmere: expect dry, sunny days of about 33 C, so pack light summer clothes.'; rec('note-weather', 'L8', 'text', 'describes July conditions; RESEARCH.md says May is 14-17 C with showers')
    T['places']['pier-grill']['status'] = 'CLOSED_TEMPORARILY'; rec('day-3-1830', 'L8', 'stale', 'places.json (27 Apr) says Pier Grill is CLOSED_TEMPORARILY; the plan and the 12 Apr note still rely on it')
    item(6, '08:30')['note'] += ' This is the shortest transfer of the trip.'; rec('day-6-0830', 'L9', 'text', 'a 35-min airport transfer called the shortest; the hotel-station leg is 10 min (distances.json)')
    return T, D


DECOYS = ('dec-museum', 'dec-rename', 'dec-confirmed', 'dec-train', 'dec-fort', 'dec-rating')


def decorate(T):
    """Make the decoy rows correct-but-suspicious: a renamed hotel, a confirmed booking with a reference, a 135-minute train."""
    T['hotels'][0]['note'] = 'Confirmation HM-4471. Formerly Marlow Inn.'
    return T


def evidence_files(T):
    return {'places.json': T['places'], 'distances.json': T['distances'], 'bookings.json': T['bookings']}
