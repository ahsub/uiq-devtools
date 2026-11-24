import json,csv,sys
K=json.load(open(sys.argv[1]))['keys']; out=sys.argv[3]
meta=list(csv.DictReader(open(sys.argv[2])))
wrote={r['tr_tday']:r for r in meta if r['tr_written']=='True'}
res=[]
for k in sorted(x for x in K if x.startswith('tr:snap')):
    s=K[k]; s=json.loads(s) if isinstance(s,str) else s
    td=s['tday']; run=s.get('run','')
    a=wrote.get(td)
    if a and a['di_flags']!='n/a':
        lri=int(a['lri_us'] or 0); spy=a['lri_spy']=='True'
        q='D26_stale' if (spy or lri>300) else 'clean'
    else:
        # no integrity metric: classify by run time
        hh=int(run[11:13]) if len(run)>13 else -1; same=run[:10]==td
        q='intraday_prevclose' if (same and 12<=hh<21) else ('nextday_unverified' if not same else 'unverified')
    res.append({'tday':td,'run':run,'quality':q,'regime':s.get('regime'),'ratio':(s.get('regimeMeta') or {}).get('serverRatio'),'n':len(s['recs']),'archive_file':a['file'] if a else ''})
with open(out,'w',newline='') as fh:
    w=csv.DictWriter(fh,fieldnames=list(res[0])); w.writeheader(); w.writerows(res)
import collections; print(collections.Counter(r['quality'] for r in res))
