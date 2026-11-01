---
trigger: always_on
---

# Git & Worktree Governance Rules

Governs working tree isolation, branch routing, worktree registry integrity, and direct-commit prevention.

## Master Invariant 8: Absolute Direct-to-Main Commit Prevention & Working Tree Integrity

1. **Absolute Direct Commit Ban on 'main'**:
   - Direct commits to the `main` branch are strictly prohibited across all human operators, autonomous agents, subagents, and automated tools.
   - All development, testing, and receipt recording MUST execute within an isolated feature worktree (`.worktrees/<slug>`).

2. **Single Governed PR Merge Exception**:
   - A commit on `main` is permitted ONLY AND STRICTLY within the execution of `tools/pr_governance.py merge <branch> --title "..."`.
   - Such commits MUST possess:
     1. An active `MERGE_HEAD` marker (`git rev-parse -q --verify MERGE_HEAD`) OR `GOVERNED_PR_MERGE=1` in environment.
     2. A sealed `reports/pr_review_<slug>_receipt.json` with `terminal_outcome == "PASS"`.
     3. A commit message conforming strictly to `^Merge branch '<branch>' into main`.
   - Any commit attempt on `main` lacking these markers fails closed immediately with exit code 1.

3. **Immutable Working Tree Configuration (`core.bare` Prohibition)**:
   - Configuring `core.bare = true` in the primary repository or any linked worktree is a critical governance violation.
   - The repository root MUST remain a standard non-bare working tree checked out to `main`.
   - If `core.bare = true` is detected, all git operations MUST fail closed immediately with exit code 1, emitting the mandatory prescriptive remediation command:
     `git config core.bare false`

4. **Worktree Registry Governance (6 Non-Negotiable Invariants)**:
   Every linked worktree must strictly satisfy:
   - `INV-WT-01 (ROOT_ISOLATION)`: Branch `main` is strictly reserved for the primary repository root. Secondary worktrees checking out `main` or named `*main_merge*` are strictly forbidden.
   - `INV-WT-02 (CANONICAL_LOCATION)`: All secondary worktrees must reside directly in `<repo_root>/.worktrees/<slug>`. Arbitrary nesting or unmanaged directories are prohibited.
   - `INV-WT-03 (DETERMINISTIC_SLUG)`: The directory name `<slug>` must exactly equal `slugify_branch(branch)` (or `slugify_branch(branch_basename)`).
   - `INV-WT-04 (CONVENTIONAL_BRANCH)`: Every worktree branch must match `CONVENTIONAL_BRANCH_RE` (e.g. `feat/*`, `enhance/*`, `fix/*`, `ingest/*`, `audio/*`, `transcript/*`, `chore/*`). Names like `main_merge`, `test`, `wip`, or model-branded branches fail closed.
   - `INV-WT-05 (NO_DETACHED_OR_BARE)`: Secondary worktrees must track a named conventional feature branch; detached HEAD and bare worktrees are rejected.
   - `INV-WT-06 (FULL_COVERAGE)`: The live PR queue enforces complete enumeration and fail-closed gap detection across all `.worktrees/` directory entries and git stashes.

5. **Hermetic Git Subshell & Environment Isolation**:
   - All git hooks, test runners, and child subshell executions MUST sanitize their execution environment.
   - The variables `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_COMMON_DIR`, and `GIT_PREFIX` MUST be unset before invoking child git processes unless hermetically targeting an isolated temporary test repository.

6. **Anti-Rationalization & Blocker Escalation Standard**:
   - When encountering git repository errors (`fatal: this operation must be run in a work tree`, git lock files, or unexpected config states), agents MUST NOT invent workarounds, provision unmanaged worktrees (e.g. `.worktrees/main_merge`), checkout detached heads, or patch governance source code to bypass safety checks.
   - Agents MUST halt execution immediately, document the exact error, and escalate to the operator via `ask_user_question`.
