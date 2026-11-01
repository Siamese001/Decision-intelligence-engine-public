"""End-to-end integration test for Output 01 organic signal enrichment.

Tests the full Stage 01 normalization -> Gate 1 validation -> Stage 02/03 Quote Pointer resolution
lifecycle in an isolated ephemeral sandbox directory under production purity rules.
"""

import shutil
import tempfile
from pathlib import Path
import pytest

from dual_pipeline.transcript_stages_core.forensic_normalizer import ForensicNormalizer
from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
from dual_pipeline.transcript_qa_core.gate1 import run_qa_gate_1
from dual_pipeline.transcript_stages_core.quote_pointer_resolver import (
    locate_quote_pointer,
    resolve_evidence_pointers,
    resolve_quote_from_pointer,
)


@pytest.fixture
def sandbox_env():
    """Ephemeral sandbox directory fixture adhering to production purity rules."""
    sandbox_dir = Path(tempfile.mkdtemp(prefix="eval_sandbox_output1_e2e_"))
    try:
        yield sandbox_dir
    finally:
        shutil.rmtree(sandbox_dir, ignore_errors=True)


def test_output1_organic_signal_consolidation_e2e(sandbox_env):
    """Verify that Stage 01 consolidates micro-turns, eliminates disfluencies, passes Gate 1,

    and supports 100% downstream quote pointer resolution.
    """
    raw_fixture = (
        "# Live Meeting Transcript | 10/13/2026\n\n"
        "**Date:** 10/13/2026\n\n"
        "**Participants:**\n"
        "- Amit Ayer\n"
        "- Alex Sanchez\n"
        "- Eduardo Juan More\n\n"
        "---\n\n"
        "[00:00:48] Amit Ayer: OK.\n"
        "[00:01:00] Alex Sanchez: Today we are reviewing the notification architecture for Project LENS.\n"
        "[00:01:05] Alex Sanchez: We need to ensure that notifications are dispatched with low latency across all mobile clients.\n"
        "[00:01:29] Eduardo Juan More: the\n"
        "[00:01:31] Alex Sanchez: backend services are configured to route through the primary event bus.\n"
        "[00:03:07] Alex Sanchez: overall\n"
        "[00:03:10] Alex Sanchez: performance targets require sub-second delivery for critical alerts.\n"
        "[00:05:53] Amit Ayer: Yeah.\n"
        "[00:05:55] Eduardo Juan More: What about offline message queuing? Any questions on this?\n"
        "[00:05:58] Alex Sanchez: Yeah.\n"
        "[00:06:00] Alex Sanchez: Offline messages will be queued in SQLite before syncing to AWS S3 once network recovers.\n"
        "[00:08:22] Eduardo Juan More: It -it -it -that I think is part of phase one, is -is that at least what we agreed?\n"
        "[00:08:26] Alex Sanchez: Yes, exactly.\n\n"
        "## Ambiguity Lock Register\n\n"
        "| Item # | Identified Ambiguity | Source Locator | Affected Statement IDs | Final Resolved Record | Resolution Basis |\n"
        "|---|---|---|---|---|---|\n"
        "| 1 | Speaker Attribution: Alex Sanchez | [00:01:00 – Session End] | stmt-01 | Resolved via conversational context. | Pre-Resolved Roster Grounding |\n"
        "| 2 | Speaker Attribution: Eduardo Juan More | [00:05:55 – Session End] | stmt-02 | Resolved via conversational context. | Pre-Resolved Roster Grounding |\n"
        "| 3 | Speaker Attribution: Amit Ayer | [00:00:48 – Session End] | stmt-03 | Resolved via conversational context. | Pre-Resolved Roster Grounding |\n"
        "| 4 | Enterprise Domain Lexicon & Acronym Normalization | [00:00:00 – Session End] | stmt-lex-01 | Domain terminology (AWS S3, SQLite) anchored against canonical Knowledge Graph. | Canonical GraphDB Grounding (Rule C-16) |\n\n"
        "## Attribution Summary\n\n"
        "| Speaker | Turns Attributed | Primary Acoustic/Context Basis | Confidence Tier |\n"
        "|---|---|---|---|\n"
        "| Alex Sanchez | 4 | Conversational Context & Roster Match | HIGH |\n"
        "| Eduardo Juan More | 2 | Conversational Context & Roster Match | HIGH |\n"
        "| Amit Ayer | 1 | Conversational Context & Roster Match | HIGH |\n"
    )

    metadata = {
        "meeting_name": "Sync_Mandy_PartnerAdvisory",
        "date": "10/13/2026",
        "source_modality": "AUDIO_DIARIZED",
        "duration_minutes": 5,
        "attendees": ["Amit Ayer", "Alex Sanchez", "Eduardo Juan More"],
        "roster": [{"name": "Amit Ayer"}, {"name": "Alex Sanchez"}, {"name": "Eduardo Juan More"}],
        "speaker_map": {
            "Speaker 1": "Alex Sanchez",
            "Speaker 2": "Eduardo Juan More",
            "Speaker 3": "Amit Ayer",
        },
    }

    # Step 1: Run Stage 01 Forensic Normalization & Healing
    healed_transcript, changes = DeterministicHealer.heal_output_1(raw_fixture, metadata=metadata)
    out1_path = sandbox_env / "01_Sync_Mandy_PartnerAdvisory_10-13-2026_hitl_resolved_transcript.md"
    out1_path.write_text(healed_transcript, encoding="utf-8")

    # Verify Output 01 on disk
    saved_content = out1_path.read_text(encoding="utf-8")
    assert "[00:00:48]" not in saved_content, "Passive opening OK must be pruned"
    assert "[00:05:53]" not in saved_content, "Passive interrupting Yeah must be pruned"
    assert "[00:05:58] Alex Sanchez: Yeah." in saved_content, "Question-response Yeah must be preserved"
    assert "That I think is part of phase one, is that at least" in saved_content, "Hyphen stutter must be collapsed"

    # Step 2: Validate Gate 1 passes
    gate1_report = run_qa_gate_1(
        saved_content,
        metadata=metadata,
    )
    assert gate1_report.get("passed") is True, f"Gate 1 failed on enriched transcript: {gate1_report.get('failures')}"

    # Step 3: Downstream Stage 02/03 Quote Pointer Resolution
    quote_text = "Offline messages will be queued in SQLite before syncing to AWS S3 once network recovers."
    assert quote_text in saved_content

    pointer = locate_quote_pointer(saved_content, quote_text)
    assert pointer is not None, "Quote pointer must locate exact substring in Output 01"
    start_idx, end_idx = pointer
    resolved_slice = resolve_quote_from_pointer(saved_content, [start_idx, end_idx])
    assert resolved_slice == quote_text, "Resolved pointer slice must match verbatim quote exactly"

    # Step 4: Full Evidence Index Binding
    mock_stage03_projection = {
        "evidence_index": {
            "ev-01": {
                "source": "01_Sync_Mandy_PartnerAdvisory_10-13-2026_hitl_resolved_transcript.md",
                "speaker": "Alex Sanchez",
                "quote_pointer": [start_idx, end_idx],
            },
            "ev-02": {
                "source": "01_Sync_Mandy_PartnerAdvisory_10-13-2026_hitl_resolved_transcript.md",
                "speaker": "Alex Sanchez",
                "text": quote_text,
            },
        }
    }
    resolved_projection = resolve_evidence_pointers(mock_stage03_projection, saved_content)
    ev01 = resolved_projection["evidence_index"]["ev-01"]
    assert ev01["exact_text"] == quote_text
    assert ev01["grounding_status"] == "GROUNDED"

    ev02 = resolved_projection["evidence_index"]["ev-02"]
    assert ev02["quote_pointer"] == [start_idx, end_idx]
    assert ev02["exact_text"] == quote_text
    assert ev02["grounding_status"] == "GROUNDED"


def test_output1_bold_header_organic_signal_e2e(sandbox_env):
    """Verify that Stage 01 healing preserves bold headers, prunes passive backchannels, and passes Gate 1."""
    raw_fixture = (
        "# Forensic Meeting Transcript | 09/14/2026\n\n"
        "**Date:** 09/14/2026\n\n"
        "**Participants:**\n"
        "- Kannan Chandraprakash\n"
        "- Amit Ayer\n"
        "- Drew Oakley\n\n"
        "---\n\n"
        "## Forensic Transcript\n\n"
        "**[00:22:34] Kannan Chandraprakash:** Try to listen and do not jump directly to answering.\n\n"
        "**[00:25:23] Amit Ayer:** Yeah.\n\n"
        "**[00:25:41] Kannan Chandraprakash:** But for now, moving on to CSV. That is the kind of answer expected.\n\n"
        "## Ambiguity Lock Register\n\n"
        "| Item # | Identified Ambiguity | Source Locator | Affected Statement IDs | Final Resolved Record | Resolution Basis |\n"
        "|---|---|---|---|---|---|\n"
        "| 1 | Speaker Attribution: Kannan Chandraprakash | [00:22:34] | stmt-01 | Resolved via conversational context. | Pre-Resolved Roster Grounding |\n"
        "| 2 | Speaker Attribution: Amit Ayer | [00:25:23] | stmt-02 | Resolved via conversational context. | Pre-Resolved Roster Grounding |\n"
        "| 3 | Enterprise Domain Lexicon | [00:25:41] | stmt-lex-01 | Domain terminology (CSV) anchored against Knowledge Graph. | Canonical GraphDB Grounding |\n"
    )

    metadata = {
        "meeting_name": "Kannan_Welcome_Interviews",
        "date": "09/14/2026",
        "source_modality": "AUDIO_UNDIARIZED",
        "duration_minutes": 30,
        "attendees": ["Kannan Chandraprakash", "Amit Ayer", "Drew Oakley"],
        "roster": [{"name": "Kannan Chandraprakash"}, {"name": "Amit Ayer"}, {"name": "Drew Oakley"}],
        "speaker_map": {
            "Speaker 1": "Kannan Chandraprakash",
            "Speaker 2": "Amit Ayer",
            "Speaker 3": "Drew Oakley",
        },
    }

    healed_transcript, changes = DeterministicHealer.heal_output_1(raw_fixture, metadata=metadata)
    out1_path = sandbox_env / "01_Kannan_Welcome_Interviews_09-14-2026_hitl_resolved_transcript.md"
    out1_path.write_text(healed_transcript, encoding="utf-8")

    saved = out1_path.read_text(encoding="utf-8")
    assert "**[00:25:23] Amit Ayer:**" not in saved, "Passive backchannel Yeah must be pruned"
    assert "**[00:22:34] Kannan Chandraprakash:** Try to listen and do not jump directly to answering." in saved
    assert "**[00:25:41] Kannan Chandraprakash:** But for now, moving on to CSV." in saved
    assert "## Ambiguity Lock Register" in saved

    from dual_pipeline.llm_judge_core.forensic_checks import check_organic_signal_hygiene
    passed, defect_codes, metrics = check_organic_signal_hygiene(saved)
    assert passed is True
    assert len(defect_codes) == 0
    assert metrics["backchannel_count"] == 0
