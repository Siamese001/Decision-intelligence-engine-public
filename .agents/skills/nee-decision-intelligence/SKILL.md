---
name: nee-decision-intelligence
description: Decision Intelligence engine, bounded HTTP service, typed framing, option evaluation, and strategic reasoning taxonomy across 18 roles, 6 modalities, and W1-W7 waves.
---

# NEE Decision Intelligence Operations

This skill guides operations for the Decision Intelligence subsystem in `dual_pipeline/`.

## When to use

- User requests strategic decision analysis, option comparison, or trade-off evaluation
- When running or querying the bounded Decision Intelligence HTTP server
- When constructing or validating typed `DecisionFrame` objects
- When executing strategic reasoning wave evaluations (W1 through W7)
- When auditing decision intelligence boundaries or semantic spine conformance

## Architecture Overview

Decision Intelligence operates on top of the knowledge graph while maintaining a strict boundary:
1. **Retrieval Rank Independence**: Retrieval rank is deliberately excluded from option scoring. Options are evaluated strictly against evidence-visible criteria, not search popularity.
2. **Read-Only Authority**: Generated decision artifacts and recommendations are query outputs; they never directly mutate graph truth.
3. **Deterministic Option Resolution**: Recommendations require a unique, evidence-supported position. If two options tie within the ambiguity threshold, the engine flags `SPLIT` or triggers HITL review.

## Bounded HTTP Daemon

Run the Decision Intelligence HTTP service:

```bash
python -m dual_pipeline.decision_api --port 8080 --host 127.0.0.1
```

### Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/route` | Classifies query intent, materiality, and destination |
| `POST` | `/query` | Evaluates typed decision frames against graph evidence |
| `GET` | `/runs/<run_id>` | Retrieves execution trace, decision artifacts, and scores |

### Example Request (`POST /query`)

```json
{
  "question": "Which architecture pattern should we select for real-time telemetry?",
  "options": ["Kafka Event Streaming", "Polled SQLite Sidecar", "Direct REST Ingestion"],
  "criteria": ["low latency", "minimal host CPU impact", "deterministic replay"],
  "constraints": ["must run on edge nodes with < 2GB RAM"],
  "materiality": "high",
  "delivery_profile": "executive"
}
```

## Typed Decision Framing ([`decision_models.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/decision_models.py))

### Key Models

- `DecisionFrame`: Typed container holding question, candidate options, criteria, constraints, assumptions, and time horizon.
- `AssessmentState`:
  - `RECOMMENDED`: A single option decisively satisfies criteria and constraints.
  - `SPLIT`: Multiple options have identical or near-identical evidence support.
  - `INSUFFICIENT_EVIDENCE`: Graph evidence lacks sufficient facts to score criteria.
- `ConfidenceBand`: `HIGH`, `MEDIUM`, `LOW`.

## Strategic Reasoning Taxonomy ([`strategic_reasoning.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/strategic_reasoning.py))

Strategic reasoning annotates assertions without altering graph authority:

### Knowledge Roles (18 Closed Roles)
`current_state_fact`, `official_intent`, `business_problem`, `design_principle`, `target_outcome`, `capability`, `option`, `evaluation_criterion`, `constraint`, `risk`, `dependency`, `external_pattern`, `decision`, `observed_outcome`, `metric`, `source_position`, `assumption`, `open_question`.

### Modalities (6 Closed Values)
`descriptive`, `normative`, `intended`, `proposed`, `predictive`, `observed`.

### Authority Scopes (5 Scopes)
`nee_current_state`, `official_project_intent`, `source_position`, `external_reference`, `stakeholder_perspective`.

### Applicability Scopes
`nee_specific`, `client_specific`, `generalizable`, `analogous_only`.

### Realization States
`not_applicable`, `intended`, `planned`, `in_progress`, `observed`, `measured`.

## Strategic Reasoning Waves (W1–W7)

Execute strategic wave progression:

```bash
# Run end-to-end decision intelligence test
python tools/run_decision_intelligence_e2e.py

# Run Daily Intelligence evaluation (W4)
python tools/run_w4_daily_intelligence_evaluation.py

# Run Temporal Observation evaluation (W3)
python tools/run_w3_temporal_observation_evaluation.py

# Run Strategic Evidence evaluation (W6)
python tools/run_w6_strategic_evidence_evaluation.py
```

## Boundary Auditing

Verify that decision intelligence does not mutate graph state or bypass governance:

```bash
python tools/audit_decision_intelligence_boundaries.py
python tools/audit_decision_intelligence_semantic_spine.py
```

## Related Files

- HTTP API transport: [`decision_api.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/decision_api.py)
- Decision engine: [`decision_engine.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/decision_engine.py)
- Decision models: [`decision_models.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/decision_models.py)
- Strategic reasoning: [`strategic_reasoning.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/strategic_reasoning.py)
- Daily intelligence: [`daily_intelligence.py`](file:///Users/amitayer/Git/decision-intelligence-engine/dual_pipeline/daily_intelligence.py)
