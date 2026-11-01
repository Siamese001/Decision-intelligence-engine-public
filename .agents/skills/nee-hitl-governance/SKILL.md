---
name: nee-hitl-governance
description: Human-in-the-Loop (HITL) atomic decision presentation, SSOT margin evaluation, feedback ledger storage, and prior calibration. Use for operator review queues.
---

# NEE HITL Governance Operations

This skill guides the presentation, evaluation, and calibration of Human-in-the-Loop (HITL) decisions across the ingestion, decision intelligence, and release pipelines.

## When to use

- Formulating or presenting a clarification or decision to the human operator
- Evaluating whether an ambiguity requires human intervention or autonomous resolution (SSOT margin rule)
- Validating operator approval origin before graph mutations
- Managing the A2A review queue or preparing HITL review packets
- Calibrating option confidence based on historical decision ledger entries

## Normative Governance SSOT

All HITL decision presentations, ambiguity thresholds, and approval validations are canonically governed by:
- [`.agents/rules/hitl-decisions.md`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/rules/hitl-decisions.md) (Atomic presentation, 20% margin rule, anti-synthetic origin validation)
- [AGENTS.md §HITL decisions](file:///Users/amitayer/Git/decision-intelligence-engine/AGENTS.md)
- [`dual_pipeline/hitl_governance.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/hitl_governance.py)

This skill provides practical operational templates, speaker-context contracts, and learning-loop commands.

## Presentation Template

When surfacing an operator decision, you MUST use the `ask_user_question` tool. Do not use plain text markdown. Format the tool call as follows:

- `question`: 
  "Decision: [Scope / Title]\n**Context**: [Clear description of the ambiguity in plain English without technical jargon]"
- `options`: 
  - "(Recommended) Option Label (Confidence: HIGH (90%) [82%–95%, n=28]) - Plain summary. Impact: Clear operational outcome or risk."
  - "Alternative Option (Confidence: MEDIUM (70%) [58%–80%, n=14]) - Plain summary. Impact: Clear operational trade-offs."

## Speaker Assignment Evidence Context

- Every human speaker-assignment review must display target transcript turns per the formatting and context rules in [.agents/rules/hitl-decisions.md](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/rules/hitl-decisions.md) §Speaker Assignment Evidence Context.
- Contract specification: [`contracts/dual_pipeline/speaker_utterance.schema.json`](file:///Users/amitayer/Git/decision-intelligence-engine/contracts/dual_pipeline/speaker_utterance.schema.json).

## Closed-Loop System Learning & UWG Parameter Commit

1. **Cohort Analysis & Proposals (`tools/govern_learning_proposals.py`)**:
   - Operator override patterns in `hitl_decision_ledger.json` trigger failure cohorts ($\ge 2$ overrides or $>20\%$ rate).
   - Generates contract-valid `learning-change-proposal:1.0.0` artifacts proposing tuned parameters:
     - `hitl.bayesian_prior_strength` [1.0, 10.0]
     - `hitl.human_feedback_weight` [0.5, 2.0]
     - `hitl.autonomous_feedback_weight` [0.01, 0.20]
     - `hitl.ambiguity_margin_threshold` [0.05, 0.30]

2. **Simulation Gate**:
   - Replays historical decisions; mandates Brier score improvement ($\Delta_{\text{Brier}} < 0$).

3. **Cryptographic Approval & UWG Commit**:
   - Requires Ed25519-signed human approval over RFC 8785 canonical bytes.
   - Commits parameter modifications strictly through the **Universal Write Gateway (UWG)** `learning_parameter_commit` lane.
   - Proves zero graph mutation (`graph_sha256` unchanged, `+0` across all graph tables).
   - Generates dual receipts: `uwg-learning-commit-receipt:1.0.0` and an exact bidirectional rollback receipt.

4. **L4 Stage-Chain Sealing**:
   - Telemetry tracks stages `H0`–`H3` and `LP0`–`LP4`.
   - Stage `L4` generates an immutable Merkle hash chain verified against `learning.sqlite` and `run_seal.json`.

## A2A Review Queue Commands

```bash
# Scan decision ledger for override cohorts and generate proposals
python tools/govern_learning_proposals.py hitl-scan

# Simulate proposal impact on Brier score
python tools/govern_learning_proposals.py simulate <proposal_id>

# Apply approved proposal or rollback via Universal Write Gateway
# See [.agents/rules/system-learning-and-feedback.md](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/rules/system-learning-and-feedback.md) §Universal Write Gateway (UWG) Commit Lane

# Generate A2A HITL review queue
python tools/create_a2a_hitl_review_queue.py

# Finalize HITL review packets after operator resolution
python tools/finalize_a2a_hitl_source_packets.py
```

## Related Files

- Operator Decision Glossary: [`governance/OPERATOR_DECISION_GLOSSARY.md`](file:///Users/amitayer/Git/decision-intelligence-engine/governance/OPERATOR_DECISION_GLOSSARY.md)
- Vocabulary SSOT: [`config/operator_decision_vocabulary.py`](file:///Users/amitayer/Git/decision-intelligence-engine/config/operator_decision_vocabulary.py)
- Tool name SSOT: [`config/hitl_tool_name.py`](file:///Users/amitayer/Git/decision-intelligence-engine/config/hitl_tool_name.py)
- Decision Record Writer: [`dual_pipeline/hitl_governance_core/decision_record_writer.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/hitl_governance_core/decision_record_writer.py)
- Decision Clearance: [`dual_pipeline/hitl_governance_core/decision_clearance.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/hitl_governance_core/decision_clearance.py)
- Layman Clarity: [`dual_pipeline/hitl_governance_core/layman_clarity.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/hitl_governance_core/layman_clarity.py)
- Question Builder & Validator: [`dual_pipeline/hitl_governance_core/question_builder.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/hitl_governance_core/question_builder.py)
- HITL governance: [`hitl_governance.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/hitl_governance.py)
- HITL rule: [`.agents/rules/hitl-decisions.md`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/rules/hitl-decisions.md)
- System learning rule: [`.agents/rules/system-learning-and-feedback.md`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/rules/system-learning-and-feedback.md)
- UWG gateway: [`dual_pipeline/uwg_core/gateway.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/uwg_core/gateway.py)
- Learning engine: [`dual_pipeline/learning_engine.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/learning_engine.py)
- A2A queue builder: [`create_a2a_hitl_review_queue.py`](file:///Users/amitayer/Git/decision-intelligence-engine/tools/create_a2a_hitl_review_queue.py)
