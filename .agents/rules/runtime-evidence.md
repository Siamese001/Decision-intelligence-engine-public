---
trigger: glob
globs: "{dual_pipeline/runtime_evidence_core/**,dual_pipeline/runtime_evidence.py}"
---

# Runtime and Retrieval Evidence Rules

Governs claims about embedding backends, runtime state, and retrieval evidence.

## Evidence Inventory & Scoping
- **Evidence Guard Compliance**: Comply with the canonical evidence guard in [AGENTS.md](../../AGENTS.md); run inventory before stating status.
- **Explicit Scoping**: Bound `active_pair.json` to its explicit runtime root; follow pointers in receipts and include deployment-owned roots.
- **Absence Claims**: Pattern search misses never prove absence; report only as unobserved in explicitly searched scope.
- **Authorization Boundary**: Unqualified production status is an authorization boundary, not proof of inactivity.

## Retrieval Evaluations & Claim Commands
- **Retrieval Evaluations**: Sealed evaluations in `<runtime_root>/retrieval_evaluations/` constitute evidence under the 7,500ms latency gate.
- **Governed Claims Protocol**: Execute positive and negative claims using `dual_pipeline.runtime_evidence` per AGENTS.md contracts.
- **Telemetry Reporting**: Capture runtime root, active generation, backend, model identifier, run identifiers, and evidence paths.

## Grounded Delivery & Claim Sourcing Invariants
- **Confirmed Assertion Grounding**: Factual delivery claims must bind to confirmed assertions in active GraphDB snapshots.
- **Deterministic Text Metrics**: Enforce strict word count boundaries (< 500 words) and persona title verification.
- **Post-Verification Immutability**: Prohibit modifying payload content between verification and release sink dispatch.
- **Safe Failure Contract**: Blocked releases suppress raw draft text while providing transparent diagnostic reason codes.
