// Airbnb search-results extractor. Schema last verified live against a real search-results page on 2026-09-28 (see the
// field-path notes below and listing-search/SKILL.md). Airbnb's field names change without error or warning (an earlier
// shape used pricingQuote/listing.name; this one uses structuredDisplayPrice/demandStayListing). Run
// scripts/check_airbnb_coverage.py on every batch before trusting it.
//
// Run via Playwright MCP's browser_evaluate (or browser_run_code_unsafe) on an already-loaded Airbnb search-results page:
//   the return value is a JSON-serializable array of {id, name, type_label, ll, price_text, price_amount,
//   price_currency, rating, review_count, badges}. Paginate by navigating to the next page and re-running; this extracts one page's worth.
//
// Field paths, as verified 2026-09-28 (update this comment when you re-verify):
//   searchResults array : data-deferred-state-N script tag's JSON -> ... -> presentation.staysSearch.results.searchResults
//                          (found generically below by walking for an array whose first element has .demandStayListing,
//                          so the exact parent path doesn't need to be hardcoded -- it has already moved once)
//   listing id           : atob(result.demandStayListing.id) -> "DemandStayListing:<digits>" -> take the digits
//   listing name          : result.subtitle (the actual title text); result.title is a short type label ("Flat in <city>"),
//                          NOT the name -- a common misreading. Cross-checked against
//                          result.demandStayListing.description.name.localizedStringWithTranslationPreference (same value).
//   coordinates           : result.demandStayListing.location.coordinate.{latitude,longitude}
//   price                 : result.structuredDisplayPrice.primaryLine.price (a string like "$126 USD" or "₹10,500")
//                          -- currency and format vary by locale/site domain; extract_currency() below is best-effort.
//   rating                : result.avgRatingLocalized ("4.97 (33)" or "New" when unrated) and the more parseable
//                          result.avgRatingA11yLabel ("4.97 out of 5 average rating,  33 reviews") -- prefer the
//                          a11y label when present, it is less likely to reformat.
//   badges                : result.badges[].loggingContext.badgeType (e.g. GUEST_FAVORITE, SUPERHOST)

(function extractAirbnbSearchResults() {
  function findSearchResults() {
    const scripts = [...document.querySelectorAll('script[id^="data-deferred-state"]')];
    let found = null;
    function walk(o, depth) {
      if (depth > 14 || o == null || found) return;
      if (Array.isArray(o) && o.length && o[0] && typeof o[0] === 'object' && o[0].demandStayListing) { found = o; return; }
      if (typeof o === 'object') for (const k in o) walk(o[k], depth + 1);
    }
    for (const s of scripts) {
      try { walk(JSON.parse(s.textContent), 0); } catch (e) { /* not this script tag */ }
      if (found) break;
    }
    return found || [];
  }

  function parseId(r) {
    try {
      const decoded = atob(r.demandStayListing.id);
      const m = decoded.match(/:(\d+)$/);
      return m ? m[1] : null;
    } catch (e) { return null; }
  }

  function parseRating(r) {
    const label = r.avgRatingA11yLabel || '';
    const m = label.match(/([\d.]+) out of 5\D*?([\d,]+)\s+reviews?/i);   // "1,234 reviews" is 1234, not 234
    if (m) return { rating: parseFloat(m[1]), review_count: parseInt(m[2].replace(/,/g, ''), 10) };
    const loc = r.avgRatingLocalized || '';
    if (/^new$/i.test(loc.trim())) return { rating: null, review_count: 0 };
    const m2 = loc.match(/([\d.]+)\s*\(([\d,]+)\)/);
    if (m2) return { rating: parseFloat(m2[1]), review_count: parseInt(m2[2].replace(/,/g, ''), 10) };
    return { rating: null, review_count: null };
  }

  function parsePrice(text) {
    if (!text) return { amount: null, currency: null };
    // The number is the one beside the currency (a symbol or a 3-letter code), not a digit elsewhere ("3 nights ₹10,500").
    const t = text.replace(/[  ]/g, ' ');
    const num = '(\\d{1,2}(?:,\\d{2})+,\\d{3}(?:\\.\\d{1,2})?|\\d{1,3}(?:[., ’\']\\d{3})+(?:[.,]\\d{1,2})?|\\d+(?:[.,]\\d{1,2})?)';
    const mark = '(US\\$|CA\\$|MX\\$|AU\\$|NT\\$|HK\\$|NZ\\$|(?<![A-Za-z])R\\$|(?<![A-Za-z])A\\$|(?<![A-Za-z])C\\$|(?<![A-Za-z])S\\$|(?<![A-Za-z])Rs\\.?|[₹$€£]|[A-Z]{3})';   // multi-letter marks first: "R$" is reais, not dollars
    const m = t.match(new RegExp(mark + '\\s?' + num)) || t.match(new RegExp(num + '\\s?' + mark));
    if (!m) return { amount: null, currency: null };
    const codes = { 'US$': 'USD', 'CA$': 'CAD', 'MX$': 'MXN', 'AU$': 'AUD', 'NT$': 'TWD', 'R$': 'BRL', 'A$': 'AUD', 'C$': 'CAD', 'S$': 'SGD', 'HK$': 'HKD', 'NZ$': 'NZD', 'Rs': 'INR', 'Rs.': 'INR' };
    const cur0 = /^[\d]/.test(m[1]) ? m[2] : m[1];
    const cur = codes[cur0] || cur0;
    const raw = /^[\d]/.test(m[1]) ? m[1] : m[2];
    let s = raw.replace(/[ ’']/g, '');
    if (/^\d{1,2}(,\d{2})+,\d{3}(\.\d+)?$/.test(s)) s = s.replace(/,/g, '');                          // 1,23,456 (lakh grouping)
    if (/^\d{1,3}(\.\d{3})+(,\d{1,2})?$/.test(s)) s = s.replace(/\./g, '').replace(',', '.');        // 1.234,50
    else if (/^\d{1,3}(,\d{3})+(\.\d+)?$/.test(s)) s = s.replace(/,/g, '');                           // 1,234.50
    else if (/^\d+,\d{1,2}$/.test(s)) s = s.replace(',', '.');                                        // 12,50
    return { amount: parseFloat(s), currency: cur || null };
  }

  const results = findSearchResults();
  return results.map(r => {
    const priceText = r.structuredDisplayPrice && r.structuredDisplayPrice.primaryLine && r.structuredDisplayPrice.primaryLine.price;
    const { rating, review_count } = parseRating(r);
    const { amount, currency } = parsePrice(priceText);
    return {
      id: parseId(r),
      name: r.subtitle || (r.demandStayListing && r.demandStayListing.description && r.demandStayListing.description.name &&
            r.demandStayListing.description.name.localizedStringWithTranslationPreference) || null,
      type_label: r.title || null,
      ll: r.demandStayListing && r.demandStayListing.location && r.demandStayListing.location.coordinate
        ? [r.demandStayListing.location.coordinate.latitude, r.demandStayListing.location.coordinate.longitude] : null,
      price_text: priceText || null,
      price_amount: amount,
      price_currency: currency,
      rating,
      review_count,
      badges: (r.badges || []).map(b => b.loggingContext && b.loggingContext.badgeType).filter(Boolean),
    };
  });
})();
