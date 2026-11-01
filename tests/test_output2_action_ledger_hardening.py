"""
tests/test_output2_action_ledger_hardening.py

Unit tests for Wave 3: Deliverable 02 Action Ledger Hardening & Timestamp Clamping.
Verifies:
1. Gate 2.2 regex recognition of factual no-actions confirmation statement.
2. Recap healer timestamp clamping and 1-hour offset rectification.
3. Grounded action table synthesis producing factual statement instead of hallucinated actions.
"""

from dual_pipeline.transcript_qa_core.gate2 import (
    _NO_ACTIONS_CONFIRMED_PATTERN,
    _check_typed_action_register,
)
from dual_pipeline.transcript_qa_core.recap_healer import _clamp_locators_to_duration
from dual_pipeline.transcript_qa_core.recap_healer_helpers import _synthesize_grounded_action_table


def test_no_actions_confirmed_pattern_matches_factual_statement():
    """Verify that Gate 2.2 recognizes the factual no-actions declaration."""
    factual_stmt = "No forward commitments or action items were assigned during this technical sync."
    match = _NO_ACTIONS_CONFIRMED_PATTERN.search(factual_stmt)
    assert match is not None, f"Pattern failed to match: '{factual_stmt}'"

    recap_text = f"""# Subject: Apex Global Enterprises - Technical Sync
## Action & Governance Ledger
{factual_stmt}
"""
    res = _check_typed_action_register(recap_text)
    # returns tuple: gate_passed, action_items_count, no_passive_actions, no_unassigned_owners, failures, action_status_rationale
    passed = res[0]
    count = res[1]
    failures = res[4]
    rationale = res[5]
    assert passed is True
    assert count == 0
    assert rationale == "NO_ACTIONS_CONFIRMED"
    assert len(failures) == 0


def test_clamp_locators_to_duration():
    """Verify out-of-bounds timestamps and 1-hour offsets are rectified."""
    # Meeting is 28 minutes long.
    # 01:06:51 is a 1-hour offset -> should become 00:06:51
    # 02:15:30 is way past 28 mins -> clamped to max minutes
    sample_text = (
        "Anthony discussed the architecture at [01:06:51]. "
        "Further details were mentioned at [02:15:30]. "
        "Normal discussion occurred at [00:15:10]."
    )
    meta = {"meeting_duration": 28}
    changes = []
    audit = []
    result = _clamp_locators_to_duration(sample_text, meta=meta, changes=changes, audit_log=audit)

    assert "[00:06:51]" in result
    assert "[00:15:10]" in result
    assert "[01:06:51]" not in result
    assert "[02:15:30]" not in result
    assert len(changes) == 1
    assert "Clamped 2 out-of-bounds timestamps" in changes[0]


def test_synthesize_grounded_action_table_eliminates_alex_sanchez_hallucination():
    """Verify fallback does not inject fake Alex Sanchez actions."""
    text_without_actions = "# Subject: Technical Sync\n\nDiscussion about rules."
    result = _synthesize_grounded_action_table(text_without_actions, output_1=None)
    assert "Alex Sanchez" not in result
    assert "01:58:31" not in result
    assert "No forward commitments or action items were assigned during this technical sync." in result
