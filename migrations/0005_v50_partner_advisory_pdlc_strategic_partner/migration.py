#!/usr/bin/env python3
"""Fail-closed snapshot migration for the exact approved NEE V50 graph."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

BASE_SHA='fdd7015357f41bc8a3ee8424522f4199c95be9352b8df269c7af7e230a0fdb05'
FINAL_SHA='e8492bafc1066352e6f57fa9bbc97bb700ccf99a0178f43b151bd67e7f9793a8'
ROOT=Path(__file__).resolve().parents[2]
FINAL=ROOT/'graph'/'nee_graph.sqlite'
ROLLBACK=ROOT/'rollback'/'baseline'/'nee_graph_v49.sqlite'
CANONICAL_IDS=['asrt_v50_b4cc91cdc2492f766a3ea9dc878eaf', 'asrt_v50_f986aefa6f431d77ed18e7d9c58893', 'asrt_v50_34078c82f0cd5f4bbda5ac86e74677', 'asrt_v50_f1a498dee656bf18e84db456a11ffd', 'asrt_v50_43e02867ff1590e1d0e4c7a5f6da3e', 'asrt_v50_27394ab26319bf7f1c8ecfb47b0440', 'asrt_v50_91199add75cb01a7477e58ab247480', 'asrt_v50_dac99a37abc64fba999137fe22e62c', 'asrt_v50_d80a69967c3d6ddb97af01e503590f', 'asrt_v50_41da6d3fc34db8b4e524c2a7461d23', 'asrt_v50_948b92bf920e74b83d2139b3e76936', 'asrt_v50_2404e8c9069c93905c9bc6e888d699', 'asrt_v50_46559356f9320f7e3999284e4e02b6', 'asrt_v50_2ee1c7bb9bd94c93a277c17485d86a', 'asrt_v50_8adcea55b918abafb979d1b740db5a', 'asrt_v50_2f51ebbe3b9e1d01b3b68079548998', 'asrt_v50_c08ecc36ac6ff06f6f6c904963e7d9', 'asrt_v50_e256683a857ad69ceeaeff226c4ccb', 'asrt_v50_fb6d1b60bb6ba788ac8e5787470471', 'asrt_v50_7f860b96fb4e3c1c7749e3784dfb41', 'asrt_v50_ec7ea91b9bd03d7f4257cb24f184fe', 'asrt_v50_3bf2cffa20e5e4cc1e6059ff99612b', 'asrt_v50_49dfdca845d187f5bd9d226cc99449', 'asrt_v50_ba26114657796cd65b23f4d86d6e86', 'asrt_v50_54f46c422959a0044568e48b72c382', 'asrt_v50_f4b9bb907bd58e48058ca0b9b47d99', 'asrt_v50_d0347e92f639d783f4dd4303148116']
CANDIDATE_IDS=['asrt_v50_04652774bfedbcd9f63bc4cf18fd0a', 'asrt_v50_17cdad0acdcd16e0bd4db0a57c8711', 'asrt_v50_3f4650dc1b3700bf210ea3d2b2b595', 'asrt_v50_d43e7d7fd06a03bd8e2d342edcf4e3', 'asrt_v50_d809d833603926345fa92b65e0e875', 'asrt_v50_5c59a965e50ba8f3c73dd58b50a975', 'asrt_v50_167ebb3a6a8eed84e2aea0c7a2740b', 'asrt_v50_471db5955fac36825c708d6110ee82', 'asrt_v50_fdb87685d764aa1ca6c6d7beb603fb', 'asrt_v50_276434bed0036f511fff898ed72d7c', 'asrt_v50_511b64c70b9ea873671d91a7cde79e', 'asrt_v50_a8507ecd97f72fe17cfa136d8a8773', 'asrt_v50_af5d70908046fb509a9a9b04040ba2']
NODE_IDS=['source_v50_user_pdlc_strategic_partner_clarification_2026_07_13', 'source_v50_user_jon_perkins_role_confirmation_2026_07_13', 'source_v50_linkedin_jon_perkins_2026_07_13', 'stakeholder_jon_perkins', 'pattern_role_specific_agentic_assistant_architecture', 'pattern_specification_driven_development', 'pattern_ai_pdlc_role_evolution', 'pattern_ai_pdlc_change_orchestration', 'risk_ai_pdlc_bottleneck_displacement']
APPROVAL_REF='AD-V50-partner_advisory-PDLC-STRATEGIC-PARTNER-2026-001'
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for c in iter(lambda:f.read(1024*1024),b''): h.update(c)
 return h.hexdigest()
def validate(p):
 con=sqlite3.connect(p)
 q=','.join('?' for _ in CANONICAL_IDS); qc=','.join('?' for _ in CANDIDATE_IDS); qn=','.join('?' for _ in NODE_IDS)
 checks={
  'integrity':[r[0] for r in con.execute('pragma integrity_check')],
  'fk':[list(r) for r in con.execute('pragma foreign_key_check')],
  'canonical_assertions':con.execute(f"select count(*) from assertion where assertion_id in ({q}) and canonical_status='canonical' and validation_state='accepted_legacy' and retrieval_eligible=1 and approval_reference=?",CANONICAL_IDS+[APPROVAL_REF]).fetchone()[0],
  'candidate_assertions':con.execute(f"select count(*) from assertion where assertion_id in ({qc}) and canonical_status='candidate' and validation_state='needs_review' and retrieval_eligible=0",CANDIDATE_IDS).fetchone()[0],
  'nodes':con.execute(f"select count(*) from node where node_id in ({qn})",NODE_IDS).fetchone()[0],
  'approval':con.execute('select count(*) from governance_approval where approval_reference=? and status="approved" and active=1',(APPROVAL_REF,)).fetchone()[0],
  'fts':con.execute('select count(*) from search_fts_docsize').fetchone()[0],
  'validations':con.execute('select count(*) from validation_queue').fetchone()[0],
 }
 con.close(); checks['result']='PASS' if checks['integrity']==['ok'] and not checks['fk'] and checks['canonical_assertions']==27 and checks['candidate_assertions']==13 and checks['nodes']==9 and checks['approval']==1 and checks['fts']==1258 and checks['validations']==174 else 'FAIL'; return checks
def forward(target):
 cur=sha(target)
 if cur==FINAL_SHA: return {'result':'PASS','status':'idempotent_noop','database_sha256':cur,'validation':validate(target)}
 if cur!=BASE_SHA: raise RuntimeError(f'baseline hash mismatch: {cur}')
 if sha(FINAL)!=FINAL_SHA: raise RuntimeError('packaged final database hash mismatch')
 tmp=target.with_suffix(target.suffix+'.v50.tmp'); shutil.copy2(FINAL,tmp); tmp.replace(target)
 v=validate(target)
 if v['result']!='PASS': raise RuntimeError('post-migration validation failed')
 return {'result':'PASS','status':'applied','database_sha256':sha(target),'validation':v}
def rollback(target):
 cur=sha(target)
 if cur==BASE_SHA: return {'result':'PASS','status':'idempotent_noop','database_sha256':cur}
 if cur!=FINAL_SHA: raise RuntimeError(f'final hash mismatch: {cur}')
 if sha(ROLLBACK)!=BASE_SHA: raise RuntimeError('rollback snapshot hash mismatch')
 tmp=target.with_suffix(target.suffix+'.v49.tmp'); shutil.copy2(ROLLBACK,tmp); tmp.replace(target)
 return {'result':'PASS','status':'rolled_back','database_sha256':sha(target)}
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('action',choices=['forward','validate','rollback']); ap.add_argument('--database',type=Path,required=True); ap.add_argument('--output',type=Path); a=ap.parse_args()
 result=forward(a.database) if a.action=='forward' else rollback(a.database) if a.action=='rollback' else validate(a.database)
 text=json.dumps(result,indent=2,sort_keys=True)+'\n'
 if a.output: a.output.write_text(text,encoding='utf-8')
 print(text,end=''); return 0 if result.get('result')=='PASS' else 1
if __name__=='__main__': raise SystemExit(main())
