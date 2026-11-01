"""Unit, adversarial, and end-to-end verification for Wave 2 extracted automation scanner tools and prompt deduplication."""
from __future__ import annotations

import re
from pathlib import Path

from tools.lint_prompt_ssot import EXPECTED_CANONICAL_PROMPTS, SHARED_PARTIAL_NAME
from tools.run_e2e_deliverable_assurance_scan import (
    scan_ast_wiring_and_swallows,
    scan_storage_and_routing_integrity,
)
from tools.run_testing_gaps_ast_scan import scan_testing_gap_hotspots

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_scan_ast_wiring_and_swallows_detects_broken_lazy_import_and_fabricated_swallow(tmp_path: Path) -> None:
    """Verifies scan_ast_wiring_and_swallows detects broken lazy imports and fabricated COMPLETED swallows in a hermetic fixture."""
    dp = tmp_path / "dual_pipeline" / "cli"
    dp.mkdir(parents=True)
    (dp / "parser.py").write_text(
        'import argparse\np = argparse.ArgumentParser()\np.add_argument("--unwired-flag")\n',
        encoding="utf-8",
    )
    (dp / "ingest.py").write_text('_FLAG_ATTR_MAP = {}\n', encoding="utf-8")

    mod_file = tmp_path / "dual_pipeline" / "broken_worker.py"
    mod_file.write_text(
        "def run_worker():\n"
        "    from dual_pipeline.nonexistent_pkg import MissingSymbol\n"
        "    try:\n"
        "        return MissingSymbol()\n"
        "    except Exception:\n"
        "        return {'status': 'COMPLETED'}\n",
        encoding="utf-8",
    )

    report = scan_ast_wiring_and_swallows(tmp_path)
    assert len(report["lazy_import_defects"]) >= 1
    assert any("nonexistent_pkg" in item for item in report["lazy_import_defects"])
    assert len(report["fabricated_success_swallows"]) == 1
    assert "broken_worker.py" in report["fabricated_success_swallows"][0]
    assert any("--unwired-flag" in item for item in report["unwired_cli_args"])


def test_scan_storage_and_routing_integrity_live_repo() -> None:
    """Verifies scan_storage_and_routing_integrity runs cleanly against the repository."""
    report = scan_storage_and_routing_integrity(REPO_ROOT)
    assert report["graph_integrity_ok"] is True
    assert report["sidecar_integrity_ok"] is True
    assert report["evidence_route_ok"] is True
    assert report["di_route_ok"] is True
    assert report["canary_routes_non_ambiguous"] is True


def test_scan_testing_gap_hotspots_ranks_untested_complex_module(tmp_path: Path) -> None:
    """Verifies scan_testing_gap_hotspots ranks untested branching modules above tested simple modules."""
    dp = tmp_path / "dual_pipeline"
    dp.mkdir(parents=True)
    (dp / "hotspot_mod.py").write_text(
        "def complex_fn(x):\n"
        "    try:\n"
        "        if x > 10 and x < 20:\n"
        "            return x * 2\n"
        "        elif x == 5:\n"
        "            return 0\n"
        "    except Exception:\n"
        "        return -1\n",
        encoding="utf-8",
    )
    (dp / "tested_mod.py").write_text(
        "def simple_fn(x):\n    return x + 1\n",
        encoding="utf-8",
    )
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_simple.py").write_text(
        "from dual_pipeline.tested_mod import simple_fn\n"
        "def test_it():\n"
        "    assert simple_fn(1) == 2\n",
        encoding="utf-8",
    )

    result = scan_testing_gap_hotspots(tmp_path, top_k=10, git_commits=10)
    assert result["total_modules"] == 2
    assert result["untested_modules"] == 1
    assert result["silent_swallow_modules"] == 1
    assert result["top_20_hotspots"][0]["file"] == "dual_pipeline/hotspot_mod.py"
    assert result["top_20_hotspots"][0]["score"] > result["top_20_hotspots"][1]["score"]


def test_scan_testing_gap_hotspots_rejects_zero_assert_and_partial_decoy_tests(tmp_path: Path) -> None:
    """Verifies zero-assertion tests and unused decoy imports do not grant false coverage credit."""
    dp = tmp_path / "dual_pipeline"
    dp.mkdir(parents=True)
    (dp / "decoy_target.py").write_text(
        "def target_fn(x):\n    return x * 3\n",
        encoding="utf-8",
    )
    (dp / "real_target.py").write_text(
        "def real_fn(x):\n    return x + 1\n",
        encoding="utf-8",
    )
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_zero_asserts.py").write_text(
        "from dual_pipeline.decoy_target import target_fn\n"
        "def test_nothing():\n"
        "    target_fn(2)\n",
        encoding="utf-8",
    )
    (tests_dir / "test_mixed_decoy_anti_spoofing.py").write_text(
        "from dual_pipeline.real_target import real_fn\n"
        "import dual_pipeline.decoy_target as unused_decoy\n"
        "def test_real_with_0_199_margin_and_15ms_budget():\n"
        "    margin = 0.199\n"
        "    latency = '15ms'\n"
        "    assert real_fn(1) == 2 and margin < 0.201 and latency == '15ms'\n",
        encoding="utf-8",
    )

    result = scan_testing_gap_hotspots(tmp_path, top_k=10, git_commits=10)
    assert "tests/test_zero_asserts.py" in result["zero_assertion_test_files"]
    assert "tests/test_mixed_decoy_anti_spoofing.py" in result["partial_decoy_test_files"]
    by_file = {row["file"]: row for row in result["top_20_hotspots"]}
    assert by_file["dual_pipeline/decoy_target.py"]["t"] == 0
    assert by_file["dual_pipeline/real_target.py"]["t"] == 1
    pillars = result["consensus_6_pillar_counts"]
    assert pillars["privacy_approval_guards"] >= 1
    assert pillars["budget_speed_guards"] >= 1
    assert pillars["anti_spoofing_scoring"] >= 1


def test_all_17_active_prompts_sequence_alignment_and_word_budget() -> None:
    """Verifies all 17 active prompts have zero /16 or /18 sequence denominators, zero inline 60-line Python blocks, and <33,000 total words."""
    prompts_dir = REPO_ROOT / "automations" / "prompts"
    total_words = 0
    for task_id, rel_path in sorted(EXPECTED_CANONICAL_PROMPTS.items()):
        fpath = prompts_dir / rel_path
        assert fpath.is_file(), f"Missing canonical prompt for {task_id}: {rel_path}"
        text = fpath.read_text(encoding="utf-8")
        total_words += len(text.split())
        assert not re.search(r"Order\s+\d+\s*/\s*(?:16|18)\b", text), (
            f"{rel_path} contains stale /16 or /18 sequence denominator"
        )
        assert "<<'PY'" not in text, f"{rel_path} still contains inline heredoc Python script"

    shared_text = (prompts_dir / SHARED_PARTIAL_NAME).read_text(encoding="utf-8")
    assert not re.search(r"Order\s+\d+\s*/\s*(?:16|18)\b", shared_text)
    assert total_words < 33000, f"Expected total words across 17 active prompts < 33,000, got {total_words}"
