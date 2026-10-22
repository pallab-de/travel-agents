---
name: community-intel
description: "Find what people who actually went there say (forums, Reddit, blogs), not the premade-itinerary consensus. Use for should-we-go and hidden-gem calls in an itinerary."
user-invocable: true
---

# Community intel

Every generic itinerary agrees on the same five cities and the same photo-op list, because they're all built
from the same curated top-10 blogs. The genuinely useful research question is different: **what do people who
actually went there say when nobody's selling them anything?** That's forum threads, not listicles — and it
takes a deliberate search strategy to surface, because search engines rank the listicles first.

The case this skill exists for: a draft itinerary puts 2-3 days in a famous village region by default (it's on
every itinerary list), while a candid forum thread says *"Don't bother with an overnight there. It's pretty, but
nothing special... spend the extra days in the nearby market town and day-trip out."* That's the kind of finding
a keyword search for "best villages in <region>" will never surface, because nobody writing SEO content says
"skip this."

## Where to look, in order

1. **Reddit** — the highest-signal source for candid opinion. Search `site:reddit.com <query>`, or search
   directly within relevant subs: the destination's own subs (found per trip, e.g. `r/<country>` or `r/<city>`), general
   ones (`r/travel`, `r/solotravel`). Read the actual comment thread, not just the
   post title — the real signal is usually in replies arguing with each other, not the top-level post.
2. **TripAdvisor forums** (not TripAdvisor's own attraction rankings, which are as curated as any listicle) —
   search `site:tripadvisor.com "ShowTopic"` or search the forum directly for the destination. Older threads
   (5-10 years) are often still relevant for "is X worth it" opinions that don't change fast, but check for
   anything time-sensitive (an attraction that's since closed, changed hands, or gotten dramatically busier).
3. **Lonely Planet's Thorntree** (`community.lonelyplanet.com`) — smaller and slower-moving than Reddit, but
   skews toward more experienced independent travellers; good for a second opinion when Reddit's take seems
   thin or contested.
4. **Google Maps reviews** for a specific place already under consideration — use `venue-verification`'s
   `check_place.py`, which already pulls this. Sort mentally for recency and look for a repeated specific
   complaint/praise, not the star average alone (see the proportionality rule below).
5. **Blogs and "best of" listicles are a LOWER-weight source, not a source to avoid** — useful for basic
   facts (opening hours, how to get somewhere) but treat any *opinion* content in them ("a hidden gem!") as
   marketing copy, not evidence, since that's exactly the genre this skill exists to see past.

## Search prompt patterns

Don't search "<place> things to do" — that returns exactly the curated consensus this skill is trying to get
underneath. Use adversarial/candid framings instead:

- `"<place>" overrated reddit`
- `"<place>" vs "<alternative>" reddit` — the single most useful pattern when two options are being weighed
  (the query that surfaces a head-to-head thread directly)
- `is "<place>" worth visiting reddit` / `is "<place>" worth an overnight reddit`
- `skip "<place>" reddit` / `"<place>" tourist trap`
- `"<place>" hidden gem reddit` (for the inverse: underrated spots not on the standard list)
- `"<place>" with a young child` / `"<place>" stroller friendly reddit` — for a group-specific constraint, always
  search the constraint explicitly rather than assuming a generic answer applies
- `site:reddit.com "<region>" itinerary` — for the wide sweep, per `research-delegation`'s existing pattern
- `"<region>" day trip instead of "<place>"` — when a redditor's alternative (a smaller town recommended over the famous one)
  needs its own follow-up research before it can replace the original plan

Run at least 2-3 variants per genuinely contested question, not just one — a single search's top result is a
sample size of one.

## Weighing what comes back (uses review-synthesis's proportionality rule)

One redditor's strong opinion is an anecdote. The signal worth acting on is **independent convergence**:
multiple different threads, written by different people, at different times, landing on the same take. Before
treating a community opinion as a finding:

- Open more than one thread. If 3-4 separate threads independently say "<famous place> is a letdown
  for the crowds" or "<nearby town> is underrated," that's corroboration. If it's one loud comment with no replies
  agreeing, it's a data point to note, not a verdict to build an itinerary around.
- Watch for a vocal minority effect the other direction too — a contrarian take that's actually just one
  person's bad day, contradicted by everyone else in the same thread, is not a finding.
- Check whether the opinion is dated (a complaint about crowds from ten years ago may not reflect today) or
  group-specific (advice from solo backpackers doesn't automatically transfer to a family with a young child in a
  stroller — always check the commenter's own context matches yours before applying their take).
- A "well actually" reply deep in a thread that corrects the top comment is common and often the more
  accurate take — read to the end, not just the top few replies.

## Storing findings so they're searchable and referenceable later

Log every community-intel finding into the project's `RESEARCH.md`, under a **Community intel**
subsection per topic, in this shape (plain markdown — searchable with a normal grep, no new file format
needed):

```markdown
### Community intel: <topic, e.g. "<famous village region> vs <market town> day trips">
- **Claim:** <the actual finding, stated plainly>
- **Corroboration:** <how many independent sources/threads said this, and whether they agree or split>
- **Contradicts the default/curated consensus?** <yes/no - this is usually the interesting part>
- **Group-fit check:** <does this opinion's context match this trip's own group/constraints, or does it need
  adjustment - e.g. a hiking-heavy recommendation needs a stroller-friendly caveat for this group>
- **Sources:** <one line per source - site, thread title or URL, and the date you fetched it>
- **Verdict / what changed:** <what this means for the route or plan - a decision, or still-open with why>
```

This keeps every contested call auditable: a future session (or a review pass) can re-open the
sources instead of trusting a bare "Reddit says X" left in the itinerary with no trail.

## Sources this skill covers

Which source may back which kind of claim, and how much each weighs, is `fact-guard`'s ("Sources, by kind of claim" and
the rank-score formula); `review-synthesis` covers review providers. This skill is about the community sources:

| Source | What it is used for | Method |
|---|---|---|
| Reddit and forums | Concerns and themes; the earliest warning of a renovation, a management change, a scam, a safety issue. Never the score: unverified and skewed toward complaints. A post counts only if dated and linked. | The search patterns above. Store the posts read as a review file (method, retrieved_at, each post's text and hash) so they can corroborate a concern. |
| X (Twitter) | Recent complaints or praise not showing elsewhere; concerns, never the score. | `WebSearch` only (e.g. `site:x.com <hotel> complaint`); no paid API, no scraping the live site. If nothing surfaces, say so rather than claiming there is no activity. |
| Government and tourism-authority sites | Facts a commercial site won't carry: visa rules, seasonal closures, official advisories, opening days of public sites. | Read like any other source; cite the URL and read date. |

**How much sourcing a fact needs is a judgement, scaled to what's at stake.** Critical facts (flight and train
times and fares, booking amounts and cancellation dates, ticketed or reserved event times) need two independent
sources, and the gate enforces it; aim for three when sources disagree. A claim that decides a ranking deserves
more corroboration than one that colours it. A hotel's own star rating is fine from its own site; "quiet at night"
needs reviews from more than one place.

## Relationship to other skills

- **`research-delegation`**: when fanning a sweep out to subagents, brief each one with this skill's search
  patterns and the storage format, not just "research things to do."
- **`venue-verification`**: once a community thread names a specific place, verify it exists/is open/is where
  they say with that skill's tools before it goes in the itinerary spec — a candid opinion about a real place
  still needs the place itself fact-checked.
- **`fetch-integrity`**: run `venue-verification/scripts/check_fetch.py <page text> --expect <place>` on a forum page before trusting it loaded real content — a
  blocked/login-walled Reddit or TripAdvisor page looks like "no results," not an error.
- **`review-synthesis`**: its proportionality principles are what this skill's "weighing what comes back"
  section applies at forum-thread scale instead of review-corpus scale.

## Done when

- Every genuinely contested "is X worth it" / "X vs Y" question was searched with an adversarial pattern
  (`overrated`, `skip`, `vs`), not just a plain "things to do" search.
- At least 2-3 independent sources were checked before a community opinion was treated as a finding, not one.
- Each finding that changed the plan is logged in `RESEARCH.md` in the Community intel format, with sources
  and a group-fit check — not just summarized in chat and lost.
- A recommendation drawn from people with different constraints than this trip's group (solo travellers,
  hikers, no kids) was explicitly checked for fit, not applied blindly.
