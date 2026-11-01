#!/usr/bin/env python3
"""Fail-closed exact forward/validate/rollback migration for NEE v49."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

BASE_SHA='b6b10223b7c6f25eed890df932f0dca50dc0ed47b5d57cb30f3f2f1df01f6ea6'
CANDIDATE_SHA='fdd7015357f41bc8a3ee8424522f4199c95be9352b8df269c7af7e230a0fdb05'
ROOT=Path(__file__).resolve().parents[2]
CANDIDATE=ROOT/'graph'/'nee_graph.sqlite'
ROLLBACK=ROOT/'rollback'/'baseline'/'nee_graph_v48.2.sqlite'
SOURCE_IDS=['source_v49_ext_ap_apex_global_dominion_2026_05_18', 'source_v49_ext_reuters_apex_global_google_cloud_2025_12_08', 'source_v49_ext_reuters_duane_arnold_google_2025_10_27', 'source_v49_ext_nrc_duane_arnold_restart_2026_07_13', 'source_v49_ext_scrum_guide_2020', 'source_v49_ext_opm_structured_interviews_2026_07_13', 'source_v49_ext_opm_work_samples_2026_07_13', 'source_v49_ext_metr_dev_productivity_rct_2025_07_12', 'source_v49_ext_google_enterprise_ai_rct_2024_10_16', 'source_v49_ext_reuters_pjm_google_ai_2025_04_10', 'source_v49_ext_reuters_miso_microsoft_2026_01_06', 'source_v49_ext_reuters_inl_microsoft_nuclear_ai_2025_07_16', 'source_v49_ext_emerald_grid_interactive_dc_2025_07_01']
ASSERTION_IDS=['ast_v49_49726c9ce7da556d188ac857d4ea', 'ast_v49_0742302814eda7d95cc89cad5627', 'ast_v49_df5c449a7cb4a944b58c0fef50dc', 'ast_v49_4885a1e1b0ecd5abd54607de0222', 'ast_v49_f590569f2a36d85c6ae3286c7d0e', 'ast_v49_3f9b90b2d5f02bd2c63d9b0a9aab', 'ast_v49_f41e61620b8d462faa4a56aa60d5', 'ast_v49_0bed865471915c8f14c051134c4b', 'ast_v49_b1c236c973ed1bb6d4a711d15d4b', 'ast_v49_88f1140a6a1843c22bd74f3c5912', 'ast_v49_28ab471941a0d621681dfe3c8d4b', 'ast_v49_f6f1c8bb6caffd4f106f58cebd8b', 'ast_v49_fe7e92b4f55dfe0f73a7614b31fc', 'ast_v49_c2b847c233a0b2dbebf5b0afc8e9', 'ast_v49_aab18f57ea60df122b76468d16a4', 'ast_v49_f25a139228a51e842996a8fc8d03', 'ast_v49_84eb042bca5937753b84e43b4fd9', 'ast_v49_2eb5568b59a56b84219c88baee90']
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for c in iter(lambda:f.read(1024*1024),b''): h.update(c)
 return h.hexdigest()
def validate(p):
 con=sqlite3.connect(p)
 q=','.join('?' for _ in SOURCE_IDS); qa=','.join('?' for _ in ASSERTION_IDS)
 checks={
  'integrity':[r[0] for r in con.execute('pragma integrity_check')],
  'fk':[list(r) for r in con.execute('pragma foreign_key_check')],
  'sources':con.execute(f'select count(*) from source where source_id in ({q})',SOURCE_IDS).fetchone()[0],
  'assertions':con.execute(f'select count(*) from assertion where assertion_id in ({qa})',ASSERTION_IDS).fetchone()[0],
  'candidate_assertions':con.execute(f"select count(*) from assertion where assertion_id in ({qa}) and canonical_status='candidate' and canonical_eligible=0 and retrieval_eligible=0 and hitl_required=1",ASSERTION_IDS).fetchone()[0],
  'authoritative_leakage':con.execute(f'select count(*) from search_document where assertion_id in ({qa}) and retrieval_eligible=1',ASSERTION_IDS).fetchone()[0],
  'validations':con.execute('select count(*) from validation_queue').fetchone()[0],
 }
 con.close()
 checks['result']='PASS' if checks['integrity']==['ok'] and not checks['fk'] and checks['sources']==13 and checks['assertions']==18 and checks['candidate_assertions']==18 and checks['authoritative_leakage']==0 and checks['validations']==174 else 'FAIL'
 return checks
def forward(target):
 current=sha(target)
 if current==CANDIDATE_SHA: return {'result':'PASS','status':'idempotent_noop','database_sha256':current,'validation':validate(target)}
 if current!=BASE_SHA: raise RuntimeError(f'baseline hash mismatch: {current}')
 if sha(CANDIDATE)!=CANDIDATE_SHA: raise RuntimeError('packaged candidate database hash mismatch')
 tmp=target.with_suffix(target.suffix+'.v49.tmp'); shutil.copy2(CANDIDATE,tmp); tmp.replace(target)
 v=validate(target)
 if v['result']!='PASS': raise RuntimeError('post-migration validation failed')
 return {'result':'PASS','status':'applied','database_sha256':sha(target),'validation':v}
def rollback(target):
 current=sha(target)
 if current==BASE_SHA: return {'result':'PASS','status':'idempotent_noop','database_sha256':current}
 if current!=CANDIDATE_SHA: raise RuntimeError(f'candidate hash mismatch: {current}')
 if sha(ROLLBACK)!=BASE_SHA: raise RuntimeError('rollback snapshot hash mismatch')
 tmp=target.with_suffix(target.suffix+'.v482.tmp'); shutil.copy2(ROLLBACK,tmp); tmp.replace(target)
 return {'result':'PASS','status':'rolled_back','database_sha256':sha(target)}
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('action',choices=['forward','validate','rollback']); ap.add_argument('--database',type=Path,required=True); ap.add_argument('--output',type=Path); a=ap.parse_args()
 result=forward(a.database) if a.action=='forward' else rollback(a.database) if a.action=='rollback' else validate(a.database)
 text=json.dumps(result,indent=2,sort_keys=True)+'\n'
 if a.output: a.output.write_text(text,encoding='utf-8')
 print(text,end=''); return 0 if result.get('result')=='PASS' else 1
if __name__=='__main__': raise SystemExit(main())
