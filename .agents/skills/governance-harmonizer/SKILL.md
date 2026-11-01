---
name: governance-harmonizer
description: Reconciliation auditor scanning governance files, rules, skills, hooks, and CI gates for drift and duplicate definitions. Emits harmonization reports.
---

# Governance Harmonizer

Daily automated reconciliation of assumptions, constants, definitions, and
terminology across the entire NEE governance surface.

## When to Use

- **Daily scheduled sweep**: Runs automatically via `/schedule` cron to
  detect governance drift before it compounds.
- **After editing governance files**: Run after modifying `AGENTS.md`,
  any `.agents/rules/*.md`, `.agents/skills/*/SKILL.md`, or
  `.agents/hooks.json` to verify cross-surface consistency.
- **Before PR promotion**: As a pre-merge validation that no governance
  inconsistencies exist in the branch.

## What It Detects

### 1. Threshold Drift
Numeric constants and percentages that diverge across governance files.

**Known drift vectors** (as of initial audit):

| Constant | SSOT Value | SSOT File | Status |
| :--- | :--- | :--- | :--- |
| HITL Ambiguity Margin | **20% (inclusive)** | `config/runtime/parameters.json` | ✅ Consistent across `parameters.py`, `parameters.json`, `hitl-decisions.md` |
| HITL Entity Clarification | **15% (inclusive)** | `governance/policy_registry.yaml` | ✅ Consistent for domain D6 entity disambiguation |
| E2E Test Coverage Score | **80.0%** | `governance/policy_registry.yaml` | ✅ Consistent across `pyproject.toml`, `e2e-and-edge-case-coverage.md` |
| Module Size Budget | 400 lines | `governance/policy_registry.yaml` | ✅ Consistent |
| Retrieval Latency Cap | < 7,500 ms | `config/runtime/parameters.json` | ✅ Consistent |
| Pytest Timeout | `--timeout=60` | `governance/policy_registry.yaml` | ✅ Consistent across `pyproject.toml`, `wave-progression.md` |

### 2. Definition Duplication
Near-verbatim governance blocks repeated across 3+ files instead of
referencing a single SSOT.

**Known duplication clusters**:
- Ingestion Directive Lifecycle (AGENTS.md + ingestion-lifecycle.md + nee-ingestion/SKILL.md)
- Progress Bar Requirements (AGENTS.md + progress-indicators.md + nee-testing-protocol)
- Wave Progression Standard (AGENTS.md + wave-progression.md + 4 skills)
- BGE-M3 Retrieval Requirements (12+ files)
- HITL Atomic Presentation Rules (AGENTS.md + hitl-decisions.md + 6 skills)

### 3. Term Overloading
Polysemous governance terms used with conflicting semantics.

| Term | Meaning A | Meaning B |
| :--- | :--- | :--- |
| "wave" | Engineering implementation plan phase (Wave 1-N) | Strategic reasoning stage (W1-W7) |
| "completion gate" | Intake directive terminal command | Wave plan subsection header |
| "receipt" | 14+ distinct receipt taxonomies | — |

### 4. Taxonomy Misalignment
Stage labels, subsection counts, or enum values that disagree between
SSOT and downstream files.

**Known misalignments**:
- Intake Stage Labels: `AGENTS.md` labels L1="Identity", L0="Routing";
  `nee-ingestion/SKILL.md` labels L1="Normalization & planning", L0="Identity resolution"
- Wave Subsections: `AGENTS.md` requires 4; `wave-progression.md` lists only 3
  (omits "Objective & Scope")
- Temporal JSON Format: `temporal-representation-ssot.md` mandates ISO-8601;
  `AGENTS.md` line 109 and output contracts mandate MM/DD/YYYY

### 5. Missing Back-References
Concepts defined in downstream rules/skills but absent from AGENTS.md.

**Known gaps**: `learning-change-proposal`, `ParameterRegistry`, `graph_scout`

## Execution

### Manual Run
```bash
python tools/audit_governance_harmonization.py --output-dir reports/governance-harmonization
```

### Scheduled Daily Run (via Antigravity /schedule)
```
Prompt: "Run the daily governance harmonization audit:
  1. Execute: python tools/audit_governance_harmonization.py --output-dir reports/governance-harmonization
  2. Review the markdown report for any CRITICAL findings
  3. For each CRITICAL finding, summarize the exact files and lines that need updating
  4. Present findings as an artifact — do NOT auto-fix"
CronExpression: "0 6 * * *"
```

### CI Integration
Add to `governed-ci.yml` as a non-blocking informational job:
```yaml
governance-harmonization:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - run: python tools/audit_governance_harmonization.py --json-only
```

## Output

Each run produces two files in `reports/governance-harmonization/`:
- `harmonization_report_<timestamp>.json` — structured machine-readable report
- `harmonization_report_<timestamp>.md` — human-readable markdown summary

## Known Inconsistencies (Baseline 2026-10-01)

### RESOLVED: HITL Ambiguity Margin SSOT (20% inclusive)
The threshold is governed by SSOT accessor `needs_operator_decision()` in `dual_pipeline/parameters.py`
(20% inclusive in `config/runtime/parameters.json`):

1. `.agents/rules/wave-progression.md` (line 38)
2. `.agents/rules/agent-routing.md` (line 19)
3. `.agents/rules/transcript-synthesis.md` (line 41)
4. `.agents/skills/nee-governance/SKILL.md` (lines 19, 42, 45)
5. `.agents/skills/nee-hitl-governance/SKILL.md` (lines 4, 16, 44-47, 59)
6. `.agents/skills/nee-orchestration/SKILL.md` (lines 49, 79, 97)
7. `.agents/skills/nee-transcript-synthesis/SKILL.md` (line 184)
8. `.agents/skills/agent-subagent-orchestration/SKILL.md` (line 84)

### CRITICAL: Temporal JSON Format Contradiction
- `temporal-representation-ssot.md` + `AGENTS.md` line 179: ISO-8601
- `AGENTS.md` line 109 + output contracts + `gate3.py`: MM/DD/YYYY

### RESOLVED: E2E Coverage Score (80.0%)
- `governance/policy_registry.yaml`: `INV-MIN-E2E-COVERAGE-SCORE` = 80.0%
- `pyproject.toml` [tool.nee.budgets]: `min_e2e_coverage_score` = 80.0%
- `e2e-and-edge-case-coverage.md`: `audit_testing_gaps.py` score ≥ 80%

### RESOLVED: Wave Subsection Count (4 Subsections)
- `governance/policy_registry.yaml` & `wave-progression.md`: 4 required subsections (Objective & Scope, Milestones & Deliverables, Acceptance Criteria, Runtime Receipt & Completion Gate).
- `tools/validate_implementation_plan.py`: Checks all 4 canonical subsections.

### WARNING: Intake Stage Label Taxonomy
- `AGENTS.md` + `unified-agent-architecture.md`: L1="Identity", L0="Routing"
- `nee-ingestion/SKILL.md`: L1="Normalization & planning", L0="Identity resolution"

## Architecture

```
governance/policy_registry.yaml (Root SSOT)
  ├── AGENTS.md                (Generated compact entry point index <=12KB)
  ├── .agents/rules/*.md       (Decomposed rule files — reference policy_registry.yaml)
  ├── .agents/skills/*/SKILL.md (Operational cheatsheets — reference rules, not duplicate)
  ├── .agents/hooks.json        (Lifecycle hooks — enforce rules at runtime)
  ├── .githooks/*               (Native git hooks — enforce at commit/push)
  ├── tools/*                   (CI gates & linters — enforce programmatically)
  └── config/*                  (Runtime parameters, model registry, credentials)
```

**Information flow**: policy_registry.yaml → AGENTS.md / rules → skills (reference, don't duplicate)
**Enforcement flow**: hooks.json + .githooks + tools
