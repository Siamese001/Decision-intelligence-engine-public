"""Report rendering utilities for governance harmonization."""
from __future__ import annotations

from .common import HarmonizationReport


def render_markdown(report: HarmonizationReport) -> str:
    """Render human-readable markdown summary."""
    lines = [
        "# Governance Harmonization Report",
        "",
        f"**Scan Time**: {report.scan_timestamp}",
        f"**Files Scanned**: {report.total_files_scanned}",
        f"**Total Findings**: {report.summary.get('total', 0)}",
        "",
        "## Summary",
        "",
        "| Category | Count |",
        "| :--- | ---: |",
    ]
    for cat in ["threshold_drift", "duplication", "overloading", "taxonomy", "missing_backref"]:
        lines.append(f"| {cat.replace('_', ' ').title()} | {report.summary.get(cat, 0)} |")
    lines.append(f"| **CRITICAL** | {report.summary.get('severity_CRITICAL', 0)} |")
    lines.append(f"| **WARNING** | {report.summary.get('severity_WARNING', 0)} |")
    lines.append(f"| **INFO** | {report.summary.get('severity_INFO', 0)} |")
    lines.append("")

    # Group findings by severity
    for sev in ["CRITICAL", "WARNING", "INFO"]:
        findings = [f for f in report.findings if f.severity == sev]
        if not findings:
            continue
        lines.append(f"## {sev} Findings")
        lines.append("")
        for f in findings:
            lines.append(f"### {f.title}")
            lines.append("")
            lines.append(f"**Category**: `{f.category}` | **SSOT**: `{f.ssot_file}` → `{f.ssot_value}`")
            lines.append("")
            lines.append(f"{f.description}")
            lines.append("")
            if f.affected_files:
                lines.append("| File | Line | Current | Expected |")
                lines.append("| :--- | ---: | :--- | :--- |")
                for af in f.affected_files[:10]:  # Cap display
                    lines.append(
                        f"| `{af['path']}` | {af.get('line', '—')} | "
                        f"{af.get('current_value', '—')[:60]} | {af.get('expected_value', '—')[:60]} |"
                    )
                if len(f.affected_files) > 10:
                    lines.append(f"| ... | | +{len(f.affected_files) - 10} more | |")
                lines.append("")
            if f.proposed_fix:
                lines.append(f"> **Proposed Fix**: {f.proposed_fix}")
                lines.append("")
    return "\n".join(lines)
