"""
Master Vector Orchestrator and Decomposed Multi-Judge.

Orchestrator for deterministic, semantic vector, and decomposed LLM micro-judge evaluations.
"""
from typing import Any, Dict, List, Optional, Tuple

from dual_pipeline.llm_judge_core.judges import (
    ForensicTranscriptJudge,
    ExecutiveRecapJudge,
    GraphExtractionJudge,
)
from dual_pipeline.llm_judge_core.reconciler import CrossArtifactReconciler


def _eval_or_reuse_frozen_deliverable(
    judge_instance: Any,
    key: str,
    text: str,
    ref_text: str,
    frozen_deliverables: Any,
    mock: bool,
    meta: Dict[str, Any],
    kwargs: Dict[str, Any],
) -> Dict[str, Any]:
    frozen_card = frozen_deliverables.get(key) if isinstance(frozen_deliverables, dict) else None
    if isinstance(frozen_card, dict) and frozen_card.get("verdict") in (
        "PASS",
        "PASS_ADVISORY",
        "PASS_AUTHORIZED_EXCEPTION",
    ):
        reused = dict(frozen_card)
        reused["frozen_bypass"] = True
        reused["tokens_saved_by_freeze"] = True
        return reused
    return judge_instance.evaluate_deliverable(key, text, ref_text, mock=mock, metadata=meta, **kwargs)


def _compute_parallel_aggregate(
    res1: Dict[str, Any], res2: Dict[str, Any], res3: Dict[str, Any], recon_pass: bool, hard_veto: bool
) -> Tuple[str, float]:
    v1, v2, v3 = res1.get("verdict", "FAIL"), res2.get("verdict", "FAIL"), res3.get("verdict", "FAIL")
    has_veto = hard_veto or not recon_pass
    all_passed = (
        v1 in ("PASS", "PASS_ADVISORY", "PASS_AUTHORIZED_EXCEPTION")
        and v2 in ("PASS", "HITL_REQUIRED", "PASS_AUTHORIZED_EXCEPTION")
        and v3 in ("PASS", "HITL_REQUIRED", "PASS_AUTHORIZED_EXCEPTION")
        and not has_veto
    )
    overall_status = "PASS" if all_passed else "FAIL"
    scores = [res1.get("composite_score", 0), res2.get("composite_score", 0), res3.get("composite_score", 0)]
    valid_scores = [s for s in scores if s is not None and isinstance(s, (int, float))]
    aggregate_score = round(sum(valid_scores) / len(valid_scores), 2) if valid_scores else 0.0
    if overall_status == "FAIL":
        if has_veto:
            aggregate_score = 0.0
        else:
            failing_scores = [s for s in valid_scores if s < 85]
            aggregate_score = min(failing_scores) if failing_scores else min(aggregate_score, 50.0)
    return overall_status, aggregate_score


class DecomposedMultiJudge:
    """
    Decomposed Multi-Judge coordinating specialized micro-judges
    (ForensicTranscriptJudge, ExecutiveRecapJudge, GraphExtractionJudge)
    and MasterVectorEvaluator.
    """
    def __init__(self, **kwargs: Any):
        self.mock = bool(kwargs.get("mock", False))
        self.model = kwargs.get("model")
        self.reasoning_effort = kwargs.get("reasoning_effort")
        self.judge_forensic = ForensicTranscriptJudge(
            model=self.model, reasoning_effort=self.reasoning_effort, use_local_model=self.mock
        )
        self.judge_recap = ExecutiveRecapJudge(
            model=self.model, reasoning_effort=self.reasoning_effort, use_local_model=self.mock
        )
        self.judge_graph = GraphExtractionJudge(
            model=self.model, reasoning_effort=self.reasoning_effort, use_local_model=self.mock
        )
        self.reconciler = CrossArtifactReconciler()
        self._evaluator = None

    def get_borderline_band(self, judge_key: str, priors_path: Optional[Any] = None) -> Tuple[float, float]:
        """Returns calibrated borderline escalation band for a specialized micro-judge."""
        norm = judge_key.lower().strip()
        out_num = 1 if ("1" in norm or "forensic" in norm) else (3 if ("3" in norm or "graph" in norm) else 2)
        from dual_pipeline.llm_judge_core.calibration_protocol import get_calibrated_borderline_band
        return get_calibrated_borderline_band(norm, output_num=out_num, priors_path=priors_path)

    @property
    def evaluator(self) -> Any:
        if self._evaluator is None:
            from dual_pipeline.llm_judge_core.master_vector_evaluator import MasterVectorEvaluator
            self._evaluator = MasterVectorEvaluator()
        return self._evaluator

    def evaluate_all(
        self, transcript: str, recap: str, claims: List[Dict[str, Any]], active_facts: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Runs the Master Vector Evaluator."""
        return self.evaluator.evaluate_release(
            raw_transcript=transcript,
            recap_text=recap,
            candidate_claims=claims,
            active_historical_facts=active_facts,
        )

    def evaluate_graph(
        self, f3_text: str, f1_text: str = "", **kwargs: Any
    ) -> Dict[str, Any]:
        """Dispatch heuristic pre-judging prior to frontier LLM judge invocation."""
        if kwargs.get("mock"):
            return self.judge_graph.evaluate(f3_text, f1_text, **kwargs)
        from dual_pipeline.llm_judge_core.heuristic_pre_judge import evaluate_heuristic_pre_judge
        pre_res = evaluate_heuristic_pre_judge(f3_text, f1_text)
        if not pre_res.get("passed_pre_check", True):
            return pre_res
        return self.judge_graph.evaluate(f3_text, f1_text, **kwargs)

    def evaluate_deliverable(
        self,
        deliverable: str,
        content: str,
        reference_text: str = "",
        *,
        client: Optional[Any] = None,
        mock: bool = False,
        archetype: Optional[Any] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Evaluate a single deliverable independently using its dedicated micro-judge.
        Supports:
        - Output 01 / Forensic Transcript
        - Output 02 / Executive Recap
        - Output 03 / Canonical Signal Projection
        """
        # Tier 1 Fail-Fast Deterministic Pre-Check Gate (0 LLM Tokens)
        if not mock:
            from dual_pipeline.llm_judge_core.heuristic_pre_judge import run_tier1_deterministic_pre_judge
            tier1_receipt = run_tier1_deterministic_pre_judge(
                target_artifact=deliverable,
                content=content,
                reference_text=reference_text,
                metadata=metadata,
            )
            if not tier1_receipt.passed:
                receipt_dict = tier1_receipt.to_dict()
                receipt_dict["is_valid_scorecard"] = True
                receipt_dict["composite_score"] = 0.0
                receipt_dict["tier1_gate_receipt"] = receipt_dict.copy()
                receipt_dict["validation_errors"] = [v["message"] for v in receipt_dict.get("violations", [])]
                receipt_dict["errors"] = receipt_dict["validation_errors"]
                receipt_dict["defect_codes"] = [v["code"] for v in receipt_dict.get("violations", [])]
                receipt_dict["forensic_rationale"] = (
                    f"TIER 1 DETERMINISTIC GATE BLOCKED: Deliverable failed structural/schema pre-checks prior to LLM dispatch. "
                    f"Violations: {'; '.join(receipt_dict['validation_errors'][:3])}"
                )
                return receipt_dict

        norm_key = deliverable.lower().strip()
        if norm_key in ("output_1", "output_01", "01", "1", "forensic", "transcript"):
            res = self.judge_forensic.evaluate(
                content,
                reference_text,
                client=client,
                mock=mock,
                archetype=archetype,
                metadata=metadata,
                **kwargs,
            )
            out_num = 1
        elif norm_key in ("output_2", "output_02", "02", "2", "recap", "executive_recap"):
            ref_artifact_text = kwargs.pop("ref_artifact_text", "")
            res = self.judge_recap.evaluate(
                content,
                reference_text,
                ref_artifact_text,
                client=client,
                mock=mock,
                archetype=archetype,
                metadata=metadata,
                **kwargs,
            )
            out_num = 2
        elif norm_key in ("output_3", "output_03", "03", "3", "graph", "canonical_signal_projection"):
            arch_val = archetype.value if hasattr(archetype, "value") else archetype
            res = self.evaluate_graph(
                content,
                reference_text,
                client=client,
                mock=mock,
                archetype=arch_val,
                metadata=metadata,
                **kwargs,
            )
            out_num = 3
        else:
            raise ValueError(f"Unknown deliverable identifier: {deliverable}")

        from dual_pipeline.release_policy import validate_micro_judge_scorecard
        is_valid, errs = validate_micro_judge_scorecard(res, output_num=out_num)
        res["is_valid_scorecard"] = is_valid
        if not is_valid:
            res["status"] = "FAIL"
            res["verdict"] = "FAIL"
            res["validation_errors"] = errs
        elif "status" not in res:
            res["status"] = (
                "PASS" if res.get("verdict") in ("PASS", "PASS_ADVISORY", "PASS_AUTHORIZED_EXCEPTION")
                else ("HITL_REQUIRED" if res.get("verdict") == "HITL_REQUIRED" else "FAIL")
            )
        return res

    def evaluate_parallel(
        self,
        output_1_text: str,
        output_2_text: str,
        output_3_text: str,
        raw_transcript: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Runs coordinated evaluation across Outputs 01, 02, and 03."""
        import json
        meta = metadata or {}
        kwargs = dict(kwargs)
        mock = kwargs.pop("mock", self.mock)
        frozen_deliverables = (
            kwargs.pop("frozen_deliverables", None)
            or meta.get("frozen_deliverables")
            or meta.get("frozen_artifacts")
            or {}
        )
        ref_text = raw_transcript or output_1_text

        res1 = _eval_or_reuse_frozen_deliverable(self, "output_1", output_1_text, ref_text, frozen_deliverables, mock, meta, kwargs)
        res2 = _eval_or_reuse_frozen_deliverable(self, "output_2", output_2_text, ref_text, frozen_deliverables, mock, meta, kwargs)
        res3 = _eval_or_reuse_frozen_deliverable(self, "output_3", output_3_text, ref_text, frozen_deliverables, mock, meta, kwargs)

        f3_dict = json.loads(output_3_text) if isinstance(output_3_text, str) and output_3_text.strip().startswith("{") else (output_3_text if isinstance(output_3_text, dict) else {})
        reconciliation = self.reconciler.reconcile(output_1_text, output_2_text, f3_dict, meta)

        from dual_pipeline.llm_judge_core.adversarial_probe import BoundedAdversarialProbe
        adv_res = BoundedAdversarialProbe().audit(output_1_text, output_2_text, f3_dict, mock=mock)
        overall_status, aggregate_score = _compute_parallel_aggregate(
            res1, res2, res3, reconciliation.get("passed", True), adv_res.hard_veto
        )
        return {
            "status": overall_status,
            "overall_verdict": overall_status,
            "composite_score": aggregate_score,
            "aggregate_composite_score": aggregate_score,
            "evaluator_model": self.model,
            "judge_model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "rubric_version": "2.0",
            "output_1_evaluation": res1,
            "output_2_evaluation": res2,
            "output_3_evaluation": res3,
            "cross_artifact_reconciliation": reconciliation,
            "adversarial_audit": adv_res.to_dict(),
        }


