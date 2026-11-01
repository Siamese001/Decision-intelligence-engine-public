---
trigger: model_decision
---

# Career Leverage & Artifact Independence Firewall Rules

Governs partitioning between team deliverables (Outputs 01 & 02), shared graph knowledge (Output 03), and personal advisory leverage (`INTERNAL_ADVISORY`).

## Core Principles
1. **Artifact Independence**:
   - **Output 01 (Transcript)**: Forensic record of dialogue. Must remain 100% independent of GraphDB mutations; zero analytical spin.
   - **Output 02 (Recap)**: Distributed openly to consulting and client leadership. Focuses exclusively on joint catalyst, technical landscape, decisions, and next steps.
2. **Career Strategy Firewall**:
   - Executive positioning (executive compensation band), personal negotiation leverage, private political power maps, and confidential career notes are **strictly prohibited** from Outputs 01, 02, and 03.
   - Firewalled into encrypted local shadow graph (`namespace: 'INTERNAL_ADVISORY'`, `is_quarantined: 1`, `stealth_score: 1.0`).
3. **Dual-Benefit Scope for Output 03**:
   - Canonical GraphDB (`CANONICAL_GRAPH.db`) captures intelligence benefiting both PartnerAdvisory and Client. Unilateral personal insights are rejected from Output 03.
4. **Pipeline Enforcement**: Ingestion and synthesis stages (`_execute_native`, `_execute_undiarized`, `llm_judge.py`) fail closed if career leverage leaks into Outputs 01 or 02.
