"""Blind packets: today's published rows, the agent's kept fees, and post-gate published rows, shuffled."""
import json, random
from pathlib import Path
HERE = Path(__file__).resolve().parent
R = f"{HERE}/run/"
before, after = json.load(open(R + "before.json")), json.load(open(R + "after.json"))
KEY = f"{HERE}/key/"
rng = random.Random(20261015)
hidden = {}
fields = ("category", "amount", "amount_min", "amount_max", "currency", "period")
def sig(r): return (r["category"], r["amount"], r["amount_min"], r["amount_max"], r["currency"])
for sid in sorted(before, key=int):
    res = json.load(open(f"{R}agent_{sid}.json"))["results"][0]
    cands = []  # (row dict, set of views)
    by_id = {}
    for r in before[sid]:
        if r["published"]:
            c = [dict(category=r["category"], amount=r["amount"], amount_min=r["amount_min"], amount_max=r["amount_max"],
                      currency=r["currency"], period=r["period"], label=r["plan_name"] or r["age_group"],
                      year=r["academic_year"], group=r["age_group"], url=r["source_url"]), {"today"}]
            cands.append(c); by_id[r["id"]] = c
    agent = []
    for f in res.get("fees") or []:
        if f.get("kept"):
            c = [dict(category=f["category"], amount=f["amount"], amount_min=f["amount_min"], amount_max=f["amount_max"],
                      currency=f["currency"], period=f["period"], label=f["label"], year=f["academic_year"],
                      group=None, url=f["source_url"]), {"agent"}]
            cands.append(c); agent.append(c)
    for r in after[sid]:
        if not r["published"]:
            continue
        if r["id"] in by_id and r["extractor"] != "agent":
            by_id[r["id"]][1].add("post"); continue
        m = [c for c in agent if "post" not in c[1] and sig(c[0]) == sig(r) and c[0]["period"] == r["period"]] or \
            [c for c in agent if "post" not in c[1] and sig(c[0]) == sig(r)]
        if m and m[0][0]["period"] == r["period"]:
            m[0][1].add("post")
        else:  # a period Stage 6 filled in, or a row without an agent twin: its own candidate
            cands.append([dict(category=r["category"], amount=r["amount"], amount_min=r["amount_min"], amount_max=r["amount_max"],
                               currency=r["currency"], period=r["period"], label=r["plan_name"] or r["age_group"],
                               year=r["academic_year"], group=r["age_group"], url=r["source_url"]), {"post"}])
    key = json.load(open(f"{KEY}{sid}.json"))
    if not cands and not key["site_fees"]:
        continue
    rng.shuffle(cands)
    out = []
    hidden[sid] = {}
    for i, (row, views) in enumerate(cands, 1):
        out.append({"oid": f"o{i}", **row}); hidden[sid][f"o{i}"] = sorted(views)
    json.dump({"school_id": int(sid), "candidates": out}, open(f"{HERE}/packets/{sid}.json", "w"), ensure_ascii=False, indent=1)
    print(sid, len(key["site_fees"]), "key;", len(out), "cands;", {v: sum(v in s for _, s in cands) for v in ("today", "agent", "post")})
json.dump(hidden, open(f"{HERE}/hidden_map.json", "w"), indent=1)
