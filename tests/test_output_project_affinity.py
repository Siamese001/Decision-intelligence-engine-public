"""tests/test_output_project_affinity.py

Tests project affinity validation in tools/validate_output_hierarchy.py:
Ensures deliverable files with explicit project prefixes (NEA_, EA_, Lens_, NEET_, etc.)
are prevented from being placed in mismatched project directories, while respecting
governed cross-stream aliases (e.g. nexus_reid_nea deliverables under project_nexus).
"""

from __future__ import annotations

from pathlib import Path

from tools.validate_output_hierarchy import (
    _validate_meeting_container,
    _validate_project_affinity,
)


def test_validate_project_affinity_unit():
    """Unit tests for _validate_project_affinity logic."""
    # Mismatches: NEA in enterprise_architecture
    err = _validate_project_affinity(
        "NEA_Agentic_Governance_Evals_09-02-2026_0400PM_0430PM_ET",
        "enterprise_architecture",
        "enterprise_architecture/09-02-2026",
    )
    assert err is not None
    assert "[PROJECT_AFFINITY_MISMATCH]" in err
    assert "prefix 'nea'" in err
    assert "enterprise_architecture" in err

    # Valid: NEA in nea
    assert _validate_project_affinity(
        "NEA_Agentic_Governance_Evals_09-02-2026_0400PM_0430PM_ET",
        "nea",
        "nea/09-02-2026/NEA_Agentic_Governance_Evals",
    ) is None

    # Valid: NEA cross-stream in project_nexus
    assert _validate_project_affinity(
        "NEA_Screener_Sync_09-01-2026_153232",
        "project_nexus",
        "project_nexus/09-01-2026",
    ) is None

    # Mismatches: Lens in nea
    err_lens = _validate_project_affinity(
        "Project_Lens_SOW1_Observability_Catchup_09-15-2026",
        "nea",
        "nea/09-15-2026",
    )
    assert err_lens is not None
    assert "[PROJECT_AFFINITY_MISMATCH]" in err_lens

    # Valid: Lens in project_lens
    assert _validate_project_affinity(
        "Project_Lens_SOW1_Observability_Catchup_09-15-2026",
        "project_lens",
        "project_lens/09-15-2026",
    ) is None

    # Mismatches: EA in nea
    err_ea = _validate_project_affinity(
        "EA_AI_Policy_Strategy_07-15-2026_1200PM_1230PM_ET",
        "nea",
        "nea/07-15-2026",
    )
    assert err_ea is not None
    assert "[PROJECT_AFFINITY_MISMATCH]" in err_ea

    # Neutral / Unprefixed: passes regardless of project
    assert _validate_project_affinity(
        "Phase1_Designs_Jen_Amit_09-15-2026",
        "project_lens",
        "project_lens/09-15-2026",
    ) is None
    assert _validate_project_affinity(
        "Phase1_Designs_Jen_Amit_09-15-2026",
        "nea",
        "nea/09-15-2026",
    ) is None


def test_meeting_container_fails_on_misplaced_nea_meeting(tmp_path: Path):
    """Verifies that an NEA meeting placed in enterprise_architecture fails closed."""
    ea_dir = tmp_path / "enterprise_architecture" / "09-02-2026"
    ea_dir.mkdir(parents=True)

    # Deliverables with NEA_ prefix in EA directory
    (ea_dir / "01_NEA_Agentic_Governance_Evals_09-02-2026_hitl_resolved_transcript.md").write_text("# Transcript", encoding="utf-8")
    (ea_dir / "02_NEA_Agentic_Governance_Evals_09-02-2026_executive_recap.md").write_text("# Recap", encoding="utf-8")
    (ea_dir / "03_NEA_Agentic_Governance_Evals_09-02-2026_canonical_signal_projection.json").write_text("{}", encoding="utf-8")
    (ea_dir / "qa_gates_report.json").write_text("{}", encoding="utf-8")
    (ea_dir / "llm_judge_evaluation.json").write_text("{}", encoding="utf-8")
    (ea_dir / "final_release_decision.json").write_text('{"authoritative_write_authorized": true}', encoding="utf-8")
    (ea_dir / "intake_package.json").write_text("{}", encoding="utf-8")

    violations = _validate_meeting_container(
        container=ea_dir,
        proj_name="enterprise_architecture",
        meeting_date="09-02-2026",
        container_label="enterprise_architecture/09-02-2026",
    )

    affinity_violations = [v for v in violations if "[PROJECT_AFFINITY_MISMATCH]" in v]
    assert len(affinity_violations) > 0, f"Expected PROJECT_AFFINITY_MISMATCH, got: {violations}"
    assert "prefix 'nea'" in affinity_violations[0]
    assert "enterprise_architecture" in affinity_violations[0]


def test_meeting_container_passes_on_correct_nea_meeting(tmp_path: Path):
    """Verifies that an NEA meeting placed in nea directory passes cleanly."""
    nea_dir = tmp_path / "nea" / "09-02-2026" / "NEA_Agentic_Governance_Evals"
    nea_dir.mkdir(parents=True)

    (nea_dir / "01_NEA_Agentic_Governance_Evals_09-02-2026_hitl_resolved_transcript.md").write_text("# Transcript", encoding="utf-8")
    (nea_dir / "02_NEA_Agentic_Governance_Evals_09-02-2026_executive_recap.md").write_text("# Recap", encoding="utf-8")
    (nea_dir / "03_NEA_Agentic_Governance_Evals_09-02-2026_canonical_signal_projection.json").write_text("{}", encoding="utf-8")
    (nea_dir / "qa_gates_report.json").write_text("{}", encoding="utf-8")
    (nea_dir / "llm_judge_evaluation.json").write_text("{}", encoding="utf-8")
    (nea_dir / "final_release_decision.json").write_text('{"authoritative_write_authorized": true}', encoding="utf-8")
    (nea_dir / "intake_package.json").write_text("{}", encoding="utf-8")

    violations = _validate_meeting_container(
        container=nea_dir,
        proj_name="nea",
        meeting_date="09-02-2026",
        container_label="nea/09-02-2026/NEA_Agentic_Governance_Evals",
    )

    affinity_violations = [v for v in violations if "[PROJECT_AFFINITY_MISMATCH]" in v]
    assert len(affinity_violations) == 0, f"Unexpected violations: {violations}"
