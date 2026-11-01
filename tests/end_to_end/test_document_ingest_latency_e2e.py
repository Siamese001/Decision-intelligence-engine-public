"""End-to-end lifecycle tests for document ingestion latency remediation (Waves 2 & 3).

Exercises:
1. Bounded remediation checkpoint binding resilience across context_sha256, hitl_ledger_sha256,
   and extraction revision updates plus monotonic rollback re-binding without STALE_BINDING errors.
2. Deterministic post-judge auto-pruning of ungrounded sig-auto-* edges on >= 90 floor-passing
   candidates so high-scoring runs qualify on Attempt 1 without burning 6-minute LLM retry cycles.
3. Release policy alignment for post-prune PASS and >= 90 borderline HITL clarification routing.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from dual_pipeline.canonical_pipeline.document_resume import (
    _only_bounded_remediation_prompt_changed,
    record_document_failure,
    refresh_validated_document_checkpoint,
    run_synthesis_with_resume,
)
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.strategies.qa import QAEvaluationResult
from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult
from dual_pipeline.document_pipeline_core.quality import qualify_document_judge
from dual_pipeline.release_policy import (
    evaluate_document_release_gate,
    validate_micro_judge_scorecard,
)


def test_e2e_remediation_binding_and_monotonic_rollback_rebind(tmp_path: Path) -> None:
    """E2E: 2-attempt remediation with context_sha256/extraction updates and disk touch must not raise STALE_BINDING."""
    before_binding: Dict[str, Any] = {
        "schema_version": "1.0",
        "reuse_blockers": ["EXTRACTION_MODEL_REVISION_UNBOUND"],
        "components": {
            "source_sha256": "abc123",
            "prompts": {"system": "sys-v1", "document_user_content": "attempt-1"},
            "provider": {"type": "vertex", "model": "gemini", "declared_revision": None},
            "extraction": {"type": "vertex", "model": "gemini", "declared_revision": None},
            "context_sha256": "ctx-1",
            "hitl_ledger_sha256": "hitl-1",
            "metadata_sha256": "meta-1",
            "execution_options": {"max_judge_retries": 2},
            "learned_aliases_sha256": "alias-1",
        },
    }
    after_binding: Dict[str, Any] = {
        "schema_version": "1.0",
        "reuse_blockers": [],
        "components": {
            "source_sha256": "abc123",
            "prompts": {"system": "sys-v1", "document_user_content": "attempt-2-remediation"},
            "provider": {"type": "vertex", "model": "gemini", "declared_revision": "rev-1"},
            "extraction": {"type": "vertex", "model": "gemini", "declared_revision": "rev-1"},
            "context_sha256": "ctx-2-updated",
            "hitl_ledger_sha256": "hitl-2-updated",
            "metadata_sha256": "meta-2",
            "execution_options": {"max_judge_retries": 2},
            "learned_aliases_sha256": "alias-2",
        },
    }
    assert _only_bounded_remediation_prompt_changed(before_binding, after_binding) is True

    ckpt_path = tmp_path / "synthesis_checkpoint.json"
    meta: Dict[str, Any] = {
        "_document_checkpoint_path": str(ckpt_path),
        "run_id": "run-e2e-rollback-test",
        "project": "internal_consulting",
    }

    def _fake_synthesize(**kwargs: Any):
        # Simulate Attempt 2 mutating metadata and touching checkpoint on disk during rollback
        kwargs["metadata"]["remediation_directive"] = "Fix peer project edge"
        kwargs["metadata"]["extraction_model_revision"] = "rev-1"
        ckpt_path.write_text(ckpt_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        synth = SynthesisResult(output_1="", output_2="", output_3='{"graph_mutations": {}}')
        eval_res = QAEvaluationResult(
            qa_report={"passed": True},
            judge_report={"status": "FAIL", "output_3_evaluation": {"verdict": "FAIL", "composite_score": 88}},
            final_decision={"status": "REJECTED"},
            passed=False,
            authoritative_write_authorized=False,
        )
        return 1, synth, eval_res

    attempt, _, eval_res = run_synthesis_with_resume(
        synthesize=_fake_synthesize,
        modality=CanonicalModality.DOCUMENT,
        source_text="PartnerAdvisory internal call notes.",
        metadata=meta,
        provider=None,
        provider_type="mock",
        model_name="mock-model",
        reasoning_effort="low",
        pa_context={"entities": []},
        max_judge_retries=2,
    )
    assert attempt == 1
    assert eval_res.passed is False
    assert meta["document_resume"]["reason"] == "IN_RUN_REMEDIATION_NOT_REUSABLE"

    # Also verify record_document_failure does not raise STALE_BINDING after external touch
    record_document_failure(meta, "L2_SYNTHESIS", RuntimeError("Simulated failure"))


def test_e2e_deterministic_auto_healed_edge_pruning_on_98_scorecard() -> None:
    """E2E: A 98/100 candidate whose sole defect is a sig-auto-* edge auto-prunes the edge and passes."""
    projection: Dict[str, Any] = {
        "graph_mutations": {
            "entity_upserts": [
                {"id": "org:partner_advisory", "type": "org", "name": "PartnerAdvisory"},
                {"id": "project:google-funded-work", "type": "project", "name": "Google-Funded Work"},
                {"id": "project:sherpa", "type": "project", "name": "Sherpa"},
            ],
            "relationship_signals": [
                {
                    "signal_id": "sig-valid-1",
                    "from_id": "org:partner_advisory",
                    "to_id": "project:google-funded-work",
                    "relationship": "OWNS",
                    "predicate": "OWNS",
                    "evidence_ids": ["ev-01"],
                },
                {
                    "signal_id": "sig-auto-project-sherpa-contains-project-google-funded-work-1234abcd",
                    "from_id": "project:sherpa",
                    "to_id": "project:google-funded-work",
                    "relationship": "CONTAINS",
                    "predicate": "CONTAINS",
                    "evidence_ids": ["ev-02"],
                    "auto_healed": True,
                },
            ],
        },
        "evidence_index": {
            "ev-01": {"exact_text": "PartnerAdvisory Google-funded work."},
            "ev-02": {"exact_text": "Sherpa rescue project."},
        },
    }
    raw_scorecard: Dict[str, Any] = {
        "dimension_1_strategic_edge": 15,
        "dimension_2_knowledge_layer_density": 15,
        "dimension_3_semantic_atomicity": 20,
        "dimension_4_predicate_specificity": 15,
        "dimension_5_exclusion_of_action_items": 15,
        "dimension_6_evidence_grounding_and_schema": 18,
        "composite_score": 98,
        "verdict": "PASS",
        "defect_codes": ["ERR_UNGROUNDED_MUTATION"],
        "forensic_rationale": (
            "Auto-healed edge sig-auto-project-sherpa-contains-project-google-funded-work "
            "fabricated a CONTAINS relationship between peer projects."
        ),
    }
    judge_payload: Dict[str, Any] = {
        **raw_scorecard,
        "raw_judge_scorecard": dict(raw_scorecard),
        "projection": projection,
        "assertion_candidates": {
            "assertions": list(projection["graph_mutations"]["relationship_signals"]),
        },
        "graph_candidates": {
            "edges": list(projection["graph_mutations"]["relationship_signals"]),
        },
    }

    qualified, passed, score = qualify_document_judge(judge_payload)
    assert passed is True
    assert score == 98
    assert qualified["verdict"] == "PASS"
    assert qualified.get("pruned_auto_healed_signals") == [
        "sig-auto-project-sherpa-contains-project-google-funded-work-1234abcd"
    ]

    remaining_sigs = projection["graph_mutations"]["relationship_signals"]
    assert len(remaining_sigs) == 1
    assert remaining_sigs[0]["signal_id"] == "sig-valid-1"
    assert len(judge_payload["assertion_candidates"]["assertions"]) == 1
    assert len(judge_payload["graph_candidates"]["edges"]) == 1

    gate_decision = evaluate_document_release_gate(qualified)
    assert gate_decision["status"] == "PASS"
    assert gate_decision["verdict"] == "PASS"


def test_e2e_non_auto_healed_96_defect_routes_to_hitl_clarification() -> None:
    """E2E: A >=90 floor-passing candidate with a non-auto-healed defect routes to HITL_REQUIRED without policy crash."""
    scorecard: Dict[str, Any] = {
        "dimension_1_strategic_edge": 15,
        "dimension_2_knowledge_layer_density": 14,
        "dimension_3_semantic_atomicity": 20,
        "dimension_4_predicate_specificity": 15,
        "dimension_5_exclusion_of_action_items": 15,
        "dimension_6_evidence_grounding_and_schema": 17,
        "composite_score": 96,
        "verdict": "PASS",
        "defect_codes": ["ERR_EPISTEMIC_MISATTRIBUTION"],
        "forensic_rationale": "Signal sig-manual-leo-member overstates tentative onboarding.",
    }
    qualified, passed, score = qualify_document_judge(scorecard)
    assert passed is False
    assert score == 96
    assert qualified["verdict"] == "FAIL"

    # Policy validator must accept FAIL on >=90 scorecard when named defects are present
    valid_sc, sc_errs = validate_micro_judge_scorecard(qualified, output_num=3)
    assert valid_sc is True, f"Unexpected scorecard policy errors: {sc_errs}"

    # Release gate routes >=90 floor-passing defect candidate to HITL clarification
    gate_res = evaluate_document_release_gate(qualified)
    assert gate_res["status"] == "HITL_REQUIRED"
    assert gate_res["hitl_clarification_eligible"] is True


def test_e2e_hygiene_receipt_sealing_and_differential_fingerprint_equivalence(tmp_path: Path) -> None:
    """Wave 3 E2E: Hygiene release manifests are cryptographically sealed and MPS/CPU fingerprints reuse vectors."""
    import json

    from dual_pipeline.core import digest
    from dual_pipeline.index_core.differential import inspect_prior_sidecar
    from dual_pipeline.release_core.helpers import _verify_receipt
    from dual_pipeline.storage.governed_context import GovernedStorageContext
    from tools.tier2_dedup_core.hygiene_promotion import (
        _preserve_sidecar_encoding_fingerprint,
        _write_release_and_activation_manifests,
    )

    rt_root = tmp_path / "runtime"
    gen_id = "generation-hygiene-test-001"
    next_pair = {
        "graph_sha256": "a" * 64,
        "sidecar_sha256": "b" * 64,
    }
    _write_release_and_activation_manifests(
        rt_root, gen_id, "generation-prev-000", next_pair, "2026-10-27T17:00:00Z"
    )

    rel_file = rt_root / "releases" / f"release-{gen_id}.json"
    act_file = rt_root / "releases" / f"activation-{gen_id}.json"
    rel_data = json.loads(rel_file.read_text(encoding="utf-8"))
    act_data = json.loads(act_file.read_text(encoding="utf-8"))

    assert rel_data.get("receipt_digest") is not None
    assert act_data.get("receipt_digest") is not None
    _verify_receipt(rel_data)
    _verify_receipt(act_data)

    # Create a sidecar SQLite file with MPS fingerprint and verify CPU backend reuses it
    sidecar_path = tmp_path / "sidecar.sqlite"
    base_fp = {
        "model_id": "BAAI/bge-m3",
        "model_revision": "5617a9f61b028005a4858fdac845db406aefb181",
        "model_manifest_digest": "manifest-digest-123",
        "precision": "float16",
        "device": "mps",
        "tokenizer": "BAAI/bge-m3",
        "max_length": 8192,
        "truncation": True,
        "normalize_embeddings": True,
        "dense_dimensions": 1024,
        "max_colbert_tokens": 32,
    }
    mps_fp_digest = digest(base_fp)
    cpu_fp = {**base_fp, "device": "cpu"}
    cpu_fp_digest = digest(cpu_fp)
    assert mps_fp_digest != cpu_fp_digest

    conn = GovernedStorageContext.connect(sidecar_path, readonly=False)
    conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("CREATE TABLE retrieval_record (record_id TEXT PRIMARY KEY, retrieval_text_hash TEXT)")
    conn.executemany(
        "INSERT INTO metadata (key, value) VALUES (?, ?)",
        [
            ("embedding_backend", "bge-m3"),
            ("model_id", "BAAI/bge-m3"),
            ("model_revision", "5617a9f61b028005a4858fdac845db406aefb181"),
            ("colbert_dimensions", "1024"),
            ("dense_dimensions", "1024"),
        ],
    )
    conn.execute("INSERT INTO retrieval_record VALUES ('rec-1', 'hash-1')")
    conn.commit()
    conn.close()

    _preserve_sidecar_encoding_fingerprint(
        sidecar_path, {"encoding_fingerprint_digest": mps_fp_digest}
    )

    class _DummyBackend:
        backend_id = "bge-m3"
        model_id = "BAAI/bge-m3"
        model_revision = "5617a9f61b028005a4858fdac845db406aefb181"
        encoding_fingerprint = cpu_fp
        encoding_fingerprint_digest = cpu_fp_digest

    eligible, hashes, col_dim = inspect_prior_sidecar(sidecar_path, _DummyBackend())  # type: ignore[arg-type]
    assert eligible is True
    assert hashes.get("rec-1") == "hash-1"
    assert col_dim == 1024

    # Verify truly incompatible fingerprint (different max_colbert_tokens) is still rejected
    class _IncompatibleBackend(_DummyBackend):
        encoding_fingerprint = {**cpu_fp, "max_colbert_tokens": 128}
        encoding_fingerprint_digest = digest({**cpu_fp, "max_colbert_tokens": 128})

    eligible_bad, _, _ = inspect_prior_sidecar(sidecar_path, _IncompatibleBackend())  # type: ignore[arg-type]
    assert eligible_bad is False

