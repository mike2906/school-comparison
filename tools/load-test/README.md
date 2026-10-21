# Load test

`parents.js` is a [k6](https://k6.io) script. Each virtual user acts as one parent, at a
parent's pace: open the search page (list, filters, counts, exam averages), sometimes type
a school's name into search (one request per pause, as the search box sends), read two to
four school pages, and sometimes compare them. So the number of virtual users is the
number of people using the site at the same moment. Schools and names are picked at
random from the live list, which is the pessimistic case for Cloudflare's cache: real
visits bunch up on popular schools.

It ramps to half the peak, holds for 4 minutes, then ramps to the full peak and holds for
4 minutes (about 15 minutes in all). It stops early if more than 2% of requests fail or
the 95th percentile passes 3 s.

## Run

Against production, from GitHub Actions: **Actions → Load test → Run workflow** (input:
peak, default 1000). The results appear as annotations on the run. Don't run it while a
data publish runs (`backend/scripts/publish_snapshot.sh` holds the publish lock), since a
publish restarts the API.

Locally, for a quick check of the script (20 s, a few users):

```bash
QUICK=1 PEAK=3 k6 run tools/load-test/parents.js
```

`API_URL` points it at another API (default `https://api.schooldecider.com`).

## What it does not cover

The site's own files (Cloudflare Pages), map tiles (CARTO) and address lookups
(OpenStreetMap Nominatim) are not loaded. Those are third-party or static, with their own
limits.
