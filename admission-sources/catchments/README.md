# Sofia first-grade catchments: addresses → schools

Source: https://urbandata.sofia.bg/dataset/34e764b9-dc1f-466d-9eed-ff0d4123bab4/resource/f1a40d7a-2e38-46c7-81a1-d5d8bd45261f/download/addresses-and-associated-schools.xlsx
Retrieved: 2026-10-14

## Files
- `addresses-and-associated-schools.xlsx.gz`: the original xlsx, unchanged (gzipped because it is 5.05 MB).
- `addresses_and_schools.csv.gz`: the single sheet as UTF-8 CSV with a header row and all values as text. Leading zeros are kept (`01`, `095`).

## Sheets
One sheet, `Адреси и прилежащи училища`: 106,629 data rows plus a header, 12 columns.

| Column | Meaning | Notes |
|---|---|---|
| OBJECTID | row id in the source GIS layer | unique |
| Код на населено място ГРАО | ЕКАТТЕ/ГРАО settlement code | 54 distinct (68134 = гр. София) |
| Име на населено място | settlement | e.g. `ГР.СОФИЯ`, `ГР.СОФИЯ,КВ.ДРАГАЛЕВЦИ`, `С.БИСТРИЦА`, `ГР.БАНКЯ` |
| Код на район | district code `01`–`24` | 24 distinct (same codes as kg.sofia.bg `regions` and arcgis `num`) |
| Наименование на район | district name | |
| Код на ПА ГРАО | ГРАО street (пътна артерия) code | 3,665 distinct; codes repeat across settlements, so key on (settlement code, street code) |
| Наименование на пътна артерия | street / ж.к. name, upper case | e.g. `УЛ.АВИЦЕНА`, `БУЛ.ВАСИЛ ЛЕВСКИ`, `Ж.К.ЛЮЛИН` |
| Номер | house or block number, zero-padded to 3 digits | `095`, `544` (in a ж.к. this is the block number) |
| Вход | entrance | blank in 86,133 rows; `А`, `Б`, `04`… |
| ИД на прилежащо училище | id of the address's own catchment school (прилежащо училище) | 159 distinct |
| Прилежащо училище | school name | |
| ИД на гранични прилежащи училища | comma-separated ids of the neighbouring-catchment schools (гранични прилежащи училища) | 1–8 ids per address |

No coordinates. For geometry use `../arcgis_sofia/school_catchment_polygons.geojson` (same ids) or match addresses to OSM points (`../osm/`).

## Neighbouring (гранични) catchments
They are stored per address as an id list, but the list depends only on the address's own school:
every address in school X's catchment carries the same list. The one exception is 542, which has the same ids written with and without spaces.
The list equals the `adjacentezonesid` field of school X in arcgis layer 1 (checked: 159 of 159 match).
So the граничен район of school Y = all addresses whose list contains Y = the union of the catchment polygons of the schools Z whose `adjacentezonesid` contains Y.
Adjacency is almost symmetric (658 directed pairs, 8 one-directional), so always compute it in that direction.

## How schools are identified
By a numeric GIS id (`ИД на прилежащо училище`, e.g. 616 = 1. СУ "Пенчо П. Славейков"), not by name or school number.
The same id is:
- `esriId` in the kg.sofia.bg school list (`../kg_sofia/schools_all.json`). kg.sofia.bg's own `id` is different: 1. СУ is `id` 629, `esriId` 616. All 159 match.
- `integrationid` in arcgis layer 1 (school points) and `schoolid`/`id` in layer 2 (catchment polygons). All 159 match.

## 10 sample rows (random)
```
OBJECTID,Код на населено място ГРАО,Име на населено място,Код на район,Наименование на район,Код на ПА ГРАО,Наименование на пътна артерия,Номер,Вход,ИД на прилежащо училище,Прилежащо училище,ИД на гранични прилежащи училища
62701,68134,ГР.СОФИЯ,15,Младост,80128,Ж.К.МЛАДОСТ 1,019,04,595,"118. СУ ""Акад. Людмил Стоянов""","596, 600, 662, 663, 591"
90895,68134,ГР.СОФИЯ,07,Слатина,07421,УЛ.ИВАН ЩЕРЕВ,032,,653,"109. ОУ ""Христо Смирненски""","649, 651, 655"
76130,68134,ГР.СОФИЯ,17,Витоша,05236,УЛ.ПРЕКИ ПЪТ,016,,517,"5. ОУ ""Иван Вазов""","520, 516, 733, 670"
13966,07140,ГР.БУХОВО,22,Кремиковци,85500,УЛ.ЧАВДАР ВОЙВОДА,012,,569,"117. СУ ""Св. Св. Кирил и Методий""","568, 570"
80333,68134,ГР.СОФИЯ,03,Възраждане,12646,УЛ.ОДРИН,105,,531,"18. СУ ""Уилям Гладстон""","534, 537, 532, 533, 536, 542, 642"
85662,68134,ГР.СОФИЯ,04,Оборище,18633,УЛ.ХРИСТО КОВАЧЕВ,014,,617,"112. ОУ ""Стоян Заимов""","616, 618, 643, 659, 640, 649, 661"
63851,68134,ГР.СОФИЯ,19,Люлин,80180,Ж.К.ЛЮЛИН,544,А,588,"97. СУ ""Братя Миладинови""","587, 579, 589, 621"
42788,68134,ГР.СОФИЯ,05,Сердика,11377,УЛ.ПЪРВА БЪЛГАРСКА АРМИЯ,109,,645,"58. ОУ ""Сергей Румянцев""","646, 603, 609, 647"
63240,68134,ГР.СОФИЯ,17,Витоша,80176,Ж.К.БЪКСТОН,028,Г,516,"2. СУ ""Акад. Емилиян Станев""","733, 517, 620, 565"
688,00357,ГР.НОВИ ИСКЪР,21,Нови Искър,75407,УЛ.СВ.ИВАН РИЛСКИ,001,,610,"170. СУ ""Васил Левски""","612, 611, 604"
```
