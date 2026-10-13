"""Score today's published rows, the agent's rows (no hold) and post-gate output against the step-4 key."""
import json, sys
from pathlib import Path
S = Path(__file__).resolve().parent
hidden = json.load(open(S / "hidden_map.json"))
adj = {int(p.stem): json.load(open(p)) for p in (S / "adj").glob("*.json")}
UNJUDGED_FEES = {}  # every school with key fees had rows to judge
RIGHT, BLANK = {"correct"}, {"missing_period"}
WRONG = {"wrong_period", "wrong_category", "wrong_amount", "wrong_group_plan_year", "wrong_currency", "stale"}
exclude = set(int(x) for x in sys.argv[1:])  # e.g. 505 for the sensitivity run
views = ("today", "agent", "post")
tot = {v: {"rows": 0, "right": 0, "blank": 0, "wrong": 0, "dup": 0, "unres": 0, "found": set(), "found_right": set()} for v in views}
site_fees = 0
per_school = []
for sid, a in sorted(adj.items()):
    if sid in exclude:
        continue
    fees = [f for f in a["site_fees"]]
    site_fees += len(fees)
    line = {"school": sid, "site_fees": len(fees)}
    period_of = {f["fid"]: f.get("period") for f in fees}
    packet = {c["oid"]: c for c in json.load(open(S / f"packets/{sid}.json"))["candidates"]}
    for c in a["candidates"]:
        vs = hidden[str(sid)][c["oid"]]
        for v in vs:
            verdict = c["verdict"]
            if verdict == "duplicate" and c.get("fid"):
                # Judges saw both extractors in one list; a copy of another view's match is judged
                # on its own fields unless its own view already matches that fee.
                own = [o for o in a["candidates"] if o is not c and o.get("fid") == c["fid"]
                       and v in hidden[str(sid)][o["oid"]] and o["verdict"] != "duplicate"]
                if not own:
                    row_period, site_period = packet[c["oid"]]["period"], period_of.get(c["fid"])
                    verdict = ("correct" if row_period == site_period else
                               "missing_period" if row_period is None else "wrong_period")
            t = tot[v]
            t["rows"] += 1
            if verdict in RIGHT:
                t["right"] += 1; t["found"].add((sid, c["fid"])); t["found_right"].add((sid, c["fid"]))
            elif verdict in BLANK:
                t["blank"] += 1; t["found"].add((sid, c["fid"]))
            elif verdict in WRONG:
                t["wrong"] += 1
            elif verdict == "duplicate":
                t["dup"] += 1
            else:
                t["unres"] += 1
    for v in views:
        line[v] = sum(1 for k in tot[v]["found"] if k[0] == sid)
    per_school.append(line)
for sid, n in UNJUDGED_FEES.items():
    if sid not in exclude:
        site_fees += n
        per_school.append({"school": sid, "site_fees": n, "today": 0, "agent": 0, "post": 0})
pct = lambda a, b: f"{a}/{b} ({100*a/b:.0f}%)" if b else "0/0"
print(f"site fees: {site_fees}" + (f" (excluding {sorted(exclude)})" if exclude else ""))
for v in views:
    t = tot[v]; judged = t["rows"] - t["unres"] - t["dup"]
    print(f"{v:6} rows {t['rows']:3}  right {pct(t['right'], judged)}  blank period {t['blank']}  wrong {pct(t['wrong'], judged)}"
          f"  dup {t['dup']}  unresolved {t['unres']}  found {pct(len(t['found']), site_fees)}  fully right found {pct(len(t['found_right']), site_fees)}")
print("per school (site fees found):")
for l in sorted(per_school, key=lambda l: l["school"]):
    print("  ", l)
