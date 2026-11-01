# Event and learning-ledger definitions

Initiative: `NEE-KG-DUAL-PIPELINE-01`  
Contract version: `1.0.0`

OpenTelemetry and the durable learning ledger are complementary. Telemetry
records timing, topology, counts, identifiers, hashes, route, lane, status,
model, and generation metadata. The durable ledger records reconstructable
decisions and references governed payloads by locator and digest. Neither
contract permits raw meeting content in telemetry attributes.

## OpenTelemetry envelope

The `otel-event-envelope` contract permits only these attributes:

| Attribute | Purpose |
|---|---|
| `run_id` | Correlate every span in one run |
| `use_case` | `intake`, `query`, or `shared` |
| `stage` | U0 through L4 stage identity |
| `route` | Deterministically selected route, when known |
| `lane` | Approved, candidate, or external-public lane, when applicable |
| `decision_status` | Outcome without raw decision content |
| `model_id` | Model identifier, never prompt or response text |
| `generation_id` | Immutable index/model generation identifier |
| `graph_snapshot` | Exact graph binding |
| `input_digest`, `output_digest` | Replay-safe content bindings |
| `record_count`, `retry_count` | Operational counts |

Conditional-L2 events may additionally carry the closed safe allowlist of
query type, materiality, L2 policy and reason, model selection, egress posture,
evidence counts, call purpose and ordinal, token and estimated-cost counts,
latency, response digest, disposition, and bounded call counters. Prompts,
responses, raw evidence, and secrets remain forbidden as attributes.

Unknown attributes fail closed. Sensitive or raw content keys are rejected by
both schema and semantic privacy checks.

## Durable ledger envelope

Every `learning-event` requires:

- event, trace, span, and run identifiers;
- use case and stage;
- exact event type;
- policy, graph, model, or index bindings where applicable;
- input and output digests;
- status and timestamp;
- a complete correlation object with nullable source, candidate, query,
  graph-operation, and HITL identifiers;
- a governed payload reference and payload digest.

At least one domain correlation identifier must be populated. Candidate,
query, graph-operation, and HITL event families additionally require their
corresponding identifier.

## Event type inventory

| Family | Event types |
|---|---|
| Intake | `intake_run`, `source_record`, `candidate_record`, `entity_resolution_candidate`, `entity_resolution_decision`, `assertion_disposition`, `external_validation_result`, `hitl_review_item`, `hitl_decision`, `graph_change_set`, `graph_change_operation`, `graph_write_receipt`, `embedding_generation_receipt` |
| Query | `query_classification`, `query_plan`, `route_decision`, `retrieval_event`, `retrieved_record`, `graph_path`, `evidence_packet`, `l2_execution_decision`, `prompt_event`, `l2_primary_call`, `l2_schema_repair`, `l2_quality_escalation`, `l2_provider_failure`, `l2_output_selection`, `reasoning_event`, `exit_gate_event`, `user_feedback`, `downstream_outcome` |
| Shared | `pipeline_event`, `learning_change_proposal`, `policy_change_approval`, `evaluation_result`, `release_receipt` |

## Learning-change boundary

`learning-change-proposal` may propose an alias, extraction template,
authority policy, threshold, prompt, route, retrieval weight, workflow, or
benchmark case. Its status is always `proposed`, `approval_required` is always
`true`, and `auto_apply` is always `false`. Runtime self-modification is outside
the contract and fails validation.
