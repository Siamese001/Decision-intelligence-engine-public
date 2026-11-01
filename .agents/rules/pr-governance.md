---
trigger: glob
globs: "{tools/pr_governance*.py,config/pr_*.json}"
---

# PR Governance & Local Main Promotion Rules

Governs feature branch review and promotion into local `main`. Sourced from `governance/LOCAL_PR_PROCESS.md`.

1. **Governed Two-Stage PR Lifecycle**: Direct fast-forwards or unstructured squashes into `main` are prohibited. The lifecycle strictly separates:
   - **Stage A: Branch Alignment & Post-Rebase Readiness** (`python tools/pr_governance.py sync <branch>` or `rebase <branch>`): Rebase onto `main` under lock, establish linear history, and qualify post-rebase readiness.
   - **Stage B: Governed PR Review & Merge** (`python tools/pr_governance.py review <branch>` and `merge <branch>`): Fast-path review (<5s) and governed `--no-ff` merge commit with the `GOVERNED_PR_MERGE` authorization token.
2. **32-Gate Main Readiness Prerequisite**: Branches merging into `main` require a passing receipt (`reports/readiness/local_main_readiness_<slug>_receipt.json`) from `tools/verify_main_readiness.py`, evaluated under proportional risk lanes (Lane 0: 6, Lane 1: 25, Lane 2: 32 gates).
3. **Out-of-Tree Receipt Hygiene**: Readiness receipts (`reports/readiness/local_main_readiness_<slug>_receipt.json`), stage logs, and PR review receipts are git-ignored. Committing readiness receipts into git is prohibited as it alters HEAD and invalidates the cached qualification.
4. **Governed Merge Commit Standard**: Use `--no-ff` with format: `Merge branch '<branch>' into main (<Summary>)`.
5. **Dry-Run Conflict Verification**: Mainline sync is verified before review and merge via `git merge-tree`. Conflicts require clean rebase before merge.
6. **PR Receipts**: Merges produce `reports/pr_merge_<slug>_receipt.json` committed to `main`.

## Strict Worktree & PR Promotion Invariants

1. **Root Workspace Checkout Ban**:
   - The agent is strictly prohibited from running `git checkout <branch>` or `git switch <branch>` in the primary workspace root.
   - All feature development, rebase, and PR merge verification must execute in an isolated worktree under `.worktrees/<slug>/`.

2. **Zero Dirty-Dump Commits**:
   - Never stage or commit bulk unstaged changes using `git commit -a`, `git add -A`, `git add .`, or generic messages ("chore: commit dirty files"). Stage specific authored paths explicitly.
   - Untracked or dirty files outside task scope must be stashed (`git stash`) or ignored, never swept into a PR.

3. **Indivisible Meeting Triad Contract**:
   - Commits touching `output/<project>/<date>/<slug>/` must atomically commit all three deliverables (`01_*.md`, `02_*.md`, `03_*.json`) and their 4 receipts together. Partial triads fail closed.

4. **Production Output Purity**:
   - No file or directory containing `draft`, `tmp`, or `backup` may ever be created or committed under `output/`.

5. **Linear Rebase Only**:
   - Feature branches must rebase onto `main` (`git rebase main` or `python tools/pr_governance.py sync <branch>`). Merging `main` backwards into a feature branch (`git merge main`) is strictly prohibited.

