import pytest
from dual_pipeline.transcript_qa_core.gate3_evidence import (
    _count_dialogue_turns,
    check_substantive_transcript_evidence,
)
from dual_pipeline.transcript_qa_core.reconciliation_coverage_healer import (
    heal_substantive_transcript_evidence,
)
from dual_pipeline.transcript_stages_core.recap_executor import (
    _process_meeting_tone_arc,
)


def test_count_dialogue_turns_bold_markdown():
    f1_text = (
        "**Alice Smith:** [00:00:00]: Good morning everyone.\n\n"
        "**Bob Jones:** [00:00:05]: Morning! Let's get started on the migration.\n\n"
        "**Charlie Brown:** [00:00:15]: All systems look good on my end.\n\n"
        "**Alice Smith:** [00:00:25]: Perfect, thank you.\n"
    )
    count = _count_dialogue_turns(f1_text)
    assert count == 4


def test_heal_substantive_transcript_evidence_bold_markdown():
    f1_text = (
        "**Alice Smith:** [00:00:00]: Good morning everyone, we need to verify the database indexes today.\n\n"
        "**Bob Jones:** [00:00:15]: The indexing pipeline has been running and all jobs completed successfully.\n\n"
        "**Charlie Brown:** [00:00:30]: We also reviewed the API endpoint latency metrics with the platform team.\n\n"
        "**Alice Smith:** [00:00:45]: Great, that covers the immediate deliverables for this sprint.\n"
    )
    data = {
        "evidence_index": {},
        "graph_mutations": {},
    }
    changes = []
    heal_substantive_transcript_evidence(data, f1_text=f1_text, changes=changes)
    assert len(data["evidence_index"]) >= 3
    ok, errs = check_substantive_transcript_evidence(data, f1_text=f1_text)
    assert ok is True
    assert len(errs) == 0


def test_recap_executor_suppresses_tone_arc_for_tactical_standup():
    recap_text = (
        "# Executive Recap\n\n"
        "## Key Discussion Points\nSome points.\n\n"
        "## Meeting Tone Arc\n| Timestamp | Tone | Analysis |\n| --- | --- | --- |\n| 00:01:00 | Focused | Neutral |\n\n"
        "## Action Items\n- Item 1\n"
    )
    metadata_tactical = {"archetype": "TACTICAL_ALIGNMENT"}
    out_tactical = _process_meeting_tone_arc(recap_text, metadata_tactical, forbid_sections=[])
    assert "Meeting Tone Arc" not in out_tactical

    metadata_standup = {"archetype": "STANDUP"}
    out_standup = _process_meeting_tone_arc(recap_text, metadata_standup, forbid_sections=[])
    assert "Meeting Tone Arc" not in out_standup
