#!/usr/bin/env python3
"""Lightweight CTS/Route net-name and pin-signature matching audit."""
import json
from collections import defaultdict
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent

def read_nets(info):
    out = {}
    for p in (info.parent / 'nets').glob('net_*.json'):
        try:
            x=json.loads(p.read_text(encoding='utf-8'))
            name=str(x.get('name',''))
            if name:
                sig=tuple(sorted((str(a.get('i','')),str(a.get('p','')),int(a.get('driver',-1))) for a in (x.get('pins') or [])))
                out[name]=sig
        except Exception:
            pass
    return out

rows=[]
root_designs=sorted(p for p in ROOT.iterdir() if p.is_dir() and p.name!='dataset_card')
for d in root_designs:
    cts={p.parent.name:p for p in (ROOT/d.name/'cts'/'vectors').rglob('design_info.json')}
    route={p.parent.name:p for p in (ROOT/d.name/'route'/'vectors').rglob('design_info.json')}
    for s in sorted(set(cts)&set(route)):
        c=read_nets(cts[s]); r=read_nets(route[s]); cn=set(c); rn=set(r); exact=cn&rn
        co=cn-rn; ro=rn-cn; cb=defaultdict(list); rb=defaultdict(list)
        for n in co: cb[c[n]].append(n)
        for n in ro: rb[r[n]].append(n)
        renamed=sum(min(len(cb[k]),len(rb[k])) for k in set(cb)&set(rb))
        matched=len(exact)+renamed
        rows.append({'design':d.name,'strategy':s,'cts_nets':len(cn),'route_nets':len(rn),'exact_name_matches':len(exact),'cts_only':len(co),'route_only':len(ro),'renamed_signature_matches':renamed,'matched_after_signature':matched,'cts_match_rate':matched/len(cn) if cn else None,'route_match_rate':matched/len(rn) if rn else None,'name_jaccard':len(exact)/len(cn|rn) if cn|rn else None})
df=pd.DataFrame(rows); df.to_csv(OUT/'net_matching_summary.csv',index=False)
df.groupby('design').mean(numeric_only=True).reset_index().to_csv(OUT/'net_matching_by_design.csv',index=False)
print(df.groupby('design')[['cts_nets','route_nets','exact_name_matches','cts_only','route_only','renamed_signature_matches','cts_match_rate','route_match_rate']].mean().round(4).to_string())
