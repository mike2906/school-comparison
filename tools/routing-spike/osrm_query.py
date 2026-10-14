"""Answer spike pairs with a local OSRM: one table per source, plus full routes for a few."""
import json
import sys
import urllib.request

BASE = "http://localhost:5000"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=120) as r:
        return json.load(r)


pairs = json.load(open(sys.argv[1]))
dests = ";".join(f"{x:.6f},{y:.6f}" for x, y in pairs["dests"])
out = {"tables": [], "routes": []}
for i, (x, y) in enumerate(pairs["sources"]):
    t = get(f"/table/v1/driving/{x:.6f},{y:.6f};{dests}?sources=0&annotations=duration,distance")
    out["tables"].append({"source": [x, y], "durations": t["durations"][0][1:],
                          "distances": t["distances"][0][1:],
                          "snapped_source": t["sources"][0]["location"]})
for (x, y), (a, b) in pairs.get("routes", []):
    r = get(f"/route/v1/driving/{x:.6f},{y:.6f};{a:.6f},{b:.6f}?overview=full&geometries=geojson")
    out["routes"].append({"from": [x, y], "to": [a, b], "duration": r["routes"][0]["duration"],
                          "distance": r["routes"][0]["distance"],
                          "geometry": r["routes"][0]["geometry"]["coordinates"]})
json.dump(out, open(sys.argv[2], "w"))
print(len(out["tables"]), "tables,", len(out["routes"]), "routes")
