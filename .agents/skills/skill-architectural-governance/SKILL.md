---
name: skill-architectural-governance
description: Architectural integrity audits, AST layer dependency verification, WAL storage concurrency stress tests, provider fault injection, and worktree hygiene sweeps.
---

# Architectural Governance Skill

This skill performs unified architectural integrity audits, storage concurrency safety verification, LLM provider fault resilience testing, and workspace hygiene sweeps. Grounded in the 60-PR Forensic Audit.

## When to use

- Before committing any feature, refactor, or new pipeline module
- When reviewing pull requests for architectural compliance or running pre-merge audits
- After modifying storage, semantic cache, or provider adapters
- When inspecting linked git worktrees for untracked or dangling test files

---

## 1. Unidirectional Layer Dependency & Complexity Audit

Executes AST inspection and file budget linters to enforce modularity and unidirectional imports:

```bash
# Verify unidirectional L0-L6 layer imports
python tools/lint_layer_imports.py --staged

# Verify 400-line module budgets and cognitive complexity <= 15
python tools/lint_file_budgets.py --staged --max-lines 400 --max-complexity 10

# Scan for direct os.environ API secret lookups outside SSOT managers
python tools/scan_credential_leaks.py --staged
```

- **Invariants**: Lower layers (L0 Storage, L1 Normalization) must never import from higher layers. Enforced by [`.agents/rules/layer-architectural-integrity.md`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/rules/layer-architectural-integrity.md).
- **Credentials**: Secrets resolved strictly via `storage.credentials.CredentialManager`. Enforced by [`.agents/rules/credential-isolation-and-egress-sanitization.md`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/rules/credential-isolation-and-egress-sanitization.md).

---

## 2. Storage Concurrency Stress Testing

Executes multi-process and multi-threaded stress verification to certify zero lock contention under parallel execution:

```bash
python -m storage.stress_harness \
  --workers 16 \
  --duration-seconds 10 \
  --target-db-mode wal \
  --verify-integrity
```

- **Verification Criteria**:
  - Zero `sqlite3.OperationalError: database is locked` exceptions across all 16 workers.
  - 100% committed transactions validated via cryptographic hash checks.
  - Database file passes `PRAGMA integrity_check;` with `ok`.
- **Invariants**: Enforced by [`.agents/rules/concurrency-and-storage-safety.md`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/rules/concurrency-and-storage-safety.md).

---

## 3. Provider Fault Injection (L2 Healing Mesh)

Executes chaos testing against external LLM gateway integrations to verify fallback cascades:

```bash
python -m evals.run_provider_fault_injection \
  --scenarios FAULT-429,FAULT-503,FAULT-TRUNC,FAULT-TIMEOUT \
  --pipeline pipeline_b_extraction \
  --verify-mesh-receipt
```

- **Scenarios**:
  - `FAULT-429`: Exponential backoff retries 3 times, then cascades to secondary provider tier.
  - `FAULT-503`: Immediate fallback to semantic cache or local heuristic parser.
  - `FAULT-TRUNC`: Partial-JSON repair restores valid parse tree; unclosed records dropped gracefully.
  - `FAULT-TIMEOUT`: Hard timeout terminates at <= 46s without hanging orchestrator.

---

## 4. Git Worktree & Runtime Hygiene Sweep

Enforces runtime evidence binding and sweeps dangling test artifacts:

```bash
# Sweep uncommitted .tmp files and orphaned test artifacts
python tools/audit_worktree_hygiene.py --clean-dangling-tmp

# Verify active pair and runtime evidence inventory
python -m dual_pipeline.runtime_evidence inventory
```

- **Invariants**: Enforced by [`.agents/rules/git-worktree-governance.md`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/rules/git-worktree-governance.md) and [`.agents/rules/production-purity.md`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/rules/production-purity.md).

---

## Related Files

- Layer linter: [`tools/lint_layer_imports.py`](file:///Users/amitayer/Git/decision-intelligence-engine/tools/lint_layer_imports.py)
- Budget linter: [`tools/lint_file_budgets.py`](file:///Users/amitayer/Git/decision-intelligence-engine/tools/lint_file_budgets.py)
- Credential scanner: [`tools/scan_credential_leaks.py`](file:///Users/amitayer/Git/decision-intelligence-engine/tools/scan_credential_leaks.py)
- Concurrency harness: [`storage/stress_harness.py`](file:///Users/amitayer/Git/decision-intelligence-engine/storage/stress_harness.py)
- Fault injector: [`evals/run_provider_fault_injection.py`](file:///Users/amitayer/Git/decision-intelligence-engine/evals/run_provider_fault_injection.py)
- Hygiene auditor: [`tools/audit_worktree_hygiene.py`](file:///Users/amitayer/Git/decision-intelligence-engine/tools/audit_worktree_hygiene.py)
