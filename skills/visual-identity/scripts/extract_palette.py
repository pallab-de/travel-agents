"""Pull N dominant colors out of a downloaded hero image (Pillow color quantization) as a starting anchor for
that trip's bespoke palette - see the visual-identity SKILL.md for how to turn these raw swatches (often
muted/desaturated, straight off a photo) into an accessible, high-contrast semantic palette. Don't wire these
hex values directly into CSS as text/background pairs without checking contrast yourself.

Usage: python extract_palette.py <image_path> [n_colors=6]
"""
import sys
from PIL import Image


import sys as _sys
if _sys.stdout.encoding and _sys.stdout.encoding.lower() != 'utf-8':   # names in other scripts must print on a Windows pipe too
    _sys.stdout.reconfigure(encoding='utf-8')


def dominant_colors(path, n=6):
    im = Image.open(path).convert('RGB')
    im.thumbnail((200, 200))  # a small copy quantizes just as informatively and far faster
    q = im.quantize(colors=n * 4, method=Image.MEDIANCUT)
    palette = q.getpalette()
    counts = sorted(q.getcolors(), reverse=True)  # [(pixel_count, palette_index), ...]
    total = im.width * im.height
    out = []
    for count, idx in counts[:n]:
        r, g, b = palette[idx * 3:idx * 3 + 3]
        out.append({'hex': '#%02x%02x%02x' % (r, g, b), 'share': round(count / total, 3)})
    return out


if __name__ == '__main__':
    if len(sys.argv) < 2 or sys.argv[1].startswith('-'):
        print(__doc__)
        sys.exit(0 if sys.argv[1:] in (['-h'], ['--help']) else 1)
    path = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    for c in dominant_colors(path, n):
        print(c['hex'], c['share'])
