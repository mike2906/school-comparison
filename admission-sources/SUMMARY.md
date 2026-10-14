# Admission source data: summary (gathered 2026-10-14)

Temporary data drop for the admission points / catchment work. Read it, then delete the branch.

## Headline
Sofia publishes official **first-grade catchment polygons** on ArcGIS (`arcgis_sofia/`). Neither address geocoding nor OSM is needed to draw a school's catchment.
Every source keys schools by the same GIS id: urbandata `ИД на прилежащо училище` = kg.sofia.bg `esriId` = arcgis `integrationid`/`schoolid`. All 159 match.
Catchment display has two tiers:
- own area (прилежащ) = the school's polygon;
- neighbouring area (граничен) = the union of the polygons of the schools whose `adjacentezonesid` lists it. It gives +5 points, and only in group 4.

## Files
| Path | What |
|---|---|
| catchments/addresses-and-associated-schools.xlsx.gz | urbandata xlsx, original (gzipped) |
| catchments/addresses_and_schools.csv.gz | its one sheet as UTF-8 CSV: 106,629 addresses → catchment school id + neighbouring ids |
| catchments/README.md | columns, ids, how гранични are represented, 10 sample rows |
| arcgis_sofia/school_catchment_polygons.geojson | 159 official catchment polygons, WGS84 |
| arcgis_sofia/schools_points.geojson | 252 school points with `adjacentezonesid` |
| arcgis_sofia/districts_rayoni_polygons.geojson | 24 official район polygons |
| arcgis_sofia/kindergartens_points.geojson | 377 kindergarten points |
| arcgis_sofia/*.layer.json, README.md | layer definitions and notes |
| kg_sofia/*.json | raw kg.sofia.bg API: 159 schools, 342 kindergartens, 324 preparatory, regions, criteria codes, campaign state |
| kg_sofia/README.md | endpoints and fields. **The repo adapter's `/kg/type/{type}/all` paths now return 500; they need `?filterType=by_region&kgType=0&regionId=0`.** |
| osm/stolichna_addresses_osm.csv.gz | 44,548 OSM address points in Столична община |
| osm/stolichna_municipality_osm.geojson, sofia_settlements_osm_admin8.geojson, sofia_neighbourhoods_osm_admin9.geojson | OSM boundaries |
| osm/README.md, pbf_extract.py | method and notes |
| legal/kg_ordinance.txt + kg_ordinance_NOTES.md | consolidated kindergarten ordinance (Ciela) as amended by Реш. 200/12.03.2026; criteria, nursery point, address rules |
| legal/reshenie_200_2026.{pdf,txt}, reshenie_200_PZR.txt | Реш. 200 cover page and its transitional provisions (ПЗР) |
| legal/first_grade_system.{txt,pdf} + first_grade_NOTES.md | consolidated 1st-grade system (12sou copy of the sofia.obshtini.bg print); groups, extra points, address lock, 2026 schedule |
| legal/reshenie_201_2026.{pdf,txt} | Реш. 201 cover page |
| legal/court_appeals_NOTE.md | court outcomes, from press reports |
| legal/naredba10_art57_59.txt | Наредба 10/2016, чл. 48(1) and 55–60 (8th-grade score) |
| legal/naredba10_art73_74_grade5.txt + grade5_NOTE.md | 5th grade: no Sofia system; the national one is for math/science profiled schools only |
| legal/grade8_minmax_2026.csv + grade8_2026/ + grade8_NOTES.md | 2026 8th-grade min/max бал per class, rounds 1–4 (1,584 rows) + original xlsx + draft plan PDF |

## Failed / missing
- **OSM via Overpass:** every server failed with 504 or a timeout, so I used the Geofabrik PBF instead.
- **OSM районa:** OSM has no 24-district layer; use the official arcgis layer 3.
- **kg.sofia.bg per-address lookup:** `POST /public/checkAddress/` needs a reCAPTCHA token, and `/api/school/details` returns 401. Neither is needed thanks to arcgis.
- **8th grade:** the RUO list of each class's scoring formula ("с кодове и балообразуване 30.06.2026") is behind a Cloudflare challenge on ruo.mon.bg, so there is no grade8_plan_2026.csv. A direct `/files/` URL copied from a browser would download with curl.
- **2025 8th-grade min/max:** skipped.
- **Решение 200/201 annexes:** the amending texts (Приложение № 1) were not found on council.sofia.bg; the consolidated texts are used instead.
- **Kindergarten чл. 18 т. 1 before 2026:** not available.
- **1st-grade 2026 schedule:** from press reports and a school notice, because kg.sofia.bg is a JavaScript app.
- **Court acts:** press reports only, no case texts.
