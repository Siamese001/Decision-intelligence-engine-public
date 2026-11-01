"""
dual_pipeline/llm_judge_core/adversarial_recap_auditor.py

Adversarial Multi-Agent Design Reviewer for Executive Recaps (Output 02).
Operates under GPT-6-Astra with high reasoning effort to cross-examine primary
judge evaluations, detect generative sycophancy, audit transcript continuity,
and prevent superficial summaries from passing release gates.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from dual_pipeline.model_registry import ModelTier, resolve_tier_model
from dual_pipeline.transcript_duration_helper import resolve_meeting_duration

from ..telemetry import record_eval_result, trace_llm_execution

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AdversarialRecapAuditResult:
    """Immutable outcome of adversarial review on Output 02."""
    status: str  # "CLEARED" | "INDICTED" | "HARD_VETO"
    hard_veto: bool
    sycophancy_detected: bool
    upstream_truncation_detected: bool
    density_defects: List[str] = field(default_factory=list)
    adversarial_penalty: int = 0
    indictment_summary: str = ""
    prosecutorial_findings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "hard_veto": self.hard_veto,
            "sycophancy_detected": self.sycophancy_detected,
            "upstream_truncation_detected": self.upstream_truncation_detected,
            "density_defects": list(self.density_defects),
            "adversarial_penalty": self.adversarial_penalty,
            "indictment_summary": self.indictment_summary,
            "prosecutorial_findings": list(self.prosecutorial_findings),
        }


def _parse_ts(ts_str: str) -> Optional[int]:
    parts = ts_str.strip().split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except ValueError:
        return None
    return None


def _detect_upstream_truncation(f1_text: str) -> tuple[bool, List[str]]:
    body = f1_text.split("## Transcript", 1)[-1] if "## Transcript" in f1_text else f1_text
    ts_matches = list(re.finditer(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\]", body))
    parsed_ts = []
    for m in ts_matches:
        sec = _parse_ts(m.group(1))
        if sec is not None:
            parsed_ts.append((sec, m.start(), m.end(), m.group(1)))

    unannotated_gaps = []
    for i in range(len(parsed_ts) - 1):
        t1_sec, _, t1_end, t1_str = parsed_ts[i]
        t2_sec, t2_start, _, t2_str = parsed_ts[i + 1]
        delta = t2_sec - t1_sec
        if delta > 180:
            intervening = body[t1_end:t2_start]
            if not re.search(r"\[Recording gap:\s*[^\]]+\]", intervening, re.IGNORECASE):
                unannotated_gaps.append(f"[{t1_str}]-[{t2_str}] ({delta}s)")

    upstream_truncation = len(unannotated_gaps) > 0
    findings = []
    if upstream_truncation:
        findings.append(
            f"UPSTREAM TRUNCATION DETECTED: Reference transcript contains unannotated silence gap(s) > 180s: {', '.join(unannotated_gaps)}."
        )
    return upstream_truncation, findings


def _extract_wedge_word_count(f2_text: str) -> int:
    wedge_m = re.search(r"#{1,3}\s+(?:Immediate Delivery Scope|Bounded Execution Wedge)", f2_text, re.IGNORECASE)
    if not wedge_m:
        return 0
    after_wedge = f2_text[wedge_m.end():]
    next_sec = re.search(r"\n#{1,3}\s+", after_wedge)
    wedge_content = after_wedge[:next_sec.start()] if next_sec else after_wedge
    return len(wedge_content.split())


def _audit_density_and_sections(
    dur_min: int,
    word_count: int,
    rigor_tier: str,
    arch: Any,
    has_synthesis: bool,
    wedge_words: int,
    output2_contract: Any,
    min_floor: Optional[int] = None,
) -> tuple[List[str], List[str]]:
    density_defects: List[str] = []
    findings: List[str] = []
    if dur_min < 15:
        return density_defects, findings

    floor_val = min_floor if min_floor is not None else (
        350 if rigor_tier == "TIER_3_LIGHTWEIGHT_STATUS" else (500 if dur_min < 45 else 800)
    )
    if word_count < floor_val:
        defect = f"Recap word count is light ({word_count} words < {floor_val} floor for a {dur_min}m meeting)"
        density_defects.append(defect)
        findings.append(f"DENSITY DEFECT: {defect}.")

    if output2_contract.is_section_mandatory("Strategic Synthesis", arch) and not has_synthesis:
        defect = "Missing Strategic Synthesis ('What We Heard' vs. 'What It Means') translation matrix"
        density_defects.append(defect)
        findings.append(f"STRUCTURAL DEFECT: {defect}.")

    if output2_contract.is_section_forbidden("Immediate Delivery Scope & Release Boundaries", arch):
        if wedge_words > 0:
            defect = f"Forbidden section 'Immediate Delivery Scope & Release Boundaries' present for {arch.value}"
            density_defects.append(defect)
            findings.append(f"EXECUTION DEFECT: {defect}.")
    elif wedge_words > 0 and wedge_words < 50:
        defect = f"Bounded Execution Wedge is superficial ({wedge_words} words < 50 floor)"
        density_defects.append(defect)
        findings.append(f"EXECUTION DEFECT: {defect}.")

    return density_defects, findings


def _audit_substance_and_density(
    f1_text: str,
    f2_text: str,
    meta: Dict[str, Any],
) -> tuple[Optional[int], List[str], List[str]]:
    dur_min = resolve_meeting_duration(meta, f2_text) or resolve_meeting_duration(meta, f1_text)

    from dual_pipeline.prompts import OUTPUT2_CONTRACT, MeetingArchetype
    from dual_pipeline.transcript_qa_core.signal_density import compute_technical_signal_density

    arch_val = meta.get("archetype") or meta.get("meeting_type")
    arch = MeetingArchetype.from_string(arch_val) if arch_val else OUTPUT2_CONTRACT.detect_archetype(text=f2_text)
    profile = compute_technical_signal_density(f2_text, metadata=meta)
    rigor_tier = profile.rigor_tier.value
    min_floor = profile.min_narrative_words

    # Compute core narrative prose word count by excluding Appendix and code-block exhibits
    core_prose = re.sub(r"#{1,3}\s+Appendix:[\s\S]*", "", f2_text, flags=re.IGNORECASE)
    core_prose = re.sub(r"```[\s\S]*?```", "", core_prose)
    word_count = len(core_prose.split())

    has_synthesis = bool(
        re.search(r"#{1,3}\s+Strategic Synthesis", f2_text, re.IGNORECASE)
        or re.search(r"\|\s*What We Heard.*?\bWhat It Means", f2_text, re.IGNORECASE)
    )

    wedge_words = _extract_wedge_word_count(f2_text)
    density_defects: List[str] = []
    findings: List[str] = []
    if dur_min:
        density_defects, findings = _audit_density_and_sections(
            dur_min=dur_min,
            word_count=word_count,
            rigor_tier=rigor_tier,
            arch=arch,
            has_synthesis=has_synthesis,
            wedge_words=wedge_words,
            output2_contract=OUTPUT2_CONTRACT,
            min_floor=min_floor,
        )

    return dur_min, density_defects, findings


def _check_sycophancy(
    primary_score: int,
    density_defects: List[str],
    upstream_truncation: bool,
    findings: List[str],
) -> bool:
    has_issues = bool(density_defects) or upstream_truncation
    if not has_issues or primary_score < 88:
        return False
    findings.append(
        f"SYCOPHANCY DETECTED: Primary judge awarded composite score {primary_score}/100 "
        f"despite {len(density_defects)} density defect(s) and upstream truncation. "
        f"Primary judge rubber-stamped a light/truncated summary."
    )
    return True


def _build_hard_veto_summary(
    sycophancy: bool,
    truncation: bool,
    defect_count: int,
) -> str:
    parts: List[str] = []
    if sycophancy:
        parts.append("sycophantic primary judge pass, ")
    if truncation:
        parts.append("upstream transcript truncation, ")
    parts.append(f"and {defect_count} critical structural density defect(s).")
    return f"PROSECUTORIAL HARD VETO: Recap rejected due to {''.join(parts)}"


def _evaluate_adversarial_outcome(
    primary_score: int,
    density_defects: List[str],
    upstream_truncation: bool,
    findings: List[str],
    dur_min: Optional[int],
) -> tuple[bool, bool, int, str, str]:
    sycophancy_detected = _check_sycophancy(primary_score, density_defects, upstream_truncation, findings)
    is_long = bool(dur_min and dur_min >= 15)
    hard_veto = sycophancy_detected or (is_long and len(density_defects) >= 2)

    if hard_veto:
        status = "HARD_VETO"
        penalty = max(35, primary_score - 60) if primary_score > 0 else 35
        summary = _build_hard_veto_summary(sycophancy_detected, upstream_truncation, len(density_defects))
        return hard_veto, sycophancy_detected, penalty, status, summary

    has_defects = bool(density_defects) or upstream_truncation
    if has_defects:
        status = "INDICTED"
        trunc_penalty = 15 if upstream_truncation else 0
        penalty = len(density_defects) * 10 + trunc_penalty
        summary = f"ADVERSARIAL INDICTMENT: Found {len(findings)} governance finding(s)."
        return hard_veto, sycophancy_detected, penalty, status, summary

    status = "CLEARED"
    summary = "ADVERSARIAL AUDIT CLEARED: Executive recap exhibits sufficient density and grounding."
    return hard_veto, sycophancy_detected, 0, status, summary


class AdversarialRecapAuditor:
    """
    Prosecutorial multi-agent reviewer cross-examining Output 02 executive recaps
    and primary judge verdicts. Prevents rubber-stamping and enforces strict
    density, continuity, and executive rigor floors.
    """

    def __init__(
        self,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
    ) -> None:
        resolved_model, resolved_effort = resolve_tier_model(ModelTier.DIALECTICAL_JUDGE)
        self.model = model or resolved_model
        self.reasoning_effort = reasoning_effort or resolved_effort or "high"

    def audit(
        self,
        f1_text: str,
        f2_text: str,
        out2_eval: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        client: Optional[Any] = None,
        mock: bool = False,
    ) -> AdversarialRecapAuditResult:
        span, recorder = trace_llm_execution(
            operation_name="pipeline.b.adversarial_recap_auditor",
            requested_model=self.model,
            reasoning_effort=self.reasoning_effort,
            prompt_content=f2_text[:1000],
        )
        try:
            meta = metadata or {}
            findings: List[str] = []

            # 1. Audit Transcript for Unannotated Large Gaps & Truncation (Rule C-11)
            upstream_truncation, trunc_findings = _detect_upstream_truncation(f1_text)
            findings.extend(trunc_findings)

            # 2. Audit Meeting Duration vs Recap Substance
            dur_min, density_defects, substance_findings = _audit_substance_and_density(f1_text, f2_text, meta)
            findings.extend(substance_findings)

            # 3. Cross-Examine Primary Judge for Generative Sycophancy / Rubber-Stamping
            primary_score = out2_eval.get("composite_score", 0) if out2_eval else 0
            hard_veto, sycophancy_detected, penalty, status, summary = _evaluate_adversarial_outcome(
                primary_score, density_defects, upstream_truncation, findings, dur_min
            )

            res = AdversarialRecapAuditResult(
                status=status,
                hard_veto=hard_veto,
                sycophancy_detected=sycophancy_detected,
                upstream_truncation_detected=upstream_truncation,
                density_defects=density_defects,
                adversarial_penalty=penalty,
                indictment_summary=summary,
                prosecutorial_findings=findings,
            )
            record_eval_result(
                span,
                gate_id="adversarial_recap_auditor",
                verdict="FAIL" if hard_veto else ("PASS_ADVISORY" if penalty > 0 else "PASS"),
                score=max(0, 100 - penalty),
                is_mock=mock or client is None,
            )
            return res
        finally:
            span.end()
