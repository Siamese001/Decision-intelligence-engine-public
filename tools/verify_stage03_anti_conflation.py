#!/usr/bin/env python3
"""
tools/verify_stage03_anti_conflation.py

Verification tool for Wave 3: Ingestion Pipeline & Signal Projection Hardening.
Verifies Master Constraint C-28, anti-conflation gates, decomposition, and test suite passage.
Produces reports/wave3_stage03_conflation_receipt.json.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dual_pipeline.transcript_stages_core.anti_conflation import (
    ERR_COMPOUND_WORKSTREAM_CONFLATION,
    CompoundWorkstreamConflationError,
    decompose_multi_workstream_utterance,
    is_compound_workstream_reference,
    validate_anti_conflation,
)


def verify_wave3() -> dict:
    repo_root = Path(__file__).resolve().parent.parent

    # 1. Verify prompt constraints
    gov_prompt_path = repo_root / "config/prompts/synthesis/RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md"
    gov_text = gov_prompt_path.read_text(encoding="utf-8")
    gov_has_c28 = "C-28" in gov_text and "Strict Entity-Workstream Disentanglement" in gov_text

    stage03_prompt_path = repo_root / "config/prompts/synthesis/stages/03_graph_extractor.md"
    stage03_text = stage03_prompt_path.read_text(encoding="utf-8")
    stage03_has_c28 = "C-28" in stage03_text and "Strict Workstream Disentanglement" in stage03_text

    # 2. Verify regex detection
    regex_checks = {
        "detects_lens_slash_sherpa": is_compound_workstream_reference("Project LENS / Sherpa"),
        "detects_lens_underscore_sherpa": is_compound_workstream_reference("workstream:lens_sherpa"),
        "allows_clean_lens": not is_compound_workstream_reference("Project LENS"),
        "allows_clean_sherpa": not is_compound_workstream_reference("Project Beacon"),
    }
    regex_valid = all(regex_checks.values())

    # 3. Verify fail-closed behavior on compound entity
    synthetic_fail_closed = False
    try:
        validate_anti_conflation({
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "workstream:lens/sherpa", "name": "Project LENS / Sherpa"}
                ]
            }
        })
    except CompoundWorkstreamConflationError as e:
        if ERR_COMPOUND_WORKSTREAM_CONFLATION in str(e):
            synthetic_fail_closed = True

    # 4. Verify Cameron Mercer 09/18 quote decomposition
    quote = (
        "Whether that's the Sherpa team in clay, whether that's stuff that "
        "we're seeing on their NEA teams like the Lens team and Alex and whatnot."
    )
    envelopes = decompose_multi_workstream_utterance(quote)
    decomposition_valid = (
        len(envelopes) == 2
        and any(e["workstream_id"] == "workstream_project_beacon" for e in envelopes)
        and any(e["workstream_id"] == "workstream_lens" for e in envelopes)
        and sum(e["compound_entity_count"] for e in envelopes) == 0
    )

    # 5. Run pytest test_stage03_anti_conflation_gate.py
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        str(repo_root / "tests" / "test_stage03_anti_conflation_gate.py"),
        "-v",
        "--timeout=60",
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root)
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(repo_root), env=env)
    pytest_passed = (res.returncode == 0)

    checks = {
        "governance_prompt_c28": gov_has_c28,
        "stage03_prompt_c28": stage03_has_c28,
        "regex_detection": regex_valid,
        "synthetic_fail_closed": synthetic_fail_closed,
        "decomposition_valid": decomposition_valid,
        "pytest_suite_passed": pytest_passed,
    }

    all_passed = all(checks.values())

    receipt = {
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "wave": "Wave 3: Ingestion Pipeline & Signal Projection Hardening (Anti-Conflation Rule C-28)",
        "checks": checks,
        "regex_details": regex_checks,
        "pytest_output_summary": res.stdout.splitlines()[-1] if res.stdout else "",
        "pass": all_passed,
    }

    receipt_path = repo_root / "reports" / "wave3_stage03_conflation_receipt.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(f"Wave 3 receipt written to {receipt_path}")
    print(f"Overall status: {'PASS' if all_passed else 'FAIL'}")
    return receipt


if __name__ == "__main__":
    receipt = verify_wave3()
    if not receipt["pass"]:
        sys.exit(1)
