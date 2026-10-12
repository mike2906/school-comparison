Read-only export from the launch DB after the 2026-10-12 targeted price refresh
(`scripts/refresh_prices.py`, main ba20094), for reproducing the 171, 533, 564 and 610 results.

Per school: `pages.json` (pricing pages and pages a row points to, with raw_markdown),
`pricing_rows.json` (rows stored now), `recordings/` (the recorded PageFees answer),
`refresh_log.txt` (proposed rows and the hold reasons).

171, 533 and 564 were held, so their stored rows are the pre-refresh ones. 610's refresh
was committed and then restored from the pre-refresh backup, so its stored rows are also
pre-refresh; the refresh's rows are only in its log and recording. No pages were dropped:
none names staff or parents.
