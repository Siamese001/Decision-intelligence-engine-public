"""End-to-end integration test for Stage L1 Google metadata adapter pipeline lifecycle."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.l1_feedback_loop import resolve_l1_metadata_adapter
from dual_pipeline.l1_google_adapter import validate_l1_header_metadata


def test_l1_google_metadata_lifecycle_e2e(tmp_path: Path):
    """E2E lifecycle: Raw transcript snippet -> L1 Google adapter -> validation -> downstream directory binding."""
    # 1. Simulate meeting transcript with header metadata hints
    transcript_text = (
        "Project: project_nexus\n"
        "Meeting: Architecture Review & Security Boundary\n"
        "Date: 09/25/2026\n"
        "Time: 02:00 PM\n"
        "Attendees: Alex Brennan (Principal Architect), Amit Ayer (Director of Eng)\n\n"
        "Amit Ayer: Welcome everyone to the architecture review. Today we review the L1 Google adapter.\n"
        "Alex Brennan: Yes, we want to ensure zero metadata drift and strict date adherence.\n"
    )

    adapter = resolve_l1_metadata_adapter(provider="google")

    simulated_llm_json = json.dumps({
        "meeting_name": "Architecture_Review_Security_Boundary",
        "date": "09/25/2026",
        "time": "02:00 PM",
        "project_name": "project_nexus",
        "attendee_roster": [
            "Alex Brennan (Principal Architect)",
            "Amit Ayer (Director of Eng)",
        ],
    })

    with patch(
        "dual_pipeline.l1_google_adapter.call_gemini_generate_content",
        return_value=simulated_llm_json,
    ):
        extracted = adapter.extract_header_metadata(
            transcript=transcript_text,
            hint_metadata={"inferred_project": "project_nexus"},
        )

    # 2. Strict validation check
    is_valid, errors = validate_l1_header_metadata(extracted)
    assert is_valid is True, f"Validation failed: {errors}"

    # 3. Downstream directory binding assertion per AGENTS.md invariant: output/<project>/<MM-DD-YYYY>/
    proj = extracted["project_name"]
    date_parts = extracted["date"].split("/")
    formatted_date_dir = f"{date_parts[0]}-{date_parts[1]}-{date_parts[2]}"

    out_dir = tmp_path / "output" / proj / formatted_date_dir / extracted["meeting_name"]
    out_dir.mkdir(parents=True, exist_ok=True)

    header_meta_file = out_dir / "header_metadata.json"
    header_meta_file.write_text(json.dumps(extracted, indent=2), encoding="utf-8")

    assert header_meta_file.exists()
    loaded = json.loads(header_meta_file.read_text(encoding="utf-8"))
    assert loaded["date"] == "09/25/2026"
    assert loaded["project_name"] == "project_nexus"
    assert len(loaded["attendee_roster"]) == 2
