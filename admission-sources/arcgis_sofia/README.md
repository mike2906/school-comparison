# arcgis.sofia.bg: official school catchment polygons

Retrieved: 2026-10-14. MapServer: `https://arcgis.sofia.bg/arcgis/rest/services/Schools/SchoolsZones/MapServer`.
It backs the map app `https://arcgis.sofia.bg/EsriBg.Sofia.Mun.Schools/` that kg.sofia.bg links to (config `BaseProject/Config/config-bg.json`, `Data.json`).
Downloaded with `query?where=1=1&outFields=*&outSR=4326&f=geojson`, so coordinates are WGS84 lon/lat. All counts were below maxRecordCount (1000), so nothing was cut off.
`*.layer.json` holds each layer's definition (fields, domains, renderer).

| File | Layer | Features | Key fields |
|---|---|---|---|
| school_catchment_polygons.geojson | 2 "Райони към училищата" | 159 MultiPolygons | `schoolid` (= `id`) = GIS school id, `name` |
| schools_points.geojson | 1 "Училища" | 252 points | `integrationid` = GIS school id (= kg.sofia.bg `esriId`), `adjacentezonesid` = comma list of neighbouring catchment ids (set for the 159 schools with a catchment), `fullname`, `fulladdress`, `schooltype` (coded; see layer json), `preduch`, `hyperlink` |
| districts_rayoni_polygons.geojson | 3 "Райони" | 24 Polygons | `num` (01–24), `name_rajon` |
| kindergartens_points.geojson | 0 "Детски градини" | 377 points | `integrationid` (= kg.sofia.bg kindergarten id/esriId), `fullname`, `kindergardentype`, `zabelevka` |

Cross-checks: the 159 polygon ids equal the 159 catchment school ids in the urbandata xlsx and the 159 schools on kg.sofia.bg (via `esriId`). The urbandata per-address border lists equal `adjacentezonesid` for all 159.

**Drawing a school's catchment:** own area (прилежащ) = its polygon from layer 2. Neighbouring area (граничен) = union of the polygons of every school Z whose `adjacentezonesid` contains this school's id.
Kindergartens have no catchment polygons. Their address criterion is district-based, and the district polygons are in layer 3.
