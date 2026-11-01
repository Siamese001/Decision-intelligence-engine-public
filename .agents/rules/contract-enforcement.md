---
trigger: glob
globs: "contracts/**"
---

# Contract Enforcement Rules

Governs dual-pipeline contract constraints. Sourced from `contracts/dual_pipeline/` and `contract_inventory.json`.

## Compatibility & Canonical Digest Integrity
- **Fail Closed**: Governed artifacts fail closed on unknown fields (`additionalProperties: false`).
- **Versioning**: Breaking changes require major version bump; backward-compatible optional fields require minor; doc fixes require patch.
- **Canonical Digest**: RFC-8259 UTF-8 JSON, sorted keys, separators `,` and `:`, SHA-256. `canonical_digest` field excluded before hashing.

## Contract Inventory & Validation
- **Intake**: copilot, markdown/docx/html/pdf-source, architecture-diagram, batch-admission, a2a-fact-vector-packet.
- **Processing**: normalized-candidate-bundle, ingestion-plan, entity-resolution, assertion-disposition, route-decision.
- **Query & Decision**: query-plan, l2-execution-decision, retrieval-record, connected-evidence-map, ingestion-decision-explanation, hitl-decision.
- **Graph & Telemetry**: graph-change-set, card-materialization-receipt, otel-event-envelope, learning-event, learning-change-proposal.
- **Validation**: `python tools/validate_dual_pipeline_contracts.py` and `python tests/test_dual_pipeline_wave1.py`.

## Privacy & Learning Boundaries
- **Telemetry Privacy**: Forbidden in `otel-event-envelope`: prompt/response text, raw evidence, secrets, meeting content.
- **Learning Proposals**: `learning-change-proposal` requires `status: "proposed"`, `approval_required: true`, `auto_apply: false`.
