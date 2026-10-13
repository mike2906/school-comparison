import json
S="../step4/"  # step 4 packets and adjudications
after=json.load(open("run/after.json"))
def k(r, per=True):
    return (r["category"], r.get("amount"), r.get("amount_min"), r.get("amount_max"), (r.get("currency") or "").upper(), r.get("period") if per else None)
todo={}; res={}
for sid, rows in sorted(after.items(), key=lambda x:int(x[0])):
    pub=[r for r in rows if r["published"]]
    if not pub: continue
    try:
        pk=json.load(open(f"{S}packets/{sid}.json"))["candidates"]; adj=json.load(open(f"{S}adj/{sid}.json"))
    except FileNotFoundError:
        todo[sid]=pub; continue
    v={c["oid"]:c for c in adj["candidates"]}
    for r in pub:
        row=dict(category=r["category"],amount=r["amount"],amount_min=r["amount_min"],amount_max=r["amount_max"],currency=r["currency"],period=r["period"])
        lab=r["plan_name"] or r["age_group"]; yr=r["academic_year"]
        m=[c for c in pk if k(c)==k(row) and (c.get("year") or None)==(yr or None) and (c.get("label") or None)==(lab or None)]
        m=m or [c for c in pk if k(c)==k(row) and (c.get("year") or None)==(yr or None)]
        if m:
            vs=[v[c["oid"]] for c in m]
            # prefer a non-duplicate verdict
            best=next((x for x in vs if x["verdict"]!="duplicate"), vs[0])
            res.setdefault(sid,[]).append({"id":r["id"],**row,"year":yr,"label":lab,"verdict":best["verdict"],"fid":best.get("fid"),"from":best["oid"],"amb":len({x["verdict"] for x in vs})>1})
        else:
            todo.setdefault(sid,[]).append(r)
json.dump(res,open("inherited.json","w"),ensure_ascii=False,indent=1)
json.dump(todo,open("todo.json","w"),ensure_ascii=False,indent=1)
for sid in sorted(set(res)|set(todo),key=int):
    print(sid,"inherited",len(res.get(sid,[])),[ (x["category"],x["amount"],x["period"],x["verdict"]) for x in res.get(sid,[]) if x["verdict"]!="correct" or x["amb"]],"| new",[(r["category"],r["amount"],r["period"],r["academic_year"],r["plan_name"] or r["age_group"]) for r in todo.get(sid,[])])
