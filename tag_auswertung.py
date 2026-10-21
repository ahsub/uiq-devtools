import csv,sys,collections,statistics
# usage: tag_auswertung.py DAY file1.csv [file2.csv ...]  (DAY = YYYY-MM-DD des Handelstags)
day=sys.argv[1]; rows=[]
for f in sys.argv[2:]: rows+=list(csv.DictReader(open(f,encoding="utf-8")))
print("Handelstag",day,"Zeilen",len(rows),collections.Counter(r["status"] for r in rows))
# Tageszeile: erster Slot mit Close
d=collections.defaultdict(dict)
for r in rows:
    if r["source"]=="yahoo_daily" and r["rec_key"].startswith(day):
        d[r["ticker"]][r["slot_utc"]]=r["close"]
first={};final={};stab={}
for t,v in d.items():
    sl=sorted(v)
    ok=[s for s in sl if v[s]!=""]
    if ok:
        first[t]=ok[0]; final[t]=float(v[ok[-1]])
        stab[t]=len({v[s] for s in ok})==1
slots=sorted({r["slot_utc"] for r in rows if r["source"]=="yahoo_daily" and r["slot_utc"]>day+"T"})
mor=sorted({s for t in d for s in d[t]})
cnt=collections.OrderedDict()
for s in mor:
    n=sum(1 for t in d if d[t].get(s,"")!="")
    cnt[s[11:16]+("(+1)" if s[:10]>day else "")]=f"{n}/{len(d)}"
print("Tageszeile mit Close je Slot (nur Slots mit Daten fuer den Tag):")
print(dict(cnt))
print("Erster Slot mit Close je Ticker:",collections.Counter(first[t][11:16] for t in first))
print("Tageswert stabil nach erstem Close:",sum(stab.values()),"/",len(stab))
# Balken: letzter regulaerer Balken des Tages je Slot
bar=collections.defaultdict(dict)
post=collections.defaultdict(dict)
for r in rows:
    if r["source"]!="yahoo_intraday" or not r["rec_local"].startswith(day): continue
    if "letzter_regulaerer_balken" in r["flags"]: bar[r["ticker"]][r["slot_utc"]]=float(r["close"])
    if r["session_class"].startswith("nach_Schluss"): post[r["ticker"]][r["slot_utc"]]=(r["rec_local"][11:16],float(r["close"]))
print(f"{'Ticker':10s} {'Tag':>10s} | {'1.Balken':>9s} {'Abw%':>6s} | {'fin.Balken':>10s} {'Abw%':>6s} | Korr% (1.->fin) | letzte Aenderung | Post")
korr=[];f1=[];f2=[]
for t in sorted(first):
    if t not in bar: continue
    sl=sorted(bar[t]); v=[bar[t][s] for s in sl]
    a=100*(v[0]-final[t])/final[t]; b=100*(v[-1]-final[t])/final[t]; k=100*(v[-1]-v[0])/v[0]
    chg=[sl[i][11:16] for i in range(1,len(sl)) if v[i]!=v[i-1]]
    p=post.get(t); ps=""
    if p:
        s=sorted(p)[-1]; ps=f"{p[s][0]} {p[s][1]:.4f} Abw {100*(p[s][1]-final[t])/final[t]:+.2f}% (ab {sorted(p)[0][11:16]}Z)"
    print(f"{t:10s} {final[t]:10.4f} | {v[0]:9.4f} {a:+6.2f} | {v[-1]:10.4f} {b:+6.2f} | {k:+6.2f} | {chg[-1] if chg else '-':>5s} | {ps}")
    if t!="ORG.AX": korr.append(k);f1.append(a);f2.append(b)
print("EU(13): Korrektur min/max %.2f/%.2f; erster Balken Abw min/max %.2f/%.2f; finaler Balken Abw min/max %.2f/%.2f"%(min(korr),max(korr),min(f1),max(f1),min(f2),max(f2)))
print("EU: erster Balken < Tagesschluss:",sum(1 for x in f1 if x<0),"/",len(f1),"; finaler exakt gleich:",sum(1 for x in f2 if abs(x)<0.0005))
