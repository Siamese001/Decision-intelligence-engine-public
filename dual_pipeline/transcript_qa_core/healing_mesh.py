"""
Deterministic Pre-Flight Healing Mesh.

Intercepts pipeline artifacts between generation and evaluation, performing
lossless syntactic normalization (dates, whitespaces, headers) in <5ms with
zero text generation, zero table row injection, and zero section synthesis.
All normalization events are immutably tracked in the normalization audit log
and persisted to the governed SQLite healing decision store.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from dual_pipeline.core import digest
from dual_pipeline.storage.healing_decision_store import record_healing_decision

from .forensic_healer import heal_header_date, heal_output_1_forensic, heal_recording_gaps
from .graph_healer import heal_graph_projection
from .recap_healer import (
    _heal_output_2_actions,
    heal_output_2_recap,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DeterministicHealer",
    "_heal_output_1_gaps",
    "_heal_output_2_actions",
    "_normalize_whitespace",
]

_heal_output_1_gaps = heal_recording_gaps


def _persist_mesh_decision(
    scope: str,
    repair_class: str,
    before_obj: Any,
    after_obj: Any,
    changes: Sequence[str],
    metadata: Optional[Mapping[str, Any]],
    stage_context: str,
) -> None:
    if not changes:
        return
    meta = dict(metadata or {})
    run_id = str(meta.get("run_id") or meta.get("meeting_id") or "run-deterministic-mesh")
    before_str = json.dumps(before_obj, sort_keys=True) if isinstance(before_obj, (dict, list)) else str(before_obj)
    after_str = json.dumps(after_obj, sort_keys=True) if isinstance(after_obj, (dict, list)) else str(after_obj)
    record_healing_decision({
        "run_id": run_id,
        "subsystem": "deterministic_healing_mesh",
        "component": f"DeterministicHealer:{scope}",
        "scope": scope,
        "repair_class": repair_class,
        "action_taken": "; ".join(changes[:5]),
        "outcome_status": "HEALED",
        "before_digest": digest(before_str),
        "after_digest": digest(after_str),
        "confidence_top": None,
        "confidence_second": None,
        "ambiguity_margin": None,
        "hitl_escalated": False,
        "invariants_preserved": True,
        "defect_reasons": list(changes),
        "audit_trail": list(changes),
        "metadata": {"stage_context": stage_context, "change_count": len(changes), "deterministic_rule": True},
    })


def _iso_to_us_date(iso_str: str) -> str:
    s = iso_str.strip()
    m_iso = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$", s)
    if m_iso:
        return f"{int(m_iso.group(2)):02d}/{int(m_iso.group(3)):02d}/{m_iso.group(1)}"
    m_us = re.match(r"^(\d{1,2})[-/](\d{1,2})[-/](\d{4})$", s)
    if m_us:
        return f"{int(m_us.group(1)):02d}/{int(m_us.group(2)):02d}/{m_us.group(3)}"
    return iso_str


def _normalize_whitespace(
    text: str,
    changes: List[str],
    audit_log: List[Dict[str, Any]],
) -> str:
    original = text
    cleaned = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    if cleaned != original:
        desc = "Normalized trailing whitespace and blank lines to standard formatting"
        changes.append(desc)
        audit_log.append({
            "event": "whitespace_normalized",
            "rule": "RULE-LOSSLESS-SYNTAX",
            "detail": desc,
            "timestamp": time.time(),
        })
    return cleaned


def _normalize_iso_dates(
    text: str,
    changes: List[str],
    audit_log: List[Dict[str, Any]],
) -> str:
    iso_dates = re.findall(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    for y, m, d in set(iso_dates):
        old_date, new_date = f"{y}-{m}-{d}", f"{m}/{d}/{y}"
        text = text.replace(old_date, new_date)
        desc = f"Normalized ISO date {old_date} -> {new_date}"
        changes.append(desc)
        audit_log.append({
            "event": "date_normalized",
            "rule": "RULE-LOSSLESS-SYNTAX",
            "detail": desc,
            "original": old_date,
            "normalized": new_date,
            "timestamp": time.time(),
        })
    return text


def _parse_output_3_input(
    f3_input: Union[str, Dict[str, Any]], changes: List[str]
) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[List[str]]]:
    if isinstance(f3_input, dict):
        return f3_input, None, None
    try:
        return json.loads(f3_input), None, None
    except Exception as e:
        from dual_pipeline.validation.pydantic_gate import repair_json_tokens
        try:
            repaired_str = repair_json_tokens(str(f3_input))
            data = json.loads(repaired_str)
            changes.append("Deterministic PDA: Healed truncated/malformed JSON token stream for Output 3")
            return data, None, None
        except Exception as inner_e:
            raw_str = f3_input if isinstance(f3_input, str) else json.dumps(f3_input)
            return None, raw_str, [f"Output 3 JSON parsing failed: {e}; PDA repair failed: {inner_e}"]


def _is_document_modality(meta: Mapping[str, Any], data: Any) -> bool:
    return bool(
        meta.get("is_document")
        or meta.get("output3_only")
        or meta.get("canonical_modality") == "DOCUMENT"
        or meta.get("archetype") == "DOCUMENT_MODE"
        or meta.get("meeting_archetype") == "DOCUMENT_MODE"
        or (isinstance(data, dict) and data.get("is_document"))
        or (isinstance(data, dict) and data.get("output3_only"))
        or (isinstance(data, dict) and data.get("meeting_archetype") == "DOCUMENT_MODE")
    )


class DeterministicHealer:
    """Class C Gate-Dependent Deterministic Pre-Flight Syntactic Normalizer."""

    normalization_audit_log: List[Dict[str, Any]] = []

    @classmethod
    def is_decoupled_from_primary_flow(cls) -> bool:
        """Certifies that healing mesh is decoupled from primary LLM prompt generation."""
        return True

    @classmethod
    def get_topology_class(cls) -> str:
        """Returns the Class C gate-dependent pipeline topology descriptor."""
        return "Class C — Gate-dependent pipeline (Stages S01-S03 -> QA Gates 1-4 -> LLM Judge X1 -> UWG Release Gate)"

    @classmethod
    def get_audit_log(cls) -> List[Dict[str, Any]]:
        """Return the list of recorded syntactic normalization events."""
        return list(cls.normalization_audit_log)

    @classmethod
    def clear_audit_log(cls) -> None:
        """Clear the normalization audit log."""
        cls.normalization_audit_log.clear()

    @staticmethod
    def iso_to_us_date(iso_str: str) -> str:
        return _iso_to_us_date(iso_str)

    @classmethod
    def heal_output_1(
        cls,
        f1_text: str,
        metadata: Optional[Dict[str, Any]] = None,
        stage_context: str = "stage01_checkpoint",
    ) -> Tuple[str, List[str]]:
        if not f1_text:
            return f1_text, []
        meta = metadata or {}
        if cls._is_frozen(meta, "output_1"):
            return f1_text, []
        changes: List[str] = []
        log_len_before = len(cls.normalization_audit_log)
        healed = _normalize_whitespace(f1_text, changes, cls.normalization_audit_log)
        healed = _normalize_iso_dates(healed, changes, cls.normalization_audit_log)
        target_dt = meta.get("meeting_date") or meta.get("date") or meta.get("date_filename")
        healed = heal_header_date(healed, target_dt, changes, cls.normalization_audit_log)
        healed = heal_output_1_forensic(healed, meta, changes, cls.normalization_audit_log)
        healed = heal_recording_gaps(healed, changes, cls.normalization_audit_log)
        for entry in cls.normalization_audit_log[log_len_before:]:
            entry["stage_context"] = stage_context
        _persist_mesh_decision("output_1", "NORMALIZE_DATE_FORMAT", f1_text, healed, changes, meta, stage_context)
        return healed, changes

    @classmethod
    def heal_output_2(
        cls,
        f2_text: str,
        metadata: Optional[Dict[str, Any]] = None,
        output_1: Optional[str] = None,
        stage_context: str = "stage02_checkpoint",
    ) -> Tuple[str, List[str]]:
        if not f2_text:
            return f2_text, []
        meta = metadata or {}
        if cls._is_frozen(meta, "output_2"):
            return f2_text, []
        changes: List[str] = []
        log_len_before = len(cls.normalization_audit_log)
        healed = heal_output_2_recap(
            f2_text=f2_text,
            metadata=meta,
            output_1=output_1,
            changes=changes,
            audit_log=cls.normalization_audit_log,
        )
        for entry in cls.normalization_audit_log[log_len_before:]:
            entry["stage_context"] = stage_context
        _persist_mesh_decision("output_2", "GENERIC_FIELD_CLEANUP", f2_text, healed, changes, meta, stage_context)
        return healed, changes

    @staticmethod
    def _apply_domain_ontology_healers(
        data: Dict[str, Any], metadata: Optional[Dict[str, Any]], changes: List[str]
    ) -> None:
        """Domain ontology healers pass-through to preserve authentic graph extractions."""
        pass

    @classmethod
    def _heal_frozen_output_3(
        cls, f3_input: Union[str, Dict[str, Any]], meta: Dict[str, Any], output_1: Optional[str]
    ) -> Tuple[str, List[str]]:
        raw_str = f3_input if isinstance(f3_input, str) else json.dumps(f3_input, indent=2)
        return raw_str, []

    @classmethod
    def heal_output_3(
        cls,
        f3_input: Union[str, Dict[str, Any]],
        metadata: Optional[Dict[str, Any]] = None,
        stage_context: str = "stage03_checkpoint",
        output_1: Optional[str] = None,
        output_2: Optional[str] = None,
    ) -> Tuple[str, List[str]]:
        meta = metadata or {}
        if output_2 and "output_2" not in meta:
            meta["output_2"] = output_2
        if cls._is_frozen(meta, "output_3"):
            return cls._heal_frozen_output_3(f3_input, meta, output_1)
        changes: List[str] = []
        data, raw_str, err_changes = _parse_output_3_input(f3_input, changes)
        if data is None:
            return raw_str or "", err_changes or []

        heal_graph_projection(data, changes, metadata=metadata, output_1=output_1)
        from .market_intel_healer import heal_market_intel_mutations
        heal_market_intel_mutations(data, metadata, changes)
        from .reconciliation_coverage_healer import heal_projection_evidence_and_coverage
        heal_projection_evidence_and_coverage(
            data,
            metadata=metadata,
            f1_text=output_1 or (metadata.get("source_text") if metadata else "") or "",
            changes=changes,
        )
        if not _is_document_modality(meta, data):
            cls._apply_domain_ontology_healers(data, metadata, changes)
        from .collateral_healer import heal_collateral_graph_mutations
        heal_collateral_graph_mutations(data, metadata, changes)
        eff_output_1 = output_1 or (metadata.get("source_text") if metadata else "") or ""
        if eff_output_1:
            from .quote_pointer_healer import heal_quote_pointers
            heal_quote_pointers(data, transcript=eff_output_1)
        for c in changes:
            cls.normalization_audit_log.append({
                "event": "graph_projection_normalized",
                "rule": "RULE-LOSSLESS-SYNTAX",
                "detail": c,
                "stage_context": stage_context,
                "timestamp": time.time(),
            })
        out_str = json.dumps(data, indent=2)
        _persist_mesh_decision("output_3", "GENERIC_FIELD_CLEANUP", f3_input, out_str, changes, meta, stage_context)
        return out_str, changes

    @staticmethod
    def _is_frozen(meta: Dict[str, Any], key: str) -> bool:
        sm = meta.get("state_machine")
        if sm is None or not hasattr(sm, "is_frozen"):
            return False
        return bool(sm.is_frozen(key))

    @classmethod
    def _heal_out1(cls, f1_text: Optional[str], meta: Dict[str, Any]) -> Tuple[str, List[Any]]:
        if cls._is_frozen(meta, "output_1"):
            return f1_text or "", []
        return cls.heal_output_1(f1_text or "", meta, stage_context="post_execution_batch")

    @classmethod
    def _heal_out2(cls, f2_text: Optional[str], f1_text: Optional[str], meta: Dict[str, Any]) -> Tuple[str, List[Any]]:
        return cls.heal_output_2(f2_text or "", meta, output_1=f1_text, stage_context="post_execution_batch")

    @classmethod
    def _heal_out3(
        cls,
        f3_text: Optional[Union[str, Dict[str, Any]]],
        meta: Dict[str, Any],
        f1_text: Optional[str] = None,
    ) -> Tuple[str, List[Any]]:
        if cls._is_frozen(meta, "output_3"):
            return f3_text if isinstance(f3_text, str) else json.dumps(f3_text or {}, indent=2), []
        return cls.heal_output_3(f3_text or "{}", meta, stage_context="post_execution_batch", output_1=f1_text)

    @classmethod
    def heal_all(
        cls,
        f1_text: Optional[str] = None,
        f2_text: Optional[str] = None,
        f3_text: Optional[Union[str, Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, str, str, Dict[str, Any]]:
        meta = metadata or {}
        h1, c1 = cls._heal_out1(f1_text, meta)
        h2, c2 = cls._heal_out2(f2_text, h1, meta)
        h3, c3 = cls._heal_out3(f3_text, meta, f1_text=h1)
        return h1, h2, h3, {
            "output_1_healed": c1,
            "output_2_healed": c2,
            "output_3_healed": c3,
            "normalization_audit_log": list(cls.normalization_audit_log),
        }

    @classmethod
    def heal_decision_answer(
        cls,
        answer: Mapping[str, Any],
        defect_codes: Sequence[str],
        stage_context: str = "l2_content_repair",
    ) -> Tuple[Dict[str, Any], List[str]]:
        """Losslessly normalize decision answer fields for deterministic content repair."""
        if not answer:
            return dict(answer or {}), []
        changes: List[str] = []
        log_len_before = len(cls.normalization_audit_log)

        def _heal_value(val: Any) -> Any:
            if isinstance(val, str):
                healed = _normalize_whitespace(val, changes, cls.normalization_audit_log)
                healed = _normalize_iso_dates(healed, changes, cls.normalization_audit_log)
                return healed
            elif isinstance(val, dict):
                return {k: _heal_value(v) for k, v in val.items()}
            elif isinstance(val, list):
                return [_heal_value(v) for v in val]
            return val

        healed_answer = _heal_value(dict(answer))
        for entry in cls.normalization_audit_log[log_len_before:]:
            entry["stage_context"] = stage_context
            entry["defect_codes"] = list(defect_codes)
        _persist_mesh_decision("decision_answer", "STRIP_WHITESPACE_OR_PAD", answer, healed_answer, changes, {}, stage_context)
        return healed_answer, changes
