# Abschnitt 2: Anteil der Ticker, deren Kurs im Snapshot älter ist als der Track-Record-Tag.
# Eingaben: archive_prices.json (Ausgabe von extract_archive.py, nur aus Rohdaten), day_quality.csv, Ausgabedatei
import json,sys,pandas as pd
P=json.load(open(sys.argv[1])); q=pd.read_csv(sys.argv[2]); out=[]
for _,r in q.iterrows():
    f=r.archive_file
    v=[x[1] for x in P.get(f,{}).values() if x[1]] if isinstance(f,str) else []
    out.append((r.tday,r.quality,round(sum(1 for d in v if d<r.tday)/len(v),2) if v else None))
o=pd.DataFrame(out,columns=['tday','quality','stale_share']); o.to_csv(sys.argv[3],index=False)
print('aktuell (<=0,5):',(o.stale_share<=0.5).sum(),'veraltet (>0,5):',(o.stale_share>0.5).sum(),'nicht prüfbar:',o.stale_share.isna().sum())
