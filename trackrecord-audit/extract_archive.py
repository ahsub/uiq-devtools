import json,gzip,os,sys,csv
src=sys.argv[1]; out=sys.argv[2]
rows=[]; prices={}
for f in sorted(os.listdir(src)):
    try: s=json.load(gzip.open(os.path.join(src,f)))
    except Exception as e: rows.append({'file':f,'err':str(e)[:80]}); continue
    m=s.get('meta',{}); mk=s.get('market',{}) or {}
    vt=mk.get('vixTerm') or {}; snap=((mk.get('snapshot') or {}).get('data') or {})
    di=m.get('data_integrity') or {}
    lri=(di.get('lastRowIncomplete') or {}); la=(di.get('labelAheadOfTickerData') or {})
    tr=s.get('trackRecord') or {}
    etf={x.get('sym'):x for x in ((s.get('markets') or {}).get('etfs') or []) if isinstance(x,dict)}
    r={'file':f,'generated':m.get('generated'),'ltd':m.get('last_trading_day'),'version':m.get('version'),
       'regime':m.get('regimeUsed'),'ratio':(m.get('regimeMeta') or {}).get('serverRatio'),
       'vix':vt.get('vix'),'vix3m':vt.get('vix3m'),
       'spy':(snap.get('spy') or {}).get('price'),'qqq':(snap.get('qqq') or {}).get('price'),'iwm':(snap.get('iwm') or {}).get('price'),
       'rsp':(etf.get('RSP') or {}).get('price'),'spy_etf':(etf.get('SPY') or {}).get('price'),
       'spy_asof':(etf.get('SPY') or {}).get('_lastCloseDate') or (etf.get('SPY') or {}).get('_dataAsOf'),
       'lri_us':(lri.get('us') or {}).get('n'),'lri_nonus':(lri.get('nonUs') or {}).get('n'),'lri_spy':lri.get('spy'),
       'la_us':(la.get('us') or {}).get('n'),'di_flags':';'.join(map(str,di.get('flags') or [])) if di else 'n/a',
       'tr_written':tr.get('written'),'tr_reason':tr.get('reason'),'tr_tday':tr.get('tday'),
       'dce_mode':(s.get('dce') or {}).get('mode'),'hmm_state':(s.get('dce') or {}).get('hmm_state')}
    # universe prices
    tk=s.get('tickers') or []
    pd={}
    for x in tk:
        if isinstance(x,dict) and x.get('sym') and x.get('price') is not None:
            pd[x['sym']]=(x['price'],x.get('_lastCloseDate') or x.get('_dataAsOf'),x.get('_lastRowIncomplete'))
    prices[f]=pd
    rows.append(r)
keys=sorted({k for r in rows for k in r})
with open(out+'/archive_meta.csv','w',newline='') as fh:
    w=csv.DictWriter(fh,fieldnames=keys); w.writeheader(); w.writerows(rows)
json.dump(prices,open(out+'/archive_prices.json','w'))
print(len(rows))
