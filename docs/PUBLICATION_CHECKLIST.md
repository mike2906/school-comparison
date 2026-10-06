# Publication preparation

Repository visibility is a separate maintainer decision. These changes do not make the
repository public, deploy a release or modify the existing school database.

## Prepared in code

- Local PostgreSQL, Redis and SearXNG ports bind to loopback.
- Demo seeding requires an explicit reset flag and a local database named
  `sofia_schools_demo`; query-string connection overrides are refused.
- Locked dependency updates remove known fixable advisories and unused AI provider SDKs.
- CI tests a fresh PostgreSQL migration chain, model/schema alignment, populated-schema
  reapplication and a separate backup restoration.
- Deployment takes and validates a private custom-format PostgreSQL backup before migration.
  PostgreSQL is started and awaited first. A backup failure stops deployment and preserves
  previous archives; successful backup validation keeps the newest seven script-created
  archives. Backup files are excluded from Git.
- Golden-corpus fixtures have people's names, personal email addresses and mobile numbers
  replaced with made-up ones (current tree only; earlier commits keep the originals).
- The README names the source of `backend/data/bg/education.geojson` (Eurostat GISCO, from
  the Ministry of Education and Science) and says its licence is not stated.
- The initial migration now creates the pipeline table before later revisions alter it.
  The schema-alignment revision adds missing fields only where absent, preserving databases
  previously initialized with ORM `create_all`. Its downgrade intentionally retains fields
  that may predate the revision and hold production data. Historical downgrade-to-base is
  not a supported production rollback procedure; use the deployment image rollback runbook.

## Maintainer actions before publication

- Review and merge the hardening PR after green CI and independent pre-review.
- Install the updated host deployment script using [the runbook](../deploy/README.md).
  Verify its backup location, available disk space and recovery process before deploying.
- Enable protection for `main`: pull requests, required Backend/Frontend checks, restricted
  force pushes and deletion. Choose a GitHub plan supporting these controls while private,
  or configure them as part of the deliberate publication change. The current private
  repository's plan does not provide the requested protection controls.
- The `production` environment exists, is restricted to `main`, and the deploy jobs name
  it. Set `DEPLOY_SSH_KEY`, `CLOUDFLARE_PAGES_TOKEN` and `DEPLOY_HOST` in it and delete the
  repository secrets of the same names ([runbook](../deploy/README.md)); until they are
  deleted, a workflow on any branch can still read them.
- Set an off-host backup and retention policy; the deployment backup alone is not disaster
  recovery. Test restoration before deleting old archives.
- Confirm the intended license and rights to any published data/assets. Add actual product
  screenshots when useful; never present mockups as implementation evidence.

## Remaining dependency limitations

The frontend runtime dependency audit has no findings. The Tailwind 3 build dependency
chain still includes the unpatched `braces` recursion advisory
[GHSA-vfj7-8cjw-p6xm](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm).
The affected glob patterns are fixed in the repository, not supplied by visitors. A separate
Tailwind migration should remove the development dependency; this is not a zero-findings
all-dependency audit.

Crawl4AI's NLTK dependency has an unpatched model-artifact loading advisory
[GHSA-8mgp-746c-j5xp](https://github.com/advisories/GHSA-8mgp-746c-j5xp).
Do not load untrusted NLTK model artifacts. The website pipeline uses text processing,
not uploaded classifier/model archives. Crawl4AI also uses its upstream LiteLLM fork;
package-name changes alone do not prove the underlying code is free of advisories.
The production API does not run the offline scraping pipeline. Recheck advisories before
future extraction runs and dependency updates.
