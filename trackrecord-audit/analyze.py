import pandas as pd, numpy as np, json, sys
d=pd.read_csv(sys.argv[1]); ms=json.load(open(sys.argv[2])); OUT=sys.argv[3]
cal=sorted(ms['vix'].keys())
def fwd(series,td,h):
    s=ms[series]
    if td not in cal: return np.nan
    i=cal.index(td)
    if i+h>=len(cal): return np.nan
    a=s.get(td); b=s.get(cal[i+h])
    return (b/a-1)*100 if a and b else np.nan
for h in (7,30):
    for ser in ('spy','rsp'):
        m={td:fwd(ser,td,h) for td in d.tday.unique()}
        d[f'{ser}{h}']=d.tday.map(m)*d['dir']
    d[f'e{h}']=d[f'r{h}']-d[f'rsp{h}']   # excess vs equal-weight S&P 500, direction-adjusted
GATE={'long_minervini':'momentum','long_breakout':'breakout','long_swing':'swing','ko_long':'ko','options_csp':'csp_wheel',
 'options_atmna':'atmna','options_weekly':'weekly_income','options_cc':'cc','long_value':'value','long_dividend':'dividend',
 'long_mr':'meanrev','short_fading':'fading_short'}
G={"BULL_QUIET":{"momentum":"green","breakout":"green","swing":"green","ko":"green","csp_wheel":"green","atmna":"green","weekly_income":"green","cc":"green","value":"amber","dividend":"amber","meanrev":"red","fading_short":"red"},
"BULL_FRAGILE":{"momentum":"amber","swing":"amber","csp_wheel":"amber","weekly_income":"amber","cc":"amber","atmna":"amber","value":"amber","dividend":"green","breakout":"red","ko":"red","meanrev":"red","fading_short":"red"},
"STRESS_UNSTABLE":{"fading_short":"green","meanrev":"amber","csp_wheel":"amber","value":"amber","cc":"amber","dividend":"amber","momentum":"red","swing":"red","breakout":"red","ko":"red","atmna":"red","weekly_income":"red"},
"POST_PANIC_REVERSION":{"meanrev":"green","csp_wheel":"green","atmna":"green","weekly_income":"green","cc":"green","value":"green","dividend":"amber","fading_short":"amber","momentum":"red","swing":"red","breakout":"red","ko":"red"}}
d['gatekey']=d.strat.map(GATE)
d['gate']=[G.get(r,{}).get(g) if isinstance(g,str) else None for r,g in zip(d.regime,d.gatekey)]
d['win']=np.where(d.tday>='2026-10-26','stress_from_2610','pre_2610')
d.to_csv(OUT+'/eval_enriched.csv',index=False)

def summ(df,h,by):
    r=f'r{h}'; x=df[df[r].notna()]
    def f(g):
        day=g.groupby('tday')[r].mean()
        dayE=g.groupby('tday')[f'e{h}'].mean()
        return pd.Series({'n':len(g),'nFresh':int(g.fresh.sum()),'days':g.tday.nunique(),
          'hit':(g[r]>0).mean(),'hitFresh':(g[g.fresh][r]>0).mean() if g.fresh.any() else np.nan,
          'mean':g[r].mean(),'alphaSPY':g[f'a{h}'].mean(),'exRSP':g[f'e{h}'].mean(),
          'dayMean':day.mean(),'daySE':day.std(ddof=1)/np.sqrt(len(day)) if len(day)>1 else np.nan,
          'dayExRSP':dayE.mean(),'dayExSE':dayE.std(ddof=1)/np.sqrt(len(dayE)) if len(dayE)>1 else np.nan})
    return x.groupby(by).apply(f)
pd.set_option('display.width',250); pd.set_option('display.max_rows',300)
res={}
res['h7_strat_window']=summ(d,7,['strat','win'])
res['h7_strat_regime']=summ(d,7,['strat','regime'])
res['h30_strat_regime']=summ(d,30,['strat','regime'])
d['h30win']=np.where(d.tday>='2026-09-14','h30_into_stress(09-14..10-06)','h30_before(<09-14)')
res['h30_strat_window']=summ(d,30,['strat','h30win'])
for k,v in res.items():
    v.round(3).to_csv(f'{OUT}/{k}.csv')
print(res['h7_strat_window'].round(2).to_string())
