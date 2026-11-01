"""3-Pass Parallel Modular DAG for Stage L2 Deliverable 02 (Executive Recap) synthesis.

Decomposes monolithic recap synthesis into 3 parallel bounded passes:
- Pass 2A: Strategic narrative (>= 800 words floor)
- Pass 2B: Action & governance ledger (structured Markdown table)
- Pass 2C: Detailed stakeholder perspectives appendix (>= 600 words floor)
Enforces word-floor validations with auto-retry and blocks monolithic single-call mode for non-frontier models.
"""
from __future__ import annotations

import concurrent.futures
import logging
import re
import threading
from contextvars import copy_context
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from config.request_deadline import check_deadline, deadline_scope, remaining_timeout
from config.transport import BoundedThreadPoolExecutor, SiblingPassAbortedError

from dual_pipeline.parameters import get_output2_dag_word_floors
from .call_ledger import D02CallLedger, get_active_call_ledger, measure_prompt_block_bytes
from .external_synthesizer import call_gemini_generate_content
from .gemini_context_cache import (
    create_gemini_context_cache,
    delete_gemini_context_cache,
    is_context_caching_enabled,
)
from .recap_coherence_gate import (
    parse_recap_sections,
    resolve_remediation_target_passes,
    stitch_recap_sections,
    validate_cross_pass_coherence,
)

logger = logging.getLogger(__name__)

DEFAULT_MIN_WORDS_NARRATIVE, DEFAULT_MIN_WORDS_PERSPECTIVES = get_output2_dag_word_floors()

DEFAULT_PASS_2A_DEADLINE_SEC = 120.0
DEFAULT_PASS_2B_DEADLINE_SEC = 60.0
DEFAULT_PASS_2C_DEADLINE_SEC = 120.0


def resolve_pass_deadlines(model: str) -> Dict[str, float]:
    """Resolve per-pass deadlines, scaled for thinking level and model capabilities."""
    if "gemini-3" in model.lower():  # noqa: SSOT_EXEMPT
        return {"2A": 150.0, "2B": 90.0, "2C": 150.0}
    return {"2A": DEFAULT_PASS_2A_DEADLINE_SEC, "2B": DEFAULT_PASS_2B_DEADLINE_SEC, "2C": DEFAULT_PASS_2C_DEADLINE_SEC}


def _intercept_pass_retry(attempt: int, sys_prompt: str, user_content: str) -> None:
    if attempt > 0:
        from dual_pipeline.cost_tracker import get_token_tracker
        get_token_tracker().intercept_token_call((len(sys_prompt) + len(user_content)) // 4)


_LAST_D02_CALL_BUDGET: Optional[Dict[str, Any]] = None


def get_last_d02_call_budget() -> Optional[Dict[str, Any]]:
    """Retrieve the most recent D02 call budget receipt summary."""
    return _LAST_D02_CALL_BUDGET

from .recap_pass_2b import (  # noqa: F401
    ALT_ZERO_COMMITMENTS_SENTINEL,
    DEFAULT_PASS_2B_DEADLINE_SEC,
    PASS_2B_SYSTEM_PROMPT,
    Pass2BStructureError,
    ZERO_COMMITMENTS_SENTINEL,
    execute_pass_2b,
    extract_remediation_from_user_content,
    validate_pass_2b_structure,
)

PASS_2A_SYSTEM_PROMPT = (
    "You are an executive intelligence analyst synthesizing Artifact 02 (executive_recap.md). "
    "Generate the Strategic Narrative: Document Header (Title, Date, Time, Duration, Attendees grouped by organization), "
    "Meeting Objective, Executive Summary, Key Discussion Points & Strategic Findings, and Architecture / Technical Decisions. "
    f"You MUST provide extensive detail, technical nuance, and complete explanations. Minimum length is {DEFAULT_MIN_WORDS_NARRATIVE} words. "
    "Do NOT generate the Action Ledger or Detailed Stakeholder Perspectives Appendix (they are generated separately)."
)

PASS_2C_SYSTEM_PROMPT = (
    "You are an executive forensic analyst synthesizing the Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown for Artifact 02. "
    "For each meeting attendee, provide a comprehensive section formatted per Rule C-21:\n"
    "# [Speaker Name] - [Org], [Role]\n"
    "followed by substantive bullet points: '- **[Strategic / Technical Theme]**: [Detailed forensic narrative of contributions, positions taken, and pushback]'. "
    f"You MUST cover every attendee substantively. Minimum length is {DEFAULT_MIN_WORDS_PERSPECTIVES} words."
)


class WordFloorError(ValueError):
    """Raised when pass output fails to meet declared word floor minimums."""
    pass


class MonolithicRecapBlockedError(ValueError):
    """Raised when monolithic single-call D02 synthesis is attempted on non-frontier models."""
    pass


def validate_word_floor(text: str, min_words: int, pass_label: str) -> int:
    """Validate word count against minimum floor."""
    count = len(text.split())
    if count < min_words:
        raise WordFloorError(f"{pass_label} generated {count} words, failing required floor of {min_words} words.")
    return count


def _build_cached_pass_content(
    base_pass_prompt: str,
    system_prompt: Optional[str],
    user_content: str,
    retry_note: str = "",
) -> str:
    """Builds complete pass prompt preserving Stage 02 system instructions and remediation blocks when cached_content is active."""
    parts: List[str] = []
    if system_prompt and system_prompt.strip():
        parts.append(system_prompt.strip())
    parts.append(base_pass_prompt)
    if retry_note:
        parts.append(retry_note)
    prior_cand, critique = extract_remediation_from_user_content(user_content)
    if prior_cand:
        parts.append(f"<prior_recap_candidate>\n{prior_cand}\n</prior_recap_candidate>")
    if critique:
        parts.append(f"<remediation_directive>\n{critique}\n</remediation_directive>")
    return "\n\n".join(parts)


def execute_pass_2a(
    model: str,
    user_content: str,
    min_words: int = DEFAULT_MIN_WORDS_NARRATIVE,
    max_retries: int = 2,
    deadline_seconds: float = DEFAULT_PASS_2A_DEADLINE_SEC,
    ledger: Optional[D02CallLedger] = None,
    system_prompt: Optional[str] = None,
    abort_event: Optional[threading.Event] = None,
    cached_content: Optional[str] = None,
) -> str:
    """Pass 2A: Strategic narrative generation with auto-retry ONLY on word floor enforcement."""
    with deadline_scope(deadline_seconds):
        last_text = ""
        for attempt in range(max_retries + 1):
            check_deadline()
            if abort_event is not None and abort_event.is_set():
                raise SiblingPassAbortedError("Pass 2A aborted because a sibling pass failed terminally.")

            retry_note = (
                f"RETRY DIRECTIVE: Previous attempt produced only {len(last_text.split())} words. "
                f"You MUST expand every discussion point and technical decision with exhaustive context to exceed {min_words} words."
                if attempt > 0
                else ""
            )
            if cached_content:
                req_system_prompt = ""
                req_user_content = _build_cached_pass_content(
                    PASS_2A_SYSTEM_PROMPT, system_prompt, user_content, retry_note=retry_note
                )
            else:
                prefix = f"{system_prompt.strip()}\n\n" if system_prompt and system_prompt.strip() else ""
                prompt = f"{prefix}{PASS_2A_SYSTEM_PROMPT}"
                if retry_note:
                    prompt = f"{prompt}\n\n{retry_note}"
                req_system_prompt = prompt
                req_user_content = user_content

            _intercept_pass_retry(attempt, req_system_prompt, req_user_content)
            try:
                kwargs_2a: Dict[str, Any] = {
                    "model": model, "system_prompt": req_system_prompt, "user_content": req_user_content,
                    "json_mode": False, "deadline_seconds": remaining_timeout(deadline_seconds),
                    "pass_id": "2A", "pass_attempt": attempt + 1, "ledger": ledger, "abort_event": abort_event,
                }
                if cached_content is not None:
                    kwargs_2a["cached_content"] = cached_content
                raw = call_gemini_generate_content(**kwargs_2a).strip()
                check_deadline()
                last_text = raw
                validate_word_floor(raw, min_words, "Pass 2A (Strategic Narrative)")
                return raw
            except WordFloorError as err:
                logger.warning("Pass 2A attempt %d failed word floor: %s", attempt + 1, err)
                if attempt == max_retries:
                    logger.error("Pass 2A exhausted retries (%d); failing fast per WordFloor policy", max_retries)
                    if abort_event is not None:
                        abort_event.set()
                    raise WordFloorError(f"Pass 2A failed word floor after {max_retries} retries: {err}") from err
            except Exception as exc:
                logger.error("Pass 2A failed with non-retryable error (single retry owner policy): %s", exc)
                if abort_event is not None:
                    abort_event.set()
                raise
        if abort_event is not None:
            abort_event.set()
        raise WordFloorError(f"Pass 2A failed to produce valid output after {max_retries} retries")





def execute_pass_2c(
    model: str,
    user_content: str,
    min_words: int = DEFAULT_MIN_WORDS_PERSPECTIVES,
    max_retries: int = 2,
    deadline_seconds: float = DEFAULT_PASS_2C_DEADLINE_SEC,
    ledger: Optional[D02CallLedger] = None,
    system_prompt: Optional[str] = None,
    abort_event: Optional[threading.Event] = None,
    cached_content: Optional[str] = None,
) -> str:
    """Pass 2C: Detailed Stakeholder Perspectives Appendix with auto-retry ONLY on word floor enforcement."""
    with deadline_scope(deadline_seconds):
        last_text = ""
        for attempt in range(max_retries + 1):
            check_deadline()
            if abort_event is not None and abort_event.is_set():
                raise SiblingPassAbortedError("Pass 2C aborted because a sibling pass failed terminally.")

            retry_note = (
                f"RETRY DIRECTIVE: Previous attempt produced only {len(last_text.split())} words. "
                f"You MUST expand every attendee section with deep thematic contributions to exceed {min_words} words."
                if attempt > 0
                else ""
            )
            if cached_content:
                req_system_prompt = ""
                req_user_content = _build_cached_pass_content(
                    PASS_2C_SYSTEM_PROMPT, system_prompt, user_content, retry_note=retry_note
                )
            else:
                prefix = f"{system_prompt.strip()}\n\n" if system_prompt and system_prompt.strip() else ""
                prompt = f"{prefix}{PASS_2C_SYSTEM_PROMPT}"
                if retry_note:
                    prompt = f"{prompt}\n\n{retry_note}"
                req_system_prompt = prompt
                req_user_content = user_content

            _intercept_pass_retry(attempt, req_system_prompt, req_user_content)
            try:
                kwargs_2c: Dict[str, Any] = {
                    "model": model, "system_prompt": req_system_prompt, "user_content": req_user_content,
                    "json_mode": False, "deadline_seconds": remaining_timeout(deadline_seconds),
                    "pass_id": "2C", "pass_attempt": attempt + 1, "ledger": ledger, "abort_event": abort_event,
                }
                if cached_content is not None:
                    kwargs_2c["cached_content"] = cached_content
                raw = call_gemini_generate_content(**kwargs_2c).strip()
                check_deadline()
                last_text = raw
                validate_word_floor(raw, min_words, "Pass 2C (Stakeholder Perspectives)")
                return raw
            except WordFloorError as err:
                logger.warning("Pass 2C attempt %d failed word floor: %s", attempt + 1, err)
                if attempt == max_retries:
                    logger.error("Pass 2C exhausted retries (%d); failing fast per WordFloor policy", max_retries)
                    if abort_event is not None:
                        abort_event.set()
                    raise WordFloorError(f"Pass 2C failed word floor after {max_retries} retries: {err}") from err
            except Exception as exc:
                logger.error("Pass 2C failed with non-retryable error (single retry owner policy): %s", exc)
                if abort_event is not None:
                    abort_event.set()
                raise
        if abort_event is not None:
            abort_event.set()
        raise WordFloorError(f"Pass 2C failed to produce valid output after {max_retries} retries")


def extract_remediation_from_user_content(user_content: str) -> Tuple[Optional[str], Optional[str]]:
    """Extracts prior candidate and full critique + surgical patching directive from user_content XML blocks."""
    prior_match = re.search(r"<prior_recap_candidate>([\s\S]*?)</prior_recap_candidate>", user_content)
    prior_candidate = prior_match.group(1).strip() if prior_match else None

    critique_match = re.search(
        r"CRITIQUE DIRECTIVE:\s*\n([\s\S]*?)\n</remediation_directive>",
        user_content,
    )
    if not critique_match:
        critique_match = re.search(r"<remediation_directive>([\s\S]*?)</remediation_directive>", user_content)
    critique = critique_match.group(1).strip() if critique_match else None

    return prior_candidate, critique


def execute_d02_3pass_dag(
    model: str,
    system_prompt: str,
    user_content: str,
    min_words_2a: int = DEFAULT_MIN_WORDS_NARRATIVE,
    min_words_2c: int = DEFAULT_MIN_WORDS_PERSPECTIVES,
    max_retries: int = 2,
    allow_monolithic: bool = False,
    ledger: Optional[D02CallLedger] = None,
    receipt_path: Optional[str] = None,
    enable_caching: bool = True,
    prior_candidate: Optional[str] = None,
    remediation_directive: Optional[str] = None,
    enforce_coherence: bool = False,
) -> str:
    """Orchestrates 3-pass parallel DAG execution for Deliverable 02 executive recap synthesis.

    Supports section-targeted remediation when a prior candidate and critique directive are present,
    re-running only the affected pass(es) and freezing unaffected sections byte-identically.
    Enforces deterministic cross-pass entity coherence before returning.
    """
    global _LAST_D02_CALL_BUDGET

    if allow_monolithic and model.startswith("gemini"):
        raise MonolithicRecapBlockedError(
            f"Monolithic single-call mode is disabled for non-frontier model '{model}'. "
            "D02 synthesis MUST execute through the 3-pass parallel DAG."
        )

    active_ledger = ledger or get_active_call_ledger() or D02CallLedger()
    block_bytes = measure_prompt_block_bytes(system_prompt, user_content)
    active_ledger.set_prompt_block_bytes(block_bytes)

    deadlines = resolve_pass_deadlines(model)
    abort_event = threading.Event()

    cache_resource = None
    cache_name = None
    if enable_caching and is_context_caching_enabled() and model.startswith("gemini"):
        try:
            cache_resource = create_gemini_context_cache(
                model=model,
                system_prompt=system_prompt,
                user_content=user_content,
                ttl_seconds=300,
                display_name=f"d02_prefix_{active_ledger.operation_id}",
            )
            if cache_resource and "name" in cache_resource:
                cache_name = cache_resource["name"]
                logger.info("Active Gemini context cache for D02 DAG: %s", cache_name)
        except Exception as exc:
            logger.info("Context cache creation bypassed/failed (%s); falling back to uncached execution.", exc)

    # Detect if section-targeted remediation applies
    eff_prior_cand = prior_candidate
    eff_critique = remediation_directive
    if not eff_prior_cand or not eff_critique:
        cand_ext, crit_ext = extract_remediation_from_user_content(user_content)
        eff_prior_cand = eff_prior_cand or cand_ext
        eff_critique = eff_critique or crit_ext

    prior_sections = parse_recap_sections(eff_prior_cand) if eff_prior_cand else {}
    target_passes = resolve_remediation_target_passes(eff_critique) if eff_critique else ["2A", "2B", "2C"]

    can_target = bool(
        eff_prior_cand
        and prior_sections.get("2A")
        and prior_sections.get("2B")
        and prior_sections.get("2C")
        and len(target_passes) < 3
    )

    dag_deadline = max(deadlines.values()) + 15.0

    if can_target:
        logger.info(
            "Executing D02 Targeted Remediation on %s (target passes=%s, freezing non-target passes)...",
            model,
            target_passes,
        )
        narrative = prior_sections["2A"]
        ledger_res = prior_sections["2B"]
        perspectives = prior_sections["2C"]

        try:
            with deadline_scope(dag_deadline):
                if "2A" in target_passes:
                    narrative = execute_pass_2a(
                        model, user_content, min_words_2a, max_retries, deadlines["2A"],
                        active_ledger, system_prompt=system_prompt, abort_event=abort_event, cached_content=cache_name,
                    )
                if "2B" in target_passes:
                    ledger_res = execute_pass_2b(
                        model, user_content, deadlines["2B"], active_ledger,
                        system_prompt=system_prompt, abort_event=abort_event, cached_content=cache_name,
                    )
                if "2C" in target_passes:
                    perspectives = execute_pass_2c(
                        model, user_content, min_words_2c, max_retries, deadlines["2C"],
                        active_ledger, system_prompt=system_prompt, abort_event=abort_event, cached_content=cache_name,
                    )
        finally:
            if cache_name:
                delete_gemini_context_cache(cache_name)
    else:
        logger.info(
            "Executing D02 3-Pass Parallel Modular DAG on %s (telemetry enabled, cached=%s)...",
            model,
            bool(cache_name),
        )
        try:
            with deadline_scope(dag_deadline):
                with BoundedThreadPoolExecutor(max_workers=3, thread_name_prefix="recap_dag_worker") as executor:
                    future_2a = executor.submit(
                        copy_context().run, execute_pass_2a, model, user_content, min_words_2a,
                        max_retries, deadlines["2A"], active_ledger, system_prompt=system_prompt,
                        abort_event=abort_event, cached_content=cache_name,
                    )
                    future_2b = executor.submit(
                        copy_context().run, execute_pass_2b, model, user_content,
                        deadlines["2B"], active_ledger, system_prompt=system_prompt,
                        abort_event=abort_event, cached_content=cache_name,
                    )
                    future_2c = executor.submit(
                        copy_context().run, execute_pass_2c, model, user_content, min_words_2c,
                        max_retries, deadlines["2C"], active_ledger, system_prompt=system_prompt,
                        abort_event=abort_event, cached_content=cache_name,
                    )

                    try:
                        narrative = future_2a.result(timeout=remaining_timeout(dag_deadline))
                        ledger_res = future_2b.result(timeout=remaining_timeout(dag_deadline))
                        perspectives = future_2c.result(timeout=remaining_timeout(dag_deadline))
                    except Exception:
                        abort_event.set()
                        executor.shutdown(wait=False, cancel_futures=True)
                        for f in (future_2a, future_2b, future_2c):
                            f.cancel()
                        raise
        finally:
            if cache_name:
                delete_gemini_context_cache(cache_name)

    # Stitch final authoritative recap
    stitched = stitch_recap_sections(narrative, ledger_res, perspectives)

    # Enforce deterministic cross-pass coherence gate
    if enforce_coherence:
        transcript_match = re.search(r"<forensic_transcript>([\s\S]*?)</forensic_transcript>", user_content)
        raw_transcript = transcript_match.group(1).strip() if transcript_match else user_content
        validate_cross_pass_coherence(narrative, ledger_res, raw_transcript)

    # Emit call budget receipt
    receipt_target = Path(receipt_path) if receipt_path else None
    _LAST_D02_CALL_BUDGET = active_ledger.emit_receipt(output_path=receipt_target)

    return stitched
