---
trigger: model_decision
---

# Production Derivation Integrity & Anti-Overfitting Rules

Governs Master Invariant 8 (Production Derivation Integrity), Invariant 10 (Prohibition of Custom Ingestion Scripts), and Rule C-12 (Anti-Hallucination Guard).

## 1. Core Principles
1. **Derivation Integrity**: Outputs 01 (Transcript), 02 (Recap), and 03 (Projection) must be 100% derived from runtime source inputs through declared prompts and domain-blind normalization. Hardcoded domain paragraphs, fake attendee quotes, or synthetic IDs (`ev-910-*`, `claim-*`, `stmt-*`) are strictly prohibited and fail closed.
2. **Ban on Meeting-Specific Logic**: Modules in `dual_pipeline/` must never branch on meeting titles, attendee names, or project slugs (e.g. `is_mid_sprint`, `is_lens_catchup`, `if "reid" in text:`). Meeting-named files (`neet_healer.py`, `graph_post_*.py`) and hardcoded table constants (`REID_NEXUS_*`, `LENS_PHASE1_*`) are prohibited.
3. **Domain-Blind Healing Mesh**: `healing_mesh.py`, `table_healer.py`, and `graph_healer.py` perform only domain-blind structural normalization (ISO-8601/`MM/DD/YYYY` dates, table pipe alignment, fence closure, W2 canonical predicates). They never inject domain facts or branch on names.
4. **Deterministic Edge Triage**: When the materiality- and duration-proportional graph budget (`resolve_duration_mutation_budget`, with roster scaffolding exempt) is exceeded, naive popping is banned. Triage by ontological rank: (1) Core Structural & Delivery (`CONTAINS`, `INTEGRATES_WITH`, `GOVERNS`, `DELIVERS`, `CONTRIBUTES_TO`, `PRODUCES`, `BLOCKS`, `CONSTRAINS`), (2) Organizational (`MANAGES`, `HAS_ROLE`, `REPORTS_TO`), (3) Exploratory (`EXPLORES`). Evict lowest-ranked edges first and record `_budget_triage_audit`.
5. **Plain English Standard**: Executive recaps (Output 02) must use plain, concise English. Pseudo-academic word salad is rejected across QA Gate 2 and LLM Judge evaluations.

## 2. Enforcement
- AST & Regex Linter: `tools/lint_no_transcript_overfitting.py`.
- Lifecycle Hook: `nee-anti-overfitting-guard`.
- Verification Gate: `.githooks/pre-commit` and `tools/verify_commit.py`.
