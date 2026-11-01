---
trigger: model_decision
---
# E2E and Edge-Case Coverage Governance Rule

Governs mandatory end-to-end and adversarial edge-case testing coverage for pipeline-touching waves and pull requests.

## Core Contract

Every wave that touches `dual_pipeline/`, `pipeline_a_intake/`, `pipeline_b_extraction/`, `storage/`, or `tests/` **must** include:

1. **At least one E2E test file** (`*_e2e.py` or `tests/end_to_end/`) as a Class B receipt test.
2. **At least one adversarial or edge-case test file** (`*_adversarial*.py`, `*_edge_cases*.py`, or `tests/end_to_end/test_edge_cases_*.py`) in the wave's declared acceptance scope.
3. **E2E CI profile gate**: All declared E2E tests must resolve to an active CI profile via `resolve_all_tests()` (pattern discovery or decentralized manifests under `tests/manifests/*.json`). Unenrolled or unresolvable E2E test files outside quarantine are governance violations.

Waves touching only non-pipeline layers (documentation, receipts, configuration) are exempt — exemption must be declared explicitly in the wave's Acceptance Criteria with the token `E2E-EXEMPT`.

## Mandatory Edge-Case Categories

Every adversarial/edge-case test file added to the repo must cover **at least 3** of the following categories:

| Category | Examples |
| :--- | :--- |
| **Boundary payload** | Empty input, zero-length audio, null candidate packs, 0-byte documents |
| **Malformed input** | Truncated JSON, invalid UTF-8, schema fields with wrong types, missing required keys |
| **Concurrency & race** | Parallel WAL writes, concurrent ring buffer slot access, multi-threaded queue draining |
| **Overflow & exhaustion** | Token budget exhaustion, retry budget depletion, ring buffer wrap-around at capacity |
| **Invariant bypass attempt** | Namespace leakage across tenant boundary, unauthorized UWG writes, fake claim_check digests |
| **Rollback & recovery** | Mid-write crash recovery, non-destructive rollback of graph delta, idempotent re-ingestion |
| **Exact production regression** | Pin a known production failure scenario as a named regression test with exact input/output |

## E2E Test Enrollment Invariant (Decision H-2 / Wave 5)

- Every `tests/*_e2e*.py`, `tests/*_adversarial*.py`, and `tests/end_to_end/*.py` test file **must** resolve under `tools.test_discovery_core.resolve_all_tests()` to `E2E_TESTS`, `DETERMINISTIC_TESTS`, or `BGE_TESTS`.
- Tests are discovered automatically by path pattern (`tests/end_to_end/`, `*_e2e.py`, `*_adversarial*.py`), markers (`@pytest.mark.bge`, `heavy_model`), or decentralized manifests (`tests/manifests/*.json`). Manual edits to `tools/run_ci_gates.py` are retired.
- New E2E or adversarial test files added to `tests/` that fail dynamic resolution or import checks are **blocking governance violations**.
- Exceptions (provider-dependent tests requiring a real LLM model) resolve to `BGE_TESTS` via marker or manifest.
- `tools/audit_testing_gaps.py` must report Dimension 2 (E2E) score ≥ 80% for main promotion to be authorized.

## Class B Receipt Run Requirement

When a pipeline-touching wave declares its Class B receipt run (`Runtime Receipt & Completion Gate`), the pytest command **must include**:
- At least one `*_e2e*.py` or `tests/end_to_end/*.py` test file.
- At least one `*_adversarial*.py` or `*_edge_cases*.py` test file.

Class B receipts produced by unit-test-only runs for pipeline-touching waves are **rejected** as incomplete receipts.

## Decision H-8: Property-Based Verification for New Features (Wave 13)

Under Decision H-8 (resolved by operator approval in Wave 13), the rigid requirement for mandatory static file pairs (`*_e2e.py` + `*_adversarial*.py`) is modernized for new features:
1. **Property Coverage + Lifecycle Test**: For new features, comprehensive Hypothesis property-based testing (`tests/property/` or property tests covering boundary, malformed, and overflow invariants) combined with at least one lifecycle test satisfies the verification requirements.
2. **Legacy Suite Preservation**: Existing legacy suites are preserved without high-churn retroactive rewriting.
3. **Receipt Qualification**: A Class B receipt containing Hypothesis property tests and a lifecycle test satisfies the testing gates.

## Enforcement

- **`pre_tool_call` hook** `nee-e2e-coverage-guard`: Fires when `pytest` or `run_ci_gates.py` commands are executed. Warns and blocks if a pipeline-touching wave receipt run contains no E2E or adversarial test scope.
- **`tools/run_ci_gates.py --profile e2e`**: Runs the full `E2E_TESTS` suite as a dedicated gate profile.
- **`tools/verify_main_readiness.py` Gate 10**: Invokes `audit_testing_gaps.py` and requires E2E dimension score ≥ 80% before main promotion.
- **Pre-push hook** (`.githooks/pre-push`): Runs `verify_main_readiness.py` locally before every push, blocking if any gate fails.
- **PR governance**: PRs touching `dual_pipeline/`, pipeline layers, or `tests/` must have at least one E2E test in the declared wave scope — verified by the reviewing agent before merge authorization.

## Path-Scoped Tier 1 Cached Replay Governance

For all feature branches modifying `dual_pipeline/transcript_*`, `dual_pipeline/canonical_pipeline/`, `pipeline_a_intake/`, `pipeline_b_extraction/`, or transcript synthesis keywords:
1. **Mandatory Post-ASR Replay**: Must execute hermetic Tier 1 cached stage replay (`--resume`) via `tools/verify_tier1_cached_replay.py`.
2. **Four Invariants Verified**:
   - Gate 1.3 canonical date normalization (`**Date:** MM/DD/YYYY`).
   - Cross-worktree metadata path rebinding (foreign worktrees rebound cleanly to active target).
   - X3 quote pointer re-anchoring to verbatim dialogue spans.
   - 100% ephemeral sandbox execution without writes to `output/`.
3. **Zero-Latency Path Scoping**: Branches modifying only unrelated paths (documentation, UI, storage, schemas) bypass the replay gate with 0.00s latency overhead.

## Relationship to Other Rules

- `wave-progression.md`: This rule extends the wave receipt requirement. A wave is `COMPLETE` only if the receipt includes E2E scope when pipeline code was changed.
- `pr-governance.md`: The 32-gate main readiness prerequisite includes Gate 10 (E2E audit score gate).
- `nee-testing-protocol/SKILL.md`: The skill's Class B receipt definition is updated to require E2E coverage for pipeline-touching waves.
