# Abschnitt 6: Gate-Wirkung. Eingabe: eval_enriched.csv (Ausgabe von analyze.py), Ausgabeordner.
import pandas as pd, numpy as np, sys
d=pd.read_csv(sys.argv[1]); OUT=sys.argv[2]
x=d[d.gatekey.notna() & d.r7.notna()].copy()
rows=[]
for s,g in x.groupby('strat'):
    for col,gg in g.groupby('gate'):
        day=gg.groupby('tday').r7.mean()
        rows.append((s,col,gg.tday.nunique(),len(gg),round(day.mean(),2),round(day.std(ddof=1)/np.sqrt(len(day)),2) if len(day)>1 else None))
pd.DataFrame(rows,columns=['strat','gate','days','n','dayMean_r7','SE']).to_csv(OUT+'/gate_within_strategy_h7.csv',index=False)
def daycmp(df,lab,r):
    g=df.groupby(['tday','gate'])[r].mean().unstack()
    for c in ['green','amber','red']:
        if c not in g: g[c]=np.nan
    diff=(g.green-g.red).dropna()
    print(f'{lab}: green-red/day mean={diff.mean():.2f} SE={diff.std(ddof=1)/np.sqrt(len(diff)):.2f} days={len(diff)} share_red_better={(diff<0).mean():.2f}')
daycmp(x,'h7 all','r7')
x30=d[d.gatekey.notna() & d.r30.notna()]
daycmp(x30,'h30 all','r30'); daycmp(x30[x30.tday>='2026-09-14'],'h30 signals 14.09.-06.10.','r30')
for h in (7,30):
    r=f'r{h}'; df=d[d.gatekey.notna() & d[r].notna()]
    allm=df.groupby(['tday','strat'])[r].mean().groupby('tday').mean()
    gm=df[df.gate=='green'].groupby(['tday','strat'])[r].mean().groupby('tday').mean()
    ga=df[df.gate.isin(['green','amber'])].groupby(['tday','strat'])[r].mean().groupby('tday').mean()
    t=pd.DataFrame({'all':allm,'green':gm,'green_amber':ga}); t['win']=np.where(t.index>='2026-10-26','from_2610','pre_2610')
    t['g_minus_all']=t.green-t['all']; t['ga_minus_all']=t.green_amber-t['all']
    s=t.groupby('win').agg(all=('all','mean'),green=('green','mean'),green_amber=('green_amber','mean'),diff=('g_minus_all','mean'),
        se=('g_minus_all',lambda v: v.std(ddof=1)/np.sqrt(v.count())),days=('all','count')).round(2)
    print(f'h{h}'); print(s.to_string()); t.round(3).to_csv(f'{OUT}/gate_portfolio_h{h}.csv')
