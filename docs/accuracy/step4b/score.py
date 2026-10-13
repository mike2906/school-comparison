import json, glob
inh=json.load(open("inherited.json"))
# Hand verdicts: duplicates in step 4 re-judged on their own fields (no other post row matches that fee), and the 4 new rows.
fix={3703:("correct","f4"),3032:("missing_period","f10"),3715:("missing_period","f5"),3731:("correct","f1")}
rows=[]
for sid,xs in inh.items():
    for x in xs:
        v,f=fix.get(x["id"],(x["verdict"],x["fid"])); rows.append((int(sid),x["category"],x["amount"],x["period"],v,f))
new=[(151,"tuition",11860,"yearly","correct","f2"),(151,"registration",200,None,"missing_period","f3"),
     (151,"food",345,"quarter","correct","f5"),(520,"materials",595,None,"correct","f9")]
rows+=new
WRONG={"wrong_period","wrong_category","wrong_amount","wrong_group_plan_year","wrong_currency","stale"}
site=sum(len(json.load(open(p))["site_fees"]) for p in glob.glob("../step4/adj/*.json"))
unres=sum(r[4]=="unresolved" for r in rows); dup=sum(r[4]=="duplicate" for r in rows)
judged=len(rows)-unres-dup; wrong=sum(r[4] in WRONG for r in rows)
found={(r[0],r[5]) for r in rows if r[4] in("correct","missing_period")}
right={(r[0],r[5]) for r in rows if r[4]=="correct"}
print(f"site fees {site}; post rows {len(rows)}; right {sum(r[4]=='correct' for r in rows)}/{judged}; blank {sum(r[4]=='missing_period' for r in rows)}; wrong {wrong}/{judged} ({100*wrong/judged:.0f}%); dup {dup}; unres {unres}; found {len(found)}/{site} ({100*len(found)/site:.0f}%); fully right {len(right)}/{site} ({100*len(right)/site:.0f}%)")
for r in rows:
    if r[4] in WRONG: print("  wrong:",r)
from collections import Counter
print(Counter((r[0]) for r in rows if r[4] in("correct","missing_period")))
