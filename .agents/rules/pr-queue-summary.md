---
trigger: model_decision
description: Governs how the PR Queue and branch summary tables are rendered when requested
---

# PR Queue / Branch Summary Rendering Rules

Governs how the PR Queue / Branch Summary table is generated when the user requests it (e.g., "PR Queue", "Branch Summary", "refresh table", or similar).

---

## 0. Pre-Queue Consolidation & Deprecation Sweep (Mandatory)

Before generating the queue, the agent MUST run a topological audit via `python tools/pr_topology_auditor.py`:
1. **Superseded Branch Tombstoning**:
   - Detect branches that have been completely superseded by newer, cleaner implementations.
   - Immediately mark as `🚫 Superseded (Tombstone)` and exclude from active merge contention.
2. **Lineage Ancestry Untangling**:
   - Check if a candidate branch was branched from an unmerged parent branch rather than `main`.
   - Block child branches until parent is merged or cleanly re-anchored.
3. **Consolidation Recommendation Engine**:
   - **GraphDB Schema Atomicity**: Any branches targeting the same GraphDB schema *MUST* be flagged for mandatory consolidation into a single atomic PR to prevent state invalidation, race conditions, and hash chain destruction.
   - **Dependency Deadlock Breakers**: Actively recommend consolidating branches with tightly coupled APIs or overlapping domains to restore full end-to-end verifiable transaction boundaries and eliminate CI overhead taxes.
   - **Live Meeting Stream De-Duplication**: Actively scan branch names to determine if multiple branches represent different audio streams or attendees from the exact same live session. These MUST be recommended for consolidation into a single, unified meeting PR to prevent duplicate graph topologies.
   - **Blast Radius Limit**: Consolidation must not be used to dodge QA gates. Ensure consolidated PRs remain cohesive to prevent bloated mega-PRs that inflate deployment risk and make forensic debugging impossible.
4. **The 4-Train Consolidation Architecture**:
   - *Train 1: Core Economic & Fast-Lane Core* (Consolidate standalone P0 singleton optimizations).
   - *Train 2: Platform Velocity Pack* (Consolidate related platform changes into 1 qualification pass).
   - *Train 3: Ingress & Attribution Hardening* (Core pipeline & HITL features).
   - *Train 4: Client Deliverables Train (Data Highway)* (Batch all sealed `output/` meeting deliverables into 1 atomic commit).
5. **AST Semantic Merge Driver**:
   - Standard 3-way conflicts on append-only structures (`constants.py`, `project_registry.json`) are auto-resolved via `tools/semantic_merge_driver.py`.
6. **Mandatory Dirty Gate, Merge Proof & Commit-First Invariant (Anti-Data-Loss Standard)**:
   - **Strict Prune Precondition**: A branch with `ahead == 0` MUST NEVER be recommended for `[PRUNE]` or placed into decommission tier `D` if its worktree contains unabsorbed authored modifications (`.py`, `.md`, config files, or untracked `??` deliverables) or active stash entries (`git stash list`), regardless of `behind` count.
   - **Untracked Worktree Diff Inspection**: Because `git diff main...<branch>` is tautologically empty when `ahead == 0`, `evaluate_dirty_file_coverage()` MUST verify target path existence in `main` (`get_target_tree_map`) and inspect untracked disk contents; empty `git diff main -- <untracked_path>` stdout MUST NEVER be treated as hunk absorption.
   - **Merge Proof vs Zero-Commit Ancestors (`GENUINELY_MERGED` vs `ZERO_COMMIT_ANCESTOR`)**: Because `is_ancestor(branch, main)` is tautologically true whenever `ahead == 0`, an `ahead == 0` branch MUST NEVER be labeled `"Absorbed into main"` without a sealed `pr_merge_<slug>_receipt.json` or second-parent merge commit on `main`. Clean `ZERO_COMMIT_ANCESTOR` branches are pruned strictly as `"No unique commits vs main, abandoned zero-commit branch"`.
   - **Disposition Action**: If unabsorbed authored files exist, the disposition engine MUST emit `[COMMIT_FIRST] ahead==0 but N uncommitted authored file(s); commit before prune` and preserve the branch's domain priority tier.
   - **Open Wave Invariant**: A branch with `ahead == 0` that has active open waves in `implementation_plan.md` MUST NEVER be marked `[PRUNE]`; the disposition engine MUST emit `[KEEP] Active wave in flight (Wave N open)`.
   - **Zero Hallucinated Recommendations**: Assistants MUST NEVER invent hand-crafted next actions or advice in chat prose; Next Action strings MUST be rendered verbatim from the live output of `compute_dispositions()`.
7. **Mandatory Worktree & Stash Coverage Invariant (INV-WT-COVERAGE)**:
   - Every physical subdirectory under `.worktrees/` and every git stash entry (`stash@{N}`) MUST appear in the PR queue or be accounted for in the queue summary footer (`Worktree coverage: N/N children · M/M stashes`).
   - If an unlisted worktree folder or stash is detected by `scan_all_worktrees()`, `cmd_list` MUST fail closed with `WORKTREE_COVERAGE_GAP <name>` instead of emitting an incomplete queue.
   - Orphan folders and stashes are assigned to group `W` (`W.1`, `W.2`, …) sorting after P-levels and before decommission `D` / archive `A`.
8. **Zero Disjunctive Disposition Invariant (INV-ZERO-DISJUNCTIVE-ADVICE)**:
   - Assistants and queue engines MUST NEVER output compound or disjunctive recommendations (*"rebase or archive"*, *"prune or merge"*, *"X or Y"*).
   - The disposition MUST resolve to a single concrete, decisive verb (`[PRUNE]`, `[ARCHIVE]`, `[REBASE]`, `[MERGE]`, `[COMMIT_FIRST]`).
   - If empirical evidence does not establish a clear dominant choice (confidence margin $\Delta \le 20\%$), the assistant MUST NOT guess or hedge in prose; it MUST immediately trigger `ask_user_question` with structured, confidence-calibrated options per `hitl-decisions.md`.

---

## 1. Priority Ordering (Mandatory)

Assign a **P-level** (P0, P1, P2, P3) strictly following the SSOT rubric in `config/pr_priority_rubric.json`.
**Priority must be re-derived from first principles every time the queue is generated**, based on the branch's net diff vs `main`, commit messages, and wave plan. The registry in `config/pr_priority_registry.json` is for time-boxed overrides only.

### Ordering Philosophy: Maximize Throughput, Minimize Refactoring Risk
The goal is to merge the most value with the least risk of introducing regressions or requiring post-merge refactoring. Cost savings and performance improvements are always highest priority. Within any P-level, branches that are closer to a clean merge are preferred over those requiring significant rebase or conflict resolution work.

- **Multi-Tier Rule**: When a branch spans multiple domains, assign it to the **highest-priority tier** whose core invariants it actively satisfies (Highest-Tier Dominance). Note secondary tiers in the Domain column.
- **Risk-Adjusted Intra-P Sub-Ordering**: Within the same P-level, rank branches by merge risk (lowest risk first):
  1. **Ready Now** — All waves complete, clean merge, clean worktree, `behind ≤ 5`. Near-zero merge risk.
  2. **Low Risk** — Clean merge, `behind ≤ 10`, worktree dirty only from active wave work. Rebase is trivial.
  3. **Moderate Risk** — Clean merge but `behind > 10`, or dirty worktree with significant uncommitted changes. Rebase may require manual conflict resolution.
  4. **High Risk** — Merge conflicts detected, or `behind ≥ 20`. Rebase carries refactoring risk; defer until lower-risk branches in the same P-level are cleared.
- **Fast-Lane Merge Elevation**: Any branch where all waves are complete (`Open Waves = —`), merge is CLEAN, and worktree is clean gets a `🚀 Fast-Lane` annotation in the Readiness column and sorts to the top of its P-level regardless of other factors. Rationale: a done, conflict-free branch has zero refactoring risk and every hour it sits unmerged is pure queue waste.
- **Divergence Penalty**: Branches with `behind ≥ 20` are sunk to the bottom of their P-level. They carry high refactoring risk during rebase and should not block lower-risk branches from merging first, even if they are nominally higher-tier within the same P-level.
- **Ordering Contract (enforced by `tools/pr_governance_core/queue_order.py` and `tools/pr_governance_core/completion_state.py`)**: rows sort by P-level first (P0 → P1 → P2 → P3), then worktree/stash coverage rows (`W.1`, `W.2`, …), then decommission rows (`D`, `TOMBSTONE`), then backup snapshots (`[ARCHIVE]`) as `A.1`, `A.2` — archive rows hold no P-level and are never merge candidates. Within every P-level (`P0`–`P3`), **Completed (`COMPLETED`, `completion_rank = 0`) branches strictly sort ahead of In-Progress (`IN_PROGRESS`, `completion_rank = 1`) branches** so completed, merge-ready work is never obscured behind dirty worktrees, active git locks, active worktree PIDs, or open waves. `[COMMIT_FIRST]` rows keep their domain P-level (derived from the uncommitted paths) and sort within the In-Progress tier; tier and Next Action come from the same disposition decision (`tools/pr_governance_core/queue_reconcile.py`). Risk only reorders rows *within* a P-level and completion tier and never moves a row across P-levels. Sub-ranks (`P0.1`, `P0.2`, …) are assigned after sorting so they always read top to bottom.
- **Completed vs. In-Progress Separation Contract (`tools/pr_governance_core/completion_state.py`)**:
  - `COMPLETED` (`Code Completed? = Y`, `is_completed = True`, `is_in_progress = False`): Branch has `ahead > 0`, clean worktree (`dirty == False`), zero git worktree lock files (`index.lock`, `MERGE_HEAD`, `REBASE_HEAD`, `CHERRY_PICK_HEAD`, `rebase-merge`, `rebase-apply`), zero active OS processes bound to the worktree, `open_waves == "—"`, and zero uncommitted authored files. Its `Immediate Blocker / Action` cell is explicitly prefixed with `[COMPLETED — READY]`.
  - `IN_PROGRESS` (`Code Completed? = N`, `is_completed = False`, `is_in_progress = True`): Branch has any active authoring signal (`dirty == True`, active git lock, active worktree PIDs, `open_waves != "—"`, `ahead == 0` with uncommitted work, or `Complete In-Flight Work`). Its `Immediate Blocker / Action` cell is explicitly prefixed with `[IN PROGRESS — <reason>]` (e.g. `[IN PROGRESS — 2 dirty file(s), open waves (1/2)]`).
  - `DECOMMISSION` (`Code Completed? = N/A`, `is_completed = False`, `is_in_progress = False`): Fully absorbed, superseded tombstone, or clean zero-commit ancestor (`Decommission (No Value)`).
  - Both `reports/pr_queue_detail.json` and `decisions.json` emit top-level `completed_branch_count`, `in_progress_branch_count`, `decommission_branch_count`, `completed_branches`, `in_progress_branches`, and `decommission_branches`, and the executive footer prints `Completed & ready branches: N · In-progress branches: M · Decommission: K`.
- **Next Action Vocabulary (enforced by `tools/pr_governance_core/queue_actions.py`)**: the generic text "Active branch with unique work" is forbidden in the rendered table. Every row states one concrete step: `[RESOLVE_CONFLICTS]` (names the conflicting files), `[REBASE]`, `[CONTINUE_WAVES]`, `[COMMIT_FIRST]`, `[REVIEW]`, `[CONFIRM_TOMBSTONE]` (older doc-only iteration of the same plan file; names the newer sibling), `[ARCHIVE]` (backup snapshot), worktree/stash actions (`[RECOVER_DELIVERABLES]`, `[QUARANTINE_AUDIO]`, `[QUARANTINE_STUB]`, `[REPAIR_REGISTRATION]`, `[TRIAGE_STASH]`), plus the disposition actions `[PRUNE]`, `[TOMBSTONE]`, `[KEEP] Active wave in flight`. Versioned siblings (`-v2`, `-v3`) are never auto-consolidated; the operator confirms which one survives.

---

## 2. Table Format (Mandatory)

### Dual-Mode Observer & CLI Contract (Schema 4.0.0)

The scheduled PR Queue Observer, its on-demand interactive mode, and `tools/pr_governance.py list` share the single unified 10-column Executive Candidate Queue contract defined in `config/scheduled_tasks/pr_queue_output_contract.json` (schema 4.0.0).

The output **must** be rendered as a single unified GitHub-flavored markdown table sorted strictly by priority. Do not use distinct tables grouped by Topological Train/Cohort or separate legacy compact/decision layouts. Inactive session stubs and git stashes are collapsed into a single summary row, and 5-Agent Council deliberation rationalizes merge sequencing. Unknown Git or review facts block eligibility.

### Required 10-Column Table Schema
`| Priority | Branch | Category | Complexity | Code Completed? | Rebase Required? | PR to Main Required? | Graph Mutated (Y/N) | Recommended Disposition | Immediate Blocker / Action |`

### Column Definitions

- **Priority**: Sub-ranked tiers derived via Rubric v2 (`P0.1`, `P0.2`, `P1.1`, `P2.1`) from real 3-dot numstat and commit evidence. Decommission candidates (`Decommission (No Value)`) are placed at the bottom using `D.1`, `D.2`.
- **Branch**: Hyperlinked branch name with clickable worktree `file://` link when checked out locally. The Column 2 branch cell and separator are anchored with min-width styling (`<span style="display:inline-block;min-width:240px">` and `:----------------------------------:|`) to reserve at least 240px and prevent multi-line hyphen collapse in chat and markdown viewports.
- **Category**: High-signal category describing what the branch changes (`Live Meeting`, `Client Document`, `External Research`, `Decision Intelligence`, `Architecture`, `Knowledge Graph`, `Ingestion`, `Performance`, `Governance`, `Testing`, `Housekeeping`), resolved via `config/pr_domain_taxonomy.json`.
- **Complexity**: Refactoring complexity sizing (`XS`, `S`, `M`, `L`, `XL`) derived from 3-dot numstat and AST scope.
- **Code Completed? (Y/N/N/A)**:
  - `Y`: Working tree is clean (`dirty == False`), open waves are complete (`open_waves == "—"`), and Class B readiness receipt passes.
  - `N`: Active authoring (`dirty == True`), open waves in flight, or missing test receipts.
  - `N/A`: Decommission stubs, tombstoned branches, or stashes.
- **Rebase Required? (Y/N/N/A)**:
  - `N`: Branch tip is directly anchored to `main` HEAD (`behind == 0`). Stage 2 complete, ready for Stage 3 governed merge.
  - `Y`: Branch is behind `main` (`behind > 0`). Under `_execute_merge_under_lock()`, any branch with `behind > 0` fails closed (`base_ancestor != main_base_sha`). It MUST be rebased onto `main` top before merge qualification.
  - `N/A`: Decommission/prune candidates.
- **PR to Main Required? (Y/Y (Partial)/N)**:
  - `Y`: Branch contains high-value unique code destined for `main`.
  - `Y (Partial)`: Branch is largely superseded, but 1–K specific files hold unique logic to salvage.
  - `N`: Branch is fully absorbed, empty, or abandoned—no merge to `main` needed.
- **Graph Mutated (Y/N)**:
  - `Y`: Branch or worktree has authoritative receipt or database evidence of mutating the Knowledge Graph (`backtest_promotion_receipt.json` `PROMOTED`, `mutation_approval_receipt.json` `ACTIVATED`, `publication_coverage_receipt.json` `activated_generation_id`, or `graph_mutation_breakdown.md` `Authoritative Publication & Activation`).
  - `N`: Code-only, unmutated pre-mutation diagnostic scan (`PACKAGE_READY`), or transcript-only branch with zero graph mutations.
- **Recommended Disposition (5 Value Statuses)**:
  1. `Promote to Main`: High-value unique code; ready to merge (or rebase then merge).
  2. `Complete In-Flight Work`: Valuable branch, but has dirty files, open waves, or missing test receipts.
  3. `Salvage Subset & Archive`: Branch mostly superseded by `main`, but specific files merit cherry-picking.
  4. `Hold / Blocked`: Valuable work blocked by governance gate or unmerged parent branch.
  5. `Decommission (No Value)`: Superseded by `main`, zero diff delta, or abandoned stub — safe to prune.
- **Immediate Blocker / Action**: Concrete technical blocker, exact conflicting/overlapping file paths, or specific next command without meta-sorting noise.

### Mandatory Single-Row Collapse Invariant
Session stubs and git stashes MUST NOT be enumerated across individual rows. All inactive session stubs and git stashes must be collapsed into exactly one summary row:
`| — | [N session stub(s) & M git stash(es) collapsed](file://<path_to_detail_artifact>) | Housekeeping | — | N/A | N | N | N | Quarantine / Triage | Sealed in reports/pr_queue_detail.json |`

### Conversational Summary Invariant (No Silent Field Dropping)
Any conversational turn summarizing or presenting the PR queue to an operator must preserve the essential decision fields:
1. **Priority & Sub-Rank** (P0.1, P1.1, etc.)
2. **Branch & Category** (linked to worktree if local)
3. **Stage Gates** (Code Completed?, Rebase Required?, PR to Main Required?, Graph Mutated (Y/N))
4. **Recommended Disposition & Immediate Blocker / Action**
5. **Linked Detail Artifact** (`reports/pr_queue_detail.json`)

### Linked Detail Artifact Contract (Zero Broken Links)
Every link emitted in the queue (such as collapsed stashes or the coverage footer) MUST reference an artifact that actually exists on disk:
- Canonical path: `reports/pr_queue_detail.json`
- Contents: complete wave progress, review verdicts, sync status (ahead/behind), full worktree classification, and all collapsed git stashes with stable commit identities (`head_sha`, `parent_sha`, `classification`, `subject`, `date`).
- Zero Hallucinated Links: Emitting links to non-existent JSON receipts is a governance violation; the renderer guarantees the detail artifact exists before emitting the link.

### Optional Advisory LLM Summary
When `--llm-summary` is passed to `list` or `review`, the tool emits an advisory natural-language intent summary:
- Off by default.
- Redacts all secrets and credentials before LLM egress.
- Enforces anti-hallucination citation filter: any file path cited by the LLM that is not in the branch diff is dropped.
- Strictly advisory: NEVER affects dispositions, review verdicts, readiness, or priority tiers.

### Recently Merged Section
Append a **Recently Merged PRs** table below the active queue showing the last 2-3 merges when verified.

### Mandatory Coverage Footer
Every rendered PR queue table must conclude with the mathematically verified coverage footer:
`Worktree coverage: N/N children · M/M stashes`
`Detail artifact: [reports/pr_queue_detail.json](file://<path>)`
proving that all physical `.worktrees/` directories and all git stash entries are accounted for with accurate numerator and denominator measurements.

---

## 3. Constraints

- Total response (tables + any surrounding text) must be **< 1000 words**.
- **Mandatory Turn-Level Live Execution (Anti-Stale Cache Guard)**:
  - Every conversational turn requesting, regenerating, or reformatting the PR Queue / Branch Summary table MUST re-execute live queries (`python tools/pr_governance.py list` and `python tools/pr_governance.py worktrees`) within that exact turn immediately prior to emitting the table.
  - Re-projecting, transforming, or recycling rows from previous conversation turns, scratchpads, or memory without fresh subprocess verification is strictly prohibited and fails the Truth Invariant closed. Out-of-band mutations from parallel sessions or manual operator actions must be reflected immediately.
- Data must come from live commands — never from stale cached state:
  1. **Topology & Clustering**: `python tools/pr_topology_auditor.py`
  2. **Git Sync**: `python tools/pr_governance.py list`
  3. **Worktree State**: `python tools/pr_governance.py worktrees`
  4. **Wave Discovery**: `git log --oneline main..<branch> --grep="wave\|Wave"` (committed waves), branch-diff receipts (`reports/wave*`), and branch-native `implementation_plan.md` (verified non-stale per Stale Plan Guard above).
- Do **not** order by ease of implementation. Order strictly by P-level, then by risk-adjusted merge readiness within each P-level (lowest-risk first).
