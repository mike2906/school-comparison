# Drive times

Parents who set a location see a rush-hour drive time on each school card and can sort by it.
Everything runs in the browser: the location never leaves it.

## How it works

1. **Road graph.** `.github/workflows/road-graph.yml` downloads OpenStreetMap (Geofabrik
   Bulgaria), clips Sofia plus ~5 km, and builds a compact car graph with
   `tools/road-graph/build_graph.py` (format in that file's docstring). It runs monthly, by
   hand, and as a check on pull requests that touch the builder or the router. It publishes
   `sofia-roads.bin.gz` (~1.7 MB) to the `road-graph` release and starts a site-only deploy;
   `deploy.yml` copies the file to `/routing/` on the site.
2. **Search.** Once a location is set, `frontend/src/routing/useDriveTimes.js` starts a Web
   Worker that fetches the whole-city graph once (no tiles, so nothing about the location is
   revealed), snaps the location onto the nearest road and runs one Dijkstra over the whole
   city (`roadGraph.js`). Each school's pinned building (the same one used for distance) is
   snapped to its nearest road the same way, in the browser, so new pins apply at once.
3. **Rush hour.** Free-flow times are multiplied by Sofia's morning factor, 1.73 (TomTom
   Traffic Index public city figure), and shown as "~N min", explained as an estimate for
   leaving around 8:00 on a weekday. With one factor the order of schools is the same as off-peak.
   Road-level factors from the city's bus feed are planned (thread "Rush-hour drive times").

## Routing rules

Copied from OSRM's car profile (car.lua v5.27): highway speeds, speed limits (including zone
tags such as `source:maxspeed=BG:urban`) x 0.8, access blacklist, oneways and roundabouts,
2 s per traffic signal, turn penalties (7.5 s sigmoid with a right-hand bias), u-turns only at
dead ends (+20 s). Turn restrictions via a node, and via a way (with copies of the via way's
edges that can only be entered from the restriction's start). A blocking barrier (bollard,
block...) only stops through-traffic: each road ending at it becomes a dead end. Homes and
schools never snap onto motorways or motorway/trunk links.

## Checks

- `frontend/src/routing/roadGraph.test.js` runs on a hand-made network
  (`tools/road-graph/tiny.osm`); the workflow fails if the committed fixture is stale.
- Every build is compared with OSRM on the same OSM data: 40 home points x 300 school points.
  The build fails if the median ratio leaves 0.95-1.05, fewer than 95% of trips are within 20%,
  or the median rank correlation per home point drops below 0.98. October 2026: median 1.03,
  97% within 10%, rank correlation 0.998.
- The build fails if the gzipped graph is over 4 MB.
- A live user flow (`frontend/e2e/live/drive-times.test.js`) sets a location on the live site,
  waits for the times, checks the sort and fails if any main-thread task takes over 1 s. It
  posts how long the times took as a notice on the run (October 2026, GitHub runner: 2.9 s
  from opening the page, longest task 0.15 s).

## Phone cost (October 2026)

Chromium with the CPU slowed 6x: ~0.5 s to unpack, ~0.27 s per whole-city search, ~10 MB of
memory. Snapping uses a grid index built on first use (~0.5 s once), then ~0.5 ms per point,
so 300 school pins take ~0.16 s (brute force over every road took ~9 s). Not yet measured on a real phone.

## Licence

The graph is a derived database of OpenStreetMap: © OpenStreetMap contributors, ODbL 1.0.
The site credits OpenStreetMap next to the drive times, and the release notes carry the licence.
