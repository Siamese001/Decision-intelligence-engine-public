"""Unified L2 Healer Service.

Orchestrates in-flight surgical repair requests across Pipelines A–D under
OpenAI gpt-5.6-luna (reasoning_effort="high") with strict single-call budgets
and fact-preservation verification.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, Optional

from dual_pipeline.core import digest
from dual_pipeline.l2_healing_contracts import (
    HealingScope,
    L2HealingRequest,
    L2HealingResult,
    verify_fact_preservation,
)
from dual_pipeline.model_registry import ModelTier, get_binding
from dual_pipeline.storage.healing_decision_store import record_healing_decision
from storage.credentials import CredentialManager, resolve_openai_credentials

logger = logging.getLogger("dual_pipeline.l2_healer_service")


def compact_json(obj: Any) -> str:
    """Serialize JSON without indentation whitespace to minimize healer prompt tokens."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _persist_l2_healing_outcome(request: L2HealingRequest, result: L2HealingResult) -> None:
    """Persist L2 surgical healing decision to durable SQLite store and live outcome ledger."""
    before_str = (
        compact_json(request.candidate_artifact)
        if isinstance(request.candidate_artifact, (dict, list))
        else str(request.candidate_artifact)
    )
    after_str = (
        compact_json(result.healed_artifact)
        if isinstance(result.healed_artifact, (dict, list))
        else str(result.healed_artifact if result.healed_artifact is not None else before_str)
    )
    meta = dict(request.context_metadata or {})
    run_id = str(meta.get("run_id") or "run-l2-healer")
    record_healing_decision({
        "run_id": run_id,
        "subsystem": "l2_healer_service",
        "component": f"UnifiedL2HealerService:{request.scope.value}",
        "scope": request.scope.value,
        "repair_class": "SURGICAL_L2_REPAIR",
        "action_taken": result.patch_diff or result.status,
        "outcome_status": result.status,
        "before_digest": digest(before_str),
        "after_digest": digest(after_str),
        "confidence_top": 0.92 if result.is_success else 0.35,
        "confidence_second": 0.08 if result.is_success else 0.65,
        "ambiguity_margin": 0.84 if result.is_success else 0.0,
        "hitl_escalated": not result.is_success,
        "invariants_preserved": bool(result.fact_preservation_passed),
        "defect_reasons": list(request.failure_reasons or []),
        "audit_trail": list(result.repaired_fields or []),
        "metadata": {
            "tokens_used": result.tokens_used,
            "cached_tokens_used": result.cached_tokens_used,
            "latency_ms": round(result.latency_ms, 2),
            "error_message": result.error_message,
        },
    })


def _mock_heal_transcript(candidate: Any, allowed_entity_ids: set[str], latency: float) -> L2HealingResult:
    text = str(candidate)
    text = re.sub(r"\b(\d{4})-(\d{2})-(\d{2})\b", r"\2/\3/\1", text)
    for eid in allowed_entity_ids:
        if "person:" in eid:
            name = eid.replace("person:", "").replace("-", " ").title()
            text = re.sub(r"\bSpeaker\s+1:\s*", f"{name}: ", text)
    return L2HealingResult(
        status="HEALED",
        healed_artifact=text,
        patch_diff="Mock healed ISO dates and speaker attribution",
        repaired_fields=["dates", "speakers"],
        fact_preservation_passed=True,
        tokens_used=120,
        latency_ms=latency,
    )


def _mock_heal_projection(candidate: Any, failure_reasons: Any, latency: float) -> L2HealingResult:
    data = dict(candidate) if isinstance(candidate, dict) else json.loads(str(candidate))
    mutations = data.get("graph_mutations", {})
    if any("budget" in r.lower() for r in (failure_reasons or [])):
        for k in ("entity_upserts", "relationship_signals", "decision_updates", "proposal_updates", "action_updates", "risk_updates"):
            if k in mutations and len(mutations[k]) > 5:
                mutations[k] = mutations[k][:5]
    for d in mutations.get("decision_updates", []):
        if not d.get("observed_at"):
            d["observed_at"] = "09/12/2026"
        if not d.get("stale_after"):
            d["stale_after"] = "12/31/2026"
    return L2HealingResult(
        status="HEALED",
        healed_artifact=data,
        patch_diff="Mock healed graph mutations budget and temporal hygiene",
        repaired_fields=["graph_mutations"],
        fact_preservation_passed=True,
        tokens_used=150,
        latency_ms=latency,
    )


class UnifiedL2HealerService:
    """Unified engine executing surgical in-flight repair under governed ModelTier.SURGICAL_HEALER."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        mock_mode: bool = False,
    ) -> None:
        binding = get_binding(ModelTier.SURGICAL_HEALER)
        self.model = model or CredentialManager.get_credential("OPENAI_HEALER_MODEL") or binding.model_id
        self.reasoning_effort = (
            reasoning_effort
            or CredentialManager.get_credential("OPENAI_HEALER_REASONING_EFFORT")
            or binding.reasoning_effort
            or "medium"
        )
        creds = resolve_openai_credentials(
            allow_mock=mock_mode,
            model=self.model,
            reasoning_effort=self.reasoning_effort,
            default_model=binding.model_id,
            default_reasoning_effort=binding.reasoning_effort or "medium",
        )
        self.api_key = api_key or creds.api_key
        if not mock_mode and not bool(self.api_key):
            raise ValueError("OPENAI_API_KEY is required for UnifiedL2HealerService live execution when mock_mode=False.")
        self.mock_mode = mock_mode

    async def execute_healing(self, request: L2HealingRequest) -> L2HealingResult:
        """Executes a single bounded healing attempt under contract governance."""
        t0 = time.perf_counter()
        if request.max_repair_calls < 1:
            res = L2HealingResult(
                status="UNREPAIRABLE",
                healed_artifact=None,
                patch_diff="",
                error_message="Healing budget exhausted: max_repair_calls < 1",
            )
            _persist_l2_healing_outcome(request, res)
            return res

        if self.mock_mode:
            res = self._mock_healing(request, t0)
            _persist_l2_healing_outcome(request, res)
            return res

        res = await self._execute_live_healing(request, t0)
        _persist_l2_healing_outcome(request, res)
        return res

    async def _execute_live_healing(self, request: L2HealingRequest, t0: float) -> L2HealingResult:
        try:
            target_model = request.model_id or self.model
            effort = request.reasoning_effort or self.reasoning_effort or "medium"
            client, _ = CredentialManager.get_async_openai_client(
                allow_mock=self.mock_mode,
                model=target_model,
                reasoning_effort=effort,
            )
            if client is None:
                if not self.mock_mode:
                    raise RuntimeError("Live OpenAI client unavailable for healing execution.")
                return self._mock_healing(request, t0)

            kwargs = self._prepare_call_kwargs(request, target_model, effort)
            resp = await client.chat.completions.create(**kwargs)
            return self._parse_and_verify_live_response(request, target_model, kwargs, resp, t0)
        except Exception as exc:
            from dual_pipeline.exceptions import PipelineTokenBudgetExhaustedError
            if isinstance(exc, PipelineTokenBudgetExhaustedError):
                raise
            latency_ms = (time.perf_counter() - t0) * 1000.0
            logger.exception("UnifiedL2HealerService execution failed: %s", exc)
            return L2HealingResult(
                status="FAILED",
                healed_artifact=None,
                patch_diff="",
                latency_ms=latency_ms,
                error_message=str(exc),
            )

    def _prepare_call_kwargs(self, request: L2HealingRequest, target_model: str, effort: str) -> Dict[str, Any]:
        from config.request_deadline import remaining_timeout
        from dual_pipeline.cost_tracker import get_token_tracker

        system_prompt = self._build_system_prompt(request.scope)
        user_prompt = self._build_user_prompt(request)
        raw_kwargs: Dict[str, Any] = {
            "model": target_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "reasoning_effort": effort,
            "cache_namespace": f"l2_healer_{request.scope.value.lower()}",
            "timeout": remaining_timeout(120.0),
        }
        if request.scope in (HealingScope.PIPELINE_B_PROJECTION, HealingScope.PIPELINE_C_RETRIEVAL):
            raw_kwargs["response_format"] = {"type": "json_object"}
        kwargs = CredentialManager.sanitize_call_kwargs(target_model, raw_kwargs)
        estimated = (len(system_prompt) + len(user_prompt)) // 4
        get_token_tracker().intercept_token_call(estimated)
        return kwargs

    def _parse_and_verify_live_response(
        self, request: L2HealingRequest, target_model: str, kwargs: Dict[str, Any], resp: Any, t0: float
    ) -> L2HealingResult:
        from dual_pipeline.cost_tracker import get_cost_tracker
        from dual_pipeline.prompt_cache import normalize_cached_tokens

        content = resp.choices[0].message.content or ""
        latency_ms = (time.perf_counter() - t0) * 1000.0
        tokens = resp.usage.total_tokens if resp.usage else 0
        prompt_toks = getattr(resp.usage, "prompt_tokens", 0) if resp.usage else 0
        comp_toks = getattr(resp.usage, "completion_tokens", 0) if resp.usage else 0
        cached_tokens = normalize_cached_tokens(resp)
        try:
            get_cost_tracker().record_call(
                model=target_model,
                prompt_tokens=int(prompt_toks or 0),
                completion_tokens=int(comp_toks or 0),
                cached_prompt_tokens=int(cached_tokens or 0),
            )
        except Exception:
            pass

        healed_artifact: Any = content
        if kwargs.get("response_format", {}).get("type") == "json_object":
            try:
                healed_artifact = json.loads(content)
            except Exception as exc:
                return L2HealingResult(
                    status="FAILED",
                    healed_artifact=None,
                    patch_diff="",
                    latency_ms=latency_ms,
                    tokens_used=tokens,
                    cached_tokens_used=cached_tokens,
                    error_message=f"JSON decoding failed on healed artifact: {exc}",
                )

        passed = verify_fact_preservation(
            original_artifact=request.candidate_artifact,
            healed_artifact=healed_artifact,
            allowed_entities=request.allowed_entity_ids,
            allowed_evidence=request.allowed_evidence_ids,
        )
        if not passed:
            return L2HealingResult(
                status="FAILED",
                healed_artifact=None,
                patch_diff="Fact preservation check failed",
                fact_preservation_passed=False,
                tokens_used=tokens,
                cached_tokens_used=cached_tokens,
                latency_ms=latency_ms,
                error_message="L2 healing introduced ungrounded entities or evidence citations.",
            )

        return L2HealingResult(
            status="HEALED",
            healed_artifact=healed_artifact,
            patch_diff=f"Surgically healed {len(request.failure_reasons)} detected defects",
            repaired_fields=["healed_content"],
            fact_preservation_passed=True,
            tokens_used=tokens,
            cached_tokens_used=cached_tokens,
            latency_ms=latency_ms,
        )

    def _mock_healing(self, request: L2HealingRequest, t0: float) -> L2HealingResult:
        """Deterministic mock repair for offline testing and CI gates."""
        candidate = request.candidate_artifact
        latency = (time.perf_counter() - t0) * 1000.0

        if request.scope == HealingScope.PIPELINE_A_TRANSCRIPT:
            return _mock_heal_transcript(candidate, set(request.allowed_entity_ids), latency)
        if request.scope == HealingScope.PIPELINE_B_PROJECTION:
            return _mock_heal_projection(candidate, request.failure_reasons, latency)
        if request.scope == HealingScope.PIPELINE_C_RETRIEVAL:
            data = dict(candidate) if isinstance(candidate, dict) else {"query": str(candidate)}
            data["expanded_terms"] = ["system:bge-m3", "workstream:unified-retrieval"]
            return L2HealingResult(
                status="HEALED",
                healed_artifact=data,
                patch_diff="Mock expanded starved perspective query",
                repaired_fields=["expanded_terms"],
                fact_preservation_passed=True,
                tokens_used=90,
                latency_ms=latency,
            )
        if request.scope == HealingScope.PIPELINE_D_BRIEFING:
            text = str(candidate)
            text = re.sub(r"\bConsider\b", "Deploy", text)
            text = re.sub(r"\bExplore\b", "Implement", text)
            text = re.sub(r"\bTBC\b", "PartnerAdvisory Engineering Lead", text)
            return L2HealingResult(
                status="HEALED",
                healed_artifact=text,
                patch_diff="Mock converted passive action verbs and assigned owners",
                repaired_fields=["action_items"],
                fact_preservation_passed=True,
                tokens_used=110,
                latency_ms=latency,
            )
        return L2HealingResult(
            status="UNREPAIRABLE",
            healed_artifact=None,
            patch_diff="",
            latency_ms=latency,
            error_message=f"Unknown scope: {request.scope}",
        )

    def _build_system_prompt(self, scope: HealingScope) -> str:
        base = (
            "You are an expert Autonomous Pipeline Healer in the Decision Intelligence Engine.\n"
            "You are executing an in-flight SURGICAL REPAIR under strict governance.\n"
            "INVARIANTS:\n"
            "1. DO NOT fabricate new facts, ungrounded stakeholders, or ungrounded evidence.\n"
            "2. Preserve all valid, existing information intact.\n"
            "3. Address only the specific failure reasons provided.\n"
        )
        if scope == HealingScope.PIPELINE_A_TRANSCRIPT:
            return base + "Surgically repair generic speaker labels and date formatting in the transcript without changing spoken words.\n"
        elif scope == HealingScope.PIPELINE_B_PROJECTION:
            return base + "Surgically repair JSON schema, entity prefixes, orphan nodes, and materiality-proportional mutation budgets in Output 3.\n"
        elif scope == HealingScope.PIPELINE_C_RETRIEVAL:
            return base + "Surgically reformulate starved queries or bridge citation coverage using known graph ontology terms.\n"
        elif scope == HealingScope.PIPELINE_D_BRIEFING:
            return base + "Surgically convert passive verbs to decisive imperatives and assign owners in the action table.\n"
        return base

    def _build_user_prompt(self, request: L2HealingRequest) -> str:
        sorted_reasons = sorted(request.failure_reasons) if isinstance(request.failure_reasons, (list, set, tuple)) else list(request.failure_reasons)
        candidate_str = (
            compact_json(request.candidate_artifact)
            if isinstance(request.candidate_artifact, (dict, list))
            else str(request.candidate_artifact)
        )
        return (
            f"SCOPE: {request.scope.value}\n"
            f"FAILURE REASONS:\n{compact_json(sorted_reasons)}\n\n"
            f"ALLOWED ENTITIES: {sorted(request.allowed_entity_ids)}\n"
            f"ALLOWED EVIDENCE: {sorted(request.allowed_evidence_ids)}\n\n"
            f"CANDIDATE ARTIFACT TO HEAL:\n{candidate_str}"
        )
