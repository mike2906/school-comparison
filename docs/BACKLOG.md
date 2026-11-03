# Backlog

Open items carried over from the July–September go-live plan, which is now in
[`archive/GO_LIVE_PLAN.md`](archive/GO_LIVE_PLAN.md) (with its history of what went wrong
and how it was fixed). None of these were re-checked when they were carried over
(2026-11-03), so check an item is still open before starting it. Code-structure work is in
[`TECH_DEBT_PLAN.md`](TECH_DEBT_PLAN.md); each feature's status is in its own doc.

| Item | What | Archive ref |
| --- | --- | --- |
| Pipeline hardening | Items (a)–(c) were meant to land before any city-wide run of the old pipeline. Check whether the fees agent and school facts made them moot. | P2.14 |
| Icons | Emoji and SVG icons are mixed, and flags stand in for languages. | UF31 |
| kg.sofia addresses | Duplicate spellings of one building come back on each kg.sofia run. Normalise addresses before comparing. | UF39 |
| README | Screenshots, an architecture diagram and a decisions section. | P3.3 |
| Type checks | mypy in CI; replace `datetime.utcnow()` (28 uses in `backend/app`). | P4.2 |
| LLM clients | Merge the two OpenRouter model builders and drop the `inspect.signature` shims. | P4.5 |
| Filter tests | Tests for `/schools/filters` and `list_schools_filtered` attribute matching. | P4.6 |
| Fee date name | Expose the public `scraped_at` on fees under a clearer response alias. | P4.8 |

Closed when carried over: E5/E6 (cut-offs and the points calculator are live),
E1–E4 (replaced by the fees agent and school facts), P3.2 (the site is deployed),
P4.1/P4.4 (moved to `TECH_DEBT_PLAN.md`), P4.3 (frontend tests use `node --test`),
P4.7 (TypeScript, optional) and P3.5 (only needed if generated summaries return).
