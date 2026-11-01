"""
tests/test_universal_pipeline_dehardcoding.py

Comprehensive tests verifying that the transcript synthesis pipeline is fully
universal with ZERO hardcoded meeting/attendee assumptions:
1. Works across diverse projects and unfamiliar rosters (e.g., Mitch Simmons, Elena Rostova).
2. Traverses active Knowledge Graph edges to retrieve all associated project resources.
3. Spots in-dialogue entity mentions dynamically from Knowledge Graph stakeholders.
4. Generates dynamic name aliases and surgical ALR entries without hardcoded locators or names.
5. Derives meeting slugs and audiences dynamically from domain metadata.
"""
from __future__ import annotations

from pathlib import Path

from dual_pipeline.prompts import (
    OUTPUT1_CONTRACT,
    OUTPUT2_CONTRACT,
    OUTPUT3_CONTRACT,
    MeetingArchetype,
)
from dual_pipeline.query_core.offline_retriever import (
    OfflineGraphContextRetriever,
)
from dual_pipeline.transcript_metadata_helper import (
    scaffold_meeting_metadata,
)
from dual_pipeline.transcript_pipeline_core.surgical_remediator import (
    _maybe_add_dynamic_alr,
)
from dual_pipeline.transcript_qa_core.consistency import (
    get_dynamic_name_aliases,
)


def test_dynamic_graph_traversal_project_resources() -> None:
    """Verifies that OfflineGraphContextRetriever traverses active graph edges to find resources."""
    retriever = OfflineGraphContextRetriever()
    packet = retriever.retrieve_meeting_prior_context(
        project_name="project_lens",
        attendees=["Mitch Simmons (Client Lead)"],
        transcript_text="[00:02:15] Mitch: We need to coordinate with Mandy and Miles on the delivery.",
    )
    assert not packet.is_empty
    entity_names = {e.canonical_name for e in packet.entities}

    # Verify edge-traversed resources from the LENS workstream
    assert "Alex Sanchez" in entity_names
    assert "Tony Kablan" in entity_names
    assert "Brett Gavagni" in entity_names

    # Verify in-dialogue spotted resources from transcript text
    assert "harper Hoffman" in entity_names
    assert "Miles Erickson" in entity_names

    # Verify organization attribution is resolved from graph descriptions
    alex = next(e for e in packet.entities if e.canonical_name == "Alex Sanchez")
    assert alex.organization == "PartnerAdvisory"
    assert "Tech Lead" in str(alex.role) or "tech lead" in str(alex.role).lower()

    brett = next(e for e in packet.entities if e.canonical_name == "Brett Gavagni")
    assert brett.organization == "NEA"

    assert packet.duration_ms < 50.0


def test_scaffold_metadata_unfamiliar_meeting() -> None:
    """Verifies scaffold_meeting_metadata works for unfamiliar meetings without defaulting to Reid/Amit/Justin."""
    sample_text = (
        "Meeting Transcript\n"
        "Date: 09/21/2026\n\n"
        "Attendees:\n"
        "- Mitch Simmons (VP Engineering, Apex Global Enterprises)\n"
        "- Dr. Elena Rostova (Chief AI Scientist, Quantum Core)\n\n"
        "[00:00:10] Mitch Simmons: Welcome Elena to our architecture sync.\n"
        "[00:00:25] Dr. Elena Rostova: Thank you Mitch, happy to be here."
    )
    temp_file = Path("/tmp/test_unfamiliar_meeting_input.txt")
    temp_file.write_text(sample_text, encoding="utf-8")

    try:
        meta = scaffold_meeting_metadata(
            input_path=temp_file,
            project="quantum_pilot",
            meeting_name="Quantum_Architecture_Review",
            date_str="09/21/2026",
            attendees=["Mitch Simmons", "Dr. Elena Rostova"],
        )

        assert meta["project"] == "quantum_pilot"
        assert meta["date"] == "09/21/2026"
        assert meta["date_filename"] == "09-21-2026"
        assert meta["meeting_name"] == "Quantum_Architecture_Review"

        # Audiences should be dynamically derived, not hardcoded to partner_advisory/nexus/nea
        assert "partner_advisory-internal" not in meta["audiences"]
        assert any("quantum-pilot" in aud for aud in meta["audiences"])

        # Attendees should not have Reid Schenk, Quinn Mercer, or Taylor Mercer injected
        all_att_names = str(meta.get("attendees", ""))
        assert "Reid Schenk" not in all_att_names
        assert "Quinn Mercer" not in all_att_names
        assert "Taylor Mercer" not in all_att_names
    finally:
        if temp_file.exists():
            temp_file.unlink()


def test_dynamic_name_aliases_for_arbitrary_roster() -> None:
    """Verifies dynamic name alias derivation generates accurate variations for any person."""
    aliases = get_dynamic_name_aliases("Dr. Elena Rostova", {})
    assert "Elena" in aliases
    assert "Rostova" in aliases
    assert "Dr. Elena Rostova" in aliases

    mitch_aliases = get_dynamic_name_aliases("Mitch Simmons", {})
    assert "Mitch" in mitch_aliases
    assert "Simmons" in mitch_aliases

    # When graph aliases are present in metadata, they are incorporated
    meta_with_aliases = {
        "graph_aliases": {
            "Mitch Simmons": ["Mitchell", "M. Simmons"],
        }
    }
    mitch_graph_aliases = get_dynamic_name_aliases("Mitch Simmons", meta_with_aliases)
    assert "Mitchell" in mitch_graph_aliases
    assert "M. Simmons" in mitch_graph_aliases


def test_surgical_remediator_dynamic_alr() -> None:
    """Verifies surgical remediator patches ALR entries dynamically for arbitrary attendees."""
    current_text = (
        "# Forensic Transcript\n\n"
        "[00:01:00] Mitch: We need to verify the algorithm.\n\n"
        "## Ambiguity Lock Register\n\n"
        "| Item # | Identified Ambiguity | Source Locator | Affected Statement IDs | Final Resolved Record | Resolution Basis |\n"
        "| :--- | :--- | :--- | :--- | :--- | :--- |\n"
        "| ALR-01 | Audio dropout | [00:00:30] | stmt-0001 | Verified line | Speaker consensus |\n"
    )
    alr_section = current_text.split("## Ambiguity Lock Register", 1)[-1]
    critique = (
        "Output 01 ALR does not resolve the ambiguous vocative reference to 'Elena' at line [00:01:00]. "
        "Rule C-07 violation."
    )
    audit: list[str] = []

    new_text, new_alr = _maybe_add_dynamic_alr(current_text, alr_section, critique, audit)

    assert len(audit) >= 1
    assert "ALR-02" in new_alr or "Elena" in new_alr
    assert "Elena" in new_text


def test_output_contract_archetype_inference_no_hardcoded_slugs() -> None:
    """Verifies archetype inference does not rely on meeting-specific person slugs."""
    # System review without client should infer ARCHITECTURE_DEEP_DIVE from keywords, not person names
    text = "Internal engineering deep dive on pipeline algorithms and state machines"
    archetype = OUTPUT1_CONTRACT.detect_archetype(title="Technical Deep Dive", text=text)
    assert archetype == MeetingArchetype.ARCHITECTURE_DEEP_DIVE

    # Unrelated meeting with attendee names should not be misclassified as ARCHITECTURE_DEEP_DIVE
    generic_text = "Executive relationship and steering committee discussion with partners"
    generic_arch = OUTPUT1_CONTRACT.detect_archetype(title="Executive Committee", text=generic_text)
    assert generic_arch == MeetingArchetype.GOVERNANCE_REVIEW


def test_client_facing_architecture_deep_dive_detection() -> None:
    """Verifies client-facing architecture sessions with client tokens classify as ARCHITECTURE_DEEP_DIVE."""
    client_tech_text = (
        "Client leadership review with APEX: discussing architecture deep dive on system boundaries, "
        "microservices, database schemas, and architectural trade-off analysis with the client technical team."
    )
    arch = OUTPUT1_CONTRACT.detect_archetype(
        title="APEX Architecture Review & System Deep Dive",
        text=client_tech_text,
    )
    assert arch == MeetingArchetype.ARCHITECTURE_DEEP_DIVE


def test_all_five_archetypes_inference_coverage() -> None:
    """Verifies that all 5 canonical archetypes resolve deterministically from representative inputs."""
    samples = {
        MeetingArchetype.ARCHITECTURE_DEEP_DIVE: (
            "Infrastructure Pipeline Review",
            "Technical review of cloud data pipeline components, architectural trade-offs, and latency.",
        ),
        MeetingArchetype.STRATEGIC_DISCOVERY: (
            "Executive Stakeholder Discovery",
            "Initial discovery working session and stakeholder interview assessing strategic goals.",
        ),
        MeetingArchetype.GOVERNANCE_REVIEW: (
            "Steering Committee Gate Review",
            "Executive decision gate review and policy ratification session for production release.",
        ),
        MeetingArchetype.TACTICAL_ALIGNMENT: (
            "Daily Team Standup & Sprint Planning",
            "Sprint backlog grooming, standup blockers, and release deployment schedule tracking.",
        ),
        MeetingArchetype.LESSONS_LEARNED: (
            "Incident Retrospective & Postmortem",
            "Outage root cause analysis, incident retrospective, and lessons learned review.",
        ),
    }
    for expected_arch, (title, text) in samples.items():
        resolved = OUTPUT1_CONTRACT.detect_archetype(title=title, text=text)
        assert resolved == expected_arch, f"Failed for {expected_arch}: got {resolved}"


def test_architecture_deep_dive_overrides_have_zero_fixture_leakage() -> None:
    """Verifies Stage 02 and 03 override files and compiled prompts have zero hardcoded Project LENS fixtures."""
    from dual_pipeline.prompts.registry import PROMPT_FILE_MAP
    stage_02_path = PROMPT_FILE_MAP["STAGE_02_ARCHITECTURE_DEEP_DIVE_OVERRIDE"]
    stage_03_path = PROMPT_FILE_MAP["STAGE_03_ARCHITECTURE_DEEP_DIVE_OVERRIDE"]
    over_2 = stage_02_path.read_text(encoding="utf-8")
    over_3 = stage_03_path.read_text(encoding="utf-8")

    assert "INTERNAL_TECHNICAL_SYNC" not in over_2
    assert "Jen Pan" not in over_2
    assert "Jake Stark" not in over_2
    assert "Alex Sanchez" not in over_2

    for token in ["maximo", "20.2%", "01:04:24", "lens-rule-builder", "amazon-textract"]:
        assert token.lower() not in over_3.lower(), f"Found '{token}' in Stage 03 override"

    p3 = OUTPUT3_CONTRACT.compile_stage_prompt(MeetingArchetype.ARCHITECTURE_DEEP_DIVE)
    for token in ["maximo", "20.2%", "01:04:24", "lens-rule-builder", "amazon-textract"]:
        assert token.lower() not in p3.lower(), f"Found '{token}' in Stage 03 compiled prompt"


def test_scaffold_metadata_infers_and_persists_archetype() -> None:
    """Verifies scaffold_meeting_metadata automatically resolves and populates archetype and meeting_type."""
    sample_text = (
        "Meeting Transcript\n"
        "Date: 10/14/2026\n"
        "Attendees:\n- Alice Engineer\n- Bob Architect\n\n"
        "[00:00:10] Alice: Welcome to our technical architecture deep dive and system review.\n"
    )
    temp_file = Path("/tmp/test_scaffold_arch_input.txt")
    temp_file.write_text(sample_text, encoding="utf-8")
    try:
        meta = scaffold_meeting_metadata(
            input_path=temp_file,
            meeting_name="Core_Service_Architecture_Review",
            date_str="10/14/2026",
            attendees=["Alice Engineer", "Bob Architect"],
        )
        assert meta["archetype"] == MeetingArchetype.ARCHITECTURE_DEEP_DIVE.value
        assert meta["meeting_type"] == MeetingArchetype.ARCHITECTURE_DEEP_DIVE.value
        assert meta["meeting_type_hint"] == MeetingArchetype.ARCHITECTURE_DEEP_DIVE.value
    finally:
        if temp_file.exists():
            temp_file.unlink()

