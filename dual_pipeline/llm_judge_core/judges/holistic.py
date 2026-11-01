"""HolisticReleaseJudge evaluating Outputs 01, 02, and 03 in a unified inference pass."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Union

from dual_pipeline.llm_judge_core.reconciler import CrossArtifactReconciler
from dual_pipeline.prompts import CANONICAL_PROMPT_CONFIG, CANONICAL_PROMPT_REGISTRY
from dual_pipeline.release_thresholds import CANONICAL_RELEASE_POLICY, ReleaseVerdict
from dual_pipeline.telemetry import record_eval_result, trace_llm_execution

logger = logging.getLogger(__name__)


def get_holistic_judge_prompt() -> str:
    """Retrieve canonical prompt template from registry."""
    return CANONICAL_PROMPT_REGISTRY.get_prompt("HOLISTIC_RELEASE_JUDGE")


HOLISTIC_RELEASE_JUDGE_PROMPT = ""  # Backward compatibility stub


def _read_path_if_file(val: str) -> Optional[str]:
    """Read file content if string appears to be a valid file path."""
    if "\n" not in val and len(val) < 1024:
        try:
            p = Path(val)
            if p.exists() and p.is_file():
                return p.read_text(encoding="utf-8")
        except Exception:
            pass
    return None


def _resolve_content(val: Optional[Union[Path, str]], fallback: Optional[str]) -> str:
    """Resolve content from path, string, or fallback."""
    if fallback is not None:
        return fallback
    if val is None:
        return ""
    if isinstance(val, Path):
        return val.read_text(encoding="utf-8") if val.exists() else ""
    if isinstance(val, str):
        return _read_path_if_file(val) or val
    return str(val)


def _check_c19_leak(f1: str, f2: str, f3: str) -> Optional[str]:
    """Check for prohibited career leverage or internal advisory markers."""
    markers = ("$1.2M", "$1.2m", "INTERNAL_ADVISORY", "networking_wedge", "political_rationale")
    for name, text in [("Output 01", f1), ("Output 02", f2), ("Output 03", f3)]:
        if any(m in text for m in markers):
            return name
    return None


def _build_mock_holistic_output(model: str, effort: str, recon: Dict[str, Any]) -> Dict[str, Any]:
    """Construct mock evaluation output conforming to canonical policy."""
    s1, s2, s3, s_cross = 98, 96, 95, 96
    comp = int((s1 + s2 + s3 + s_cross) / 4)
    v1 = CANONICAL_RELEASE_POLICY.evaluate_output_verdict(1, s1)
    v2 = CANONICAL_RELEASE_POLICY.evaluate_output_verdict(2, s2)
    v3 = CANONICAL_RELEASE_POLICY.evaluate_output_verdict(3, s3)
    out1_pass = v1 in (ReleaseVerdict.PASS, ReleaseVerdict.PASS_ADVISORY)
    out2_pass = (v2 == ReleaseVerdict.PASS)
    out3_pass = (v3 == ReleaseVerdict.PASS)
    passed = recon["passed"] and out1_pass and out2_pass and out3_pass
    final_pass = (v1 == ReleaseVerdict.PASS) and out2_pass and out3_pass and recon["passed"]
    verdict = "PASS" if final_pass else ("PASS_ADVISORY" if passed else "FAIL")

    return {
        "rubric_version": "2.0",
        "evaluator_model": model,
        "reasoning_effort": effort,
        "evaluation_mode": "HOLISTIC_UNIFIED",
        "status": "PASS" if passed else "FAIL",
        "composite_score": comp,
        "verdict": verdict,
        "output_1_evaluation": {"composite_score": s1, "verdict": "PASS", "forensic_rationale": "Verbatim dialogue grounded with room-mic hygiene."},
        "output_2_evaluation": {"composite_score": s2, "verdict": "PASS", "forensic_rationale": "Executive strategic synthesis with clear DRI ownership."},
        "output_3_evaluation": {"composite_score": s3, "verdict": "PASS", "forensic_rationale": "Canonical graph projection adhering to W2/W3 schema boundaries."},
        "cross_artifact_reconciliation": recon,
        "cross_artifact_integrity_score": s_cross,
        "overall_rubric_summary": f"Holistic unified evaluation: composite score {comp}, status {verdict}.",
    }


def call_holistic_llm(
    client: Any,
    model: str,
    reasoning_effort: str,
    f1_text: str,
    f2_text: str,
    f3_text: str,
    source_text: str,
    recorder: Any,
) -> Dict[str, Any]:
    """Execute LLM call for holistic 3-artifact evaluation."""
    from dual_pipeline.token_optimization import TokenProfiler
    profiler = TokenProfiler(default_model=model)
    f1_bounded = profiler.truncate_to_token_limit(f1_text, max_tokens=CANONICAL_PROMPT_CONFIG.output1.max_tokens, model=model)
    f2_bounded = profiler.truncate_to_token_limit(f2_text, max_tokens=CANONICAL_PROMPT_CONFIG.output2.max_tokens, model=model)
    f3_bounded = profiler.truncate_to_token_limit(f3_text, max_tokens=CANONICAL_PROMPT_CONFIG.output3.max_tokens, model=model)
    src_bounded = profiler.truncate_to_token_limit(source_text, max_tokens=CANONICAL_PROMPT_CONFIG.output1.max_tokens, model=model)

    user_content = f"RAW SOURCE TRANSCRIPT (REFERENCE):\n{src_bounded}\n\nOUTPUT 1:\n{f1_bounded}\n\nOUTPUT 2:\n{f2_bounded}\n\nOUTPUT 3:\n{f3_bounded}"
    prompt_str = get_holistic_judge_prompt().strip()

    from config.request_deadline import remaining_timeout

    try:
        resp = client.chat.completions.create(
            model=model,
            reasoning_effort=reasoning_effort,
            messages=[
                {"role": "system", "content": prompt_str},
                {"role": "user", "content": user_content.strip()},
            ],
            max_completion_tokens=CANONICAL_PROMPT_CONFIG.judge.max_completion_tokens,
            response_format={"type": "json_object"},
            cache_namespace="holistic_judge",
            timeout=remaining_timeout(180.0),
        )
        recorder.record_response(resp)
        raw = resp.choices[0].message.content or "{}"
    except Exception as exc:
        err_msg = str(exc).lower()
        if any(w in err_msg for w in ("429", "insufficient_quota", "spend_limit", "spend limit", "rate_limit", "rate limit")):
            logger.warning("OpenAI spend limit / 429 in Holistic Judge; failing over to Google Gemini: %s", exc)
            from config.credentials import DEFAULT_GEMINI_FAILOVER_MODEL
            from dual_pipeline.transcript_stages_core.external_synthesizer import (
                call_gemini_generate_content,
                clean_json_markdown,
            )
            raw_gemini = call_gemini_generate_content(
                model=DEFAULT_GEMINI_FAILOVER_MODEL,
                system_prompt=prompt_str,
                user_content=user_content,
                json_mode=True,
                deadline_seconds=CANONICAL_PROMPT_CONFIG.judge.deadline_seconds,
            )
            raw = clean_json_markdown(raw_gemini)
        else:
            raise
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _parse_holistic_llm_result(
    parsed: Dict[str, Any],
    recon: Dict[str, Any],
    model: str,
    effort: str,
    f1_text: str = "",
) -> Dict[str, Any]:
    """Map LLM JSON response to governed scorecard adhering to SSOT thresholds."""
    s1 = parsed.get("output_1_score", 0) or 0
    s2 = parsed.get("output_2_score", 0) or 0
    s3 = parsed.get("output_3_score", 0) or 0
    comp = parsed.get("composite_score", 0) or 0

    if f1_text:
        from dual_pipeline.transcript_qa_core.gate1_attribution import (
            check_ambiguity_lock_register_and_speaker_hitl,
        )
        alr_pass, _, _ = check_ambiguity_lock_register_and_speaker_hitl(f1_text, {})
        if not alr_pass:
            s1 = min(s1, 68)
            comp = min(comp, 68)

    v1 = CANONICAL_RELEASE_POLICY.evaluate_output_verdict(1, s1)
    v2 = CANONICAL_RELEASE_POLICY.evaluate_output_verdict(2, s2)
    v3 = CANONICAL_RELEASE_POLICY.evaluate_output_verdict(3, s3)

    out1_pass = v1 in (ReleaseVerdict.PASS, ReleaseVerdict.PASS_ADVISORY)
    out2_pass = (v2 == ReleaseVerdict.PASS)
    out3_pass = (v3 == ReleaseVerdict.PASS)
    all_passed = out1_pass and out2_pass and out3_pass and recon["passed"]
    final_pass = (v1 == ReleaseVerdict.PASS) and out2_pass and out3_pass and recon["passed"]
    effective_verdict = "PASS" if final_pass else ("PASS_ADVISORY" if all_passed else "FAIL")

    return {
        "rubric_version": "2.0",
        "evaluator_model": model,
        "reasoning_effort": effort,
        "evaluation_mode": "HOLISTIC_UNIFIED",
        "status": "PASS" if all_passed else "FAIL",
        "composite_score": comp,
        "verdict": effective_verdict,
        "output_1_evaluation": {"composite_score": s1, "verdict": v1.value},
        "output_2_evaluation": {"composite_score": s2, "verdict": v2.value},
        "output_3_evaluation": {"composite_score": s3, "verdict": v3.value},
        "cross_artifact_reconciliation": recon,
        "cross_artifact_integrity_score": parsed.get("cross_artifact_integrity_score", 0),
        "forensic_rationale": parsed.get("forensic_rationale", ""),
        "actionable_remediation_diff": parsed.get("actionable_remediation_diff", ""),
        "grounding_citations": parsed.get("grounding_citations", []),
        "overall_rubric_summary": (
            f"Holistic unified evaluation: Output 1={s1}, Output 2={s2}, "
            f"Output 3={s3}, Composite={comp}. Reconciliation: {recon.get('status', 'PASS')}."
        ),
    }


_call_holistic_llm = call_holistic_llm


class HolisticReleaseJudge:
    """Unified single-pass release judge evaluating Outputs 01, 02, and 03."""

    def __init__(
        self,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        mock: bool = False,
    ) -> None:
        from dual_pipeline.model_registry import ModelRegistry, ModelTier
        def_m, def_e = ModelRegistry.resolve_tier_model(ModelTier.DIALECTICAL_JUDGE)
        self.model = model or def_m
        self.reasoning_effort = reasoning_effort or def_e
        self.mock = mock
        self.reconciler = CrossArtifactReconciler()

    def get_borderline_band(self, priors_path: Optional[Any] = None) -> Tuple[float, float]:
        from dual_pipeline.llm_judge_core.calibration_protocol import (
            get_calibrated_borderline_band,
        )
        return get_calibrated_borderline_band("holistic", output_num=2, priors_path=priors_path)

    def evaluate(
        self,
        f1_path: Optional[Union[Path, str]] = None,
        f2_path: Optional[Union[Path, str]] = None,
        f3_path: Optional[Union[Path, str]] = None,
        source_path: Optional[Union[Path, str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        *,
        output_1_text: Optional[str] = None,
        output_2_text: Optional[str] = None,
        output_3_text: Optional[str] = None,
        raw_transcript: Optional[str] = None,
        client: Optional[Any] = None,
        mock: Optional[bool] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Execute holistic 3-artifact evaluation in a unified pass."""
        f1_text = _resolve_content(f1_path, output_1_text)
        f2_text = _resolve_content(f2_path, output_2_text)
        f3_text = _resolve_content(f3_path, output_3_text) or "{}"
        source_text = _resolve_content(source_path, raw_transcript)
        meta = metadata or kwargs.get("metadata") or {"date": "08/28/2026", "meeting_name": "Executive Sync"}
        is_mock = self.mock if mock is None else mock

        span, recorder = trace_llm_execution(
            operation_name="pipeline.b.holistic_release_judge",
            requested_model=self.model,
            reasoning_effort=self.reasoning_effort,
            prompt_content=source_text[:1000],
        )

        try:
            leaked_artifact = _check_c19_leak(f1_text, f2_text, f3_text)
            if leaked_artifact:
                record_eval_result(span, gate_id="holistic_release_judge", verdict="FAIL", score=30, is_mock=is_mock or client is None)
                return {
                    "rubric_version": "2.0",
                    "evaluator_model": self.model,
                    "reasoning_effort": self.reasoning_effort,
                    "evaluation_mode": "HOLISTIC_UNIFIED",
                    "status": "FAIL",
                    "composite_score": 30,
                    "forensic_rationale": f"RULE C-19 VIOLATION: Prohibited career leverage or internal advisory markers leaked into {leaked_artifact}.",
                    "actionable_remediation_diff": f"Quarantine and purge all private advisory markers from {leaked_artifact}.",
                    "grounding_citations": [],
                }

            try:
                f3_data = json.loads(f3_text)
            except Exception:
                f3_data = {}

            recon = self.reconciler.reconcile(f1_text, f2_text, f3_data, meta)
            if is_mock:
                recorder.record_mock("Mock mode in HolisticReleaseJudge")
                out = _build_mock_holistic_output(self.model, self.reasoning_effort, recon)
                record_eval_result(span, gate_id="holistic_release_judge", verdict=out["status"], score=out["composite_score"], is_mock=True)
                return out

            if client is None:
                from config.credentials import get_openai_client
                try:
                    client, creds = get_openai_client(
                        allow_mock=False,
                        model=self.model,
                        reasoning_effort=self.reasoning_effort,
                    )
                except ValueError as exc:
                    raise RuntimeError(
                        f"Zero mocks invariant violated: Live OpenAI client required for {self.model}, "
                        f"but credentials missing: {exc}"
                    ) from exc
                if creds.is_mock or client is None:
                    raise RuntimeError(
                        f"Zero mocks invariant violated: Live OpenAI client required for {self.model}, "
                        f"but credentials resolved with is_mock={getattr(creds, 'is_mock', True)}."
                    )

            parsed = _call_holistic_llm(
                client=client,
                model=self.model,
                reasoning_effort=self.reasoning_effort,
                f1_text=f1_text,
                f2_text=f2_text,
                f3_text=f3_text,
                source_text=source_text,
                recorder=recorder,
            )

            result = _parse_holistic_llm_result(
                parsed, recon, self.model, self.reasoning_effort, f1_text=f1_text
            )
            record_eval_result(span, gate_id="holistic_release_judge", verdict=result["status"], score=result["composite_score"], is_mock=False)
            return result

        except Exception as exc:
            if isinstance(exc, RuntimeError) and "Zero mocks invariant violated" in str(exc):
                raise
            recorder.record_error(exc)
            logger.error("HolisticReleaseJudge failed: %s", exc)
            return {
                "rubric_version": "2.0",
                "evaluator_model": self.model,
                "reasoning_effort": self.reasoning_effort,
                "evaluation_mode": "HOLISTIC_UNIFIED",
                "status": "FAIL",
                "composite_score": 0,
                "verdict": "FAIL",
                "forensic_rationale": str(exc),
            }
        finally:
            span.end()


HolisticQualityJudge = HolisticReleaseJudge

__all__ = [
    "HolisticReleaseJudge",
    "HolisticQualityJudge",
    "call_holistic_llm",
]
