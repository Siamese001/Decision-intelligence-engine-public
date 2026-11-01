"""
tests/test_cross_meeting_context.py

Unit tests for CrossMeetingContextLoader, cross-meeting packet building,
and CrossMeetingCoherenceJudge adversarial enforcement.
"""

from __future__ import annotations

import json

from dual_pipeline.llm_judge_core.cross_meeting_coherence import (
    CrossMeetingCoherenceJudge,
)
from dual_pipeline.transcript_pipeline_core.cross_meeting_context import (
    CrossMeetingContextLoader,
)


def test_cross_meeting_context_loader_extraction(tmp_path):
    repo_root = tmp_path
    proj_dir = repo_root / "output" / "test_project" / "09-17-2026" / "Meeting_A"
    proj_dir.mkdir(parents=True)

    recap_md = """# Executive Recap
**Date:** 09/17/2026
**Attendees:**
- Jen Pan (PartnerAdvisory, Product Lead)
- Eduardo Juan More (Apex Global Enterprises, Lens Reviewer)

## Key Updates
- **Eduardo Juan More approved the OCR latency target of 2.5s**
- **Jen Pan decided to postpone UX review**

## Action & Governance Ledger
| Owner | Task | Target Date | Status |
| PartnerAdvisory | Finalize OCR tests | 09/24/2026 | OPEN |
"""
    (proj_dir / "02_Meeting_A_09-17-2026_executive_recap.md").write_text(recap_md)

    graph_json = {
        "entities": [
            {
                "entity_id": "person:eduardo-juan-more",
                "name": "Eduardo Juan More",
                "organization": "Apex Global Enterprises (NEA)",
                "role": "Lens Reviewer",
            }
        ]
    }
    (proj_dir / "03_Meeting_A_09-17-2026_canonical_signal_projection.json").write_text(json.dumps(graph_json))

    loader = CrossMeetingContextLoader(window_days=7, repo_root=repo_root)
    xml_packet, registry = loader.build_cross_meeting_packet(
        project_name="test_project",
        current_date_str="09/18/2026",
        current_meeting_name="Meeting_B",
    )

    assert "<cross_meeting_context" in xml_packet
    assert "Eduardo Juan More" in xml_packet
    assert "eduardo" in registry
    assert registry["eduardo"]["canonical_name"] == "Eduardo Juan More"


def test_cross_meeting_coherence_judge_detects_mutant_surname():
    judge = CrossMeetingCoherenceJudge()
    registry = {
        "mandy": {
            "canonical_name": "harper Hoffman",
            "org": "Apex Global Enterprises (NEA)",
            "role": "Product Owner",
            "source_meeting": "Mid_Sprint_Sync",
        }
    }

    # Candidate text hallucinates "Mandy Haskell"
    f1_text = "[00:10] Mandy Haskell: We are checking the image pipeline."
    f2_text = "- Mandy Haskell presented the revised schedule."
    f3_json = {"statements": []}

    res = judge.audit(f1_text, f2_text, f3_json, cross_meeting_registry=registry)
    assert res.passed is False
    assert res.hard_veto is True
    assert any("ENTITY_IDENTITY_DRIFT" in f for f in res.findings)


def test_cross_meeting_coherence_judge_catches_corrupted_locators():
    judge = CrossMeetingCoherenceJudge()
    f1_text = "[00:10] Jen Pan: Okay."
    f2_text = "- Jen Pan agreed on the timeline."
    f3_json = {
        "statements": [
            {
                "statement_id": "stmt-1",
                "locator": {
                    "kind": "timestamp",
                    "value": "This is raw dialogue text instead of 00:10",
                },
            }
        ]
    }

    res = judge.audit(f1_text, f2_text, f3_json)
    assert res.passed is False
    assert res.hard_veto is True
    assert any("CORRUPTED_LOCATOR_SCHEMA" in f for f in res.findings)
