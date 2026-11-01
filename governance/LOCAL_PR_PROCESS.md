# Local PR & Git Governance Process Standard

**Version**: 2.0.0  
**Effective Date**: 2026-09-16  
**Authority**: Repository Master Architectural & Implementation Invariants (`AGENTS.md`)

---

## 1. Overview & Purpose

In this repository, all changes targeting `main` must flow through a formalized, receipt-gated Pull Request (PR) lifecycle. Direct un-audited fast-forward pushes or unstructured squashes into `main` are strictly prohibited.

The PR process guarantees:
1. **Provenance Traceability**: All branch merges preserve explicit commit history using governed `--no-ff` merge commits (`Merge branch '<branch>' into main (<Topic>)`).
2. **Proportional Gatekeeper Parity**: Every candidate branch must prove compliance across its applicable controls from the 30-control governance manifest (`config/governance/gate_manifest.json`) via `tools/verify_main_readiness.py` (Lane 0: 6 controls, Lane 1: 22 controls, Lane 2: 30 controls).
3. **Immutable Verification Receipts**: Every review and merge emits a cryptographic JSON receipt recorded under `reports/`, with branch-scoped receipts preserved under `reports/readiness/`.
4. **Commit-Time Quality**: A native `.githooks/commit-msg` hook enforces Conventional Commits standard at commit time, preventing malformed commits prior to PR review.
5. **Zero-Regression & Main Purity**: Merge receipts are committed directly to `main` and re-verification executes post-merge on `main` to certify zero environmental or side-effect drift. Post-merge coverage audits reuse qualified pre-merge audit packages for identical candidate trees.

---

## 2. The Solo-Developer Git Workflow Lifecycle

Target Flow:
`Feature Branch → Explicit Commit → Stage A: Sync (Rebase + Readiness) → Stage B: Governed Review & Merge → Local Main → Origin Main`

```
+-------------------------------------------------------------------------+
| Step 1: Feature Branch & Isolated Worktree                              |
|         (python tools/pr_governance.py start feat/<name>)               |
+-------------------------------------------------------------------------+
                                    |
                                    v
+-------------------------------------------------------------------------+
| Step 2: Explicit Staging & Fast Pre-Commit Quality Gatekeeper (<2s)     |
|         (git add <specific_files> -> git commit)                        |
|         * Strict prohibition: NEVER use git add -A or git add .         |
+-------------------------------------------------------------------------+
                                    |
                                    v
+-------------------------------------------------------------------------+
| Step 3: Stage A: Mainline Sync & Post-Rebase Readiness Qualification   |
|         (python tools/pr_governance.py sync <branch>)                   |
|         - Serialized rebase onto latest main under queue lock           |
|         - Linear history verified (behind == 0)                         |
|         - Out-of-tree 32-gate readiness qualification (.gitignore)      |
+-------------------------------------------------------------------------+
                                    |
                                    v
+-------------------------------------------------------------------------+
| Step 4: Stage B: Streamlined Pre-Merge Validation & Governed Merge      |
|         (python tools/pr_governance.py merge <branch> --title "...")    |
|         - Verifies mainline sync, conventional commits, diff syntax     |
|         - Reuses post-rebase readiness attestation in <5s               |
|         - Governed --no-ff merge into local main + post-merge cert      |
|         (Optional: `pr_governance.py review <branch>` for dry-run audit)|
+-------------------------------------------------------------------------+
                                    |
                                    v
+-------------------------------------------------------------------------+
| Step 5: Local Main & Worktree Teardown                                  |
|         (python tools/pr_governance.py finish <branch>)                 |
+-------------------------------------------------------------------------+
                                    |
                                    v
+-------------------------------------------------------------------------+
| Step 6: Origin Main Backup (Lightweight Pre-Push Gate, <0.3s)           |
|         (git push origin main -> .githooks/pre-push validates receipt)  |
+-------------------------------------------------------------------------+
```

### Phase 1: Local Feature Branch Creation & Commit-Time Quality
To create a clean feature branch conforming to conventional naming:
```bash
python tools/pr_governance.py create feat/my-feature-topic
```
Permitted prefixes: `feat/*`, `feature/*`, `fix/*`, `chore/*`, `docs/*`, `refactor/*`, `perf/*`, `test/*`, `ci/*`.

At commit time, `.githooks/commit-msg` invokes `tools/lint_commit_message.py` to validate:
`<type>(<scope>): <subject>`

### Phase 2: Tier 1 Pre-Commit Structural Legality (7 Categories)
During commit, `tools/verify_commit.py --staged` verifies all 7 canonical categories in <10 seconds:
1. Standard Code Hygiene (Syntax & AST Compilation)
2. Architectural & Layer Governance (file budgets, unidirectional DAG, model-neutral naming, ast invariants, blast radius)
3. Production Purity & Ingestion Protection (derivation integrity, anti-custom scripts)
4. Code Quality & Cognitive Complexity (cognitive complexity, strict typing, hardcoded parameters)
5. Contract, Schema & Hierarchy Integrity (dual pipeline contracts, implementation plan, output hierarchy)
6. Workspace Skills & MCP Budgets (MCP schema overhead, skills budget)
7. Secrets, Privacy & Runtime Controls (credential scanning, career firewall, synthetic embedding ban, runtime evidence)

### Phase 3: Stage A Mainline Sync & Post-Rebase Readiness (`pr_governance.py sync`)
Before submitting a PR, synchronize the branch with `main` and certify post-rebase readiness:
```bash
python tools/pr_governance.py sync <branch-name>
```
This executes the three essential Stage A actions:
1. Verifies worktree cleanliness (rejects uncommitted edits).
2. Performs serialized rebase onto `main` under the queue lock, aborting cleanly if conflicts exist.
3. Runs authoritative post-rebase `verify_main_readiness.py`, writing immutable out-of-tree readiness receipts and DSSE attestations.

Readiness receipts and logs are tracked in `.gitignore` and never committed into git, ensuring `HEAD` SHA and tree digests remain stable.

### Phase 4: Stage B Pre-Merge Review Audit
To validate a candidate branch before merging without modifying `main`:
```bash
python tools/pr_governance.py review <branch-name>
```
The command audits:
1. Conventional Commits across the branch revision range (`main..<branch>`).
2. Mainline sync (`behind == 0`) and dry-run conflict-free merge tree (`git merge-tree`).
3. Python/JSON diff syntax checks and touched file count.
4. Fast-path reuse of post-rebase readiness attestation in <5s without re-running gates.

### Phase 5: Streamlined Pre-Merge & Governed Merge Execution
Execute the streamlined pre-merge and merge into `main` in one command:
```bash
python tools/pr_governance.py merge <branch-name> --title "feat(scope): <Description>"
```
The merge engine:
1. Automatically executes pre-merge validation (reusing fresh cached review receipt if already run).
2. Verifies clean mergeability and checks out `main`.
3. Performs governed `--no-ff` merge adhering to `Merge branch '<branch>' into main (<Topic>)`.
4. Emits `reports/pr_merge_<branch_slug>_receipt.json` and commits it to `main`, ensuring `main` remains 100% clean.
5. Executes post-merge structural verification (`tools/verify_post_merge.py`).
6. Automatically cleans up the merged feature worktree / branch (`--delete-branch`).

---

## 3. Environment Health & Doctor Audit

Verify your local git governance environment at any time:
```bash
python tools/pr_governance.py doctor
```
Audits:
- `core.hooksPath` set to `.githooks`
- Executable status of all 6 hooks (`pre-commit`, `commit-msg`, `pre-merge-commit`, `pre-push`, `post-checkout`, `post-merge`)
- Python virtual environment availability
- Critical gatekeeper tools presence

---

## 4. Conflict Resolution & Rebase Protocol

If `tools/pr_governance.py review <branch>` detects merge conflicts:
1. Rebase the feature branch onto local `main`:
   ```bash
   git -C .worktrees/<slug> rebase main
   ```
2. Resolve conflicts while preserving unidirectional architectural dependencies.
3. Re-run `python tools/verify_main_readiness.py`.
4. Re-run `python tools/pr_governance.py review <branch>`.

*(Remote operations via `git fetch origin` remain optional for sole-developer workflows and are never required on the critical promotion path).*
