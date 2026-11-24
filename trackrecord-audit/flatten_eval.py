import json,csv,sys
K=json.load(open(sys.argv[1]))['keys']; q={r['tday']:r for r in csv.DictReader(open(sys.argv[2]))}
rows=[]
snapreg={}
for k in K:
    if k.startswith('tr:snap'):
        s=K[k]; s=json.loads(s) if isinstance(s,str) else s; snapreg[s['tday']]=s.get('regime')
for k in sorted(x for x in K if x.startswith('tr:eval')):
    e=K[k]; e=json.loads(e) if isinstance(e,str) else e
    td=e['tday']
    for key,r in e['recs'].items():
        t=r.get('trade') or {}
        rows.append({'tday':td,'regime':e.get('regime'),'snap_regime':snapreg.get(td),'quality':q.get(td,{}).get('quality',''),
          'sym':r.get('sym'),'strat':r.get('strat'),'src':r.get('src'),'dir':r.get('dir'),'fresh':r.get('fresh'),
          'r7':r.get('r7'),'a7':r.get('a7'),'r30':r.get('r30'),'a30':r.get('a30'),'r90':r.get('r90'),
          'status':r.get('status'),'trade_st':t.get('st'),'trade_r':t.get('r')})
with open(sys.argv[3],'w',newline='') as fh:
    w=csv.DictWriter(fh,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
print(len(rows))
