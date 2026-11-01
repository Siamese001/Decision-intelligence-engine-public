#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

BASE_SHA='5c326c4825c9ad3621a014361257f13d8fa4a9637196d6473281af06ae03d7a3'
FINAL_SHA='a780950fffe48e3dbc3e6509f7af8cfd5afef2e2117e0b3437f2afeb11a8129f'
ROOT=Path(__file__).resolve().parents[2]
FINAL=ROOT/'graph'/'nee_graph.sqlite'
ROLLBACK=ROOT/'rollback'/'baseline'/'nee_graph_v53.sqlite'
APPROVAL_REF='AD-V54-EDO-AUTHORITY-BOUNDARY-2026-001'
EXPECTED={'source':174,'claim':583,'assertion':1687,'validation':184,'route':3948,'search':2271,'eligible_search':1352,'canonical_edges':638,'current_assertions':1085,'decision_safe_assertions':702}
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for c in iter(lambda:f.read(1024*1024),b''):h.update(c)
 return h.hexdigest()
def validate(p):
 con=sqlite3.connect(p); integ=[r[0] for r in con.execute('pragma integrity_check')]; fk=[list(r) for r in con.execute('pragma foreign_key_check')]
 vals={'source':con.execute('select count(*) from source').fetchone()[0],'claim':con.execute('select count(*) from claim').fetchone()[0],'assertion':con.execute('select count(*) from assertion').fetchone()[0],'validation':con.execute('select count(*) from validation_queue').fetchone()[0],'route':con.execute('select count(*) from canonical_route').fetchone()[0],'search':con.execute('select count(*) from search_document').fetchone()[0],'eligible_search':con.execute('select count(*) from search_document where retrieval_eligible=1').fetchone()[0],'canonical_edges':con.execute('select count(*) from v_canonical_edge').fetchone()[0],'current_assertions':con.execute('select count(*) from v_current_assertion').fetchone()[0],'decision_safe_assertions':con.execute('select count(*) from v_decision_safe_assertion').fetchone()[0],'approval':con.execute('select count(*) from governance_approval where approval_reference=? and status="approved" and active=1',(APPROVAL_REF,)).fetchone()[0]}
 con.close(); vals['integrity']=integ;vals['fk']=fk;vals['result']='PASS' if integ==['ok'] and not fk and vals['approval']==1 and all(vals[k]==v for k,v in EXPECTED.items()) else 'FAIL';return vals
def forward(target):
 cur=sha(target)
 if cur==FINAL_SHA:return {'result':'PASS','status':'idempotent_noop','database_sha256':cur,'validation':validate(target)}
 if cur!=BASE_SHA:raise RuntimeError(f'baseline hash mismatch: {cur}')
 if sha(FINAL)!=FINAL_SHA:raise RuntimeError('packaged final database mismatch')
 tmp=target.with_suffix(target.suffix+'.v54.tmp');shutil.copy2(FINAL,tmp);tmp.replace(target);v=validate(target)
 if v['result']!='PASS':raise RuntimeError('post migration validation failed')
 return {'result':'PASS','status':'applied','database_sha256':sha(target),'validation':v}
def rollback(target):
 cur=sha(target)
 if cur==BASE_SHA:return {'result':'PASS','status':'idempotent_noop','database_sha256':cur}
 if cur!=FINAL_SHA:raise RuntimeError(f'final hash mismatch: {cur}')
 if sha(ROLLBACK)!=BASE_SHA:raise RuntimeError('rollback snapshot mismatch')
 tmp=target.with_suffix(target.suffix+'.v53.tmp');shutil.copy2(ROLLBACK,tmp);tmp.replace(target);return {'result':'PASS','status':'rolled_back','database_sha256':sha(target)}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('action',choices=['forward','validate','rollback']);ap.add_argument('--database',type=Path,required=True);ap.add_argument('--output',type=Path);a=ap.parse_args();r=forward(a.database) if a.action=='forward' else rollback(a.database) if a.action=='rollback' else validate(a.database);t=json.dumps(r,indent=2,sort_keys=True)+'\n';print(t,end='');a.output and a.output.write_text(t);return 0 if r.get('result')=='PASS' else 1
if __name__=='__main__':raise SystemExit(main())
