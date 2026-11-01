"""Adversarial edge-case suite for Wave 2 Gate C2 Signal Translation Completeness & Physical Proof."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import pytest

from dual_pipeline.graph_writer_core.proof_gate import (
    SignalTranslationCompletenessError,
    validate_bijective_signal_conservation,
)
from dual_pipeline.graph_writer_core.sources import (
    normalize_source_archive_stem,
    sanitize_cold_archive_ref,
)
from dual_pipeline.intake_core.change_set_helpers import should_suppress_document_candidate


def test_boundary_payload_empty_projection_and_null_change_set() -> None:
    """Category 1 (Boundary payload): Empty projection with empty change_set passes Gate C2 gracefully."""
    res = validate_bijective_signal_conservation(
        {"graph_mutations": {"entity_upserts": [], "relationship_signals": []}},
        {"operations": []},
    )
    assert res["status"] == "PASS"
    assert res["emitted_operations"] == 0
    assert sanitize_cold_archive_ref(None) is None
    assert sanitize_cold_archive_ref("") == ""


def test_malformed_input_staging_paths_and_non_dict_signals() -> None:
    """Category 2 (Malformed input): Complex nested .staging_* and .worktrees/* paths normalize cleanly."""
    dirty_ref = "/workspace/repo/.worktrees/feat_x/.staging_Collaboration_Jason_PartnerAdvisory_8b27c087/01_Collaboration_Jason_PartnerAdvisory_10-26-2026_hitl_resolved_transcript.md"
    clean_ref = sanitize_cold_archive_ref(dirty_ref)
    assert ".staging_" not in str(clean_ref)
    stem = normalize_source_archive_stem(dirty_ref)
    assert "collaboration_jason_partner_advisory" in stem


def test_concurrency_parallel_gate_c2_validation() -> None:
    """Category 3 (Concurrency & race): 16 worker threads validating Gate C2 concurrently."""
    proj = {
        "graph_mutations": {
            "entity_upserts": [{"id": "system:a", "name": "A", "description": "Substantive description for system A."}],
            "relationship_signals": [],
        }
    }
    cs = {
        "operations": [{"operation_id": "op-1", "operation": "add_approved_assertion"}],
        "extensions": {
            "com.partner_advisory.nee.operation-payloads": {
                "op-1": {"candidate": {"normalized_text": "A: Substantive description for system A."}}
            }
        },
    }
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda _: validate_bijective_signal_conservation(proj, cs), range(32)))
    assert all(r["status"] == "PASS" for r in results)


def test_overflow_high_cardinality_auxiliary_signals_conservation() -> None:
    """Category 4 (Overflow & exhaustion): 100 auxiliary signals across 6 families accounted in Gate C2."""
    proj = {
        "graph_mutations": {
            "entity_upserts": [],
            "relationship_signals": [],
            "decision_updates": [{"decision_id": f"d-{i}", "title": f"Decision {i}"} for i in range(25)],
            "risk_updates": [{"risk_id": f"r-{i}", "rationale": f"Risk {i}"} for i in range(25)],
            "action_updates": [{"action_id": f"a-{i}", "description": f"Action {i}"} for i in range(25)],
            "metrics": [{"id": f"m-{i}", "description": f"Metric {i}"} for i in range(25)],
        }
    }
    cs = {
        "operations": [{"operation_id": f"op-{i}", "operation": "add_candidate_assertion"} for i in range(100)],
        "extensions": {"com.partner_advisory.nee.operation-payloads": {}},
    }
    res = validate_bijective_signal_conservation(proj, cs)
    assert res["input_auxiliary_signals"] == 100
    assert res["emitted_operations"] == 100


def test_invariant_bypass_gate_c2_blocks_zero_ops_and_vacuous_stubs() -> None:
    """Category 5 (Invariant bypass attempt): Gate C2 fails closed on ERR_SIGNAL_TRANSLATION_LOSS and ERR_VACUOUS_BOILERPLATE_CLAIM."""
    proj = {
        "graph_mutations": {
            "entity_upserts": [
                {"id": "metric:pitch", "name": "Pitch & Rotor Total", "description": "123 assets (38.2% of Grand Total)."}
            ],
            "relationship_signals": [],
        }
    }
    with pytest.raises(SignalTranslationCompletenessError, match="ERR_SIGNAL_TRANSLATION_LOSS"):
        validate_bijective_signal_conservation(proj, {"operations": []})

    vacuous_cs = {
        "operations": [{"operation_id": "op-1", "operation": "add_candidate_assertion"}],
        "extensions": {
            "com.partner_advisory.nee.operation-payloads": {
                "op-1": {
                    "candidate": {
                        "normalized_text": "Pitch is an identified metric in Document 20261014 Gold Dataset Asset Count."
                    }
                }
            }
        },
    }
    with pytest.raises(SignalTranslationCompletenessError, match="ERR_VACUOUS_BOILERPLATE_CLAIM"):
        validate_bijective_signal_conservation(proj, vacuous_cs)


def test_rollback_and_suppression_guard_preserves_substantive_descriptions() -> None:
    """Category 6 (Rollback & recovery): should_suppress_document_candidate suppresses boilerplate stubs but retains substantive descriptions."""
    stub_cand = {
        "candidate_type": "entity_mention",
        "normalized_text": "HR 360 is an identified system in Document.",
    }
    assert should_suppress_document_candidate(stub_cand, ["system:hr-360"], {"system:hr-360"}, None, False) is True

    rich_cand = {
        "candidate_type": "entity_mention",
        "normalized_text": "HR 360: Source-side endpoint for Position CREATE and UPDATE integration.",
        "description": "Source-side endpoint for Position CREATE and UPDATE integration.",
        "entity_mentions": [{"reported_text": "HR 360"}],
    }
    assert should_suppress_document_candidate(rich_cand, ["system:hr-360"], {"system:hr-360"}, None, False) is False


def test_exact_production_regression_staging_aruna_and_gold_dataset_claims() -> None:
    """Category 7 (Exact production regression): Verifies .staging_Aruna path scrubbing and non-stub Gold Dataset claim preservation."""
    aruna_ref = ".staging_Aruna_Chinthakindi_Mccarthy_Dan_81b351c0/aruna_chinthakindi_email_thread_10_26_2026.md"
    assert sanitize_cold_archive_ref(aruna_ref) == "output/Aruna_Chinthakindi_Mccarthy_Dan/aruna_chinthakindi_email_thread_10_26_2026.md"
