"""
tests/test_intelligent_automation_hierarchy.py

Unit tests verifying:
1. Flexible dotted date normalization (e.g. 8.27.2026, 9.14.2026).
2. Category-based output directory resolution (e.g. output/nea/readouts/08-27-2026/Chakra).
3. Spaces in category directories (e.g. output/nea/Client Interview Prep/10-02-2026/meeting_1).
4. Shorter, impactful deliverable filenames embedding date (01_*_Transcript.md, 02_*_Recap.md, 03_*_Signals.json).
5. Backward compatibility for legacy flat and subfoldered project paths.
"""
from pathlib import Path

from dual_pipeline.meeting_paths import (
    MeetingPathEnvelope,
    normalize_meeting_date,
)


def test_dotted_date_normalization():
    """Verify normalize_meeting_date parses dot-separated dates with and without leading zeros."""
    dir_name, disp = normalize_meeting_date("8.27.2026")
    assert dir_name == "08-27-2026"
    assert disp == "08/27/2026"

    dir_name2, disp2 = normalize_meeting_date("09.14.2026")
    assert dir_name2 == "10-02-2026"
    assert disp2 == "09/14/2026"


def test_category_folder_resolution(tmp_path: Path):
    """Verify category parameter properly nests output directory under project/category/date/subfolder."""
    env = MeetingPathEnvelope.resolve(
        project_name="nea",
        meeting_date="8.27.2026",
        meeting_name="Chakra",
        repo_root=tmp_path,
        category="readouts",
        subfolder=True,
    )
    expected_out = tmp_path / "output" / "nea" / "readouts" / "08-27-2026" / "Chakra"
    assert env.output_dir == expected_out
    assert env.category == "readouts"
    assert env.subfolder == "Chakra"
    assert env.deliverable_01.name == f"01_{env.stem}_hitl_resolved_transcript.md"


def test_category_with_spaces_resolution(tmp_path: Path):
    """Verify categories containing spaces are preserved cleanly in filesystem paths."""
    env = MeetingPathEnvelope.resolve(
        project_name="nea",
        meeting_date="9.14.2026",
        meeting_name="Meeting_1_Kannan_Debrief",
        repo_root=tmp_path,
        category="Client Interview Prep",
        subfolder=True,
    )
    expected_out = tmp_path / "output" / "nea" / "Client Interview Prep" / "10-02-2026" / "Meeting_1_Kannan_Debrief"
    assert env.output_dir == expected_out
    assert env.category == "Client Interview Prep"


def test_shorter_impactful_deliverables(tmp_path: Path):
    """Verify short_deliverables flag generates concise, impactful deliverable filenames embedding date."""
    env = MeetingPathEnvelope.resolve(
        project_name="nea",
        meeting_date="8.27.2026",
        meeting_name="NEA_Chakra_Operating_Model",
        repo_root=tmp_path,
        category="readouts",
        subfolder="Chakra",
        short_deliverables=True,
    )
    # Redundant project prefix "nea_" stripped, time string omitted from filename
    assert env.deliverable_01.name == "01_Chakra_Operating_Model_08-27-2026_Transcript.md"
    assert env.deliverable_02.name == "02_Chakra_Operating_Model_08-27-2026_Recap.md"
    assert env.deliverable_03.name == "03_Chakra_Operating_Model_08-27-2026_Signals.json"


def test_backward_compatibility_flat_resolution(tmp_path: Path):
    """Verify legacy resolution without category remains 100% backward compatible."""
    env = MeetingPathEnvelope.resolve(
        project_name="nea",
        meeting_date="08-27-2026",
        meeting_name="Chakra_Operating_Model",
        repo_root=tmp_path,
    )
    expected_out = tmp_path / "output" / "nea" / "08-27-2026"
    assert env.output_dir == expected_out
    assert env.category is None
    assert env.deliverable_01.name.endswith("_hitl_resolved_transcript.md")


def test_validate_output_hierarchy_with_category_and_short_deliverables(tmp_path: Path):
    """Verify validate_output_hierarchy validates category structures and shorter deliverables."""
    from tools.validate_output_hierarchy import validate_output_hierarchy

    out_root = tmp_path / "output"
    meeting_dir = out_root / "nea" / "readouts" / "08-27-2026" / "Chakra"
    meeting_dir.mkdir(parents=True, exist_ok=True)

    (meeting_dir / "01_Chakra_08-27-2026_Transcript.md").write_text("# Transcript", encoding="utf-8")
    (meeting_dir / "02_Chakra_08-27-2026_Recap.md").write_text("# Recap", encoding="utf-8")
    (meeting_dir / "03_Chakra_08-27-2026_Signals.json").write_text("{}", encoding="utf-8")

    (meeting_dir / "qa_gates_report.json").write_text("{}", encoding="utf-8")
    (meeting_dir / "llm_judge_evaluation.json").write_text("{}", encoding="utf-8")
    (meeting_dir / "final_release_decision.json").write_text("{}", encoding="utf-8")
    (meeting_dir / "intake_package.json").write_text("{}", encoding="utf-8")

    violations = validate_output_hierarchy(out_root, project="nea")
    assert violations == []
