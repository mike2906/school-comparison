"""Build a compact edge-based car graph for Sofia from an OSM extract (routing spike).

Follows OSRM's car profile loosely: same highway speeds, maxspeed x 0.8, access blacklist,
oneways and roundabouts, blocking barriers, 2 s per traffic signal, turn penalties computed in
the browser from stored bearings, no_/only_ turn restrictions (via-node only).

Output (little-endian, one file, gzip/brotli by the server):
  header  Uint32[8]: magic, version, nNodes, nEdges, nChains, nGeomPts, nRestr, flags
  nodeLat, nodeLon        Int32[nNodes]  1e-6 deg, delta-coded in node order
  firstOut                Uint32[nNodes+1] delta-coded (= out-degree)
  target                  Int32[nEdges]  target - source (zigzag-free, small after Hilbert sort)
  weight                  Uint16[nEdges] deciseconds, off-peak
  [peak                   Uint16[nEdges] deciseconds, only with --peak-array]
  nRoads                  Uint8[nNodes]  distinct neighbour nodes
  bearingIn, bearingOut   Uint8[nEdges]  bearing at edge start / end, 256 steps
  chainFwd, chainBwd      Int32[nChains] edge id or -1 (chains = undirected way pieces)
  chainNPts               Uint16[nChains] interior shape point count
  geom                    Int16[2*nGeomPts] interior points, 1e-5 deg deltas from previous point
  restrFrom, restrTo      Uint32[nRestr] banned (incoming edge, outgoing edge) pairs
"""
import argparse
import json
import math
import struct
import sys
from collections import defaultdict

import numpy as np
import osmium
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components

SPEEDS = {  # OSRM car.lua v5.27, km/h
    "motorway": 90, "motorway_link": 45, "trunk": 85, "trunk_link": 40,
    "primary": 65, "primary_link": 30, "secondary": 55, "secondary_link": 25,
    "tertiary": 40, "tertiary_link": 20, "unclassified": 25, "residential": 25,
    "living_street": 10, "service": 15,
}
ACCESS_BLACKLIST = {"no", "private", "agricultural", "forestry", "emergency", "psv", "customers"}
ACCESS_TAGS = ("motorcar", "motor_vehicle", "vehicle", "access")
BARRIER_OK = {"cattle_grid", "border_control", "toll_booth", "sally_port", "gate", "lift_gate",
              "no", "entrance", "height_restrictor", "arch"}
SNAP_EXCLUDE = {"motorway", "motorway_link", "trunk_link"}
SIGNAL_DS = 20  # deciseconds per traffic signal
MAXSPEED_ZONES = {"bg:urban": 50, "bg:rural": 90, "bg:motorway": 140, "bg:trunk": 120,
                  "bg:living_street": 20, "walk": 5}


def parse_maxspeed(v):
    if not v:
        return None
    v = v.strip().lower()
    if v in MAXSPEED_ZONES:
        return MAXSPEED_ZONES[v]
    try:
        if v.endswith("mph"):
            return float(v[:-3]) * 1.609
        return float(v.split()[0])
    except ValueError:
        return None


def car_access(tags):
    for k in ACCESS_TAGS:
        v = tags.get(k)
        if v:
            return v not in ACCESS_BLACKLIST
    return True


class Reader(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.ways = []  # (way_id, hw, oneway, speed_kmh, [node ids], [(lon,lat)])
        self.signals = set()
        self.blocked = set()
        self.restrictions = []  # (kind, from_way, via_node, to_way)
        self.via_way_skipped = 0

    def node(self, n):
        t = n.tags
        if t.get("highway") == "traffic_signals":
            self.signals.add(n.id)
        b = t.get("barrier")
        if b and b not in BARRIER_OK:
            if t.get("motorcar") not in ("yes", "designated") and t.get("access") not in ("yes",):
                self.blocked.add(n.id)

    def way(self, w):
        t = w.tags
        hw = t.get("highway")
        if hw not in SPEEDS or t.get("area") == "yes" or not car_access(t):
            return
        if t.get("service") == "emergency_access":
            return
        ow = t.get("oneway", "")
        if ow in ("yes", "1", "true"):
            oneway = 1
        elif ow == "-1":
            oneway = -1
        elif ow == "reversible":
            return
        elif ow == "no":
            oneway = 0
        else:
            oneway = 1 if (t.get("junction") in ("roundabout", "circular") or hw == "motorway") else 0
        ms = parse_maxspeed(t.get("maxspeed"))
        speed = ms * 0.8 if ms and ms > 0 else SPEEDS[hw]
        if hw == "service" and t.get("service") in ("parking_aisle", "driveway", "alley", "parking"):
            speed *= 0.5
        try:
            pts = [(nd.lon, nd.lat) for nd in w.nodes]
        except osmium.InvalidLocationError:
            return
        self.ways.append((w.id, hw, oneway, speed, [nd.ref for nd in w.nodes], pts))

    def relation(self, r):
        t = r.tags
        if t.get("type") != "restriction":
            return
        kind = t.get("restriction:motorcar") or t.get("restriction")
        if not kind or "motorcar" in (t.get("except") or ""):
            return
        frm = [m.ref for m in r.members if m.role == "from" and m.type == "w"]
        to = [m.ref for m in r.members if m.role == "to" and m.type == "w"]
        via = [(m.type, m.ref) for m in r.members if m.role == "via"]
        if len(frm) != 1 or len(to) != 1 or len(via) != 1:
            return
        if via[0][0] != "n":
            self.via_way_skipped += 1
            return
        self.restrictions.append((kind, frm[0], via[0][1], to[0]))


def hav(a, b):
    lon1, lat1 = map(math.radians, a)
    lon2, lat2 = map(math.radians, b)
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def bearing(a, b):
    lon1, lat1 = map(math.radians, a)
    lon2, lat2 = map(math.radians, b)
    y = math.sin(lon2 - lon1) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(lon2 - lon1)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def hilbert(x, y, order=16):
    d = 0
    s = 1 << (order - 1)
    while s:
        rx = 1 if x & s else 0
        ry = 1 if y & s else 0
        d += s * s * ((3 * rx) ^ ry)
        if ry == 0:
            if rx == 1:
                x, y = s - 1 - x, s - 1 - y
            x, y = y, x
        s >>= 1
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pbf")
    ap.add_argument("out")
    ap.add_argument("--peak-array", action="store_true")
    ap.add_argument("--peak-factor", type=float, default=1.73)
    ap.add_argument("--meta", help="write builder-side metadata (edge ways etc.) as JSON")
    a = ap.parse_args()

    rd = Reader()
    rd.apply_file(a.pbf, locations=True)
    print(f"ways {len(rd.ways)}, signals {len(rd.signals)}, blocked {len(rd.blocked)}, "
          f"restrictions {len(rd.restrictions)} (+{rd.via_way_skipped} via-way skipped)", file=sys.stderr)

    use = defaultdict(int)
    for _, _, _, _, refs, _ in rd.ways:
        for i, r in enumerate(refs):
            use[r] += 2 if i in (0, len(refs) - 1) else 1
    split = {n for n, c in use.items() if c > 1}
    split |= {v for _, _, v, _ in rd.restrictions}
    split |= rd.blocked

    # Chains: maximal runs of a way between split nodes.
    loc = {}
    chains = []  # (way_id, hw, oneway, speed, [osm node ids], [pts])
    for wid, hw, oneway, speed, refs, pts in rd.ways:
        start = 0
        for i in range(1, len(refs)):
            if refs[i] in split or i == len(refs) - 1:
                cr, cp = refs[start:i + 1], pts[start:i + 1]
                start = i
                if any(r in rd.blocked for r in cr):  # blocking barrier is a chain end: drop chains touching it
                    continue
                if len(cr) < 2 or (cr[0] == cr[-1] and len(cr) == 2):
                    continue
                chains.append((wid, hw, oneway, speed, cr, cp))
                loc[cr[0]] = cp[0]
                loc[cr[-1]] = cp[-1]

    # Directed edges.
    raw = []  # (src osm, dst osm, ds, bIn, bOut, chain idx, dir, way)
    for ci, (wid, hw, oneway, speed, cr, cp) in enumerate(chains):
        length = sum(hav(cp[k], cp[k + 1]) for k in range(len(cp) - 1))
        sig = sum(1 for r in cr[1:-1] if r in rd.signals)
        ds = length / (speed / 3.6) * 10 + sig * SIGNAL_DS
        for d in ((1,) if oneway == 1 else (-1,) if oneway == -1 else (1, -1)):
            p = cp if d == 1 else cp[::-1]
            r = cr if d == 1 else cr[::-1]
            if r[-1] in rd.signals:
                ds_e = ds + SIGNAL_DS
            else:
                ds_e = ds
            raw.append((r[0], r[-1], ds_e, bearing(p[0], p[1]), bearing(p[-2], p[-1]), ci, d, wid))

    # Largest strongly connected component.
    ids = {n: i for i, n in enumerate(loc)}
    src = np.array([ids[e[0]] for e in raw])
    dst = np.array([ids[e[1]] for e in raw])
    m = csr_matrix((np.ones(len(raw)), (src, dst)), shape=(len(ids), len(ids)))
    ncomp, lab = connected_components(m, directed=True, connection="strong")
    big = np.bincount(lab).argmax()
    keep_node = lab == big
    print(f"nodes {len(ids)}, edges {len(raw)}, SCCs {ncomp}, largest {keep_node.sum()}", file=sys.stderr)

    # Hilbert node order.
    nodes = [n for n in loc if keep_node[ids[n]]]
    minx = min(loc[n][0] for n in nodes); maxx = max(loc[n][0] for n in nodes)
    miny = min(loc[n][1] for n in nodes); maxy = max(loc[n][1] for n in nodes)
    def hkey(n):
        x, y = loc[n]
        return hilbert(int((x - minx) / (maxx - minx) * 65535), int((y - miny) / (maxy - miny) * 65535))
    nodes.sort(key=hkey)
    nid = {n: i for i, n in enumerate(nodes)}

    raw = [e for e in raw if e[0] in nid and e[1] in nid]
    raw.sort(key=lambda e: (nid[e[0]], nid[e[1]]))
    E = len(raw)
    N = len(nodes)
    first_out = np.zeros(N + 1, dtype=np.uint32)
    for e in raw:
        first_out[nid[e[0]] + 1] += 1
    first_out = np.cumsum(first_out).astype(np.uint32)
    tgt = np.array([nid[e[1]] - nid[e[0]] for e in raw], dtype=np.int32)
    w = np.array([min(65535, max(1, round(e[2]))) for e in raw], dtype=np.uint16)
    b_in = np.array([round(e[3] / 360 * 256) % 256 for e in raw], dtype=np.uint8)
    b_out = np.array([round(e[4] / 360 * 256) % 256 for e in raw], dtype=np.uint8)

    # Chains with surviving edges.
    ch_edges = defaultdict(lambda: [-1, -1])
    for ei, e in enumerate(raw):
        ch_edges[e[5]][0 if e[6] == 1 else 1] = ei
    chain_ids = [ci for ci in sorted(ch_edges, key=lambda c: min(x for x in ch_edges[c] if x >= 0))
                 if chains[ci][1] not in SNAP_EXCLUDE]
    cf = np.array([ch_edges[c][0] for c in chain_ids], dtype=np.int32)
    cb = np.array([ch_edges[c][1] for c in chain_ids], dtype=np.int32)
    npts = np.array([len(chains[c][5]) - 2 for c in chain_ids], dtype=np.uint16)
    geom = []
    for c in chain_ids:
        cp = chains[c][5]
        px, py = round(cp[0][0] * 1e5), round(cp[0][1] * 1e5)
        for x, y in cp[1:-1]:
            qx, qy = round(x * 1e5), round(y * 1e5)
            geom += [qx - px, qy - py]
            px, py = qx, qy
    geom = np.array(geom, dtype=np.int16)

    # Restrictions -> banned (in edge, out edge) pairs.
    by_way_in = defaultdict(list)   # (way, via node) -> edges ending at via
    by_way_out = defaultdict(list)  # (way, via node) -> edges starting at via
    out_of = defaultdict(list)
    for ei, e in enumerate(raw):
        by_way_in[(e[7], e[1])].append(ei)
        by_way_out[(e[7], e[0])].append(ei)
        out_of[e[0]].append(ei)
    banned = set()
    unmatched = 0
    for kind, fw, via, tw in rd.restrictions:
        ins, outs = by_way_in.get((fw, via), []), by_way_out.get((tw, via), [])
        if not ins or not outs:
            unmatched += 1
            continue
        for i in ins:
            if kind.startswith("no_"):
                banned.update((i, o) for o in outs)
            elif kind.startswith("only_"):
                banned.update((i, o) for o in out_of[via] if o not in outs)
    banned = sorted(banned)
    print(f"restriction pairs {len(banned)}, unmatched relations {unmatched}", file=sys.stderr)
    r_from = np.array([p[0] for p in banned], dtype=np.uint32)
    r_to = np.array([p[1] for p in banned], dtype=np.uint32)

    lat = np.array([round(loc[n][1] * 1e6) for n in nodes], dtype=np.int64)
    lon = np.array([round(loc[n][0] * 1e6) for n in nodes], dtype=np.int64)
    dlat = np.diff(lat, prepend=0).astype(np.int32)
    dlon = np.diff(lon, prepend=0).astype(np.int32)
    deg = np.diff(first_out).astype(np.uint32)
    # Distinct neighbours per node: OSRM applies turn penalties only where > 2 roads meet.
    nb = defaultdict(set)
    for e in raw:
        nb[nid[e[0]]].add(nid[e[1]])
        nb[nid[e[1]]].add(nid[e[0]])
    nroads = np.array([min(255, len(nb[i])) for i in range(N)], dtype=np.uint8)

    parts = [dlat, dlon, np.concatenate([[0], deg]).astype(np.uint32), tgt, w]
    flags = 0
    if a.peak_array:
        # Stand-in for future road-level factors: class factor with per-edge spread, so the size
        # measured is what a real non-uniform array would cost, not a trivially compressible copy.
        rng = np.random.default_rng(1)
        f = a.peak_factor * rng.uniform(0.8, 1.2, E)
        parts.append(np.minimum(65535, np.round(w * f)).astype(np.uint16))
        flags |= 1
    parts += [nroads, b_in, b_out, cf, cb, npts, geom, r_from, r_to]
    hdr = struct.pack("<8I", 0x53524731, 1, N, E, len(chain_ids), len(geom) // 2, len(banned), flags)
    with open(a.out, "wb") as fh:
        fh.write(hdr)
        for p in parts:
            buf = p.astype(p.dtype.newbyteorder("<")).tobytes()
            fh.write(buf)
            fh.write(b"\0" * (-len(buf) % 4))  # 4-byte alignment for typed array views
    print(f"N {N} E {E} chains {len(chain_ids)} geomPts {len(geom)//2} restr {len(banned)}", file=sys.stderr)
    for name, p in zip(["dlat", "dlon", "deg", "tgt", "w"] + (["peak"] if a.peak_array else [])
                       + ["nRoads", "bIn", "bOut", "chainFwd", "chainBwd", "nPts", "geom", "rFrom", "rTo"], parts):
        print(f"  {name:9s} {p.nbytes/1e6:6.2f} MB", file=sys.stderr)
    if a.meta:
        json.dump({"bbox": [minx, miny, maxx, maxy]}, open(a.meta, "w"))


if __name__ == "__main__":
    main()
