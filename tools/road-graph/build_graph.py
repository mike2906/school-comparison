"""Build the compact car road graph the browser routes on (docs/DRIVE_TIMES.md).

Follows OSRM's car profile (car.lua v5.27) where it matters for city trips: highway speeds,
maxspeed x 0.8, access blacklist, oneways and roundabouts, 2 s per traffic signal, turn
penalties (computed in the browser from stored bearings), u-turns only at dead ends, no_/only_
turn restrictions via a node or via a way. A blocking barrier (bollard, block...) only stops
through-traffic: each road ending at it becomes a dead end.

Output (little-endian; each section padded to 4 bytes so typed arrays can view it):
  header      Uint32[8]  magic, version, N nodes, E edges, C chains, G shape points,
                         R banned turns, O entry-limited edges
  dLat, dLon  Int32[N]   1e-6 deg, delta-coded in node order (Hilbert curve)
  outDegree   Uint32[N+1] first value 0; prefix sums give each node's first edge
  dTarget     Int32[E]   target - source node index
  weight      Uint16[E]  deciseconds, free flow
  nRoads      Uint8[N]   distinct neighbouring nodes (turn penalty applies where > 2)
  bearingIn   Uint8[E]   bearing at the edge start, 256 steps
  bearingOut  Uint8[E]   bearing at the edge end
  chainFwd    Int32[C]   chains are undirected way pieces the user can snap to;
  chainBwd    Int32[C]   their edge in each direction, or -1
  chainNPts   Uint16[C]  shape points between the two end nodes
  geom        Int16[2G]  those points, 1e-5 deg steps from the previous point (start = node)
  bannedFrom  Uint32[R]  banned (incoming edge, outgoing edge) pairs
  bannedTo    Uint32[R]
  limited     Uint32[O]  edges that may only be entered from one edge: copies of a via way's
  limitedFrom Uint32[O]  edges, so a via-way restriction applies only to traffic from its start
"""
import argparse
import math
import struct
import sys
from collections import defaultdict

import numpy as np
import osmium
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components

MAGIC, VERSION = 0x53524731, 2
SPEEDS = {  # km/h
    "motorway": 90, "motorway_link": 45, "trunk": 85, "trunk_link": 40,
    "primary": 65, "primary_link": 30, "secondary": 55, "secondary_link": 25,
    "tertiary": 40, "tertiary_link": 20, "unclassified": 25, "residential": 25,
    "living_street": 10, "service": 15,
}
ACCESS_BLACKLIST = {"no", "private", "agricultural", "forestry", "emergency", "psv", "customers"}
ACCESS_TAGS = ("motorcar", "motor_vehicle", "vehicle", "access")
BARRIER_OK = {"cattle_grid", "border_control", "toll_booth", "sally_port", "gate", "lift_gate",
              "no", "entrance", "height_restrictor", "arch"}
SLOW_SERVICE = {"parking_aisle", "driveway", "alley", "parking", "drive-through"}
# Never snap a home or a school onto these.
SNAP_EXCLUDE = {"motorway", "motorway_link", "trunk_link"}
SIGNAL_DS = 20
# OSRM reads these keys in order; a zone value such as "BG:urban" maps through ZONE_SPEEDS.
MAXSPEED_KEYS = ("maxspeed:advisory", "maxspeed", "source:maxspeed", "maxspeed:type")
ZONE_SPEEDS = {"urban": 50, "rural": 90, "trunk": 110, "motorway": 130}


def parse_maxspeed(v):
    if not v:
        return None
    v = v.strip().lower()
    if ":" in v:
        return ZONE_SPEEDS.get(v.split(":", 1)[1])
    try:
        return float(v[:-3]) * 1.609 if v.endswith("mph") else float(v.split()[0])
    except ValueError:
        return None


def maxspeed(tags, direction):
    """Speed limit for one direction ("forward"/"backward"), as OSRM's maxspeed handler."""
    for k in MAXSPEED_KEYS:
        v = parse_maxspeed(tags.get(f"{k}:{direction}") or tags.get(k))
        if v and v > 0:
            return v
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
        self.ways = {}  # way id -> (hw, oneway, (fwd km/h, bwd km/h), [node ids], [(lon, lat)])
        self.signals = set()
        self.blocked = set()
        self.restrictions = []  # (kind, from way, ("n"|"w", via id), to way)

    def node(self, n):
        t = n.tags
        if t.get("highway") == "traffic_signals":
            self.signals.add(n.id)
        b = t.get("barrier")
        if b and b not in BARRIER_OK and t.get("motorcar") not in ("yes", "designated") \
                and t.get("access") != "yes":
            self.blocked.add(n.id)

    def way(self, w):
        t = w.tags
        hw = t.get("highway")
        if hw not in SPEEDS or t.get("area") == "yes" or not car_access(t) \
                or t.get("service") == "emergency_access":
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
        speeds = []
        for direction in ("forward", "backward"):
            ms = maxspeed(t, direction)
            speed = ms * 0.8 if ms else SPEEDS[hw]
            if hw == "service" and t.get("service") in SLOW_SERVICE:
                speed *= 0.5
            speeds.append(speed)
        try:
            pts = [(nd.lon, nd.lat) for nd in w.nodes]
        except osmium.InvalidLocationError:
            return
        self.ways[w.id] = (hw, oneway, tuple(speeds), [nd.ref for nd in w.nodes], pts)

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
        if len(frm) == 1 and len(to) == 1 and len(via) == 1 and via[0][0] in "nw":
            self.restrictions.append((kind, frm[0], via[0], to[0]))


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


class Edge:
    __slots__ = ("src", "dst", "ds", "b_in", "b_out", "chain", "fwd", "way", "copy_of", "limited_from", "idx")

    def __init__(self, src, dst, ds, b_in, b_out, chain, fwd, way, copy_of=None):
        self.src, self.dst, self.ds, self.b_in, self.b_out = src, dst, ds, b_in, b_out
        self.chain, self.fwd, self.way, self.copy_of = chain, fwd, way, copy_of
        self.limited_from = None


def build_chains(rd):
    """Cut ways into chains at junctions, restriction via nodes and blocking barriers."""
    use = defaultdict(int)
    for _, _, _, refs, _ in rd.ways.values():
        for i, r in enumerate(refs):
            use[r] += 2 if i in (0, len(refs) - 1) else 1
    split = {n for n, c in use.items() if c > 1} | rd.blocked
    split |= {v[1] for _, _, v, _ in rd.restrictions if v[0] == "n"}
    chains = []  # (way, hw, oneway, speed, [node keys], [pts])
    for wid, (hw, oneway, speed, refs, pts) in rd.ways.items():
        start = 0
        for i in range(1, len(refs)):
            if refs[i] not in split and i != len(refs) - 1:
                continue
            cr, cp = list(refs[start:i + 1]), pts[start:i + 1]
            start = i
            if len(cr) < 2 or (cr[0] == cr[-1] and len(cr) == 2):
                continue
            # A barrier end becomes a node of its own, so the chain is a dead end there.
            ci = len(chains)
            if cr[0] in rd.blocked:
                cr[0] = ("barrier", cr[0], ci)
            if cr[-1] in rd.blocked:
                cr[-1] = ("barrier", cr[-1], ci)
            chains.append((wid, hw, oneway, speed, cr, cp))
    return chains


def build_edges(rd, chains):
    edges = []
    for ci, (wid, hw, oneway, speed, cr, cp) in enumerate(chains):
        length = sum(hav(cp[k], cp[k + 1]) for k in range(len(cp) - 1))
        sig = sum(1 for r in cr[1:-1] if r in rd.signals)
        for fwd in ((True,) if oneway == 1 else (False,) if oneway == -1 else (True, False)):
            p, r = (cp, cr) if fwd else (cp[::-1], cr[::-1])
            # A signal at a junction is passed once, on the way in.
            extra = SIGNAL_DS if r[-1] in rd.signals else 0
            ds = length / (speed[0 if fwd else 1] / 3.6) * 10 + sig * SIGNAL_DS
            edges.append(Edge(r[0], r[-1], ds + extra, bearing(p[0], p[1]), bearing(p[-2], p[-1]), ci, fwd, wid))
    return edges


def apply_restrictions(rd, edges):
    """Return banned (edge, edge) pairs; add entry-limited copies for via-way restrictions."""
    into, out_of, way_from, into_way = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
    for e in edges:
        into[(e.way, e.dst)].append(e)
        into_way[e.way].append(e)
        way_from[(e.way, e.src)].append(e)
        out_of[e.src].append(e)
    banned, node_bans = set(), set()
    stats = defaultdict(int)
    copies = {}  # (in edge, via path) -> list of copy edges

    def via_path(via_way, start):
        """Edges along via_way from one of its ends, or None."""
        refs = rd.ways[via_way][3] if via_way in rd.ways else None
        if not refs or start not in (refs[0], refs[-1]):
            return None
        end, path, cur = (refs[-1] if start == refs[0] else refs[0]), [], start
        while cur != end and len(path) < 20:
            nxt = [e for e in way_from.get((via_way, cur), []) if not path or e.dst != path[-1].src]
            if not nxt:
                return None
            path.append(nxt[0])
            cur = nxt[0].dst
        return path if cur == end else None

    for kind, fw, (vt, vid), tw in rd.restrictions:
        only = kind.startswith("only_")
        if not (only or kind.startswith("no_")):
            continue
        if vt == "n":
            ins, outs = into.get((fw, vid), []), [e for e in out_of.get(vid, []) if e.way == tw]
            if not ins or not outs:
                stats["unmatched"] += 1
                continue
            for i in ins:
                bad = [o for o in out_of[vid] if o not in outs] if only else outs
                node_bans.update((i, o) for o in bad)
            stats["via_node"] += 1
            continue
        # Via way: find the from edge ending at one end of the via way.
        done = False
        for i in into_way.get(fw, []):
            path = via_path(vid, i.dst)
            if not path:
                continue
            end = path[-1].dst
            outs = [e for e in out_of.get(end, []) if e.way == tw]
            if not outs:
                continue
            key = (id(i), tuple(id(p) for p in path))
            if key not in copies:
                prev, cps = i, []
                for p in path:
                    c = Edge(p.src, p.dst, p.ds, p.b_in, p.b_out, -1, p.fwd, p.way, copy_of=p)
                    c.limited_from = prev
                    cps.append(c)
                    banned.add((prev, p))  # stay on the copies, so the last turn can be checked
                    prev = c
                edges.extend(cps)
                copies[key] = cps
            cps = copies[key]
            if only:  # the whole via way must be followed: onto it, and along it past its junctions
                banned.update((i, o) for o in out_of[i.dst] if o is not path[0])
                for c in cps[:-1]:
                    banned.update((c, o) for o in out_of[c.dst])
            bad = [o for o in out_of[end] if o not in outs] if only else outs
            banned.update((cps[-1], o) for o in bad)
            done = True
        stats["via_way" if done else "unmatched"] += 1
    # A copy of a via-way edge keeps the via-node restrictions of the edge it copies.
    by_from = defaultdict(list)
    for f, o in node_bans:
        by_from[id(f)].append(o)
    for cps in copies.values():
        for c in cps:
            banned.update((c, o) for o in by_from.get(id(c.copy_of), []))
    return banned | node_bans, dict(stats)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pbf")
    ap.add_argument("out")
    a = ap.parse_args()

    rd = Reader()
    rd.apply_file(a.pbf, locations=True)
    chains = build_chains(rd)
    edges = build_edges(rd, chains)
    banned, rstats = apply_restrictions(rd, edges)

    loc = {}
    for _, _, _, _, cr, cp in chains:
        loc[cr[0]], loc[cr[-1]] = cp[0], cp[-1]

    # Keep the largest strongly connected part (drops islands and unreachable stubs).
    ids = {n: i for i, n in enumerate(loc)}
    m = csr_matrix((np.ones(len(edges)), ([ids[e.src] for e in edges], [ids[e.dst] for e in edges])),
                   shape=(len(ids), len(ids)))
    _, lab = connected_components(m, directed=True, connection="strong")
    big = np.bincount(lab).argmax()
    nodes = [n for n in loc if lab[ids[n]] == big]
    minx, maxx = min(loc[n][0] for n in nodes), max(loc[n][0] for n in nodes)
    miny, maxy = min(loc[n][1] for n in nodes), max(loc[n][1] for n in nodes)
    nodes.sort(key=lambda n: hilbert(int((loc[n][0] - minx) / (maxx - minx) * 65535),
                                     int((loc[n][1] - miny) / (maxy - miny) * 65535)))
    nid = {n: i for i, n in enumerate(nodes)}
    edges = [e for e in edges if e.src in nid and e.dst in nid]
    edges.sort(key=lambda e: (nid[e.src], nid[e.dst], e.copy_of is not None))
    for i, e in enumerate(edges):
        e.idx = i
    kept = {id(e) for e in edges}
    N, E = len(nodes), len(edges)

    deg = np.zeros(N + 1, dtype=np.uint32)
    for e in edges:
        deg[nid[e.src] + 1] += 1
    tgt = np.array([nid[e.dst] - nid[e.src] for e in edges], dtype=np.int32)
    w = np.array([min(65535, max(1, round(e.ds))) for e in edges], dtype=np.uint16)
    b_in = np.array([round(e.b_in / 360 * 256) % 256 for e in edges], dtype=np.uint8)
    b_out = np.array([round(e.b_out / 360 * 256) % 256 for e in edges], dtype=np.uint8)
    nb = defaultdict(set)
    for e in edges:
        nb[nid[e.src]].add(nid[e.dst])
        nb[nid[e.dst]].add(nid[e.src])
    nroads = np.array([min(255, len(nb[i])) for i in range(N)], dtype=np.uint8)

    ch_edges = defaultdict(lambda: [-1, -1])
    for e in edges:
        if e.copy_of is None:
            ch_edges[e.chain][0 if e.fwd else 1] = e.idx
    chain_ids = sorted((c for c in ch_edges if chains[c][1] not in SNAP_EXCLUDE),
                       key=lambda c: max(ch_edges[c]))
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

    pairs = sorted((f.idx, t.idx) for f, t in banned if id(f) in kept and id(t) in kept)
    limited = sorted((e.idx, e.limited_from.idx) for e in edges
                     if e.limited_from is not None and id(e.limited_from) in kept)

    lat = np.array([round(loc[n][1] * 1e6) for n in nodes], dtype=np.int64)
    lon = np.array([round(loc[n][0] * 1e6) for n in nodes], dtype=np.int64)
    parts = [
        np.diff(lat, prepend=0).astype(np.int32), np.diff(lon, prepend=0).astype(np.int32), deg, tgt, w,
        nroads, b_in, b_out, cf, cb, npts, geom,
        np.array([p[0] for p in pairs], dtype=np.uint32), np.array([p[1] for p in pairs], dtype=np.uint32),
        np.array([p[0] for p in limited], dtype=np.uint32), np.array([p[1] for p in limited], dtype=np.uint32),
    ]
    with open(a.out, "wb") as fh:
        fh.write(struct.pack("<8I", MAGIC, VERSION, N, E, len(chain_ids), len(geom) // 2, len(pairs), len(limited)))
        for p in parts:
            buf = p.astype(p.dtype.newbyteorder("<")).tobytes()
            fh.write(buf + b"\0" * (-len(buf) % 4))
    print(f"nodes {N} edges {E} chains {len(chain_ids)} shape points {len(geom) // 2} "
          f"banned turns {len(pairs)} limited edges {len(limited)} restrictions {rstats} "
          f"barriers {len(rd.blocked)} signals {len(rd.signals)}", file=sys.stderr)


if __name__ == "__main__":
    main()
