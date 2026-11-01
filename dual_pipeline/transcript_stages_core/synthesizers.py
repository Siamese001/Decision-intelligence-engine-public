
"""Offline (fixture) and Live LLM (OpenAI) transcript synthesizers."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from config.request_deadline import RequestDeadlineExceeded, check_deadline, deadline_scope, remaining_timeout
from dual_pipeline.exceptions import PipelineTokenBudgetExhaustedError
from dual_pipeline.model_registry import ModelRegistry

from .models import AgenticLeverageNode, SynthesisTruncationError
from .offline_synthesizer import OfflineTranscriptSynthesizer

logger = logging.getLogger(__name__)

DEFAULT_SYNTHESIS_MAX_TOKENS = 64000
DEFAULT_RECAP_MAX_TOKENS = 24000
DEFAULT_FAST_MAX_TOKENS = 32000
DEFAULT_SYNTHESIS_DEADLINE_SEC = 1200.0
DEFAULT_RETRY_DEADLINE_SEC = 600.0
DEFAULT_FAST_DEADLINE_SEC = 120.0

from .heartbeat_logger import BackgroundHeartbeatLogger

__all__ = [
    "LLMTranscriptSynthesizer",
    "OfflineTranscriptSynthesizer",
    "SynthesisTruncationError",
    "BackgroundHeartbeatLogger",
]


def _safe_stderr_newline() -> None:
    try:
        import sys
        sys.stderr.write("\n")
        sys.stderr.flush()
    except Exception:
        pass


def _resolve_cache_namespace(operation_name: str) -> str:
    if "Stage01" in operation_name:
        return "stage01_forensic"
    if "Stage02" in operation_name:
        return "stage02_recap"
    if "Stage03" in operation_name:
        return "stage03_projection"
    if "Signal Triage" in operation_name:
        return "signal_triage"
    if "AgenticHunter" in operation_name or "Leverage" in operation_name:
        return "agentic_leverage"
    if "Preflight" in operation_name:
        return "preflight_metadata"
    return "synthesizer"


def _record_completion_usage(resp: Any, eff_model: str, is_batch: bool, start_mono: float) -> None:
    usage = getattr(resp, "usage", None)
    if not usage:
        return
    import time

    from dual_pipeline.cost_tracker import get_cost_tracker, get_token_tracker
    from dual_pipeline.telemetry_core.metrics import record_llm_call_metrics

    pt = int(usage.prompt_tokens) if isinstance(getattr(usage, "prompt_tokens", None), (int, float)) else 0
    ct = int(usage.completion_tokens) if isinstance(getattr(usage, "completion_tokens", None), (int, float)) else 0
    details = getattr(usage, "prompt_tokens_details", None)
    cpt = int(details.cached_tokens) if (details and isinstance(getattr(details, "cached_tokens", None), (int, float))) else 0
    cdetails = getattr(usage, "completion_tokens_details", None)
    rt = int(cdetails.reasoning_tokens) if (cdetails and isinstance(getattr(cdetails, "reasoning_tokens", None), (int, float))) else 0
    cwt = int(getattr(details, "cache_creation_input_tokens", 0) or getattr(details, "cache_write_tokens", 0) or 0)

    get_token_tracker().record_usage(
        prompt_tokens=pt,
        completion_tokens=ct,
        cached_prompt_tokens=cpt,
        cache_write_tokens=cwt,
        reasoning_tokens=rt,
    )
    get_cost_tracker().record_call(
        model=eff_model,
        prompt_tokens=pt,
        completion_tokens=ct,
        cached_prompt_tokens=cpt,
        cache_write_tokens=cwt,
        reasoning_tokens=rt,
        is_batch=is_batch,
    )
    record_llm_call_metrics(
        model=eff_model,
        duration_seconds=time.monotonic() - start_mono,
        prompt_tokens=pt,
        completion_tokens=ct,
        reasoning_tokens=rt,
    )


class LLMTranscriptSynthesizer:
    def __init__(
        self,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        d01_model: Optional[str] = None,
        d02_model: Optional[str] = None,
        d03_model: Optional[str] = None,
        agentic_model: Optional[str] = None,
        use_complexity_classifier: Optional[bool] = None,
        *,
        _client: Optional[Any] = None,
        _creds: Optional[Any] = None,
    ):
        from config.credentials import REASONING_MODEL_PREFIXES, canonicalize_model_name
        eff_model = model
        eff_effort = reasoning_effort
        canon = canonicalize_model_name(eff_model) if eff_model else None
        if eff_effort is None and canon and canon.lower().startswith(REASONING_MODEL_PREFIXES):
            eff_effort = "high"

        if _client is not None or _creds is not None:
            # Dependency-injected path: skip credential resolution entirely.
            # Used by deterministic tests that verify call-plumbing invariants
            # without requiring live API credentials.
            self.client = _client
            if _creds is None:
                from config.credentials import OpenAICredentials
                from dual_pipeline.model_registry import ModelTier, resolve_tier_model_name
                creds = OpenAICredentials(
                    api_key="test-key-stub",
                    is_mock=False,
                    model=eff_model or resolve_tier_model_name(ModelTier.COPILOT_FAST_SNAP),
                    reasoning_effort=eff_effort or "high",
                    masked_key="[TEST_STUB]",
                    source="test_injection",
                )
            else:
                creds = _creds
        else:
            try:
                from dual_pipeline.credentials import get_openai_client
                self.client, creds = get_openai_client(
                    allow_mock=False,
                    model=eff_model,
                    reasoning_effort=eff_effort,
                )
            except ValueError as e:
                from dual_pipeline.credentials import CredentialManager
                if "OPENAI_API_KEY" in str(e) and (CredentialManager.get_credential("GEMINI_API_KEY", "") or CredentialManager.get_credential("GOOGLE_API_KEY", "")):
                    class _StubCreds:
                        api_key = CredentialManager.get_credential("GEMINI_API_KEY", "") or CredentialManager.get_credential("GOOGLE_API_KEY", "")
                        model = canon or ModelRegistry.resolve_tier(ModelTier.FAST_TIER)
                        reasoning_effort = eff_effort
                    self.client = None
                    creds = _StubCreds()
                else:
                    raise
        self.api_key = creds.api_key
        self.model = creds.model
        self.reasoning_effort = getattr(creds, "reasoning_effort", eff_effort) or "high"
        from dual_pipeline.token_optimization import PromptCacheOptimizer
        self.cache_optimizer = PromptCacheOptimizer()

        use_complexity = use_complexity_classifier
        if use_complexity is None:
            import os
            env_val = os.environ.get("USE_COMPLEXITY_ROUTING")
            if env_val is not None:
                use_complexity = env_val.lower() not in ("0", "false", "no")
            elif _client is not None or (_creds is not None and getattr(_creds, "source", "") in ("test", "test_injection")):
                use_complexity = False
            else:
                use_complexity = True
        self.use_complexity_classifier = use_complexity

        from .external_synthesizer import init_stage_models
        init_stage_models(
            self,
            model,
            d01_model,
            d02_model,
            d03_model,
            agentic_model,
            use_complexity_classifier=use_complexity,
        )

    def __getattr__(self, name: str) -> Any:
        if name in ("d01_model", "d02_model", "d03_model", "agentic_model"):
            from dual_pipeline.model_registry import ModelTier, get_binding
            return getattr(self, "model", get_binding(ModelTier.COPILOT_FAST_SNAP).model_id)
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    def _create_completion_with_effort(
        self,
        target_effort: str,
        operation_name: str = "LLM Generation",
        deadline_seconds: float = 1200.0,
        **kwargs,
    ):
        """Invoke one deadline-bound call path and record the effective settings."""
        from .completion_call import create_completion_with_effort
        with BackgroundHeartbeatLogger(operation_name=operation_name, interval=10.0):
            return create_completion_with_effort(self, target_effort, operation_name, deadline_seconds, **kwargs)

    @staticmethod
    def _stitch_continuation(prefix: str, continuation: str) -> str:
        """Stitch truncated prefix and continuation, eliminating small overlap or mid-word seam."""
        from .external_synthesizer import stitch_continuation
        return stitch_continuation(prefix, continuation)

    def _retry_forensic(
        self,
        messages: List[Dict[str, Any]],
        current_effort: str,
        current_words: int,
        partial_content: str = "",
    ) -> tuple[str, Optional[str]]:
        # Under corporate key and SRE circuit breaker architecture, preserve reasoning effort
        retry_effort = current_effort
        logger.warning(
            f"Stage01: Token budget exhausted on reasoning_effort='{current_effort}' "
            f"(finish_reason='length', words={current_words}). Retrying with '{retry_effort}' (max_completion_tokens=DEFAULT_SYNTHESIS_MAX_TOKENS)..."
        )
        if partial_content.strip():
            retry_messages = list(messages)
            retry_messages.append({"role": "assistant", "content": partial_content})
            retry_messages.append({
                "role": "user",
                "content": (
                    "Your previous response was truncated mid-stream due to output length limits. "
                    "Continue directly from where the transcript was cut off without repeating prior timestamps or turns, "
                    "preserving the exact turn numbering and forensic format to the end."
                ),
            })
        else:
            retry_messages = messages

        from dual_pipeline.cost_tracker import get_token_tracker
        estimated_tokens = sum(len(str(m.get("content", ""))) for m in retry_messages if isinstance(m, dict)) // 4
        get_token_tracker().intercept_token_call(estimated_tokens)
        check_deadline()
        response = self._create_completion_with_effort(
            target_effort=retry_effort,
            operation_name="L2-Stage01 Forensic Normalization (Retry)",
            deadline_seconds=remaining_timeout(DEFAULT_RETRY_DEADLINE_SEC),
            model=self.d01_model,
            messages=retry_messages,
            max_completion_tokens=DEFAULT_SYNTHESIS_MAX_TOKENS,
        )
        check_deadline()
        raw = response.choices[0].message.content or ""
        finish_reason = response.choices[0].finish_reason
        logger.info(f"Stage01 retry finish_reason={finish_reason}, length={len(raw)}")
        if not raw.strip():
            raise RuntimeError(f"OpenAI API returned empty content during forensic normalization retry (finish_reason={finish_reason})")
        if partial_content.strip() and raw.strip():
            stitched = self._stitch_continuation(partial_content, raw)
            return stitched, finish_reason
        return raw.strip(), finish_reason

    def synthesize_signal_triage(self, transcript: str) -> str:
        from dual_pipeline.sanitizer import LocalPreEgressSanitizer
        prompt = (
            "Evaluate if the following transcript contains substantive meeting content, "
            "or if it is pure chit-chat, audio checks, or social filler. "
            "Reply strictly with 'NO_SIGNAL' if it is pure filler, or 'PROCEED' if it contains substantive content."
        )
        sample = transcript[:4000] if len(transcript) > 4000 else transcript
        messages = LocalPreEgressSanitizer.sanitize_messages([
            {"role": "system", "content": prompt},
            {"role": "user", "content": sample}
        ])
        from dual_pipeline.model_registry import ModelTier, get_binding
        fast_binding = get_binding(ModelTier.FAST_ROUTING.value)
        triage_timeout = max(60.0, fast_binding.timeout or 60.0)
        with deadline_scope(triage_timeout):
            check_deadline()
            resp = self._create_completion_with_effort(
                target_effort=fast_binding.reasoning_effort or "low",
                operation_name="L1 Signal Triage",
                model=fast_binding.model_id,
                messages=messages,
                temperature=0.0,
                deadline_seconds=remaining_timeout(triage_timeout),
            )
            check_deadline()
            if hasattr(resp, "choices") and resp.choices:
                return (resp.choices[0].message.content or "").strip()
            return str(resp).strip()

    def synthesize_forensic(self, system_prompt: str, user_content: str) -> str:
        from dual_pipeline.sanitizer import LocalPreEgressSanitizer
        with deadline_scope(DEFAULT_SYNTHESIS_DEADLINE_SEC):
            check_deadline()
            if self.d01_model.startswith("gemini") and not hasattr(self._create_completion_with_effort, "call_count"):
                from .external_synthesizer import call_gemini_generate_content
                logger.info(f"L2-Stage01: Calling Google {self.d01_model} for forensic normalization...")
                sanitized_msgs = LocalPreEgressSanitizer.sanitize_messages([
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ])
                clean_sys = str(sanitized_msgs[0].get("content", system_prompt))
                clean_usr = str(sanitized_msgs[1].get("content", user_content))
                raw = call_gemini_generate_content(
                    self.d01_model, clean_sys, clean_usr, json_mode=False,
                    reasoning_effort=self.reasoning_effort, deadline_seconds=remaining_timeout(DEFAULT_SYNTHESIS_DEADLINE_SEC),
                )
                check_deadline()
                if not raw or not raw.strip():
                    raise RuntimeError(f"Google Gemini returned empty content for forensic extraction ({self.d01_model})")
                return raw.strip()
            effort = self.reasoning_effort or "medium"
            logger.info(
                f"L2-Stage01: Calling OpenAI {self.d01_model} for forensic normalization "
                f"(reasoning_effort='{effort}')..."
            )
            messages, comp = self.cache_optimizer.build_cacheable_messages(system_prompt, user_content, model=self.d01_model)
            messages = LocalPreEgressSanitizer.sanitize_messages(messages)
            try:
                response = self._create_completion_with_effort(
                    target_effort=effort, operation_name="L2-Stage01 Forensic Normalization",
                    deadline_seconds=remaining_timeout(DEFAULT_SYNTHESIS_DEADLINE_SEC), model=self.d01_model,
                    messages=messages, max_completion_tokens=DEFAULT_SYNTHESIS_MAX_TOKENS,
                )
                check_deadline()
                raw = response.choices[0].message.content or ""
                finish_reason = response.choices[0].finish_reason
                logger.info(f"Stage01 finish_reason={finish_reason}, length={len(raw)}")
                hop = 0
                while finish_reason == "length" and hop < 3:
                    hop += 1
                    retry_effort = "low" if effort in ("high", "medium") else effort
                    raw, finish_reason = self._retry_forensic(messages, retry_effort, len(raw.split()), partial_content=raw)
                check_deadline()
                if not raw.strip():
                    raise RuntimeError(f"OpenAI API returned empty content for forensic normalization (finish_reason={finish_reason})")
                return raw.strip()
            except (TimeoutError, RequestDeadlineExceeded, PipelineTokenBudgetExhaustedError):
                raise
            except Exception as e:
                logger.error(f"L2-Stage01 failed: {e}")
                raise RuntimeError(f"LLM forensic normalization failed: {e}") from e

    def _retry_recap(
        self, messages: List[Dict[str, Any]], current_effort: str, current_words: int, partial_content: str = "",
    ) -> tuple[str, Optional[str]]:
        retry_effort = current_effort
        logger.warning(f"Stage02: Token budget exhausted on '{current_effort}' (finish_reason='length', words={current_words}). Retrying with '{retry_effort}' (max_completion_tokens={DEFAULT_RECAP_MAX_TOKENS})...")
        if partial_content.strip():
            retry_messages = list(messages) + [
                {"role": "assistant", "content": partial_content},
                {"role": "user", "content": "Your previous response was truncated mid-stream due to output length limits. Continue directly from where the narrative was cut off without repeating prior sentences or headers, preserving the exact section hierarchy and completing all remaining sections through the Appendix to the end."},
            ]
        else:
            retry_messages = messages
        from dual_pipeline.cost_tracker import get_token_tracker
        get_token_tracker().intercept_token_call(sum(len(str(m.get("content", ""))) for m in retry_messages if isinstance(m, dict)) // 4)
        check_deadline()
        response = self._create_completion_with_effort(
            target_effort=retry_effort, operation_name="L2-Stage02 Executive Recap (Retry)",
            deadline_seconds=remaining_timeout(DEFAULT_RETRY_DEADLINE_SEC), model=self.d02_model,
            messages=retry_messages, max_completion_tokens=DEFAULT_RECAP_MAX_TOKENS,
        )
        check_deadline()
        raw, finish_reason = response.choices[0].message.content or "", response.choices[0].finish_reason
        logger.info(f"Stage02 retry finish_reason={finish_reason}, length={len(raw)}")
        if not raw.strip():
            raise RuntimeError(f"OpenAI API returned empty content during executive recap retry (finish_reason={finish_reason})")
        if partial_content.strip() and raw.strip():
            return self._stitch_continuation(partial_content, raw), finish_reason
        return raw.strip(), finish_reason

    def _continue_appendix(self, messages: List[Dict[str, Any]], current_effort: str, partial_content: str) -> tuple[str, Optional[str]]:
        """Engage targeted two-phase continuation prompt to generate the mandatory Speaker Appendix."""
        continuation_messages = list(messages) + [
            {"role": "assistant", "content": partial_content},
            {
                "role": "user",
                "content": (
                    "The executive recap is missing the mandatory '# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown' "
                    "(Rule C-21). Generate ONLY the complete persona-centric Appendix now, providing rich multi-thematic depth "
                    "for all active participants (>=500w PRIMARY, >=200w CO_LEAD, >=120w PARTICIPANT) starting directly with "
                    "'# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown'."
                ),
            },
        ]
        from dual_pipeline.cost_tracker import get_token_tracker
        estimated_tokens = sum(len(str(m.get("content", ""))) for m in continuation_messages if isinstance(m, dict)) // 4
        get_token_tracker().intercept_token_call(estimated_tokens)
        check_deadline()
        response = self._create_completion_with_effort(
            target_effort=current_effort, operation_name="L2-Stage02 Appendix Continuation",
            deadline_seconds=remaining_timeout(DEFAULT_RETRY_DEADLINE_SEC), model=self.d02_model, messages=continuation_messages, max_completion_tokens=DEFAULT_RECAP_MAX_TOKENS,
        )
        check_deadline()
        raw_app = response.choices[0].message.content or ""
        finish_reason = response.choices[0].finish_reason
        logger.info(f"Stage02 Appendix continuation finish_reason={finish_reason}, length={len(raw_app)}")
        if not raw_app.strip():
            raise RuntimeError(f"OpenAI API returned empty content during Appendix continuation (finish_reason={finish_reason})")
        return self._stitch_continuation(partial_content, raw_app), finish_reason

    def synthesize_recap(self, system_prompt: str, user_content: str) -> str:
        with deadline_scope(DEFAULT_SYNTHESIS_DEADLINE_SEC):
            check_deadline()
            if self.d02_model.startswith("gemini"):
                from .recap_modular_dag import execute_d02_3pass_dag
                logger.info(f"L2-Stage02: Calling Google {self.d02_model} via 3-Pass Modular DAG...")
                return execute_d02_3pass_dag(self.d02_model, system_prompt, user_content)
            effort = self.reasoning_effort or "high"
            logger.info(f"L2-Stage02: Calling OpenAI {self.d02_model} for executive recap (reasoning_effort='{effort}')...")
            from dual_pipeline.sanitizer import LocalPreEgressSanitizer
            messages, comp = self.cache_optimizer.build_cacheable_messages(system_prompt, user_content, model=self.d02_model)
            messages = LocalPreEgressSanitizer.sanitize_messages(messages)
            try:
                response = self._create_completion_with_effort(
                    target_effort=effort, operation_name="L2-Stage02 Executive Recap", deadline_seconds=remaining_timeout(DEFAULT_SYNTHESIS_DEADLINE_SEC),
                    model=self.d02_model, messages=messages, max_completion_tokens=DEFAULT_RECAP_MAX_TOKENS, seed=42, temperature=0.0,
                )
                check_deadline()
                raw = response.choices[0].message.content or ""
                finish_reason = response.choices[0].finish_reason
                logger.info(f"Stage02 finish_reason={finish_reason}, length={len(raw)}")
                if finish_reason == "length":
                    raw, finish_reason = self._retry_recap(messages, "low" if effort in ("high", "medium") else effort, len(raw.split()), partial_content=raw)
                if "blocked by the gate f" in raw.lower() or "gate f audio-completeness requirement" in raw.lower():
                    logger.error("Stage02: LLM emitted Gate F refusal stub: %s", raw[:200])
                    raise RuntimeError(f"LLM erroneously refused Output 02 generation due to Gate F: {raw.strip()}")
                from dual_pipeline.transcript_duration_helper import resolve_meeting_duration
                dur = resolve_meeting_duration({}, raw)
                if dur is not None and dur >= 15 and not re.search(r"\n#{1,3}\s+(?:Appendix:\s*Detailed Stakeholder Perspectives|Appendix\b)", raw, re.IGNORECASE):
                    logger.warning("Stage02: Output 2 completed with finish_reason='stop' but lacks mandatory Appendix (duration >= 15m). Triggering targeted Appendix continuation...")
                    try:
                        raw, finish_reason = self._continue_appendix(messages, "low" if effort in ("high", "medium") else effort, partial_content=raw)
                    except Exception as cont_err:
                        logger.error(f"Stage02 Appendix continuation attempt failed: {cont_err}")
                        raise RuntimeError(f"Stage02 Appendix continuation failed for meeting duration >= 15m: {cont_err}") from cont_err
                check_deadline()
                if not raw.strip():
                    raise RuntimeError(f"OpenAI API returned empty content for executive recap (finish_reason={finish_reason})")
                return raw.strip()
            except (TimeoutError, RequestDeadlineExceeded, PipelineTokenBudgetExhaustedError):
                raise
            except Exception as e:
                _safe_stderr_newline()
                logger.error(f"L2-Stage02 failed: {e}")
                raise RuntimeError(f"LLM recap synthesis failed: {e}") from e

    def synthesize_graph(self, system_prompt: str, user_content: str) -> str:
        with deadline_scope(DEFAULT_SYNTHESIS_DEADLINE_SEC):
            check_deadline()
            if self.d03_model.startswith("gemini"):
                from dual_pipeline.sanitizer import LocalPreEgressSanitizer
                from .external_synthesizer import call_gemini_generate_content, clean_json_markdown
                logger.info(f"L2-Stage03: Calling Google {self.d03_model} for graph extraction...")
                system_prompt, user_content = LocalPreEgressSanitizer.sanitize_text(system_prompt), LocalPreEgressSanitizer.sanitize_text(user_content)
                raw = call_gemini_generate_content(
                    self.d03_model, system_prompt, user_content, json_mode=True,
                    reasoning_effort=self.reasoning_effort, deadline_seconds=remaining_timeout(DEFAULT_SYNTHESIS_DEADLINE_SEC),
                )
                check_deadline()
                if not raw or not raw.strip():
                    raise RuntimeError(f"Google Gemini returned empty content for graph extraction ({self.d03_model})")
                from .completion_call import _record_observed
                _record_observed(self, None, self.d03_model, self.reasoning_effort)
                return clean_json_markdown(raw)
            effort = self.reasoning_effort or "high"
            logger.info(f"L2-Stage03: Calling OpenAI {self.d03_model} for graph extraction (reasoning_effort='{effort}')...")
            from dual_pipeline.sanitizer import LocalPreEgressSanitizer
            messages, comp = self.cache_optimizer.build_cacheable_messages(system_prompt, user_content, model=self.d03_model)
            messages = LocalPreEgressSanitizer.sanitize_messages(messages)
            try:
                response = self._create_completion_with_effort(
                    target_effort=effort, operation_name="L2-Stage03 Graph Extraction",
                    deadline_seconds=remaining_timeout(DEFAULT_SYNTHESIS_DEADLINE_SEC), model=self.d03_model,
                    messages=messages, max_completion_tokens=DEFAULT_SYNTHESIS_MAX_TOKENS, response_format={"type": "json_object"}, seed=42, temperature=0.0,
                )
                check_deadline()
                raw = response.choices[0].message.content or ""
                finish_reason = response.choices[0].finish_reason
                logger.info(f"Stage03 finish_reason={finish_reason}, length={len(raw)}")

                is_json_valid = False
                if raw.strip():
                    try:
                        json.loads(raw.strip())
                        is_json_valid = True
                    except Exception:
                        is_json_valid = False
                if finish_reason in ("length", "max_tokens") or (finish_reason == "length" and not is_json_valid):
                    raise SynthesisTruncationError(
                        f"OpenAI Output 3 graph extraction truncated mid-stream due to output token limit "
                        f"(finish_reason='{finish_reason}', valid_json={is_json_valid}). "
                        "Aborting identical retry to prevent token spend. Prune prompt context or schema."
                    )
                if not raw.strip():
                    raise RuntimeError(f"OpenAI API returned empty content for graph extraction (finish_reason={finish_reason})")
                from .external_synthesizer import clean_json_markdown
                return clean_json_markdown(raw)
            except (SynthesisTruncationError, TimeoutError, RequestDeadlineExceeded, PipelineTokenBudgetExhaustedError):
                raise
            except Exception as e:
                _safe_stderr_newline()
                logger.error(f"L2-Stage03 failed: {e}")
                raise RuntimeError(f"LLM graph extraction failed: {e}") from e

    def synthesize_metadata(self, system_prompt: str, user_content: str) -> str:
        from dual_pipeline.sanitizer import LocalPreEgressSanitizer
        with deadline_scope(DEFAULT_FAST_DEADLINE_SEC):
            check_deadline()
            if self.model.startswith("gemini") and not hasattr(self._create_completion_with_effort, "call_count"):
                from .external_synthesizer import call_gemini_generate_content, clean_json_markdown
                logger.info(f"Preflight: Calling Google {self.model} for metadata extraction...")
                system_prompt, user_content = LocalPreEgressSanitizer.sanitize_text(system_prompt), LocalPreEgressSanitizer.sanitize_text(user_content)
                raw = call_gemini_generate_content(self.model, system_prompt, user_content, json_mode=True, reasoning_effort="low", deadline_seconds=remaining_timeout(DEFAULT_FAST_DEADLINE_SEC))
                check_deadline()
                if not raw or not raw.strip():
                    raise RuntimeError(f"Google Gemini returned empty content for metadata extraction ({self.model})")
                return clean_json_markdown(raw)
            logger.info(f"Preflight: Calling OpenAI {self.model} for metadata extraction (reasoning_effort='low')...")
            messages, _ = self.cache_optimizer.build_cacheable_messages(system_prompt, user_content, model=self.model)
            messages = LocalPreEgressSanitizer.sanitize_messages(messages)
            try:
                response = self._create_completion_with_effort(
                    target_effort="low", operation_name="Preflight Metadata Extraction",
                    deadline_seconds=remaining_timeout(DEFAULT_FAST_DEADLINE_SEC), model=self.model,
                    messages=messages, max_completion_tokens=DEFAULT_FAST_MAX_TOKENS, response_format={"type": "json_object"},
                )
                check_deadline()
                raw = response.choices[0].message.content or ""
                if not raw.strip():
                    raise RuntimeError(f"OpenAI API returned empty content for metadata extraction (finish_reason={response.choices[0].finish_reason})")
                from .external_synthesizer import clean_json_markdown
                return clean_json_markdown(raw)
            except (TimeoutError, RequestDeadlineExceeded):
                raise
            except Exception as e:
                _safe_stderr_newline()
                logger.error(f"Preflight metadata extraction failed: {e}")
                raise RuntimeError(f"LLM preflight metadata extraction failed: {e}") from e

    def synthesize_agentic_leverage(self, system_prompt: str, user_content: str) -> List[AgenticLeverageNode]:
        from .external_synthesizer import clean_json_markdown
        prompt_with_instructions = (
            f"{system_prompt}\n\nExtract political dynamics and career wedges matching criteria. "
            "Respond with JSON object containing array 'leverage_nodes' with node_type, key_players, "
            "political_rationale, networking_wedge, verbatim_quote, speaker. Return {'leverage_nodes': []} if none."
        )
        from dual_pipeline.sanitizer import LocalPreEgressSanitizer
        with deadline_scope(360.0):
            check_deadline()
            if self.agentic_model.startswith("gemini"):
                from .external_synthesizer import call_gemini_generate_content
                try:
                    san_prompt, san_user = LocalPreEgressSanitizer.sanitize_text(prompt_with_instructions), LocalPreEgressSanitizer.sanitize_text(user_content)
                    raw = call_gemini_generate_content(self.agentic_model, san_prompt, san_user, json_mode=True, reasoning_effort=self.reasoning_effort, deadline_seconds=remaining_timeout(360.0))
                    check_deadline()
                    if not raw or not raw.strip():
                        raise RuntimeError(f"Google Gemini returned empty content for agentic leverage extraction ({self.agentic_model})")
                    nodes_data = json.loads(clean_json_markdown(raw)).get("leverage_nodes", [])
                    return [AgenticLeverageNode.model_validate(n) for n in (nodes_data if isinstance(nodes_data, list) else [nodes_data])]
                except (TimeoutError, RequestDeadlineExceeded):
                    raise
                except Exception as e:
                    logger.error(f"Agentic leverage synthesis failed: {e}")
                    raise RuntimeError(f"Google Gemini agentic leverage synthesis failed: {e}") from e

            logger.info(f"L2-AgenticHunter: Calling OpenAI {self.agentic_model} for Agentic Leverage extraction (reasoning_effort='high')...")
            san_msgs = LocalPreEgressSanitizer.sanitize_messages([{"role": "system", "content": prompt_with_instructions}, {"role": "user", "content": user_content}])
            try:
                response = self._create_completion_with_effort(
                    target_effort="high", operation_name="L2-AgenticHunter Leverage Extraction",
                    deadline_seconds=remaining_timeout(360.0), model=self.agentic_model,
                    messages=san_msgs,
                    max_completion_tokens=DEFAULT_FAST_MAX_TOKENS, response_format={"type": "json_object"},
                )
                check_deadline()
                raw = response.choices[0].message.content or ""
                if not raw.strip():
                    raise RuntimeError(f"OpenAI API returned empty content for agentic leverage extraction (finish_reason={response.choices[0].finish_reason})")
                nodes_data = json.loads(clean_json_markdown(raw)).get("leverage_nodes", [])
                return [AgenticLeverageNode.model_validate(n) for n in (nodes_data if isinstance(nodes_data, list) else [nodes_data])]
            except (TimeoutError, RequestDeadlineExceeded):
                raise
            except Exception as e:
                logger.error(f"Agentic leverage synthesis failed: {e}")
                raise RuntimeError(f"OpenAI agentic leverage synthesis failed: {e}") from e
