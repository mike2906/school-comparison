# Load test

`parents.js` is a [k6](https://k6.io) script. Each virtual user acts as one parent, at a
parent's pace: open the search page (list, filters, counts, exam averages), sometimes type
a school's name into search (one request per pause, as the search box sends), read two to
four school pages, and sometimes compare them. So the number of virtual users is the
number of people using the site at the same moment. Schools and names are picked at
random from the live list; real visits bunch up on popular schools.

Cloudflare keeps API answers for 5 minutes, so after a minute or two most requests of a
normal run are served from its copy and the run mostly measures Cloudflare. To measure
the server alone, as right after a publish when no copy is fresh, set **bypass cache**:
every request then carries its own query string and goes to the server.

It ramps to half the peak, holds for 4 minutes, then ramps to the full peak and holds for
4 minutes (about 15 minutes in all). It stops early if more than 2% of requests fail or
the 95th percentile passes 3 s.

## Run

Against production, from GitHub Actions: **Actions → Load test → Run workflow** (inputs:
peak, default 1000; bypass cache). The results appear as annotations on the run. It
refuses to start while a deploy or publish runs (`deploy.yml`), since those restart the
API; a publish still uploading from the PC is not visible to it, so check the publish lock
there first.

Locally, for a quick check of the script (20 s, a few users):

```bash
QUICK=1 PEAK=3 k6 run tools/load-test/parents.js           # BYPASS_CACHE=1 for the server alone
```

`API_URL` points it at another API (default `https://api.schooldecider.com`).

## What it does not cover

The site's own files (Cloudflare Pages), map tiles (CARTO) and address lookups
(OpenStreetMap Nominatim) are not loaded. Those are third-party or static, with their own
limits.
