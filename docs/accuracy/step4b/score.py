"""Score each step 4b run's published rows against the step 4 key.

Rows that match a step 4 candidate take its verdict (inherit.py). New rows were judged by hand
against the same key (HAND below). A row step 4 called a duplicate, or one whose fee another row
of this run already took, is re-judged on its own fields: it moves to another site fee with the
same category, amount and currency if one is free (two grade tiers at one price), else it is a
duplicate only if another row of this run matches that fee.
"""
import json, glob, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
WRONG = {"wrong_period", "wrong_category", "wrong_amount", "wrong_group_plan_year", "wrong_currency", "stale"}
site = {int(p.split("/")[-1][:-5]): json.load(open(p))["site_fees"] for p in glob.glob(str(HERE.parent / "step4/adj/*.json"))}
HAND = {  # (school, category, amount, label) -> (verdict, fid)
    (151, "tuition", 11860.0, "5 - 7 grade"): ("correct", "f2"),
    (151, "registration", 200.0, None): ("missing_period", "f3"),
    (151, "food", 345.0, None): ("correct", "f5"),
    (520, "materials", 595.0, None): ("correct", "f9"),
    (170, "tuition", 4200.0, "На два пъти"): ("correct", "f11"),
    (170, "transport", 2000.0, "Еднократно плащане"): ("missing_period", "f13"),
    (170, "transport", 2500.0, "На два пъти"): ("correct", "f14"),
    (568, "tuition", 9300.0, "Разсрочено плащане"): ("correct", "f2"),
    # step 4d (#290's second read)
    (568, "tuition", 9300.0, "разсрочено плащане"): ("correct", "f2"),
    (170, "tuition", 5000.0, "На два пъти"): ("correct", "f5"),
    (170, "tuition", 5400.0, "На осем вноски"): ("correct", "f6"),
    (170, "tuition", 5300.0, "На два пъти"): ("correct", "f8"),
    (170, "tuition", 5300.0, "На осем вноски"): ("correct", "f3"),
    (170, "tuition", 5700.0, "На осем вноски"): ("correct", "f9"),
    (170, "tuition", 4600.0, "На два пъти"): ("correct", "f2"),
    (170, "tuition", 5100.0, "Еднократно плащане"): ("missing_period", "f7"),
    (170, "tuition", 4700.0, "Еднократно плащане"): ("missing_period", "f4"),
    (520, "materials", 595.0, "1, 2, 3, 4, 5, 6 и 7 клас"): ("correct", "f9"),
    (520, "tuition", 7300.0, "За 1 вноска"): ("missing_period", "f1"),
    (520, "tuition", 3850.0, "На 2 вноски"): ("correct", "f2"),
    (520, "tuition", 1950.0, "На 4 вноски"): ("correct", "f3"),
}
def own(sid, r, fid):
    f = next(f for f in site[sid] if f["fid"] == fid)
    return "correct" if r["period"] == f["period"] else "missing_period" if r["period"] is None else "wrong_period"
def score(run):
    inh, todo = json.load(open(HERE / run / "inherited.json")), json.load(open(HERE / run / "todo.json"))
    rows = [(int(s), x, x["verdict"], x["fid"]) for s, xs in inh.items() for x in xs]
    # a new row nobody judged yet counts as unresolved rather than stopping the script
    rows += [(int(s), r, *HAND.get((int(s), r["category"], r["amount"], r["plan_name"] or r["age_group"]), ("unresolved", None)))
             for s, rs in todo.items() for r in rs]
    out, taken = [], set()
    for sid, r, v, fid in sorted(rows, key=lambda t: t[2] == "duplicate"):  # duplicates last
        if fid and (sid, fid) in taken and v != "duplicate":
            v = "duplicate"
        if v == "duplicate" and fid:
            f0 = next(f for f in site[sid] if f["fid"] == fid)
            free = [f["fid"] for f in site[sid] if (sid, f["fid"]) not in taken and f["category"] == f0["category"]
                    and f["amount"] == f0["amount"] and f["currency"] == f0["currency"]]
            if free:
                fid = free[0]; v = own(sid, r, fid)
        if v in ("correct", "missing_period"):
            taken.add((sid, fid))
        out.append((sid, r["category"], r["amount"], r["period"], v, fid))
    unres = sum(o[4] == "unresolved" for o in out); dup = sum(o[4] == "duplicate" for o in out)
    judged = len(out) - unres - dup; wrong = sum(o[4] in WRONG for o in out)
    found = {(o[0], o[5]) for o in out if o[4] in ("correct", "missing_period")}
    total = sum(len(v) for v in site.values())
    f170 = sum(1 for k in found if k[0] == 170); n170 = len(site[170])
    print(f"{run:5} rows {len(out)}  wrong {wrong}/{judged} ({100*wrong/judged:.0f}%)  dup {dup}  unresolved {unres}"
          f"  found {len(found)}/{total} ({100*len(found)/total:.0f}%)"
          f"  without 170: {len(found)-f170}/{total-n170} ({100*(len(found)-f170)/(total-n170):.0f}%)")
for run in sys.argv[1:] or ["run", "run2", "run3"]:
    score(run)
