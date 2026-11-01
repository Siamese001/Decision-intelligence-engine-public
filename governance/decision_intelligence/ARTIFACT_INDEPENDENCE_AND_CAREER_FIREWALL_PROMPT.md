# Artifact Independence & Career Leverage Firewall Prompt Contract (v1.0.0)
**Document ID:** `PROMPT-GOV-FIREWALL-INDEPENDENCE-20260912-V1`  
**Classification:** Confidential Governance & Epistemic Boundary Protocol  
**Target Pipelines:** `dual_pipeline/transcript_pipeline.py`, `dual_pipeline/transcript_stages.py`, `dual_pipeline/graph_writer.py`, `storage/database.py`  
**Enforcement Boundary:** Strict isolation between Team-Facing Deliverables (Outputs 01 & 02), Shared Enterprise GraphDB (Output 03), and Private Executive Career Leverage (`INTERNAL_ADVISORY` Enclave).

---

## 1. PURPOSE & ARCHITECTURAL FOUNDATION

The Decision Intelligence Engine produces three primary artifacts from meeting recordings and transcripts:
- **Output 01 (`01_<meeting>_hitl_resolved_transcript.md`)**: Forensic Source-of-Truth Transcript.
- **Output 02 (`02_<meeting>_executive_recap.md`)**: Email-Ready Executive Recap for PartnerAdvisory and Client Teams.
- **Output 03 (`03_<meeting>_canonical_signal_projection.json`)**: Canonical Knowledge Graph Delta for PartnerAdvisory and Apex Global (NEE).

### The Tripartite Independence & Firewall Mandate
1. **Output 01 & Output 02 Independence**: Outputs 01 and 02 must remain **strictly independent** of the GraphDB and Output 03 mutations. They represent empirical spoken truth and collaborative team synthesis, completely untainted by post-hoc graph mutations.
2. **Team-Safe Availability**: Outputs 01 and 02 are distributed openly to the PartnerAdvisory account team, cross-functional engineering pods, and client leadership (Apex Global / Apex Utility). They must remain professional, objective, high-signal, and free of any personal or private commentary.
3. **GraphDB Dual-Benefit Scope**: Output 03 and the canonical GraphDB (`CANONICAL_GRAPH.db`) capture intelligence that benefits **both PartnerAdvisory and the Client** (business capability maps, architectural decisions, technical debt blast radius, system dependencies, co-delivery operating models).
4. **Career Leverage Firewall ($1.2M Career Track)**: Strategic insights, political power maps, informal veto analyses, personal compensation leverage, and career positioning vectors that **solely benefit the operator's personal career** must be **rigorously partitioned and firewalled** away from Outputs 01, 02, and canonical Output 03. They route exclusively to the encrypted local `INTERNAL_ADVISORY` shadow graph.

---

## 2. THE FOUR ISOLATION TIERS

```
┌─────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                    THE FOUR ISOLATION TIERS & DATA BOUNDARIES                               │
├──────────────┬───────────────────────────────┬───────────────────────────┬──────────────────────────────────┤
│ Tier         │ Artifact / Subsystem          │ Target Audience           │ Permitted Content & Governance   │
├──────────────┼───────────────────────────────┼───────────────────────────┼──────────────────────────────────┤
│ TIER 1       │ Output 01:                    │ PartnerAdvisory Team,              │ Pure literal dialogue, verbatim  │
│ Empirical    │ Forensic Transcript           │ Client Participants,      │ locators, phonetic cleanup, ASR  │
│ Transcript   │ (`01_hitl_resolved_...md`)    │ Legal / Compliance Record │ repair. ZERO personal notes.     │
├──────────────┼───────────────────────────────┼───────────────────────────┼──────────────────────────────────┤
│ TIER 2       │ Output 02:                    │ PartnerAdvisory Account Team,      │ Executive objective, findings,   │
│ Team-Safe    │ Executive Recap               │ Delivery Pods,            │ decisions, and actionable next   │
│ Recap        │ (`02_executive_recap.md`)     │ Client Leadership         │ steps. ZERO career strategy.     │
├──────────────┼───────────────────────────────┼───────────────────────────┼──────────────────────────────────┤
│ TIER 3       │ Output 03:                    │ PartnerAdvisory Engineering,       │ Canonical entities, capabilities,│
│ Canonical    │ Canonical Signal Projection   │ Client Enterprise Arch,   │ technical dependencies, ADRs.    │
│ GraphDB      │ (`03_canonical_signal_...json`│ Governed Knowledge Graph  │ Benefits joint PartnerAdvisory + NEE work.│
├──────────────┼───────────────────────────────┼───────────────────────────┼──────────────────────────────────┤
│ TIER 4       │ Encrypted Shadow Graph:       │ OPERATOR ONLY             │ Personal career leverage, private│
│ Firewalled   │ `INTERNAL_ADVISORY` Enclave   │ (Strictly Private & Local)│ political maps, informal vetoes, │
│ Career Intel │ (`shadow_graph_nodes`)        │                           │ compensation positioning angles. │
└──────────────┴───────────────────────────────┴───────────────────────────┴──────────────────────────────────┘
```

---

## 3. MASTER FIREWALL RULES (NON-NEGOTIABLE)

### Rule F-01: Absolute Transcript Independence (Output 01)
- The forensic transcript is an immutable, empirical record of what was spoken during the recorded session.
- Output 01 must NEVER incorporate GraphDB entity IDs, graph mutations, analytical interpretations, or personal strategic reflections.
- Output 01 must NEVER be edited to reflect post-meeting developments or graph-derived insights.

### Rule F-02: Clean Team-Safe Executive Recap (Output 02)
- Output 02 is distributed directly to the PartnerAdvisory account team and client stakeholders via email and shared repositories.
- **Prohibited in Output 02**:
  - Personal career advancement strategies or positioning notes.
  - Confidential consulting commercial leverage angles (e.g., SOW renegotiation traps, margin capture tactics).
  - Unstated personal political observations (e.g., "Stakeholder X has weak executive standing").
  - Compensation, promotion, or personal credit-claiming commentary.
- **Mandatory in Output 02**: High-signal executive context, joint business value, technical landscape grounding, confirmed architectural decisions, and an owner-attributed Next Steps table.

### Rule F-03: Joint Enterprise Value for Canonical Graph (Output 03)
- Output 03 generates mutations solely for the joint PartnerAdvisory + Client knowledge graph.
- Every promoted node and assertion must answer: *"Does this fact benefit the joint enterprise delivery, architectural clarity, or governance of PartnerAdvisory and NEE?"*
- Any insight that fails this test (i.e. it only benefits the operator's personal career) is **STRICTLY REJECTED** from Output 03 and forbidden from entering canonical SQLite tables (`node`, `edge`, `assertion`, `evidence_item`).

### Rule F-04: Encrypted Shadow Partitioning for Career Leverage (Tier 4)
- Personal career intelligence (e.g., tracking who holds informal veto power over promotions, mapping organizational friction for personal advisory positioning, formulating personal negotiation posture) must be routed **exclusively** to the `INTERNAL_ADVISORY` shadow storage:
  - Database table: `shadow_graph_nodes` & `shadow_graph_edges` in `storage/database.py`.
  - Namespace: `INTERNAL_ADVISORY`.
  - Security Flags: `is_quarantined = 1`, `stealth_score = 1.0`.
  - Categories: `STAKEHOLDER_DYNAMICS`, `DECISION_INFLUENCE`, `ADOPTION_RISK`, `BLOCKER`.
- Tier 4 data is strictly firewalled from git tracking, client-facing export scripts, and team-shared directories.

---

## 4. PROMPT IMPLEMENTATION DIRECTIVES

When running transcript synthesis pipelines, the language model extractor and judge MUST enforce the following stage-specific directives:

### In Stage 01 (Forensic Normalization):
```markdown
[FIREWALL DIRECTIVE]: You are generating Output 01. Your sole duty is literal phonetic and dialogue fidelity. 
Do not inject graph mutations, external analytical commentary, or personal interpretations. Output 01 must remain 
an independent, unvarnished source of truth for the entire team.
```

### In Stage 02 (Executive Recap):
```markdown
[FIREWALL DIRECTIVE]: You are generating Output 02, which will be distributed to the PartnerAdvisory consulting team and 
client stakeholders. You MUST ensure that Output 02 contains ZERO personal career positioning, private political notes, 
confidential consulting leverage strategies, or internal compensation angles. Focus exclusively on the joint business 
catalyst, technical landscape, confirmed decisions, and team next steps.
```

### In Stage 03 (Canonical Signal Projection):
```markdown
[FIREWALL DIRECTIVE]: You are generating Output 03 for the canonical GraphDB. Only extract entities, capabilities, 
and relationships that provide legitimate enterprise value to PartnerAdvisory and NEE. If you identify sensitive political friction, 
informal vetoes, or career leverage angles that solely benefit the operator, do NOT emit them into Output 03. 
Route them to the firewalled INTERNAL_ADVISORY shadow partition.
```

---

```
═════════════════════════════════════════════════════════════════════════════════════════════════════════════
END OF PROMPT CONTRACT — ENFORCE ACROSS ALL SYNTHESIS STAGES AND LLM JUDGE GATES
═════════════════════════════════════════════════════════════════════════════════════════════════════════════
```
