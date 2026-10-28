# Outdoor space

Each school page has an "Outdoor space" card, and compare a row: per building, what a
source shows about a yard, garden, playground or pitch the pupils can use. Mike asked for it
on 2026-10-26 after hearing that Maple Bear Kambanite rents part of an office block.

## Rule: sources only add

Schools write about the yard they have, never that they have none, and a building with no
school plot on the map can still have a play area: Maple Bear Kambanite is in an office
block, yet a visit on 2026-10-26 found a small play area in the business park's garden and a
small artificial-turf pitch. So no source except a visit can say "no outdoor space"; a
building none of them covers reads "Not known", with a link to email us a photo.

## Sources, strongest first

Computed per request in `backend/app/utils/outdoor_space.py` (`outdoor_space` on the detail
and compare responses):

| Source | Answer | Where it comes from |
| --- | --- | --- |
| Visit | what was seen, with the date | `backend/app/utils/outdoor_visits.json`, by school id and address words; photos in the project files (`outdoor-space/visits/`) |
| School website | "The school's website says:" and the facts, each linking its page | shown facilities facts (not "minor" ones, which the page hides) that name a yard, garden, outdoor playground or pitch; a court, "игрище" or "площадка" counts only in a fact naming no indoor hall (`school-facts` stage; the reader is asked for them). Only for a school with one building: with several, the page rarely says which, so the facts are listed for the school |
| OpenStreetMap, own plot | its own grounds, with open ground (plot less buildings and multipolygon holes, to 100 m²; under 100 m² is not claimed), pitches and playgrounds | `outdoor-grounds` stage, `attributes["outdoor_grounds"]`; the plot carries the school's MoE code, or no code and a name whose every distinctive word is in the school's with no differing school number. A school outline tagged as a building is a building, not grounds |
| OpenStreetMap, another school's plot | "In the grounds of X; whether the pupils use them is not known" | the pin is inside a plot carrying another school's MoE code; X is named from our own data |
| Register address | in another school's building / on a floor of a larger building; yard not known | "сградата на", or a floor, flat, ground floor or entrance in the address ("бл." alone is not enough) |

A stored plot is ignored once the pin moves more than 30 m, and hidden (approximate) pins
get no OSM answer. Not used: the building type under the pin (office, flats), which rests on
the pin alone and misled at Maple Bear; a vision model on aerial photos (cost, unreliable
plot shapes); parent photo uploads (user data, Mike's call).

## Refresh

`outdoor-grounds` (free, one Overpass query, no model) runs in `refresh_pins.sh` after the
monthly register import, once the week's pins are placed: a dry run saves the OSM answer
(`--osm-json`), and when plots changed the script backs up the DB and applies from that same
answer, before the publish. Only schools whose plots changed are written. A failure is
logged and never holds the pins back. A run with fewer than 100 school plots (a cut Overpass
answer) stops. By hand:

```bash
cd backend
uv run python -m app.scrapers.cli outdoor-grounds --out /tmp/outdoor.json          # dry run
uv run python -m app.scrapers.cli outdoor-grounds --apply --out /tmp/outdoor.json  # store
```

To add a visit, add an entry to `outdoor_visits.json` (school id, words of the building's
register address, date, `kind` `grounds` or `none`, text in both languages) by PR.

## Licence

The stored plots are a derived database of OpenStreetMap: © OpenStreetMap contributors,
ODbL 1.0. The API serves them openly and the page credits OpenStreetMap beside any answer
from it, as for drive and transit times.

## Coverage (sample, 2026-10-26)

From the website facts alone: 27 of 70 private schools and 50 of 101 private kindergartens.
In a 126-school OpenStreetMap sample every state school had its plot mapped, private
campuses too; private kindergartens in houses almost never.
