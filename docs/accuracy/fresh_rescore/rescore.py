"""Rescore agent and published fee rows from blind per-school adjudications.

Usage, from the repository root: python3 docs/accuracy/fresh_rescore/rescore.py
Rules: rules.md. Inputs: run/fresh.checked.json (the agent run and today's stored rows),
packets/ (the blind lists the adjudicators saw), hidden_map.json (which row came from which
side), adj/ (one verdict file per school) and ../reference_set_fresh.json (the original key).
"""
import collections
import json
import pathlib
import re

HERE = pathlib.Path(__file__).parent
run = {r["school_id"]: r for r in json.load(open(HERE / "run/fresh.checked.json"))}
hidden = json.load(open(HERE / "hidden_map.json"))
adj = {int(p.stem): json.load(open(p)) for p in (HERE / "adj").glob("*.json")}
ref = {s["school_id"]: s for s in json.load(open(HERE.parent / "reference_set_fresh.json"))["schools"]}

corrections = json.load(open(HERE / "corrections.json"))
CURRENT_YEAR = 2026  # the 2026/27 school year
PERIOD_WORDS = {"semester": ("семест", "semester")}

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
old_fees = other_school = 0
wrong_rows = []
missed = collections.Counter()


def start_year(fee):
    """First year of an academic year such as 2025/2026, 2026-2027 or 2026/27."""
    m = re.search(r"(20\d\d)\s*[/-]\s*(?:20)?\d\d", str(fee.get("year") or ""))
    return int(m.group(1)) if m else None


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
    not_here = set(corrections["not_this_institution"].get(str(sid), []))
    for f in a["site_fees"]:
        # rules.md: a period the site does not state stays blank. The adjudicators read
        # "2 x" as semester; only keep that when the page says so.
        words = PERIOD_WORDS.get(f.get("period"))
        if words and not any(w in (f.get("quote") or "").lower() for w in words):
            f["period"] = None
            f["period_cleared"] = True
    # The target is the current school year's schedule or a later one. An older year's fee
    # is left out only when the site also states that category for 2026/27 or later (group
    # names change between tables, so this matches on category).
    current = {f["category"] for f in a["site_fees"] if (start_year(f) or 0) >= CURRENT_YEAR}
    old_year = {
        f["fid"]
        for f in a["site_fees"]
        if (start_year(f) or CURRENT_YEAR) < CURRENT_YEAR and f["category"] in current
    }
    old_fees += len(old_year)
    other_school += len(not_here)
    fees = {f["fid"] for f in a["site_fees"]} - old_year - not_here
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
        if c["verdict"] in USABLE | {"wrong_period"} and by_fid.get(c.get("fid"), {}).get("period_cleared"):
            c = c | {"verdict": field_verdict(packet[c["oid"]], by_fid[c["fid"]]), "rejudged": True}
        if c["verdict"] == "stale" and (start_year(by_fid.get(c.get("fid"), {})) or 0) >= CURRENT_YEAR:
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
        if c["verdict"] not in USABLE | {"unresolved"}:
            row = packet[c["oid"]]
            wrong_rows.append((src, sid, row["category"], row["amount"] or (row["amount_min"], row["amount_max"]), row["period"], c["verdict"]))
        verdicts[src][c["verdict"]] += 1
        if c.get("group_unstated"):
            group_unstated[src] += 1
        if c["verdict"] in USABLE and c.get("fid") in fees:
            found[src].add((sid, c["fid"]))
            hit[src] = True
        if c["verdict"] in RIGHT and c.get("fid") in fees:
            found_full[src].add((sid, c["fid"]))
    missed[sid] = len(fees - {fid for (s2, fid) in found["agent"] if s2 == sid})
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
print(f"fees of a sibling school left out (corrections.json): {other_school}")
print("rows not right (source, school, category, amount, period, verdict):")
for w in sorted(wrong_rows, key=str):
    print("  ", w)
print("agent misses by school:", sorted(((n, s) for s, n in missed.items() if n), reverse=True))
print("key changes (school, original fees, corrected fees):", key_changes)
