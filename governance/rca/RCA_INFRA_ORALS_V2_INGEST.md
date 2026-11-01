# Five-Agent Forensic Root-Cause Analysis: Infrastructure Automation Orals V2 Transcript Ingestion

**Branch:** `feat/rca-infra-orals-v2-ingest`  
**Worktree:** `.worktrees/feat_rca_infra_orals_v2_ingest`  
**Verdict:** **100% CODE DEFECTS (5 Compounding Pipeline/Healer Bugs) — ZERO JUDGE CALIBRATION DEFECTS**

---

## Executive Verdict: Code vs. Judges vs. Input

| Surface | Verdict | Empirical Evidence |
| :--- | :--- | :--- |
| **LLM Judges (`gpt-6-luna`)** | **100% ACCURATE (Not at Fault)** | Every deduction across Output 01 (`77 -> 87`), Output 02 (`81 -> 61`), and Output 03 (`64 -> 83`) flagged real, verifiable corruptions introduced by deterministic pipeline code during the run. |
| **V2 Input Deliverable (`01_..._00000129_hitl_resolved_transcript.md`)** | **CLEAN (90.0 PASS on `main`)** | The source V2 transcript on `main` already had `"Dan Okray"` properly attributed across all 22 turns and verified attendee roles (`AI/ML Director`, `Enterprise Compute and Automation Engineer`, `Cloud Platform Engineer`). |
| **Pipeline Ingress, Healers & Role Reconciler (Code)** | **ROOT CAUSE (5 Code Bugs)** | Passing a finalized `01_*_hitl_resolved_transcript.md` into `nee.py ingest-meeting` without staging it into the isolated worktree caused L2 to re-synthesize Output 01 from scratch, triggering 5 deterministic code bugs across ingress, `forensic_healer.py`, `speaker_role_reconciler.py`, `appendix_healer.py`, and `graph_healer.py`. |

---

## Five-Agent Forensic Collapse Chain

### 1. Detective 1 (Ingress & Worktree Inheritance): Co-Located Metadata Pollution & Unstaged Deliverable 01
- **Files:** [`dual_pipeline/cli/ingest.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/cli/ingest.py#L259-L273), [`dual_pipeline/canonical_pipeline/worktree_inheritance.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/canonical_pipeline/worktree_inheritance.py#L126-L138), [`dual_pipeline/canonical_pipeline/strategies/synthesis.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/canonical_pipeline/strategies/synthesis.py#L134-L237)
- **Mechanism:**
  1. When `--input output/infrastructure_automation/10-02-2026/Nextera_Energy_Slalom/01_Nextera_Energy_Slalom_10-02-2026_00000129_hitl_resolved_transcript.md` was passed, `ingest.py:260` auto-loaded the co-located `metadata.json`, injecting `time = "01:00 PM - 02:30 PM ET"` (shifting `time_filename` from `00000129` to `0100PM0230PMET`) and stale `speaker_role_resolutions`.
  2. `worktree_inheritance.py` redirected `output_dir` (`out_p`) to `.worktrees/transcript_infrastructure_automation_10_02_2026_nextera_energy_slalom/output/...` without copying/staging `main`'s sealed `01_..._00000129_hitl_resolved_transcript.md` or `final_release_decision.json` into the worktree.
  3. Because `checkpoint_memoizer.py:_memoize_output_1` (`L423-445`) and `synthesis.py:_resolve_output_1` (`L158-210`) only inspect `out_p` (and never check if `input_path` is itself a valid `01_*_hitl_resolved_transcript.md`), Stage L2 treated the V2 deliverable as raw text and re-ran `ForensicNormalizer` from scratch.

### 2. Detective 2 (Gate & Guard Integrity): Cross-Project `"conduit"` Substring Match Corrupting `"Dan Okray"` $\to$ `"Dan Okay"`
- **Files:** [`dual_pipeline/transcript_qa_core/forensic_healer.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/transcript_qa_core/forensic_healer.py#L323-L336), [`dual_pipeline/transcript_stages_core/attribution_builder.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/transcript_stages_core/attribution_builder.py#L162-L201)
- **Mechanism:**
  - `forensic_healer.py:323` evaluates:
    ```python
    is_conduit = any("conduit" in str(x).lower() for x in (meta.get("project", ""), meta.get("meeting_name", ""), str(meta)))
    ```
  - Including `str(meta)` searched the entire 559 KB serialized metadata dictionary, matching `"PD Conduit / Conductor"` in `graph_context_packet` and `"the conduit team"` at `00:11:51`.
  - Line 332 (`current = re.sub(r"\bOkray\b", "Okay", current)`) then executed on this `infrastructure_automation` run, corrupting all 22 of Dan Okray's turns to `**Dan Okay:**`.
  - Seventy lines later (`forensic_healer.py:390-401`), `AttributionSummaryBuilder` searched for `**Dan Okray:**`, found 0 matches, and emitted `| Dan Okray | ... | 0 | 0.0% |`.

### 3. Detective 2 & Detective 3 (Role Precedence Inversion): Gate C0.5 Overwriting Gate C0.6 HITL Role Resolutions
- **Files:** [`dual_pipeline/transcript_stages_core/c05_title_gate.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/transcript_stages_core/c05_title_gate.py#L92-L215), [`dual_pipeline/transcript_stages_core/c06_graphdb_role_gate.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/transcript_stages_core/c06_graphdb_role_gate.py#L181-L258), [`dual_pipeline/transcript_stages_core/speaker_role_reconciler.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/transcript_stages_core/speaker_role_reconciler.py#L55-L97)
- **Mechanism:**
  - Gate C0.6 (`c06_graphdb_role_gate.py`) properly resolved the verified titles from `hitl_decision_ledger` into `metadata["hitl_role_resolutions"]` (`Andrew Hanson`: `"AI/ML Director"`, `Luis Arencibia`: `"Enterprise Compute and Automation Engineer"`, `Daniel Simao`: `"Cloud Platform Engineer"`).
  - However, C0.6 never propagated those resolutions back to `metadata["roster"]` or `metadata["speaker_role_resolutions"]` (which held stale C0.5 GraphDB strings: `"June 29 notes support Project Spring..."`, `"IT Technology Manager"`, `"AWS SME"`).
  - Worse, in `speaker_role_reconciler.py:55-97` (and `L428-431`), `reconcile()` applied `hitl_role_resolutions` first (`L60-77`) and `speaker_role_resolutions` second (`L79-97`), **overwriting** the operator-verified C0.6 titles with stale C0.5 GraphDB descriptions across Outputs 01, 02, and 03.

### 4. Detective 3 & Detective 4 (Synthesis & Healer Regressions): `appendix_healer.py` Destroying Output 02 (`81` $\to$ `61`) & Endpoint/Orphan Bugs in Output 03
- **Files:** [`dual_pipeline/transcript_qa_core/appendix_healer.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/transcript_qa_core/appendix_healer.py#L504-L573), [`dual_pipeline/transcript_qa_core/recap_healer.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/transcript_qa_core/recap_healer.py#L766-L816), [`dual_pipeline/transcript_qa_core/graph_healer.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/transcript_qa_core/graph_healer.py#L121-L155), [`dual_pipeline/remediation_core/state_machine.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/remediation_core/state_machine.py#L130-L196)
- **Mechanism:**
  1. **Output 02 Collapse (`81` $\to$ `61`):** Because `"Dan Okray"` was missing from Output 01 (corrupted to `"Dan Okay"`), `_is_existing_appendix_invalid()` in `appendix_healer.py:518-569` flagged the LLM's rich multi-paragraph appendix as invalid on Attempt 2 and overwrote all 14 speaker profiles with synthetic 2-bullet regex boilerplate using the corrupted C0.5 roles—crashing Dimension 3 from `15/20` to `5/20`. Additionally, on the terminal attempt (`attempt == max_attempts`), `state_machine.py` did not write the rolled-back Attempt 1 candidate (`81`) to disk.
  2. **Output 03 Endpoint & System Defects (`64` $\to$ `83`):** `graph_healer.py:121-155` and `graph_post_extractor.py:467-508` unconditionally typed `MOLE-VC (Conference Room Endpoint)` as `"type": "person"` (`person:mole_vc`), while `_heal_enterprise_system_nodes` (`graph_healer.py:1395-1445`) only extracted hardcoded Jarvis systems rather than transcript-evidenced infrastructure systems (`AWS`, `GCP`, `GitHub Actions`, `Backstage`, `ServiceNow`, `Terraform`, `Entra ID`, `Splunk`).

### 5. Detective 5 (Release & Graph Mutation Lifecycle): Why `main` Had `+0` Graph Mutations
- **Files:** [`dual_pipeline/transcript_pipeline_core/evals_coordinator.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/transcript_pipeline_core/evals_coordinator.py#L257-L283), [`dual_pipeline/transcript_pipeline_core/uwg_deployer.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/transcript_pipeline_core/uwg_deployer.py#L368-L405), [`dual_pipeline/cli/intake_approval_helper.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/cli/intake_approval_helper.py#L11-L85)
- **Mechanism:**
  - In `main` run `54b0828a905c4ddb8906a73b4087169a`, Stage EVALS first executed the read-only Tier 1 Scoped Scout (`PRAGMA query_only = ON`), writing the `Pre-Mutation Diagnostic` stub (`Delta: +0`) to `graph_mutation_breakdown.md`.
  - When `publish_release_to_graph` invoked `uwg_deployer.build_publish_args()` (`uwg_deployer.py:400`), it hardcoded `auth_token=None` instead of forwarding the verified `auth_token` / `mutation_approval_receipt` from `metadata`.
  - Consequently, `resolve_intake_publish_approval` returned `status="AWAITING_APPROVAL"` (`terminal_outcome="AWAITING_APPROVAL"`, `graph_mutated=False`), exiting before `GovernedGraphWriter.apply()` could mutate `graph.sqlite` or overwrite `graph_mutation_breakdown.md`.
