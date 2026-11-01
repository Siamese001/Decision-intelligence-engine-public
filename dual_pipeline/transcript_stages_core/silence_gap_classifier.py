"""
dual_pipeline/transcript_stages_core/silence_gap_classifier.py

Silence Gap Classification & Session Continuity Engine.
Detects silence/recording gaps > 180 seconds, evaluates confidence between
NATURAL_SESSION_PAUSE and RECORDING_GAP_INTERRUPTION against the 15% margin
threshold, and surfaces atomic HitlRequirementEnvelopes to ensure gap annotations
are compliant with Rule C-11 and Gate 1.3 timestamp integrity.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from dual_pipeline.hitl_core.envelope import create_hitl_envelope
from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.parameters import get_hitl_ambiguity_margin_threshold

logger = logging.getLogger(__name__)

# Patterns for timestamp lines e.g. [00:15:20] or [00:15:20 - 00:15:25]
TS_PATTERN = re.compile(r"\[(\d{1,2}:\d{2}:\d{2})\]")
GAP_NOTE_PATTERN = re.compile(r"\[(?:Recording gap|Natural pause|Session pause):\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*[–-]\s*(\d{1,2}:\d{2}(?::\d{2})?)[^\]]*\]", re.IGNORECASE)


def _ts_to_seconds(ts: str) -> float:
    parts = [float(p) for p in ts.split(":")]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return 0.0


def _is_hardware_starvation_gap(
    start_sec: float,
    end_sec: float,
    line_text: str,
    metadata: Dict[str, Any],
) -> bool:
    """Evaluates whether a gap interval corresponds to hardware/audio starvation."""
    line_lower = line_text.lower()
    if any(
        k in line_lower
        for k in (
            "starvation",
            "dropped stream",
            "audio drop",
            "starved",
            "underrun",
            "buffer stall",
            "stream stall",
        )
    ):
        return True

    starvation_events = metadata.get("audio_starvation_events") or []
    for ev in starvation_events:
        ev_ts = ev.get("start_sec")
        if ev_ts is None:
            ev_ts = _ts_to_seconds(ev.get("time_str", "00:00"))
        if (start_sec - 30.0) <= ev_ts <= (end_sec + 30.0):
            return True

    return False


def _build_gap_options(
    is_starvation: bool,
    duration: float,
    line_lower: str,
    start_str: str,
    end_str: str,
) -> List[Dict[str, Any]]:
    if is_starvation:
        return [
            {
                "key": "RECORDING_GAP_INTERRUPTION",
                "label": f"Recording gap: {start_str} – {end_str} (Audio drop / recording starvation / interruption)",
                "confidence": 0.95,
            },
            {
                "key": "NATURAL_SESSION_PAUSE",
                "label": f"Session pause: {start_str} – {end_str} (Bio break / offline discussion)",
                "confidence": 0.05,
            },
        ]
    if duration >= 300.0 or "starvation" in line_lower or "drop" in line_lower:
        return [
            {
                "key": "RECORDING_GAP_INTERRUPTION",
                "label": f"Recording gap: {start_str} – {end_str} (Audio drop / recording starvation / interruption)",
                "confidence": 0.85,
            },
            {
                "key": "NATURAL_SESSION_PAUSE",
                "label": f"Session pause: {start_str} – {end_str} (Bio break / offline discussion)",
                "confidence": 0.15,
            },
        ]
    return [
        {
            "key": "NATURAL_SESSION_PAUSE",
            "label": f"Session pause: {start_str} – {end_str} (Bio break / offline discussion)",
            "confidence": 0.54,
        },
        {
            "key": "RECORDING_GAP_INTERRUPTION",
            "label": f"Recording gap: {start_str} – {end_str} (Audio drop / recording pause)",
            "confidence": 0.46,
        },
    ]


def _resolve_gap_key(
    envelope: Any,
    options: List[Dict[str, Any]],
    metadata: Dict[str, Any],
    interactive_resolver: Optional[Any],
    is_starvation: bool,
    start_str: str,
    end_str: str,
    duration: float,
) -> str:
    selected_key = options[0]["key"]
    is_offline = bool(
        metadata.get("provider") == "offline"
        or metadata.get("provider_type") == "offline"
        or metadata.get("mock")
        or metadata.get("allow_autonomous_gaps")
    )
    if interactive_resolver is not None:
        try:
            selected_key = str(interactive_resolver(envelope))
        except Exception as e:
            logger.warning("Interactive silence gap resolver failed: %s", e)
    elif is_offline or not envelope.should_surface:
        selected_key = envelope.calibrated_options[0]["key"] if envelope.calibrated_options else options[0]["key"]
    else:
        from dual_pipeline.transcript_stages_core.models import HITLRequiredError

        raise HITLRequiredError(
            f"AMBIGUOUS_SILENCE_GAP: Silence interval [{start_str} – {end_str}] ({duration:.0f}s) "
            "has confidence margin within SSOT ambiguity threshold. Autonomous classification is prohibited."
        )

    if is_starvation and "PAUSE" in selected_key:
        logger.warning(
            "[STARVATION-GUARD] Refusing to classify hardware audio starvation gap [%s – %s] as NATURAL_SESSION_PAUSE. "
            "Strictly enforcing RECORDING_GAP_INTERRUPTION.",
            start_str,
            end_str,
        )
        selected_key = "RECORDING_GAP_INTERRUPTION"
    return selected_key


def _format_gap_annotation(selected_key: str, is_starvation: bool, start_str: str, end_str: str) -> str:
    if is_starvation:
        return f"[Recording gap: {start_str} – {end_str} (Audio starvation / dropped stream)]"
    if "PAUSE" in selected_key:
        return f"[Session pause: {start_str} – {end_str}]"
    return f"[Recording gap: {start_str} – {end_str}]"


def classify_silence_gaps(
    text: str,
    metadata: Dict[str, Any],
    store: Optional[HITLDecisionStore] = None,
    interactive_resolver: Optional[Any] = None,
) -> Tuple[str, Dict[str, Any]]:
    """Scan transcript for >180s gaps and classify via HITL requirement envelopes."""
    if not text:
        return text, metadata

    lines = text.splitlines(keepends=True)
    hitl_resolutions = metadata.setdefault("hitl_resolutions", {})
    decision_store = store or HITLDecisionStore()

    modified_lines: List[str] = []
    last_ts_sec: Optional[float] = None
    last_ts_str: Optional[str] = None
    intervening_lines: List[str] = []
    in_alr_section = False

    for line in lines:
        if re.match(r"^\s*#{1,3}\s+(?:Ambiguity Lock Register|Attribution Summary)\b", line, re.IGNORECASE):
            in_alr_section = True
        if in_alr_section:
            modified_lines.append(line)
            continue

        gap_match = GAP_NOTE_PATTERN.search(line)
        if gap_match:
            start_str, end_str = gap_match.group(1), gap_match.group(2)
            start_sec = _ts_to_seconds(start_str)
            end_sec = _ts_to_seconds(end_str)
            duration = end_sec - start_sec
            if duration < 180.0:
                last_ts_sec = end_sec
                last_ts_str = end_str
                intervening_lines.clear()
                modified_lines.append(line)
                continue

            is_starvation = _is_hardware_starvation_gap(start_sec, end_sec, line, metadata)
            options = _build_gap_options(is_starvation, duration, line.lower(), start_str, end_str)

            envelope = create_hitl_envelope(
                category="silence_gap_classification",
                domain="session_continuity",
                question=f"Classify {duration:.0f}s silence interval [{start_str} – {end_str}]",
                options=options,
                store=decision_store,
                threshold=get_hitl_ambiguity_margin_threshold(),
            )

            selected_key = _resolve_gap_key(
                envelope,
                options,
                metadata,
                interactive_resolver,
                is_starvation,
                start_str,
                end_str,
                duration,
            )

            is_op = interactive_resolver is not None
            receipt = envelope.resolve_and_persist(
                selection=selected_key,
                operator_id="human_operator" if is_op else "autonomous_context",
                store=decision_store,
            )

            annotation = _format_gap_annotation(selected_key, is_starvation, start_str, end_str)
            line = GAP_NOTE_PATTERN.sub(annotation, line)

            hitl_key = f"gap_{start_str.replace(':', '')}"
            basis_text = (
                f"Operator Confirmed (HITL Silence Gap Classification, receipt {receipt['receipt_digest'][:8]})"
                if is_op
                else f"Autonomous Silence Classification (receipt {receipt['receipt_digest'][:8]})"
            )
            hitl_resolutions[hitl_key] = {
                "ambiguity": f"Unannotated silence interval: [{start_str} – {end_str}] ({duration:.0f}s)",
                "locator": f"[{start_str} – {end_str}]",
                "statement_id": f"stmt-gap-{start_str.replace(':', '')[:4]}",
                "resolution": f"Verified as {selected_key} ({annotation}).",
                "basis": basis_text,
            }
            logger.info("Classified gap [%s – %s] as %s", start_str, end_str, selected_key)
            last_ts_sec = end_sec
            last_ts_str = end_str
            intervening_lines.clear()
            modified_lines.append(line)
            continue

        ts_match = TS_PATTERN.search(line)
        if ts_match:
            curr_str = ts_match.group(1)
            curr_sec = _ts_to_seconds(curr_str)
            intervening_text = "".join(intervening_lines).strip()
            from dual_pipeline.transcript_qa_core.gate1 import _is_unannotated_gap
            if (
                last_ts_sec is not None
                and (curr_sec - last_ts_sec) >= 180.0
                and _is_unannotated_gap(intervening_text, int(curr_sec - last_ts_sec))
            ):
                is_starv_unannotated = _is_hardware_starvation_gap(last_ts_sec, curr_sec, "", metadata)
                gap_tag = " (Audio starvation / dropped stream)" if is_starv_unannotated else ""
                modified_lines.append(f"[Recording gap: {last_ts_str} – {curr_str}{gap_tag}]\n\n")
            last_ts_sec = curr_sec
            last_ts_str = curr_str
            intervening_lines.clear()
        else:
            intervening_lines.append(line)

        modified_lines.append(line)

    return "".join(modified_lines), metadata
