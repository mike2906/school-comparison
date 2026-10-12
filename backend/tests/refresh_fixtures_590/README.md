Read-only export for school 590 (Erich Kästner kindergarten) after the 2026-10-12 refresh.
`pages.json`: /ceni pages and the two price-list image transcriptions (29234 mobile, 29235 desk).
`pricing_rows.json`: rows stored now; the €120 materials row was extracted as MONTHLY
(`period_as_extracted`) and set to YEARLY by hand. `validation.json`: Stage 6 report.
`recordings/`: every recorded answer for 590 today (three PageFees calls from the refresh runs,
one general-info call from the earlier extract). Logs: refresh-590b (with navigation, which
re-validated the template pages and produced USD rows) and refresh-590c (stored pages only,
after the template pages were invalidated again).
