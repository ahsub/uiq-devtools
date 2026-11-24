# Abschnitt 2: Empfindlichkeit nach Datenqualitätsklasse.
# Eingaben: eval_enriched.csv (analyze.py), day_quality.csv (classify_days.py), gate_portfolio_h7.csv (gate_effect.py)
import pandas as pd, sys
d=pd.read_csv(sys.argv[1]); q=pd.read_csv(sys.argv[2]); g=pd.read_csv(sys.argv[3])
for h in (7,30):
    r=f'r{h}'; x=d[d[r].notna()]
    t=x.groupby('quality').apply(lambda s: pd.Series({'days':s.tday.nunique(),'dayMean':s.groupby('tday')[r].mean().mean(),'dayExRSP':s.groupby('tday')[f'e{h}'].mean().mean()}))
    print(f'h{h}'); print(t.round(2).to_string())
m=g.merge(q[['tday','quality']],on='tday'); print('Gate-Differenz nur grün minus alle (h7) je Qualitätsklasse')
print(m.groupby('quality').g_minus_all.agg(['mean','count']).round(2).to_string())
