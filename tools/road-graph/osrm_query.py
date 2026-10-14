"""Ask a local OSRM (car profile) for one row of the duration table per sample home point."""
import json
import sys
import urllib.request

BASE = "http://localhost:5000"

pairs = json.load(open(sys.argv[1]))
dests = ";".join(f"{x:.6f},{y:.6f}" for x, y in pairs["dests"])
tables = []
for x, y in pairs["sources"]:
    url = f"{BASE}/table/v1/driving/{x:.6f},{y:.6f};{dests}?sources=0&annotations=duration"
    with urllib.request.urlopen(url, timeout=120) as r:
        tables.append({"durations": json.load(r)["durations"][0][1:]})
json.dump({"tables": tables}, open(sys.argv[2], "w"))
print(f"{len(tables)} OSRM tables")
