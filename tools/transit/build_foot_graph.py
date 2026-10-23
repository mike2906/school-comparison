"""Build the compact walking graph for public transport times (docs/TRANSIT_TIMES.md).

Follows OSRM's foot profile (foot.lua v5.27) where it matters for city walks: the highways a
person may walk on, foot/access blacklist, no oneways, walls and fences block. Every way is
two-way at one speed, so the graph is undirected: one edge per chain (a way piece between
junctions), with its shape simplified to ~2 m for snapping.

Output (little-endian; each section padded to 4 bytes so typed arrays can view it):
  header    Uint32[6]  magic, version, N nodes, C chains, G shape points, walking speed (mm/s)
  dLat,dLon Int32[N]   1e-6 deg, delta-coded in node order (Hilbert curve)
  dA        Int32[C]   first end node, delta from the previous chain's (chains sorted by it)
  dB        Int32[C]   other end node minus first end node
  weight    Uint16[C]  deciseconds at walking speed
  nPts      Uint16[C]  shape points between the two end nodes
  geom      Int16[2G]  those points, 1e-5 deg steps from the previous point (start = node A)
"""
import argparse
import math
import os
import struct
import sys
from collections import defaultdict

import numpy as np
import osmium
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "road-graph"))
from build_graph import hav, hilbert  # noqa: E402

MAGIC, VERSION = 0x46475431, 1
# A parent with a young child (OSRM walks at 5 km/h). Checked against the official planner:
# at 4.5 km/h too many "leave at" times were a few minutes late (docs/TRANSIT_TIMES.md).
WALK_KMH = 4.0
WALKABLE = {
    "primary", "primary_link", "secondary", "secondary_link", "tertiary", "tertiary_link",
    "unclassified", "residential", "road", "living_street", "service", "track", "path", "steps",
    "pedestrian", "platform", "footway", "pier", "cycleway", "corridor",
}
ACCESS_BLACKLIST = {"no", "agricultural", "forestry", "private", "delivery"}
ACCESS_TAGS = ("foot", "access")
AVOID = {"impassable", "proposed", "construction"}
BARRIER_BLOCK = {"wall", "fence", "yes"}
SIMPLIFY_M = 2.0


def foot_access(tags):
    for k in ACCESS_TAGS:
        v = tags.get(k)
        if v:
            return v not in ACCESS_BLACKLIST
    return True


class Reader(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.ways = {}  # way id -> ([node ids], [(lon, lat)])
        self.blocked = set()

    def node(self, n):
        t = n.tags
        if t.get("barrier") in BARRIER_BLOCK and t.get("foot") not in ("yes", "designated") \
                and t.get("access") != "yes":
            self.blocked.add(n.id)

    def way(self, w):
        t = w.tags
        hw = t.get("highway")
        walkable = hw in WALKABLE or t.get("railway") == "platform" or t.get("public_transport") == "platform"
        if hw == "cycleway" and t.get("foot") not in ("yes", "designated", "permissive"):
            walkable = False  # OSRM walks a cycleway only when foot is allowed
        if not walkable or hw in AVOID or not foot_access(t):
            return
        try:
            pts = [(nd.lon, nd.lat) for nd in w.nodes]
        except osmium.InvalidLocationError:
            return
        self.ways[w.id] = ([nd.ref for nd in w.nodes], pts)


def simplify(pts):
    """Douglas-Peucker in metres (local flat projection); keeps both ends."""
    if len(pts) <= 2:
        return pts
    kx = math.cos(math.radians(pts[0][1])) * 111320
    ky = 110540
    xy = [(p[0] * kx, p[1] * ky) for p in pts]
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        (ax, ay), (bx, by) = xy[i], xy[j]
        dx, dy = bx - ax, by - ay
        l2 = dx * dx + dy * dy
        best, bk = -1.0, -1
        for k in range(i + 1, j):
            px, py = xy[k]
            t = 0 if l2 == 0 else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / l2))
            d = math.hypot(ax + t * dx - px, ay + t * dy - py)
            if d > best:
                best, bk = d, k
        if best > SIMPLIFY_M:
            keep[bk] = True
            stack += [(i, bk), (bk, j)]
    return [p for p, k in zip(pts, keep) if k]


def build_chains(rd):
    use = defaultdict(int)
    for refs, _ in rd.ways.values():
        for i, r in enumerate(refs):
            use[r] += 2 if i in (0, len(refs) - 1) else 1
    split = {n for n, c in use.items() if c > 1} | rd.blocked
    chains = []  # ([node keys], [pts])
    for refs, pts in rd.ways.values():
        start = 0
        for i in range(1, len(refs)):
            if refs[i] not in split and i != len(refs) - 1:
                continue
            cr, cp = list(refs[start:i + 1]), pts[start:i + 1]
            start = i
            if len(cr) < 2 or cr[0] == cr[-1]:
                continue
            # A wall or fence end becomes a node of its own: the chain is a dead end there.
            ci = len(chains)
            if cr[0] in rd.blocked:
                cr[0] = ("barrier", cr[0], ci)
            if cr[-1] in rd.blocked:
                cr[-1] = ("barrier", cr[-1], ci)
            chains.append((cr[0], cr[-1], cp))
    return chains


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pbf")
    ap.add_argument("out")
    args = ap.parse_args()

    rd = Reader()
    rd.apply_file(args.pbf, locations=True)
    chains = build_chains(rd)

    loc = {}
    for u, v, cp in chains:
        loc[u], loc[v] = cp[0], cp[-1]
    ids = {n: i for i, n in enumerate(loc)}
    m = csr_matrix((np.ones(len(chains)), ([ids[u] for u, _, _ in chains], [ids[v] for _, v, _ in chains])),
                   shape=(len(ids), len(ids)))
    _, lab = connected_components(m, directed=False)
    big = np.bincount(lab).argmax()
    nodes = [n for n in loc if lab[ids[n]] == big]
    minx, maxx = min(loc[n][0] for n in nodes), max(loc[n][0] for n in nodes)
    miny, maxy = min(loc[n][1] for n in nodes), max(loc[n][1] for n in nodes)
    nodes.sort(key=lambda n: hilbert(int((loc[n][0] - minx) / (maxx - minx) * 65535),
                                     int((loc[n][1] - miny) / (maxy - miny) * 65535)))
    nid = {n: i for i, n in enumerate(nodes)}

    speed = WALK_KMH / 3.6
    out = []  # (a, b, ds, simplified pts from a to b)
    for u, v, cp in chains:
        if u not in nid:
            continue
        length = sum(hav(cp[k], cp[k + 1]) for k in range(len(cp) - 1))
        ds = min(65535, max(1, round(length / speed * 10)))
        a, b, pts = nid[u], nid[v], cp
        if b < a:
            a, b, pts = b, a, cp[::-1]
        out.append((a, b, ds, simplify(pts)))
    out.sort(key=lambda c: (c[0], c[1]))
    N, C = len(nodes), len(out)

    dA = np.diff(np.array([c[0] for c in out], dtype=np.int64), prepend=0).astype(np.int32)
    dB = np.array([c[1] - c[0] for c in out], dtype=np.int32)
    w = np.array([c[2] for c in out], dtype=np.uint16)
    npts = np.array([len(c[3]) - 2 for c in out], dtype=np.uint16)
    geom = []
    for c in out:
        cp = c[3]
        px, py = round(cp[0][0] * 1e5), round(cp[0][1] * 1e5)
        for x, y in cp[1:-1]:
            qx, qy = round(x * 1e5), round(y * 1e5)
            geom += [qx - px, qy - py]
            px, py = qx, qy
    geom = np.array(geom, dtype=np.int16)
    lat = np.array([round(loc[n][1] * 1e6) for n in nodes], dtype=np.int64)
    lon = np.array([round(loc[n][0] * 1e6) for n in nodes], dtype=np.int64)
    parts = [np.diff(lat, prepend=0).astype(np.int32), np.diff(lon, prepend=0).astype(np.int32),
             dA, dB, w, npts, geom]
    with open(args.out, "wb") as fh:
        fh.write(struct.pack("<6I", MAGIC, VERSION, N, C, len(geom) // 2, round(speed * 1000)))
        for p in parts:
            buf = p.astype(p.dtype.newbyteorder("<")).tobytes()
            fh.write(buf + b"\0" * (-len(buf) % 4))
    print(f"nodes {N} chains {C} shape points {len(geom) // 2} walls/fences {len(rd.blocked)}", file=sys.stderr)


if __name__ == "__main__":
    main()
