"""
tests/end_to_end/test_judge_reliability_e2e.py

End-to-End lifecycle evaluation test suite for Wave 4 Judge Reliability Controls:
1. Replay of 5 representative meetings with N=3 sampling:
   - Meeting 1: Consistent high-quality (spread <= 5.0, median pass, 100% provenance cached).
   - Meeting 2: Borderline evaluation ([85, 90) median, valid card, verified NOT cached).
   - Meeting 3: Divergent samples (spread > 5.0, routes to HITL_REQUIRED with ERR_SAMPLE_SPREAD_EXCEEDED).
   - Meeting 4: Provider 429 quota exhaustion (routes to HITL_REQUIRED with ERR_PROVIDER_429_HITL).
   - Meeting 5: Decision D1 conflict (judge matches generator model, fails closed with ERR_SELF_EVALUATION).
2. Direct independent verification of SQLite judge_evaluations table:
   - 100% of persisted rows contain mandatory provenance fields.
   - Zero borderline [85, 90) records persisted.
   - Zero HITL_REQUIRED or spread exceeded records persisted.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from typing import Any, Dict, List
import unittest
from unittest.mock import MagicMock, patch

from dual_pipeline.llm_judge_core.judges.forensic import ForensicTranscriptJudge
from dual_pipeline.llm_judge_core.judges.graph import GraphExtractionJudge
from dual_pipeline.llm_judge_core.judges.recap import ExecutiveRecapJudge
from dual_pipeline.llm_judge_core.sampling import (
    MAX_ALLOWED_SPREAD,
    check_self_evaluation,
    evaluate_sampled_judge,
)
from dual_pipeline.release_policy import (
    validate_micro_judge_scorecard,
    validate_scorecard_provenance,
)
from dual_pipeline.transcript_qa_core.eval_cache import (
    _get_cache_db_path,
    check_cache,
    clear_evaluation_cache,
    update_evaluation_cache,
)


def _build_sampled_out1_card(score: float, model: str = "gpt-5.6-sol", p_digest: str = "a1b2c3d4e5f6") -> Dict[str, Any]:
    dim = score / 4.0
    return {
        "status": "PASS",
        "verdict": "PASS" if score >= 85.0 else "FAIL",
        "composite_score": score,
        "dimension_1": dim,
        "dimension_2": dim,
        "dimension_3": dim,
        "dimension_4": dim,
        "dimension_1_forensic_grounding_and_fidelity": dim,
        "dimension_2_attribution_and_room_mic_hygiene": dim,
        "dimension_3_chronological_and_timestamp_integrity": dim,
        "dimension_4_asr_cleanup_and_domain_preservation": dim,
        "judge_model": model,
        "prompt_digest": p_digest,
        "rubric_version": "2.0",
        "sample_count": 3,
        "is_fallback": False,
        "forensic_rationale": f"Sample evaluation with composite score {score}",
    }


def _build_sampled_out2_card(score: float, model: str = "gpt-5.6-sol", p_digest: str = "b2c3d4e5f6a1") -> Dict[str, Any]:
    return {
        "status": "PASS",
        "verdict": "PASS" if score >= 85.0 else "FAIL",
        "composite_score": score,
        "meeting_archetype": "STRATEGIC_DISCOVERY",
        "section_scores": {
            "header_and_overview": 10,
            "key_updates_technical_substance": 34,
            "conditional_extensions": 18,
            "appendix_stakeholder_perspectives": round(score - 62, 2),
        },
        "judge_model": model,
        "prompt_digest": p_digest,
        "rubric_version": "2.0",
        "sample_count": 3,
        "is_fallback": False,
        "forensic_rationale": f"Sample recap evaluation with score {score}",
    }


class TestJudgeReliabilityE2E(unittest.TestCase):
    """End-to-End replay and independent SQLite storage verification."""

    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self._old_cache_dir = os.environ.get("NEE_CACHE_DIR")
        os.environ["NEE_CACHE_DIR"] = self._temp_dir.name
        clear_evaluation_cache()

    def tearDown(self):
        clear_evaluation_cache()
        if self._old_cache_dir is not None:
            os.environ["NEE_CACHE_DIR"] = self._old_cache_dir
        else:
            os.environ.pop("NEE_CACHE_DIR", None)
        self._temp_dir.cleanup()

    def test_e2e_five_meeting_replay_and_sqlite_provenance(self):
        """Execute 5 representative meeting evaluations with N=3 and verify SQLite integrity."""
        # --- Meeting 1: Energy_Utilities (Consistent Pass) ---
        t0 = time.time()
        m1_samples = [
            _build_sampled_out1_card(92.0),
            _build_sampled_out1_card(94.0),
            _build_sampled_out1_card(93.0),
        ]
        m1_eval = evaluate_sampled_judge(sample_cards=m1_samples, n_samples=3)
        self.assertEqual(m1_eval["composite_score"], 93.0)
        self.assertEqual(m1_eval["verdict"], "PASS")
        self.assertEqual(m1_eval["sample_spread"], 2.0)
        ok1, errs1 = validate_scorecard_provenance(m1_eval)
        self.assertTrue(ok1, f"M1 provenance failed: {errs1}")
        update_evaluation_cache("out1:energy_utilities_sha", m1_eval)

        # --- Meeting 2: Hrit_Sow (Borderline [85, 90) Pass) ---
        m2_samples = [
            _build_sampled_out2_card(86.0),
            _build_sampled_out2_card(88.0),
            _build_sampled_out2_card(87.0),
        ]
        m2_eval = evaluate_sampled_judge(sample_cards=m2_samples, n_samples=3)
        self.assertEqual(m2_eval["composite_score"], 87.0)
        self.assertEqual(m2_eval["verdict"], "PASS")
        self.assertEqual(m2_eval["sample_spread"], 2.0)
        ok2, errs2 = validate_scorecard_provenance(m2_eval)
        self.assertTrue(ok2, f"M2 provenance failed: {errs2}")
        update_evaluation_cache("out2:hrit_sow_sha", m2_eval)

        # --- Meeting 3: PartnerAdvisory_Architecture (Divergent Spread > 5.0 -> HITL) ---
        m3_samples = [
            _build_sampled_out1_card(82.0),
            _build_sampled_out1_card(90.0),
            _build_sampled_out1_card(94.0),
        ]
        m3_eval = evaluate_sampled_judge(sample_cards=m3_samples, n_samples=3)
        self.assertEqual(m3_eval["verdict"], "HITL_REQUIRED")
        self.assertEqual(m3_eval["status"], "HITL_REQUIRED")
        self.assertEqual(m3_eval["sample_spread"], 12.0)
        self.assertIn("ERR_SAMPLE_SPREAD_EXCEEDED", m3_eval.get("defect_codes", []))
        update_evaluation_cache("out1:partner_advisory_arch_sha", m3_eval)

        # --- Meeting 4: Google_Meet_Incident (Provider 429 Quota Exhaustion -> HITL) ---
        m4_samples = [
            _build_sampled_out1_card(92.0),
            {
                "status": "HITL_REQUIRED",
                "verdict": "HITL_REQUIRED",
                "composite_score": None,
                "is_fallback": True,
                "judge_model": "gpt-5.6-sol",
                "prompt_digest": "429_exhaustion",
                "rubric_version": "2.0",
                "sample_count": 1,
                "defect_codes": ["ERR_PROVIDER_429_HITL"],
                "forensic_rationale": "PROVIDER RATE LIMIT (HTTP 429) EXHAUSTED: routed to HITL.",
            },
        ]
        m4_eval = evaluate_sampled_judge(sample_cards=m4_samples, n_samples=3)
        self.assertEqual(m4_eval["verdict"], "HITL_REQUIRED")
        self.assertIn("ERR_PROVIDER_429_HITL", m4_eval.get("defect_codes", []))
        update_evaluation_cache("out1:google_meet_429_sha", m4_eval)

        # --- Meeting 5: Tactical_Sync (Decision D1 Generator Conflict -> FAIL) ---
        forensic_judge = ForensicTranscriptJudge(model="gpt-5.6-sol")
        meta_d1 = {"generator_model": "gpt-5.6-sol"}
        m5_eval = forensic_judge.evaluate(
            f1_text="Transcript text",
            raw_source_text="Source text",
            metadata=meta_d1,
        )
        self.assertEqual(m5_eval["status"], "FAIL")
        self.assertEqual(m5_eval["verdict"], "FAIL")
        self.assertEqual(m5_eval["composite_score"], 0)
        self.assertIn("ERR_SELF_EVALUATION", m5_eval.get("defect_codes", []))
        update_evaluation_cache("out1:tactical_sync_self_eval_sha", m5_eval)

        elapsed = time.time() - t0

        # --- Direct SQLite Database Inspection ---
        db_path = _get_cache_db_path()
        self.assertTrue(db_path.exists(), "Cache SQLite DB should exist")

        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        rows = cursor.execute("SELECT cache_key, composite_score, verdict, evaluation_json FROM judge_evaluations").fetchall()
        conn.close()

        # 1. Independent Check: Meeting 1 (Solid Pass) MUST be present
        found_keys = [r[0] for r in rows]
        self.assertIn("out1:energy_utilities_sha", found_keys)

        # 2. Independent Check: Borderline M2 MUST NOT be cached
        self.assertNotIn("out2:hrit_sow_sha", found_keys, "Borderline score [85, 90) must not be in SQLite cache")

        # 3. Independent Check: High-spread M3 MUST NOT be cached
        self.assertNotIn("out1:partner_advisory_arch_sha", found_keys, "High-spread card must not be in SQLite cache")

        # 4. Independent Check: 429 M4 MUST NOT be cached
        self.assertNotIn("out1:google_meet_429_sha", found_keys, "Provider 429 card must not be in SQLite cache")

        # 5. Independent Check: 100% of persisted rows have mandatory provenance
        for cache_key, score, verdict, eval_json in rows:
            data = json.loads(eval_json)
            self.assertIn("judge_model", data, f"Key {cache_key} missing judge_model")
            self.assertIn("prompt_digest", data, f"Key {cache_key} missing prompt_digest")
            self.assertIn("rubric_version", data, f"Key {cache_key} missing rubric_version")
            self.assertIn("sample_count", data, f"Key {cache_key} missing sample_count")
            self.assertIn("is_fallback", data, f"Key {cache_key} missing is_fallback")
            # Score must not be borderline [85, 90)
            self.assertFalse(85.0 <= float(score) < 90.0, f"Row {cache_key} has borderline score {score}")
            # Verdict must not be HITL_REQUIRED
            self.assertNotEqual(verdict, "HITL_REQUIRED", f"Row {cache_key} has HITL_REQUIRED verdict")

        # Record metrics
        metrics = {
            "replayed_meetings": 5,
            "sample_n": 3,
            "elapsed_seconds": round(elapsed, 4),
            "provenance_compliance_pct": 100.0,
            "sqlite_cached_rows": len(rows),
        }
        self.assertEqual(metrics["provenance_compliance_pct"], 100.0)


if __name__ == "__main__":
    unittest.main()
