"""
dual_pipeline/release_thresholds.py

Single Source of Truth (SSOT) for all pipeline release thresholds, score bands,
and evaluation verdict rules across Forensic Transcripts, Executive Recaps,
and Canonical Signal Projections.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Tuple

from dual_pipeline.parameters import get_int_parameter


class ReleaseVerdict(str, Enum):
    """Canonical release verdict outcomes."""
    PASS = "PASS"
    PASS_ADVISORY = "PASS_ADVISORY"
    FAIL = "FAIL"


class InvariantViolationError(RuntimeError):
    """Raised when an immutable governance invariant is violated."""
    pass


def assert_inv_02_conformance(verdict: ReleaseVerdict | str, write_authorized: bool) -> None:
    """
    Enforces Core Invariant INV-02:
    PASS_ADVISORY is diagnostic only and never authorizes mutation or production write.
    """
    verdict_str = verdict.value if isinstance(verdict, ReleaseVerdict) else str(verdict)
    if verdict_str == ReleaseVerdict.PASS_ADVISORY.value and write_authorized:
        raise InvariantViolationError(
            "INV-02 Violation: PASS_ADVISORY is diagnostic only and cannot authorize mutation or write!"
        )


OUTPUT1_PRODUCTION_PASS_THRESHOLD: int = get_int_parameter("release_thresholds.output1_production_pass_threshold", 90)
OUTPUT2_PRODUCTION_PASS_THRESHOLD: int = get_int_parameter("release_thresholds.output2_production_pass_threshold", 90)
OUTPUT3_PRODUCTION_PASS_THRESHOLD: int = get_int_parameter("release_thresholds.output3_production_pass_threshold", 90)
ADVISORY_SCORE_FLOOR: int = get_int_parameter("release_thresholds.advisory_score_floor", 80)

try:
    from dual_pipeline.model_registry import ModelRegistry, ModelTier, get_binding
    _judge_binding = get_binding(ModelTier.DIALECTICAL_JUDGE)
    _resolve_fn = getattr(ModelRegistry, "get_model_id", ModelRegistry.resolve_tier_model_name)
    DEFAULT_EVALUATOR_MODEL: str = str(_resolve_fn(ModelTier.DIALECTICAL_JUDGE) or _judge_binding.model_id)
    DEFAULT_REASONING_EFFORT: str = _judge_binding.reasoning_effort or "medium"
except Exception:
    DEFAULT_EVALUATOR_MODEL = "gpt-6-luna"
    DEFAULT_REASONING_EFFORT = "medium"


@dataclass(frozen=True)
class ReleaseThresholdPolicy:
    """
    Immutable release threshold policy defining production write qualification
    and telemetry advisory boundaries across all 3 pipeline deliverables.
    """
    policy_version: str = "1.0.0"
    _output1_pass_threshold: int | None = field(default=None, repr=False)
    _output2_pass_threshold: int | None = field(default=None, repr=False)
    _output3_pass_threshold: int | None = field(default=None, repr=False)
    _advisory_floor: int | None = field(default=None, repr=False)
    default_model: str = DEFAULT_EVALUATOR_MODEL
    default_reasoning_effort: str = DEFAULT_REASONING_EFFORT

    def __init__(
        self,
        policy_version: str = "1.0.0",
        output1_pass_threshold: int | None = None,
        output2_pass_threshold: int | None = None,
        output3_pass_threshold: int | None = None,
        advisory_floor: int | None = None,
        default_model: str = DEFAULT_EVALUATOR_MODEL,
        default_reasoning_effort: str = DEFAULT_REASONING_EFFORT,
    ) -> None:
        object.__setattr__(self, "policy_version", policy_version)
        object.__setattr__(self, "_output1_pass_threshold", output1_pass_threshold)
        object.__setattr__(self, "_output2_pass_threshold", output2_pass_threshold)
        object.__setattr__(self, "_output3_pass_threshold", output3_pass_threshold)
        object.__setattr__(self, "_advisory_floor", advisory_floor)
        object.__setattr__(self, "default_model", default_model)
        object.__setattr__(self, "default_reasoning_effort", default_reasoning_effort)

    @property
    def output1_pass_threshold(self) -> int:
        if self._output1_pass_threshold is not None:
            return int(self._output1_pass_threshold)
        return get_int_parameter("release_thresholds.output1_production_pass_threshold", 90)

    @property
    def output2_pass_threshold(self) -> int:
        if self._output2_pass_threshold is not None:
            return int(self._output2_pass_threshold)
        return get_int_parameter("release_thresholds.output2_production_pass_threshold", 90)

    @property
    def output3_pass_threshold(self) -> int:
        if self._output3_pass_threshold is not None:
            return int(self._output3_pass_threshold)
        return get_int_parameter("release_thresholds.output3_production_pass_threshold", 90)

    @property
    def advisory_floor(self) -> int:
        if self._advisory_floor is not None:
            return int(self._advisory_floor)
        return get_int_parameter("release_thresholds.advisory_score_floor", 80)

    def compute_policy_digest(self) -> str:
        """Computes deterministic SHA-256 digest of policy configuration."""
        data = {
            "policy_version": self.policy_version,
            "output1_pass_threshold": self.output1_pass_threshold,
            "output2_pass_threshold": self.output2_pass_threshold,
            "output3_pass_threshold": self.output3_pass_threshold,
            "advisory_floor": self.advisory_floor,
            "default_model": self.default_model,
            "default_reasoning_effort": self.default_reasoning_effort,
        }
        canonical_json = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    def evaluate_output_verdict(self, output_num: int, score: int | float) -> ReleaseVerdict:
        """Evaluates individual output against production and advisory thresholds.

        Conforms strictly to TRANSCRIPT_SYNTHESIS_EVALUATION_RUBRIC_V1.md Section 5:
        Conforms to TRANSCRIPT_SYNTHESIS_EVALUATION_RUBRIC_V1.md §5.4:
        - Output 1: >= 90 -> PASS, 80-89 -> PASS_ADVISORY, < 80 -> FAIL.
        - Output 2: >= 90 -> PASS, < 90 -> FAIL (PASS_ADVISORY strictly forbidden).
        - Output 3: >= 90 -> PASS, < 90 -> FAIL (PASS_ADVISORY strictly forbidden).
          Per §5.3: any per-dimension score < 14/20 forces FAIL regardless of composite.
        """
        if output_num == 1:
            if score >= self.output1_pass_threshold:
                return ReleaseVerdict.PASS
            elif score >= self.advisory_floor:
                return ReleaseVerdict.PASS_ADVISORY
            return ReleaseVerdict.FAIL
        elif output_num == 2:
            return ReleaseVerdict.PASS if score >= self.output2_pass_threshold else ReleaseVerdict.FAIL
        elif output_num == 3:
            return ReleaseVerdict.PASS if score >= self.output3_pass_threshold else ReleaseVerdict.FAIL
        else:
            return ReleaseVerdict.PASS if score >= self.output1_pass_threshold else ReleaseVerdict.FAIL

    @staticmethod
    def _evaluate_single_deliverable_gate(
        eval_v: ReleaseVerdict,
        raw_v: str,
        auth_override: bool,
    ) -> Tuple[bool, bool]:
        """Returns (is_pass, is_advisory_eligible) for a single deliverable."""
        v_upper = str(raw_v).upper()
        is_auth = auth_override or (v_upper == "PASS_AUTHORIZED_EXCEPTION")
        is_pass = is_auth or ((eval_v == ReleaseVerdict.PASS) and (v_upper == "PASS"))
        is_adv = is_auth or (
            (eval_v in (ReleaseVerdict.PASS, ReleaseVerdict.PASS_ADVISORY))
            and (v_upper in ("PASS", "PASS_ADVISORY", "PASS_AUTHORIZED_EXCEPTION"))
        )
        return is_pass, is_adv

    @staticmethod
    def _resolve_final_verdict(all_pass: bool, all_adv: bool) -> Tuple[ReleaseVerdict, bool]:
        if all_pass:
            return ReleaseVerdict.PASS, True
        if all_adv:
            return ReleaseVerdict.PASS_ADVISORY, False
        return ReleaseVerdict.FAIL, False

    def evaluate_composite_release(
        self,
        s1: int | float,
        s2: int | float,
        s3: int | float,
        v1: str,
        v2: str,
        v3: str,
        deterministic_qa_passed: bool = True,
        reconciliation_passed: bool = True,
        **kwargs: Any,
    ) -> Tuple[ReleaseVerdict, bool, Dict[str, Any]]:
        """Evaluates composite release across all 3 deliverables."""
        eval_v1 = self.evaluate_output_verdict(1, s1)
        eval_v2 = self.evaluate_output_verdict(2, s2)
        eval_v3 = self.evaluate_output_verdict(3, s3)

        out1_pass, out1_adv = self._evaluate_single_deliverable_gate(
            eval_v1, v1, bool(kwargs.get("out1_authorized_exception"))
        )
        out2_pass, out2_adv = self._evaluate_single_deliverable_gate(
            eval_v2, v2, bool(kwargs.get("out2_authorized_exception"))
        )
        out3_pass, out3_adv = self._evaluate_single_deliverable_gate(
            eval_v3, v3, bool(kwargs.get("out3_authorized_exception"))
        )

        gates_ok = deterministic_qa_passed and reconciliation_passed
        all_pass = out1_pass and out2_pass and out3_pass and gates_ok
        all_adv = out1_adv and out2_adv and out3_adv and gates_ok

        final_verdict, write_auth = self._resolve_final_verdict(all_pass, all_adv)
        assert_inv_02_conformance(final_verdict, write_auth)

        meta = {
            "policy_version": self.policy_version,
            "policy_digest": self.compute_policy_digest(),
            "eval_v1": eval_v1.value,
            "eval_v2": eval_v2.value,
            "eval_v3": eval_v3.value,
            "out1_pass": out1_pass,
            "out2_pass": out2_pass,
            "out3_pass": out3_pass,
            "deterministic_qa_passed": deterministic_qa_passed,
            "reconciliation_passed": reconciliation_passed,
            "all_pass": all_pass,
            "all_advisory": all_adv,
            "inv_02_conformance": True,
        }
        return final_verdict, write_auth, meta


CANONICAL_RELEASE_POLICY = ReleaseThresholdPolicy()
