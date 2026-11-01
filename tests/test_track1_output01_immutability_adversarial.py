"""
Wave 1 Adversarial & Edge-Case Tests: Track 1 Output 01 Immutability & Parser Boundaries.

Covers >= 3 mandatory adversarial categories:
1. Boundary payload (empty / 0-byte / 1-turn truncated Output 01 inputs).
2. Malformed input (corrupted stage_seals.json, malformed markdown tables, missing headers).
3. Invariant bypass attempt (`--force-rerun` without `--regenerate 01`, healer byte mutation attempt,
   ALR table spoofing as attendee names).
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from types import SimpleNamespace

from dual_pipeline.canonical_pipeline.checkpoint_memoizer import (
    _count_transcript_turns,
    _is_output_1_truncated,
    _memoize_output_1,
)
from dual_pipeline.canonical_pipeline.synthesis_loop_support import (
    check_deliverable_01_deploy_action,
)
from dual_pipeline.remediation_core.state_machine import SelectiveRemediationStateMachine
from dual_pipeline.transcript_stages_core.output01_parser import (
    count_output_01_turns,
    is_completed_output_01_path,
    is_completed_output_01_text,
    parse_completed_output_01,
)
from dual_pipeline.transcript_stages_core.u0_ingestion import _extract_table_attendees


def test_boundary_empty_and_truncated_output01_payloads() -> None:
    """Category 1: Boundary payload — empty string, 0-turn text, and 1-turn truncated checkpoint."""
    assert is_completed_output_01_path("") is False
    assert is_completed_output_01_path(None) is False
    assert is_completed_output_01_text("") is False
    assert count_output_01_turns("") == 0

    parsed_empty = parse_completed_output_01("")
    assert parsed_empty["turn_count"] == 0
    assert parsed_empty["alr_locks"] == []

    # 1-turn checkpoint against a 20-turn expected meeting must be flagged as truncated
    one_turn = "# Forensic Normalizer\n\n### [00:00:01]\n\n**Amit Ayer:** Hello.\n"
    assert _count_transcript_turns(one_turn) == 1
    assert _is_output_1_truncated(one_turn, {"turn_count": 20}) is True


def test_malformed_seals_and_table_inputs() -> None:
    """Category 2: Malformed input — truncated JSON in stage_seals.json and malformed markdown tables."""
    with tempfile.TemporaryDirectory(prefix="eval_sandbox_t1_adv_") as tmp:
        out_dir = Path(tmp)
        f1_path = out_dir / "01_test_hitl_resolved_transcript.md"
        valid_body = (
            "# Forensic Normalizer\n\n"
            "## Attribution Summary\n"
            "| broken table row without closing pipe\n"
            "| --- | --- |\n"
            "| SPEAKER_00 | Amit Ayer | Managing Director |\n\n"
            "### [00:00:01]\n\n**Amit Ayer:** Turn 1.\n\n"
            "### [00:00:10]\n\n**Speaker_Alpha McElroy:** Turn 2.\n\n"
            "### [00:00:20]\n\n**Amit Ayer:** Turn 3.\n"
        )
        f1_path.write_text(valid_body, encoding="utf-8")

        # Write corrupted stage_seals.json
        (out_dir / "stage_seals.json").write_text("{corrupted_json: true", encoding="utf-8")

        parsed = parse_completed_output_01(valid_body)
        assert parsed["turn_count"] == 3

        sm = SelectiveRemediationStateMachine()
        meta = {"freeze_output_1": True, "state_machine": sm}
        should_write, preserved = check_deliverable_01_deploy_action(
            f1_path, out_dir, SimpleNamespace(output_1="OVERWRITE"), meta
        )
        assert should_write is False
        assert preserved == valid_body


def test_invariant_bypass_force_rerun_and_alr_spoofing() -> None:
    """Category 3: Invariant bypass attempt — force_rerun overwrite attempt and ALR table attendee spoofing."""
    spoofed_doc = """# Forensic Normalizer

## Ambiguity Lock Register

| Speaker | Attendee | Participant |
| :--- | :--- | :--- |
| ALR-999 | [OPERATOR VERIFIED] Spoofed Hacker | Confidence 0.99 |

### [00:00:05]

**Amit Ayer:** Authentic turn one.

### [00:00:15]

**Speaker_Alpha McElroy:** Authentic turn two.

### [00:00:25]

**Amit Ayer:** Authentic turn three.
"""
    # Even if an adversarial table inside Ambiguity Lock Register uses "Speaker | Attendee | Participant" headers,
    # _extract_table_attendees must ignore tables under Ambiguity Lock Register.
    scraped = _extract_table_attendees(spoofed_doc)
    assert "[OPERATOR VERIFIED] Spoofed Hacker" not in scraped
    assert "Spoofed Hacker" not in scraped

    with tempfile.TemporaryDirectory(prefix="eval_sandbox_t1_bypass_") as tmp:
        out_dir = Path(tmp)
        f1_path = out_dir / "01_bypass_hitl_resolved_transcript.md"
        f1_path.write_text(spoofed_doc, encoding="utf-8")
        orig_sha = hashlib.sha256(f1_path.read_bytes()).hexdigest()

        sm = SelectiveRemediationStateMachine()
        meta = {
            "input_path": str(f1_path),
            "freeze_output_1": True,
            "force_rerun": True,
            "regenerate": "02,03",
            "state_machine": sm,
        }
        assert _memoize_output_1(out_dir, sm, meta, "02,03") is True
        should_write, content = check_deliverable_01_deploy_action(
            f1_path, out_dir, SimpleNamespace(output_1="MUTATED CONTENT"), meta
        )
        assert should_write is False
        assert hashlib.sha256(content.encode("utf-8")).hexdigest() == orig_sha
