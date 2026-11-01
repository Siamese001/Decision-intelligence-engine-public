"""CLI Handler for 'nee executive-brief'."""
from __future__ import annotations

import argparse
import glob
import json
import sys
from typing import List, Optional

from dual_pipeline.canonical_pipeline.cross_bu_whitespace_engine import CrossBUWhiteSpaceEngine
from dual_pipeline.executive_decision_exoskeleton import generate_top_down_csuite_brief


def _resolve_default_db_path() -> str:
    try:
        from pathlib import Path
        from dual_pipeline.core import ROOT
        ap_file = ROOT / ".local" / "dual_pipeline" / "active_pair.json"
        if ap_file.is_file():
            ap_data = json.loads(ap_file.read_text(encoding="utf-8"))
            cand = (ap_file.parent / str(ap_data.get("graph_path", ""))).resolve()
            if cand.is_file():
                return str(cand)
    except Exception:
        pass
    gen_dbs = sorted(glob.glob(".local/dual_pipeline/generations/*/graph.sqlite"), reverse=True)
    return gen_dbs[0] if gen_dbs else ":memory:"


def handle_executive_brief_cli(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="nee executive-brief",
        description="Generate 1-page forward-looking C-suite strategic briefing across Apex Global BUs.",
    )
    parser.add_argument("--stakeholder", "-s", required=True, help="Executive name or role (e.g. 'Mark Lemasney', 'Ben Miron')")
    parser.add_argument("--horizon", choices=["forward", "all", "grounded"], default="forward", help="Horizon perspective")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format")
    parser.add_argument("--audience", choices=["INTERNAL_ADVISORY", "CLIENT_SAFE"], default="INTERNAL_ADVISORY", help="Target audience")
    parser.add_argument("--db", default=None, help="Optional SQLite database path")

    args = parser.parse_args(argv)

    # Resolve database connection
    from storage.connection import create_sqlite_conn

    conn = create_sqlite_conn(args.db or _resolve_default_db_path())
    close_after = True

    try:
        brief = generate_top_down_csuite_brief(
            conn=conn,
            stakeholder_name=args.stakeholder,
            audience=args.audience,
            suppress_l4_noise=(args.horizon != "all"),
            horizon=args.horizon,
        )

        if args.format == "json":
            print(json.dumps(brief, indent=2))
            return 0

        # Render 1-Page Text Executive Brief
        lines = [
            "=" * 80,
            f"EXECUTIVE STRATEGIC BRIEFING: {brief['executive_name'].upper()}",
            f"Business Unit: {brief['primary_bu']} | Reporting Leader: {brief['reporting_leader']}",
            f"One Technology Role: {brief['one_tech_role']}",
            f"Strategic Foresight Ratio: {int(brief['strategic_foresight_ratio'] * 100)}% (Floor >=30%) | Audience: {brief['audience']}",
            "=" * 80,
            "",
            "1. TOP-DOWN BUSINESS UNIT MANDATES (12-18 Month Forward Outcomes):",
        ]
        if brief["top_down_mandates"]:
            for m in brief["top_down_mandates"]:
                lines.append(f"  • [{m.get('horizon', 'H2_18_MONTH')}] {m.get('title', '')}")
                lines.append(f"    Objective: {m.get('objective', '')}")
                if m.get("budget"):
                    lines.append(f"    CapEx/OpEx Baseline: ${m['budget']:,.0f}")
        else:
            lines.append("  • Active Enterprise Strategic Alignment in progress under One Tech umbrella.")

        lines.append("")
        lines.append("2. NOVEMBER 'ONE TECHNOLOGY' TRANSITION TENSIONS & RISKS:")
        if brief["cross_bu_reorg_tensions"]:
            for t in brief["cross_bu_reorg_tensions"]:
                lines.append(f"  • Tension [{t.get('category', 'PLATFORM_OWNERSHIP')}]: {t.get('source_bu', '')} -> {t.get('target_bu', '')} (Severity: {t.get('severity', 4)}/5)")
                lines.append(f"    Description: {t.get('desc', '')}")
                lines.append(f"    Reorg Impact: {t.get('reorg_impact', '')}")
                lines.append(f"    Mitigation: {t.get('mitigation', '')}")
        else:
            lines.append("  • No unmitigated cross-BU transition blockers detected.")

        lines.append("")
        lines.append("3. PROACTIVE WHITE-SPACE EXPANSION PLAYS (Ranked by SOW Value):")
        for idx, p in enumerate(brief["proactive_whitespace_plays"], 1):
            sow_val = f"${p['sow_value']:,.0f}" if p.get("sow_value") else "N/A"
            lines.append(f"  {idx}. {p.get('title', '')} [Est. SOW: {sow_val} | Horizon: {p.get('horizon', 'H1_90_DAY')}]")
            lines.append(f"     Proposed Play: {p.get('play', '')}")
            lines.append(f"     Competitive Threat: {p.get('threat', 'None')}")

        lines.append("")
        lines.append("4. GROUNDED CONTEXT & OPERATIONAL SIGNALS:")
        if brief["grounded_context_facts"]:
            for f in brief["grounded_context_facts"][:5]:
                lines.append(f"  • [{f.get('predicate', 'FACT')}] {f.get('claim', '')}")
        else:
            lines.append("  • Zero unrequested low-level delivery noise (L4 operational bugs suppressed).")

        lines.append("=" * 80)
        print("\n".join(lines))
        return 0
    finally:
        if close_after and conn:
            conn.close()
