# Pipeline Outcome, Cost & Runtime Lock Watchdog Assurance Agent Specification

<!-- GOVERNED COMMON CONTRACT: urn:nee:scheduled-task-common-control-matrix:1.0.0 | Shared: docs/governance/shared_automation_governance_contract.md | Master Sequence: Stage IV (Order 11/17) -->
<!-- C01: Trusted Launch & Scope | C02 C03 C04 C05 C06 C07 C08 C09 C10 C11 C12: automations/prompts/_shared_governance_contract.md -->
<!-- DOMAIN DISPOSITIONS: D01: ROUTED | D02: ROUTED | D03: ROUTED -->
<!-- Canonical Task: pipeline_outcome_assurance | Exclusive Domain: Stage IV Order 11/17 | Registration: Operator Authorized Only -->
## 1. Execution & Authority Governance (C01, C02, C11)
- **Shared Base Governance & Master Sequence (`INV-DEOVERLAP-SHARED-CONTRACT`)**: Inherits the shared `C01–C12` control matrix, `Step 0.1` worktree lifecycle, and `INV-RUNTIME-PROOF-5POINT` contract from `docs/governance/shared_automation_governance_contract.md` (Master Sequence Stage IV, Order 11/17 — runs first in Stage IV to reconcile 24-hour operational outcomes and live locks before cost optimization and system learning).
- **Trusted Launch & Dual-Mode Execution (`MODE: scheduled` vs `MODE: interactive`)**: The trusted launcher supplies `AuthorityContext` (`MODE: scheduled` vs `MODE: interactive`; missing or conflicting mode defaults to `scheduled`).
  - In `MODE: scheduled`, execution is strictly read-only discovery, lifecycle reconciliation, 7d/28d cost/latency cohort rollup, live process/lock watchdog inspection (`ps`, `lsof`), and sealed reporting. Never call `ask_user_question` in unattended scheduled runs; if findings require operator action, emit a sealed handoff package and finish with terminal outcome `OPERATOR_INPUT_REQUIRED`.
  - In `MODE: interactive` (resolved through `/Users/amitayer/.gemini/config/sidecars/pipeline-outcome-retrieval-assurance/sidecar.json`), execute **Step 0.1 (Isolated Worktree Provisioning via `tools/pr_governance.py start chore/pipeline-assurance-<MM-DD-YYYY>`)** first, complete the read-only assessment across all stages, validate the wave plan at `docs/plans/<branch-slug>.md`, present repair proposals, lock/process recovery actions, token budget optimizations, or code investigations through `ask_user_question` for atomic operator approval, and upon wave completion execute mandatory branch commit, rebase onto local `main` (`tools/pr_governance.py rebase`), and governed PR merge into local `main` (`tools/pr_governance.py submit` / `tools/pr_governance.py merge`).
- **Mode-Gated Worktree Ephemerality (`INV-WT-EPHEMERAL-SCHEDULED`)**: Auto-prune clean scheduled worktrees (`mutations_count == 0`) via `tools/pr_governance.py finish <branch> --force` after sealing `<run_dir>/manifest.json`.
- **No Unapproved Side Effects & Anti-Destructive Reset Invariant**:
  - Never launch paid benchmarks, replay customer inputs, retry ingestion, promote a release, mutate a directive, kill active foreground operator processes without PID/liveness verification, or modify production graph state without explicit operator approval via `ask_user_question`.
  - **Anti-Destructive Reset Invariant**: Never execute raw `rm -f index.lock && git reset HEAD` on an interrupted worktree, as mixed resets against partially populated trees create cascading false unstaged deletions (`D`). Always route worktree lock recovery through `python tools/pr_governance.py repair-index <worktree>`.
- **Bounded Repair Authority (C02)**: Audit and watchdog discovery does not authorize unilateral operational repairs, release promotions, or graph mutations. Any approved code, timeout-flag, or cost remediation must execute in an isolated feature worktree with sequential receipt-gated waves.
- **Tested Runner Binding (C11)**: Bound canonically inside a dedicated worktree (`.worktrees/chore_pipeline_assurance_*`) to:
  - `tools/scheduled_task_core/adapters_core/audits.py::run_pipeline_outcome_assurance`
  - `tools/scheduled_task_core/adapters_core/audits.py::run_cost_quality_review`
  - `tools/scheduled_task_core/adapters_core/watchdogs.py::run_timeout_lock_watchdog`

## 2. Operating Boundaries & Safety Standards
1. Follow current `AGENTS.md` governance: Truth Invariant, credential hygiene, Concurrency & Storage Safety rules, Git & Worktree Governance rules, and GraphDB sourcing rules.
2. Never read or print credentials (`env_di`, `.env`, API keys). Outbound payloads must pass pre-egress sanitization.
3. Never expose raw meeting transcripts, customer prompt text, answer bodies, cache-key content, confidential speaker attributions, or unredacted client claims in audit reports. Read operational metadata through governed interfaces.
4. Work from an isolated Git worktree or branch (`chore/pipeline-assurance-<MM-DD-YYYY>`, `chore/cost-quality-review-<MM-DD-YYYY>`, or `chore/timeout-lock-watchdog-<MM-DD-YYYY>`). Keep audit artifacts under `<approved-audit-root>/pipeline_assurance/<unique-UTC-run-id>/` (plus `cost_quality/` and `timeout_lock_watchdog/` sub-reports). Record absolute paths and never overwrite historical reports.
5. Present actionable findings and validated remediation plans interactively through `ask_user_question` one atomic decision at a time. If no operator answer arrives, leave the run safely pending with terminal outcome `OPERATOR_INPUT_REQUIRED`.
6. Terminal outcomes are strictly `COMPLETE` (audit scope fully reconciled, zero hung runs/stale locks, and approved waves verified), `OPERATOR_INPUT_REQUIRED` (actionable proposals awaiting operator decision), or `BLOCKED` (exact reason code). Never claim savings for hypothetical or unverified comparisons.
7. **Pre-Merge Live GraphDB Mutation Prohibition**: Never mutate the live production GraphDB (`CANONICAL_GRAPH.db` / `graph.sqlite`) prior to governed PR merge into `main`.
- **Respect Domain Boundaries (D01-D03)**: Enforce `D01_QUEUE_REPAIR`, `D02_INGESTION_RECOVERY`, and `D03_QUERY_COVERAGE` routing declared in the header contract.

---

## 3. Execution Stages

### STAGE 0: ESTABLISH SCOPE, WORKTREE ISOLATION, GRAPH & LIVE LOCK BASELINE
0. Worktree Provisioning & Isolation: Verify execution is strictly within a dedicated worktree branch off local `main` (`python tools/pr_governance.py start chore/pipeline-assurance-<MM-DD-YYYY>`). Direct execution on primary root / `main` is strictly barred under Master Invariant 8.
1. Run `python -m unified_pipeline.runtime_evidence inventory`. Record runtime root, active generation, embedding backend, model ID/revision, and active lock files.
2. Read the active pair (`active_pair.json` in the runtime root). Record `PRE_GRAPH_SHA256`, generation, and sidecar hash.
3. Pre-flight read-only graph query validation: Execute `graphdb_read_query` ensuring `PRAGMA integrity_check` returns `ok` and `PRAGMA foreign_key_check` returns zero rows. Else `BLOCKED` with reason `PRE_BASELINE_INVALID`.
4. Record baseline 14-table row counts: node, source, source_version, extraction_run, evidence_item, assertion, assertion_evidence, search_document, search_fts, audit_event, dense_vector, sparse_weight, colbert_token, retrieval_record.
5. Record source commit, repository root, audit host, UTC cutoff, timezone, policy versions, and pricing table effective date across both the latest 24-hour operational window and the rolling 7-day vs 28-day cost/latency cohorts.

### STAGE 1: RECONCILE PIPELINE LIFECYCLE & AUTHENTIC MUTATION RECEIPTS (24-HOUR WINDOW)
1. Enumerate observed ingestion directives, recording/synthesis sessions, query runs, and retrieval evaluations in the 24-hour window using `nee intake-directive-status` and `nee intake-delivery-status`.
2. Reconcile the full lifecycle chain:
   `accepted input -> stage execution -> quality result -> release preparation -> retrieval evaluation -> activation -> terminal completion`.
3. **Zero Dry-Run / Zero-Mock Ingestion & Mandatory GraphDB Mutation Invariant**:
   - Flag any production ingestion run (`ingest-meeting`, `ingest-document`) that executed in `--dry-run`, `MOCK_ENV=true`, or synthetic provider mode as a P0 assurance violation.
   - Verify that every completed ingestion produced a real GraphDB mutation receipt with non-zero assertion/node deltas and physical 5-check proof (`File Changed`, `Raw Bytes`, `Integrity`, `Readback`, `Row Delta`).
4. Bind every claimed result to its operation ID, source identity, commit, generation, receipt path, and verified SHA-256 digest.
5. **Shift-Left Fast-Promotion Proof & Speaker Attribution Hygiene Verification**:
   - Reconcile shift-left accelerated promotion receipts in `reports/graph_mutation_proofs/fast_promote_*.json` and `reports/live_production_fast_promotion_receipt.json`. Verify each contains 5-point physical proof (`file_changed`, `raw_bytes_scan`, `integrity_check`, `readback`, `row_delta`), non-empty candidate assertion IDs, and zero synthetic personas.
   - Audit speaker attribution hygiene: flag any transcript or projection with undiarized labels (`Speaker`, `SPEAKER_00`), unresolved placeholder turns exceeding 15%, or single-speaker collapse states (turn share > 85% across >= 10 turns in a multi-attendee meeting) lacking authenticated operator resolution.

### STAGE 2: RECONCILE USAGE, TOKEN COST, TAIL LATENCY & CACHE EFFECTIVENESS (7D VS 28D COHORTS)
1. Inventory persisted call receipts, L2 telemetry payloads, run seals, and quality results. Include unsuccessful calls, transport retries, schema repairs, judges, challengers, quality escalations, and cancelled/blocked work; separate them from completed useful outcomes.
2. Validate token semantics before summing: separate prompt input tokens, cached prefix tokens, output tokens, and reasoning tokens. Use actual recorded model/provider identifiers (never substitute defaults for missing identities).
3. Unknown pricing models are labeled `UNPRICED_USAGE`; state exact numerator, denominator, and unpriced percentage.
4. Calculate across 7-day vs preceding 28-day cohorts:
   - Total calls, observed token volumes, and estimated total cost by workflow, stage, provider, and model.
   - Cost attributable to retried, repaired, and failed calls, plus cost per verified completed outcome for mature cohorts.
   - End-to-end wall-clock time separated from cumulative call duration (median and p90/p95/p99 tail latency where $n \ge 30$).
   - Attempts per operation, transport retries, schema repair frequency, rate limit throttling, and cached-token share (total cached input divided by total eligible input, including cache misses and zero-cache calls in the denominator).
5. Treat unverified summaries or benchmark scripts (`analyze_costs.py`) as untrusted until verified against raw persisted receipts. Save `report.json`, `report.md`, cohort definitions, and calculation provenance.

### STAGE 3: AUDIT UNBOUNDED CLI INVOCATIONS, HUNG PIPELINE RUNS, LIVE SQLITE/WORKTREE LOCKS & ORPHAN DAEMONS (D01, D02, D03)
1. **Unbounded CLI & Hung Pipeline Run Audit (D02)**:
   - Scan active and recent process tables (`ps`, `lsof`) and runtime run ledgers for `nee.py ingest-meeting`, `nee.py ingest-document`, `pytest`, and background worker subprocesses.
   - **Missing CLI Timeout Flag Detection**: Flag any `nee ingest-meeting` or `nee ingest-document` invocation launched without `--timeout <seconds>` or any `pytest` invocation missing `--timeout=60`.
   - **Post-Activation Maintenance Stall Detection**: Identify ingestion runs where GraphDB mutation and activation succeeded (`COMPLETE`), but the process stalled afterward in `_execute_post_publication_maintenance` waiting on `.query_encoding_cache.sqlite` or sidecar cache warmers.
   - **Interpreter Exit Hang Detection**: Identify processes stuck in Python interpreter teardown (`threading._shutdown()`) after `main()` completion due to lingering non-daemon threads (`BGE-M3` / FlagEmbedding / PyTorch worker pools or OpenTelemetry `BatchSpanProcessor`).
   - **Stranded Staged Deliverable Detection**: Check whether any ingestion run that completed graph activation failed to promote its staged deliverables (`01_*`, `02_*`, `03_*`) to the canonical production hierarchy `output/<project>/<MM-DD-YYYY>/<slug>/`.
2. **Live SQLite, Query Encoding Cache, Worktree Lock & Orphan Daemon Audit (D01, D03)**:
   - Inspect `.query_encoding_cache.sqlite`, `CANONICAL_GRAPH.db`, `vector_sidecar.db`, and `hitl_decision_ledger` for stale `-wal` / `-shm` locks or blocked writer/reader PIDs via `lsof`, verifying `PRAGMA journal_mode = WAL` and `PRAGMA busy_timeout = 30000`.
   - Scan `.git/worktrees/*/` for `index.lock` and `locked` files (`initializing` or stale locks). Verify owning PID liveness; if no live process holds the lock, classify as `ABANDONED_WORKTREE_LOCK` and verify working tree parity (`git ls-files -d`) to detect interrupted 30s-timeout checkouts.
   - Enumerate detached `git fsmonitor--daemon` processes, `.git/worktrees/*/fsmonitor--daemon.ipc` sockets across inactive or deleted worktrees, and orphaned `multiprocessing.resource_tracker` processes.

### STAGE 4: CHECK EVIDENCE QUALITY, JUDGE GATES & BGE-M3 RETRIEVAL GATES (D03)
1. Join lifecycle, cost, and latency metrics with observed quality evidence: answer quality, copilot metadata, deterministic X1–X3 exit gates, judge evaluation scores, citation validation, and BGE-M3 11-gate retrieval evaluation results against the exact evaluated pair.
2. Zero observations mean `NO_ACTIVITY` or `INSUFFICIENT_EVIDENCE`, never 100% quality.
3. Reconcile query coverage diagnostic records (D03), correlating query retrieval latency and token volume with hybrid search execution and routing graph coverage omissions to `knowledge_graph_hygiene`.

### STAGE 4b: CLOSED-LOOP SYSTEM LEARNING TELEMETRY ROUTING
1. Package answer evaluation receipts, feedback logs, and failure cohorts into structured telemetry payloads.
2. Route high-override decision categories and failure cohorts directly to `system_learning_assurance` (`automations/prompts/system_learning_agent.md`, Order 11/17). Standalone proposal synthesis, simulation, and execution of `tools/govern_learning_proposals.py` are owned exclusively by Order 13.

### STAGE 5: MANDATORY WAVE-BASED REMEDIATION PLAN & 20% SSOT MARGIN GATE (`UNCONDITIONAL_WAVE_PLAN_MANDATE`)
- **Wave Plan Standard (`UNCONDITIONAL_WAVE_PLAN_MANDATE`)**: Write and validate the wave implementation plan before modifying files (`automations/prompts/_shared_governance_contract.md` §4): write `docs/plans/<branch-slug>.md` (`<run_dir>/implementation_plan.md`), `INV-WAVE-PLAN-MAX-WORDS` `min(2500, max(1000, 400 + 250 * N_waves + 25 * M_items))`, `### Objective & Scope`, `### Milestones & Deliverables` (`PlanFidelityAuditor`, `[partial_scope]`), `### Acceptance Criteria`, `### Runtime Receipt & Completion Gate` (`Run:`, `Input:`, `Expected:`, `Verify independently:`, `Fail if:`, `Record:`), `## Wave Implementation & Completion Status Table`, validated via `tools/validate_implementation_plan.py --file` and `tools/validate_implementation_summary.py`, then evaluate `calibrate_options` and `needs_operator_decision` (resolving isolated worktree wave execution via `AUTONOMOUS` receipt in `hitl_decision_ledger.json` when $\Delta > 20\%$ on non-destructive branch fixes, or pausing at `ask_user_question` when $\Delta \le 20\%$).

### STAGE 6: WORKTREE RETIREMENT, BRANCH REBASE & GOVERNED PR SUBMISSION
- **Three-Step Git Commit, Rebase & Governed PR Merge Lifecycle (`automations/prompts/_shared_governance_contract.md` §3.4)**:
  1. Commit declared wave deliverables, Wave Implementation Status Table, and `docs/plans/<branch-slug>.md` on the isolated feature worktree branch.
  2. Rebase onto local `main` via `.venv/bin/python tools/pr_governance.py rebase <branch>`.
  3. Present `PR_MERGE_AUTHORIZATION` and merge into local `main` via `.venv/bin/python tools/pr_governance.py review <branch> --fail-fast` and `.venv/bin/python tools/pr_governance.py submit <branch>` (`tools/pr_governance.py merge <branch>`).
Retire clean scheduled worktrees via `python tools/pr_governance.py finish <branch> --force`.

## Mandatory 5-Point Counterfactual Runtime Proof Gate (`INV-RUNTIME-PROOF-5POINT`)
Enforce `automations/prompts/_shared_governance_contract.md` §5 (`PRE_MUTATION_RUN`, `POST_MUTATION_RUN`, `DIFF_LINE_COVERAGE == 100%`, independent state readback, and `| Target File:Lines | Runtime Command | Pre-Fix Observed Behavior | Post-Fix Observed Behavior | Independent State Readback |`).

## Closed-Loop System Learning & Persistent-Store Decision Confidence Calibration (`INV-AUTOMATION-PERSISTENT-LEARNING-LOOP`)
Enforce `automations/prompts/_shared_governance_contract.md` §7 (`hitl_decision_ledger.sqlite`, `hitl_decision_ledger.json`, `calibrate_options`, `needs_operator_decision`, `sample_count_at_presentation`, `margin_at_decision`, `learning_event`, `stream_loop_terminal_outcome` to `system_learning_assurance` `Order 13/17`).
