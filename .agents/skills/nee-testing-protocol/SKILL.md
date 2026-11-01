---
name: nee-testing-protocol
description: Testing protocol, wave execution, and CI gates. Use for Class A/B pytest runs, mandatory --timeout=60, progress tracking, and test gap audits.
---

# NEE Testing Protocol & Wave Execution

This skill guides test execution, progress tracking, and CI gate verification across the repository.

## When to use

- Running the **Testing Agent** (`testing-gaps-assurance-agent` sidecar) or **Functionality Agent** (`e2e-critical-functionality-assurance-agent` sidecar): **MUST** first read `/Users/amitayer/.gemini/config/sidecars/<sidecar-dir>/sidecar.json` and execute [`automations/prompts/testing_gaps_assurance_agent.md`](file:///Users/amitayer/Git/decision-intelligence-engine/automations/prompts/testing_gaps_assurance_agent.md) or [`automations/prompts/e2e_critical_functionality_assurance_agent.md`](file:///Users/amitayer/Git/decision-intelligence-engine/automations/prompts/e2e_critical_functionality_assurance_agent.md) end-to-end (`Step 0.1` isolated worktree creation via `tools/pr_governance.py start` -> discovery -> wave plan in `docs/plans/<branch-slug>.md` -> atomic HITL -> branch commit, `tools/pr_governance.py rebase`, and governed PR merge to `main`).
- Executing wave implementation plans ("implement all waves", "wave N", "finish wave 2", "implementation wave")
- Running unit tests, regression tests, or acceptance suites
- Running multi-module test suites requiring live progress tracking
- Executing CI gate profiles (`deterministic` or `bge`)
- Verifying pytest governance compliance before wave progression
- Monitoring and terminating background test processes

## Normative Testing & Wave Governance SSOT

All wave progression requirements, pytest run classes, and progress reporting rules are canonically governed by:
- [`.agents/rules/wave-progression.md`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/rules/wave-progression.md) (Sequential wave locks, Class A single-test debug runs vs Class B wave receipt runs, mandatory `--timeout=60`, prohibited unbounded pytest patterns)
- [`.agents/rules/e2e-and-edge-case-coverage.md`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/rules/e2e-and-edge-case-coverage.md) (Mandatory E2E and adversarial edge-case coverage)
- [`.agents/rules/progress-indicators.md`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/rules/progress-indicators.md) (Live progress tracking requirement)
- [`.agents/rules/engineering-output.md`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/rules/engineering-output.md) (Turn completion reports)

This skill provides operational execution commands for running governed test suites and CI profiles.

## Mandatory Progress Tracking ([`run_tests_with_progress.py`](file:///Users/amitayer/Git/decision-intelligence-engine/tools/run_tests_with_progress.py))

Multi-module test suites must be executed through the progress runner:

```bash
python tools/run_tests_with_progress.py \
  --test-files tests/test_graph_retrieval_unit.py tests/test_graph_delta_unit.py \
  --timeout 60
```

### Display Features
- **Live run_tests_with_progress.py**: `[████████░░░░░░░░]` showing percentage and test count (`[45/150]`).
- **Execution Metrics**: Elapsed time, estimated time remaining (ETA), throughput rate.
- **Real-Time Counters**: Pass, Fail, Error, and Skip tallies updated live.

## CI Gate Execution ([`run_ci_gates.py`](file:///Users/amitayer/Git/decision-intelligence-engine/tools/run_ci_gates.py))

Run CI gate profiles with gate-level stage tracking:

```bash
# Deterministic governance gates (pre-push profile)
python tools/run_ci_gates.py --profile deterministic

# E2E and adversarial acceptance suite (full suite, ~15 min, provider-free)
python tools/run_ci_gates.py --profile e2e

# Full BGE retrieval qualification profile (manual dispatch only, self-hosted runner)
python tools/run_ci_gates.py --profile bge
```

## E2E & Edge-Case Coverage Verification

Mandatory edge-case categories (≥3 required per adversarial test file) and CI profile enrollment rules are governed by [`.agents/rules/e2e-and-edge-case-coverage.md`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/rules/e2e-and-edge-case-coverage.md).

Verify E2E and testing gap scores prior to main promotion:
```bash
python tools/audit_testing_gaps.py --min-e2e-score 80 --fail-on-threshold
```

## Pytest Governance Compliance Check

Verify that all test files conform to repo test standards:

```bash
python tools/check_pytest_compliance.py
```

## Path-Scoped Tier 1 Cached Stage Replay Gate

For branches modifying transcript synthesis or pipeline layers (`dual_pipeline/transcript_*`, `dual_pipeline/canonical_pipeline/`, `pipeline_a_intake/`, `pipeline_b_extraction/`):

```bash
# Verify hermetic Tier 1 cached stage replay in an ephemeral sandbox
python tools/verify_tier1_cached_replay.py

# With path scoping (bypasses on unrelated paths)
python tools/verify_tier1_cached_replay.py docs/readme.md
```

## Background Task Watchdog

If any command is sent to the background:
1. Check task status immediately: `manage_task status`
2. If the task exceeds its expected execution window (pytest: 2 min max; other scripts: 5 min max), terminate immediately: `manage_task kill`
3. Never leave background zombie processes running across turns.

## Related Files

- Engineering output policy: [`.agents/rules/engineering-output.md`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/rules/engineering-output.md)
- Progress runner: [`run_tests_with_progress.py`](file:///Users/amitayer/Git/decision-intelligence-engine/tools/run_tests_with_progress.py)
- CI gate runner: [`run_ci_gates.py`](file:///Users/amitayer/Git/decision-intelligence-engine/tools/run_ci_gates.py)
- Compliance checker: [`check_pytest_compliance.py`](file:///Users/amitayer/Git/decision-intelligence-engine/tools/check_pytest_compliance.py)
- E2E coverage rule: [`.agents/rules/e2e-and-edge-case-coverage.md`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/rules/e2e-and-edge-case-coverage.md)
- Progress rule: [`.agents/rules/progress-indicators.md`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/rules/progress-indicators.md)
- Wave progression rule: [`.agents/rules/wave-progression.md`](file:///Users/amitayer/Git/decision-intelligence-engine/.agents/rules/wave-progression.md)
