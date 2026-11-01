#!/usr/bin/env python3
"""Fail-closed snapshot migration for the approved NEE V52.0 graph."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

BASE_SHA='c802383576a6556aff228ac46c36f8ad66ce440aebe21e7420bc893cb78cf781'
FINAL_SHA='d2fccfee82f5b944a0eedc5cf99dff34f2f22385a143cadb4218e0231b489b44'
ROOT=Path(__file__).resolve().parents[2]
FINAL=ROOT/'graph'/'nee_graph.sqlite'
ROLLBACK=ROOT/'rollback'/'baseline'/'nee_graph_v51.sqlite'
APPROVAL_REF='AD-V52-UPLOADED-MATERIALS-2026-001'
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for c in iter(lambda:f.read(1024*1024),b''): h.update(c)
 return h.hexdigest()
def validate(p):
 con=sqlite3.connect(p)
 checks={
  'integrity':[r[0] for r in con.execute('pragma integrity_check')],
  'fk':[list(r) for r in con.execute('pragma foreign_key_check')],
  'sources':con.execute('select count(*) from source').fetchone()[0],
  'nodes':con.execute('select count(*) from node').fetchone()[0],
  'edges':con.execute('select count(*) from edge').fetchone()[0],
  'claims':con.execute('select count(*) from claim').fetchone()[0],
  'assertions':con.execute('select count(*) from assertion').fetchone()[0],
  'aliases':con.execute('select count(*) from canonical_alias').fetchone()[0],
  'validations':con.execute('select count(*) from validation_queue').fetchone()[0],
  'open_validations':con.execute("select count(*) from validation_queue where status='open'").fetchone()[0],
  'eligible_search':con.execute('select count(*) from search_document where retrieval_eligible=1').fetchone()[0],
  'fts':con.execute('select count(*) from search_fts_docsize').fetchone()[0],
  'approval':con.execute('select count(*) from governance_approval where approval_reference=? and status="approved" and active=1',(APPROVAL_REF,)).fetchone()[0]
 }
 con.close()
 checks['result']='PASS' if checks['integrity']==['ok'] and not checks['fk'] and checks['sources']==172 and checks['nodes']==652 and checks['edges']==1069 and checks['claims']==568 and checks['assertions']==1670 and checks['aliases']==424 and checks['validations']==182 and checks['open_validations']==147 and checks['eligible_search']==checks['fts']==1334 and checks['approval']==1 else 'FAIL'
 return checks
def forward(target):
 cur=sha(target)
 if cur==FINAL_SHA:return {'result':'PASS','status':'idempotent_noop','database_sha256':cur,'validation':validate(target)}
 if cur!=BASE_SHA:raise RuntimeError(f'baseline hash mismatch: {cur}')
 if sha(FINAL)!=FINAL_SHA:raise RuntimeError('packaged final database hash mismatch')
 tmp=target.with_suffix(target.suffix+'.v52.tmp');shutil.copy2(FINAL,tmp);tmp.replace(target)
 v=validate(target)
 if v['result']!='PASS':raise RuntimeError('post-migration validation failed')
 return {'result':'PASS','status':'applied','database_sha256':sha(target),'validation':v}
def rollback(target):
 cur=sha(target)
 if cur==BASE_SHA:return {'result':'PASS','status':'idempotent_noop','database_sha256':cur}
 if cur!=FINAL_SHA:raise RuntimeError(f'final hash mismatch: {cur}')
 if sha(ROLLBACK)!=BASE_SHA:raise RuntimeError('rollback snapshot hash mismatch')
 tmp=target.with_suffix(target.suffix+'.v51.tmp');shutil.copy2(ROLLBACK,tmp);tmp.replace(target)
 return {'result':'PASS','status':'rolled_back','database_sha256':sha(target)}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('action',choices=['forward','validate','rollback']);ap.add_argument('--database',type=Path,required=True);ap.add_argument('--output',type=Path);a=ap.parse_args()
 result=forward(a.database) if a.action=='forward' else rollback(a.database) if a.action=='rollback' else validate(a.database)
 text=json.dumps(result,indent=2,sort_keys=True)+'\n'
 if a.output:a.output.write_text(text,encoding='utf-8')
 print(text,end='');return 0 if result.get('result')=='PASS' else 1
if __name__=='__main__':raise SystemExit(main())
