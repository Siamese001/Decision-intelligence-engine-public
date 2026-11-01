from config.credentials import CredentialManager

#!/usr/bin/env python3
"""
scripts/analyze_output_hierarchy_luna.py

Executes a forensic analysis on the entire output/ folder structure using
OpenAI gpt-5.6-luna (reasoning_effort='xhigh') to evaluate conformance with
governed hierarchy and artifact contracts defined in AGENTS.md and
RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CredentialManager.ensure_ssot_env_loaded()
OUTPUT_DIR = ROOT / "output"

DATE_REGEX = re.compile(r"^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])-(19|20)\d{2}$")
ART01_REGEX = re.compile(r"^01_.*_hitl_resolved_transcript\.md$")
ART02_REGEX = re.compile(r"^02_.*_executive_recap\.md$")
ART03_REGEX = re.compile(r"^03_.*_canonical_signal_projection\.json$")


def build_inventory():
    inventory = {
        "root_aux_dirs": [],
        "root_files": [],
        "projects": {},
    }

    for item in sorted(OUTPUT_DIR.iterdir()):
        if item.name.startswith("."):
            continue
        if item.is_file():
            inventory["root_files"].append(item.name)
        elif item.is_dir():
            if item.name == "transcript_cache":
                inventory["root_aux_dirs"].append(item.name)
                continue

            proj_data = {}
            for sub in sorted(item.iterdir()):
                if sub.name.startswith("."):
                    continue
                if sub.is_file():
                    proj_data[sub.name] = {"type": "FLAT_FILE_ERROR", "size": sub.stat().st_size}
                elif sub.is_dir():
                    date_name = sub.name
                    f_info = {
                        "is_valid_date_format": bool(DATE_REGEX.match(date_name)),
                        "art01": [],
                        "art02": [],
                        "art03": [],
                        "qa_gates_report": None,
                        "llm_judge_evaluation": None,
                        "final_release_decision": None,
                        "intake_package": None,
                        "auxiliary_files": [],
                        "subdirectories": [],
                    }
                    for subitem in sorted(sub.iterdir()):
                        if subitem.name.startswith("."):
                            continue
                        if subitem.is_dir():
                            f_info["subdirectories"].append(subitem.name)
                        elif subitem.is_file():
                            fn = subitem.name
                            if ART01_REGEX.match(fn):
                                f_info["art01"].append(fn)
                            elif ART02_REGEX.match(fn):
                                f_info["art02"].append(fn)
                            elif ART03_REGEX.match(fn):
                                f_info["art03"].append(fn)
                            elif fn == "qa_gates_report.json":
                                f_info["qa_gates_report"] = fn
                            elif fn == "llm_judge_evaluation.json":
                                f_info["llm_judge_evaluation"] = fn
                            elif fn == "final_release_decision.json":
                                f_info["final_release_decision"] = fn
                            elif "intake_package" in fn:
                                f_info["intake_package"] = fn
                            else:
                                f_info["auxiliary_files"].append(fn)
                    proj_data[date_name] = f_info
            inventory["projects"][item.name] = proj_data
    return inventory


SYSTEM_PROMPT = """You are a Senior Principal AI Systems Architect and Governance Auditor for the Apex Global Enterprises (NEE) Decision Intelligence Engine.
Your task is to conduct an authoritative, exhaustive architectural and governance compliance audit of the entire `output/` directory tree.

GOVERNANCE CONTRACT RULES (AGENTS.md & RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md):
1. Hierarchy Structure:
   - Root: `output/`
   - Level 1: `<project_name>/` (strict lowercase snake_case, e.g. `project_nexus`, `project_atlas`, `nea`).
   - Level 2: `<meeting_date>/` (strictly `MM-DD-YYYY`, e.g. `08-28-2026`).
   - Sibling sub-workstreams must share parent project folder (e.g. `nexus_reid_nea` resides in `project_nexus/08-28-2026/`).
   - NO symlinks anywhere.
   - NO flat file dumps directly under `output/` or `output/<project_name>/`.
   - Allowed root auxiliary directories: `transcript_cache/`.

2. Deliverable Artifact Contract:
   Every meeting date subfolder MUST strictly contain the 3 canonical deliverables:
   - `01_<meeting_name>_<date>_<time>_hitl_resolved_transcript.md` (Forensic transcript with timestamps, ASR cleanup, Ambiguity Lock Register)
   - `02_<meeting_name>_<date>_<time>_executive_recap.md` (Executive synthesis, strict MM/DD/YYYY date, ET time, company-grouped attendees, actionable next steps)
   - `03_<meeting_name>_<date>_<time>_canonical_signal_projection.json` (Canonical graph delta, evidence envelope)
   - Parameterized by `<meeting_name>_<date>_<time>` with date formatted as `MM-DD-YYYY`.

3. Supporting Governance Artifacts (Required for governed release & GraphDB mutation):
   - `qa_gates_report.json` (Deterministic QA verification)
   - `llm_judge_evaluation.json` (Decomposed multi-micro-judge evaluation report)
   - `final_release_decision.json` (Authoritative write decision receipt)
   - `intake_package.json` (or `<name>_intake_package.json` for sealed downstream ingestion)

4. Cleanliness & Hygiene Violations:
   - Residual raw audio (.wav, .m4a), chunks directories, segmentation logs, png files, or scratch files left in release directories.
   - Non-canonical report formats (e.g., .md instead of .json for qa/judge/release receipts).
   - Duplicate or unversioned deliverables (e.g., multiple 01/02/03 versions in the same date folder).
   - In-place `archive_*` subdirectories inside date folders instead of global or isolated archival paths.
   - Date naming format anomalies (e.g. YYYYMMDD or MMDDYYYY without hyphens).
   - Missing Deliverable 02 (executive recap) or 03 (graph projection).

AUDIT REPORT REQUIREMENTS:
Produce an exhaustive, highly structured markdown report containing:
1. Executive Summary & Compliance Scorecard across all projects and meeting subfolders.
2. Comprehensive Project-by-Project Audit Breakdown table with columns:
   - Project Name
   - Meeting Date Subfolder
   - Canonical Deliverables Status (01, 02, 03)
   - Governance Receipts Status (QA Gate, Judge Eval, Release Decision, Intake Package)
   - Hygiene & Cleanliness (Extraneous files, audio dumps, archives)
   - Compliance Verdict (COMPLIANT, PARTIAL, NON-COMPLIANT)
3. Granular Findings & Detailed Violations:
   - Categorize by Severity: Critical, High, Medium, Low / Informational.
   - Specifically call out missing artifacts (e.g. missing 02 in `project_nexus/08-18-2026/`), date parameter formatting anomalies, leftover audio/chunk files, unparsed markdown reports, and archival clutter.
4. Actionable Remediation Roadmap:
   - Clear, step-by-step instructions to bring 100% of subfolders into full compliance with the governed hierarchy.
"""


def main():
    print("Collecting output/ folder hierarchy inventory...")
    inventory = build_inventory()
    inventory_json = json.dumps(inventory, indent=2)
    print(f"Inventory collected. Projects audited: {len(inventory['projects'])}")

    client, _ = CredentialManager.get_openai_client(allow_mock=False)
    from dual_pipeline.model_registry import ModelRegistry, ModelTier
    def_m, def_e = ModelRegistry.resolve_tier_model(ModelTier.FRONTIER_HIGH_REASONING)
    print(f"Invoking OpenAI {def_m} (reasoning_effort='{def_e or 'xhigh'}')...")

    user_prompt = f"""Here is the complete inventory of the `output/` folder structure:

```json
{inventory_json}
```

Perform the comprehensive governance and hierarchy audit now according to the system instructions."""

    response = client.chat.completions.create(
        model=def_m,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        reasoning_effort=def_e or "xhigh",
    )

    report_content = response.choices[0].message.content
    out_file = ROOT / "output_hierarchy_audit_report.md"
    out_file.write_text(report_content, encoding="utf-8")
    print(f"Audit completed successfully! Report written to: {out_file}")
    print("\n" + "="*80)
    print(report_content[:2000] + "\n...[truncated for console]...")


if __name__ == "__main__":
    main()
