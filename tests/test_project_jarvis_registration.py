"""Unit and regression tests for project_jarvis registration and alias resolution."""
from __future__ import annotations

import pytest
from dual_pipeline.meeting_paths import MeetingPathEnvelope
from dual_pipeline.parameters import get_known_projects, resolve_project_name


def test_project_jarvis_is_in_known_projects() -> None:
    known = get_known_projects()
    assert "project_jarvis" in known


def test_jarvis_aliases_resolve_to_project_jarvis() -> None:
    assert resolve_project_name("jarvis") == "project_jarvis"
    assert resolve_project_name("JARVIS") == "project_jarvis"
    assert resolve_project_name("project jarvis") == "project_jarvis"
    assert resolve_project_name("project_jarvis") == "project_jarvis"
    assert resolve_project_name("project jarvis") == "project_jarvis"
    assert resolve_project_name("project_jarvis") == "project_jarvis"


def test_meeting_path_envelope_resolves_project_jarvis() -> None:
    env = MeetingPathEnvelope.resolve(
        project_name="project_jarvis",
        meeting_name="JARVIS STANDUP",
        meeting_date="10-09-2026",
        meeting_time="0900AM",
    )
    assert env.project_name == "project_jarvis"
    assert "project_jarvis" in str(env.output_dir)
    assert env.deliverable_01.name.startswith("01_")


def test_unknown_project_still_fails_closed() -> None:
    with pytest.raises(ValueError, match="Unknown project identifier"):
        MeetingPathEnvelope.resolve(
            project_name="unregistered_synthetic_project",
            meeting_name="Sample",
            meeting_date="10-09-2026",
        )


def test_infer_project_from_title() -> None:
    from dual_pipeline.project_aliases import infer_project_from_title

    assert infer_project_from_title("JARVIS STANDUP") == "project_jarvis"
    assert infer_project_from_title("jarvis weekly sync") == "project_jarvis"
    assert infer_project_from_title("Nexus Architecture") == "project_nexus"
    assert infer_project_from_title("LENS Discussion") == "project_lens"
    assert infer_project_from_title("Project Beacon Review") == "project_beacon"
    assert infer_project_from_title("Generic Architecture Call") is None
    assert infer_project_from_title("") is None
    assert infer_project_from_title(None) is None

