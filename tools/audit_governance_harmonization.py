#!/usr/bin/env python3
"""Governance Harmonization Auditor — Daily Reconciliation Scanner.

Scans all governance surface files (AGENTS.md, .agents/rules/*.md,
.agents/skills/*/SKILL.md, .agents/hooks.json, .githooks/*, tools/*) for:

  1. Threshold drift     — numeric constants that diverge across files.
  2. Definition duplication — near-verbatim blocks repeated across tiers.
  3. Term overloading     — polysemous governance terms used with conflicting
                            semantics (e.g. "wave", "completion gate").
  4. Taxonomy misalignment — stage labels, subsection lists, or enum values
                             that disagree between SSOT and downstream files.
  5. Missing back-references — concepts defined in rules/skills but absent
                                from the root AGENTS.md (or vice-versa).

Output: a structured JSON report + human-readable markdown summary.

Usage:
    python tools/audit_governance_harmonization.py [--output-dir DIR] [--fix-mode dry-run|propose]

Exit codes:
    0 — clean (no inconsistencies found)
    1 — inconsistencies detected (report emitted)
    2 — scanner error
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.audit_governance_harmonization_core import (  # noqa: E402
    AGENTS_MD,
    AUTOMATIONS_DIR,
    CONFIG_DIR,
    CONTRACTS_DIR,
    DISCOVERY_SCOPE_EXCLUSIONS,
    DUPLICATION_SIGNATURES,
    EXPECTED_AGENTS_MD_CONCEPTS,
    GITHOOKS_DIR,
    GOVERNANCE_DIR,
    HOOKS_JSON,
    HarmonizationReport,
    MCP_CONFIG_JSON,
    OVERLOADED_TERMS,
    PLUGINS_DIR,
    PROJECT_INSTRUCTIONS_MD,
    PYPROJECT_TOML,
    REPO_ROOT,
    RULES_DIR,
    SKILLS_DIR,
    SKILLS_JSON,
    TAXONOMY_CHECKS,
    THRESHOLD_REGISTRY,
    TOOLS_DIR,
    WORKFLOWS_DIR,
    Finding,
    discover_governance_files,
    read_text_safe,
    relative,
    render_markdown,
    scan_deprecated_tool_names,
    scan_duplication,
    scan_literal_margins,
    scan_missing_backrefs,
    scan_overloading,
    scan_policy_registry_sync,
    scan_replaced_decision_terms,
    scan_taxonomy_alignment,
    scan_threshold_drift,
    scan_unenumerated_receipts,
)

__all__ = [
    "AGENTS_MD",
    "AUTOMATIONS_DIR",
    "CONFIG_DIR",
    "CONTRACTS_DIR",
    "DISCOVERY_SCOPE_EXCLUSIONS",
    "DUPLICATION_SIGNATURES",
    "EXPECTED_AGENTS_MD_CONCEPTS",
    "Finding",
    "GITHOOKS_DIR",
    "GOVERNANCE_DIR",
    "HOOKS_JSON",
    "HarmonizationReport",
    "MCP_CONFIG_JSON",
    "OVERLOADED_TERMS",
    "PLUGINS_DIR",
    "PROJECT_INSTRUCTIONS_MD",
    "PYPROJECT_TOML",
    "REPO_ROOT",
    "RULES_DIR",
    "SKILLS_DIR",
    "SKILLS_JSON",
    "TAXONOMY_CHECKS",
    "THRESHOLD_REGISTRY",
    "TOOLS_DIR",
    "WORKFLOWS_DIR",
    "discover_governance_files",
    "main",
    "read_text_safe",
    "relative",
    "render_markdown",
    "scan_deprecated_tool_names",
    "scan_duplication",
    "scan_literal_margins",
    "scan_missing_backrefs",
    "scan_overloading",
    "scan_policy_registry_sync",
    "scan_replaced_decision_terms",
    "scan_taxonomy_alignment",
    "scan_threshold_drift",
    "scan_unenumerated_receipts",
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Governance Harmonization Auditor — daily reconciliation scanner"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "reports" / "governance-harmonization",
        help="Directory for report output (default: reports/governance-harmonization/)",
    )
    parser.add_argument(
        "--fix-mode",
        choices=["dry-run", "propose"],
        default="dry-run",
        help="dry-run: report only; propose: emit fix proposals (default: dry-run)",
    )
    parser.add_argument(
        "--json-only",
        action="store_true",
        help="Emit JSON report only (suppress markdown)",
    )
    args = parser.parse_args()

    print("🔍 Governance Harmonization Auditor v1.0.0")
    print(f"   Repo root: {REPO_ROOT}")
    print(f"   Output:    {args.output_dir}")
    print()

    # Discover files
    files = discover_governance_files()
    print(f"📂 Discovered {len(files)} governance files")

    # Build report
    report = HarmonizationReport(
        scan_timestamp=datetime.now(timezone.utc).isoformat(),
        repo_root=str(REPO_ROOT),
        total_files_scanned=len(files),
    )

    # Run all scanners
    scanners = [
        ("Threshold Drift", scan_threshold_drift),
        ("Definition Duplication", scan_duplication),
        ("Term Overloading", scan_overloading),
        ("Taxonomy Alignment", scan_taxonomy_alignment),
        ("Missing Back-References", scan_missing_backrefs),
        ("Unenumerated Receipts", scan_unenumerated_receipts),
        ("Deprecated Tool Names", scan_deprecated_tool_names),
        ("Replaced Decision Terms", scan_replaced_decision_terms),
        ("Literal Ambiguity Margins", scan_literal_margins),
        ("Policy Registry Sync", scan_policy_registry_sync),
    ]

    for name, scanner in scanners:
        print(f"  ⏳ Scanning: {name}...")
        pre_count = len(report.findings)
        scanner(files, report)
        delta = len(report.findings) - pre_count
        print(f"     → {delta} finding(s)")

    report.finalize()
    print()

    # Output
    args.output_dir.mkdir(parents=True, exist_ok=True)
    ts_slug = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    # JSON report
    json_path = args.output_dir / f"harmonization_report_{ts_slug}.json"
    json_content = json.dumps(asdict(report), indent=2, default=str)
    json_path.write_text(json_content, encoding="utf-8")
    latest_json = args.output_dir / "harmonization_report_latest.json"
    latest_json.write_text(json_content, encoding="utf-8")
    print(f"📄 JSON report: {json_path}")

    # Markdown report
    if not args.json_only:
        md_path = args.output_dir / f"harmonization_report_{ts_slug}.md"
        md_content = render_markdown(report)
        md_path.write_text(md_content, encoding="utf-8")
        latest_md = args.output_dir / "harmonization_report_latest.md"
        latest_md.write_text(md_content, encoding="utf-8")
        print(f"📄 Markdown report: {md_path}")

    # Summary
    total = report.summary.get("total", 0)
    critical = report.summary.get("severity_CRITICAL", 0)
    warning = report.summary.get("severity_WARNING", 0)
    info = report.summary.get("severity_INFO", 0)

    print()
    if critical == 0 and warning == 0:
        if info == 0:
            print("✅ Clean — no inconsistencies detected")
        else:
            print(f"✅ Pass — 0 CRITICAL, 0 WARNING ({info} informational notice(s))")
        return 0
    else:
        print(f"⚠️  {total} finding(s): {critical} CRITICAL, {warning} WARNING, {info} INFO")
        return 1


if __name__ == "__main__":
    sys.exit(main())
