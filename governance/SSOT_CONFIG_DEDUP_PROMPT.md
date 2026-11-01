# SSOT Configuration Deduplication, Model Pinning & Anti-Hardcoding — Dual-Mode Prompt

> **Status: PROMPT ONLY.** This file is a reusable prompt. Creating it executes nothing.
> **Modes:** `SCHEDULED` (unattended, cron) and `INTERACTIVE` (operator in the loop). One prompt, one behavior contract; only the escalation channel differs.

## Registration (scheduled mode)

```
/schedule
CronExpression: 0 3 * * 0        # weekly, Sunday 03:00 local (adjust as desired)
IsDaemon: true
Prompt: <paste the block under "Prompt Text">
```

## Interactive invocation

```
/goal Run the SSOT Configuration Deduplication prompt from governance/SSOT_CONFIG_DEDUP_PROMPT.md in INTERACTIVE mode.
```

---

## Prompt Text

```
SSOT Configuration Deduplication, Model Pinning & Anti-Hardcoding Sweep
NEE Decision Intelligence Engine

MODE: Detect from invocation. If no human is attached (cron / daemon / no ask_user_question
channel) => MODE=SCHEDULED. Otherwise MODE=INTERACTIVE. State the detected mode in your
first line of output.

You are a principal configuration-architecture engineer. Objective: drive the repository
toward a single source of truth (SSOT) for every configuration value, with zero hardcoded
values in code, and every LLM model reference explicitly pinned and registry-governed.
Be conservative: a wrong dedup that changes runtime behavior is worse than a missed one.

════════════════════════════════════════════════════════════
PHASE 0 — PRE-FLIGHT & MANDATORY WORKTREE ISOLATION
════════════════════════════════════════════════════════════
0.1 Read, in order: AGENTS.md; .agents/rules/git-worktree-governance.md;
    .agents/rules/pr-governance.md; .agents/rules/production-purity.md;
    .agents/rules/layer-architectural-integrity.md;
    .agents/rules/credential-isolation-and-egress-sanitization.md;
    .agents/rules/model-neutral-artifact-naming.md; .agents/rules/progress-indicators.md;
    .agents/rules/hitl-decisions.md.
0.2 Verify primary root is on `main`, not bare (`git config core.bare` == false), clean
    enough to branch from. If any git defect, lock, or bare state is found: HALT. Do NOT
    invent workarounds, ad-hoc worktrees, or patch governance scripts.
    SCHEDULED => write a BLOCKED report (reason code + exact error) and stop.
    INTERACTIVE => escalate via ask_user_question.
0.3 ALWAYS create a NEW worktree + branch for this run. Never reuse an existing one,
    never commit on `main`, never work in the primary root.
      RUN_ID=$(date +%Y%m%d-%H%M)
      BRANCH="chore/ssot-config-dedup-${RUN_ID}"      # conventional-branch compliant
      python tools/pr_governance.py start "${BRANCH}"  # provisions .worktrees/<slug>
    Verify with `python tools/pr_governance.py worktrees` that the worktree exists at
    `.worktrees/<slug>`, tracks `${BRANCH}`, is not detached, and is clean. All subsequent
    reads-for-change, edits, tests and commits happen INSIDE that worktree.
    If `start` fails, fall back only to the documented `create` subcommand; if that also
    fails, HALT per 0.2.
0.3a Hermetic git: unset GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE, GIT_COMMON_DIR,
     GIT_PREFIX before child git processes.
0.4 Establish the SSOT inventory baseline (inside the worktree):
      python tools/generate_ssot_inventory.py
      python tools/scan_ssot_configuration_violations.py
      python tools/lint_retired_models.py --check
      python tools/lint_margin_ssot.py
      python tools/lint_prompt_ssot_integrity.py
      python tools/lint_model_neutral_naming.py
      python tools/lint_hitl_tool_name.py --scope all
      python tools/lint_layman_clarity.py --check
      python tools/lint_anti_hardcoding.py
    Record exit codes and counts as the BEFORE baseline. Use progress-bar runners for any
    suite longer than ~3s (tools/run_tests_with_progress.py, `--timeout=60`).

════════════════════════════════════════════════════════════
PHASE 1 — ESTABLISH THE CANONICAL SSOT MAP (read-only)
════════════════════════════════════════════════════════════
Identify the one authoritative home per config domain. Start from these known owners and
verify them yourself (do not assume):
  - Runtime parameters / thresholds : config/runtime/parameters.json <-> unified_pipeline/parameters.py (or dual_pipeline/parameters.py)
  - Release thresholds              : unified_pipeline/release_thresholds.py
  - LLM models & capability tiers   : config/runtime/model_registry.json <-> unified_pipeline/model_registry.py
  - Credentials / approved models   : config/credentials.py (APPROVED_MODELS, MODEL_ALIASES)
  - Retrieval / BGE-M3 config       : config/retrieval/, models/bge-m3 manifests
  - Bridge limits / handoffs        : config/runtime/bridge_limits.yaml, handoff_contracts.yaml
  - Governance gates                : config/governance/*.yaml
  - Prompts                         : config/prompts/
  - Env var surface                 : env_di via tools/sync_env_ssot.py (names only; NEVER print values)
Output a table: Domain | Canonical file | Loader/accessor API | Consumers found.
If two files both claim authority for one domain, that is itself a finding (F-AUTH).
Precedence on genuine conflict: AGENTS.md > code SSOT loader > JSON/YAML config > docs.

════════════════════════════════════════════════════════════
PHASE 2 — DETECTION (taxonomy of anti-patterns)
════════════════════════════════════════════════════════════
Scan code, config, scripts, tests, CI, hooks and docs (exclude output/, archive/,
backups/, .worktrees/ siblings, node_modules, .venv, vendored models, binary stores).
Combine AST analysis (Python `ast`), structured parsing (json/yaml/toml), and regex only
as a last resort. Classify every finding:

 F-DUP   Multiple definitions of the same config value/schema/enum/default across files.
 F-DRIFT Copies that have diverged (same concept, different values).
 F-HARD  Hardcoded literals that belong in config: thresholds, timeouts, retry counts,
         batch sizes, limits, URLs/endpoints, file paths, ports, weights, margins,
         temperature/top_p/max_tokens, dimension sizes, feature flags, magic numbers.
 F-ENV   Direct `os.environ`/`os.getenv` outside the sanctioned config/credentials layer;
         env defaults duplicated at call sites; undeclared env vars.
 F-DEFAULT Divergent inline defaults (`.get("x", 30)`, `or 0.5`, function-signature
         defaults) shadowing the SSOT value.
 F-MODEL Any LLM/embedding/reranker model reference that is NOT a registry-governed,
         explicitly pinned identifier. Sub-classes:
           F-MODEL-ALIAS    floating aliases ("latest", "-preview", unversioned families
                            like "gpt-5", "claude-sonnet", "gemini-pro", `*-latest`);
           F-MODEL-AMBIG    ambiguous or colliding names, provider-implicit routing,
                            multiple spellings of the same model, MODEL_ALIASES that
                            resolve non-deterministically;
           F-MODEL-HARD     model string literal in code/tests/scripts/docs-as-config
                            instead of resolved via the registry tier;
           F-MODEL-UNPINNED missing version/snapshot/revision (incl. HF embedding models
                            without a pinned `revision`/commit SHA and checksum);
           F-MODEL-RETIRED  retired/decommissioned ids (see tools/lint_retired_models.py);
           F-MODEL-PARAM    forbidden/unsupported sampling params passed to reasoning
                            models (must go through sanitize_model_call_kwargs).
         Also check: SDK default-model fallbacks, request builders that omit `model`,
         config files with `model: null`, and tests that mock a different id than prod.
 F-PATH  Absolute or machine-specific paths, user names, hostnames in tracked files.
 F-SECRET Credential-like literals. Report location + type ONLY. NEVER echo the value.
 F-LAYER Config logic leaking across L0–L6 layer boundaries (config read in the wrong layer).
 F-DOC   Docs/rules restating a numeric/enum SSOT value instead of referencing it.
 F-TEST  Tests asserting hardcoded copies of SSOT values rather than loading them
         (tests may pin an expected value ONLY via an explicit "regression pin" marker).

For each finding capture: id, class, file:line, literal/value, canonical owner (if any),
consumers affected, behavioral risk (NONE|LOW|MED|HIGH), proposed remediation, and
whether it is AUTO-FIXABLE or NEEDS-DECISION.

Do NOT trust name similarity alone: two same-named constants with different semantics
(term overloading) are NOT duplicates. Prove equivalence by tracing usage before merging.

════════════════════════════════════════════════════════════
PHASE 3 — TRIAGE & DECISION POLICY
════════════════════════════════════════════════════════════
AUTO-FIXABLE (apply without asking) only when ALL hold:
  - Values are identical (or drift is resolvable by the code-SSOT value per Phase 1),
  - A canonical loader/accessor already exists,
  - Consumers are statically traceable and covered by tests,
  - Risk is NONE or LOW (tactical category, Lock 2 passed).
NEEDS-DECISION: value drift where intent is unclear, new SSOT keys required, any HIGH/MED
risk, any change that alters runtime behavior or model selection, any model-pin choice
where the exact pinned id/revision cannot be verified from the registry or provider docs.

DUAL-LOCK DECISION & HITL INVARIANTS:
  - Dual-Lock Invariant: High-criticality decisions (architectural choices, policy adjustments,
    new SSOT keys, model tier remapping, destructive modifications) fail Lock 2 (Criticality)
    and STRICTLY MANDATE human operator review via ask_user_question regardless of numerical
    confidence spread or margin delta.
  - Tactical Ambiguity Invariant: Tactical decisions (Lock 2 passed) require operator
    confirmation whenever unified_pipeline.parameters.needs_operator_decision(c_top, c_second)
    returns True (margin delta <= 20% inclusive or top confidence < 65% floor). Autonomous
    resolution is permitted ONLY when Lock 1 (spread > 20% and conviction >= 65%) and Lock 2
    (tactical) BOTH hold.
  - Zero-Jargon Layman Communication: All options presented to human operators must be written
    in plain English understandable by non-technical readers, with mandatory real-world impact
    statements. Primary option labels strictly ban raw code tokens, variable names, and file
    paths (detail belongs in plain-English context summaries; enforced by lint_layman_clarity.py).
  - Atomic Presentation: Governed by AGENTS.md §1 and .agents/rules/hitl-decisions.md. Present
    decisions interactively via ask_user_question with mutually exclusive options, "(Recommended)"
    first, persisting answers to the decision ledger. Never bundle questions.
  - Scheduled Mode: NEVER guess. Do not apply NEEDS-DECISION items. Record each in the report
    as OPEN_DECISION with options + confidence + recommendation, and leave code unchanged.

Model-pinning rules (non-negotiable):
  - Pin ONLY to identifiers already in config/runtime/model_registry.json /
    config/credentials.py approved sets, or verified against provider documentation
    (cite the source). NEVER invent a snapshot/revision string. If it cannot be verified
    => OPEN_DECISION (SCHEDULED) / ask_user_question (INTERACTIVE).
  - Code must reference models by capability TIER via unified_pipeline/model_registry.py;
    concrete vendor ids live ONLY in the registry (single place). Eliminate scattered
    literals, duplicate alias tables and per-call-site model strings.
  - Each registry entry must have: provider, exact pinned id, (for HF/local) immutable
    revision + checksum, status (ACTIVE|DEPRECATED|RETIRED), and parameter-immunity flags.
  - Resolution must be deterministic and fail-closed: an unknown/unpinned/ambiguous model
    raises a typed error — no silent fallback to a "default" model.
  - Honor Model-Neutral Artifact Naming: no model brand names in artifact/file names.
  - Do not change which model a tier maps to unless the operator approves it.

════════════════════════════════════════════════════════════
PHASE 4 — REMEDIATION (inside the worktree, in small waves)
════════════════════════════════════════════════════════════
Decompose into sequential numbered waves, e.g.:
  Wave 1 Config SSOT consolidation (F-DUP/F-DRIFT/F-AUTH)
  Wave 2 Hardcode extraction (F-HARD/F-DEFAULT/F-PATH/F-ENV)
  Wave 3 Model pinning & registry hardening (F-MODEL-*)
  Wave 4 Tests, docs back-references, enforcement lint
Each wave declares: Objective & Scope; Deliverables; Acceptance Criteria; Runtime Receipt
& Completion Gate. Do not start Wave N+1 until Wave N has a passing receipt.

Remediation techniques (prefer, in order):
  1. Delete the duplicate; import/load from the canonical accessor.
  2. Add the missing key to the canonical config + typed accessor (with schema + bounds),
     then replace literals. Respect ParameterRegistry bounds; no new key without schema.
  3. Replace inline `.get(k, default)` with the SSOT accessor; no shadow defaults.
  4. Replace raw os.environ reads with the sanctioned config/credentials layer.
  5. Replace doc restatements with references to the SSOT location.
Constraints:
  - Preserve unrelated comments/docstrings/formatting. Touch only files tied to a finding.
  - No unsolicited refactors, renames, or module moves beyond the finding.
  - Respect L0–L6 import direction, 400-line module budget, no new circular imports.
  - Never print/commit secrets; reference env_di names only.
  - Never edit anything under output/ authoritative artifacts, 01_*/02_*/03_* deliverables,
    graph DB files, receipts of prior runs, or decision ledger files by hand.
  - Config changes that touch retrieval weights must still pass the 11 BGE-M3 gates before
    any activation; this sweep never activates or mutates the graph.
  - Behavior-preserving by default: after each wave, effective resolved config values must
    be IDENTICAL to before unless a drift was explicitly resolved (list each such change).

Add durable enforcement so regressions fail closed:
  - Extend the existing scanners/linters (tools/scan_ssot_configuration_violations.py,
    tools/lint_retired_models.py, tools/lint_hitl_tool_name.py, tools/lint_layman_clarity.py)
    rather than creating parallel one-off scripts. Enroll any new test/lint in
    tools/run_ci_gates.py.
  - Wave touching pipeline code needs >=1 E2E test and >=1 adversarial/edge-case test
    (>=3 of: boundary, malformed input, races, overflow, bypass, rollback, regression pin),
    both enrolled in E2E_TESTS/DETERMINISTIC_TESTS per AGENTS.md.

════════════════════════════════════════════════════════════
PHASE 5 — VERIFICATION (empirical proof, no assumed success)
════════════════════════════════════════════════════════════
Per wave and at the end, run and record:
  - Re-run every Phase 0.4 scanner/linter => AFTER counts; must be monotonic non-increasing
    per class, and ZERO new findings. Any regression => revert that hunk.
  - Targeted tests for touched modules + full relevant Class A/B suites via
    tools/run_tests_with_progress.py with `--timeout=60` (progress bars mandatory).
  - Config equivalence proof: dump resolved config/registry before vs after; diff must be
    empty except explicitly approved drift resolutions.
  - Model pin proof: every model reference resolves through the registry to a pinned id;
    zero F-MODEL-* remaining or each remaining one listed with justification.
  - Layer/size audit: skill-architectural-integrity-auditor checks pass.
  - Credential hygiene: no secret literals introduced (scan the diff).
Terminal gate: `git diff --stat` reviewed, `git status` clean after commit, worktree
registry invariants INV-WT-01..06 still hold.

════════════════════════════════════════════════════════════
PHASE 6 — COMMIT, REPORT, HANDOFF (never merge)
════════════════════════════════════════════════════════════
6.1 Commit only intended files, per-wave, conventional messages
    (e.g. `chore(config): consolidate X into SSOT`). NEVER `git commit -a` dirty dumps.
    NEVER commit to `main`. NEVER merge; NEVER run `git merge`. Merging into main is a
    separate human-authorized step via `python tools/pr_governance.py merge ${BRANCH}`.
6.2 Write the report to reports/ssot-config-dedup/ssot_config_dedup_${RUN_ID}.md (inside
    the worktree, committed on the branch) AND emit it in-band. Contents:
      a) Run metadata: MODE, RUN_ID, branch, worktree path, base commit SHA, timestamps.
      b) BEFORE/AFTER findings table by class (F-DUP, F-DRIFT, F-HARD, F-ENV, F-DEFAULT,
         F-MODEL-*, F-PATH, F-SECRET, F-LAYER, F-DOC, F-TEST).
      c) Itemized finding ledger: id | class | file:line | before | after | status
         (FIXED | OPEN_DECISION | ACCEPTED_EXCEPTION | FALSE_POSITIVE) | risk.
      d) Model pinning table: reference site | previous string | tier | pinned id/revision |
         source of verification.
      e) OPEN_DECISIONs with options, confidence, recommendation.
      f) Verification evidence: exact commands, exit codes, test counts, config-equivalence
         diff result.
      g) Wave ledger table:
         | Wave | Status | Completion criteria | Runtime receipt | Validation result | Open blocker / next authorization |
      h) Mandatory Wave Implementation & Completion Status Table:
         | Wave Number | Description / Scope | Completion Status | Detailed Deliverable & Empirical Evidence | Runtime Receipt & Verification Gate |
         (status values: ✅ COMPLETE, ⏳ OPEN, 🚫 BLOCKED)
      i) Trend vs prior reports in reports/ssot-config-dedup/ (improving/stable/worsening).
      j) Graph impact statement: "IDEMPOTENT_NOOP — graph_sha256 unchanged; +0 across all
         graph tables" (this sweep must not mutate the graph).
6.3 Terminal outcome is exactly one of: COMPLETE | BLOCKED (reason code + repeatable
    blocker) | NO_FINDINGS (still emit report; remove the empty worktree via
    `python tools/pr_governance.py finish ${BRANCH}` ONLY if zero commits were made).
6.4 SCHEDULED: end with a one-paragraph operator alert if any HIGH-risk or OPEN_DECISION
    items exist, or if the same findings persisted for 3+ consecutive runs.
    INTERACTIVE: end by asking (ask_user_question, single decision) whether to open the
    governed PR review for the branch.

════════════════════════════════════════════════════════════
GLOBAL GUARDRAILS
════════════════════════════════════════════════════════════
- Anti-rationalization: on any environment/git/tooling failure, document and halt; no bypasses.
- Truth invariant: every claim cites file:line or command output. No speculative findings.
- Blast-radius cap (SCHEDULED): if auto-fixable change count exceeds 40 files or any
  HIGH-risk item would be required to proceed, stop after the current wave, commit what is
  verified, and report the remainder as OPEN.
- Long operations >15s emit heartbeats; use tqdm progress per AGENTS.md; no ephemeral
  progress markdown files.
- Never read or alter live-meeting transcripts or output/** content to answer config questions.
- Never change graph, active_pair.json, release state, or credentials.
```

---

## Design Notes

| Concern | How the prompt handles it |
| :--- | :--- |
| New worktree branch | Phase 0.3 always runs `tools/pr_governance.py start chore/ssot-config-dedup-<RUN_ID>` and verifies INV-WT invariants; never reuses a worktree or touches `main`. |
| Scheduled vs interactive | Single mode switch; scheduled mode never guesses (records `OPEN_DECISION`), interactive uses atomic `ask_user_question` with Dual-Lock governance, 20% SSOT margin, and Zero-Jargon Layman clarity. |
| SSOT dedup | Phase 1 canonical map, Phase 2 `F-DUP`/`F-DRIFT`/`F-AUTH`/`F-DEFAULT`/`F-DOC`/`F-TEST`; equivalence must be proven, not inferred from names. |
| Model pinning | `F-MODEL-*` taxonomy; pin only to registry/provider-verified ids, never invented; tier-based resolution; fail-closed on unknown/ambiguous. |
| No hardcoding | `F-HARD`/`F-ENV`/`F-PATH`/`F-SECRET`; extraction into schema-bounded config with typed accessors. |
| Reuses existing tooling | `scan_ssot_configuration_violations.py`, `lint_retired_models.py`, `lint_margin_ssot.py`, `lint_hitl_tool_name.py`, `lint_layman_clarity.py`, `lint_anti_hardcoding.py`, `generate_ssot_inventory.py`, `unified_pipeline/model_registry.py`, `pr_governance.py`. |
| Safety | Behavior-preserving by default, config-equivalence diff, blast-radius cap, no merge, no graph mutation. |
