# RAW TRANSCRIPT SYNTHESIS PROMPT -- CHANGELOG & VERSION HISTORY

This document records the complete evolutionary history of the canonical prompt contract for raw transcript synthesis, executive meeting recaps, and knowledge graph signal projections.

---

## v5.7 (Current Version)

### Goal: Enterprise Generalization & Scalability Across All Meeting Archetypes
Prompt refactoring based on an exhaustive adversarial audit (20 findings across 8 enterprise meeting archetypes) and grounded in 4 independent research streams (Meeting Taxonomy, Few-Shot Exemplars, Action Extraction, and Token Budget Optimization).

### Key Additions & Refactorings:
1. **Balanced 5-Category Taxonomy (Fixes F-01, F-02, F-15)**:
   - Symmetrically defines 5 meeting archetypes: **Forward-Looking** (Planning, Architecture, Sprint, Kickoff, Roadmap), **Operational** (Status Sync, SteerCo, Health Check, Sprint Review, Incident Triage), **Collaborative** (Working Session, Discovery, Mob Design, Requirements Workshop), **Backward-Looking** (Retrospective, Post-Mortem, Lessons Learned, Project Closure), and **Informational** (Briefing, Demo, Town Hall, Readout, Training).
   - Formulates type-calibrated action density floors: `floor(min/10)`, `floor(min/12)`, `floor(min/20)`, `floor(min/15)`, and `max(1, floor(min/30))`.
   - Enforces 4 Universal Extraction Invariants across all categories: Universal Actionability, Universal Attribution Waterfall, Intent-Calibrated Density Floor, and Cross-Section Continuity.
2. **Domain Parameterization (Fixes F-03, F-04, F-16)**:
   - Eliminates hardcoded company references in structural templates; replaces with parameterized placeholders `[Delivery Partner / Consulting Firm]`, `[Client Organization]`, `[Other Organization(s)]`.
   - Generalizes Owner Attribution Waterfall to use `[Speaker's Organization] ([Team Name], escalation: [Speaker Name])`.
   - Embeds balanced BAD vs GOOD exemplars across all 5 archetypes with Chain-of-Thought (CoT) reasoning.
   - Generalizes input template attendee list and adds explicit `Duration (minutes)` field.
3. **Cross-Output Consistency Gate (Fixes F-05)**:
   - Adds Section 10.4 verifying coherence across all three artifacts: action owners in Output 2 exist in Output 1 participant list; Output 3 entities trace to Output 1 timestamps; recap actions and graph action_updates agree on ownership/timing; date, counts, and type classification match across outputs.
4. **Schema & Contract Hardening (Fixes F-06, F-08, F-13)**:
   - Adds required top-level `"schema_version": "5.7"` in Output 3 JSON.
   - Provides multi-topic meeting guidance (unified projection under primary cadence slug).
   - Enforces single canonical index-prefix filename ordering (`01_`, `02_`, `03_`).
   - Tightens cross-validation escape hatch: "no action committed" note requires finding reference, timestamp span, and confirmation that no verbal commitment was made.
5. **Token Budget Optimization & Clean ASCII Encoding (Fixes F-09, F-10, F-11, F-12, F-14, F-17, F-19)**:
   - Extracts 140 lines of changelog/version history to this dedicated file, leaving a concise stub in Section 13.
   - Standardizes date formats across filenames (`MM-DD-YYYY`), headers (`MM/DD/YYYY`), and ISO JSON (`MM/DD/YYYY`).
   - Eliminates all Unicode mojibake characters (`â€”`, `â€œ`, `â€`, `â‰ `), standardizing on clean UTF-8 with ASCII punctuation.
   - Clarifies tool exclusion (incidental analogy vs architectural selection).
   - Adds HITL heuristic confidence honesty note.

---

## v5.6

### Focus: Recap Quality Hardening & Actionability Safeguards
Addressed critical recap quality failures identified in real-world retrospective analysis (e.g. passive observations masquerading as work deliverables):

1. **One-Shot Exemplars (H-1)**: BAD vs GOOD action table examples for standard and retrospective meetings embedded in Section 8.
2. **Signal-Density Validation Gate (H-2)**: Post-generation self-check computing expected action rows and triggering transcript re-scans.
3. **Retrospective Meeting Type Guidance (H-3)**: Explicit rules requiring forward-looking meta-actions in retrospectives.
4. **Actions-vs-Findings Cross-Validation Gate (H-4)**: Alignment between Key Findings and Next Steps table.
5. **Tightened Social/Coordination Boundary (H-5)**: Strict exclusion of social check-ins from Next Steps; reframing travel logistics around deliverable sessions.
6. **Owner Attribution Waterfall (H-6)**: 5-level precedence for ownership attribution.

---

## v5.5

### Focus: Single-Path Execution & Architectural Simplification
1. **Single completion path after HITL**: Removed multi-step trigger chains (`GENERATE RECAP`, `GENERATE CANONICAL`).
2. **Downloadable links only**: Prohibited inline artifact dumping.
3. **Three-tier extraction architecture**: Knowledge Graph Spine, Decision Intelligence Signals, Evidence Index.
4. **Negative Exclusion Filter**: Binary pre-filters (Who, Observation, Scope, Durability) before mutation scoring.
5. **Prior Context Protocol (C-16)**: Graph query before synthesis to use `CONFIRM` for known entities and prevent duplicate creation.
6. **Hard Evidence Ceiling (C-17)**: `evidence_only_statement_ids` capped at `max(3, 2x mutations)`.
7. **Idempotent Signal IDs (C-18)**: Deterministic derivation of `signal_id` from `(from_id, to_id, relationship)`.
8. **Graph Lifecycle & Staleness Reaping (Section 9.11)**: Integrated with `tools/graph_gc.py`.

---

## v5.3 / v5.4

### Foundational Governance Contracts
1. Master Constraint Registry (C-01 through C-15).
2. Entity type allowlist aligned with W2 Semantic Spine registry.
3. Metadata passthrough (`classification` and `audiences`).
4. Standardized recording gap protocol.
5. Ambiguity Lock Register with statement ID cross-linking.
