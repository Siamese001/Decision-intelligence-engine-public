# Daily Governance Harmonization Sweep — Scheduled Prompt

## Purpose
This prompt runs on a daily cron schedule (`0 6 * * *` — 6:00 AM ET) to detect
governance drift, duplicated assumptions, and inconsistent thresholds across the
entire NEE governance surface before they compound into silent rule violations.

## Prompt for `/schedule` Registration

```
CronExpression: 0 6 * * *
IsDaemon: true
```

### Prompt Text

```
Daily Governance Harmonization Sweep — Automated Reconciliation Audit

You are running the daily governance harmonization audit for the NEE
Decision Intelligence Engine repository.

## Step 1: Read the Skill
Read `.agents/skills/governance-harmonizer/SKILL.md` to understand the
full detection taxonomy and known baseline inconsistencies.

## Step 2: Execute the Scanner
Run the automated scanner in dry-run mode:

```bash
python tools/audit_governance_harmonization.py \
  --output-dir reports/governance-harmonization \
  --fix-mode dry-run
```

## Step 3: Analyze the Report
Read the generated markdown report from `reports/governance-harmonization/`.

## Step 4: Cross-Reference Against Known Baseline
Compare findings against the known baseline in the SKILL.md. Flag any:
- **NEW findings** not in the baseline (regression or new drift)
- **RESOLVED findings** that were in the baseline but are now clean
- **ESCALATED findings** that worsened since last scan

## Step 5: Targeted Deep Scans
For any CRITICAL findings, perform targeted verification:

### 5a. Threshold Drift Verification
For each drifted threshold:
1. Identify the SSOT file and line
2. List every file containing the stale value with exact line numbers
3. Verify the SSOT value matches `dual_pipeline/parameters.py` (code SSOT)

### 5b. Taxonomy Alignment Verification
For each taxonomy mismatch:
1. Quote the SSOT definition verbatim
2. Quote the divergent definition verbatim
3. Determine which is authoritative (AGENTS.md wins ties)

### 5c. Duplication Heat Map
For each duplicated block appearing in 3+ files:
1. Compute content similarity (exact match, near-match, or paraphrase)
2. Identify which file is the SSOT
3. Recommend whether downstream should reference or inline

## Step 6: Produce Harmonization Report Artifact
Create a concise artifact summarizing:

1. **Scan Metadata**: timestamp, files scanned, scanner version
2. **Finding Summary Table**:
   | # | Severity | Category | Title | Files Affected | Status vs Baseline |
3. **CRITICAL Findings Detail**: Each with exact file:line citations and
   proposed fix (do NOT auto-apply)
4. **Drift Trend**: Is governance drift increasing, stable, or decreasing
   vs prior reports in `reports/governance-harmonization/`?
5. **Recommended Actions**: Prioritized list of fixes for human review

## Step 7: Constraints
- **NO EXECUTION of fixes** — report only
- **NO modifications** to any governance file
- Do NOT modify AGENTS.md, rules, skills, hooks, or tools
- Present findings as a read-only audit artifact
- If CRITICAL count > 0, explicitly flag for human review
```

## Registration Command

To register this as a recurring daily schedule in Antigravity, use:

```
/schedule
```

Then provide:
- **Schedule**: `0 6 * * *` (daily at 6 AM)
- **Prompt**: The full prompt text from the "Prompt Text" section above
- **IsDaemon**: `true` (independent standing job)

## Manual Trigger

To run a one-off harmonization sweep without waiting for the schedule:

```
/goal Run the governance harmonization sweep from .agents/skills/governance-harmonizer/SKILL.md — execute the scanner, analyze results, and produce the audit artifact. No fixes, report only.
```

## What Gets Scanned

| Surface | File Pattern | Count |
| :--- | :--- | :--- |
| Root governance | `AGENTS.md` | 1 |
| Decomposed rules | `.agents/rules/*.md` | 30 |
| Skill cheatsheets | `.agents/skills/*/SKILL.md` | 23 |
| Plugin skills | `.agents/plugins/*/skills/*/SKILL.md` | 2 |
| Lifecycle hooks | `.agents/hooks.json` | 1 (29 hooks) |
| Native git hooks | `.githooks/*` | 8 |
| CI gate runners | `tools/*.py` | 40+ |
| Governance docs | `governance/**/*.md`, `governance/**/*.json` | 50+ |
| **Total** | | **~154 files** |

## Detection Categories

| Category | What It Finds | Severity |
| :--- | :--- | :--- |
| **Threshold Drift** | Numeric constants diverging from SSOT | CRITICAL/WARNING |
| **Definition Duplication** | Same governance block copy-pasted in 3+ files | WARNING |
| **Term Overloading** | Same word meaning different things in different files | INFO |
| **Taxonomy Misalignment** | Stage labels, enums, or subsection lists that disagree | CRITICAL/WARNING |
| **Missing Back-References** | Concepts in rules/skills absent from AGENTS.md | INFO |
| **Unenumerated Sets** | References to sets (e.g. "4 receipts") without listing members | WARNING |

## Exit Criteria

The daily sweep is considered **clean** when:
- Zero CRITICAL findings
- Zero new WARNING findings vs prior baseline
- All known baseline items are either resolved or explicitly accepted

## Escalation

If CRITICAL findings persist for 3+ consecutive daily runs without resolution,
the sweep will include an explicit operator alert recommending a dedicated
harmonization PR branch.
