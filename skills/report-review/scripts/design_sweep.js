// Scripted design sweep for a built HTML report or itinerary. Run it through the Playwright MCP tool `browser_run_code_unsafe`
// (pass this file's contents as `code`) AFTER navigating to the page over http (file: URLs are blocked). Prints one line per failure and a verdict.
//
// Measures, at widths 360/390/768/1024/1180/1400/1920 in light AND dark colour scheme:
//   overflow   document is wider than the viewport (horizontal scroll)
//   contrast   text against its effective background: >= 4.5:1 normal text, >= 3:1 large text (>= 24px, or >= 18.66px bold)
//   targets    interactive elements under 24x24 CSS px FAIL (WCAG 2.2 AA); under 44x44 is an ADVISORY count (phone widths only; inline text links exempt)
//   images     images that failed to load/decode
//   links      anchors with an empty, "#"-only or javascript: href (except ids in ALLOW_HASH_LINKS, which are buttons styled as links)
//   texturls   plain-text URLs that are not links
//   console    console errors while loading (favicon 404s are ignored)
async (page) => {
  const WIDTHS = [360, 390, 768, 1024, 1180, 1400, 1920];
  const ALLOW_HASH_LINKS = ['themeBtn'];
  const out = [];
  const errors = [];
  page.on('console', m => { if (m.type() === 'error' && !/favicon/i.test(m.text() + (m.location().url || ''))) errors.push(m.text().slice(0, 160)); });
  page.on('pageerror', e => errors.push('pageerror: ' + String(e).slice(0, 160)));
  await page.reload({ waitUntil: 'load' });
  await page.addStyleTag({ content: '*, *::before, *::after { transition: none !important; animation: none !important; scroll-behavior: auto !important; }' });   // never sample colours mid-transition

  const measure = async ([allow, phone]) => {   // page.evaluate passes ONE argument: destructure it
    const res = { overflow: null, contrast: {}, targets: [], advisory: 0, images: 0, links: [], texturls: 0 };
    const de = document.documentElement;
    if (de.scrollWidth > de.clientWidth + 1) res.overflow = de.scrollWidth + ' > ' + de.clientWidth;
    const vis = el => { const r = el.getBoundingClientRect(), s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && +s.opacity > 0; };
    const cv = document.createElement('canvas'); cv.width = cv.height = 1; const cx = cv.getContext('2d', { willReadFrequently: true });
    const parse = c => { if (!c) return null; cx.clearRect(0, 0, 1, 1); cx.fillStyle = 'rgba(0,0,0,0)'; cx.fillStyle = c; cx.fillRect(0, 0, 1, 1); const d = cx.getImageData(0, 0, 1, 1).data; return { r: d[0], g: d[1], b: d[2], a: d[3] / 255 }; };   // handles color-mix(), color(srgb ...), oklch() etc.
    const over = (f, b) => ({ r: f.r * f.a + b.r * (1 - f.a), g: f.g * f.a + b.g * (1 - f.a), b: f.b * f.a + b.b * (1 - f.a), a: 1 });
    const lum = c => { const t = [c.r, c.g, c.b].map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }); return 0.2126 * t[0] + 0.7152 * t[1] + 0.0722 * t[2]; };
    const bgOf = el => {
      let stack = [], n = el;
      while (n && n.nodeType === 1) { const s = getComputedStyle(n); if (s.backgroundImage !== 'none') return null;   /* image or gradient behind the text: the effective background is indeterminate, so skip rather than guess */ const c = parse(s.backgroundColor); if (c && c.a > 0) { stack.push(c); if (c.a >= 0.99) break; } n = n.parentElement; }
      let base = { r: 255, g: 255, b: 255, a: 1 };
      if (!stack.length || stack[stack.length - 1].a < 0.99) { const bc = parse(getComputedStyle(document.body).backgroundColor); base = bc && bc.a >= 0.99 ? bc : (matchMedia('(prefers-color-scheme: dark)').matches ? { r: 18, g: 18, b: 18, a: 1 } : base); }
      for (let i = stack.length - 1; i >= 0; i--) base = over(stack[i], base);
      return base;
    };
    let seen = 0;
    for (const el of document.querySelectorAll('body *')) {
      if (seen > 4000) break;
      if (['SCRIPT', 'STYLE', 'NOSCRIPT', 'SVG', 'PATH', 'IMG', 'CANVAS'].includes(el.tagName)) continue;
      const own = [...el.childNodes].filter(n => n.nodeType === 3 && n.textContent.trim()).map(n => n.textContent.trim()).join(' ');
      if (!own || !vis(el)) continue;
      seen++;
      const s = getComputedStyle(el);
      if (/text/.test(s.webkitBackgroundClip || s.backgroundClip || '') || /rgba\(0, 0, 0, 0\)|transparent/.test(s.webkitTextFillColor || '')) continue;   // gradient-clipped text: no single foreground colour
      const fg = parse(s.color), bg = bgOf(el);
      if (!fg || !bg) continue;
      const f = over(fg, bg), L1 = lum(f), L2 = lum(bg), ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
      const px = parseFloat(s.fontSize), large = px >= 24 || (px >= 18.66 && +s.fontWeight >= 700), need = large ? 3 : 4.5;
      if (ratio < need) { const k = Math.round(ratio * 100) / 100 + ' (' + Math.round(f.r) + ',' + Math.round(f.g) + ',' + Math.round(f.b) + ') on (' + Math.round(bg.r) + ',' + Math.round(bg.g) + ',' + Math.round(bg.b) + ') needs ' + need; (res.contrast[k] = res.contrast[k] || { n: 0, ex: own.slice(0, 40) }).n++; }
    }
    if (phone) for (const el of document.querySelectorAll('a[href], button, input, select, textarea, summary, [role=button], [tabindex]:not([tabindex="-1"])')) {
      if (!vis(el)) continue; const r = el.getBoundingClientRect(), st = getComputedStyle(el);
      if (el.tagName === 'A' && st.display === 'inline') continue;   // links inside running text are exempt
      const label = el.tagName.toLowerCase() + ' "' + (el.innerText || el.getAttribute('aria-label') || el.id || '').trim().slice(0, 24) + '" ' + Math.round(r.width) + 'x' + Math.round(r.height);
      if (r.width < 24 || r.height < 24) res.targets.push(label); else if (r.width < 44 || r.height < 44) res.advisory++;
    }
    // Images are lazy-loaded: load them all first, then count only the ones that errored (an off-screen image that was never fetched isn't a failure).
    await Promise.all([...document.images].filter(i => i.getAttribute('src')).map(i => { i.loading = 'eager'; return i.decode().catch(() => {}); }));
    res.images = [...document.images].filter(i => i.getAttribute('src') && i.complete && i.naturalWidth === 0).length;
    res.links = [...document.querySelectorAll('a')].filter(a => { const h = (a.getAttribute('href') || '').trim(); return (!h || h === '#' || /^javascript:/i.test(h)) && !allow.includes(a.id) && a.getAttribute('role') !== 'button'; }).map(a => (a.innerText || a.id || 'a').trim().slice(0, 24));
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, { acceptNode: n => n.parentElement && !n.parentElement.closest('a, script, style, noscript, code') && /https?:\/\/\S+/.test(n.textContent) ? 1 : 2 });
    while (walker.nextNode()) res.texturls++;
    return res;
  };

  const seenLines = new Set();
  for (const scheme of ['light', 'dark']) {
    await page.emulateMedia({ colorScheme: scheme });
    await page.evaluate(t => { document.documentElement.setAttribute('data-theme', t); }, scheme);   // pages with their own theme toggle use this attribute
    await page.waitForTimeout(300);
    for (const w of WIDTHS) {
      await page.setViewportSize({ width: w, height: 900 });
      await page.waitForTimeout(150);
      const r = await page.evaluate(measure, [ALLOW_HASH_LINKS, w <= 768]);
      const add = line => { const k = line; if (!seenLines.has(k)) { seenLines.add(k); out.push(line); } };
      if (r.overflow) add(`FAIL overflow @${w} ${scheme}: ${r.overflow}`);
      for (const [k, v] of Object.entries(r.contrast)) add(`FAIL contrast ${scheme}: ${k} - ${v.n} element(s), e.g. "${v.ex}"`);
      if (r.targets.length) add(`FAIL targets @${w}: ${r.targets.length} under 24x24 (WCAG 2.2 AA), e.g. ${r.targets.slice(0, 3).join('; ')}`);
      if (r.advisory) add(`ADVISORY targets @${w}: ${r.advisory} between 24 and 44 px (aim for 44 on phones)`);
      if (r.images) add(`FAIL images @${w}: ${r.images} failed to load`);
      if (r.links.length) add(`FAIL links: ${r.links.length} empty/#-only, e.g. ${r.links.slice(0, 3).join('; ')}`);
      if (r.texturls) add(`FAIL texturls: ${r.texturls} plain-text URL(s) that are not links`);
    }
  }
  for (const e of [...new Set(errors)]) out.push('FAIL console: ' + e);
  await page.evaluate(() => document.documentElement.removeAttribute('data-theme'));
  await page.emulateMedia({ colorScheme: null });
  const fails = out.filter(l => l.startsWith('FAIL')).length;
  return (out.length ? out.join('\n') + '\n' : '') + (fails ? `${fails} FAIL line(s)` : `PASS: no FAIL lines at ${WIDTHS.join('/')} in light and dark`);
}
