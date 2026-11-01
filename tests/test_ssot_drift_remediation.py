"""Deterministic unit and regression tests for SSOT drift and tech debt remediation."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from dual_pipeline.audio_ingestion_core.audio_asr_cache import load_audio_asr_cache, save_audio_asr_cache
from dual_pipeline.ambiguity_core.detector_d2_vocabulary import _match_single_word
from dual_pipeline.graph_scout_core.matching import _string_similarity
from dual_pipeline.hitl_governance_core.crypto import create_signed_hitl_approval_entry
from tools.audit_judge_criteria import analyze_record
from tools.validate_hitl_ledger_integrity import ZERO_HASH, _validate_record


def test_v4_document_mode_floor_parity_revalidation() -> None:
    """Verify DOCUMENT_MODE Output 03 revalidation delegates floor checks to OUTPUT3_CONTRACT."""
    data = {
        "mode": "document",
        "overall_verdict": "PASS",
        "output_1_evaluation": {"status": "NOT_APPLICABLE", "reason": "NOT_APPLICABLE_DOCUMENT_MODE"},
        "output_2_evaluation": {"status": "NOT_APPLICABLE", "reason": "NOT_APPLICABLE_DOCUMENT_MODE"},
        "output_3_evaluation": {
            "meeting_archetype": "DOCUMENT_MODE",
            "dimension_1_strategic_edge": 15,
            "dimension_2_knowledge_layer_density": 10,
            "dimension_3_semantic_atomicity": 20,
            "dimension_4_predicate_specificity": 15,
            "dimension_5_exclusion_of_action_items": 15,
            "dimension_6_evidence_grounding_and_schema": 20,
            "composite_score": 95,
            "verdict": "PASS",
        },
    }
    res = analyze_record("2026-10-25T00:00:00Z", Path("output/doc/llm_judge_evaluation.json"), data, revalidate=True)
    assert res["defects"]["F2_floor_breach"] is False
    assert res["verdicts"]["output_3"] == "PASS"


def test_hitl_approval_v1_alr_digest_validation() -> None:
    """Verify validate_hitl_ledger_integrity accepts signed hitl-approval/v1 ALR digests."""
    entry = create_signed_hitl_approval_entry(
        subject_sha256="a" * 64,
        case_ids=["test-case"],
        decision="APPROVE",
    )
    entry["sequence_number"] = 0
    entry["previous_entry_hash"] = ZERO_HASH
    errs, next_hash = _validate_record(0, entry, ZERO_HASH)
    assert errs == []
    assert next_hash == entry["entry_digest"]


def test_unified_pipeline_cli_module_execution() -> None:
    """Verify python -m unified_pipeline.cli --help executes cleanly without relative import errors."""
    repo_root = Path(__file__).resolve().parents[1]
    res = subprocess.run(
        [sys.executable, "-m", "unified_pipeline.cli", "--help"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert res.returncode == 0
    assert "usage:" in res.stdout.lower()


def test_d2_atomic_asr_cache_and_v4_rapidfuzz_similarity(tmp_path: Path) -> None:
    """Verify atomic ASR sidecar cache persistence and rapidfuzz string similarity helpers."""
    target = save_audio_asr_cache(
        audio_sha256="a" * 64,
        raw_segments=[{"start": 0.0, "end": 1.0, "text": "Hello world"}],
        model_name="mlx-community/whisper-large-v3-mlx",
        language="en",
        stem="meeting",
        cache_dir=tmp_path,
    )
    assert target.is_file()
    loaded = load_audio_asr_cache(
        audio_sha256="a" * 64,
        model_name="mlx-community/whisper-large-v3-mlx",
        language="en",
        stem="meeting",
        cache_dir=tmp_path,
    )
    assert loaded is not None
    assert loaded[0]["text"] == "Hello world"

    assert _string_similarity("Apex Global Enterprises", "Apex Global Enterprises") == 1.0
    assert _match_single_word("apex_global", "Apex Global", ["NEE"]) is None
    assert _match_single_word("APEX GLOBA", "Apex Global", []) is not None


def test_ssot_drift_remediation_prompt_includes_config_anti_sprawl_domain() -> None:
    """Verify ssot_drift_remediation_agent.md enforces Sub-Domain E (C_CFG_1-C_CFG_6) config centralization."""
    repo_root = Path(__file__).resolve().parents[1]
    prompt_text = (repo_root / "automations" / "prompts" / "ssot_drift_remediation_agent.md").read_text(encoding="utf-8")
    for token in (
        "Sub-Domain E: Hardcoded Configuration Discovery, Anti-Config-Sprawl Audit & Centralized SSOT Consolidation",
        "C_CFG_1",
        "C_CFG_2",
        "C_CFG_3",
        "C_CFG_4",
        "C_CFG_5",
        "C_CFG_6",
        "lint_hardcoded_parameters.py --all",
        "lint_parameter_references.py --all --check-dead",
        "SSOT_CONFINED_ASSIGNMENTS",
    ):
        assert token in prompt_text

    from dual_pipeline.parameters import get_runtime_infra_parameters

    infra = get_runtime_infra_parameters()
    assert infra["max_retrieval_workers"] == 16
    assert infra["healing_max_retries"] == 3
    assert infra["connection_pool_size"] == 16
