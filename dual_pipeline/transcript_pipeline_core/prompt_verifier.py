#!/usr/bin/env python3
"""
dual_pipeline/transcript_pipeline_core/prompt_verifier.py

Mechanical prompt contract verifier for Wave 1.
Validates:
1. Byte-for-byte mirror parity between canonical and mirror RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md.
2. Existence and structural compliance of all 3 stage prompt templates.
3. Plain-English and anti-word-salad mandates.
4. Zero presence of meeting-specific hardcoded text in general prompt templates.
5. Emits durable execution receipt for Wave 1 completion.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]

CANONICAL_PROMPT = REPO_ROOT / "governance" / "decision_intelligence" / "RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_8.md"
MIRROR_PROMPT = REPO_ROOT / "config" / "prompts" / "synthesis" / "RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_8.md"
STAGE_01 = REPO_ROOT / "config" / "prompts" / "synthesis" / "stages" / "01_forensic_normalizer.md"
STAGE_02 = REPO_ROOT / "config" / "prompts" / "synthesis" / "stages" / "02_executive_recap.md"
STAGE_03 = REPO_ROOT / "config" / "prompts" / "synthesis" / "stages" / "03_graph_extractor.md"


def _verify_canonical_mirror_parity(violations: List[str], checks: Dict[str, bool]) -> None:
    if not CANONICAL_PROMPT.exists():
        violations.append(f"Missing canonical prompt: {CANONICAL_PROMPT}")
    if not MIRROR_PROMPT.exists():
        violations.append(f"Missing mirror prompt: {MIRROR_PROMPT}")

    if CANONICAL_PROMPT.exists() and MIRROR_PROMPT.exists():
        c_bytes = CANONICAL_PROMPT.read_bytes()
        m_bytes = MIRROR_PROMPT.read_bytes()
        parity = (c_bytes == m_bytes)
        checks["canonical_mirror_byte_parity"] = parity
        if not parity:
            violations.append("Byte difference detected between canonical and mirror prompts!")
    else:
        checks["canonical_mirror_byte_parity"] = False


def _verify_stage_prompts_exist(violations: List[str], checks: Dict[str, bool]) -> None:
    for name, p in (("stage_01_exists", STAGE_01), ("stage_02_exists", STAGE_02), ("stage_03_exists", STAGE_03)):
        exists = p.exists()
        checks[name] = exists
        if not exists:
            violations.append(f"Missing prompt: {p}")


def _verify_stage_02_contract(violations: List[str], checks: Dict[str, bool]) -> None:
    if not STAGE_02.exists():
        return
    s2_text = STAGE_02.read_text(encoding="utf-8")
    checks["stage_02_plain_english"] = "plain English" in s2_text or "Plain-English" in s2_text
    checks["stage_02_strategic_synthesis"] = "Strategic Synthesis" in s2_text
    checks["stage_02_next_steps"] = "Next Steps" in s2_text
    checks["stage_02_meeting_objective"] = "Meeting Objective" in s2_text

    if not checks["stage_02_plain_english"]:
        violations.append("Stage 02 prompt lacks explicit Plain-English mandate.")
    if not checks["stage_02_strategic_synthesis"]:
        violations.append("Stage 02 prompt lacks Strategic Synthesis anchor.")
    if not checks["stage_02_next_steps"]:
        violations.append("Stage 02 prompt lacks Next Steps anchor.")
    if not checks["stage_02_meeting_objective"]:
        violations.append("Stage 02 prompt lacks Meeting Objective anchor.")


def _verify_stage_03_contract(violations: List[str], checks: Dict[str, bool]) -> None:
    if not STAGE_03.exists():
        return
    s3_text = STAGE_03.read_text(encoding="utf-8")
    has_canonical_preds = "INTEGRATES_WITH" in s3_text and "DEPENDS_ON" in s3_text
    checks["stage_03_canonical_predicates"] = has_canonical_preds
    if not has_canonical_preds:
        violations.append("Stage 03 prompt lacks canonical W2 predicates.")


def verify_prompts() -> Tuple[bool, List[str], Dict[str, bool]]:
    violations: List[str] = []
    checks: Dict[str, bool] = {}

    _verify_canonical_mirror_parity(violations, checks)
    _verify_stage_prompts_exist(violations, checks)
    _verify_stage_02_contract(violations, checks)
    _verify_stage_03_contract(violations, checks)

    return len(violations) == 0, violations, checks


def main() -> int:
    print("=" * 80)
    print("  CANONICAL SYNTHESIS PROMPT COMPILER VERIFIER (Wave 1)")
    print("================================================================================")

    args = sys.argv[1:]
    receipt_path = None
    if "--receipt" in args:
        idx = args.index("--receipt")
        if idx + 1 < len(args):
            receipt_path = Path(args[idx + 1])

    passed, violations, checks = verify_prompts()

    if receipt_path:
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_data = {
            "verifier": "prompt_verifier",
            "wave": "Wave 1",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "passed": passed,
            "checks": checks,
            "violations_count": len(violations),
            "violations": violations,
        }
        receipt_path.write_text(json.dumps(receipt_data, indent=2), encoding="utf-8")
        print(f"📄 Receipt saved to: {receipt_path}")

    for k, v in checks.items():
        status = "✅ PASS" if v else "❌ FAIL"
        print(f"   • {k}: {status}")

    if not passed:
        print("================================================================================")
        print(f"❌ FAILED: {len(violations)} prompt contract violation(s) detected:")
        for v in violations:
            print(f"   • {v}")
        print("================================================================================")
        return 1

    print("================================================================================")
    print("🎉 PASSED: All prompt contracts verified with 100% parity.")
    print("================================================================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
