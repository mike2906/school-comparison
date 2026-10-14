# OpenStreetMap: Столична община boundaries and address points

Source: Geofabrik `bulgaria-latest.osm.pbf` (OSM data as of 2026-10-13T20:21Z), © OpenStreetMap contributors, ODbL.
Overpass was unusable on 2026-10-14: overpass-api.de and the mail.ru mirror returned 504 and private.coffee timed out. I therefore processed the PBF locally with `pbf_extract.py` (pyosmium + shapely).
Clipping polygon: OSM relation 7276261 "Столична".

| File | Content |
|---|---|
| stolichna_municipality_osm.geojson | the municipality boundary (relation 7276261) |
| sofia_settlements_osm_admin8.geojson | 38 admin_level=8 areas: гр. София and the 37 other settlements/кметства in the municipality |
| sofia_neighbourhoods_osm_admin9.geojson | 121 admin_level=9 areas: Sofia's ж.к./кв. neighbourhoods (not the районa) |
| stolichna_addresses_osm.csv.gz | 44,548 address points: osm_type, osm_id, lat, lng, street, housenumber, place, postcode. Ways use the mean of their nodes. |
| pbf_extract.py | the script used |

**The 24 районa are not admin boundaries in OSM** (admin_level 8 = settlements, 9 = neighbourhoods).
Use the official polygons in `../arcgis_sofia/districts_rayoni_polygons.geojson` (24 features, `num` + `name_rajon`).

Address points: 40,578 are ways (buildings) and 3,970 are nodes. 36,639 have addr:street, 5,644 only addr:place, and 2,265 neither (house number only).
OSM addresses cover well under half of the 106,629 ГРАО addresses in the urbandata catchment file.
For drawing catchments, use the official polygons (`../arcgis_sofia/school_catchment_polygons.geojson`) instead.
