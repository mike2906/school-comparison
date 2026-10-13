"""Rescore agent and published fee rows from blind per-school adjudications.

Usage, from the repository root: python3 docs/accuracy/fresh_rescore/rescore.py
Rules: rules.md. Inputs: run/fresh.checked.json (the agent run and today's stored rows),
packets/ (the blind lists the adjudicators saw), hidden_map.json (which row came from which
side), adj/ (one verdict file per school) and ../reference_set_fresh.json (the original key).
"""
import collections
import json
import pathlib

HERE = pathlib.Path(__file__).parent
run = {r["school_id"]: r for r in json.load(open(HERE / "run/fresh.checked.json"))}
hidden = json.load(open(HERE / "hidden_map.json"))
adj = {int(p.stem): json.load(open(p)) for p in (HERE / "adj").glob("*.json")}
ref = {s["school_id"]: s for s in json.load(open(HERE.parent / "reference_set_fresh.json"))["schools"]}

RIGHT = {"correct"}
USABLE = {"correct", "missing_period"}  # amount, category and group right; period left blank
verdicts = {"agent": collections.Counter(), "published": collections.Counter()}
found = {"agent": set(), "published": set()}
found_full = {"agent": set(), "published": set()}
site_fees = set()
schools = {"with_fees": 0, "agent": 0, "published": 0}
group_unstated = collections.Counter()
key_changes = []
rejudged = collections.Counter()
old_fees = 0


def field_verdict(row, fee):
    def num(x):
        return None if x is None else float(x)
    if row["currency"] != fee["currency"]:
        return "wrong_currency"
    if row["category"] != fee["category"]:
        return "wrong_category"
    if (num(row["amount"]), num(row["amount_min"]), num(row["amount_max"])) != (
        num(fee.get("amount")), num(fee.get("amount_min")), num(fee.get("amount_max"))
    ):
        return "wrong_amount"
    if row["period"] == fee.get("period"):
        return "correct"
    return "missing_period" if row["period"] is None else "wrong_period"
missing = [sid for sid in run if sid not in adj]

for sid, a in sorted(adj.items()):
    # A fee for 2025/26 that the site also states for 2026/27 (same category and group) is an
    # old table left on the page, not a current fee to find.
    newer = {(f["category"], f.get("group")) for f in a["site_fees"] if "2026" in str(f.get("year"))}
    old_year = {
        f["fid"]
        for f in a["site_fees"]
        if "2025" in str(f.get("year")) and (f["category"], f.get("group")) in newer
    }
    old_fees += len(old_year)
    fees = {f["fid"] for f in a["site_fees"]} - old_year
    site_fees |= {(sid, fid) for fid in fees}
    hit = {"agent": False, "published": False}
    by_fid = {f["fid"]: f for f in a["site_fees"]}
    packet = {c["oid"]: c for c in json.load(open(HERE / f"packets/{sid}.json"))["candidates"]}
    # Fees each source already matches with a row the adjudicator kept (not a duplicate).
    seen = {
        (hidden[f"{sid}:{c['oid']}"][0], c.get("fid"))
        for c in a["candidates"]
        if c["verdict"] in USABLE
    }
    for c in a["candidates"]:
        src, _ = hidden[f"{sid}:{c['oid']}"]
        if c["verdict"] == "stale" and "2026" in str(by_fid.get(c.get("fid"), {}).get("year")):
            # 2026/27 is the current school year, so its fees are not stale.
            verdict = field_verdict(packet[c["oid"]], by_fid[c["fid"]])
            if verdict in USABLE and (src, c["fid"]) in seen:
                verdict = "duplicate"
            c = c | {"verdict": verdict, "rejudged": True}
        elif c["verdict"] == "duplicate" and (src, c.get("fid")) not in seen and c.get("fid") in by_fid:
            # The adjudicators saw both sources in one list, so a row repeating the other
            # source's match is not a duplicate within its own source: judge it on its fields.
            c = c | {"verdict": field_verdict(packet[c["oid"]], by_fid[c["fid"]]), "rejudged": True}
        if c["verdict"] in USABLE:
            seen.add((src, c.get("fid")))  # a re-judged row keeps a later repeat a duplicate
        rejudged[src] += bool(c.get("rejudged"))
        verdicts[src][c["verdict"]] += 1
        if c.get("group_unstated"):
            group_unstated[src] += 1
        if c["verdict"] in USABLE and c.get("fid") in fees:
            found[src].add((sid, c["fid"]))
            hit[src] = True
        if c["verdict"] in RIGHT and c.get("fid") in fees:
            found_full[src].add((sid, c["fid"]))
    if fees:
        schools["with_fees"] += 1
        schools["agent"] += hit["agent"]
        schools["published"] += hit["published"]
    old = len([p for p in ref[sid]["prices"] if p.get("amount") is not None or p.get("amount_min") is not None])
    if old != len(fees):
        key_changes.append((sid, old, len(fees)))


def rate(n, d):
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else f"{n}/0"


print(f"adjudicated schools: {len(adj)}; missing: {missing}")
print(f"site fees (corrected key): {len(site_fees)}; original key: "
      f"{sum(len(ref[s]['prices']) for s in adj)}")
for src in ("agent", "published"):
    v = verdicts[src]
    total = sum(v.values())
    decided = total - v["unresolved"]
    print(f"\n{src}: {total} rows, verdicts {dict(v)}")
    print(f"  right (strict): {rate(v['correct'], decided)} of decided rows")
    print(f"  right or period blank: {rate(v['correct'] + v['missing_period'], decided)}")
    print(f"  wrong: {rate(decided - v['correct'] - v['missing_period'], decided)}; unresolved {v['unresolved']}")
    print(f"  duplicates re-judged on their fields (other source matched first): {rejudged[src]}")
    print(f"  rows judged without a group label: {group_unstated[src]}")
    print(f"  site fees found (usable): {rate(len(found[src]), len(site_fees))}; "
          f"fully right: {rate(len(found_full[src]), len(site_fees))}")
    print(f"  schools with fees where it has at least one usable fee: {rate(schools[src], schools['with_fees'])}")
print(f"\nold-year site fees left out of the denominator: {old_fees}")
print("key changes (school, original fees, corrected fees):", key_changes)
