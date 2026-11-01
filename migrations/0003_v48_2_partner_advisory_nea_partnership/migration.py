#!/usr/bin/env python3
"""Fail-closed exact forward/rollback migration for NEE v48.2."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

BASE_SHA='5a376919027b37b1b3c566074a8923b18fe842f70d38a5459534e3eaa52f8b0a'
CANDIDATE_SHA='b6b10223b7c6f25eed890df932f0dca50dc0ed47b5d57cb30f3f2f1df01f6ea6'
SOURCE_ID='source_partner_advisory_nea_partnership_briefing_2026_07_12'
VALIDATION_ID='validation_partner_advisory_nea_staffing_authority_2026_07_12_p1'
ROOT=Path(__file__).resolve().parents[2]
CANDIDATE=ROOT/'graph'/'nee_graph.sqlite'
ROLLBACK=ROOT/'rollback'/'baseline'/'nee_graph_v48.1.sqlite'
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for c in iter(lambda:f.read(1024*1024),b''):h.update(c)
 return h.hexdigest()
def validate(p):
 con=sqlite3.connect(p); checks={
  'integrity':[r[0] for r in con.execute('pragma integrity_check')],
  'fk':[list(r) for r in con.execute('pragma foreign_key_check')],
  'source':con.execute('select count(*) from source where source_id=?',(SOURCE_ID,)).fetchone()[0],
  'assertions':con.execute('select count(*) from assertion where primary_source_id=?',(SOURCE_ID,)).fetchone()[0],
  'authoritative_leakage':con.execute('select count(*) from search_document where source_id=? and retrieval_eligible=1',(SOURCE_ID,)).fetchone()[0],
  'validation':con.execute('select count(*) from validation_queue where validation_id=?',(VALIDATION_ID,)).fetchone()[0]}
 con.close(); checks['result']='PASS' if checks['integrity']==['ok'] and not checks['fk'] and checks['source']==1 and checks['assertions']==14 and checks['authoritative_leakage']==0 and checks['validation']==1 else 'FAIL';return checks
def forward(target):
 current=sha(target)
 if current==CANDIDATE_SHA:return {'result':'PASS','status':'idempotent_noop','database_sha256':current,'validation':validate(target)}
 if current!=BASE_SHA:raise RuntimeError(f'baseline hash mismatch: {current}')
 if sha(CANDIDATE)!=CANDIDATE_SHA:raise RuntimeError('packaged candidate database hash mismatch')
 tmp=target.with_suffix(target.suffix+'.v482.tmp');shutil.copy2(CANDIDATE,tmp);tmp.replace(target)
 v=validate(target)
 if v['result']!='PASS':raise RuntimeError('post-migration validation failed')
 return {'result':'PASS','status':'applied','database_sha256':sha(target),'validation':v}
def rollback(target):
 current=sha(target)
 if current==BASE_SHA:return {'result':'PASS','status':'idempotent_noop','database_sha256':current}
 if current!=CANDIDATE_SHA:raise RuntimeError(f'candidate hash mismatch: {current}')
 if sha(ROLLBACK)!=BASE_SHA:raise RuntimeError('rollback snapshot hash mismatch')
 tmp=target.with_suffix(target.suffix+'.v481.tmp');shutil.copy2(ROLLBACK,tmp);tmp.replace(target)
 return {'result':'PASS','status':'rolled_back','database_sha256':sha(target)}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('action',choices=['forward','validate','rollback']);ap.add_argument('--database',type=Path,required=True);ap.add_argument('--output',type=Path);a=ap.parse_args()
 result=forward(a.database) if a.action=='forward' else rollback(a.database) if a.action=='rollback' else validate(a.database)
 text=json.dumps(result,indent=2,sort_keys=True)+'\n'
 if a.output:a.output.write_text(text,encoding='utf-8')
 print(text,end='');return 0 if result.get('result')=='PASS' else 1
if __name__=='__main__':raise SystemExit(main())
