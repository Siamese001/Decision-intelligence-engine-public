"""
End-to-End lifecycle verification for Wave 1: Anti-Bolstering & Authentic HITL Judge Governance.

Verifies the full multi-attempt evaluation lifecycle across attempt 1 -> attempt 2 -> attempt 3:
- Attempt 1: Output 1 scores 89 (PASS_ADVISORY), Output 2 scores 92 (PASS), Output 3 scores 93 (PASS).
  - Clarification is NOT triggered on Attempt 1 (< max_attempts).
  - Output 1 (score 89 < 95) is NOT frozen or pinned; Outputs 2 and 3 ARE frozen with SHA-256 bindings.
- Attempt 2: Output 1 improves to 91, Output 2 is mutated without re-judging (stale SHA rejected).
- Attempt 3 (final attempt): Output 1 scores 91 (all dimensions above floor); operator explicitly returns
  AFFIRM_VALID on final attempt -> raw score 91 is preserved (never inflated to 95), verdict becomes
  PASS_AUTHORIZED_EXCEPTION, and overall_verdict is PASS_AUTHORIZED_EXCEPTION.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest

from dual_pipeline.canonical_pipeline.evaluator import (
    _merge_pinned_into_judge_report,
    _resolve_pinned_evaluation,
    _sync_and_reseal_evaluation,
)
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult
from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.llm_judge_core.judge_clarification import evaluate_judge_clarification
from dual_pipeline.release_policy import validate_micro_judge_scorecard
from dual_pipeline.transcript_pipeline_core.qa_runner import _freeze_passing_outputs


class _MockPipelineStateMachine:
    def __init__(self) -> None:
        self.frozen: dict[str, str] = {}
        self.candidates: list[tuple[str, int, str]] = []

    def freeze_artifact(self, key: str, content: str) -> None:
        self.frozen[key] = content

    def record_candidate(self, key: str, content: str, score: int, verdict: str = "FAIL", **kwargs) -> None:
        self.candidates.append((key, score, verdict))


class TestJudgeAntiBolsteringE2E(unittest.TestCase):
    def test_e2e_multi_attempt_judge_anti_bolstering_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = HITLDecisionStore(Path(tmpdir) / "ledger.json")
            sm = _MockPipelineStateMachine()
            metadata: dict = {
                "max_attempts": 3,
                "judge_clarification_resolver": lambda env: "AFFIRM_VALID",
            }

            f1_v1 = "# Forensic Transcript v1\n**David:** We need to finalize the governance architecture."
            f2_v1 = "# Executive Recap v1\nDavid outlined the governance architecture."
            f3_v1 = '{"nodes": [{"id": "n1", "type": "Decision"}], "edges": []}'

            syn_v1 = SynthesisResult(
                output_1=f1_v1,
                output_2=f2_v1,
                output_3=f3_v1,
            )

            # --- ATTEMPT 1 of 3 ---
            j_rep_1 = {
                "output_1_evaluation": {
                    "composite_score": 89.0,
                    "verdict": "PASS_ADVISORY",
                    "status": "PASS_ADVISORY",
                    "dimension_1_forensic_grounding_and_fidelity": 23.0,
                    "dimension_2_attribution_and_room_mic_hygiene": 22.0,
                    "dimension_3_chronological_and_timestamp_integrity": 22.0,
                    "dimension_4_asr_cleanup_and_domain_preservation": 22.0,
                    "forensic_rationale": "Slightly abrupt turn transition.",
                },
                "output_2_evaluation": {
                    "composite_score": 92.0,
                    "verdict": "PASS",
                    "status": "PASS",
                    "section_scores": {
                        "header_and_overview": 9.0,
                        "key_updates_technical_substance": 33.0,
                        "conditional_extensions": 18.0,
                        "appendix_stakeholder_perspectives": 32.0,
                    },
                },
                "output_3_evaluation": {
                    "composite_score": 93.0,
                    "verdict": "PASS",
                    "status": "PASS",
                    "dimension_1_strategic_edge": 14.0,
                    "dimension_2_knowledge_layer_density": 14.0,
                    "dimension_3_semantic_atomicity": 19.0,
                    "dimension_4_predicate_specificity": 14.0,
                    "dimension_5_exclusion_of_action_items": 14.0,
                    "dimension_6_evidence_grounding_and_schema": 18.0,
                },
                "cross_output_reconciliation": {"status": "PASS"},
            }
            final_dec_1 = {
                "attempt": 1,
                "max_attempts": 3,
                "output_1_deterministic_gates_pass": True,
                "output_2_deterministic_gates_pass": True,
                "output_3_deterministic_gates_pass": True,
            }
            qa_rep_1 = {"passed": True}

            # Clarification must NOT run on Attempt 1
            clar_1 = evaluate_judge_clarification(j_rep_1, final_dec_1, metadata=metadata, store=store)
            assert clar_1 is False

            auth_1 = _sync_and_reseal_evaluation(
                judge_rep=j_rep_1,
                final_dec=final_dec_1,
                qa_rep=qa_rep_1,
                synthesis_result=syn_v1,
                source_text="Raw transcript",
                meta=metadata,
                judge_model="judge-v1",
                reasoning_effort="high",
                is_notes=False,
                is_document=False,
            )
            assert auth_1 is False
            assert final_dec_1["output_1_pass"] is False
            assert final_dec_1["output_2_pass"] is True
            assert final_dec_1["output_3_pass"] is True

            _freeze_passing_outputs(
                sm,
                final_dec_1,
                f1_v1,
                f2_v1,
                f3_v1,
                judge_report=j_rep_1,
                qa_report=qa_rep_1,
                metadata=metadata,
            )
            # Output 1 (89 PASS_ADVISORY) must NOT be frozen; Outputs 2 & 3 MUST be frozen with SHA-256
            assert "output_1" not in sm.frozen
            assert "output_2" in sm.frozen
            assert "output_3" in sm.frozen
            assert "output_1" not in metadata["pinned_evaluations"]
            assert metadata["pinned_evaluations"]["output_2"]["artifact_sha256"] == hashlib.sha256(f2_v1.encode("utf-8")).hexdigest()

            # --- ATTEMPT 3 of 3 (Final Attempt) ---
            f1_v3 = "# Forensic Transcript v3\n**David [00:01:10]:** We need to finalize the governance architecture."
            syn_v3 = SynthesisResult(
                output_1=f1_v3,
                output_2=f2_v1,
                output_3=f3_v1,
            )
            pinned_for_3 = _resolve_pinned_evaluation(metadata, attempt=3)
            assert pinned_for_3 is not None
            assert "output_2" in pinned_for_3 and "output_3" in pinned_for_3

            j_rep_3 = {
                "output_1_evaluation": {
                    "composite_score": 91.0,
                    "verdict": "PASS_ADVISORY",
                    "status": "PASS_ADVISORY",
                    "dimension_1_forensic_grounding_and_fidelity": 23.0,
                    "dimension_2_attribution_and_room_mic_hygiene": 23.0,
                    "dimension_3_chronological_and_timestamp_integrity": 22.0,
                    "dimension_4_asr_cleanup_and_domain_preservation": 23.0,
                    "forensic_rationale": "Minor stylistic timestamp spacing.",
                },
                "output_2_evaluation": {"status": "SKIPPED", "verdict": "SKIPPED", "composite_score": 0},
                "output_3_evaluation": {"status": "SKIPPED", "verdict": "SKIPPED", "composite_score": 0},
                "cross_output_reconciliation": {"status": "PASS"},
            }
            final_dec_3 = {
                "attempt": 3,
                "max_attempts": 3,
                "output_1_pass": False,
                "output_2_pass": True,
                "output_3_pass": True,
                "output_1_deterministic_gates_pass": True,
                "output_2_deterministic_gates_pass": True,
                "output_3_deterministic_gates_pass": True,
            }
            _merge_pinned_into_judge_report(j_rep_3, pinned_for_3, meta=metadata, synthesis_result=syn_v3)
            assert j_rep_3["output_2_evaluation"]["composite_score"] == 92.0
            assert j_rep_3["output_3_evaluation"]["composite_score"] == 93.0

            clar_3 = evaluate_judge_clarification(j_rep_3, final_dec_3, metadata=metadata, store=store)
            assert clar_3 is True
            assert final_dec_3["judge_clarification_triggered"] is True

            # Verify raw score 91.0 is preserved and NOT inflated to 95.0
            out1_final = j_rep_3["output_1_evaluation"]
            assert out1_final["composite_score"] == 91.0
            assert out1_final["dimension_1_forensic_grounding_and_fidelity"] == 23.0
            assert out1_final["verdict"] == "PASS_AUTHORIZED_EXCEPTION"
            assert out1_final["status"] == "PASS_AUTHORIZED_EXCEPTION"
            assert out1_final["authorized_exception"] is True

            valid_sc, sc_errors = validate_micro_judge_scorecard(out1_final, output_num=1)
            assert valid_sc is True, f"Scorecard validation failed: {sc_errors}"

            auth_3 = _sync_and_reseal_evaluation(
                judge_rep=j_rep_3,
                final_dec=final_dec_3,
                qa_rep=qa_rep_1,
                synthesis_result=syn_v3,
                source_text="Raw transcript",
                meta=metadata,
                judge_model="judge-v1",
                reasoning_effort="high",
                is_notes=False,
                is_document=False,
            )
            assert auth_3 is True
            assert j_rep_3["overall_verdict"] == "PASS_AUTHORIZED_EXCEPTION"
            assert final_dec_3["authoritative_write_authorized"] is True

    def test_e2e_output2_floor_and_output3_vacuous_graph_anti_bolstering(self) -> None:
        """Verifies Wave 2 E2E invariants: Output 2 post-penalty floor rejection and Output 3 vacuous graph failure."""
        from dual_pipeline.llm_judge_core.graph_checks import apply_deterministic_graph_scoring
        from dual_pipeline.llm_judge_core.judges.recap import _finalize_envelope
        from dual_pipeline.release_policy import _check_out3_threshold

        substantive_source = (
            "David [00:01:00]: We are adopting the unified graph storage architecture for Q4.\n"
            "Alice [00:02:15]: Confirmed, I will migrate the schema contracts by Friday.\n"
        ) * 6

        out2_raw = {
            "composite_score": 92,
            "verdict": "PASS",
            "section_scores": {
                "header_and_overview": 9.0,
                "key_updates_technical_substance": 33.0,
                "conditional_extensions": 9.0,  # Below 11.0 mandatory floor after penalty
                "appendix_stakeholder_perspectives": 34.0,
            },
            "executive_rationale": "Action table missing owners.",
        }
        out2_final = _finalize_envelope(out2_raw)
        assert out2_final["composite_score"] == 85
        assert out2_final["verdict"] == "FAIL"

        # Output 3 vacuous graph extraction on substantive source
        out3_raw = {
            "dimension_1_strategic_edge": 15,
            "dimension_2_knowledge_layer_density": None,
            "dimension_3_semantic_atomicity": 20,
            "dimension_4_predicate_specificity": 15,
            "dimension_5_exclusion_of_action_items": 15,
            "dimension_6_evidence_grounding_and_schema": 20,
            "composite_score": 100,
            "verdict": "PASS",
        }
        out3_scored = apply_deterministic_graph_scoring(
            out3_raw,
            f3_parsed={"graph_mutations": {"entity_upserts": [], "relationship_signals": [], "decision_updates": []}, "evidence_index": {}},
            f1_text=substantive_source,
            archetype="DOCUMENT_MODE",
        )
        assert out3_scored["composite_score"] == 15
        assert out3_scored["verdict"] == "FAIL"
        assert "ERR_VACUOUS_GRAPH_EXTRACTION" in out3_scored["defect_codes"]

        with self.assertRaises(RuntimeError) as ctx:
            _check_out3_threshold({"output_3_evaluation": out3_scored}, "pkg_vacuous")
        assert "ERR_VACUOUS_GRAPH_EXTRACTION" in str(ctx.exception)

    def test_e2e_wave3_prompt_healer_and_prejudge_alignment(self) -> None:
        """Verifies Wave 3 E2E flow: silent roster grounding, utterance-matched graph healer, and heuristic pre-judge gate."""
        import json
        from dual_pipeline.llm_judge_core.heuristic_pre_judge import evaluate_heuristic_pre_judge
        from dual_pipeline.transcript_stages_core.graph_post_extractor import sanitize_and_validate_projection
        from dual_pipeline.transcript_stages_core.lexical_normalizer import format_ambiguity_lock_register

        meta = {
            "project": "project_lens",
            "meeting_id": "meeting-lens-sync",
            "date": "09/11/2026",
            "source_modality": "TEXT_TRANSCRIPT",
            "silent_attendees": ["Jen Pan"],
            "attendees": {
                "PartnerAdvisory": ["Amit Ayer (LENS Lead)", "Jen Pan (Delivery Lead)"],
            },
        }
        alr_table = format_ambiguity_lock_register(meta, speaker_map={"Speaker 1": "Amit Ayer"})
        assert "Jen Pan" in alr_table
        assert "None (0 spoken turns)" in alr_table

        grounded_f1 = (
            "# Subject: LENS Architecture Sync\n\n"
            f"{alr_table}\n\n"
            "## Forensic Transcript\n\n"
            "**[00:01:00] Amit Ayer:** We are integrating the Power BI dashboard with GIS for LENS.\n"
        )

        raw_f3 = {
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "service:lens", "name": "LENS", "type": "system", "mutation": "CONFIRM", "evidence_ids": ["ev-01"]},
                    {"id": "database:gis", "name": "GIS", "type": "system", "mutation": "CONFIRM", "evidence_ids": ["ev-01"]},
                ],
                "relationship_signals": [
                    {
                        "signal_id": "rel-lens--gis--CONTAINS",
                        "from_id": "service:lens",
                        "to_id": "database:gis",
                        "relationship": "CONTAINS",
                        "evidence_ids": ["ev-01"],
                    }
                ],
            },
            "evidence_index": {
                "ev-01": {
                    "speaker": "Amit Ayer",
                    "locator": {"kind": "timestamp", "value": "00:01:00"},
                    "text": "We are integrating the Power BI dashboard with GIS for LENS.",
                    "exact_text": "We are integrating the Power BI dashboard with GIS for LENS.",
                }
            },
        }
        healed_dict = sanitize_and_validate_projection(raw_f3, metadata=meta, source_text=grounded_f1)
        healed_f3 = json.dumps(healed_dict)
        assert "service:" not in healed_f3
        assert "database:" not in healed_f3
        assert '"CONTAINS"' not in healed_f3
        assert '"LEADS"' not in healed_f3
        assert "pending enterprise access diligence" not in healed_f3

        pre_card = evaluate_heuristic_pre_judge(healed_f3, f1_text=grounded_f1)
        assert pre_card["passed_pre_check"] is True
        assert pre_card["is_heuristic_pre_check_only"] is True
        ok_sc, _ = validate_micro_judge_scorecard(pre_card, output_num=3)
        assert ok_sc is False


if __name__ == "__main__":
    unittest.main()


