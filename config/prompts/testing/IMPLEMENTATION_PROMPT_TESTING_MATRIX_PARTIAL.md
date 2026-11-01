# Reusable Implementation Prompt Testing Coverage Specification (Partial)
**Identifier:** `PARTIAL-GOV-PROMPT-TESTING-MATRIX-V1`  
**Classification:** Governed Reusable Verification Blueprint  

Every autonomous agent execution prompt, implementation plan, or wave specification in this repository MUST incorporate automated verification across all applicable testing dimensions. This document defines the canonical matrix standard.

---

## 1. MANDATORY PER-WAVE TESTING DIMENSION CHECKLIST

For every wave that introduces or modifies production code, tools, or configuration:

| Dimension | Mandatory Scope & Requirements | Minimum Artifact / Deliverable |
| :--- | :--- | :--- |
| **1. Unit Isolation** | Pure algorithmic logic, input validation, state transitions, and error parsing tested without network, LLM, or filesystem side effects. | `tests/test_<subsystem>_unit.py` |
| **2. Integration** | Inter-module contracts, pipeline handoffs, process exit code propagation, and hybrid database/vector index consistency. | `tests/test_<subsystem>_integration.py` |
| **3. End-to-End (E2E)** | Unbroken lifecycle completion from raw user/stream input to persistent storage and release receipts. | `tests/end_to_end/test_<subsystem>_e2e.py` |
| **4. Adversarial & Edge-Cases** | Explicit simulation of $\ge 3$ edge-case categories (boundary payloads, malformed inputs, concurrency races, overflow/exhaustion, bypass attempts, rollback/recovery, exact production regression pins). | `tests/adversarial/test_<subsystem>_adversarial.py` |
| **5. Regression & Parity** | Differential assertions comparing old vs. new implementations, negative controls proving failure when invariants are broken, and allowlist persistence. | `tests/test_<subsystem>_parity_regression.py` |
| **6. Performance & Latency** | Hardware-bound latency budgets benchmarked with statistical rigor ($N \ge 5$ iterations, median, p95, host fingerprint). | `reports/<subsystem>_benchmark_receipt.json` |
| **7. HITL Decision Gating** | 15% ambiguity margin gate ($\Delta = c_{top} - c_{second} < 15\%$), atomic presentation, calibrated confidence scores, and persistent decision ledger updates. | `tests/test_<subsystem>_hitl.py` |
| **8. Epistemic Security** | Cloud pre-egress credential redaction, tenant isolation boundaries, and career leverage firewall enforcement. | `tests/test_<subsystem>_security.py` |
| **9. Tooling & Gate Parity** | Git hook staged vs. full scan parity, worktree path indirection safety (`git rev-parse --git-path`), and hermetic subshell isolation. | `tests/test_<subsystem>_hook_parity.py` |

---

## 2. CLASS B RECEIPT EXECUTION PROTOCOL

Every wave completion command MUST execute as a Class B receipt run:
1. Runner: `tools/run_tests_with_progress.py`
2. Mandatory timeout: `--timeout=60` per test
3. Output receipt: Structured JSON saved to `reports/<wave_name>_receipt.json` conforming to governed schema
4. CI enrollment: All new E2E and adversarial test files enrolled in `run_ci_gates.py`
