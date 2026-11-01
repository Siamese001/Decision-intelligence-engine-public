#!/usr/bin/env python3
"""
tools/lint_contract_parity.py

Mechanical Contract Parity Gatekeeper.
Enforces zero configuration drift between:
1. Declarative OutputContracts (SSOT in dual_pipeline/prompts/contracts/).
2. Stage Generation Prompts (config/prompts/synthesis/stages/).
3. Judge Evaluation Prompts (governance/decision_intelligence/).
4. Deterministic QA Gates (dual_pipeline/llm_judge_core/recap_checks.py).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.prompts.contracts import (
    OUTPUT1_CONTRACT,
    OUTPUT2_CONTRACT,
    OUTPUT3_CONTRACT,
    OutputContract,
)
from dual_pipeline.prompts.registry import CANONICAL_PROMPT_REGISTRY

CONTRACT_STAGES_MAP: Dict[int, Tuple[OutputContract, str, str]] = {
    1: (OUTPUT1_CONTRACT, "STAGE_01_FORENSIC", "JUDGE_FORENSIC"),
    2: (OUTPUT2_CONTRACT, "STAGE_02_RECAP", "JUDGE_RECAP"),
    3: (OUTPUT3_CONTRACT, "STAGE_03_GRAPH", "JUDGE_GRAPH"),
}


def _check_point_conservation(contract: OutputContract) -> List[str]:
    """Verify that rubric dimension points sum to exactly 100."""
    total = sum(d.max_points for d in contract.rubric_dimensions)
    if total != 100:
        return [f"Output {contract.output_id}: Rubric dimensions sum to {total} points (must be exactly 100)."]
    return []


def _check_section_presence(section_name: str, spec: Any, prompt_text: str, stage_key: str) -> List[str]:
    """Check if a section header or any alias is present in the prompt text."""
    p_lower = prompt_text.lower()
    candidates = [section_name.lower(), spec.header.lower()]
    if hasattr(spec, "aliases") and spec.aliases:
        candidates.extend(a.lower() for a in spec.aliases)

    if any(c in p_lower for c in candidates):
        return []
    return [f"{stage_key}: Section '{section_name}' (header '{spec.header}') not found in stage prompt."]


def _check_section_parity(contract: OutputContract, stage_key: str) -> List[str]:
    """Verify that all contract sections are documented in the stage prompt."""
    prompt_text = CANONICAL_PROMPT_REGISTRY.get_prompt(stage_key)
    defects: List[str] = []
    for sec_name, spec in contract.sections.items():
        defects.extend(_check_section_presence(sec_name, spec, prompt_text, stage_key))
    return defects


def _check_rubric_parity(contract: OutputContract, judge_key: str) -> List[str]:
    """Verify that all contract rubric dimensions are present in the judge prompt."""
    judge_text = CANONICAL_PROMPT_REGISTRY.get_prompt(judge_key)
    defects: List[str] = []
    for dim in contract.rubric_dimensions:
        if dim.key not in judge_text:
            defects.append(f"{judge_key}: Rubric dimension key '{dim.key}' not found in judge prompt.")
    return defects


def _check_anti_string_replace(repo_root: Path) -> List[str]:
    """Verify zero prohibited string replacement hacks on archetypes exist in prompt contracts."""
    defects: List[str] = []
    contracts_dir = repo_root / "dual_pipeline" / "prompts" / "contracts"
    pattern = re.compile(r"\.replace\(\s*['\"](?:meeting_)?archetype")
    for py_file in contracts_dir.glob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        for i, line in enumerate(content.splitlines(), 1):
            if pattern.search(line):
                defects.append(
                    f"{py_file.name}:{i}: Prohibited archetype string replacement hack detected."
                )
    return defects


def _check_qa_gate_pattern_alignment() -> List[str]:
    """Verify that Output 2 contract prohibited patterns align with QA gate checks."""
    from dual_pipeline.llm_judge_core.recap_checks import WORD_SALAD_PATTERNS
    from dual_pipeline.transcript_qa_core.appendix_standard import FORBIDDEN_DISCLAIMER_PATTERNS
    defects: List[str] = []
    ws_str = WORD_SALAD_PATTERNS.pattern.lower()
    disc_str = FORBIDDEN_DISCLAIMER_PATTERNS.pattern.lower()
    for p in OUTPUT2_CONTRACT.prohibited_patterns:
        p_lower = p.lower().strip()
        p_esc = re.escape(p_lower)
        in_ws = (
            p_lower in ws_str
            or p_esc in ws_str
            or any(word in ws_str for word in p_lower.split() if len(word) > 5)
        )
        in_disc = (
            p_lower in disc_str
            or p_esc in disc_str
            or any(word in disc_str for word in p_lower.split() if len(word) > 5)
        )
        if not (in_ws or in_disc):
            defects.append(f"Output 2 Contract prohibited pattern '{p}' not covered in QA gate pattern registries.")
    return defects


def _check_l0_prompt_contract_engine_parity() -> List[str]:
    """Verify that L0 prompt contracts align with OutputContracts and pass integrity validation."""
    from dual_pipeline.l0.prompt_contract import CANONICAL_PROMPT_CONTRACTS
    defects: List[str] = []
    for out_id, contract in CANONICAL_PROMPT_CONTRACTS.items():
        ok, issues = contract.validate_integrity()
        if not ok:
            for issue in issues:
                defects.append(f"L0 Output {out_id} PromptContract integrity failure: {issue}")
    return defects


def run_contract_parity_checks(verbose: bool = False) -> Tuple[bool, List[str]]:
    """Execute all contract-prompt-judge parity checks."""
    all_defects: List[str] = []

    # 1. Anti-string replace invariant
    all_defects.extend(_check_anti_string_replace(ROOT))

    # 2. Output 1-3 Parity Checks
    for out_id, (contract, stage_key, judge_key) in CONTRACT_STAGES_MAP.items():
        all_defects.extend(_check_point_conservation(contract))
        all_defects.extend(_check_section_parity(contract, stage_key))
        all_defects.extend(_check_rubric_parity(contract, judge_key))

    # 3. QA gate pattern alignment
    all_defects.extend(_check_qa_gate_pattern_alignment())

    # 4. L0 Prompt Contract Engine Parity
    all_defects.extend(_check_l0_prompt_contract_engine_parity())

    passed = len(all_defects) == 0
    return passed, all_defects


def main() -> int:
    parser = argparse.ArgumentParser(description="Mechanical Contract-Prompt-Judge Parity Linter")
    parser.add_argument("--check", action="store_true", help="Run parity checks and exit non-zero on failure")
    parser.add_argument("--verbose", action="store_true", help="Print detailed diagnostic info")
    args = parser.parse_args()

    passed, defects = run_contract_parity_checks(verbose=args.verbose)
    if not passed:
        print("=" * 80)
        print("❌ CONTRACT PARITY LINTER FAILED:")
        for d in defects:
            print(f"  • {d}")
        print("=" * 80)
        return 1

    print("=" * 80)
    print("✅ PASSED: Contract-Prompt-Judge Parity Verified (0 defects across Outputs 1-3).")
    print("   Zero config drift between declarative contracts, stage prompts, and judge rubrics.")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())
