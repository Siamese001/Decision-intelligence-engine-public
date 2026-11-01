import json
from pathlib import Path
from unittest.mock import patch

import pytest

from dual_pipeline.transcript_models import (
    ActionItemRow,
    CanonicalSignalProjectionV57,
)
from dual_pipeline.transcript_pipeline import RawTranscriptPipeline


def test_pipeline_initialization():
    pipeline = RawTranscriptPipeline(provider_type="offline")
    assert pipeline.u0 is not None
    assert pipeline.l1 is not None
    assert pipeline.l0 is not None
    # c0 is now split into c0_native (NATIVE path) and c0_undiarized (UNDIARIZED path)
    assert pipeline.c0_native is not None
    assert pipeline.c0_undiarized is not None
    assert pipeline.pa is not None
    assert pipeline.l2 is not None
    assert pipeline.x_gates is not None
    # New split L2 executors
    assert pipeline.l2_forensic is not None
    assert pipeline.l2_recap is not None
    assert pipeline.l2_graph is not None


def test_u0_ingestion_boundary():
    pipeline = RawTranscriptPipeline(provider_type="offline")
    metadata = {
        "meeting_name": "Test Meeting",
        "date": "01/01/2026",
        "time": "10:00AM",
        "classification": "internal"
    }
    raw_transcript = "Speaker A: Let's do this."

    ctx = pipeline.u0.execute(raw_transcript, metadata)
    assert "hash" in ctx
    assert ctx["clean_transcript"] == raw_transcript


def test_u0_eliminates_blind_speaker_mapping():
    """Verify arbitrary attendee indexing does not synthesize speaker_map in U0."""
    pipeline = RawTranscriptPipeline(provider_type="offline")
    metadata = {
        "meeting_name": "Test Meeting",
        "date": "01/01/2026",
        "time": "10:00AM",
        "classification": "internal",
        "attendees": ["Alex Sanchez", "Alex Rivera"],
        "auto_map_speakers": True,
    }
    raw_transcript = "Speaker 1: Hello.\nSpeaker 2: Hi there."
    ctx = pipeline.u0.execute(raw_transcript, metadata)
    assert "speaker_map" not in ctx["metadata"]


def test_u0_missing_metadata():
    pipeline = RawTranscriptPipeline(provider_type="offline")
    with pytest.raises(ValueError, match="Metadata missing required keys"):
        pipeline.u0.execute("text", {"meeting_name": "Test"})


def test_l0_passes_real_data():
    """Finding #3: L0 must pass real transcript data, not fabricate replacements."""
    from dual_pipeline.transcript_stages import L1PlanContract
    pipeline = RawTranscriptPipeline(provider_type="offline")
    u0_ctx = {
        "hash": "abc123",
        "clean_transcript": "Speaker A: Real data here.",
        "metadata": {"meeting_name": "Test", "date": "01/01/2026", "time": "10:00", "classification": "internal"}
    }
    hitl_transcript = "# Test -- HITL-Resolved\n\nSpeaker A: Real data here."
    plan = L1PlanContract(
        source_type="DIARIZED_TEXT",
        diarization_status="NATIVE",
        roster_gate_needed=False,
    )
    l0_result = pipeline.l0.execute(u0_ctx, hitl_transcript, plan)

    assert l0_result["transcript"] == hitl_transcript
    assert l0_result["hash"] == "abc123"
    assert "statements" in l0_result
    assert isinstance(l0_result["statements"], list)


def test_pa_passes_real_data():
    """Finding #4: PA must assemble real context, not return a hardcoded string."""
    pipeline = RawTranscriptPipeline(provider_type="offline")
    c0_ctx = {
        "transcript": "Real transcript content",
        "statements": ["stmt1", "stmt2"],
        "hash": "abc123",
        "conformance_checked": True,
    }
    metadata = {"meeting_name": "Test", "date": "01/01/2026", "time": "10:00", "classification": "internal"}
    pa_result = pipeline.pa.execute(c0_ctx, metadata)

    assert isinstance(pa_result, dict)
    assert pa_result["transcript"] == "Real transcript content"
    assert pa_result["statements"] == ["stmt1", "stmt2"]
    assert pa_result["metadata"] == metadata


def test_x3_exit_gate_schema_validation():
    """Finding #5: X-gates must validate with Pydantic, not just check schema_version string."""
    pipeline = RawTranscriptPipeline(provider_type="offline")

    metadata = {
        "meeting_name": "Test Meeting",
        "date": "01/01/2026",
        "time": "10:00AM",
        "classification": "internal"
    }

    valid_recap = """# Subject: Test Meeting -- Executive Recap

## Meeting Objective
Test objective.

## Key Discussion Points & Findings
- **Topic 1** -- Finding.

## Next Steps & Action Items

| # | Action Item / Deliverable | Owner | Target Timing / Deadline | Source Locator |
|---|---|---|---|---|
| 1 | Deliver report | Amit Ayer | 2026-10-01 | 00:01:00 |
"""

    valid_graph = json.dumps({
        "schema_version": "5.7",
        "meeting_id": "meet-123",
        "classification": "internal",
        "audiences": [],
        "evidence_index": {},
        "graph_mutations": {
            "entity_upserts": [],
            "relationship_signals": [],
            "decision_updates": [],
            "proposal_updates": [],
            "action_updates": [],
            "risk_updates": []
        },
        "evidence_only_statement_ids": []
    })

    assert pipeline.x_gates.execute(valid_recap, valid_graph, metadata) is True

    # Invalid schema version
    invalid_graph = json.dumps({"schema_version": "5.6"})
    with pytest.raises(ValueError, match="schema_version 5.7"):
        pipeline.x_gates.execute(valid_recap, invalid_graph, metadata)


def test_x2_recap_missing_objective():
    """X2 must reject recaps missing the Meeting Objective section."""
    pipeline = RawTranscriptPipeline(provider_type="offline")
    metadata = {"meeting_name": "T", "date": "01/01/2026", "time": "10:00", "classification": "internal"}
    bad_recap = "# Subject: Test\n\n## Next Steps & Action Items\nNone."
    valid_graph = json.dumps({
        "schema_version": "5.7", "meeting_id": "m-1", "classification": "internal",
        "audiences": [], "evidence_index": {},
        "graph_mutations": {"entity_upserts": [], "relationship_signals": [],
                            "decision_updates": [], "proposal_updates": [],
                            "action_updates": [], "risk_updates": []},
        "evidence_only_statement_ids": []
    })
    with pytest.raises(ValueError, match="Meeting Objective"):
        pipeline.x_gates.execute(bad_recap, valid_graph, metadata)


def test_x2_anti_embarrassment_gate():
    """X2 must reject passive actions in the recap table."""
    pipeline = RawTranscriptPipeline(provider_type="offline")
    metadata = {"meeting_name": "T", "date": "01/01/2026", "time": "10:00", "classification": "internal"}
    passive_recap = """# Subject: Test
## Meeting Objective
Test.
## Next Steps & Action Items
| # | Action Item / Deliverable | Owner | Target | Locator |
|---|---|---|---|---|
| 1 | Acknowledge the risk | Team | 2026-10-01 | 00:01:00 |
"""
    valid_graph = json.dumps({
        "schema_version": "5.7", "meeting_id": "m-1", "classification": "internal",
        "audiences": [], "evidence_index": {},
        "graph_mutations": {"entity_upserts": [], "relationship_signals": [],
                            "decision_updates": [], "proposal_updates": [],
                            "action_updates": [], "risk_updates": []},
        "evidence_only_statement_ids": []
    })
    with pytest.raises(ValueError, match="Anti-embarrassment"):
        pipeline.x_gates.execute(passive_recap, valid_graph, metadata)


def test_pydantic_model_validates_graph():
    """Finding #5: Verify the Pydantic model catches invalid graph structures."""
    valid_data = {
        "schema_version": "5.7",
        "meeting_id": "meet-123",
        "classification": "internal",
        "audiences": [],
        "evidence_index": {},
        "graph_mutations": {
            "entity_upserts": [],
            "relationship_signals": [],
            "decision_updates": [],
            "proposal_updates": [],
            "action_updates": [],
            "risk_updates": []
        },
        "evidence_only_statement_ids": []
    }
    model = CanonicalSignalProjectionV57.model_validate(valid_data)
    assert model.schema_version == "5.7"

    # Missing required field
    with pytest.raises(Exception):
        CanonicalSignalProjectionV57.model_validate({"schema_version": "5.7"})


def test_action_item_anti_embarrassment():
    """Finding #16: Expanded passive verb blocklist must catch more patterns."""
    for passive in ["Acknowledge the risk", "Note the feedback", "Consider options",
                     "Monitor the situation", "Be aware of the deadline",
                     "Discuss at next meeting", "Explore alternatives"]:
        with pytest.raises(ValueError, match="passive observation"):
            ActionItemRow(id=1, action_deliverable=passive, owner="Team",
                          target_timing="2026-10-01", source_locator="00:01:00")

    # Active verbs should pass
    ActionItemRow(id=1, action_deliverable="Deliver the report",
                  owner="Amit Ayer", target_timing="2026-10-01",
                  source_locator="00:01:00")


@patch("dual_pipeline.transcript_pipeline.RunRecorder")
def test_full_pipeline_execution(mock_recorder, tmp_path):
    pipeline = RawTranscriptPipeline(provider_type="offline")
    metadata = {
        "meeting_name": "Test Sync",
        "date": "09/01/2026",
        "time": "14:00",
        "classification": "internal",
        "attendees": [
            {"name": "Reid Schenk", "org": "Apex Global Enterprises", "role": "VP"},
            {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Senior Principal"},
        ],
    }

    # Create mock text file with named speaker labels (NATIVE diarization)
    mock_input_file = tmp_path / "mock_transcript.txt"
    mock_input_file.write_text(
        "**Reid Schenk:** Let's align on the architecture.\n"
        "**Amit Ayer:** Agreed, let's proceed.",
        encoding="utf-8"
    )

    out_dir = tmp_path / "artifacts"
    result = pipeline.run(str(mock_input_file), metadata, str(out_dir))

    assert "01_transcript" in result
    assert "02_recap" in result
    assert "03_graph" in result

    # Verify files created
    assert Path(result["01_transcript"]).exists()
    assert Path(result["02_recap"]).exists()
    assert Path(result["03_graph"]).exists()

    # Finding #14: Verify output content quality
    transcript_content = Path(result["01_transcript"]).read_text(encoding="utf-8")
    assert ("HITL-Resolved Transcript" in transcript_content or "Forensic Transcript" in transcript_content)
    assert "Ambiguity Lock Register" in transcript_content

    recap_content = Path(result["02_recap"]).read_text(encoding="utf-8")
    assert "Meeting Objective" in recap_content
    assert "Next Steps" in recap_content

    graph_content = Path(result["03_graph"]).read_text(encoding="utf-8")
    graph_data = json.loads(graph_content)
    assert graph_data["schema_version"] == "5.7"
    CanonicalSignalProjectionV57.model_validate(graph_data)


@patch("dual_pipeline.transcript_pipeline.RunRecorder")
def test_audio_pipeline_execution(mock_recorder, tmp_path):
    pipeline = RawTranscriptPipeline(provider_type="offline")
    metadata = {
        "meeting_name": "Audio Sync",
        "date": "09/01/2026",
        "time": "15:00",
        "attendees": [
            {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Senior Principal"},
            {"name": "Reid Schenk", "org": "Apex Global Enterprises", "role": "VP"},
        ],
        "speaker_map": {
            "Speaker 1": "Amit Ayer (PartnerAdvisory)",
            "Speaker 2": "Reid Schenk (Apex Global Enterprises)",
        },
    }

    # Create mock audio file
    mock_input_file = tmp_path / "mock_recording.mp3"
    mock_input_file.write_text("fake binary audio", encoding="utf-8")

    out_dir = tmp_path / "audio_artifacts"
    result = pipeline.run(str(mock_input_file), metadata, str(out_dir))

    assert "01_transcript" in result

    # Verify the mock audio transcript was generated and processed
    f1 = Path(result["01_transcript"])
    content = f1.read_text(encoding="utf-8")
    assert "mock audio transcript for mock_recording.mp3" in content


@patch("dual_pipeline.transcript_pipeline.RunRecorder")
def test_unsupported_format_raises_error(mock_recorder, tmp_path):
    """Finding #9: Unsupported file extensions must raise ValueError, not crash."""
    pipeline = RawTranscriptPipeline(provider_type="offline")
    metadata = {
        "meeting_name": "Test", "date": "01/01/2026",
        "time": "10:00", "classification": "internal"
    }
    bad_file = tmp_path / "recording.xyz"
    bad_file.write_text("data", encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported input format"):
        pipeline.run(str(bad_file), metadata, str(tmp_path / "out"))


@patch("dual_pipeline.transcript_pipeline.RunRecorder")
def test_filename_sanitization(mock_recorder, tmp_path):
    """Finding #18: Meeting names with filesystem-unsafe chars must be sanitized."""
    pipeline = RawTranscriptPipeline(provider_type="offline")
    metadata = {
        "meeting_name": "Q3/Q4 Planning: Architecture <Review>",
        "date": "09/01/2026",
        "time": "14:00",
        "classification": "internal",
        "attendees": [
            {"name": "Reid Schenk", "org": "Apex Global Enterprises", "role": "VP"},
        ],
    }
    mock_input = tmp_path / "transcript.txt"
    # Use named speaker format so diarization_status → NATIVE
    mock_input.write_text("**Reid Schenk:** Hello.", encoding="utf-8")

    out_dir = tmp_path / "sanitized"
    result = pipeline.run(str(mock_input), metadata, str(out_dir))

    # Verify no unsafe characters in output filenames (check basename only, not full path)
    from pathlib import Path as P
    for path_str in result.values():
        basename = P(path_str).name
        for char in '<>:"/\\|?*':
            assert char not in basename, f"Unsafe char '{char}' found in filename {basename}"


def test_l1_blocks_unmapped_speakers_hitl():
    """Verify that L1 blocks output 1 when diarized speakers are unmapped."""
    from dual_pipeline.transcript_stages import (
        HITLRequiredError,
        L1ForensicCognition,
        L1PlanContract,
        OfflineTranscriptSynthesizer,
    )
    l1 = L1ForensicCognition()
    provider = OfflineTranscriptSynthesizer()

    # A NATIVE transcript with generic Speaker X labels and no speaker_map
    u0_ctx = {
        "clean_transcript": "**Speaker A** [00:00:00]: Hello.\n**Speaker B** [00:00:05]: Hi.",
        "metadata": {
            "meeting_name": "Test Meeting",
            "date": "08/28/2026",
            "time": "10:00",
            "classification": "internal",
        },
        "diarization_status": "NATIVE",
    }
    plan_native = L1PlanContract(
        source_type="DIARIZED_TEXT",
        diarization_status="NATIVE",
        roster_gate_needed=False,
    )
    with pytest.raises(HITLRequiredError, match="L1 ATTRIBUTION GATE BLOCKED"):
        l1.execute(u0_ctx, plan_native, provider)

    # When speaker_map is provided, it passes cleanly
    u0_ctx["metadata"]["speaker_map"] = {
        "Speaker A": "Reid Schenk (Apex Global Enterprises)",
        "Speaker B": "Quinn Mercer (PartnerAdvisory)"
    }
    resolved = l1.execute(u0_ctx, plan_native, provider)
    assert "Reid Schenk (Apex Global Enterprises)" in resolved
    assert "Quinn Mercer (PartnerAdvisory)" in resolved
    assert "**Speaker A" not in resolved and "Speaker A:" not in resolved


def test_x2_blocks_unresolved_speaker_recap():
    """Verify X2 gate blocks recaps containing unresolved Speaker [A-Z] or Organization not specified."""
    from dual_pipeline.transcript_stages import XExitGatesEvaluator
    x_gates = XExitGatesEvaluator()
    bad_recap = """# Subject: Test Recap
**Attendees:**
- **Organization not specified:** Speaker A, Speaker B

## Meeting Objective
Align on test goals.

## Next Steps & Action Items
| # | Action Item / Deliverable | Owner | Target Timing | Source Locator |
|---|---|---|---|---|
| 1 | Deliver terraform modules | Speaker A | 09/15/2026 | 00:01:00 |
"""
    valid_graph = '{"schema_version": "5.7", "meeting_id": "m1", "classification": "internal", "audiences": [], "evidence_index": {}, "graph_mutations": {}, "evidence_only_statement_ids": []}'
    with pytest.raises(ValueError, match="Unresolved 'Speaker"):
        x_gates.execute(bad_recap, valid_graph, {"meeting_name": "Test"})


def test_x1_blocks_duplicate_entity_ids_sprawl():
    """Verify X1-S blocks graph projections containing duplicate entity IDs (sprawl prevention)."""
    from dual_pipeline.transcript_stages import XExitGatesEvaluator
    x_gates = XExitGatesEvaluator()
    valid_recap = (
        "# Subject: Test\n**Attendees:**\n- **PartnerAdvisory:** Drew Oakley\n\n"
        "## Meeting Objective\nAlign on roadmap.\n\n"
        "## Next Steps & Action Items\n| # | Action | Owner | Target | Locator |\n|---|---|---|---|---|\n| 1 | Align | Drew Oakley | 09/01/2026 | 00:01 |\n"
    )
    duplicate_entities_graph = json.dumps({
        "schema_version": "5.7",
        "meeting_id": "meeting-01",
        "classification": "internal",
        "audiences": ["partner_advisory"],
        "evidence_index": {},
        "graph_mutations": {
            "entity_upserts": [
                {"id": "person:dan-okray", "type": "person", "name": "Drew Oakley", "mutation": "CREATE"},
                {"id": "person:dan-okray", "type": "person", "name": "Drew Oakley", "mutation": "CONFIRM"},
            ]
        },
        "evidence_only_statement_ids": [],
    })
    with pytest.raises(ValueError, match="Duplicate entity ID 'person:dan-okray'"):
        x_gates.execute(valid_recap, duplicate_entities_graph, {"meeting_name": "Test"})


def test_x1_blocks_duplicate_edge_triples_sprawl():
    """Verify X1-S blocks graph projections containing duplicate parallel edges with the same triple."""
    from dual_pipeline.transcript_stages import XExitGatesEvaluator
    x_gates = XExitGatesEvaluator()
    valid_recap = (
        "# Subject: Test\n**Attendees:**\n- **PartnerAdvisory:** Drew Oakley\n\n"
        "## Meeting Objective\nAlign on roadmap.\n\n"
        "## Next Steps & Action Items\n| # | Action | Owner | Target | Locator |\n|---|---|---|---|---|\n| 1 | Align | Drew Oakley | 09/01/2026 | 00:01 |\n"
    )
    duplicate_edges_graph = json.dumps({
        "schema_version": "5.7",
        "meeting_id": "meeting-01",
        "classification": "internal",
        "audiences": ["partner_advisory"],
        "evidence_index": {
            "stmt-01": {"speaker": "Drew Oakley", "source_classification": "FACT", "text": "Test statement", "locator": {"kind": "section", "value": "00:01"}}
        },
        "graph_mutations": {
            "relationship_signals": [
                {
                    "signal_id": "rel-dan-okray--partner_advisory--member_of",
                    "from_id": "person:dan-okray",
                    "relationship": "member_of",
                    "to_id": "org:partner_advisory",
                    "evidence_ids": ["stmt-01"],
                },
                {
                    "signal_id": "rel-dan-okray--partner_advisory--member_of-duplicate",
                    "from_id": "person:dan-okray",
                    "relationship": "member_of",
                    "to_id": "org:partner_advisory",
                    "evidence_ids": ["stmt-01"],
                },
            ]
        },
        "evidence_only_statement_ids": [],
    })
    with pytest.raises(ValueError, match="Duplicate relationship edge"):
        x_gates.execute(valid_recap, duplicate_edges_graph, {"meeting_name": "Test"})


def test_x1_blocks_duplicate_assertion_ids_sprawl():
    """Verify X1-S blocks duplicate assertion IDs across decisions, proposals, actions, and risks."""
    from dual_pipeline.transcript_stages import XExitGatesEvaluator
    x_gates = XExitGatesEvaluator()
    valid_recap = (
        "# Subject: Test\n**Attendees:**\n- **PartnerAdvisory:** Drew Oakley\n\n"
        "## Meeting Objective\nAlign on roadmap.\n\n"
        "## Next Steps & Action Items\n| # | Action | Owner | Target | Locator |\n|---|---|---|---|---|\n| 1 | Align | Drew Oakley | 09/01/2026 | 00:01 |\n"
    )
    duplicate_risk_graph = json.dumps({
        "schema_version": "5.7",
        "meeting_id": "meeting-01",
        "classification": "internal",
        "audiences": ["partner_advisory"],
        "evidence_index": {
            "stmt-01": {"speaker": "Drew Oakley", "source_classification": "FACT", "text": "Test statement", "locator": {"kind": "section", "value": "00:01"}}
        },
        "graph_mutations": {
            "risk_updates": [
                {"risk_id": "risk:capacity-shortage", "mutation": "CREATE", "status": "active", "description": "Shortage", "risk_scope": "delivery", "evidence_ids": ["stmt-01"]},
                {"risk_id": "risk:capacity-shortage", "mutation": "MODIFY", "status": "active", "description": "Updated shortage", "risk_scope": "delivery", "evidence_ids": ["stmt-01"]},
            ]
        },
        "evidence_only_statement_ids": [],
    })
    with pytest.raises(ValueError, match="Duplicate assertion ID 'risk:capacity-shortage' in risk_updates"):
        x_gates.execute(valid_recap, duplicate_risk_graph, {"meeting_name": "Test"})


def test_x1_blocks_create_mutation_on_prior_context_entity():
    """Verify X1-S enforces that entities known in prior context must overwrite via CONFIRM/MODIFY, never CREATE."""
    from dual_pipeline.transcript_stages import XExitGatesEvaluator
    x_gates = XExitGatesEvaluator()
    valid_recap = (
        "# Subject: Test\n**Attendees:**\n- **PartnerAdvisory:** Drew Oakley\n\n"
        "## Meeting Objective\nAlign on roadmap.\n\n"
        "## Next Steps & Action Items\n| # | Action | Owner | Target | Locator |\n|---|---|---|---|---|\n| 1 | Align | Drew Oakley | 09/01/2026 | 00:01 |\n"
    )
    recreated_entity_graph = json.dumps({
        "schema_version": "5.7",
        "meeting_id": "meeting-01",
        "classification": "internal",
        "audiences": ["partner_advisory"],
        "evidence_index": {},
        "graph_mutations": {
            "entity_upserts": [
                {"id": "org:partner_advisory", "type": "org", "name": "PartnerAdvisory", "mutation": "CREATE"}
            ]
        },
        "evidence_only_statement_ids": [],
    })
    metadata = {
        "meeting_name": "Test",
        "prior_entity_ids": ["org:partner_advisory", "person:dan-okray"]
    }
    with pytest.raises(ValueError, match="Entity 'org:partner_advisory' exists in prior context but declared mutation 'CREATE'"):
        x_gates.execute(valid_recap, recreated_entity_graph, metadata)


def test_x1_passes_valid_in_place_overwrite():
    """Verify X1-S cleanly passes valid in-place overwrites with CONFIRM/MODIFY mutations."""
    from dual_pipeline.transcript_stages import XExitGatesEvaluator
    x_gates = XExitGatesEvaluator()
    valid_recap = (
        "# Subject: Test\n**Attendees:**\n- **PartnerAdvisory:** Drew Oakley\n\n"
        "## Meeting Objective\nAlign on roadmap.\n\n"
        "## Next Steps & Action Items\n| # | Action | Owner | Target | Locator |\n|---|---|---|---|---|\n| 1 | Align | Drew Oakley | 09/01/2026 | 00:01 |\n"
    )
    valid_overwrite_graph = json.dumps({
        "schema_version": "5.7",
        "meeting_id": "meeting-01",
        "classification": "internal",
        "audiences": ["partner_advisory"],
        "evidence_index": {
            "stmt-01": {"speaker": "Drew Oakley", "source_classification": "FACT", "text": "Test statement", "locator": {"kind": "section", "value": "00:01"}}
        },
        "graph_mutations": {
            "entity_upserts": [
                {"id": "org:partner_advisory", "type": "org", "name": "PartnerAdvisory", "mutation": "CONFIRM"}
            ],
            "relationship_signals": [
                {
                    "signal_id": "rel-dan-okray--partner_advisory--member_of",
                    "from_id": "person:dan-okray",
                    "relationship": "member_of",
                    "to_id": "org:partner_advisory",
                    "evidence_ids": ["stmt-01"],
                }
            ],
            "proposal_updates": [
                {
                    "proposal_id": "proposal:q4-wedge",
                    "mutation": "MODIFY",
                    "status": "evaluated",
                    "description": "Updated wedge proposal",
                    "evidence_ids": ["stmt-01"],
                }
            ]
        },
        "evidence_only_statement_ids": [],
    })
    metadata = {
        "meeting_name": "Test",
        "prior_entity_ids": ["org:partner_advisory"]
    }
    assert x_gates.execute(valid_recap, valid_overwrite_graph, metadata) is True


def test_pipeline_shared_room_mic_disambiguation(tmp_path):
    """Verify that RawTranscriptPipeline disambiguates shared room mics when configured in metadata."""
    from pathlib import Path

    from dual_pipeline.transcript_pipeline import RawTranscriptPipeline

    transcript_content = """**Speaker A** [00:01:00]: Let's start the review.
**NEA/APEX (room)** [00:01:10]: What that first thing that we should be targeting is a Q4 milestone, right?
**NEA/APEX (room)** [00:01:30]: Solar 40, batteries between 10 and 20 individual projects. LAMS is nothing more than a database. Can we query that?
**NEA/APEX (room)** [00:01:45]: Yeah.
"""
    input_file = tmp_path / "raw_transcript.txt"
    input_file.write_text(transcript_content, encoding="utf-8")
    out_dir = tmp_path / "output"

    metadata = {
        "meeting_name": "APEX Development Rewire",
        "date": "08/28/2026",
        "time": "02:30 PM",
        "platform": "Teams",
        "classification": "confidential",
        "speaker_map": {
            "Speaker A": "Drew Oakley",
        },
        "shared_room_mics": [
            {
                "mic_label": "JB-E1419-VC",
                "display_label": "NEA/APEX (room)",
                "in_room_attendees": [
                    {
                        "name": "Reid Schenk",
                        "person_id": "person:reid-schenk",
                        "role": "Executive Sponsor",
                        "domain_topics": ["quarterly", "milestone", "q4"],
                    },
                    {
                        "name": "Daniel Brankin",
                        "person_id": "person:daniel-brankin",
                        "role": "Development Lead",
                        "domain_topics": ["solar 40", "batteries", "lams", "query"],
                    },
                ],
            }
        ],
        "mock_disambiguation": True,
    }

    pipeline = RawTranscriptPipeline(provider_type="offline")
    result = pipeline.run(str(input_file), metadata, str(out_dir))

    out1_path = next(p for p in result.values() if "hitl_resolved_transcript" in p)
    content = Path(out1_path).read_text(encoding="utf-8")

    assert "Reid Schenk" in content
    assert "Daniel Brankin" in content
    assert "NEA/APEX (room)" in content


def test_pipeline_mandates_qa_and_judge_outputs(tmp_path):
    """Pipeline must mandatorily produce qa_gates_report.json, llm_judge_evaluation.json, and final_release_decision.json."""
    pipeline = RawTranscriptPipeline(provider_type="offline")
    metadata = {
        "meeting_name": "Executive QA Test",
        "date": "09/10/2026",
        "time": "10:00 AM - 10:05 AM",
        "classification": "internal",
        "attendees": [
            {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Senior Principal"},
            {"name": "Reid Schenk", "org": "Apex Global Enterprises", "role": "VP"},
        ],
    }
    input_file = tmp_path / "transcript.txt"
    input_file.write_text("**Amit Ayer:** Testing mandatory QA and judge outputs.\n**Reid Schenk:** Confirmed.", encoding="utf-8")
    out_dir = tmp_path / "qa_output"

    result = pipeline.run(str(input_file), metadata, str(out_dir))

    # All 6 outputs must exist in result and on disk
    for key in ("01_transcript", "02_recap", "03_graph", "qa_gates_report", "llm_judge_evaluation", "final_release_decision"):
        assert key in result, f"Missing {key} in pipeline result"
        assert Path(result[key]).exists(), f"File {result[key]} was not written to disk"

    # Verify final release decision
    decision_data = json.loads(Path(result["final_release_decision"]).read_text(encoding="utf-8"))
    assert decision_data["authoritative_write_authorized"] is True
    assert decision_data["status"] == "PASS"

    # Verify QA gates report
    qa_data = json.loads(Path(result["qa_gates_report"]).read_text(encoding="utf-8"))
    assert qa_data["status"] == "PASS"
    assert qa_data["output_01_forensic_transcript"]["status"] == "PASS"
    assert qa_data["output_02_executive_recap"]["status"] == "PASS"
    assert qa_data["output_03_canonical_signal_projection"]["status"] == "PASS"
    assert qa_data["cross_artifact_consistency"]["status"] == "PASS"

    # Verify judge report
    judge_data = json.loads(Path(result["llm_judge_evaluation"]).read_text(encoding="utf-8"))
    assert judge_data["status"] == "PASS"
    assert judge_data["output_1_evaluation"]["verdict"] in ("PASS", "PASS_ADVISORY")
    assert judge_data["output_2_evaluation"]["verdict"] in ("PASS", "PASS_ADVISORY")
    assert judge_data["output_3_evaluation"]["verdict"] in ("PASS", "PASS_ADVISORY")


def test_docx_teams_transcript_u0_ingestion(tmp_path):
    """RawTranscriptPipeline extracts and normalizes Microsoft Teams .docx in U0."""
    import zipfile

    from dual_pipeline.transcript_pipeline import RawTranscriptPipeline

    docx_path = tmp_path / "teams_transcript.docx"
    xml_content = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Carlos 10:00</w:t></w:r></w:p>
    <w:p><w:r><w:t>Good morning team, let us review the conduit progress.</w:t></w:r></w:p>
    <w:p><w:r><w:t>Robert 10:01</w:t></w:r></w:p>
    <w:p><w:r><w:t>The pipeline is fully operational.</w:t></w:r></w:p>
  </w:body>
</w:document>"""
    with zipfile.ZipFile(docx_path, "w") as z:
        z.writestr("word/document.xml", xml_content)

    pipeline = RawTranscriptPipeline(provider_type="offline")
    metadata = {
        "meeting_name": "Docx Teams Ingestion Test",
        "date": "08/18/2026",
        "time": "10:00 AM - 11:00 AM",
        "classification": "internal",
        "attendees": [
            {"name": "Carlos", "org": "PartnerAdvisory", "role": "Delivery Lead"},
            {"name": "Robert", "org": "Apex Global Enterprises", "role": "Engineering Manager"},
        ],
    }
    out_dir = tmp_path / "docx_out"
    result = pipeline.run(str(docx_path), metadata, str(out_dir))

    assert "01_transcript" in result
    f1_content = Path(result["01_transcript"]).read_text(encoding="utf-8")
    assert "Carlos" in f1_content
    assert "Robert" in f1_content


def test_multi_judge_retry_remediation_success(tmp_path):
    """Pipeline retries when judge rejects attempt 1 and succeeds on attempt 2 with remediation directive."""
    pipeline = RawTranscriptPipeline(provider_type="offline")
    metadata = {
        "meeting_name": "Retry Remediation Test",
        "date": "09/10/2026",
        "time": "10:00 AM - 10:05 AM",
        "classification": "internal",
        "attendees": [
            {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Senior Principal"},
            {"name": "Reid Schenk", "org": "Apex Global Enterprises", "role": "VP"},
        ],
    }
    input_file = tmp_path / "transcript.txt"
    input_file.write_text("**Amit Ayer:** Testing auto retry.\n**Reid Schenk:** Approved.", encoding="utf-8")
    out_dir = tmp_path / "retry_output"

    attempts = {"count": 0}
    orig_synth_recap = pipeline.l2_recap.execute

    def mock_recap_execute(forensic_transcript, pa_context=None, remediation_directive=None):
        attempts["count"] += 1
        if attempts["count"] == 1:
            # First attempt missing mandatory QA section
            return (
                "# Subject: Incomplete Recap | 09/10/2026\n\n"
                "**Date:** 09/10/2026\n"
                "**Attendees:**\n- Amit Ayer (PartnerAdvisory)\n\n"
                "## Meeting Objective\nValid objective.\n\n"
                "## Key Discussion Points & Findings\n- Point 1\n"
            )
        # Second attempt: verify remediation directive was injected, and return valid recap
        assert remediation_directive is not None
        assert "QA Failures" in remediation_directive
        return orig_synth_recap(forensic_transcript, pa_context=pa_context, remediation_directive=remediation_directive)

    with patch.object(pipeline.l2_recap, "execute", side_effect=mock_recap_execute):
        result = pipeline.run(str(input_file), metadata, str(out_dir), max_judge_retries=2)
        assert attempts["count"] == 2
        decision_data = json.loads(Path(result["final_release_decision"]).read_text(encoding="utf-8"))
        assert decision_data["authoritative_write_authorized"] is True


def test_fail_closed_release_authority_blocks_unauthorized_package(tmp_path):
    """prepare_package and _intake_publish fail-closed when final_release_decision has authoritative_write_authorized=False."""
    import argparse

    from dual_pipeline.cli.handlers import _intake_publish
    from tools.prepare_evidence_envelope_intake import prepare_package

    # Setup mock draft and source
    draft_path = tmp_path / "03_draft.json"
    draft_path.write_text(json.dumps({
        "contract_id": "urn:nee:dual-pipeline:canonical-signal-projection:5.7.0",
        "meeting": {
            "title": "Test",
            "date": "08/18/2026",
            "time": "10:00",
            "classification": "confidential",
            "source_location": "loc/01.md",
        },
        "evidence_index": [],
        "graph_mutations": {"node_mutations": [], "relationship_signals": [], "proposal_updates": []}
    }), encoding="utf-8")

    source_path = tmp_path / "01_transcript.md"
    source_path.write_text("Preserved notes", encoding="utf-8")

    decision_path = tmp_path / "final_release_decision.json"
    decision_path.write_text(json.dumps({
        "status": "REJECTED",
        "authoritative_write_authorized": False,
        "composite_verdict": "FAIL"
    }), encoding="utf-8")

    # 1. prepare_package must reject
    with pytest.raises(RuntimeError, match="Release authorization rejected in"):
        prepare_package(
            draft_path=draft_path,
            source_path=source_path,
            output_path=tmp_path / "pkg.json",
        )

    # 2. _intake_publish must reject
    dummy_pkg = tmp_path / "pkg_fake.json"
    dummy_pkg.write_text("{}", encoding="utf-8")
    publish_args = argparse.Namespace(
        package=[dummy_pkg],
        reviewer="Reviewer",
        rationale="Rationale",
    )
    with pytest.raises(RuntimeError, match="Authoritative release authorization rejected for package"):
        _intake_publish(publish_args)


def test_pipeline_presentation_cross_modal_ingestion(tmp_path):
    """Pipeline must handle presentation cross-modal alignment, compact slide annotation, and intake packaging."""
    from tests.test_pptx_source import _create_synthetic_pptx_bytes

    pptx_bytes = _create_synthetic_pptx_bytes()
    pptx_file = tmp_path / "Strategy_Deck.pptx"
    pptx_file.write_bytes(pptx_bytes)

    transcript_file = tmp_path / "transcript.txt"
    transcript_file.write_text(
        "Maud Boisacq 10:00:00 Welcome everyone. Please look at slide 1 for our strategic vision.\n"
        "Chris Bongiovanni 10:01:00 Thanks Maud. Moving on to slide 2, our operational run rate is high.\n"
        "Murray Gordon 10:02:00 Glad to join the team.\n",
        encoding="utf-8",
    )

    metadata = {
        "meeting_name": "Strategy Review",
        "date": "09/11/2026",
        "time": "10:00 AM - 10:05 AM",
        "classification": "internal",
        "audiences": ["partner_advisory-internal"],
        "meeting_type": "STRATEGIC_DISCOVERY",
    }

    out_dir = tmp_path / "output"
    pipeline = RawTranscriptPipeline(provider_type="offline")
    result = pipeline.run(
        str(transcript_file),
        metadata,
        str(out_dir),
        presentation_path=pptx_file,
    )

    # 1. Output dictionary must include presentation_intake
    assert "presentation_intake" in result
    intake_pkg_path = Path(result["presentation_intake"])
    assert intake_pkg_path.exists()
    assert intake_pkg_path.name == "Strategy_Deck.intake.json"

    # 2. Intake markdown file must exist
    intake_md_path = intake_pkg_path.with_suffix(".md")
    assert intake_md_path.exists()

    # 3. Annotated transcript must contain compact slide annotation
    f1_path = Path(result["01_transcript"])
    f1_text = f1_path.read_text(encoding="utf-8")
    assert "> *[Referenced Presentation Deck: Slide 1: Synthetic Strategy Deck]*" in f1_text
    assert "> *[Referenced Presentation Deck: Slide 2: Market Operations]*" in f1_text





