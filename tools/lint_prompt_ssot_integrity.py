#!/usr/bin/env python3
"""
tools/lint_prompt_ssot_integrity.py

Automated mechanical enforcement validator for Prompt & Judge SSOT Integrity.
Validates:
1. Declarative OutputContracts exist for Outputs 1, 2, and 3.
2. Rubric dimensions sum exactly to 100 points for each contract.
3. Archetype section topologies are coherent (e.g. tech sync forbids consulting wedges).
4. Stage prompts and Judge prompts compile cleanly for all standard archetypes.
5. Canonical prompt registry manifest verification passes without missing files.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Add repository root to path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dual_pipeline.prompts import (
    CANONICAL_PROMPT_REGISTRY,
    OUTPUT1_CONTRACT,
    OUTPUT2_CONTRACT,
    OUTPUT3_CONTRACT,
    MeetingArchetype,
    SectionRequirement,
)
from tools.lint_contract_parity import _check_point_conservation


def validate_contracts() -> list[str]:
    defects: list[str] = []
    contracts = [OUTPUT1_CONTRACT, OUTPUT2_CONTRACT, OUTPUT3_CONTRACT]

    for c in contracts:
        # Check point totals via shared SSOT helper
        defects.extend(_check_point_conservation(c))

        # Check section specs
        if not c.sections:
            defects.append(f"Output {c.output_id} declares zero sections.")

        # Test compilation across archetypes
        for arch in MeetingArchetype:
            try:
                stage_prompt = c.compile_stage_prompt(arch)
                if not stage_prompt or len(stage_prompt) < 50:
                    defects.append(f"Output {c.output_id} stage prompt for {arch.value} too short ({len(stage_prompt)} chars).")
            except Exception as e:
                defects.append(f"Output {c.output_id} failed stage compilation for {arch.value}: {e}")

            try:
                judge_prompt = c.compile_judge_prompt(arch)
                if not judge_prompt or len(judge_prompt) < 50:
                    defects.append(f"Output {c.output_id} judge prompt for {arch.value} too short ({len(judge_prompt)} chars).")
            except Exception as e:
                defects.append(f"Output {c.output_id} failed judge compilation for {arch.value}: {e}")

    # Specific Output 2 Invariants: Single Universal SSOT
    for arch in MeetingArchetype:
        topo = OUTPUT2_CONTRACT.get_section_topology(arch)
        if topo.get("Action & Governance Ledger") != SectionRequirement.MANDATORY:
            defects.append(f"Output 2 contract MUST require 'Action & Governance Ledger' as MANDATORY for {arch.value}.")
        if topo.get("Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown") != SectionRequirement.MANDATORY:
            defects.append(f"Output 2 contract MUST require Appendix as MANDATORY for {arch.value}.")
        if topo.get("Active Technical Spikes & Experimental Inquiries") != SectionRequirement.FORBIDDEN:
            defects.append(f"Output 2 contract MUST forbid retired 'Active Technical Spikes & Experimental Inquiries' for {arch.value}.")

    # Manifest verification
    if not CANONICAL_PROMPT_REGISTRY.verify_manifest():
        defects.append("CANONICAL_PROMPT_REGISTRY.verify_manifest() failed; prompt files missing or empty.")

    # Scheduled Automation Prompts SSOT & Sprawl Verification
    from tools.lint_prompt_ssot import run_prompt_ssot_lint
    ssot_ok, ssot_errors = run_prompt_ssot_lint(ROOT)
    if not ssot_ok:
        defects.extend(ssot_errors)

    # Antigravity Automation Sidecar SSOT Link & Anti-Inlining Verification
    from tools.lint_automation_ssot_links import run_automation_ssot_links_lint
    links_ok, link_errors = run_automation_ssot_links_lint(ROOT, skip_stage_vi_duplicate=True)
    if not links_ok:
        defects.extend(link_errors)

    return defects


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Validates Prompt & Judge SSOT Integrity.")
    parser.add_argument("--sync", action="store_true", help="Synchronize canonical prompts to exports before validation.")
    parser.add_argument("--check", action="store_true", help="Read-only check mode (never mutates tracked files).")
    parser.add_argument("--repo-root", type=Path, default=ROOT, help="Root path of target repository.")
    args = parser.parse_args(argv)

    if args.sync and not args.check:
        from tools.lint_prompt_ssot import sync_prompt_exports
        synced = sync_prompt_exports(args.repo_root)
        if synced:
            print(f"⚡ Synced {len(synced)} prompt(s) to artifacts/exports/scheduled_tasks_hardened/:")
            for s in synced:
                print(f"   - {s}")

    print("Validating Prompt & Judge SSOT Integrity...")
    defects = validate_contracts()
    if defects:
        print(f"[FAIL] Found {len(defects)} prompt/judge SSOT defect(s):")
        for d in defects:
            print(f"  - {d}")
        return 1

    print("[PASS] All OutputContracts and PromptRegistry manifest satisfy SSOT integrity.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
