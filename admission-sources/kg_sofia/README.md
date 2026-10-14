# kg.sofia.bg public API (raw JSON)

Retrieved: 2026-10-14. Base: `https://kg.sofia.bg/api` (SPA `main.e7b27bb19f002f04.js`, `apiUrl:"/api"`).

**The repo adapter's paths are broken.** `/api/public/kg/type/{type}/all` with no query string now returns HTTP 500.
The SPA calls `/api/public/kg/type/{type}/{grpType}?filterType=…&kgType=…&regionId=…`.
`school/all?filterType=by_region&kgType=0&regionId=0` (exactly what the SPA's `loadAllSchool()` sends) works, and so do the same parameters for `kinderGarden` and `preparative`.
`backend/app/scrapers/sources/bg/kg_sofia.py` will need this query string.

| File | Endpoint | Content |
|---|---|---|
| schools_all.json | GET /public/kg/type/school/all?filterType=by_region&kgType=0&regionId=0 | `items.kinderGardens`: 159 schools; `items.regions`: 24 districts + "всички" |
| kindergartens_all.json | GET /public/kg/type/kinderGarden/all?… (same params) | 342 kindergartens |
| preparative_all.json | GET /public/kg/type/preparative/all?… | 324 preparatory-group providers |
| regions_all.json | GET /public/regions/all | district list |
| school_draft.json | GET /public/school/draft | current school campaign info (`currentDraft`, `freeSpots`) |
| school_state.json / kg_state.json | GET /public/schoolState, /public/state | campaign state strings |
| criteria_codes.json | GET /public/admin/criteria-codes | criteria codes, e.g. KG_K01..KG_X17 (type 1 = kindergarten), S_K01.. (type 2 = school) |

Item fields: `id` (kg.sofia.bg id), `name.publicName`, `name.publicType` (СУ/ОУ/…), `nameStr`, `address` (free text), `region` (district name), `contacts[]`, and **`esriId`**. `esriId` is the GIS id used by urbandata and arcgis.sofia.bg (see ../catchments/README.md). For kindergartens `esriId` = `id`, and 324 of 342 match arcgis layer 0 `integrationid`.

## Catchment (прилежащ / граничен район) endpoints
- `POST /api/public/checkAddress/` takes an address and returns `items.kinderGardens.kinderGarden` (the прилежащо училище) and `items.kinderGardens.nearBy` (гранични). It needs a reCAPTCHA v3 token (`recaptcha` field), so I did not call it.
- The school page (`#/school/{id}`) calls `GET /api/school/details?id=` → 401 (needs login).
- The SPA's map links go to `https://arcgis.sofia.bg/EsriBg.Sofia.Mun.Schools/?schoolId=` / `?kinderGartenId=`. That ArcGIS app serves the catchment polygons publicly, saved in `../arcgis_sofia/`. This is the usable source.
- Other public paths seen in the bundle: `/public/kg/draft/{year}[/{region}]`, `/public/school/draft/{region}`, `/public/free/spots/{type}?timeMode=&draft=&regionId=`, `/public/{type}/dz[/by-region|by-type]`, `/public/search/{school|kindergarten}?searchTerms=`, `/public/calendarEvents`.
