import sys, json, csv, gzip, osmium
from shapely.geometry import shape, Point
from shapely.prepared import prep
from shapely import wkb
pbf, outdir = sys.argv[1], sys.argv[2]
gj = osmium.geom.GeoJSONFactory()
class H(osmium.SimpleHandler):
    def __init__(s):
        super().__init__(); s.areas = []; s.addr = []
    def area(s, a):
        if a.from_way(): return
        t = a.tags
        if t.get("boundary") != "administrative": return
        rid = a.orig_id()
        if t.get("admin_level") in ("7","8","9") or rid == 7276261:
            try: g = json.loads(gj.create_multipolygon(a))
            except Exception as e: print("geom fail", rid, e); return
            s.areas.append({"type":"Feature","properties":{"osm_relation_id":rid,"name":t.get("name"),"name:en":t.get("name:en"),"admin_level":t.get("admin_level")},"geometry":g})
    def _rec(s, typ, oid, lat, lon, t):
        s.addr.append((typ, oid, round(lat,7), round(lon,7), t.get("addr:street",""), t.get("addr:housenumber",""), t.get("addr:place",""), t.get("addr:postcode","")))
    def node(s, n):
        if "addr:housenumber" in n.tags and n.location.valid():
            s._rec("node", n.id, n.location.lat, n.location.lon, n.tags)
    def way(s, w):
        if "addr:housenumber" in w.tags:
            pts = [(nd.lat, nd.lon) for nd in w.nodes if nd.location.valid()]
            if len(pts) > 1 and pts[0] == pts[-1]: pts = pts[:-1]
            if pts: s._rec("way", w.id, sum(p[0] for p in pts)/len(pts), sum(p[1] for p in pts)/len(pts), w.tags)
h = H(); h.apply_file(pbf, locations=True, idx="flex_mem")
muni = [f for f in h.areas if f["properties"]["osm_relation_id"] == 7276261]
print("muni found", len(muni), "areas", len(h.areas))
M = shape(muni[0]["geometry"]); PM = prep(M)
dist = [f for f in h.areas if f["properties"]["osm_relation_id"] != 7276261 and PM.contains(shape(f["geometry"]).representative_point())]
import collections; print(collections.Counter(f["properties"]["admin_level"] for f in dist))
for lvl,fn in (("8","sofia_rayoni_osm_admin8.geojson"),("9","sofia_neighbourhoods_osm_admin9.geojson"),("7","sofia_osm_admin7.geojson")):
    fs=[f for f in dist if f["properties"]["admin_level"]==lvl]
    if fs: json.dump({"type":"FeatureCollection","features":fs}, open(f"{outdir}/{fn}","w",encoding="utf-8"), ensure_ascii=False); print(lvl, len(fs), sorted(f["properties"]["name"] for f in fs)[:30])
json.dump({"type":"FeatureCollection","features":muni}, open(f"{outdir}/stolichna_municipality_osm.geojson","w",encoding="utf-8"), ensure_ascii=False)

minx,miny,maxx,maxy = M.bounds; n=0
with gzip.open(f"{outdir}/stolichna_addresses_osm.csv.gz","wt",encoding="utf-8",newline="") as f:
    w = csv.writer(f); w.writerow(["osm_type","osm_id","lat","lng","street","housenumber","place","postcode"])
    for r in h.addr:
        if minx<=r[3]<=maxx and miny<=r[2]<=maxy and PM.contains(Point(r[3], r[2])):
            w.writerow(r); n+=1
print("addresses", n, "of BG", len(h.addr))
