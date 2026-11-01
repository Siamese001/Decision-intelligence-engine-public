"""
Deterministic Cross-Artifact Consistency QA Gates.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _parse_raw_roster_string(raw: str) -> List[Any]:
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else [raw]
    except Exception:
        parts = re.split(r",\s*(?![^()]*\))", raw)
        return [p.strip() for p in parts if p.strip()]


def _extract_roster_names(meta: Dict[str, Any]) -> List[str]:
    raw_roster = meta.get("roster", []) or meta.get("attendees", [])
    if isinstance(raw_roster, str):
        raw_roster = _parse_raw_roster_string(raw_roster)

    names: List[str] = []
    for item in raw_roster:
        if isinstance(item, dict) and item.get("name"):
            names.append(item["name"])
        elif isinstance(item, str):
            clean_name = item.split("(")[0].strip()
            if clean_name:
                names.append(clean_name)
    return names


NAME_ALIASES: Dict[str, List[str]] = {
    "Jennifer Pan": ["Jennifer Pan", "Jen Pan", "Jen", "Jenn"],
    "harper Hoffman": ["harper Hoffman", "Mandy H", "Mandy"],
    "Amit Ayer": ["Amit Ayer", "Amit"],
    "Jake Stark": ["Jake Stark", "Jake"],
    "Miles Erickson": ["Miles Erickson", "Miles"],
    "Anthony PartnerAdvisory": ["Anthony PartnerAdvisory", "Anthony"],
    "Eduardo More": ["Eduardo More", "Eduardo", "Ed Moore", "Ed More", "Eduardo Juan More", "Eduardo (Ed) More"],
    "Ed Moore": ["Ed Moore", "Ed More", "Eduardo More", "Eduardo Juan More", "Eduardo (Ed) More"],
    "Alexandra Coro": ["Alexandra Coro", "Alex Coro", "Alexandra Corrow", "Alex Corrow", "Alexandra (Alex) Corrow"],
    "Alexandra Corrow": ["Alexandra Corrow", "Alexandra Coro", "Alex Corrow", "Alex Coro", "Alexandra (Alex) Corrow"],
}

_NICKNAME_STEMS: Dict[str, List[str]] = {
    "mitchell": ["mitch"], "jennifer": ["jen", "jenn"],
    "stephan": ["stephane", "steph"], "stephane": ["stephan", "steph"],
    "alexander": ["alex"], "anthony": ["tony"], "matthew": ["matt"],
    "william": ["bill", "billy", "will"], "robert": ["bob", "bobby", "rob"],
    "richard": ["rick", "rich"], "james": ["jim", "jimmy"],
    "michael": ["mike"], "daniel": ["dan", "danny"],
    "christopher": ["chris"], "nicholas": ["nick"], "edward": ["ed", "eddie"],
}


def _add_name_parts_and_nicknames(base: str, aliases: List[str]) -> None:
    parts = base.split()
    if len(parts) >= 2:
        for p in (parts[0], parts[-1], f"{parts[0]} {parts[-1][0]}.", f"{parts[0]} {parts[-1][0]}"):
            if p not in aliases:
                aliases.append(p)
    elif len(parts) == 1 and parts[0] not in aliases:
        aliases.append(parts[0])

    if parts:
        for nick in _NICKNAME_STEMS.get(parts[0].lower(), []):
            if nick.capitalize() not in aliases:
                aliases.append(nick.capitalize())


def _add_graph_aliases_map(targets: Set[str], graph_map: Any, aliases: List[str]) -> None:
    if not isinstance(graph_map, dict):
        return
    for k, g_list in graph_map.items():
        if k.lower() in targets and isinstance(g_list, list):
            for a in g_list:
                if a and a not in aliases:
                    aliases.append(a)


def _add_graph_entities_aliases(targets: Set[str], all_entities: List[Any], aliases: List[str]) -> None:
    for ent in all_entities:
        if not isinstance(ent, dict):
            continue
        c_name = ent.get("canonical_name", "")
        d_name = ent.get("display_name", "")
        if c_name.lower() in targets or d_name.lower() in targets:
            for cand in (c_name, d_name):
                if cand and cand not in aliases:
                    aliases.append(cand)


def _add_graph_metadata_aliases(clean: str, clean_no_prefix: str, meta: Dict[str, Any], aliases: List[str]) -> None:
    targets = {clean.lower(), clean_no_prefix.lower()}
    _add_graph_aliases_map(targets, meta.get("graph_aliases", {}), aliases)
    all_entities = meta.get("known_graph_entities", []) + meta.get("graph_project_resources", [])
    _add_graph_entities_aliases(targets, all_entities, aliases)


def _add_static_name_aliases(clean: str, first_name: str, aliases: List[str]) -> None:
    clean_low = clean.lower()
    first_low = first_name.lower()
    for static_k, static_list in NAME_ALIASES.items():
        if clean_low == static_k.lower() or first_low == static_k.split()[0].lower():
            for a in static_list:
                if a not in aliases:
                    aliases.append(a)


def get_dynamic_name_aliases(name: str, meta: Optional[Dict[str, Any]] = None) -> List[str]:
    """Dynamically resolve all valid aliases, nicknames, and name variations for an attendee."""
    clean = re.sub(r"\s*\(.*?\)", "", name).strip()
    aliases = [name] if name else []
    if clean and clean not in aliases:
        aliases.append(clean)

    clean_no_prefix = re.sub(r"^(?:dr\.?|prof\.?|mr\.?|ms\.?|mrs\.?)\s+", "", clean, flags=re.IGNORECASE).strip()
    if clean_no_prefix and clean_no_prefix not in aliases:
        aliases.append(clean_no_prefix)

    for base in (clean, clean_no_prefix):
        _add_name_parts_and_nicknames(base, aliases)

    if meta and isinstance(meta, dict):
        _add_graph_metadata_aliases(clean, clean_no_prefix, meta, aliases)

    first_name = clean.split()[0] if clean else ""
    _add_static_name_aliases(clean, first_name, aliases)

    return list(dict.fromkeys(a for a in aliases if len(a) >= 2))


def _check_entity_alignment(f1_text: str, f2_text: str, meta: Dict[str, Any]) -> Tuple[bool, List[str]]:
    ok = True
    failures: List[str] = []
    is_notes_mode = bool(
        meta.get("notes_mode")
        or meta.get("is_notes")
        or meta.get("source_type") in ("copilot_notes", "meeting_notes", "notes")
        or meta.get("outputs") == ["02", "03"]
    )
    for name in _extract_roster_names(meta):
        aliases = get_dynamic_name_aliases(name, meta)
        if not is_notes_mode and not any(alias in f1_text for alias in aliases):
            ok = False
            failures.append(f"Cross-Artifact: Roster participant '{name}' missing from Output 1 transcript.")
        if not any(alias in f2_text for alias in aliases):
            ok = False
            failures.append(f"Cross-Artifact: Roster participant '{name}' missing from Output 2 recap.")
    return ok, failures


def _check_temporal_consistency(f2_text: str, meta_date: str) -> Tuple[bool, List[str]]:
    ok = True
    failures: List[str] = []
    if meta_date:
        slash_date = meta_date.replace("-", "/")
        if meta_date not in f2_text and slash_date not in f2_text:
            ok = False
            failures.append(f"Cross-Artifact: Canonical date '{meta_date}' (or '{slash_date}') missing from Output 2 recap header.")
        iso_matches = re.findall(r"\b(20\d\d-\d{2}-\d{2})\b", f2_text)
        if iso_matches:
            ok = False
            failures.append(f"Cross-Artifact: Disallowed ISO dates found in Output 2: {iso_matches[:3]}")
    return ok, failures


def _check_ledger_row_locators_and_handoffs(f2_text: str, f1_text: str) -> List[str]:
    failures: List[str] = []
    for line in f2_text.splitlines():
        s = line.strip()
        if re.match(r"^\|\s*\d+\s*\|", s) and not re.search(r"\[\d{1,2}:\d{2}", s) and "attachment:" not in s.lower():
            failures.append(f"Cross-Artifact: Output 2 Action Ledger row lacks valid evidence locator: '{s}'")
    if not f1_text:
        return failures
    from .recap_action_handoff_healer import _reconcile_single_ledger_row, parse_transcript_turns

    turns = parse_transcript_turns(f1_text)
    for line in f2_text.splitlines():
        _, audit_msg = _reconcile_single_ledger_row(line, turns)
        if audit_msg:
            failures.append(f"Cross-Artifact Action Handoff Mismatch: {audit_msg}")
    return failures


def _check_action_coverage(f2_text: str, f1_text: str = "") -> Tuple[bool, List[str]]:
    if not re.search(
        r"#{1,3}\s+(?:Action\s*(?:&|and)\s*Governance\s*Ledger|Next Steps|Action Items|Follow[- ]?Up\s*Actions)",
        f2_text,
        re.IGNORECASE,
    ):
        return True, []
    failures: List[str] = []
    if re.search(r"\|\s*(?:TBD|Unassigned|None)\s*\|", f2_text, re.I):
        failures.append("Cross-Artifact: Output 2 contains unassigned/TBD action items.")
    failures.extend(_check_ledger_row_locators_and_handoffs(f2_text, f1_text))
    return len(failures) == 0, failures


def _check_career_firewall(f1_text: str, f2_text: str, f3_text: str, f3_dict: Dict[str, Any]) -> Tuple[bool, List[str]]:
    markers = ["$1.2M", "$1.2m", "INTERNAL_ADVISORY", "networking_wedge", "political_rationale"]
    f3_str = json.dumps(f3_dict) if f3_dict else str(f3_text)
    failures: List[str] = []
    for name, content in [("Output 1", f1_text), ("Output 2", f2_text), ("Output 3", f3_str)]:
        for m in markers:
            if m in content:
                failures.append(f"Cross-Artifact Rule C-19 Violation: {name} contains prohibited career leverage / internal advisory marker '{m}'.")
    return len(failures) == 0, failures


def _parse_duration_seconds(dur: Any) -> Optional[float]:
    if not dur:
        return None
    if isinstance(dur, (int, float)):
        return float(dur)
    if isinstance(dur, str):
        dur_clean = dur.strip().lower()
        if dur_clean.endswith("m"):
            try:
                return float(dur_clean[:-1]) * 60.0
            except ValueError:
                pass
        if ":" in dur_clean:
            from dual_pipeline.attachment_core.review_provenance import parse_hms_to_seconds
            return parse_hms_to_seconds(dur_clean)
    return None


def _check_temporal_plausibility(attachments: List[Any], dur_secs: Optional[float]) -> List[str]:
    if not dur_secs:
        return []
    from dual_pipeline.attachment_core.review_provenance import parse_hms_to_seconds
    fails = []
    for att in attachments:
        if not isinstance(att, dict):
            continue
        fname = att.get("filename", "")
        drop_elapsed = att.get("dropped_at_elapsed")
        if drop_elapsed:
            d_secs = parse_hms_to_seconds(str(drop_elapsed))
            if d_secs is not None and d_secs > (dur_secs + 300.0):
                fails.append(
                    f"[GATE_CROSS_5] Temporal plausibility violation: Attachment '{fname}' "
                    f"drop timestamp '{drop_elapsed}' exceeds meeting duration."
                )
    return fails


def _extract_item_locators(item: Dict[str, Any]) -> List[str]:
    locs: List[str] = []
    for loc_key in ("source_locators", "locators", "locator", "evidence_locators"):
        raw_loc = item.get(loc_key, [])
        if isinstance(raw_loc, str):
            locs.append(raw_loc)
        elif isinstance(raw_loc, dict) and raw_loc.get("value"):
            locs.append(str(raw_loc["value"]))
        elif isinstance(raw_loc, list):
            for lv in raw_loc:
                if isinstance(lv, str):
                    locs.append(lv)
                elif isinstance(lv, dict) and lv.get("value"):
                    locs.append(str(lv["value"]))
    return locs


def _extract_all_f3_items(f3_dict: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Any] = []
    for key in ("evidence_statements", "entity_upserts", "relationship_signals", "statements"):
        items.extend(f3_dict.get(key, []))
    gm = f3_dict.get("graph_mutations", {})
    if isinstance(gm, dict):
        for key in ("entity_upserts", "relationship_signals", "decision_updates", "risk_updates"):
            items.extend(gm.get(key, []))
    ev_idx = f3_dict.get("evidence_index", {})
    if isinstance(ev_idx, dict):
        items.extend(ev_idx.values())
    return [it for it in items if isinstance(it, dict)]


def _check_single_attachment_locator(
    aloc: str,
    item: Dict[str, Any],
    all_declared: Set[str],
    background_names: Set[str],
) -> List[str]:
    fails = []
    att_target = aloc.split("attachment:", 1)[1].strip().lower()
    stem_target = att_target.rsplit(".", 1)[0]
    matched_declared = any(
        att_target == decl or stem_target == decl.rsplit(".", 1)[0]
        for decl in all_declared
    )
    if not matched_declared and all_declared:
        fails.append(
            f"[GATE_CROSS_5] Source linkage failure: Output 3 cites '{aloc}' "
            f"with no linkage in declared meeting attachments."
        )
    if item.get("attribution_mode") == "COLLATERAL_REVIEWED":
        if any(att_target == bg or stem_target == bg.rsplit(".", 1)[0] for bg in background_names):
            fails.append(
                f"[GATE_CROSS_5] Unsupported review assertion: Output 3 claims "
                f"COLLATERAL_REVIEWED for background attachment '{att_target}' without in-session review."
            )
    return fails


def _check_cr_evidence_propagation(
    cr_attachments: List[Dict[str, Any]],
    f3_locator_blob: str,
) -> List[str]:
    fails = []
    for att in cr_attachments:
        fname = att.get("filename", "")
        if not fname:
            continue
        fname_lower = fname.lower()
        attachment_key = f"attachment:{fname_lower}"
        stem = fname_lower.rsplit(".", 1)[0] if "." in fname_lower else fname_lower
        if attachment_key not in f3_locator_blob and stem not in f3_locator_blob:
            fails.append(
                f"[GATE_CROSS_5] COLLATERAL_REVIEWED attachment '{fname}' has no corresponding "
                f"evidence in Output 3. Add entity_upsert or relationship_signal with "
                f"locator 'attachment:{fname}'."
            )
    return fails


def _check_collateral_evidence_propagation(
    f3_dict: Dict[str, Any], meta: Dict[str, Any]
) -> Tuple[bool, List[str]]:
    """Gate Cross-5: Collateral evidence propagation, source linkage, and temporal plausibility."""
    attachments = meta.get("attachments", [])
    cr_attachments = [
        a for a in attachments
        if isinstance(a, dict) and a.get("attribution_mode") == "COLLATERAL_REVIEWED"
    ]

    for d_key in ("output_dir", "target_output_dir", "deliverable_dir"):
        d_val = meta.get(d_key)
        if d_val and Path(d_val).is_dir():
            drop_log_path = Path(d_val) / "collateral_drop_log.json"
            if drop_log_path.is_file():
                try:
                    entries = json.loads(drop_log_path.read_text(encoding="utf-8"))
                    if isinstance(entries, list):
                        for e in entries:
                            if isinstance(e, dict):
                                fn = e.get("filename") or e.get("file_name") or ""
                                mode = str(e.get("attribution_mode") or "").upper()
                                is_rev = e.get("reviewed") is not False and mode != "BACKGROUND"
                                if fn and is_rev and not any(a.get("filename") == fn for a in cr_attachments):
                                    cr_attachments.append({"filename": fn, "attribution_mode": "COLLATERAL_REVIEWED"})
                    elif isinstance(entries, dict):
                        for fn, info in entries.items():
                            if isinstance(info, dict) and info.get("attribution_mode") != "BACKGROUND":
                                if not any(a.get("filename") == fn for a in cr_attachments):
                                    cr_attachments.append({"filename": fn, "attribution_mode": "COLLATERAL_REVIEWED"})
                except Exception:
                    pass

    background_attachments = {
        a["filename"].lower()
        for a in attachments
        if isinstance(a, dict) and a.get("attribution_mode") == "BACKGROUND" and a.get("filename")
    }
    all_declared = {
        a["filename"].lower()
        for a in attachments
        if isinstance(a, dict) and a.get("filename")
    }

    dur_secs = _parse_duration_seconds(meta.get("duration"))
    failures: List[str] = _check_temporal_plausibility(attachments, dur_secs)

    items = _extract_all_f3_items(f3_dict)
    all_locators: List[str] = []
    for item in items:
        locs = _extract_item_locators(item)
        all_locators.extend(locs)
        for aloc in [l for l in locs if "attachment:" in l.lower()]:
            failures.extend(
                _check_single_attachment_locator(aloc, item, all_declared, background_attachments)
            )

    f3_locator_blob = "\n".join(all_locators).lower()
    failures.extend(_check_cr_evidence_propagation(cr_attachments, f3_locator_blob))
    return len(failures) == 0, failures


def run_cross_artifact_consistency(
    q1: Dict[str, Any],
    q2: Dict[str, Any],
    q3: Dict[str, Any],
    f1_text: str = "",
    f2_text: str = "",
    f3_text: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Execute Cross-Artifact Consistency QA Gates."""
    failures: List[str] = []
    for q, label in [(q1, "Output 1"), (q2, "Output 2"), (q3, "Output 3")]:
        if not q.get("passed", False):
            failures.append(f"{label} failed QA gates.")

    meta = metadata or {}
    f3_dict: Dict[str, Any] = {}
    if f3_text:
        try:
            f3_dict = json.loads(f3_text) if isinstance(f3_text, str) else f3_text
        except Exception:
            f3_dict = {}

    c1_ok, c1_fails = _check_entity_alignment(f1_text, f2_text, meta)
    c2_ok, c2_fails = _check_temporal_consistency(f2_text, meta.get("date", ""))
    c3_ok, c3_fails = _check_action_coverage(f2_text, f1_text=f1_text)
    c4_ok, c4_fails = _check_career_firewall(f1_text, f2_text, f3_text, f3_dict)
    c5_ok, c5_fails = _check_collateral_evidence_propagation(f3_dict, meta)

    for f_list in (c1_fails, c2_fails, c3_fails, c4_fails, c5_fails):
        failures.extend(f_list)

    passed = (
        q1.get("passed", False)
        and q2.get("passed", False)
        and q3.get("passed", False)
        and c1_ok
        and c2_ok
        and c3_ok
        and c4_ok
        and c5_ok
    )
    return {
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "gates": {
            "Gate_Cross_1_Entity_Alignment": {"passed": c1_ok},
            "Gate_Cross_2_Temporal_Consistency": {"passed": c2_ok},
            "Gate_Cross_3_Action_Item_Coverage": {"passed": c3_ok},
            "Gate_Cross_4_Career_Firewall_Integrity": {"passed": c4_ok},
            "Gate_Cross_5_Collateral_Evidence_Propagation": {"passed": c5_ok},
        },
        "failures": failures,
    }
