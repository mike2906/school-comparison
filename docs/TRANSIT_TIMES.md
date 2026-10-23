# Public transport times

For "My child's options" (`docs/MY_CHILD_OPTIONS.md`): when to leave home, by public transport,
to arrive at each school by the start of the day, and how long the trip takes. Like drive times
(`docs/DRIVE_TIMES.md`), everything runs in the browser on whole-city files, so the home never
leaves it.

## How it works

1. **Data.** `.github/workflows/transit.yml` builds two files every month (6th, after the map pin
   refresh), by hand, and as a check on pull requests that touch the builders or the router. It
   publishes them to the `transit` release and starts a site-only deploy; `deploy.yml` copies
   them to `/routing/`.
   - `sofia-foot.bin.gz` (~1.2 MB): the walking network from OpenStreetMap
     (`tools/transit/build_foot_graph.py`, format in its docstring). OSRM's foot profile for
     which ways can be walked (no motorways or trunk roads, no `foot=no`, walls and fences
     block), every way two-way at 4 km/h.
   - `sofia-timetable.bin.gz` (~0.2 MB): one school day of Sofia's official GTFS
     (`tools/transit/build_timetable.mjs`, format in its header). The day is pinned: the first
     Tuesday to Thursday from tomorrow with a normal weekday's number of trips, so not a
     holiday timetable. Whole trips that run between 06:00-09:00 or 11:30-14:00 are kept. It also
     holds the walks between stops (on the walking network, up to 8 min; the feed's own
     transfer time where it is longer, which matters inside metro stations) and, for every
     school building the API lists, the stops within 15 min on foot.
2. **Search** (`frontend/src/routing/transit.js`, `footGraph.js`). A walk from home over the
   walking network finds the stops within 20 min, and the schools within 40 min on foot. One
   range RAPTOR search then goes backwards from the latest departure: for each school it finds
   the latest time to leave home that still arrives by the deadline, with up to three changes.
   A building moved since the monthly build is walked in the browser.
3. **Choosing the trip.** Among the ways to arrive in time, each change counts as 5 minutes, so a
   parent is not sent on an extra change to leave 2 minutes later. Walking all the way wins
   when it takes no longer.

## Rules a parent would recognise

- Walking pace 4 km/h, a parent with a young child (OSRM uses 5).
- Be at the first stop 2 min before the vehicle leaves; 2 min for every change on top of the walk
  between stops.
- Up to 20 min on foot to the first stop and 15 min from the last stop.
- Trips longer than 100 min are not shown.

## Checks

- `frontend/src/routing/transit.test.js` runs on a hand-made street map
  (`tools/transit/tiny.osm`) and timetable (`tools/transit/tiny-gtfs/`): walking rules, the
  pinned day, the latest bus, a change with the feed's transfer time, walking instead.
  The workflow fails if the committed walking-graph fixture is stale.
- Every build is checked against the official journey planner (sofiatraffic.bg, the city's own
  OpenTripPlanner): 30 seeded home and school pairs, arriving by 08:00 on the pinned day. For
  each, the planner is asked for the earliest arrival when leaving home at the time we show.
  The build fails if fewer than 80% arrive by 08:02, or if we find no trip for more than 10%
  of the pairs the planner can do. If the planner cannot be asked, the build publishes with a
  warning.
- The build fails if the two files are over 3 MB gzipped together.

## October 2026 spike

- Size: 1.16 MB + 0.16 MB gzipped (3,180 stops, 373 stop patterns, 6,338 trips, 150k stop
  times, 27k walks between stops).
- Speed, Chromium with the CPU slowed 6x: unpack 0.24 s, school buildings 0.19 s (once), a
  search from home 0.3-0.55 s. Without the slowdown 0.04-0.17 s.
- Against the official planner, arrive by 08:00 on Tuesday 27 October: with 4.5 km/h and 1 min
  at the first stop, the trip times matched closely (median ratio 1.02 against the planner's own
  arrive-by answer) but 7 of 30 "leave at" times were more than 2 min late by the planner. The
  planner walks more slowly than OSRM does and does not always start a walk at the shortest
  path. At 4 km/h and 2 min, 26 of 30 are on time or within 2 min, and our trips are a median
  13% longer than the planner's. A parent is better served by a time that is a few minutes
  early, so those are the defaults.
- The misses are long trips with changes where the planner prefers a different connection.

## Licence

The timetable is Sofia's open data: © Център за градска мобилност ЕАД / Столична община,
CC BY 4.0, modified (one day, packed). The walking network is a derived database of
OpenStreetMap: © OpenStreetMap contributors, ODbL 1.0. The site credits both next to the times;
the release notes carry the licences.
