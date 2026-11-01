"""
dual_pipeline/transcript_pipeline_core/clearance_lifecycle.py

Executes preflight seed, non-blocking in-flight accumulation, Phase 2 clearance,
resolves in-dialogue entity ambiguities (Domain D6), and applies cleared resolutions
directly into transcript turns before Stage L2 synthesis.
"""

from __future__ import annotations

from enum import Enum
import logging
import re
import time
from typing import Any, Dict, List, Optional

from dual_pipeline.ambiguity_core.models import AmbiguityDomain, AmbiguityRecord
from dual_pipeline.clearance_lifecycle_core import (
    AmbiguityLockRegister,
    InFlightAccumulator,
    Phase2ClearanceCoordinator,
    PreflightSeeder,
)
from dual_pipeline.transcript_stages_core.hitl_guard import assert_no_u0_hitl_bypass

logger = logging.getLogger(__name__)


def _resolve_from_preconfigured(
    record: AmbiguityRecord,
    pre_configured: Dict[str, Any],
    token: str,
) -> Optional[str]:
    if not (token and token in pre_configured):
        return None
    sel_key = pre_configured[token]
    for c in record.candidates:
        if c.option_id == sel_key or sel_key.lower() in c.text.lower():
            return c.option_id
    return None


def _match_d6_candidate(cands: list, token: str, dec: Dict[str, Any]) -> Optional[str]:
    cand_tokens = {c.get("attributes", {}).get("token", "").lower() for c in cands if isinstance(c, dict)}
    return dec.get("selected_option_id") if token and token in cand_tokens else None


def _match_legacy_entity_clarification(dec: Dict[str, Any], token: str, record: AmbiguityRecord) -> Optional[str]:
    dec_token = dec.get("metadata", {}).get("token", "").lower()
    if not (token and dec_token == token.lower()):
        return None
    sel = str(dec.get("selected_option", "")).lower()
    for c in record.candidates:
        if any(part in c.text.lower() for part in sel.split() if len(part) > 3):
            return c.option_id
    return None


def _resolve_from_decision_entry(dec: Dict[str, Any], token: str, record: AmbiguityRecord) -> Optional[str]:
    if dec.get("domain") == "D6_ENTITY_CLARIFICATION":
        return _match_d6_candidate(dec.get("candidates", []), token, dec)
    if dec.get("category") == "entity_clarification":
        return _match_legacy_entity_clarification(dec, token, record)
    return None


def _resolve_from_ledger_paths(token: str, record: AmbiguityRecord) -> Optional[str]:
    import json
    from pathlib import Path
    from dual_pipeline.core import ROOT

    cand_paths = [
        Path(".local/ledger/hitl_decision_ledger.json"),
        Path("hitl_decision_ledger.json"),
        ROOT / ".local" / "ledger" / "hitl_decision_ledger.json",
    ]
    for ledger_path in cand_paths:
        if not ledger_path.is_file():
            continue
        try:
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            for dec in reversed(ledger.get("decisions", [])):
                res = _resolve_from_decision_entry(dec, token, record)
                if res is not None:
                    return res
        except Exception:
            pass
    return None


def _resolve_from_interactive_tty(record: AmbiguityRecord, token: str) -> Optional[str]:
    import sys
    if not sys.stdin.isatty():
        return None
    print("\n" + "=" * 64)
    print(f"[HITL Gating] Operator Clarification Required for: '{token.title()}'")
    print(f"Excerpt: {record.source_excerpt}")
    print("Prompt Choices:")
    for idx, c in enumerate(record.candidates, 1):
        rec_marker = " (Recommended)" if c.is_recommended else ""
        conf_pct = int(c.confidence * 100)
        print(f"  [{idx}] {c.text}{rec_marker} — Confidence: {conf_pct}%")
    try:
        from config.request_deadline import read_stdin_line_with_timeout, suspend_deadline
        timeout_s = 30.0
        with suspend_deadline(max_suspend_seconds=timeout_s):
            ans = read_stdin_line_with_timeout(
                timeout_seconds=timeout_s,
                prompt=f"Select option [1-{len(record.candidates)}] (default 1): ",
            )
        if not ans:
            return _resolve_recommended_candidate(record)
        choice_idx = int(ans) - 1 if ans.isdigit() else 0
        if 0 <= choice_idx < len(record.candidates):
            return record.candidates[choice_idx].option_id
    except (EOFError, KeyboardInterrupt):
        pass
    return None


def _resolve_recommended_candidate(record: AmbiguityRecord) -> Optional[str]:
    rec = next((c for c in record.candidates if c.is_recommended), None)
    return rec.option_id if rec else (record.candidates[0].option_id if record.candidates else None)


def _execute_governed_hitl_callback(
    record: AmbiguityRecord, meta: Dict[str, Any], pre_configured: Any
) -> Optional[str]:
    if record.domain != AmbiguityDomain.D6_ENTITY_CLARIFICATION and meta.get("auto_resolve_recommended", True):
        return _resolve_recommended_candidate(record)

    token = record.candidates[0].attributes.get("token", "").lower() if record.candidates else ""
    from_pre = _resolve_from_preconfigured(record, pre_configured, token)
    if from_pre:
        return from_pre

    from_ledger = _resolve_from_ledger_paths(token, record)
    if from_ledger:
        return from_ledger

    from_tty = _resolve_from_interactive_tty(record, token)
    if from_tty:
        return from_tty

    return _resolve_recommended_candidate(record) if meta.get("auto_resolve_recommended", True) else None


def _create_governed_hitl_callback(meta: Dict[str, Any]) -> Any:
    """Builds a governed HITL callback checking pre-configured mappings, ledger, and operator inputs."""
    assert_no_u0_hitl_bypass(meta)
    custom_cb = meta.get("hitl_callback")
    if custom_cb is not None:
        return custom_cb

    pre_configured = meta.get("entity_resolutions") or {}
    return lambda rec: _execute_governed_hitl_callback(rec, meta, pre_configured)


def _parse_dict_resolution_entry(tok: str, res: Dict[str, Any]) -> Dict[str, Any]:
    c_name = str(res.get("canonical_name") or res.get("name") or tok).strip()
    raw_org = res.get("org") or res.get("organization")
    org = str(raw_org).strip() if raw_org else None
    raw_role = res.get("role")
    role = str(raw_role).strip() if raw_role else None
    c_fields = list(res.get("confirmed_fields") or [])
    if not c_fields:
        c_fields = ["token", "canonical_name"] + (["organization"] if org else []) + (["role"] if role else [])
    return {
        "token": tok,
        "canonical_name": c_name,
        "org": org,
        "role": role,
        "status": str(res.get("status", "RESOLVED_HITL")),
        "rationale": str(res.get("rationale", "Explicit operator entity resolution")),
        "confirmed_fields": sorted(list(set(c_fields))),
        "decision_id": res.get("decision_id"),
        "hitl_receipt_id": res.get("hitl_receipt_id"),
    }


def _parse_single_resolution_entry(tok: str, resolution: Any) -> Dict[str, Any]:
    if isinstance(resolution, dict):
        return _parse_dict_resolution_entry(tok, resolution)
    return {
        "token": tok,
        "canonical_name": str(resolution).strip(),
        "org": None,
        "role": None,
        "status": "RESOLVED_HITL",
        "rationale": "Explicit operator entity resolution",
        "confirmed_fields": ["canonical_name", "token"],
    }


def _merge_explicit_resolutions(meta: Dict[str, Any], entity_clarifications: List[Dict[str, Any]]) -> None:
    res_map = meta.get("entity_resolutions")
    if not res_map:
        return
    for tok, resolution in res_map.items():
        parsed = _parse_single_resolution_entry(tok, resolution)
        existing = next((ec for ec in entity_clarifications if ec.get("token", "").lower() == tok.lower()), None)
        if existing:
            existing["canonical_name"] = parsed["canonical_name"]
            if parsed["org"] is not None:
                existing["org"] = parsed["org"]
            if parsed["role"] is not None:
                existing["role"] = parsed["role"]
            existing["status"] = parsed["status"]
            existing["rationale"] = parsed["rationale"]
            existing["confirmed_fields"] = parsed["confirmed_fields"]
        else:
            entry = dict(parsed)
            entry["org"] = entry["org"] or "Unknown"
            entry["role"] = entry["role"] or "Unknown"
            entity_clarifications.append(entry)


class IngestionLifecycleState(str, Enum):
    RUNNING = "RUNNING"
    REMEDIATING = "REMEDIATING"
    PUBLISHED = "PUBLISHED"
    AWAITING_OPERATOR = "AWAITING_OPERATOR"
    FAILED = "FAILED"


class BoundedIngestionCoordinator:
    """Coordinates bounded ingestion remediation cycles (<=2) and operator escalation under SLA."""

    def __init__(self, max_remediations: int = 2, max_wall_clock_s: float = 600.0) -> None:
        self.max_remediations = max_remediations
        self.max_wall_clock_s = max_wall_clock_s
        self.state = IngestionLifecycleState.RUNNING
        self.remediation_count = 0
        self.start_time = time.monotonic()

    def record_attempt(self, passed: bool, error: Optional[str] = None) -> IngestionLifecycleState:
        elapsed = time.monotonic() - self.start_time
        if passed:
            self.state = IngestionLifecycleState.PUBLISHED
            return self.state

        if elapsed >= self.max_wall_clock_s or self.remediation_count >= self.max_remediations:
            self.state = IngestionLifecycleState.AWAITING_OPERATOR
            return self.state

        self.remediation_count += 1
        self.state = IngestionLifecycleState.REMEDIATING
        return self.state

def _is_grounded_clarification(
    info: Dict[str, Any],
    sel: Any,
    meta: Dict[str, Any],
    rec: AmbiguityRecord,
) -> bool:
    tok_key = info["token"].lower()
    pre_configured = meta.get("entity_resolutions") or {}
    has_custom_cb = meta.get("hitl_callback") is not None
    is_operator_confirmed = (
        (tok_key in pre_configured or (has_custom_cb and rec.status.value == "RESOLVED_HITL"))
        and not any(sel.option_id.endswith(s) for s in ("-clarify", "-unassigned"))
    )
    is_verified_graph = (
        bool(sel.attributes.get("is_graph_grounded", False))
        and sel.attributes.get("org") not in ("Unknown", "Unknown Org", "External", "")
        and not any(sel.option_id.endswith(s) for s in ("-clarify", "-unassigned", "-external"))
    )
    return bool(is_operator_confirmed or is_verified_graph)


def _process_cleared_records(
    cleared_records: List[AmbiguityRecord],
    meta: Dict[str, Any],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    entity_clarifications: List[Dict[str, Any]] = []
    unresolved_entities: List[Dict[str, Any]] = []
    quarantined_entities: List[Dict[str, Any]] = []

    for rec in cleared_records:
        if rec.domain != AmbiguityDomain.D6_ENTITY_CLARIFICATION:
            continue
        sel = next((c for c in rec.candidates if c.option_id == rec.selected_option_id), None)
        if not sel:
            continue
        info = {
            "token": sel.attributes.get("token", rec.ambiguity_id),
            "canonical_name": sel.attributes.get("canonical_name", sel.text),
            "org": sel.attributes.get("org", "Unknown"),
            "role": sel.attributes.get("role", "Unknown"),
            "status": rec.status.value,
            "rationale": rec.resolution_rationale,
        }
        if _is_grounded_clarification(info, sel, meta, rec):
            entity_clarifications.append(info)
        else:
            quarantined_entities.append(info)
            logger.warning(
                f"Quarantining unverified entity clarification '{info['token']}' "
                f"(status={info['status']}) under C-12/C-27."
            )
        if rec.requires_hitl and rec.status.value != "RESOLVED_HITL":
            unresolved_entities.append(info)

    return entity_clarifications, unresolved_entities, quarantined_entities


def _reconstruct_transcript_text(updated_turns: List[Dict[str, Any]], default_text: str) -> str:
    lines = []
    for t in updated_turns:
        spk = t.get("speaker")
        txt = t.get("text", "")
        if spk and not txt.startswith(f"{spk}:") and ":" not in txt:
            lines.append(f"{spk}: {txt}")
        else:
            lines.append(txt)
    return "\n".join(lines) if lines else default_text


def execute_clearance_lifecycle(
    transcript_text: str,
    meta: Dict[str, Any],
    recorder: Any,
) -> str:
    """Executes preflight seed, accumulator, phase 2 clearance coordinator, and applies resolutions.

    Returns the updated transcript text with cleared resolutions applied.
    """
    with recorder.span("nee.uc1.clearance_lifecycle"):
        snap = PreflightSeeder().seed(
            meeting_name=meta.get("meeting_name", "Executive Meeting"),
            date_iso=meta.get("date", "09/12/2026"),
            attendees=meta.get("attendees", []),
            project_acronyms=meta.get("project_acronyms", ["NEE", "NEXUS", "BGE-M3"]),
        )
        def _parse_turn_speaker(line: str) -> str:
            clean_line = re.sub(r"\[\d{1,2}:\d{2}(?::\d{2})?\]", "", line).strip()
            if ":" in clean_line:
                raw_spk = clean_line.split(":", 1)[0].strip()
                return re.sub(r"[\*\[\]#]+", "", raw_spk).strip()
            return "Speaker"

        c10_reg = AmbiguityLockRegister()
        raw_turns = [
            {"text": line, "speaker": _parse_turn_speaker(line)}
            for line in transcript_text.splitlines()
            if line.strip()
        ]

        known_entities = meta.get("known_graph_entities") or []
        cross_registry = meta.get("cross_meeting_entity_registry") or {}

        accumulator = InFlightAccumulator(register=c10_reg)
        records = accumulator.engine.scan(
            turns=raw_turns,
            known_attendees=snap.attendees,
            project_acronyms=snap.project_acronyms,
            known_entities=known_entities,
            cross_meeting_registry=cross_registry,
        )
        accumulator.accumulated_records.extend(records)
        for rec in records:
            c10_reg.append_lock(rec)

        effective_cb = _create_governed_hitl_callback(meta)
        coordinator = Phase2ClearanceCoordinator(
            register=c10_reg,
            hitl_callback=effective_cb,
            fail_closed_on_unhandled=meta.get("fail_closed_hitl", False),
        )
        cleared_records = coordinator.clear_queue(records)
        meta["c10_latest_hash"] = c10_reg.latest_hash
        meta["c10_has_open_locks"] = c10_reg.has_open_locks()

        updated_turns = coordinator.apply_resolutions_to_transcript(raw_turns, cleared_records)

        ecs, unres, quar = _process_cleared_records(cleared_records, meta)
        meta["entity_clarifications"] = ecs
        _merge_explicit_resolutions(meta, ecs)
        if quar:
            meta["quarantined_entities"] = quar
        if unres:
            meta["unresolved_entity_clarifications"] = unres
            logger.warning(
                "Clearance lifecycle identified %d unconfirmed entities: %s",
                len(unres), [e['token'] for e in unres],
            )

        updated_transcript_text = _reconstruct_transcript_text(updated_turns, transcript_text)

        import hashlib
        meta["transcript_sha256"] = hashlib.sha256(updated_transcript_text.encode("utf-8")).hexdigest()
        meta["raw_turns"] = updated_turns

        from dual_pipeline.presentation_linter import PresentationLinter
        violations = PresentationLinter().lint_text(updated_transcript_text)
        if violations:
            logger.warning("Clearance lifecycle detected presentation violation: %s", violations[0].message)
            meta.setdefault("presentation_lint_violations", []).extend([v.message for v in violations])

        return updated_transcript_text
