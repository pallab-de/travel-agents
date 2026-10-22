---
name: visual-identity
description: "Give a report a distinctive look from the real destination: a licensed hero photo, a palette taken from it, fonts. Produces the design object the build requires."
user-invocable: true
---

# Visual identity

A report someone reopens for weeks should look like it was made for THIS trip, not stamped from a shared template.
The build refuses a spec without a `design` object and a hero photo, so this skill produces both:

1. **A real photo** of the destination as the hero, not a CSS-only illustration.
2. **A palette anchored in that photo's colours**, not default swatches.

Both are destination-specific - a desert fort's gold has nothing to do with a northern lake's teal or a southern
capital's terracotta - so deriving them per project is the point, not overhead to skip.

## 1. Source a real, licence-safe photo

Use `scripts/fetch_cc_image.py "<search query>" <project>/hero.jpg`. It searches [Openverse](https://openverse.org)
(Wikimedia Commons, Flickr Commons and other CC-licensed pools) and downloads the best commercial-use-safe match. No
API key is needed.

- **Pick the query like a photo editor, not a keyword-stuffer.** a country or region name returns generic stock; "a misty mountain
  landmark at dawn" returns something with character. For several regions, use the single most iconic one for the hero.
- The script accepts only `cc0`, `pdm` (public domain), `by` and `by-sa`, and refuses anything else outright. If it
  returns `NO_SAFE_RESULTS`, broaden the query rather than loosening the licence filter.
- **Keep the `.attribution.json` sidecar it writes.** Put the credit in the hero's `image_credit` (a stay: `hero_image_credit` in `stay_report.json`), e.g. `<title> by
  <creator> (<licence>), via Openverse`. `by` and `by-sa` require it; `cc0` and `pdm` don't, but it costs one line.
- Name the file in the hero block (`"image": "hero.jpg"`, beside the spec; a stay: `hero_image` and `hero_image_alt` in `stay_report.json`, because the stay spec is generated). The build resizes it, writes it into the
  report's `<report name>_assets/` folder with a manifest, and the audit re-hashes it, so a photo swapped after the build is caught.

## 2. Pull an anchor palette from it

Run `scripts/extract_palette.py <project>/hero.jpg 6`. It prints the 6 most common colours in the photo with their
share of the frame.

**These raw swatches are a starting point, not a finished palette.** Photo colours are desaturated and low-contrast
by design; piped straight into CSS they give illegible text. Curate them:
- Pick ONE swatch as the accent (links, the one thing that should pop): usually the colour with real character, not the
  largest swatch (often just "sky" or "stone"). Darken or desaturate it until it holds up.
- Build the light background as a near-white tint of the photo's dominant hue family, not a generic off-white.
- Build the dark background as a genuinely dark shade of the SAME hue family, not an inversion to charcoal.
- Keep a second accent for one deliberate use (a status colour family). Restraint in count is what makes a palette look
  designed.
- Write the result as `design.palette` and `design.palette_dark` (the tokens are listed in
  `report-builder`), with the `brand`, a two-sentence `ethos` and the two font families: into the spec for an itinerary; for a stay, into
  `stay_report.json` under `design` (or save it as a file and name it in `design_file`), because the stay spec is generated and a design written into it is lost on the next rebuild. The build checks contrast
  (body text 4.5:1, the accent (used as link and rank text) 4.5:1, text over the photo overlay 4.5:1) and refuses a palette that fails.

## Applying it

The hero photo sits behind the title with a dark scrim under the text in both themes. Light text on a photo is the
correct convention, and matching it to the body colour scheme only hurts legibility. Everything below the hero follows
the curated light and dark tokens.

## Icons and other assets

The engine draws no icon sprite. If a project adds icons, use a proven library such as [Lucide](https://lucide.dev),
copy its SVG paths exactly, never hand-draw them (a hand-drawn plane, bed or car clips at the edge or fails to read as
the thing), and look at the render at both large and small sizes.

## External content licensing - a hard rule for anything sourced from elsewhere

**Any asset that isn't original to this project - an icon, a photo, a font, a code snippet copied from a library or
Q&A site, a sample dataset - carries a licence, and its actual terms are followed, not assumed.**
- Fetch the project's real `LICENSE` file and read what it requires. Permissive licences (MIT, ISC, BSD) still commonly
  require the notice to "appear in all copies"; for a page shared as a standalone file, include the notice in the file
  itself (an HTML comment works), not only in a README the recipient never sees.
- A permissive licence is not the same as "no attribution needed". Re-read the exact text.
- A mixed-licensed set (one licence for most of it, another for a named subset) needs both notices.
- A visible credit line in the footer, next to the photo credit, is good practice even when the licence's letter is
  satisfied elsewhere.
- This covers every asset type this plugin pulls in: a web font, a dataset, a copied code pattern. "I forgot to check"
  is never an excuse.

## Done when

- `hero.jpg` and its `.attribution.json` are saved beside the spec, and the credit line is in the hero block's `image_credit` (a stay: `hero_image_credit` in `stay_report.json`).
- The licence text was read for the exact image, and any notice it requires is in the page.
- `design` has a brand, an ethos, both palettes (derived from the photo, then curated) and two font families, and `build_page.py` accepts it.
