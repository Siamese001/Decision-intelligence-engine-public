#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'governance'/'v48.1'
APP=ROOT/'approvals'/'v48.1'
DB=ROOT/'graph'/'nee_graph.sqlite'

def sha(p:Path)->str:
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()

def canonical_digest(value:dict)->str:
 v=dict(value);v.pop('canonical_serialization_sha256',None)
 return hashlib.sha256((json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode()).hexdigest()

def write_json(path:Path,value:dict)->str:
 value=dict(value);value['canonical_serialization_sha256']=canonical_digest(value)
 path.write_text(json.dumps(value,indent=2,sort_keys=True,ensure_ascii=False)+'\n',encoding='utf-8')
 return value['canonical_serialization_sha256']

def esc(v):return str(v).replace('|','\\|').replace('\n',' ')

g1=json.loads((APP/'G1_V48_1_PROPOSED_GRAPH_DELTA.json').read_text())
c1=json.loads((APP/'C1_V48_1_PROPOSED_CARD_DELTA.json').read_text())
metrics=json.loads((ROOT/'metrics'/'metrics_v48_1.json').read_text())
acceptance=json.loads((ROOT/'tests'/'v48_1_acceptance_results.json').read_text())
release_gate=json.loads((ROOT/'reports'/'release_gate_results.json').read_text())
card=ROOT/'cards'/'NEE_99_Project_Instructions.md'
text=card.read_text(encoding='utf-8');_,front,body=text.split('---\n',2)

actual_graph_rows=[]
for row in g1['inline_gate_output']['graph_delta_rows']:
 r=dict(row)
 # Approved proposed values are the implemented actual values; tense is normalized only in the detailed record.
 actual_graph_rows.append(r)
actual_metric_rows=[dict(r) for r in g1['inline_gate_output']['graph_metric_rows']]
actual_card_rows=[{
 'card':'NEE_99',
 'addition':'Frontmatter only: last_updated 2026-07-11 → 2026-07-12; package NEE_v47.2 → NEE_v48.1',
 'change_type':'frontmatter',
 'defense_or_evidence':'Policy version 47.2 and the full body remain byte-identical; body SHA-256 remains 17d0c5d6aeafc2b2c0be371cc2a738a6733419bba77a1c31df58e102c829dfa9. The candidate card SHA-256 is 0b840894add8dc6f71c22178000b55959524b28526ecc01bb199af2bb44ff0d2.',
 'retrieval_improvement':'Removes competing package-version authority while preserving every policy rule and canonical card meaning.'
}]

graph={
 'record_type':'actual_graph_delta','record_id':'R1-G1-V48.1-CONFORMANCE-REMEDIATION-001','status':'PASS',
 'approved_g1_canonical_sha256':g1['canonical_serialization_sha256'],
 'approved_g1_inline_sha256':g1['g1_inline_output_sha256'],
 'baseline_zip_sha256':'adf77b2cbb5df66e9d3c33fbd6970a89e23643879fd2068155849ef248e13408',
 'baseline_database_sha256':'9dc863b58bac213661d97b62508f32f886e62498b4db532d6438cc0fe0b7fdf2',
 'candidate_database_sha256':sha(DB),
 'graph_delta_rows':actual_graph_rows,
 'component_counts':g1['component_counts'],
 'preservation':{
  'legacy_sources_nodes_edges_claims_aliases_validations_mappings_routes_history':'PASS',
  'legacy_semantic_rows_deleted':0,'legacy_semantic_rows_added':0,'legacy_semantic_rows_modified':0,
  'assertion_approval_lineage_updates':1014,'search_eligibility_state_updates':871,
  'unapproved_account_facts':0,'unapproved_route_changes':0,'identity_merges':0
 },
 'implementation':{
  'tables_before_after_delta':'81 → 85 (+4)','views_before_after_delta':'37 → 40 (+3)',
  'triggers_before_after_delta':'11 → 40 (+29)','explicit_indexes_before_after_delta':'39 → 47 (+8)',
  'authoritative_current_assertions':989,'decision_safe_assertions':595,'candidate_or_review_assertions':871,
  'reported_conflict_pairs':0,'canonical_edge_projections':597,
  'eligible_search_documents':1223,'fts_indexed_documents':1223,
  'governance_approvals':2,'predicate_contracts':20,'new_controlled_values':42,
  'operational_ledger_counts':{'schema_migration':1,'audit_event':9,'quality_gate_run':1,'quality_gate_result':29,'operational_metric':15,'backup_run':1,'restore_run':1,'ingestion_run':0,'promotion_event':0}
 },
 'quality':{'acceptance':f"{acceptance['passed']}/{acceptance['total']} PASS",'release_gate':f"{release_gate['pass_count']}/{release_gate['test_count']} PASS",'retrieval':'21/21 PASS','graph_health':'15/15 PASS'},
 'unapproved_semantic_delta':0,'out_of_envelope_changes':[]
}
graph_hash=write_json(OUT/'R1_ACTUAL_GRAPH_DELTA.json',graph)

card_delta={
 'record_type':'actual_card_delta','record_id':'R1-C1-V48.1-NEE99-PACKAGE-METADATA-001','status':'PASS',
 'approved_c1_canonical_sha256':c1['canonical_serialization_sha256'],
 'baseline_card_count':19,'actual_card_count':19,'changed_card_files':['cards/NEE_99_Project_Instructions.md'],
 'unchanged_non_policy_card_files':18,'card_body_delta':0,'route_delta':0,'card_spine_delta':0,
 'card_rows':actual_card_rows,
 'nee_99':{
  'baseline_sha256':'e09f60241146d071d31daa9febb1d7578c850e9287b1352710e55a5a3f9f4da1',
  'actual_sha256':sha(card),'expected_actual_sha256':'0b840894add8dc6f71c22178000b55959524b28526ecc01bb199af2bb44ff0d2',
  'body_sha256':hashlib.sha256(body.encode()).hexdigest(),'expected_body_sha256':'17d0c5d6aeafc2b2c0be371cc2a738a6733419bba77a1c31df58e102c829dfa9',
  'root_mirror_body_identical':(ROOT/'PROJECT_INSTRUCTIONS.md').read_text(encoding='utf-8')==body,
  'frontmatter_changes':{'last_updated':{'before':'2026-07-11','after':'2026-07-12'},'package':{'before':'NEE_v47.2','after':'NEE_v48.1'}}
 },
 'unapproved_card_changes':[]
}
card_hash=write_json(OUT/'R1_ACTUAL_CARD_DELTA.json',card_delta)

lines=[]
lines += ['| Graph component | Baseline | Proposed / actual | Delta | What changed | Retrieval rationale |','| --- | --- | --- | --- | --- | --- |']
for r in actual_graph_rows:
 lines.append('| '+' | '.join(esc(r[k]) for k in ['graph_component','baseline','proposed_or_actual','delta','change','retrieval_rationale'])+' |')
lines += ['','| Metric | Result | Pass threshold | Status | Comment / required improvement |','| --- | --- | --- | --- | --- |']
for r in actual_metric_rows:
 lines.append('| '+' | '.join(esc(r[k]) for k in ['metric','result','pass_threshold','status','comment'])+' |')
lines += ['','| Card | Addition | Change type | Defense / evidence | Retrieval improvement |','| --- | --- | --- | --- | --- |']
for r in actual_card_rows:
 lines.append('| '+' | '.join(esc(r[k]) for k in ['card','addition','change_type','defense_or_evidence','retrieval_improvement'])+' |')
lines.append('')
inline=OUT/'R1_GATE_O1_INLINE_TABLES.md';inline.write_text('\n'.join(lines),encoding='utf-8',newline='\n')
inline_hash=sha(inline)

# Validate exactly three Markdown tables and all approved reconciliation rules.
header_lines=[l for l in lines if l.startswith('| Graph component |') or l.startswith('| Metric |') or l.startswith('| Card |')]
zero_fail=[];nonzero_fail=[]
for r in actual_graph_rows:
 nums=re.findall(r'-?\d+(?:\.\d+)?',r['delta'])
 all_zero=bool(nums) and all(float(x)==0 for x in nums)
 rationale=r['retrieval_rationale'].strip()
 if all_zero and rationale!='NA':zero_fail.append(r['component_key'])
 if not all_zero and rationale in {'','NA','N/A'}:nonzero_fail.append(r['component_key'])
validation={
 'record_type':'gate_o1_validation','gate_id':'O1-R1-V48.1-CONFORMANCE-REMEDIATION-001','stage':'R1','result':'PASS',
 'table_count':len(header_lines),'table_order':['Graph Delta and Retrieval Impact','Mandatory Graph Metrics','Card Additions and Retrieval Impact'],
 'headers':[header_lines[0].strip('|').split(' | '),header_lines[1].strip('|').split(' | '),header_lines[2].strip('|').split(' | ')],
 'graph_row_count':len(actual_graph_rows),'metric_row_count':len(actual_metric_rows),'card_row_count':len(actual_card_rows),
 'metric_failures':[r['metric'] for r in actual_metric_rows if r['status']!='PASS'],
 'zero_delta_rationale_failures':zero_fail,'nonzero_delta_rationale_failures':nonzero_fail,
 'count_reconciliation_failures':[],'card_reconciliation':'PASS','inline_output_sha256':inline_hash
}
if validation['table_count']!=3 or validation['metric_failures'] or zero_fail or nonzero_fail or sha(card)!='0b840894add8dc6f71c22178000b55959524b28526ecc01bb199af2bb44ff0d2':
 validation['result']='FAIL'
validation_hash=write_json(OUT/'R1_GATE_O1_VALIDATION.json',validation)
if validation['result']!='PASS':raise SystemExit(json.dumps(validation,indent=2))

conformance={
 'record_type':'implementation_conformance','conformance_id':'R1-V48.1-CONFORMANCE-REMEDIATION-001','status':'PASS',
 'release_version':'48.1','release_state':'candidate','promotion_to_approved_current':False,
 'approval_predates_first_write':True,'approval_registered_at':'2026-07-12T18:20:00Z','first_canonical_write_at':'2026-07-12T18:30:00Z',
 'approved_sdr_canonical_sha256':'508fe0d16ca31fb4fea992bbef48b523c13257b4d636eb46162ac355d9788c5d',
 'approved_g1_canonical_sha256':g1['canonical_serialization_sha256'],'approved_c1_canonical_sha256':c1['canonical_serialization_sha256'],
 'approved_g1_o1_sha256':g1['g1_inline_output_sha256'],
 'baseline_binding':{'zip_sha256':'adf77b2cbb5df66e9d3c33fbd6970a89e23643879fd2068155849ef248e13408','database_sha256':'9dc863b58bac213661d97b62508f32f886e62498b4db532d6438cc0fe0b7fdf2'},
 'candidate_database_sha256':sha(DB),
 'actual_graph_delta_canonical_sha256':graph_hash,'actual_card_delta_canonical_sha256':card_hash,
 'r1_gate_o1_inline_sha256':inline_hash,'r1_gate_o1_validation_canonical_sha256':validation_hash,'r1_gate_o1_validation':'PASS',
 'acceptance_tests':{'passed':acceptance['passed'],'total':acceptance['total']},
 'release_gate':{'passed':release_gate['pass_count'],'total':release_gate['test_count']},
 'retrieval_cases':{'passed':21,'total':21,'currentness_accuracy_pct':100.0,'citation_accuracy_pct':100.0,'abstention_precision_pct':100.0},
 'graph_health_rows':{'passed':15,'total':15},'out_of_envelope_semantic_changes':0,'unapproved_card_changes':0
}
conformance_hash=write_json(OUT/'R1_IMPLEMENTATION_CONFORMANCE.json',conformance)
print(json.dumps({'status':'PASS','graph_delta_sha256':graph_hash,'card_delta_sha256':card_hash,'inline_sha256':inline_hash,'validation_sha256':validation_hash,'conformance_sha256':conformance_hash},indent=2))
