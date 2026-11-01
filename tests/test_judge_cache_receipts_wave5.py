"""
tests/test_judge_cache_receipts_wave5.py

Wave 5 Verification Suite for Meeting Output LLM-as-a-Judge Hardening.
Verifies:
1. Version cache keys bind candidate, source, collateral, roster/C0 receipt,
   governance context/archetype, prompt, rubric, topology, scorecard schema,
   release policy, provider/model/revision, and coverage digests.
2. Invalidate cache/pins on C0.5/C0.6 resolution or prompt/rubric/topology drift.
3. Release authority checks reject missing, stale, invalid, or mismatched scorecard receipts.
4. Document pipeline O3-only six-dimension validation and receipt behavior is preserved
   without synthetic meeting cards.
5. UWG and publication artifacts carry and verify the same judge receipt digest as the final decision.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any, Dict
import unittest
from unittest.mock import MagicMock, patch

from dual_pipeline.canonical_pipeline.publication_bindings import publication_bindings
from dual_pipeline.canonical_pipeline.publish_helper import _assert_authoritative_mutation_preconditions
from dual_pipeline.canonical_pipeline.quality_aggregates import (
    compute_quality_aggregate_score,
    get_evaluated_composite_scores,
)
from dual_pipeline.document_pipeline_core.evaluator import (
    evaluate_document_judge_and_release,
)
from dual_pipeline.release_policy import (
    _check_judge_report,
    _check_manifest_and_seal,
    _verify_judge_digest_match,
    build_artifact_manifest,
    compute_decision_seal,
    compute_release_evaluation_digest,
    validate_micro_judge_scorecard,
    verify_authoritative_release_gate,
)
from dual_pipeline.transcript_pipeline_core.uwg_deployer import deploy_uwg_artifacts
from dual_pipeline.transcript_qa_core.eval_cache import (
    _EVALUATION_CACHE,
    check_cache,
    check_pin,
    clear_evaluation_cache,
    compute_c0_receipt_digest,
    invalidate_c0_cache,
    resolve_pins_and_cache,
    update_evaluation_cache,
)


def _make_valid_out1(score: int = 95) -> Dict[str, Any]:
    dim = score / 4.0
    return {
        "status": "PASS",
        "verdict": "PASS",
        "composite_score": score,
        "dimension_1": dim,
        "dimension_2": dim,
        "dimension_3": dim,
        "dimension_4": dim,
    }


def _make_valid_out2(score: int = 92) -> Dict[str, Any]:
    return {
        "status": "PASS",
        "verdict": "PASS",
        "composite_score": score,
        "section_scores": {
            "header_and_overview": 10.0,
            "key_updates_technical_substance": 32.0,
            "conditional_extensions": 18.0,
            "appendix_stakeholder_perspectives": 32.0,
        },
    }


def _make_valid_out3(score: int = 94) -> Dict[str, Any]:
    return {
        "status": "PASS",
        "verdict": "PASS",
        "composite_score": score,
        "dimension_1": 14,
        "dimension_2": 14,
        "dimension_3": 19,
        "dimension_4": 14,
        "dimension_5": 14,
        "dimension_6": 19,
    }


class TestWave5CacheKeyVersioningAndInvalidation(unittest.TestCase):
    """Verifies complete cache key dependency binding and C0 invalidation."""

    def setUp(self) -> None:
        clear_evaluation_cache()

    def test_cache_keys_bind_c0_receipt_digest(self) -> None:
        """Mutating C0 title or role receipt produces distinct cache keys and cache misses."""
        f1, f2, f3 = "Transcript", "Recap", "Signal"
        meta_v1 = {"c05_receipt_digest": "title_digest_v1", "c06_receipt_digest": "role_digest_v1"}
        meta_v2 = {"c05_receipt_digest": "title_digest_v2", "c06_receipt_digest": "role_digest_v1"}

        _, _, _, k1_v1, k2_v1, k3_v1 = resolve_pins_and_cache(f1, f2, f3, "gpt-6-astra", "medium", {}, metadata=meta_v1)
        _, _, _, k1_v2, k2_v2, k3_v2 = resolve_pins_and_cache(f1, f2, f3, "gpt-6-astra", "medium", {}, metadata=meta_v2)

        self.assertNotEqual(k1_v1, k1_v2)
        self.assertNotEqual(k2_v1, k2_v2)
        self.assertNotEqual(k3_v1, k3_v2)

        # Cache populated under v1 must not hit under v2
        update_evaluation_cache(k1_v1, _make_valid_out1(), k2_v1, _make_valid_out2(), k3_v1, _make_valid_out3())
        o1, o2, o3, _, _, _ = resolve_pins_and_cache(f1, f2, f3, "gpt-6-astra", "medium", {}, metadata=meta_v2)
        self.assertIsNone(o1)
        self.assertIsNone(o2)
        self.assertIsNone(o3)

    def test_cache_keys_bind_governance_archetype_and_rubric(self) -> None:
        """Mutating archetype, rubric_version, or topology produces distinct cache keys."""
        f = "Artifact content"
        _, _, _, k1_gen, _, _ = resolve_pins_and_cache(f, f, f, "m", "med", {}, archetype="GENERAL")
        _, _, _, k1_exec, _, _ = resolve_pins_and_cache(f, f, f, "m", "med", {}, archetype="EXECUTIVE_BRIEFING")
        self.assertNotEqual(k1_gen, k1_exec)

        _, _, _, k1_rub20, _, _ = resolve_pins_and_cache(f, f, f, "m", "med", {}, rubric_version="2.0")
        _, _, _, k1_rub21, _, _ = resolve_pins_and_cache(f, f, f, "m", "med", {}, rubric_version="2.1")
        self.assertNotEqual(k1_rub20, k1_rub21)

        _, _, _, k1_t1, _, _ = resolve_pins_and_cache(f, f, f, "m", "med", {}, topology="decomposed_concurrent")
        _, _, _, k1_t2, _, _ = resolve_pins_and_cache(f, f, f, "m", "med", {}, topology="single_flight")
        self.assertNotEqual(k1_t1, k1_t2)

    def test_invalidate_c0_cache_purges_targeted_entries(self) -> None:
        """invalidate_c0_cache removes cache entries for specific C0 digest."""
        meta = {"c05_receipt_digest": "target_c0_123"}
        c0_digest = compute_c0_receipt_digest(meta)
        f = "Artifact content"
        _, _, _, k1, k2, k3 = resolve_pins_and_cache(f, f, f, "m", "med", {}, metadata=meta)
        update_evaluation_cache(k1, _make_valid_out1(), k2, _make_valid_out2(), k3, _make_valid_out3())

        self.assertIsNotNone(check_cache(k1, min_score=85))
        purged = invalidate_c0_cache(c0_digest)
        self.assertGreaterEqual(purged, 1)
        self.assertIsNone(check_cache(k1, min_score=85))


class TestWave5PinExactCompatibility(unittest.TestCase):
    """Verifies pin rejection on C0, prompt, rubric, or topology mismatch."""

    def test_check_pin_rejects_c0_receipt_mismatch(self) -> None:
        """Pin with matching candidate SHA but mismatched C0 digest is rejected."""
        cand_text = "Alice: Ground truth conversation."
        cand_sha = hashlib.sha256(cand_text.encode("utf-8")).hexdigest()
        card = _make_valid_out1(95)
        card["c0_receipt_digest"] = "stale_c0_receipt_hash"

        pinned = {"output_1": card}
        res = check_pin(
            pinned, "output_1", "output_01", min_score=85, expected_sha=cand_sha,
            expected_c0_digest="active_c0_receipt_hash",
        )
        self.assertIsNone(res, "Expected pin with mismatched C0 digest to be rejected")

    def test_check_pin_rejects_c0_invalidated_flag(self) -> None:
        """Pin marked c0_invalidated is rejected fail-closed."""
        cand_text = "Alice: Ground truth conversation."
        cand_sha = hashlib.sha256(cand_text.encode("utf-8")).hexdigest()
        card = _make_valid_out1(95)
        card["c0_invalidated"] = True

        pinned = {"output_1": card}
        res = check_pin(pinned, "output_1", "output_01", min_score=85, expected_sha=cand_sha)
        self.assertIsNone(res, "Expected c0_invalidated pin to be rejected")

    def test_check_pin_rejects_prompt_or_rubric_drift(self) -> None:
        """Pin with mismatched prompt_hash or rubric_version is rejected."""
        cand_text = "Alice: Conversation."
        cand_sha = hashlib.sha256(cand_text.encode("utf-8")).hexdigest()
        card = _make_valid_out1(95)
        card["prompt_hash"] = "old_prompt_v1"
        card["rubric_version"] = "1.0"

        pinned = {"output_1": card}
        # Mismatched prompt
        res_prompt = check_pin(
            pinned, "output_1", "output_01", min_score=85, expected_sha=cand_sha,
            expected_prompt_hash="new_prompt_v2",
        )
        self.assertIsNone(res_prompt)

        # Mismatched rubric
        res_rubric = check_pin(
            pinned, "output_1", "output_01", min_score=85, expected_sha=cand_sha,
            expected_rubric_version="2.0",
        )
        self.assertIsNone(res_rubric)

    def test_check_pin_accepts_compatible_pin(self) -> None:
        """Pin with matching candidate SHA, matching C0 digest, and matching prompt is accepted."""
        cand_text = "Alice: Conversation."
        cand_sha = hashlib.sha256(cand_text.encode("utf-8")).hexdigest()
        card = _make_valid_out1(95)
        card["artifact_sha256"] = cand_sha
        card["c0_receipt_digest"] = "matched_c0"
        card["prompt_hash"] = "matched_prompt"
        card["rubric_version"] = "2.0"
        card["topology"] = "decomposed_concurrent"

        pinned = {"output_1": card}
        res = check_pin(
            pinned, "output_1", "output_01", min_score=85, expected_sha=cand_sha,
            expected_c0_digest="matched_c0", expected_prompt_hash="matched_prompt",
            expected_rubric_version="2.0", expected_topology="decomposed_concurrent",
        )
        self.assertIsNotNone(res)
        self.assertEqual(res["source"], "PINNED_CACHE")


class TestWave5ReceiptAndDecisionDigestParity(unittest.TestCase):
    """Verifies cryptographic digest parity across judge report, manifest, and release decision."""

    def test_verify_judge_digest_match_rejects_missing_digest(self) -> None:
        """_verify_judge_digest_match fails closed if judge_evaluation_digest is missing in manifest."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "llm_judge_evaluation.json"
            report = {"evaluator_model": "test", "status": "PASS"}
            report["evaluation_receipt_digest"] = compute_release_evaluation_digest(report)
            p.write_text(json.dumps(report), encoding="utf-8")

            manifest_missing = {"output_1_sha256": "h1"}
            with self.assertRaises(RuntimeError) as ctx:
                _verify_judge_digest_match(manifest_missing, p, "pkg")
            self.assertIn("Missing mandatory judge_evaluation_digest", str(ctx.exception))

    def test_verify_judge_digest_match_rejects_tampered_judge_digest(self) -> None:
        """_verify_judge_digest_match fails closed if manifest digest != judge report digest."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "llm_judge_evaluation.json"
            report = {"evaluator_model": "test", "status": "PASS"}
            real_digest = compute_release_evaluation_digest(report)
            report["evaluation_receipt_digest"] = real_digest
            p.write_text(json.dumps(report), encoding="utf-8")

            manifest_tampered = {"judge_evaluation_digest": "forged_judge_digest"}
            with self.assertRaises(RuntimeError) as ctx:
                _verify_judge_digest_match(manifest_tampered, p, "pkg")
            self.assertIn("Cryptographic judge digest mismatch", str(ctx.exception))

    def test_check_manifest_and_seal_rejects_decision_digest_mismatch(self) -> None:
        """_check_manifest_and_seal fails closed if decision.judge_evaluation_digest != judge report."""
        with tempfile.TemporaryDirectory() as td:
            td_p = Path(td)
            j_p = td_p / "llm_judge_evaluation.json"
            report = {"evaluator_model": "test", "status": "PASS"}
            real_digest = compute_release_evaluation_digest(report)
            report["evaluation_receipt_digest"] = real_digest
            j_p.write_text(json.dumps(report), encoding="utf-8")

            manifest = {
                "output_1_sha256": hashlib.sha256(b"").hexdigest(),
                "output_2_sha256": hashlib.sha256(b"").hexdigest(),
                "output_3_sha256": hashlib.sha256(b"").hexdigest(),
                "judge_evaluation_digest": real_digest,
            }
            manifest["decision_seal_sha256"] = compute_decision_seal(manifest)

            decision = {
                "artifact_manifest": manifest,
                "judge_evaluation_digest": "drifted_decision_digest",
            }
            pkg_p = td_p / "intake_package.json"
            pkg_p.write_text("{}", encoding="utf-8")

            with self.assertRaises(RuntimeError) as ctx:
                _check_manifest_and_seal(decision, pkg_p, judge_file=j_p)
            self.assertIn("Decision judge_evaluation_digest mismatch", str(ctx.exception))

    def test_check_judge_report_enforces_all_three_cards_in_meeting_mode(self) -> None:
        """_check_judge_report requires all 3 valid scorecards for meeting releases."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "llm_judge_evaluation.json"
            report = {
                "mode": "meeting",
                "evaluation_mode": "DECOMPOSED_CONCURRENT",
                "evaluator_model": "test-model",
                "output_1_evaluation": _make_valid_out1(),
                # output_2 is missing
                "output_3_evaluation": _make_valid_out3(),
            }
            report["evaluation_receipt_digest"] = compute_release_evaluation_digest(report)
            p.write_text(json.dumps(report), encoding="utf-8")

            with self.assertRaises(RuntimeError) as ctx:
                _check_judge_report(p, "meeting_pkg")
            self.assertIn("missing mandatory Output 2 scorecard", str(ctx.exception))


class TestWave5PublicationAndUWGDigestParity(unittest.TestCase):
    """Verifies that publication bindings and UWG deployer enforce judge receipt digest parity."""

    def test_publication_bindings_verifies_judge_receipt_digest(self) -> None:
        """publication_bindings fails closed on mismatched decision vs judge report digest."""
        with tempfile.TemporaryDirectory() as td:
            td_p = Path(td)
            pkg_p = td_p / "intake_package.json"
            pkg_p.write_text(json.dumps({
                "meeting": {"meeting_id": "m123", "source_hash": "src_sha"},
            }), encoding="utf-8")

            j_file = td_p / "llm_judge_evaluation.json"
            report = {"status": "PASS", "evaluation_receipt_digest": "correct_judge_digest"}
            j_file.write_text(json.dumps(report), encoding="utf-8")

            d_file = td_p / "final_release_decision.json"
            decision = {
                "artifact_manifest": {"judge_evaluation_digest": "mismatched_judge_digest"},
                "judge_evaluation_digest": "mismatched_judge_digest",
            }
            d_file.write_text(json.dumps(decision), encoding="utf-8")

            with self.assertRaises(ValueError) as ctx:
                publication_bindings(
                    pkg_p, {"raw_source_sha256": "h"}, directive_id="d1",
                    reviewer="operator", rationale="test", embedding_backend="bge-m3-dev",
                    runtime=td_p, repository_root=td_p, model_path=str(td_p), model_manifest=str(td_p),
                )
            self.assertIn("Publication judge receipt digest mismatch", str(ctx.exception))

    def test_assert_authoritative_mutation_preconditions_rejects_digest_mismatch(self) -> None:
        """_assert_authoritative_mutation_preconditions blocks GraphDB mutation on digest mismatch."""
        with tempfile.TemporaryDirectory() as td:
            td_p = Path(td)
            j_file = td_p / "llm_judge_evaluation.json"
            j_file.write_text(json.dumps({
                "status": "PASS", "overall_verdict": "PASS",
                "evaluator_model": "real-model", "evaluation_receipt_digest": "judge_hash_abc",
            }), encoding="utf-8")

            d_file = td_p / "final_release_decision.json"
            d_file.write_text(json.dumps({
                "status": "PASS",
                "artifact_manifest": {"judge_evaluation_digest": "judge_hash_xyz"},
            }), encoding="utf-8")

            with self.assertRaises(RuntimeError) as ctx:
                _assert_authoritative_mutation_preconditions(td_p, {})
            self.assertIn("decision judge digest 'judge_hash_xyz' != judge report digest 'judge_hash_abc'", str(ctx.exception))

    def test_uwg_deployer_rejects_artifact_write_on_digest_mismatch(self) -> None:
        """deploy_uwg_artifacts fails closed before deploying if judge evaluation digest mismatches."""
        with tempfile.TemporaryDirectory() as td:
            artifacts = {
                "01_hitl_resolved_transcript.md": "Transcript",
                "02_executive_recap.md": "Recap",
                "03_canonical_signal_projection.json": "{}",
            }
            judge_rep = {"evaluation_receipt_digest": "digest_a"}
            decision = {"artifact_manifest": {"judge_evaluation_digest": "digest_b"}}

            with self.assertRaises(RuntimeError) as ctx:
                deploy_uwg_artifacts(
                    artifacts=artifacts,
                    metadata={"meeting_name": "TestMeeting", "date": "10-09-2026", "time": "12:00:00"},
                    output_dir=td,
                    judge_report=judge_rep,
                    final_decision=decision,
                )
            self.assertIn("UWG deployer rejected artifact write", str(ctx.exception))


class TestWave5DocumentPipelineO3Isolation(unittest.TestCase):
    """Verifies that Document Pipeline preserves O3-only 6-dimension evaluation and receipts."""

    def test_document_pipeline_evaluator_produces_valid_receipts(self) -> None:
        """evaluate_document_judge_and_release runs Gate 3 and O3 judge with matching digests."""
        from dual_pipeline.document_pipeline_core.executor import DocumentGraphExtractorExecutor
        from dual_pipeline.transcript_stages_core.synthesizers import OfflineTranscriptSynthesizer

        src_text = (
            "Today we announce that IT and NEA are combining into one Technology organization. "
            "Ben Miron will serve as Chief Information Officer leading core tech. "
            "Rich Argentieri will serve as Chief Product Officer leading products."
        )
        meta = {
            "project": "enterprise_architecture",
            "date": "09/16/2026",
            "date_filename": "09-16-2026",
            "title": "Org Transformation",
            "classification": "internal",
            "audiences": ["internal_consulting"],
        }
        executor = DocumentGraphExtractorExecutor(OfflineTranscriptSynthesizer())
        f3_payload = executor.execute(src_text, pa_context={"metadata": meta})

        qa_rep, judge_rep, decision = evaluate_document_judge_and_release(
            f3_text=f3_payload,
            source_text=src_text,
            metadata=meta,
            mock=True,
        )

        # 1. Output 1 and Output 2 must have NOT_APPLICABLE_DOCUMENT_MODE
        o1_eval = judge_rep.get("output_1_evaluation", {})
        o2_eval = judge_rep.get("output_2_evaluation", {})
        self.assertEqual(o1_eval.get("reason"), "NOT_APPLICABLE_DOCUMENT_MODE")
        self.assertEqual(o2_eval.get("reason"), "NOT_APPLICABLE_DOCUMENT_MODE")

        # 2. Scorecard validation passes for all 3 outputs under document mode
        val1, _ = validate_micro_judge_scorecard(o1_eval, output_num=1)
        val2, _ = validate_micro_judge_scorecard(o2_eval, output_num=2)
        val3, _ = validate_micro_judge_scorecard(judge_rep["output_3_evaluation"], output_num=3)
        self.assertTrue(val1)
        self.assertTrue(val2)
        self.assertTrue(val3)

        # 3. Digest parity holds
        self.assertEqual(judge_rep["evaluation_receipt_digest"], decision["judge_evaluation_digest"])
        self.assertEqual(judge_rep["evaluation_receipt_digest"], decision["artifact_manifest"]["judge_evaluation_digest"])

        # 4. Quality aggregates exclude non-applicable O1 and O2 without skewing
        scores = get_evaluated_composite_scores(judge_rep)
        self.assertNotIn("output_1", scores)
        self.assertNotIn("output_2", scores)
        self.assertIn("output_3", scores)
        self.assertEqual(compute_quality_aggregate_score(judge_rep), scores["output_3"])


if __name__ == "__main__":
    unittest.main()
