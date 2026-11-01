"""Deterministic Output 02 Action Ledger to Output 03 / Intake Package Porting & Evidence Binding."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Optional, Tuple

from dual_pipeline.transcript_qa_core.recap_action_handoff_healer import (
    _LOC_SPAN_RE,
    _ts_to_seconds,
    parse_transcript_turns,
    reconcile_action_ledger_handoff_owners,
)


_LEDGER_SEC_RE = re.compile(
    r"^#+\s*(?:Action\s*(?:&|and)\s*Governance\s*Ledger|Action\s*Items|Next\s*Steps|Follow[- ]?Up\s*Actions)\b(.*?)(?=^#+\s|\Z)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
_CELL_CLEAN_RE = re.compile(r"\*\*|`")
_SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")


def _slugify(text: str) -> str:
    cleaned = _SLUG_RE.sub("-", (text or "").strip().lower()).strip("-")
    return cleaned or "item"


def _extract_ledger_section(recap_text: str) -> str:
    if not recap_text or "|" not in recap_text:
        return ""
    sec_match = _LEDGER_SEC_RE.search(recap_text)
    return sec_match.group(1) if sec_match else recap_text


def _find_col_index(header_cols: List[str], keywords: Tuple[str, ...], default: int = -1) -> int:
    for idx, col in enumerate(header_cols):
        if any(k in col for k in keywords):
            return idx
    return default


def _clean_cell(cells: List[str], idx: int, default: str = "") -> str:
    if 0 <= idx < len(cells):
        return _CELL_CLEAN_RE.sub("", cells[idx]).strip()
    return default


def _parse_single_table_row(cells: List[str], col_map: Dict[str, int]) -> Optional[Dict[str, str]]:
    act_desc = _clean_cell(cells, col_map["act"])
    if not act_desc or act_desc.lower() in ("none", "n/a", "---", "no action items"):
        return None
    owner = _clean_cell(cells, col_map["owner"], "Unassigned")
    if ":" in owner:
        owner = owner.split(":", 1)[1].replace("-", " ").title()
    return {
        "action": act_desc,
        "owner": owner or "Unassigned",
        "due_raw": _clean_cell(cells, col_map["due"]),
        "locator_raw": _clean_cell(cells, col_map["loc"]),
        "status_raw": _clean_cell(cells, col_map["status"], "planned"),
    }


def _parse_ledger_table_rows(recap_text: str) -> List[Dict[str, str]]:
    block = _extract_ledger_section(recap_text)
    header_cols: List[str] = []
    col_map: Dict[str, int] = {}
    rows: List[Dict[str, str]] = []
    for line in block.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if not cells or all(set(c) <= {"-", ":", " "} for c in cells):
            continue
        if not header_cols:
            lower_cells = [c.lower() for c in cells]
            if any(k in " ".join(lower_cells) for k in ("action", "owner", "dri", "deliverable", "task", "due")):
                header_cols = lower_cells
                col_map = {
                    "act": _find_col_index(header_cols, ("action", "task", "deliverable", "item", "description"), 0),
                    "owner": _find_col_index(header_cols, ("owner", "dri", "assignee", "lead"), -1),
                    "due": _find_col_index(header_cols, ("due", "target", "date", "timeline", "timing"), -1),
                    "loc": _find_col_index(header_cols, ("locator", "evidence", "citation", "source"), -1),
                    "status": _find_col_index(header_cols, ("status", "state"), -1),
                }
            continue
        parsed = _parse_single_table_row(cells, col_map)
        if parsed:
            rows.append(parsed)
    return rows


def _match_owner_turn(speaker: str, owner: str) -> bool:
    spk_low = (speaker or "").strip().lower()
    own_low = (owner or "").strip().lower()
    if not spk_low or not own_low:
        return False
    if spk_low == own_low or own_low in spk_low or spk_low in own_low:
        return True
    own_parts = [p for p in re.split(r"[^a-z]+", own_low) if len(p) >= 3]
    spk_parts = [p for p in re.split(r"[^a-z]+", spk_low) if len(p) >= 3]
    return bool(own_parts and spk_parts and own_parts[0] == spk_parts[0])


def resolve_action_turns_in_f1(
    locator_raw: str,
    owner: str,
    f1_text: str,
    pre_parsed_turns: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    """Resolve the best timestamp and verbatim Output 01 turns for an Action Ledger locator."""
    m_loc = _LOC_SPAN_RE.search(locator_raw or "")
    if not m_loc:
        return None, []
    start_ts = m_loc.group(1)
    end_ts = m_loc.group(2) or start_ts
    turns = pre_parsed_turns if pre_parsed_turns is not None else parse_transcript_turns(f1_text or "")
    if not turns:
        return start_ts, []
    start_sec, end_sec = _ts_to_seconds(start_ts), _ts_to_seconds(end_ts)
    window_turns = [t for t in turns if start_sec <= int(t["seconds"]) <= end_sec]
    if not window_turns:
        window_turns = [min(turns, key=lambda t: abs(int(t["seconds"]) - start_sec))]
    owner_turns = [t for t in window_turns if _match_owner_turn(str(t["speaker"]), owner)]
    primary_turn = owner_turns[0] if owner_turns else window_turns[0]
    selected_turns = list(owner_turns[:2] if owner_turns else window_turns[:2])
    for wt in window_turns:
        if wt not in selected_turns and len(selected_turns) < 3:
            selected_turns.append(wt)
    return str(primary_turn["timestamp"]), selected_turns


def _build_turn_evidence_entry(
    act_idx: int, turn_idx: int, turn: Dict[str, Any], f1_text: str, f1_sha: str
) -> Tuple[str, Dict[str, Any]]:
    ts = str(turn["timestamp"])
    speaker = str(turn["speaker"])
    utterance = str(turn["text"])
    ev_id = f"ev-action-{act_idx:02d}-turn-{turn_idx:02d}"
    pos = (f1_text or "").find(utterance)
    entry: Dict[str, Any] = {
        "evidence_id": ev_id,
        "statement_id": ev_id,
        "speaker": speaker,
        "source_classification": "COMMITMENT",
        "kind": "strategic",
        "text": utterance,
        "exact_text": utterance,
        "locator": {"kind": "timestamp", "value": ts},
        "grounding_status": "GROUNDED",
        "attribution_mode": "DIRECT",
    }
    if pos != -1:
        entry["quote_pointer"] = [pos, pos + len(utterance)]
        entry["start_offset"] = pos
        entry["end_offset"] = pos + len(utterance)
    if f1_sha:
        entry["artifact_sha256"] = f1_sha
    return ev_id, entry


def extract_reconciled_output02_actions(
    f2_text: str, f1_text: str = ""
) -> Tuple[str, List[Dict[str, Any]]]:
    """Reconcile Output 02 multi-turn handoffs against Output 01 and return structured actions with turn bindings."""
    if not f2_text or "|" not in f2_text:
        return f2_text, []
    healed_f2 = reconcile_action_ledger_handoff_owners(f2_text, output_1=f1_text) if f1_text else f2_text
    raw_rows = _parse_ledger_table_rows(healed_f2)
    if not raw_rows:
        return healed_f2, []
    f1_sha = hashlib.sha256(f1_text.encode("utf-8")).hexdigest() if f1_text else ""
    turns = parse_transcript_turns(f1_text) if f1_text else []
    actions: List[Dict[str, Any]] = []
    for idx, row in enumerate(raw_rows, 1):
        owner = row["owner"]
        resolved_ts, matched_turns = resolve_action_turns_in_f1(
            row["locator_raw"], owner, f1_text, pre_parsed_turns=turns
        )
        ev_entries = [
            _build_turn_evidence_entry(idx, t_idx, turn, f1_text, f1_sha)
            for t_idx, turn in enumerate(matched_turns, 1)
        ]
        actions.append(
            {
                "action_id": f"action-out02-{idx:02d}",
                "action": row["action"],
                "reported_owner": owner,
                "due_raw": row["due_raw"],
                "status_raw": row["status_raw"],
                "locator_raw": row["locator_raw"],
                "resolved_timestamp": resolved_ts,
                "evidence_entries": ev_entries,
                "evidence_ids": [eid for eid, _ in ev_entries],
            }
        )
    return healed_f2, actions


def _upsert_action_owner_entity(
    owner_name: str,
    eids: List[str],
    entities: List[Dict[str, Any]],
    existing_person_ids: set[str],
) -> Optional[str]:
    if not owner_name or owner_name.lower() in ("unassigned", "tbd", "none"):
        return None
    owner_slug = "person:" + _slugify(owner_name)
    if owner_slug not in existing_person_ids:
        entities.append(
            {
                "id": owner_slug,
                "type": "person",
                "name": owner_name,
                "description": f"Accountable action owner ({owner_name}).",
                "mutation": "CONFIRM",
                "evidence_ids": eids,
            }
        )
        existing_person_ids.add(owner_slug)
    return owner_slug


def port_output02_actions_to_output03(
    f2_text: str,
    f3_dict: Dict[str, Any],
    f1_text: str = "",
    *,
    preserve_action_updates: bool = False,
) -> Dict[str, Any]:
    """Port reconciled Output 02 Action Ledger items and verbatim Output 01 turn evidence into Output 03."""
    if not isinstance(f3_dict, dict) or not f2_text:
        return f3_dict
    _, reconciled_actions = extract_reconciled_output02_actions(f2_text, f1_text)
    if not reconciled_actions:
        return f3_dict

    ev_index = f3_dict.setdefault("evidence_index", {})
    mutations = f3_dict.setdefault("graph_mutations", {})
    entities = mutations.setdefault("entity_upserts", [])
    existing_person_ids = {
        str(e.get("id", "")).lower() for e in entities if isinstance(e, dict)
    }
    action_updates: List[Dict[str, Any]] = []

    for item in reconciled_actions:
        for ev_id, ev_obj in item["evidence_entries"]:
            ev_index.setdefault(ev_id, ev_obj)
        owner_name = str(item["reported_owner"] or "").strip()
        fallback_eids = item["evidence_ids"] or list(ev_index.keys())[:1]
        owner_slug = _upsert_action_owner_entity(owner_name, fallback_eids, entities, existing_person_ids)
        if preserve_action_updates and owner_slug:
            action_updates.append(
                {
                    "action_id": item["action_id"],
                    "action": item["action"],
                    "description": item["action"],
                    "owner_id": owner_slug,
                    "owner": owner_name,
                    "status": "OPEN",
                    "target_timing": item["due_raw"] or None,
                    "evidence_ids": list(item["evidence_ids"]),
                }
            )

    if preserve_action_updates:
        mutations["action_updates"] = action_updates
    return f3_dict
