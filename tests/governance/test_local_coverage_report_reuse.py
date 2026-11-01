"""tests/governance/test_local_coverage_report_reuse.py

Unit and regression test suite for Wave 2 local coverage report qualification reuse:
- Same-input reuse without recomputation (0 post-merge audit invocations).
- Altered report byte-digest rejection and safe fallback.
- Altered auditor source rejection.
- Altered quality input baseline report rejection.
- Altered tree digest candidate mismatch rejection.
- Missing or corrupt metadata fallback.
- Premerge threshold failure preservation (fail-closed).
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

from tools.coverage_report_reuse import (
    compute_audited_input_snapshot,
    extract_retained_coverage_audit_metadata,
    get_retained_package_dir,
    hash_file_bytes,
    publish_retained_coverage_report,
    run_and_retain_coverage_audit,
)
from tools.post_merge_report_regenerator import regenerate_testing_gaps_report

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def isolated_fixture_repo(tmp_path: Path) -> Path:
    """Creates a hermetic repository fixture with minimal files for coverage audit."""
    repo = tmp_path / "fixture_repo"
    repo.mkdir()

    # Create directory tree
    (repo / "tools").mkdir()
    (repo / "tests").mkdir()
    (repo / "dual_pipeline").mkdir()
    (repo / "storage").mkdir()
    (repo / "pipeline_a_intake").mkdir()
    (repo / "reports" / "aidlc").mkdir(parents=True)
    (repo / "governance" / "decision_intelligence").mkdir(parents=True)

    # Initialize a git repo so git ls-tree works
    import subprocess
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, capture_output=True, check=True)

    # Copy auditor script into tools
    shutil.copy2(ROOT / "tools" / "audit_testing_gaps.py", repo / "tools" / "audit_testing_gaps.py")

    # Sample production module and test file
    (repo / "dual_pipeline" / "sample_mod.py").write_text("def run():\n    return 42\n", encoding="utf-8")
    (repo / "tests" / "test_sample_e2e.py").write_text(
        "import pytest\ndef test_sample_pipeline_e2e():\n    assert True\n", encoding="utf-8"
    )

    # Quality baseline
    (repo / "reports" / "aidlc" / "mutation_score_baseline.json").write_text(
        json.dumps({"mutation_score_pct": 95.0}), encoding="utf-8"
    )

    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "chore: initial fixture commit"], cwd=repo, capture_output=True, check=True)

    return repo


def test_same_input_reuse(isolated_fixture_repo: Path) -> None:
    """Verifies that an unchanged candidate reuses qualified coverage reports without recomputing."""
    repo = isolated_fixture_repo
    py = sys.executable

    ok, out, dur, meta = run_and_retain_coverage_audit(
        repo_root=repo, py=py, min_e2e_score=10.0, fail_on_threshold=True
    )
    assert ok is True, f"Audit failed (exit != 0):\n{out}"
    assert meta is not None
    assert meta["summary"]["e2e_dimension_score"] >= 10.0

    # Retained package directory must exist
    tree_digest = meta["tree_digest"]
    pkg_dir = get_retained_package_dir(repo, tree_digest)
    assert (pkg_dir / "metadata.json").is_file()
    assert (pkg_dir / "REPO_TESTING_GAPS_AUDIT_REPORT_V1.md").is_file()
    assert (pkg_dir / "testing_gaps_audit_report.json").is_file()

    # Receipt metadata extraction helper must match
    extracted = extract_retained_coverage_audit_metadata(repo)
    assert extracted is not None
    assert extracted["tree_digest"] == tree_digest
    assert extracted["markdown_sha256"] == meta["output_byte_digests"]["markdown_sha256"]

    # Post-merge publishing must reuse retained package with 0 audit invocations
    pub_res = regenerate_testing_gaps_report(repo_root=repo)
    assert pub_res["status"] == "SUCCESS"
    assert pub_res["reused"] is True
    assert pub_res["action"] == "PUBLISHED_RETAINED"
    assert pub_res["audit_invocations"] == 0
    assert Path(pub_res["markdown_report"]).is_file()
    assert Path(pub_res["json_report"]).is_file()


def test_altered_report_rejection_and_fallback(isolated_fixture_repo: Path) -> None:
    """Verifies that tampering with the retained report causes hash mismatch and safe fallback."""
    repo = isolated_fixture_repo
    py = sys.executable

    ok, _, _, meta = run_and_retain_coverage_audit(
        repo_root=repo, py=py, min_e2e_score=10.0, fail_on_threshold=True
    )
    assert ok is True and meta is not None

    tree_digest = meta["tree_digest"]
    pkg_dir = get_retained_package_dir(repo, tree_digest)
    md_file = pkg_dir / "REPO_TESTING_GAPS_AUDIT_REPORT_V1.md"

    # Tamper with markdown report
    md_file.write_text(md_file.read_text(encoding="utf-8") + "\n<!-- TAMPERED -->\n", encoding="utf-8")

    # Publishing must reject the tampered file and fall back to fresh audit
    pub_res = publish_retained_coverage_report(repo)
    assert pub_res["status"] == "FALLBACK_REQUIRED"
    assert "tampered" in pub_res["reason"].lower() or "corrupted" in pub_res["reason"].lower()

    # Full regenerator must safely recompute
    regen_res = regenerate_testing_gaps_report(repo)
    assert regen_res["status"] == "SUCCESS"
    assert regen_res["reused"] is False
    assert regen_res["action"] == "RECOMPUTED_FRESH"
    assert regen_res["audit_invocations"] == 1


def test_altered_auditor_source_rejection(isolated_fixture_repo: Path) -> None:
    """Verifies that modifying the auditor source invalidates prior retained qualification."""
    repo = isolated_fixture_repo
    py = sys.executable

    ok, _, _, meta = run_and_retain_coverage_audit(
        repo_root=repo, py=py, min_e2e_score=10.0, fail_on_threshold=True
    )
    assert ok is True and meta is not None

    # Modify auditor script
    auditor = repo / "tools" / "audit_testing_gaps.py"
    auditor.write_text(auditor.read_text(encoding="utf-8") + "\n# Modified auditor\n", encoding="utf-8")

    pub_res = publish_retained_coverage_report(repo)
    assert pub_res["status"] == "FALLBACK_REQUIRED"
    assert "auditor source code modified" in pub_res["reason"].lower()


def test_altered_quality_input_rejection(isolated_fixture_repo: Path) -> None:
    """Verifies that altering quality input reports invalidates prior retained qualification."""
    repo = isolated_fixture_repo
    py = sys.executable

    ok, _, _, meta = run_and_retain_coverage_audit(
        repo_root=repo, py=py, min_e2e_score=10.0, fail_on_threshold=True
    )
    assert ok is True and meta is not None

    # Modify quality input baseline
    baseline = repo / "reports" / "aidlc" / "mutation_score_baseline.json"
    baseline.write_text(json.dumps({"mutation_score_pct": 50.0}), encoding="utf-8")

    pub_res = publish_retained_coverage_report(repo)
    assert pub_res["status"] == "FALLBACK_REQUIRED"
    assert "quality input report" in pub_res["reason"].lower()


def test_altered_tree_digest_rejection(isolated_fixture_repo: Path) -> None:
    """Verifies that modifying source or test files changes the tree digest and forces a fresh audit."""
    repo = isolated_fixture_repo
    py = sys.executable

    ok, _, _, meta = run_and_retain_coverage_audit(
        repo_root=repo, py=py, min_e2e_score=10.0, fail_on_threshold=True
    )
    assert ok is True and meta is not None

    # Add a new test file to git, changing tree digest
    import subprocess
    (repo / "tests" / "test_another_e2e.py").write_text("def test_extra(): pass\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "feat: add another test"], cwd=repo, capture_output=True, check=True)

    pub_res = publish_retained_coverage_report(repo)
    assert pub_res["status"] == "FALLBACK_REQUIRED"
    assert "no retained coverage package found" in pub_res["reason"].lower()


def test_missing_or_corrupt_metadata_fallback(isolated_fixture_repo: Path) -> None:
    """Verifies that missing or malformed metadata cleanly falls back to fresh audit."""
    repo = isolated_fixture_repo
    py = sys.executable

    ok, _, _, meta = run_and_retain_coverage_audit(
        repo_root=repo, py=py, min_e2e_score=10.0, fail_on_threshold=True
    )
    assert ok is True and meta is not None

    tree_digest = meta["tree_digest"]
    pkg_dir = get_retained_package_dir(repo, tree_digest)
    meta_path = pkg_dir / "metadata.json"

    # Corrupt metadata JSON
    meta_path.write_text("{bad_json:", encoding="utf-8")

    pub_res = publish_retained_coverage_report(repo)
    assert pub_res["status"] == "FALLBACK_REQUIRED"
    assert "corrupt metadata" in pub_res["reason"].lower()


def test_threshold_failure_preservation(isolated_fixture_repo: Path) -> None:
    """Verifies that premerge blocking checks are strictly preserved if score fails threshold."""
    repo = isolated_fixture_repo
    py = sys.executable

    # min_e2e_score set to 100% when fixture only has 1 simple test
    ok, out, dur, meta = run_and_retain_coverage_audit(
        repo_root=repo, py=py, min_e2e_score=100.0, fail_on_threshold=True
    )
    # Must fail closed with ok is False and meta is None
    assert ok is False
    assert meta is None
    assert "FAIL" in out or "below required" in out
