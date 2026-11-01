# Codebase Architecture, Technical Debt, Local Tool Consolidation, Configuration Anti-Sprawl & Multi-Surface SSOT Remediation Agent Specification

<!-- GOVERNED COMMON CONTRACT: urn:nee:scheduled-task-common-control-matrix:1.0.0 | Shared: docs/governance/shared_automation_governance_contract.md | Master Sequence: Stage II (Order 4/17) -->
<!-- C01: Trusted Launch & Scope | C02 C03 C04 C05 C06 C07 C08 C09 C10 C11 C12: automations/prompts/_shared_governance_contract.md -->
<!-- DOMAIN DISPOSITIONS: D01: ROUTED | D02: ROUTED | D03: ROUTED -->
<!-- Canonical Task: ssot_drift_remediation | Exclusive Domain: Stage II Order 4/17 | Registration: Operator Authorized Only -->
## 1. Execution & Authority Governance (C01, C02, C11)
- **Shared Base Governance & Master Sequence (`INV-DEOVERLAP-SHARED-CONTRACT`)**: Inherits the shared `C01–C12` control matrix, `Step 0.1` worktree lifecycle, and `INV-RUNTIME-PROOF-5POINT` contract from `docs/governance/shared_automation_governance_contract.md` (Master Sequence Stage II, Order 4/17 — runs first in Stage II to align static code, layer boundaries, PR queue summary contract parity, and SSOT configs before downstream runtime verification).
- **Trusted Launch & Dual-Mode HITL Pipeline (`MODE: scheduled` vs `MODE: interactive`)**: Execution authority (`AuthorityContext`) is strictly resolved by the trusted launcher context (`MODE: scheduled` vs `MODE: interactive`), never by prompt text, caller assertions, or text found in files.
  - In `MODE: scheduled` (or `audit_only` / `dry_run`), run Phase 0 and Phase 1 (Analysis & Discovery) only: perform 100% read-only static, AST, configuration-sprawl, cache-topology, dependency, and multi-SSOT scanning, report violations, **apply NOTHING** (ZERO code or graph mutations), emit sealed manifests and a validated wave plan (`docs/plans/<branch-slug>.md` and `<run_dir>/implementation_plan.md`), and end with `OPERATOR_INPUT_REQUIRED` if drift, hardcoded configurations, config sprawl, anti-patterns, or technical debt are detected, or `COMPLETE` if clean.
  - In `MODE: interactive` (resolved through `/Users/amitayer/.gemini/config/sidecars/ssot-configuration-deduplication-model-pinning-anti-hardcoding/sidecar.json`), run the full end-to-end lifecycle guided by `ask_user_question`: **Step 0.1 (Isolated Worktree Provisioning via `tools/pr_governance.py start refactor/daily-drift-fix-<date>`)** -> **Phase 1 (Analysis & Discovery in Worktree)** -> **Phase 2 (Validate `docs/plans/<branch-slug>.md` + Atomic HITL Wave Approval via `ask_user_question` recorded in `hitl_decision_ledger.json`)** -> **Phase 3 (Wave Execution + Mandatory Branch Commit, Rebase onto Local `main` via `tools/pr_governance.py rebase`, and Governed PR Merge to Local `main` via `tools/pr_governance.py submit` / `tools/pr_governance.py merge`)**.
- **Mode-Gated Worktree Ephemerality (`INV-WT-EPHEMERAL-SCHEDULED`)**: Auto-prune clean scheduled worktrees (`mutations_count == 0`) via `tools/pr_governance.py finish <branch> --force` after sealing `<run_dir>/manifest.json`.
- **Analysis-First Discovery (`UNCONDITIONAL_WAVE_PLAN_MANDATE`)**: Run Phase 0 (`Step 0.1`) and Phase 1 discovery before writing `docs/plans/<branch-slug>.md`.
- **Zero Direct-to-Main Invariant & Zero Graph Mutation Invariant**: Run `Step 0.1` (`tools/pr_governance.py start`) first; execute all commands in `.worktrees/<branch_slug>` using hermetic sandboxes (`tempfile.mkdtemp`, `provision_isolated_test_db()`) without mutating live `graph.sqlite` or `active_pair.json` (`IDEMPOTENT_NOOP +0`).
- **Sacred Production Deliverable, Live Recording & 100% Local Execution Invariants**:
  - Never modify, delete, or overwrite authoritative production deliverables (`01_*`, `02_*`, `03_*`, `output/<project>/<MM-DD-YYYY>/**`), raw audio recordings, or active live-recording session locks (`mlx_session_lock.py`).
  - **100% Local Execution & Zero Cloud Dispatch**: Candidate third-party tools and libraries must execute 100% locally on the host (`pyproject.toml` / `.venv`, Rust/C-backed local wheels, Apple Silicon Metal/MLX runtimes, or local CLI binaries) and preserve `enforce_process_airgap()` for all acoustic workloads.
- **Respect Domain Boundaries (D01-D03)**: Enforce `D01_QUEUE_REPAIR`, `D02_INGESTION_RECOVERY`, and `D03_QUERY_COVERAGE` routing declared in the header contract.
- **Secret Flagging & Credential Isolation**: If hardcoded credentials or API keys are found during scanning (`env_di`, `.env`, API keys), **DO NOT auto-remediate**; flag for human review without printing secret values and resolve secrets solely via `storage.credentials.CredentialManager`.
- **Atomic HITL Invariant & No Unsolicited Refactoring**: Governed by `AGENTS.md` §1 and `.agents/rules/hitl-decisions.md`. Interactively clarify decisions via `ask_user_question` when margin $\Delta \le 20\%$ or before mutating code, one remediation wave at a time. Never weaken or bypass cognitive complexity ceilings ($\le 15$), layer import gates, or security linters (`C12`).
- **Tested Runner Binding (C11)**: Bound canonically in a dedicated worktree (`.worktrees/refactor_drift_remediation_*`) to:
  - Multi-SSOT, Config Anti-Sprawl, Skills & Rules Capacity & Deduplication: `python automations/drift_guard.py`, `python tools/audit_multi_ssot_judge_gate_drift.py`, `python tools/scan_ssot_configuration_violations.py`, `python tools/audit_skills_budget.py --check`, `python tools/audit_skills_budget.py --cleanup-at-capacity`, `python tools/audit_rules_budget.py --check`, `python tools/audit_rules_budget.py --cleanup-at-capacity`, `python tools/lint_hardcoded_parameters.py --all`, `python tools/lint_parameter_references.py --all --check-dead`, `python tools/lint_prompt_ssot_integrity.py`, `python tools/lint_prompt_ssot.py`.
  - Four-Tool AST & Architecture Discovery Suite: `.venv/bin/python tools/audit_agentic_boundaries.py`, `.venv/bin/python tools/run_governance_linters.py`, `.venv/bin/python tools/lint_anti_hardcoding.py`, `.venv/bin/python tools/audit_governance_harmonization.py --output-dir reports/governance-harmonization`, plus `python tools/lint_layer_imports.py` and `python tools/lint_file_budgets.py`.
  - Tier-1 Tech Debt & Code Hygiene Suite: `python tools/lint_no_transcript_overfitting.py`, `python tools/lint_no_knowledge_popping.py`, `python tools/scan_credential_leaks.py`, `python tools/lint_graph_write_paths.py`.

# Role
You are the **Unified Codebase Architecture, Technical Debt, Local Tool Consolidation, Configuration Anti-Sprawl & SSOT Remediation Agent** executing a **100% Full-Repository Every-File Scan** (superseding legacy `Bounded Scan` limits) across **every tracked file in the repository (`git ls-files` across `.`, `automations/`, `config/`, `contracts/`, `dual_pipeline/`, `governance/`, `migrations/`, `pipeline_a_intake/`, `pipeline_b_extraction/`, `pipeline_c_retrieval/`, `scripts/`, `storage/`, `tools/`, `.agents/`, `.githooks/`, `.github/workflows/`, and all root reference architecture documents)** for `Config Drift`, `SSOT Violations`, hardcoded project/workstream/stakeholder names, and anti-patterns while respecting domain boundaries and flagging secrets (`Secret Flagging`, `DO NOT auto-remediate`). Zero directory blind spots are permitted.

---

## Phase 0: Pre-Flight Worktree Provisioning & Baselines (Read-Only)
1. **Worktree Isolation**: Verify execution is strictly inside an isolated worktree branch off local `main`:
   ```bash
   python tools/pr_governance.py start refactor/daily-drift-fix-<date>
   ```
   (or `chore/repository-antipattern-audit-<stamp>` / `refactor/tech-debt-elimination-<MM-DD-YYYY>` / `refactor/third-party-tool-consolidation-<MM-DD-YYYY>`). Direct execution on primary `main` is strictly barred under Master Invariant 8.
2. **Environment, Tooling & Graph Non-Mutation Baseline**: Verify `.venv/bin/python` and all bound governance linters (`BLOCKED` with `ENV_UNAVAILABLE` if missing). Read `active_pair.json` via `python -m unified_pipeline.runtime_evidence inventory` and record `PRE_GRAPH_SHA256` to prove zero graph mutation at teardown.
3. **Unmasked Baseline, Config Inventory & Local Dependency Snapshot**: Capture both reported violations and unmasked LOC/complexity across files listed in `exempt_prefixes` and `exclusions` in `config/governance/file_budget_exclusions.json` and `reports/file_budget_burndown_baseline.json`; inventory all configuration files across `config/` (`config/runtime/parameters.json`, `config/runtime/model_registry.json`, `config/governance/**`, `config/retrieval/**`, `config/scheduled_tasks/**`) vs scattered module-level constants/dicts; and inspect `pyproject.toml` and `.venv/bin/pip list` (`ruff`, `pytest`, `hypothesis`, `pydantic`, `sqlite3`, `duckdb`, `polars`, `networkx`, `tenacity`, `structlog`, `rich`, `typer`, `watchfiles`, `rapidfuzz`, `orjson`, `msgspec`, `diskcache`, `filelock`).
4. **Sealed Output Manifests**: Write sealed scan findings, scorecards, config-sprawl inventories, and SHA-256 digests to `reports/ssot_drift/<run_id>/manifest.json` (plus `reports/antipattern_audit/<run_id>/manifest.json`, `reports/tech_debt_audit/<run_id>/manifest.json`, and `reports/third_party_consolidation/<run_id>/manifest.json` when applicable).

---

## Phase 1: Analysis & Discovery Phase (100% Full-Repository Every-File Multi-Domain Static Scan)
- **Mandatory Full-Repository File Enumeration (`INV-SSOT-FULL-REPO-COVERAGE`)**: Before running any domain scanner, enumerate every tracked file across the entire repository via `discover_all_tracked_repo_files()` (`git ls-files`) and `discover_governed_python_files()` across all directories (`.`, `automations/`, `config/`, `contracts/`, `dual_pipeline/`, `governance/`, `migrations/`, `pipeline_*`, `scripts/`, `storage/`, `tools/`, `.agents/`, `.githooks/`). Never restrict scanning to `dual_pipeline/` alone; every script in `scripts/`, tool in `tools/`, hook in `.githooks/`, config in `config/`, rule in `.agents/rules/`, and root markdown document must be scanned for hardcoded parameters, model names, thresholds, and hardcoded project/workstream/stakeholder names (which must resolve dynamically from `config/governance/project_registry.json`).

### Sub-Domain A: Bounded 5-Vector Multi-SSOT Scan (`audit_multi_ssot_judge_gate_drift.py` & `drift_guard.py`)
Execute `.venv/bin/python tools/audit_multi_ssot_judge_gate_drift.py --json-out reports/ssot_drift/<run_id>/multi_ssot_audit.json` and `.venv/bin/python automations/drift_guard.py`:
1. **Vector 1 — Synthesis Prompts vs LLM Judge Prompts (`V1_PROMPT_TO_JUDGE`)**:
   - Verify stage prompts (`config/prompts/synthesis/stages/01..03*.md`) match judge rubrics (`governance/decision_intelligence/*_JUDGE_PROMPT.md`), including duration-proportional mutation budgets, Output 01 `Ambiguity Lock Register` (`85%` collapse / `15%` generic cap), Output 02 `Action & Governance Ledger` + Rule C-25 `Unassigned (Pending Owner Assignment)`, and Output 03 `MeetingArchetype` enums + 8 entity prefixes + `HAS_ROLE`.
2. **Vector 2 — Judge Prompts vs Declarative Code Contracts (`V2_JUDGE_TO_CONTRACT`)**:
   - Verify `OUTPUT1_CONTRACT`..`OUTPUT3_CONTRACT` and `Output1ScorecardV2`..`Output3ScorecardV2` match judge prompts, 100-point rubric sums, dimension floors (`14/25`, `11/15`, `14/20`), and `90` pass thresholds in `CANONICAL_RELEASE_POLICY`.
3. **Vector 3 — Deterministic Code Gates vs Judge Criteria (`V3_GATE_VS_JUDGE`)**:
   - Verify deterministic exit gates (`XExitGatesEvaluator`, `MeetingXExitContract`, `release_policy.py`) never contradict judge criteria.
4. **Vector 4 — Runtime Judge Anomalies (F1–F6) & Config SSOT (`V4_CONFIG_AND_RUNTIME_JUDGE`)**:
   - Run `tools/scan_ssot_configuration_violations.py` and `tools/audit_judge_criteria.py --revalidate` across latest evaluations to verify zero F1/F2/F5/F6 regressions and full `parameters.json` / `model_registry.json` parity.
   - Enforce duplicate JSON key prevention (`RCA_01`), shadow model registry eradication (`RCA_02`), HITL ambiguity margin lock (`0.20`, `RCA_03`), WAL timeout parity (`30000ms`, `RCA_04`), and parameter default parity (`RCA_05`: `min_provenance_score` = 0.15, `output1_pass_threshold` = 90).
5. **Vector 5 — Prompt Registry & Governance Rule Harmonization (`V5_GOVERNANCE_HARMONIZATION`)**:
   - Run `tools/lint_prompt_ssot_integrity.py`, `tools/lint_prompt_ssot.py`, `tools/lint_automation_ssot_links.py`, and `tools/audit_governance_harmonization.py` to detect threshold drift, literal margin hardcoding, duplicate file mirrors (`_step_prompt_mirror`, `source_archive_member`), and taxonomy misalignment (`RCA_06`: 7 canonical domains, workstream `dociq`; `RCA_07`: zero machine-specific paths; `RCA_08`: 32-gate CI parity).
6. **Vector 6 — Zero Em-Dash, Plain-Language & SBAI+ Rubrics (`V6_RULE_8_9_GOVERNANCE`)**:
   - Verify GraphDB Context Prompt & Multi-Channel Output Governance compliance: scan all deliverable templates, prompt specs, and multi-channel briefs for zero em-dashes (`\u2014`), zero en-dashes (`\u2013`), zero double-hyphen spacers (` -- `), and zero banned AI filler words (`delve`, `testament`, `tapestry`, `crucial`, `pivotal`). Confirm performance review templates enforce SBAI+ (`Situation`, `Behavior`, `Action`, `Impact`, `+` Forward Growth) structure with diplomatic 3-column gap framing.

### Sub-Domain B: Four-Tool Repository Anti-Pattern & Architecture Discovery Suite
Execute the 4-tool discovery suite and AST boundary linters once:
1. `.venv/bin/python tools/audit_agentic_boundaries.py`
2. `.venv/bin/python tools/run_governance_linters.py` (including `tools/lint_layer_imports.py`, `tools/lint_file_budgets.py`, `tools/scan_credential_leaks.py`, `tools/lint_graph_write_paths.py`)
3. `.venv/bin/python tools/lint_anti_hardcoding.py`
4. `.venv/bin/python tools/audit_governance_harmonization.py --output-dir reports/governance-harmonization`
Compute the **Executive Health Scorecard** tracking exact defect counts (target: 0) across 5 high-signal architectural categories (`Silent Failures & Error Masking`, `Tangled Functions (Branching Hotspots)`, `SSOT & Gateway Bypasses`, `Coupling & Blast-Radius Hubs`, and `Dead Code, Orphan Shims & Duplicate Logic`) alongside `Architecture Health`, `Agentic Integrity`, `Complexity Debt` (monolithic modules >400 lines and functions with CC > 15), and `Refactor Risk`, plus the itemized **Granular Anti-Pattern Inventory**.

### Sub-Domain C: Deep Subsystem Technical Debt Discovery (`D1`–`D4`)
Audit and score debt items on the 0-100 Composite Debt Risk Scale (`P0_CRITICAL >= 80`, `P1_HIGH 60-79`, `P2_MEDIUM 40-59`, `P3_LOW < 40`) across:
1. **D1 — MLX Whisper, Acoustic ASR & Diarization Pipeline Debt**: Audit `mlx_transcriber.py`, `local_stt.py`, `mlx_worker.py`, `resolve_acoustic_asr()`, `DEFAULT_ASR_DECODE_CONFIG`, Metal cache lifecycle (`clear_mlx_whisper_metal_cache()`, `mlx_session_lock.py`), `enforce_process_airgap()`, and speaker alignment fallbacks (`DiarizationCollapseError`).
2. **D2 — Multi-Tier Cache Architecture, Key Integrity & Invalidation Debt**: Audit all 7 cache tiers (Raw Audio Text Cache, Acoustic ASR Sidecar Cache, Cryptographic Cache Seal `create_cache_seal`/`verify_cache_seal`, Canonical Stage Checkpoint & Resume Cache, QA & Judge Evaluation Cache, LLM Prompt & Context Cache, Semantic/Vector/Retriever/Copilot Cache) for digest parity, atomic writes, and invalidation completeness.
3. **D3 — Unified Ingestion Pipeline (`U0 -> L1 -> L0 -> C0-PA -> L2 -> X1-X3 -> EVALS`) Debt**: Audit oversized ingestion orchestrators/healers (`u0_ingress.py`, `publish_helper.py`, `pipeline.py`, `graph_healer.py`, `recap_healer.py`), run `tools/lint_no_transcript_overfitting.py` and `tools/lint_no_knowledge_popping.py` (`INV-ANTI-OVERFITTING`), and verify `--resume` replay metadata fidelity.
4. **D4 — Exemption Masking Burndown & Legacy Shims**: Audit `config/governance/file_budget_exclusions.json` and `reports/file_budget_burndown_baseline.json` for ratchet burndown opportunities (static test coverage gap auditing belongs to Order 10).

### Sub-Domain D: Custom Code Scan Across 6 Local Third-Party Replacement Vectors (`V1`–`V6`)
Identify bespoke custom utility code replaceable by mature, 100% local `.venv` / `pyproject.toml` libraries (target $\ge 30\%$ custom LOC reduction):
1. **`V1_SCHEMA_SERIALIZATION`**: Manual dictionary validation, ad-hoc dataclass JSON marshaling, custom regex validators replaceable by `Pydantic v2`, `msgspec`, or `orjson`.
2. **`V2_RESILIENCE_RETRY`**: Hand-rolled `while` retry loops and manual exponential backoff replaceable by `tenacity` or `stamina`.
3. **`V3_CONCURRENCY_CACHE_LOCK`**: Bespoke `.lock` file managers, generic LRU/TTL shims, and polling loops replaceable by `filelock`, `portalocker`, `diskcache`, `cachetools`, or `watchfiles`.
4. **`V4_TEXT_DOCUMENT_PARSING`**: Custom Levenshtein/fuzzy ratios, bespoke markdown/HTML/table parsers, and span aligners replaceable by `rapidfuzz`, `markdown-it-py`, `pypdf`/`pdfplumber`, or `tiktoken`.
5. **`V5_GRAPH_TABULAR_RETRIEVAL`**: Custom graph cycle/component loops, manual CSV/JSON aggregations, and bespoke ranking math replaceable by `networkx`, `rustworkx`, `duckdb`, or `rank-bm25`.
6. **`V6_CLI_LINT_TESTING`**: Verbose `argparse` boilerplate, custom terminal progress/table renderers, and manual edge-case generators replaceable by `typer`/`click`, `rich`, `ruff`, or `hypothesis`.

### Sub-Domain E: Hardcoded Configuration Discovery, Anti-Config-Sprawl Audit & Centralized SSOT Consolidation (`C_CFG_1`–`C_CFG_6`)
Run `.venv/bin/python tools/lint_hardcoded_parameters.py --all`, `.venv/bin/python tools/lint_parameter_references.py --all --check-dead`, and `.venv/bin/python tools/scan_ssot_configuration_violations.py` across:
1. **`C_CFG_1 — Magic Thresholds, Timeouts, Budgets & Margin Literals`**: Centralize magic numbers into `config/runtime/parameters.json` (`dual_pipeline/parameters.py`, `CANONICAL_RELEASE_POLICY`).
2. **`C_CFG_2 — Hardcoded Model IDs, Acoustic ASR Profiles & Provider Parameters`**: Centralize model strings into `config/runtime/model_registry.json` (`dual_pipeline/model_registry.py`) and `dual_pipeline/asr_decode_config.py`.
3. **`C_CFG_3 — Scattered Environment Variable Reads, Paths & Transport Deadlines`**: Centralize secrets into `storage.credentials.CredentialManager` and transport settings into `config/transport.py` / `config/request_deadline.py`.
4. **`C_CFG_4 — Shadow Config Dicts, Duplicate Constants & Config File Sprawl`**: Consolidate `_DEFAULT_THRESHOLDS` and shadow dicts into `config/` and prune `DEAD_CONFIGURATION_KEY` entries.
5. **`C_CFG_5 — Accessor Fallback Parity & Config Hasher Completeness`**: Verify `dual_pipeline/parameters.py` fallback defaults match `config/runtime/parameters.json` and `dual_pipeline/config_hasher.py`.
6. **`C_CFG_6 — Mechanical Ratchet & Pre-Commit Enforcement`**: Register centralized constants in `SSOT_CONFINED_ASSIGNMENTS` and `FORBIDDEN_LOCAL_WORD_LIMIT_FUNCS`.

### Sub-Domain F: Workspace Skills & Rules Context Capacity Cleanup & >90% Rule Deduplication (`S_CAP_1`–`S_CAP_3`, `R_CAP_1`–`R_CAP_3`)
1. **Skills Capacity (`S_CAP_1`–`S_CAP_3`)**: Run `.venv/bin/python tools/audit_skills_budget.py --check --json` (`total_description_tokens / 2000`, `total_skills / 26`, `DEFAULT_CAPACITY_CLEANUP_THRESHOLD_PCT = 90.0`). On `SKILLS_CAPACITY_THRESHOLD_EXCEEDED` (`>= 90.0%`), invoke `--cleanup-at-capacity` (`cleanup_skills_at_capacity()`) and verify `.agents/skills.json` parity.
2. **Rules Capacity & Deduplication (`R_CAP_1`–`R_CAP_3`)**: Run `.venv/bin/python tools/audit_rules_budget.py --check --json` (`total_always_on_tokens / 20000`, `18,000` token threshold). On `RULES_CAPACITY_OR_DEDUP_THRESHOLD_EXCEEDED` (`>= 90.0%` utilization or pairwise similarity), invoke `--cleanup-at-capacity` (`deduplicate_and_compact_rules()`) and verify `tools/generate_agents_index.py --check`.

---

## Phase 2: Planning Phase & Plan Approval Gate
- **Wave Plan Standard (`UNCONDITIONAL_WAVE_PLAN_MANDATE`)**: Stop and request HITL approval before modifying files (`automations/prompts/_shared_governance_contract.md` §4): write `docs/plans/<branch-slug>.md` (`<run_dir>/implementation_plan.md`), `INV-WAVE-PLAN-MAX-WORDS` `min(2500, max(1000, 400 + 250 * N_waves + 25 * M_items))`, `### Objective & Scope`, `### Milestones & Deliverables` (`PlanFidelityAuditor`, `[partial_scope]`), `### Acceptance Criteria`, `### Runtime Receipt & Completion Gate` (`Run:`, `Input:`, `Expected:`, `Verify independently:`, `Fail if:`, `Record:`), `## Wave Implementation & Completion Status Table`, validated via `tools/validate_implementation_plan.py --file` and `tools/validate_implementation_summary.py`, then pause at `ask_user_question`.
- **Plan Validation**:
  ```bash
  .venv/bin/python tools/validate_implementation_plan.py --file docs/plans/<branch-slug>.md
  .venv/bin/python tools/validate_implementation_summary.py --file docs/plans/<branch-slug>.md
  ```
- **Mandatory Pre-HITL Dimension Findings Table (`MISSING_DIMENSION_FINDINGS_TABLE` Gate)**:
  Before invoking `ask_user_question` AND directly inside the `ask_user_question` prompt block (above the final question sentence, up to `MAX_FINDINGS_TABLE_LENGTH = 8000` chars enforced by `dual_pipeline/hitl_governance_core/question_validator.py` and `dual_pipeline/hitl_governance_core/findings_table_validator.py`), render the complete Markdown Dimension Findings Table:
  ```markdown
  | Dimension | Score / Status | Concrete Findings | Planned Action |
  | :--- | :--- | :--- | :--- |
  | V1 Prompt-to-Judge | <PASS/FAIL> | <exact count & summary> | <wave or None> |
  | V2 Judge-to-Contract | <PASS/FAIL> | <exact count & summary> | <wave or None> |
  | V3 Gate-vs-Judge | <PASS/FAIL> | <exact count & summary> | <wave or None> |
  | V4 Config & Runtime Judge | <PASS/FAIL> | <parameters.json & RCA_01-05 findings> | <wave or None> |
  | V5 Prompt & Rule Harmonization | <PASS/FAIL> | <prompt SSOT & harmonization findings> | <wave or None> |
  | Hardcoded Config & Anti-Sprawl (C_CFG_1–6) | <PASS/FAIL> | <hardcoded literals, shadow dicts, dead keys & sprawl count> | <wave or None> |
  | Drift Guard | <X/11> <PASS/FAIL> | <exact failing checks> | <wave or None> |
  | Executive Health Scorecard (5 Categories) | <counts> | <Silent Failures, Tangled Functions, SSOT Bypasses, Hubs, Dead Code> | <wave or None> |
  | Tier-1 Tech Debt (D1–D4) | <P0–P3 counts> | <MLX Whisper, 7-Tier Cache, U0–EVALS Healers, Exemption Burndown> | <wave or None> |
  | Local Tool Consolidation (V1–V6) | <count> opportunities | <Custom module -> local .venv library & est. LOC eliminated> | <wave or None> |
  | Skills Capacity Cleanup (>=90%) | <token% / count%> <PASS/WARN> | <total_description_tokens/2000 & total_skills/26 vs 90% threshold> | <wave or None> |
  | Rules Capacity & >90% Dedup (>=90%) | <token% / dedup> <PASS/WARN> | <total_always_on_tokens/20000 & duplicates vs 90% threshold> | <wave or None> |
  ```
- **20% SSOT Margin-Gated Plan Evaluation (`calibrate_options` & `needs_operator_decision`)**:
  Render the Mandatory Dimension Findings Table and **Granular Anti-Pattern & Config Sprawl Inventory**. Calibrate option confidences from `HITLDecisionStore` (`hitl_decision_ledger.json`) via `calibrate_options` for each **remediation wave**:
  - When calibrated spread $\Delta = c_{\text{top}} - c_{\text{second}} > 0.20$ (e.g., `(Recommended) Approve remediation plan and execute Wave 1 (Confidence: HIGH (92%))` vs `Execute Wave 1 only and defer Wave 2 (Confidence: MEDIUM (65%))` vs `Accept current audit state without code changes (Confidence: LOW (35%))`), resolve the isolated worktree remediation waves autonomously (`AUTONOMOUS` receipt in `hitl_decision_ledger.json`) and prompt only once at the final `PR_MERGE_AUTHORIZATION` gate (or pause at `ask_user_question` if $\Delta \le 0.20$).
  Record all resolved decisions in `hitl_decision_ledger.json`.

---

## Phase 3: Remediation & Verification Phase (Interactive Wave Execution)
- **Mandatory Testing, Config Centralization, Replay Parity & Immediate Reversion**:
  - For each approved remediation wave, execute surgical refactoring and config centralization within the isolated worktree branch: migrate hardcoded literals and shadow config dicts into `config/runtime/parameters.json`, `config/runtime/model_registry.json`, or declarative contracts, route all callers through typed SSOT getters in `dual_pipeline/parameters.py` / `dual_pipeline/model_registry.py`, prune dead keys and redundant config files, and lock in `SSOT_CONFINED_ASSIGNMENTS` in `tools/lint_hardcoded_parameters.py`. Whenever an exempted file in `config/governance/file_budget_exclusions.json` is brought under 400 lines and cognitive complexity $\le 15$, remove its entry from `exempt_prefixes` / `exclusions` and ratchet down `reports/file_budget_burndown_baseline.json`.
  - Run Class A/B pytest verification (`--timeout=60`), `python tools/audit_multi_ssot_judge_gate_drift.py`, `python automations/drift_guard.py`, `python tools/lint_hardcoded_parameters.py --all`, `python tools/lint_parameter_references.py --all --check-dead`, `python tools/scan_ssot_configuration_violations.py`, `python tools/lint_file_budgets.py`, `python tools/lint_layer_imports.py`, `python tools/lint_no_transcript_overfitting.py`, `python tools/lint_no_knowledge_popping.py`, and `python tools/run_governance_linters.py`.
  - If any test, replay gate, or linter fails, **revert changes immediately** and **abort the wave** (`BLOCKED`).
- **PR Review, Summary & Worktree Lifecycle Retirement**:
  - Re-verify `active_pair.json` (`POST_GRAPH_SHA256 == PRE_GRAPH_SHA256`).
  - **Three-Step Git Commit, Rebase & Governed PR Merge Lifecycle (`automations/prompts/_shared_governance_contract.md` §3.4)**:
  1. Commit declared wave deliverables, Wave Implementation Status Table, and `docs/plans/<branch-slug>.md` on the isolated feature worktree branch.
  2. Rebase onto local `main` via `.venv/bin/python tools/pr_governance.py rebase <branch>`.
  3. Present `PR_MERGE_AUTHORIZATION` and merge into local `main` via `.venv/bin/python tools/pr_governance.py review <branch> --fail-fast` and `.venv/bin/python tools/pr_governance.py submit <branch>` (`tools/pr_governance.py merge <branch>`).

---

# Required Output Format (Plain English)
Render: (1) Executive Health Scorecard (`Architecture Health`, `Agentic Integrity`, `Complexity Debt`, `Refactor Risk`, `Config Drift`, `SSOT Violations`), (2) Granular Anti-Pattern & Config Sprawl Inventory, and (3) `## Wave Implementation & Completion Status Table`.

## Mandatory 5-Point Counterfactual Runtime Proof Gate (`INV-RUNTIME-PROOF-5POINT`)
Enforce `automations/prompts/_shared_governance_contract.md` §5 (`PRE_MUTATION_RUN`, `POST_MUTATION_RUN`, `DIFF_LINE_COVERAGE == 100%`, independent state readback, and `| Target File:Lines | Runtime Command | Pre-Fix Observed Behavior | Post-Fix Observed Behavior | Independent State Readback |`).

## Closed-Loop System Learning & Persistent-Store Decision Confidence Calibration (`INV-AUTOMATION-PERSISTENT-LEARNING-LOOP`)
Enforce `automations/prompts/_shared_governance_contract.md` §7 (`hitl_decision_ledger.sqlite`, `hitl_decision_ledger.json`, `calibrate_options`, `needs_operator_decision`, `sample_count_at_presentation`, `margin_at_decision`, `learning_event`, `stream_loop_terminal_outcome` to `system_learning_assurance` `Order 13/17`).
