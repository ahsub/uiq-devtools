# Abschnitt 1: Abgleich der eigenen Zellen gegen tr:stats.
# Eingaben: tr_backup_latest.json, Ausgabeordner von analyze.py
# Geprüft je Zelle (Strategie|Regime|Horizont): n, nFresh, hit, hitFresh, avg, alpha.
# Fehlende Zellen werden in beide Richtungen gezählt.
import json,sys,math,pandas as pd
K=json.load(open(sys.argv[1]))['keys']; st=K['tr:stats']; st=json.loads(st) if isinstance(st,str) else st
TOL={'n':0,'nFresh':0,'hit':0.006,'hitFresh':0.006,'avg':0.015,'alpha':0.015}
MAP={'n':'n','nFresh':'nFresh','hit':'hit','hitFresh':'hitFresh','avg':'mean','alpha':'alphaSPY'}
tot={'cells_mine':0,'matched':0,'missing_in_stats':0,'missing_in_mine':0,'diffs':0}
for h in (7,30):
    m=pd.read_csv(f'{sys.argv[2]}/h{h}_strat_regime.csv')
    mine=set(); 
    for _,r in m.iterrows():
        key=f'{r.strat}|{r.regime}|h{h}'; mine.add(key); tot['cells_mine']+=1
        c=st['cells'].get(key)
        if c is None: tot['missing_in_stats']+=1; print('fehlt in tr:stats:',key); continue
        tot['matched']+=1
        for k,col in MAP.items():
            a=c.get(k); b=r[col]
            if a is None or (isinstance(b,float) and math.isnan(b)):
                if not (a is None and isinstance(b,float) and math.isnan(b)): tot['diffs']+=1; print('fehlender Wert',key,k,a,b)
                continue
            if abs(float(a)-float(b))>TOL[k]: tot['diffs']+=1; print('Abweichung',key,k,a,round(float(b),4))
    statkeys={k for k in st['cells'] if k.endswith(f'|h{h}')}
    for k in sorted(statkeys-mine): tot['missing_in_mine']+=1; print('fehlt in eigener Auswertung:',k)
print(tot)
