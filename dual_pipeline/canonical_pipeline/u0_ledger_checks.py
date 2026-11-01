"""
dual_pipeline/canonical_pipeline/u0_ledger_checks.py

Acoustic unknown utterance verification and ledger role resolution loading.
"""
from __future__ import annotations

import contextlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Set

from .u0_preflight_prompts import build_preflight_speaker_prompts
from .u0_preflight_types import PreflightDiagnostic

logger = logging.getLogger("dual_pipeline.canonical_pipeline.u0_ledger_checks")


def _check_acoustic_unknown_utterances(
    metadata: Dict[str, Any], missing: List[str], diagnostics: List[PreflightDiagnostic]
) -> None:
    """Verifies that acoustic 'Unknown' utterances are mapped or backed by Ed25519 signed HITL receipts."""
    raw_utterances = metadata.get("structured_utterances")
    if not raw_utterances and metadata.get("structured_utterances_path"):
        u_p = Path(metadata["structured_utterances_path"])
        if u_p.is_file():
            with contextlib.suppress(Exception):
                raw_utterances = json.loads(u_p.read_text(encoding="utf-8"))

    if not raw_utterances or not isinstance(raw_utterances, list):
        return

    hints = metadata.get("speaker_hints") or []
    if hints and raw_utterances:
        from .u0_hint_reconciler import reconcile_acoustic_unknown_utterances_with_hints

        reconcile_acoustic_unknown_utterances_with_hints(raw_utterances, hints)

    unknown_indices = [
        i
        for i, u in enumerate(raw_utterances)
        if str(u.get("speaker") or u.get("raw_speaker") or "").strip().lower() in ("unknown", "speaker unknown")
    ]
    if not unknown_indices:
        return

    spk_map = metadata.get("speaker_map") or {}
    norm_map = {str(k).lower(): v for k, v in spk_map.items()}
    if "unknown" in norm_map:
        return

    from dual_pipeline.hitl_governance_core.crypto import find_signed_approvals

    approvals = find_signed_approvals()
    approved_turn_indices = set()
    expected_subj = metadata.get("source_file_hash") or metadata.get("input_hash") or ""
    clean_subj = (
        str(expected_subj).replace("urn:nee:source-file:sha256:", "").replace("urn:nee:unanchored-text:sha256:", "")
    )

    for app in approvals:
        app_subj = app.get("payload", {}).get("subject_sha256") or app.get("subject_sha256") or ""
        clean_app_subj = (
            str(app_subj).replace("urn:nee:source-file:sha256:", "").replace("urn:nee:unanchored-text:sha256:", "")
        )
        if clean_subj and clean_app_subj and (clean_subj in clean_app_subj or clean_app_subj in clean_subj):
            case_ids = app.get("payload", {}).get("case_ids") or []
            for cid in case_ids:
                if str(cid).isdigit():
                    approved_turn_indices.add(int(cid))
                elif "turn_" in str(cid):
                    with contextlib.suppress(ValueError):
                        approved_turn_indices.add(int(str(cid).split("turn_")[-1]))
                elif cid == "ALL_UNKNOWN_TURNS":
                    approved_turn_indices.update(unknown_indices)

    unresolved = [idx for idx in unknown_indices if idx not in approved_turn_indices]
    if unresolved:
        if "speaker_map" not in missing:
            missing.append("speaker_map")
        source_id = str(
            metadata.get("meeting_name")
            or metadata.get("title")
            or metadata.get("container_slug")
            or "meeting_source"
        )
        prompts = build_preflight_speaker_prompts(raw_utterances, unresolved, source_id)
        if prompts:
            metadata["preflight_speaker_review_prompts"] = prompts
        remediation = "Resolve Unknown turns via interactive HITL attribution with 2-turn context window."
        if prompts:
            remediation += (
                f" Pre-rendered prompt available for turn {unresolved[0]} in "
                f"metadata['preflight_speaker_review_prompts']."
            )
        diagnostics.append(
            PreflightDiagnostic(
                check_name="U0_UNMAPPED_SPEAKERS",
                severity="BLOCKING",
                field_name="speaker_map",
                message=(
                    f"Acoustic utterances contain {len(unresolved)} unresolved 'Unknown' turn(s) "
                    f"(turn indices: {unresolved[:5]}) without bound Ed25519 receipts in hitl_decision_ledger.json."
                ),
                remediation=remediation,
            )
        )


def _record_ledger_candidate(
    name: str,
    raw_role: str,
    recorded_at: str,
    seq: int,
    target_names: Set[str],
    latest_order: Dict[str, Tuple[str, int]],
    resolved: Dict[str, str],
    speaker_resolutions: Dict[str, Any],
    org: Optional[str] = None,
) -> None:
    from dual_pipeline.hitl_governance_core.speaker_resolution_contract import strip_org_suffix_from_role

    if not name or name not in target_names or not raw_role:
        return
    order_key = (str(recorded_at or ""), int(seq or 0))
    if name not in latest_order or order_key >= latest_order[name]:
        clean_role, found_org = strip_org_suffix_from_role(raw_role, org)
        latest_order[name] = order_key
        resolved[name] = clean_role
        speaker_resolutions[name] = {
            "role": clean_role,
            "organization": found_org or org,
            "provenance": "HITL_LEDGER",
            "is_human_confirmed": True,
            "recorded_at": recorded_at,
        }


def _load_ledger_role_resolutions(metadata: Dict[str, Any]) -> Dict[str, str]:
    from storage.connection import create_sqlite_conn

    resolved = metadata.setdefault("hitl_role_resolutions", {})
    speaker_resolutions = metadata.setdefault("speaker_role_resolutions", {})
    latest_order: Dict[str, Tuple[str, int]] = {}
    repo_root = Path(metadata.get("repo_root") or ".")
    file_root = Path(__file__).resolve().parents[2]
    paths = [
        b / rel
        for b in (repo_root, file_root, file_root.parent.parent)
        for rel in ("hitl_decision_ledger.sqlite", ".local/ledger/hitl_decision_ledger.sqlite")
    ]
    target_names: set[str] = set()
    roster = metadata.get("roster") or metadata.get("attendees") or metadata.get("pre_enrichment_roles")
    if isinstance(roster, dict):
        for k, v in roster.items():
            if isinstance(v, list):
                for item in v:
                    if isinstance(item, str):
                        target_names.add(item.split("(")[0].strip())
                    elif isinstance(item, dict) and item.get("name"):
                        target_names.add(item["name"].strip())
            else:
                target_names.add(k.strip())
    elif isinstance(roster, list):
        for item in roster:
            if isinstance(item, dict) and item.get("name"):
                target_names.add(item["name"].strip())
            elif isinstance(item, str):
                target_names.add(item.split("(")[0].strip())
    elif isinstance(roster, str):
        for part in roster.split(","):
            target_names.add(part.split("(")[0].strip())

    target_names = {t for t in target_names if t}
    if not target_names:
        return resolved

    for p in paths:
        if p.is_file():
            try:
                conn = create_sqlite_conn(p, readonly=True)
                cur = conn.cursor()
                query = (
                    "SELECT prompt, selected_key, recorded_at, sequence_number, metadata "
                    "FROM hitl_decisions WHERE prompt LIKE ? OR category IN ('role_clarification', 'ATTENDEE_ROLE') "
                    "ORDER BY recorded_at ASC, sequence_number ASC, ROWID ASC"
                )
                cur.execute(query, ("Assign verified professional title to %",))
                for row in cur.fetchall():
                    prompt = row["prompt"] if hasattr(row, "keys") else row[0]
                    role = row["selected_key"] if hasattr(row, "keys") else row[1]
                    rec_at = row["recorded_at"] if hasattr(row, "keys") else (row[2] if len(row) > 2 else "")
                    seq = row["sequence_number"] if hasattr(row, "keys") else (row[3] if len(row) > 3 else 0)
                    meta_raw = row["metadata"] if hasattr(row, "keys") else (row[4] if len(row) > 4 else None)
                    p_name = ""
                    p_org = None
                    if meta_raw:
                        try:
                            m_dict = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
                            if isinstance(m_dict, dict):
                                p_name = m_dict.get("person_name") or ""
                                role = m_dict.get("resolved_role") or role
                                p_org = m_dict.get("organization")
                        except Exception:
                            pass
                    if not p_name and prompt:
                        p_name = str(prompt).replace("Assign verified professional title to ", "").strip()
                    _record_ledger_candidate(
                        p_name,
                        str(role or "").strip(),
                        str(rec_at or ""),
                        int(seq or 0),
                        target_names,
                        latest_order,
                        resolved,
                        speaker_resolutions,
                        org=p_org,
                    )
                conn.close()
            except Exception:
                pass

    json_paths = [
        repo_root / "hitl_decision_ledger.json",
        repo_root / ".local" / "ledger" / "hitl_decision_ledger.json",
        file_root / "hitl_decision_ledger.json",
    ]
    for jp in json_paths:
        if jp.is_file():
            try:
                j_data = json.loads(jp.read_text(encoding="utf-8"))
                entries = j_data if isinstance(j_data, list) else (j_data.get("entries") or j_data.get("decisions") or [])
                for entry in entries:
                    rec_at = entry.get("recorded_at") or entry.get("timestamp") or ""
                    seq = entry.get("sequence_number") or 0
                    payload = entry.get("payload", {})
                    p_meta = payload.get("metadata", {}) or entry.get("metadata", {})
                    p_name = p_meta.get("person_name") or entry.get("person_name") or ""
                    r_role = p_meta.get("resolved_role") or entry.get("selected_key")
                    p_org = p_meta.get("organization")
                    if p_name and r_role:
                        _record_ledger_candidate(
                            p_name,
                            str(r_role).strip(),
                            str(rec_at),
                            int(seq),
                            target_names,
                            latest_order,
                            resolved,
                            speaker_resolutions,
                            org=p_org,
                        )
                    roles_cand = p_meta.get("speaker_role_resolutions") or p_meta.get("role_resolutions") or {}
                    for nm, r_info in roles_cand.items():
                        r_str = r_info.get("role") if isinstance(r_info, dict) else str(r_info)
                        o_str = r_info.get("organization") if isinstance(r_info, dict) else None
                        _record_ledger_candidate(
                            nm,
                            str(r_str).strip(),
                            str(rec_at),
                            int(seq),
                            target_names,
                            latest_order,
                            resolved,
                            speaker_resolutions,
                            org=o_str,
                        )
            except Exception:
                pass
    return resolved


__all__ = [
    "_check_acoustic_unknown_utterances",
    "_load_ledger_role_resolutions",
]
