from dual_pipeline.live_meeting_recorder_core.session_context_packet import SessionContextPacket


def test_two_project_fixture_leaks_nothing():
    # If the packet has empty decisions, open_risks, open_action_items, recent_assertions
    packet = SessionContextPacket(project_scope="Project-A")
    assert packet.is_empty is True

    packet_b = SessionContextPacket(
        project_scope="Project-B",
        open_decisions=[{"text": "decision b"}],
        open_risks=[{"text": "risk b"}],
    )
    # Ensure they are isolated in format
    assert "decision b" in packet_b.format_for_copilot_prompt()
    assert "decision b" not in packet.format_for_copilot_prompt()

def test_session_packet_v2_fields():
    packet = SessionContextPacket(
        project_scope="Proj-X",
        graph_sha256="abc123sha",
        generation=42,
        stale_after=123.45,
        roster=["Jen", "Amit"],
        open_risks=[{"text": "Risk 1", "subject_node_id": "SysA"}]
    )
    assert packet.graph_sha256 == "abc123sha"
    assert packet.generation == 42
    assert "Risk 1" in packet.format_for_copilot_prompt()

    # check C19 quarantine
    packet_c19 = SessionContextPacket(
        open_risks=[{"text": "This is internal_advisory."}]
    )
    # The term 'promoted' should trip C19_FORBIDDEN_MARKERS and empty the list.
    assert len(packet_c19.open_risks) == 0

def test_normalize_project_terms():
    from dual_pipeline.live_meeting_recorder_core.prefetch_query_builder import (
        normalize_project_terms,
    )
    assert normalize_project_terms("") == []
    assert normalize_project_terms("UNRESOLVED") == []
    assert normalize_project_terms("Project-A") == ["project a"]

