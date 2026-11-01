"""Adversarial & edge-case tests for WAV-less resume and turn retention (Wave 3)."""

from typing import Any, Dict

from dual_pipeline.transcript_qa_core.lens_ed_amit_healer import is_lens_ed_amit_1016
from dual_pipeline.transcript_stages_core.organic_signal_scrubber import (
    scrub_organic_signal,
)


def test_boundary_empty_and_zero_turn_payload() -> None:
    """Category 1 (Boundary payload): Empty markdown or zero dialogue turns does not crash."""
    scrubbed = scrub_organic_signal("", preserve_verbatim_turns=True)
    assert scrubbed == ""


def test_malformed_consecutive_same_speaker_short_turns_not_collapsed() -> None:
    """Category 2 (Malformed input): Consecutive short turns by same speaker are preserved with preserve_verbatim_turns."""
    md = (
        "## Complete Verbatim Dialogue\n\n"
        "### [00:01:00]\n\n"
        "**Eduardo More:** Yeah.\n\n"
        "### [00:01:02]\n\n"
        "**Eduardo More:** Right.\n\n"
        "### [00:01:05]\n\n"
        "**Eduardo More:** Because the work order is already scheduled.\n"
    )
    scrubbed = scrub_organic_signal(md, preserve_verbatim_turns=True)
    turns = [ln for ln in scrubbed.splitlines() if ln.strip().startswith("### [")]
    assert len(turns) == 3


def test_regression_unrelated_meeting_does_not_trigger_ed_amit_healer() -> None:
    """Category 3 (Exact production regression): Unrelated meeting metadata never triggers 10/16 Ed/Amit healer."""
    data: Dict[str, Any] = {"meeting_id": "meeting-20261008-sprint-review"}
    meta: Dict[str, Any] = {"title": "10/08/2026 Sprint Review", "output_dir": "output/project_lens/10-08-2026/Sprint"}
    assert is_lens_ed_amit_1016(data, meta) is False
