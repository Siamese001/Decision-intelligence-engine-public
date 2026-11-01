#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha(p:Path)->str:
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()

def classify(rel:str)->str:
 if rel=='graph/nee_graph.sqlite': return 'canonical_database'
 if rel.startswith('cards/'): return 'canonical_card'
 if rel=='PROJECT_INSTRUCTIONS.md': return 'generated_policy_mirror'
 if rel.startswith('approvals/') or rel.startswith('governance/'): return 'governance'
 if rel.startswith('migrations/'): return 'migration'
 if rel.startswith('rollback/'): return 'rollback'
 if rel.startswith('tests/') or rel.startswith('retrieval_tests/'): return 'test'
 if rel.startswith('metrics/') or rel.startswith('performance/'): return 'measurement'
 if rel.startswith('ingestion/'): return 'ingestion'
 if rel.startswith('database/'): return 'database_tooling'
 if rel.startswith('documentation/'): return 'documentation'
 if rel.startswith('reports/history/'): return 'historical'
 if rel.startswith('reports/'): return 'report'
 if rel.startswith('exports/'): return 'derived_export'
 if rel.startswith('source/'): return 'governed_source_pointer_or_extract'
 if rel.startswith('tools/'): return 'build_tooling'
 return 'package_control_or_preserved_artifact'

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
 files=[]
 for p in sorted(x for x in a.root.rglob('*') if x.is_file()):
  rel=p.relative_to(a.root).as_posix()
  if rel==a.output.relative_to(a.root).as_posix(): continue
  files.append({'path':rel,'size_bytes':p.stat().st_size,'sha256':sha(p),'classification':classify(rel)})
 payload={
  'manifest_version':1,'package':'NEE_v48.1','release_version':'48.1','release_state':'candidate','approved_current':False,
  'generated_at':'2026-07-12T19:30:00Z','normalized_zip_timestamp':'2026-07-12T00:00:00Z',
  'baseline':{'package':'NEE_v48.0','zip_sha256':'adf77b2cbb5df66e9d3c33fbd6970a89e23643879fd2068155849ef248e13408','database_sha256':'9dc863b58bac213661d97b62508f32f886e62498b4db532d6438cc0fe0b7fdf2'},
  'canonical_database':{'path':'graph/nee_graph.sqlite','sha256':next(f['sha256'] for f in files if f['path']=='graph/nee_graph.sqlite')},
  'file_count_hashed':len(files),'archive_expected_file_count':len(files)+1,
  'self_reference_policy':'manifest.json is excluded from its own file list; archive validation publishes the manifest SHA-256 externally.',
  'files':files
 }
 a.output.write_text(json.dumps(payload,indent=2,sort_keys=True,ensure_ascii=False)+'\n',encoding='utf-8')
 print(json.dumps({'files_hashed':len(files),'archive_expected_file_count':len(files)+1,'database_sha256':payload['canonical_database']['sha256']},indent=2))
if __name__=='__main__':main()
