#!/usr/bin/env python3
"""dual_pipeline/hitl_governance_core/question_builder.py

Single Source of Truth Builder for Operator Decision Question Payloads.

Enforces Wave 5 Question Payload Contracts:
- Exactly one question (atomic presentation; no bundling).
- Minimum two mutually exclusive options.
- Recommended option listed first with '(Recommended)'.
- Explicit calibrated confidence (percentage, tier) and one-line reason on every option.
- Zero-jargon plain layman language (no file paths, code keywords, identifiers, or flags).
- Strict display limits without truncation (question <= 300 chars, option <= 250 chars).
- Decision D5 handling: Relocates extensive background detail into question text when possible.
- Post-learning confidence recalibration from HITLDecisionStore, learned option store, and HealingDecisionStore.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from config.credentials import CredentialManager
from dual_pipeline.hitl_governance_core.layman_clarity import build_layman_option
from dual_pipeline.hitl_governance_core.question_validator import (
    MAX_OPTION_LENGTH,
    MAX_QUESTION_LENGTH,
    QuestionPayloadValidationError,
    validate_question_payload,
)


@dataclass(frozen=True)
class OptionDraft:
    """Draft representation of a decision choice before final layout compilation."""

    label: str
    plain_english_summary: str
    impact_statement: str
    confidence_score: float
    credible_interval: Optional[Tuple[float, float]] = None
    sample_size: Optional[int] = None
    evidence_refs: Optional[Sequence[str]] = None
    recommended: bool = False
    prior_adjustment: float = 0.0
    background_detail: Optional[str] = None


@dataclass(frozen=True)
class QuestionPayload:
    """Compiled, fully validated operator question payload."""

    question: str
    options: List[Dict[str, Any]]
    display_options: List[str]
    metadata: Dict[str, Any] = field(default_factory=dict)
    recommended_index: int = 0

    def to_tool_call(self, tool_name: str = "ask_user_question") -> Dict[str, Any]:
        """Convert into the tool call parameter structure for interactive presentation."""
        return {
            "questions": [
                {
                    "question": self.question,
                    "options": list(self.display_options),
                    "is_multi_select": False,
                }
            ]
        }

    def to_dict(self) -> Dict[str, Any]:
        """Convert payload to plain dictionary."""
        return asdict(self)


def format_option_text(
    label: str,
    confidence_pct: Union[int, float],
    tier: str,
    impact: str,
    summary: Optional[str] = None,
    recommended: bool = False,
) -> str:
    """Format an option display string adhering to the Layman Visibility Standard."""
    prefix = "(Recommended) " if recommended else ""
    pct_val = int(round(float(confidence_pct)))
    tier_val = tier.capitalize() if tier.isupper() else tier

    clean_label = label.strip()
    if clean_label.startswith("(Recommended)"):
        clean_label = clean_label[len("(Recommended)") :].strip()

    conf_str = f"(Confidence: {pct_val}%, Tier: {tier_val})"
    summary_clean = (summary or "").strip()
    summary_part = ""
    if summary_clean and summary_clean != clean_label:
        summary_part = f" {summary_clean}" if summary_clean.endswith(".") else f" {summary_clean}."

    impact_clean = (impact or "").strip()
    impact_part = ""
    if impact_clean:
        impact_part = f" {impact_clean}" if impact_clean.endswith(".") else f" {impact_clean}."

    return f"{prefix}{clean_label} {conf_str}.{summary_part}{impact_part}".strip()


def _load_nuance_entries(metadata: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    candidates: List[Path] = []
    if metadata.get("learned_option_store_path"):
        candidates.append(Path(str(metadata["learned_option_store_path"])))
    env_store = CredentialManager.get_credential("NEE_LEARNED_HITL_OPTION_STORE")
    if env_store:
        candidates.append(Path(env_store))
    env_rt = CredentialManager.get_credential("NEE_RUNTIME_ROOT")
    if env_rt:
        candidates.append(Path(env_rt) / "learned_hitl_option_store.json")
    repo_root = Path(__file__).resolve().parents[2]
    candidates.append(repo_root / ".local" / "learned_hitl_option_store.json")
    candidates.append(repo_root / "config" / "runtime" / "learned_hitl_option_store.json")
    for sc in candidates:
        if sc.is_file():
            try:
                raw_store = json.loads(sc.read_text(encoding="utf-8"))
                if isinstance(raw_store, dict) and isinstance(raw_store.get("options"), dict):
                    return raw_store["options"]
            except Exception:
                continue
    return {}


def _load_healing_posteriors(metadata: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    healing_db = metadata.get("healing_db_path") or CredentialManager.get_credential("NEE_LEARNING_SQLITE_PATH")
    category = str(metadata.get("category", "")).lower()
    if not healing_db and "healing" not in category and not metadata.get("include_healing_posteriors"):
        return {}
    try:
        from dual_pipeline.storage.healing_decision_store import HealingDecisionStore

        store = HealingDecisionStore(db_path=healing_db)
        posteriors = store.compute_empirical_posteriors()
        merged: Dict[str, Dict[str, Any]] = {}
        for raw_key, stats in posteriors.items():
            if isinstance(stats, dict) and raw_key.startswith("repair_class:"):
                merged[raw_key.split("repair_class:", 1)[1]] = stats
        return merged
    except Exception:
        return {}


def _apply_nuance_override(
    draft: OptionDraft,
    c_score: float,
    c_sample: Optional[int],
    c_adj: float,
    nuance_entries: Dict[str, Dict[str, Any]],
    match_fn: Any,
    clean_k: str,
) -> Tuple[str, str, float, Optional[int], float]:
    eff_summary = draft.plain_english_summary
    eff_impact = draft.impact_statement
    matched = nuance_entries.get(clean_k) or next((nv for nk, nv in nuance_entries.items() if match_fn(nk, clean_k)), None)
    if not isinstance(matched, dict):
        return eff_summary, eff_impact, c_score, c_sample, c_adj
    eff_summary = str(matched.get("plain_english_summary") or eff_summary).strip()
    eff_impact = str(matched.get("impact_statement") or eff_impact).strip()
    if matched.get("posterior_confidence") is not None:
        learned_post = float(matched["posterior_confidence"])
        learned_n = int(matched.get("sample_count") or 0)
        if not c_sample or (learned_n >= 2 and abs(learned_post - draft.confidence_score) > abs(c_score - draft.confidence_score)):
            c_score = learned_post
            c_sample = learned_n or c_sample
            c_adj = round(c_score - draft.confidence_score, 4)
    return eff_summary, eff_impact, c_score, c_sample, c_adj


def _apply_healing_posterior_override(
    draft: OptionDraft,
    c_score: float,
    c_sample: Optional[int],
    c_adj: float,
    healing_posteriors: Dict[str, Dict[str, Any]],
    match_fn: Any,
    canon_fn: Any,
    clean_k: str,
) -> Tuple[float, Optional[int], float]:
    for hk, hv in healing_posteriors.items():
        h_total = int(hv.get("total_count") or hv.get("total") or 0)
        if h_total >= 1 and match_fn(canon_fn(hk), clean_k):
            h_post = float(hv.get("posterior_confidence") or hv.get("posterior_mean") or c_score)
            c_score = max(0.05, min(0.99, round(0.45 * c_score + 0.55 * h_post, 4)))
            c_sample = (c_sample or 0) + h_total
            c_adj = round(c_score - draft.confidence_score, 4)
            break
    return c_score, c_sample, c_adj


def _apply_nuance_and_healing_override(
    draft: OptionDraft,
    c_score: float,
    c_sample: Optional[int],
    c_adj: float,
    nuance_entries: Dict[str, Dict[str, Any]],
    healing_posteriors: Dict[str, Dict[str, Any]],
    match_fn: Any,
    canon_fn: Any,
    raw_key: Optional[str] = None,
    skip_healing_blend: bool = False,
) -> Tuple[str, str, float, Optional[int], float]:
    clean_k = canon_fn(raw_key or draft.label) or canon_fn(draft.label)
    eff_summary, eff_impact, c_score, c_sample, c_adj = _apply_nuance_override(
        draft, c_score, c_sample, c_adj, nuance_entries, match_fn, clean_k
    )
    if not skip_healing_blend:
        c_score, c_sample, c_adj = _apply_healing_posterior_override(
            draft, c_score, c_sample, c_adj, healing_posteriors, match_fn, canon_fn, clean_k
        )
    return eff_summary, eff_impact, c_score, c_sample, c_adj


def _ensure_single_recommended_first(drafts: List[OptionDraft], re_rank_by_score: bool) -> List[OptionDraft]:
    ordered = sorted(drafts, key=lambda d: d.confidence_score, reverse=True) if re_rank_by_score else list(drafts)
    return [
        OptionDraft(
            label=d.label,
            plain_english_summary=d.plain_english_summary,
            impact_statement=d.impact_statement,
            confidence_score=d.confidence_score,
            credible_interval=d.credible_interval,
            sample_size=d.sample_size,
            evidence_refs=d.evidence_refs,
            recommended=(idx == 0),
            prior_adjustment=d.prior_adjustment,
            background_detail=d.background_detail,
        )
        for idx, d in enumerate(ordered)
    ]


def _resolve_base_calibrated_options(
    ordered_drafts: List[OptionDraft], metadata: Mapping[str, Any], already_cal: bool, raw_keys: List[str]
) -> List[Dict[str, Any]]:
    if already_cal:
        return [
            {
                "label": d.label,
                "confidence_score": d.confidence_score,
                "confidence_interval": d.credible_interval,
                "confidence_sample_count": d.sample_size,
                "prior_adjustment": d.prior_adjustment,
            }
            for d in ordered_drafts
        ]
    from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore

    ledger_override = metadata.get("ledger_path") or CredentialManager.get_credential("NEE_HITL_LEDGER_JSON")
    sqlite_override = metadata.get("sqlite_path") or CredentialManager.get_credential("NEE_HITL_LEDGER_SQLITE")
    store = HITLDecisionStore(ledger_path=ledger_override, sqlite_path=sqlite_override)
    opt_dicts = [
        {"key": (raw_keys[i] if i < len(raw_keys) else d.label), "label": d.label, "confidence": d.confidence_score, "recommended": d.recommended}
        for i, d in enumerate(ordered_drafts)
    ]
    return store.calibrate_options(opt_dicts, category=str(metadata.get("category", "general")))


class QuestionBuilder:
    """Single Source of Truth Builder for operator interactive question payloads."""

    def __init__(self, question: str, metadata: Optional[Mapping[str, Any]] = None) -> None:
        self.question = question.strip()
        self.metadata = dict(metadata or {})
        self.options: List[OptionDraft] = []

    def add_option(
        self,
        label: str,
        plain_english_summary: str,
        impact_statement: str,
        confidence_score: float,
        credible_interval: Optional[Tuple[float, float]] = None,
        sample_size: Optional[int] = None,
        evidence_refs: Optional[Sequence[str]] = None,
        recommended: bool = False,
        prior_adjustment: float = 0.0,
        background_detail: Optional[str] = None,
    ) -> QuestionBuilder:
        """Add a draft option choice."""
        self.options.append(
            OptionDraft(
                label=label.strip(),
                plain_english_summary=plain_english_summary.strip(),
                impact_statement=impact_statement.strip(),
                confidence_score=float(confidence_score),
                credible_interval=credible_interval,
                sample_size=sample_size,
                evidence_refs=evidence_refs,
                recommended=bool(recommended),
                prior_adjustment=float(prior_adjustment),
                background_detail=background_detail.strip() if background_detail else None,
            )
        )
        return self

    def _calibrate_drafts(self, ordered_drafts: List[OptionDraft]) -> List[OptionDraft]:
        if not self.metadata.get("calibrate", True):
            return _ensure_single_recommended_first(ordered_drafts, re_rank_by_score=not any(o.recommended for o in ordered_drafts))
        try:
            from dual_pipeline.hitl_governance_core.bayesian import _match_keys, canonicalize_pattern_key

            already_cal = bool(self.metadata.get("already_calibrated", False))
            raw_keys = list(self.metadata.get("raw_option_keys") or [])
            calibrated = _resolve_base_calibrated_options(ordered_drafts, self.metadata, already_cal, raw_keys)
            nuance_entries = _load_nuance_entries(self.metadata)
            healing_posteriors = {} if already_cal else _load_healing_posteriors(self.metadata)

            updated_drafts: List[OptionDraft] = []
            for idx, (draft, c) in enumerate(zip(ordered_drafts, calibrated)):
                c_score = float(c.get("confidence_score", draft.confidence_score))
                c_interval = c.get("confidence_interval")
                c_sample = c.get("confidence_sample_count")
                c_adj = float(c.get("prior_adjustment", 0.0))
                rk = raw_keys[idx] if idx < len(raw_keys) else None
                eff_summary, eff_impact, c_score, c_sample, c_adj = _apply_nuance_and_healing_override(
                    draft, c_score, c_sample, c_adj, nuance_entries, healing_posteriors, _match_keys, canonicalize_pattern_key, raw_key=rk, skip_healing_blend=already_cal
                )
                updated_drafts.append(
                    OptionDraft(
                        label=str(c.get("label", draft.label)),
                        plain_english_summary=eff_summary,
                        impact_statement=eff_impact,
                        confidence_score=c_score,
                        credible_interval=tuple(c_interval) if c_interval else draft.credible_interval,
                        sample_size=c_sample if c_sample is not None else draft.sample_size,
                        evidence_refs=draft.evidence_refs,
                        recommended=draft.recommended,
                        prior_adjustment=c_adj,
                        background_detail=draft.background_detail,
                    )
                )

            has_empirical_shift = any((d.sample_size or 0) > 0 or abs(d.prior_adjustment) > 1e-4 for d in updated_drafts)
            no_rec = not any(d.recommended for d in updated_drafts)
            self.metadata["post_learning_recalibrated"] = bool(has_empirical_shift)
            return _ensure_single_recommended_first(updated_drafts, re_rank_by_score=(has_empirical_shift or no_rec))
        except Exception:
            return _ensure_single_recommended_first(ordered_drafts, re_rank_by_score=not any(o.recommended for o in ordered_drafts))

    @staticmethod
    def _compile_single_option(draft: OptionDraft) -> Tuple[Dict[str, Any], str]:
        score = max(0.0, min(1.0, draft.confidence_score))
        pct = round(score * 100.0, 2)
        tier = "HIGH" if score >= 0.85 else ("MEDIUM" if score >= 0.50 else "LOW")
        disp_text = format_option_text(
            label=draft.label,
            confidence_pct=pct,
            tier=tier,
            impact=draft.impact_statement,
            summary=draft.plain_english_summary,
            recommended=draft.recommended,
        )
        if len(disp_text) > MAX_OPTION_LENGTH and draft.plain_english_summary != draft.label:
            disp_text = format_option_text(
                label=draft.label,
                confidence_pct=pct,
                tier=tier,
                impact=draft.impact_statement,
                summary=None,
                recommended=draft.recommended,
            )
        opt_dict = build_layman_option(
            label=draft.label,
            plain_english_summary=draft.plain_english_summary,
            impact_statement=draft.impact_statement,
            confidence_score=score,
            credible_interval=draft.credible_interval,
            sample_size=draft.sample_size,
            evidence_refs=draft.evidence_refs,
            recommended=draft.recommended,
            prior_adjustment=draft.prior_adjustment,
            display_text=disp_text,
        )
        return opt_dict, disp_text

    def build(self) -> QuestionPayload:
        """Compile, format, and strictly validate the complete question payload."""
        if len(self.options) < 2:
            raise QuestionPayloadValidationError(
                f"QuestionBuilder requires at least 2 options, got {len(self.options)}."
            )

        recommended_opts = [o for o in self.options if o.recommended]
        non_recommended = [o for o in self.options if not o.recommended]
        initial_drafts = (recommended_opts + non_recommended) if recommended_opts else list(self.options)
        ordered_drafts = self._calibrate_drafts(initial_drafts)

        compiled_question = self.question
        relocated_details = [d.background_detail for d in ordered_drafts if d.background_detail]
        if relocated_details:
            extra_ctx = " Context: " + " ".join(relocated_details)
            if len(compiled_question + extra_ctx) <= MAX_QUESTION_LENGTH:
                compiled_question = (compiled_question + extra_ctx).strip()

        compiled_options: List[Dict[str, Any]] = []
        display_texts: List[str] = []
        for draft in ordered_drafts:
            opt_dict, disp_text = self._compile_single_option(draft)
            compiled_options.append(opt_dict)
            display_texts.append(disp_text)

        validation_result = validate_question_payload(
            question=compiled_question,
            options=compiled_options,
            metadata=self.metadata,
        )
        validation_result.raise_for_status()

        return QuestionPayload(
            question=compiled_question,
            options=compiled_options,
            display_options=display_texts,
            metadata=self.metadata,
            recommended_index=0,
        )


def build_operator_question(
    question: str,
    options: Sequence[Union[OptionDraft, Dict[str, Any]]],
    metadata: Optional[Mapping[str, Any]] = None,
) -> QuestionPayload:
    """Convenience functional interface to build and validate an operator question payload."""
    builder = QuestionBuilder(question=question, metadata=metadata)
    for opt in options:
        if isinstance(opt, OptionDraft):
            builder.add_option(
                label=opt.label,
                plain_english_summary=opt.plain_english_summary,
                impact_statement=opt.impact_statement,
                confidence_score=opt.confidence_score,
                credible_interval=opt.credible_interval,
                sample_size=opt.sample_size,
                evidence_refs=opt.evidence_refs,
                recommended=opt.recommended,
                prior_adjustment=opt.prior_adjustment,
                background_detail=opt.background_detail,
            )
        elif isinstance(opt, Mapping):
            builder.add_option(
                label=str(opt.get("label") or opt.get("text") or ""),
                plain_english_summary=str(opt.get("plain_english_summary") or opt.get("label") or ""),
                impact_statement=str(opt.get("impact_statement") or "Ensures predictable operational execution."),
                confidence_score=float(opt.get("confidence_score", 0.70)),
                credible_interval=opt.get("credible_interval"),
                sample_size=opt.get("sample_size"),
                evidence_refs=opt.get("evidence_refs"),
                recommended=bool(opt.get("recommended", False)),
                prior_adjustment=float(opt.get("prior_adjustment", 0.0)),
                background_detail=opt.get("background_detail"),
            )
        else:
            raise QuestionPayloadValidationError(f"Unsupported option item type: {type(opt).__name__}")
    return builder.build()


def build_mutation_approval_question(
    meeting_name: str,
    proposal_digest: str,
    expected_base_generation: Union[int, str],
    delta_summary: str,
    detail_context: Optional[str] = None,
) -> QuestionPayload:
    """Build the canonical 3-choice interactive question payload for mutation proposals."""
    clean_name = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", meeting_name).replace("_", " ").strip()
    clean_delta = delta_summary.replace("_", " ").strip()
    q_str = f"Authorize knowledge graph publication for meeting '{clean_name}' ({clean_delta}, base gen {expected_base_generation})?"
    if len(q_str) > MAX_QUESTION_LENGTH:
        q_str = f"Authorize knowledge graph publication for '{clean_name}'?"
    builder = QuestionBuilder(
        question=q_str,
        metadata={
            "proposal_digest": proposal_digest,
            "meeting_name": clean_name,
            "expected_base_generation": str(expected_base_generation),
            "interaction_type": "MUTATION_APPROVAL",
            "category": "release_activation",
        },
    )
    builder.add_option(
        label="Approve mutation",
        plain_english_summary="Authorize publishing to the knowledge graph.",
        impact_statement="Ensures publication proceeds safely by granting authority for this specific proposal and base generation.",
        confidence_score=0.95,
        recommended=True,
    )
    builder.add_option(
        label="Reject",
        plain_english_summary="Reject this proposal and terminate publication.",
        impact_statement="Terminates proposal with zero mutation to the authoritative graph and prevents unwanted changes.",
        confidence_score=0.15,
        recommended=False,
    )
    builder.add_option(
        label="Review details",
        plain_english_summary="Inspect proposal hashes, deltas, and evidence.",
        impact_statement="Preserves pending state while displaying complete proposal details and avoids unauthorized mutation.",
        confidence_score=0.60,
        recommended=False,
        background_detail=detail_context,
    )
    return builder.build()


def build_pr_merge_approval_question(
    branch: str,
    repo_root: Optional[Path] = None,
) -> QuestionPayload:
    """Build the calibrated interactive question payload for PR merge authorization."""
    from dual_pipeline.healing_policy import sanitize_to_layman_text

    raw_branch = re.sub(r"[/_\-]+", " ", str(branch)).strip() or "feature branch"
    clean_branch = sanitize_to_layman_text(raw_branch, fallback="feature branch").rstrip(".") or "feature branch"
    short_branch = clean_branch[:48].strip()
    q_str = f"Branch '{clean_branch}' passed readiness gates and review. Authorize governed merge into local main?"
    if len(q_str) > MAX_QUESTION_LENGTH:
        q_str = f"Authorize governed merge of '{clean_branch[:120]}' into local main?"
    meta: Dict[str, Any] = {
        "branch": branch,
        "category": "pr_merge_authorization",
        "interaction_type": "PR_MERGE_AUTHORIZATION",
        "criticality_tier": 0,
    }
    if repo_root is not None:
        cand_json = Path(repo_root) / ".local" / "ledger" / "hitl_decision_ledger.json"
        cand_sql = Path(repo_root) / ".local" / "ledger" / "hitl_decision_ledger.sqlite"
        if cand_json.is_file():
            meta["ledger_path"] = str(cand_json)
        if cand_sql.is_file():
            meta["sqlite_path"] = str(cand_sql)
    builder = QuestionBuilder(question=q_str, metadata=meta)
    approve_label = f"Approve merge of {short_branch}"
    hold_label = f"Hold merge of {short_branch}"
    builder.add_option(
        label=approve_label,
        plain_english_summary=approve_label,
        impact_statement="Deploys verified branch updates into local main and preserves release governance.",
        confidence_score=0.88,
        recommended=True,
    )
    builder.add_option(
        label=hold_label,
        plain_english_summary=hold_label,
        impact_statement="Preserves current main state and keeps the feature branch open for inspection.",
        confidence_score=0.25,
        recommended=False,
    )
    return builder.build()
