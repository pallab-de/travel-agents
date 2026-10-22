"""Search Openverse (openverse.org, a CC-license search engine over Wikimedia Commons/Flickr/etc.) for a
commercial-use-safe image and download the best match. No API key needed for anonymous search (rate-limited
but fine for the handful of lookups one hero needs).

Usage: python fetch_cc_image.py "<search query>" <out_path.jpg>
Writes <out_path> and a sibling <out_path>.attribution.json (source, creator, license, landing page URL) -
never discard that sidecar, credit the creator in the hero's image_credit per the visual-identity SKILL.md rule.
"""
import json, sys, urllib.request, urllib.parse
from pathlib import Path

UA = {'User-Agent': 'Mozilla/5.0 (compatible; travel-agents-visual-identity/1.0)'}
COMMERCIAL_SAFE = {'cc0', 'pdm', 'by', 'by-sa'}   # excludes -nc (non-commercial) and -nd (no-derivatives)


import sys as _sys
if _sys.stdout.encoding and _sys.stdout.encoding.lower() != 'utf-8':   # names in other scripts must print on a Windows pipe too
    _sys.stdout.reconfigure(encoding='utf-8')


def search(query, page_size=12):
    q = urllib.parse.urlencode({
        'q': query, 'license_type': 'commercial', 'page_size': page_size,
        'orientation': 'landscape', 'mature': 'false',
    })
    req = urllib.request.Request(f'https://api.openverse.org/v1/images/?{q}', headers=UA)
    return json.loads(urllib.request.urlopen(req, timeout=20).read())['results']


def best(results, min_width=1600):
    """The widest commercial-safe result (one at least min_width wide if there is one); None when none is safe."""
    safe = sorted((r for r in results if r.get('license') in COMMERCIAL_SAFE), key=lambda r: r.get('width') or 0, reverse=True)
    return safe[0] if safe else None


def download(url, out_path):
    req = urllib.request.Request(url, headers=UA)
    data = urllib.request.urlopen(req, timeout=30).read()
    Path(out_path).write_bytes(data)
    return len(data)


if __name__ == '__main__':
    if len(sys.argv) < 3 or sys.argv[1].startswith('-'):
        print('usage: fetch_cc_image.py "<search query>" OUT.jpg   (writes OUT.jpg and OUT.jpg.attribution.json; only commercial-safe licences)')
        sys.exit(0 if sys.argv[1:] in (['-h'], ['--help']) else 1)
    query, out_path = sys.argv[1], sys.argv[2]
    results = search(query)
    pick = best(results)
    if not pick:
        print('NO_SAFE_RESULTS - no result has a commercial-safe licence; try a broader query, or a different subject/landmark name')
        sys.exit(1)
    if (pick.get('width') or 0) < 1600:
        print(f"WARNING: the widest licence-safe photo is {pick.get('width')} px wide; it may look soft as a full-width hero")
    size = download(pick['url'], out_path)
    attribution = {
        'source': 'Openverse', 'title': pick.get('title'), 'creator': pick.get('creator'),
        'creator_url': pick.get('creator_url'), 'license': pick.get('license'),
        'license_version': pick.get('license_version'), 'license_url': pick.get('license_url'),
        'foreign_landing_url': pick.get('foreign_landing_url'), 'original_image_url': pick['url'],
        'downloaded_bytes': size, 'query_used': query,
    }
    Path(str(out_path) + '.attribution.json').write_text(json.dumps(attribution, indent=2))
    print(json.dumps(attribution, indent=2))
