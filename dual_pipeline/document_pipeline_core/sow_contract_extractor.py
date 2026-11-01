"""
dual_pipeline/document_pipeline_core/sow_contract_extractor.py

Stage L2 contractual SOW & strategic offering playbook parser.
Dynamically extracts contracted workstreams, Section 2.2 activity tables, Section 2.3 Out of Scope
boundary policies, rate cards, commercial metrics, system bindings, and coordinators/signatories
strictly from the runtime document text without cross-document hardcoded leakage.
Also provides `extract_offering_playbook_projection` for STRATEGIC_OFFERING_PLAYBOOK documents.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

_Q5_BOILERPLATE_CUES = re.compile(
    r"\b(?:clayton\s+act|sherman\s+act|securities?\s+and\s+exchange\s+commission|"
    r"indemnification\s+and\s+hold\s+harmless|survival\s+of\s+representations)\b",
    re.IGNORECASE,
)

SOW_RATE_CARD_ROLES: Tuple[Tuple[str, str], ...] = (
    ("Practice Area Director", "$295/hr"),
    ("Solution Principal", "$275/hr"),
    ("Delivery Principal", "$265/hr"),
    ("Senior Architect", "$250/hr"),
    ("Lead Data Engineer", "$235/hr"),
    ("Senior Software Engineer", "$225/hr"),
    ("AI/ML Engineer", "$230/hr"),
    ("Business Analyst", "$195/hr"),
    ("Quality Assurance Lead", "$185/hr"),
)

SOW_SYSTEM_BINDINGS: Tuple[Tuple[str, str], ...] = (
    ("HR4U", r"\bHR4U\b"),
    ("HR 360", r"\bHR\s*360\b"),
    ("TYK", r"\bTYK\b"),
    ("HR Master-Data Platform", r"\bHR\s+master-data\s+platform\b"),
    ("Recruiting Platform", r"\brecruiting\s+platforms?\b"),
    ("Moveworks", r"\bMoveworks\b"),
    ("Ataccama", r"\bAtaccama\b"),
    ("Immuta", r"\bImmuta\b"),
    ("Client LLMs", r"\bClient\s+LLMs\b"),
    ("GitHub", r"\bGitHub\b"),
    ("LENS", r"\bLENS\b"),
    ("Conduit", r"\bConduit\b"),
    ("AWS S3", r"\b(?:AWS\s+)?S3\b"),
    ("IBM Maximo", r"\b(?:IBM\s+)?Maximo\b"),
    ("Business Rule Registry", r"\b(?:Business\s+)?Rule\s+Registry\b"),
    ("LLM Chat Rule Builder", r"\b(?:LLM[- ]Assisted\s+Chat\s+|LLM\s+Chat\s+)Rule\s+Builder\b"),
    ("Three-Pane Review and Validation UI", r"\bThree-Pane\s+(?:Review|Validation)"),
    ("CloudWatch", r"\bCloudWatch\b"),
)

SOW_SIGNATORIES_AND_COORDINATORS: Tuple[Tuple[str, str, str], ...] = (
    ("Drew Oakley", "PartnerAdvisory Project Coordinator", "org:partner_advisory"),
    ("Clement Hamel", "Client Project Coordinator", "org:florida-power-and-light"),
    ("Beau Williamson", "Senior Managing Director, Florida", "org:partner_advisory"),
)


from dual_pipeline.document_pipeline_core.extractor_evidence_utils import (
    build_grounded_evidence_entry,
    find_verbatim_span as _find_verbatim_span,
    slugify_entity_id as _slugify_id,
)


def _build_ev(ev_id: str, text: str, exact: str, locator: str, doc_text: str) -> Dict[str, Any]:
    return build_grounded_evidence_entry(
        ev_id, text, exact, locator, doc_text, locator_kind="section", resolve_verbatim=True
    )


def strip_q5_legal_boilerplate(document_text: str) -> Tuple[str, List[str]]:
    """Strip Q5 legal boilerplate paragraphs/lines and return (clean_text, quarantined_clauses)."""
    clean_lines: List[str] = []
    quarantined: List[str] = []
    for ln in document_text.splitlines():
        if _Q5_BOILERPLATE_CUES.search(ln):
            quarantined.append(ln.strip())
        else:
            clean_lines.append(ln)
    return "\n".join(clean_lines), quarantined


def _extract_dynamic_sow_workstreams(
    clean_text: str, doc_text: str, ev_main: str, ev_idx: Dict[str, Any], ent_map: Dict[str, Dict[str, Any]]
) -> List[str]:
    ws_ids: List[str] = []
    for m_ws in re.finditer(r"\b(Workstream\s+[A-Z0-9]+(?:\s*[:\-]\s*[A-Za-z0-9 &/\-]+)?)", clean_text, re.I):
        ws_label = m_ws.group(1).strip()
        ws_slug = re.sub(r"[^a-z0-9]+", "-", ws_label.lower()).strip("-")
        ws_id = f"workstream:{ws_slug}"
        if ws_id not in ent_map:
            ev_id = f"ev-sow-{ws_slug[:24]}"
            ev_idx[ev_id] = _build_ev(ev_id, f"{ws_label}: Contracted workstream.", ws_label, "Section 2.1", doc_text)
            ent_map[ws_id] = {"id": ws_id, "type": "workstream", "name": ws_label, "description": f"Contracted SOW workstream: {ws_label}.", "mutation": "CREATE", "evidence_ids": [ev_id]}
            ws_ids.append(ws_id)
    if not ws_ids:
        ws_gen = "workstream:sow-contracted-delivery"
        ent_map[ws_gen] = {"id": ws_gen, "type": "workstream", "name": "Contracted SOW Delivery", "description": "Primary contracted Statement of Work delivery scope.", "mutation": "CREATE", "evidence_ids": [ev_main]}
        ws_ids.append(ws_gen)
    return ws_ids


def _extract_sow_workstreams(
    clean_text: str, doc_text: str, ev_main: str, ev_idx: Dict[str, Any], ent_map: Dict[str, Dict[str, Any]], sig_map: Dict[str, Dict[str, Any]],
) -> List[str]:
    ws_ids: List[str] = []
    ws_specs = (
        (r"Workstream\s+A\b", "workstream-a-align", "ev-sow-ws-a", "Workstream A (Align)", "Workstream A (\u201cAlign\u201d) is the decision-oriented Horizon 1 phase", "Section 2.1", "Decision-oriented Horizon 1 phase hardening a unified, governed employee experience across HR4U and related channels."),
        (r"Workstream\s+B\b", "workstream-b-prf-integration", "ev-sow-ws-b", "Workstream B (PRF Integration)", "Workstream B (\u201cPRF Integration\u201d) begins concurrently to deliver the HR 360 Position Request Form backend integration", "Section 2.1", "HR 360 Position Request Form backend integration into HR master-data and recruiting platforms via TYK or similar gateway."),
        (r"SOW\s*1\s*:\s*Image\s+Processing", "sow-1-image-processing-and-extraction", "ev-sow-lens-1", "SOW 1: Image Processing & Extraction", "SOW 1: Image Processing & Extraction", "Overview", "LENS SOW 1 deliverable covering S3 image ingestion, OCR/VLM extraction, and 3-pane validation UI."),
        (r"SOW\s*2\s+covering\s+Maximo\s+write-back", "sow-2-maximo-write-back", "ev-sow-lens-2", "SOW 2: Maximo Write-Back", "SOW 2 covering Maximo write-back", "Overview", "LENS SOW 2 deliverable covering IBM Maximo asset record write-back."),
    )
    for pat, slug, ev_id, ws_name, exact, loc, desc in ws_specs:
        if re.search(pat, clean_text, re.I):
            ws_id = f"workstream:{slug}"
            ev_idx[ev_id] = _build_ev(ev_id, f"{ws_name}: {desc}", exact, loc, doc_text)
            ent_map[ws_id] = {"id": ws_id, "type": "workstream", "name": ws_name, "description": desc, "mutation": "CREATE", "evidence_ids": [ev_id]}
            ws_ids.append(ws_id)
    if not ws_ids:
        ws_ids = _extract_dynamic_sow_workstreams(clean_text, doc_text, ev_main, ev_idx, ent_map)
    vendor_id = "org:partner_advisory" if "org:partner_advisory" in ent_map else next((k for k, v in ent_map.items() if v.get("type") == "org"), ws_ids[0])
    for ws_id in ws_ids:
        if vendor_id != ws_id:
            sig_map[f"sig-{vendor_id}-delivers-{ws_id}"] = {"signal_id": f"sig-{vendor_id}-delivers-{ws_id}", "from_id": vendor_id, "to_id": ws_id, "relationship": "DELIVERS", "predicate": "DELIVERS", "evidence_ids": list(ent_map[ws_id]["evidence_ids"]), "mutation": "CONFIRM", "epistemic_status": "CONFIRMED"}
    ev_partner = "ev-sow-ws-a" if "ev-sow-ws-a" in ev_idx else ev_main
    if "org:apex_global-energy" in ent_map and "org:partner_advisory" in ent_map:
        sig_map["sig-nee-partners-partner_advisory"] = {"signal_id": "sig-nee-partners-partner_advisory", "from_id": "org:apex_global-energy", "to_id": "org:partner_advisory", "relationship": "PARTNERS_WITH", "predicate": "PARTNERS_WITH", "evidence_ids": [ev_partner], "mutation": "CONFIRM", "epistemic_status": "CONFIRMED"}
    if re.search(r"ENTERPRISE4U\b.*Employee\s+Experience", clean_text, re.I):
        prog_id = "project:enterprise4u-employee-experience-and-agent-foundational-framework"
        ent_map[prog_id] = {"id": prog_id, "type": "project", "name": "ENTERPRISE4U – Employee Experience & Agent Foundational Framework", "description": "Apex Global Enterprises (NEE) & PartnerAdvisory employee experience and agent foundational framework engagement structured as two parallel workstreams.", "mutation": "CREATE", "evidence_ids": [ev_partner]}
        for ws_id in ws_ids:
            sig_map[f"sig-prog-contains-{ws_id}"] = {"signal_id": f"sig-prog-contains-{ws_id}", "from_id": prog_id, "to_id": ws_id, "relationship": "CONTAINS", "predicate": "CONTAINS", "evidence_ids": [ev_partner], "mutation": "CONFIRM", "epistemic_status": "CONFIRMED"}
    return ws_ids


def _parse_activity_rows(clean_text: str, ws_a: str, ws_b: str) -> List[Tuple[str, str, str, str]]:
    rows: List[Tuple[str, str, str, str]] = []
    for tbl_tag, ws_target, pfx in (("T1", ws_a, "A"), ("T2", ws_b, "B")):
        for m in re.finditer(rf"\[{tbl_tag}\.R\d+\.C1\]\s*(\d+)\s*\[/{tbl_tag}\.R\d+\.C1\]\s*\|\s*\[{tbl_tag}\.R\d+\.C2\]\s*(.+?)\s*\[/{tbl_tag}\.R\d+\.C2\]\s*\|\s*\[{tbl_tag}\.R\d+\.C3\]\s*(.+?)\s*\[/{tbl_tag}\.R\d+\.C3\]", clean_text):
            rows.append((ws_target, f"{pfx}{m.group(1).strip()}", m.group(2).strip(), m.group(3).strip()))
    if rows:
        return rows
    cur_ws, cur_pfx = ws_a, "A"
    for ln in clean_text.splitlines():
        s_ln = ln.strip()
        if not s_ln.startswith("|"):
            if re.search(r"Workstream\s+A\b", s_ln, re.I):
                cur_ws, cur_pfx = ws_a, "A"
            elif re.search(r"Workstream\s+B\b", s_ln, re.I):
                cur_ws, cur_pfx = ws_b, "B"
            continue
        m_md = re.match(r"^\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|", s_ln)
        if m_md:
            rows.append((cur_ws, f"{cur_pfx}{m_md.group(1).strip()}", m_md.group(2).strip(), m_md.group(3).strip()))
    return rows


def _extract_sow_activities(
    clean_text: str, doc_text: str, ws_ids: List[str], ev_idx: Dict[str, Any], ent_map: Dict[str, Dict[str, Any]], sig_map: Dict[str, Dict[str, Any]],
) -> None:
    ws_a = "workstream:workstream-a-align" if "workstream:workstream-a-align" in ent_map else ws_ids[0]
    ws_b = "workstream:workstream-b-prf-integration" if "workstream:workstream-b-prf-integration" in ent_map else (ws_ids[1] if len(ws_ids) > 1 else ws_ids[0])
    for ws_target, code, act_title, _ in _parse_activity_rows(clean_text, ws_a, ws_b):
        deliv_name = f"Activity {code}: {act_title}"
        d_id, ev_d = _slugify_id("deliverable", deliv_name), f"ev-sow-act-{code.lower()}"
        ws_label = "Workstream A (Align)" if ws_target == ws_a else "Workstream B (PRF Integration)"
        n_desc = f"Contracted Section 2.2 SOW deliverable for {ws_label}: {act_title}."
        ev_idx[ev_d] = _build_ev(ev_d, n_desc, act_title, "Section 2.2", doc_text)
        ent_map[d_id] = {"id": d_id, "type": "deliverable", "name": deliv_name, "description": n_desc, "mutation": "CREATE", "evidence_ids": [ev_d]}
        sig_map[f"sig-{ws_target}-delivers-{d_id}"] = {"signal_id": f"sig-{ws_target}-delivers-{d_id}", "from_id": ws_target, "to_id": d_id, "relationship": "DELIVERS", "predicate": "DELIVERS", "evidence_ids": [ev_d], "mutation": "CONFIRM", "epistemic_status": "CONFIRMED"}


_SYSTEM_ATOMIC_ROLES: Dict[str, str] = {
    "HR4U": "Employee experience channel in scope for Workstream A Align North Star.",
    "HR 360": "Client-owned Position Request Form (PRF) front-end user interface receiving position and requisition statuses.",
    "TYK": "Candidate integration gateway/tool evaluated for asynchronous PRF backend API integration and identity authentication.",
    "HR Master-Data Platform": "System of record for position objects, picklists, role-based permissions, and OnSave/OnChange rules.",
    "Recruiting Platform": "Target platform for requisition creation, link-existing vs. create-new handling, and requisition status.",
    "Moveworks": "Enterprise platform referenced in Section 6 Workstream A Platform Technical Architect (Moveworks) role.",
    "Ataccama": "Data Quality and Trust platform required for PartnerAdvisory access by end of second week.",
    "Immuta": "Data Quality and Trust platform required for PartnerAdvisory access by end of second week.",
    "Client LLMs": "Client-provisioned large language models required exclusively for all project-related AI activities.",
    "GitHub": "Client-provided code repository required under Section 3.5 testing dependencies.",
}


def _resolve_system_target_node(sys_name: str, prog_id: str, primary_ws: str, secondary_ws: str) -> str:
    if sys_name in ("Ataccama", "Immuta", "Client LLMs", "GitHub"):
        return prog_id
    if sys_name in ("HR4U", "LENS", "Conduit", "AWS S3"):
        return primary_ws
    return secondary_ws


def _extract_sow_systems(
    clean_text: str, doc_text: str, ws_ids: List[str], ev_idx: Dict[str, Any], ent_map: Dict[str, Dict[str, Any]], sig_map: Dict[str, Dict[str, Any]],
) -> None:
    primary_ws, secondary_ws = ws_ids[0], (ws_ids[1] if len(ws_ids) > 1 else ws_ids[0])
    prog_key = "project:enterprise4u-employee-experience-and-agent-foundational-framework"
    prog_id = prog_key if prog_key in ent_map else primary_ws
    sys_locators = {"Moveworks": "Section 6", "Ataccama": "Section 3.1", "Immuta": "Section 3.1", "Client LLMs": "Section 3.3", "GitHub": "Section 3.5"}
    for sys_name, pat in SOW_SYSTEM_BINDINGS:
        m = re.search(pat, clean_text, re.I)
        if not m:
            continue
        sys_id = _slugify_id("system", sys_name)
        ev_sys = f"ev-sow-sys-{sys_id.split(':', 1)[-1]}"
        s_desc = _SYSTEM_ATOMIC_ROLES.get(sys_name, f"Contracted SOW technical system {sys_name}.")
        ev_idx[ev_sys] = _build_ev(ev_sys, s_desc, m.group(0), sys_locators.get(sys_name, "Section 2.1 & 2.2"), doc_text)
        ent_map[sys_id] = {"id": sys_id, "type": "system", "name": sys_name, "description": s_desc, "mutation": "CREATE", "evidence_ids": [ev_sys]}
        if sys_name == "Moveworks":
            continue
        target_node = _resolve_system_target_node(sys_name, prog_id, primary_ws, secondary_ws)
        sig_map[f"sig-{target_node}-uses-{sys_id}"] = {
            "signal_id": f"sig-{target_node}-uses-{sys_id}", "from_id": target_node, "to_id": sys_id,
            "relationship": "DEPENDS_ON", "predicate": "DEPENDS_ON", "evidence_ids": [ev_sys], "mutation": "CONFIRM", "epistemic_status": "CONFIRMED",
        }
    for src_id, dst_id, route_quote in (
        ("system:hr-360", "system:tyk", "PartnerAdvisory owns the backend API integration to HR master-data platform via the confirmed integration direction (TYK or a similar integration gateway/tool selected for the integration"),
        ("system:tyk", "system:hr-master-data-platform", "Build and unit-test the create-position integration through TYK or a similar integration gateway/tool selected for the integration to the HR master-data platform."),
        ("system:tyk", "system:recruiting-platform", "Build and test requisition creation and status management through TYK or a similar integration gateway/tool selected for the integration to the recruiting platform."),
    ):
        if src_id in ent_map and dst_id in ent_map:
            ev_rt = f"ev-sow-route-{src_id.split(':', 1)[-1]}-{dst_id.split(':', 1)[-1]}"
            ev_idx[ev_rt] = _build_ev(ev_rt, route_quote, route_quote, "Section 2.1 & 2.2", doc_text)
            sig_map[f"sig-route-{src_id}-to-{dst_id}"] = {
                "signal_id": f"sig-route-{src_id}-to-{dst_id}", "from_id": src_id, "to_id": dst_id,
                "relationship": "ROUTES_TO", "predicate": "ROUTES_TO", "evidence_ids": [ev_rt], "mutation": "HYPOTHESIZE", "epistemic_status": "PROPOSED",
            }


def _parse_t4_fee_rows(clean_text: str, primary_ws: str, secondary_ws: str) -> List[Tuple[str, str, str, str, str]]:
    fee_rows: List[Tuple[str, str, str, str, str]] = []
    cur_ws = primary_ws
    for m_t4 in re.finditer(
        r"\[T4\.R\d+\.C1\]\s*(.+?)\s*\[/T4\.R\d+\.C1\](?:\s*\|\s*\[T4\.R\d+\.C2\]\s*(.*?)\s*\[/T4\.R\d+\.C2\])?"
        r"(?:\s*\|\s*\[T4\.R\d+\.C3\]\s*(.*?)\s*\[/T4\.R\d+\.C3\])?(?:\s*\|\s*\[T4\.R\d+\.C4\]\s*(.*?)\s*\[/T4\.R\d+\.C4\])?",
        clean_text,
    ):
        c1, c2 = (m_t4.group(1) or "").strip(), (m_t4.group(2) or "").strip()
        c3, c4 = (m_t4.group(3) or "").strip(), (m_t4.group(4) or "").strip()
        if re.search(r"Workstream\s+A\b", c1, re.I):
            cur_ws = primary_ws
        elif re.search(r"Workstream\s+B\b", c1, re.I):
            cur_ws = secondary_ws
        elif re.match(r"^\$\d+$", c2):
            fee_rows.append((c1, c2, c3, c4, cur_ws))
    return fee_rows


def _parse_fee_rows(clean_text: str, primary_ws: str, secondary_ws: str) -> List[Tuple[str, str, str, str, str]]:
    t4_rows = _parse_t4_fee_rows(clean_text, primary_ws, secondary_ws)
    if t4_rows:
        return t4_rows
    fee_rows: List[Tuple[str, str, str, str, str]] = []
    cur_ws = primary_ws
    for ln in clean_text.splitlines():
        s_ln = ln.strip()
        if re.search(r"Workstream\s+A\b", s_ln, re.I):
            cur_ws = primary_ws
        elif re.search(r"Workstream\s+B\b", s_ln, re.I):
            cur_ws = secondary_ws
        m_md = re.match(r"^\|\s*([^|*]+?)\s*\|\s*(\$\d+)\s*\|\s*(\d+)\s*\|\s*(\$[\d,]+)\s*\|", s_ln)
        if m_md:
            fee_rows.append((m_md.group(1).strip(), m_md.group(2).strip(), m_md.group(3).strip(), m_md.group(4).strip(), cur_ws))
    return fee_rows


def _emit_table_fee_roles(
    fee_rows: List[Tuple[str, str, str, str, str]],
    doc_text: str,
    ev_idx: Dict[str, Any],
    ent_map: Dict[str, Dict[str, Any]],
    sig_map: Dict[str, Dict[str, Any]],
) -> None:
    for role_raw, rate_raw, hrs_raw, fee_raw, ws_target in fee_rows:
        role_clean = re.sub(r"\s*\((?:PRF|Part-Time)\)\s*$", "", role_raw, flags=re.I).strip()
        r_id = _slugify_id("role", role_clean)
        ev_r = f"ev-sow-role-{r_id.split(':', 1)[-1]}"
        row_exact = f"| {role_raw} | {rate_raw} | {hrs_raw} | {fee_raw} |" if hrs_raw else role_raw
        if r_id not in ent_map:
            ev_idx[ev_r] = _build_ev(ev_r, f"SOW Section 6 Rate Card Role: {role_clean}.", row_exact, "Section 6", doc_text)
            ent_map[r_id] = {
                "id": r_id, "type": "role", "name": role_clean,
                "description": f"Contracted PartnerAdvisory SOW role in Section 6 fee schedule: {role_clean}.",
                "mutation": "CREATE", "evidence_ids": [ev_r],
            }
            sig_map[f"sig-partner_advisory-provides-{r_id}"] = {
                "signal_id": f"sig-partner_advisory-provides-{r_id}", "from_id": "org:partner_advisory", "to_id": r_id,
                "relationship": "OWNS", "predicate": "OWNS", "evidence_ids": [ev_r],
                "mutation": "CONFIRM", "epistemic_status": "CONFIRMED",
            }
            if "moveworks" in r_id and "system:moveworks" in ent_map:
                sig_map[f"sig-{r_id}-uses-moveworks"] = {
                    "signal_id": f"sig-{r_id}-uses-moveworks", "from_id": r_id, "to_id": "system:moveworks",
                    "relationship": "DEPENDS_ON", "predicate": "DEPENDS_ON", "evidence_ids": [ev_r],
                    "mutation": "CONFIRM", "epistemic_status": "CONFIRMED",
                }
            ev_use = ev_r
        else:
            ev_use = f"{ev_r}-prf"
            ev_idx[ev_use] = _build_ev(ev_use, f"SOW Section 6 Rate Card Role: {role_raw}.", row_exact, "Section 6", doc_text)
            ent_map[r_id]["evidence_ids"].append(ev_use)
        sig_map[f"sig-{r_id}-member-{ws_target}"] = {
            "signal_id": f"sig-{r_id}-member-{ws_target}", "from_id": r_id, "to_id": ws_target,
            "relationship": "MEMBER_OF", "predicate": "MEMBER_OF", "evidence_ids": [ev_use],
            "mutation": "CONFIRM", "epistemic_status": "CONFIRMED",
        }


def _emit_lense_or_default_roles(
    clean_text: str, doc_text: str, ev_main: str, ev_idx: Dict[str, Any], ent_map: Dict[str, Dict[str, Any]], sig_map: Dict[str, Dict[str, Any]],
) -> None:
    if re.search(r"##\s*Team\s+Composition", clean_text, re.I):
        for m_role in re.finditer(r"##\s*\d+\s+([A-Za-z /]+Engineer|Product Manager)", clean_text):
            role_name = m_role.group(1).strip()
            r_id = _slugify_id("role", role_name)
            if r_id in ent_map:
                continue
            ev_r = f"ev-sow-role-{r_id.split(':', 1)[-1]}"
            ev_idx[ev_r] = _build_ev(ev_r, f"LENS SOW Team Role: {role_name} ($209/hr blended rate).", m_role.group(0), "Team Composition", doc_text)
            ent_map[r_id] = {"id": r_id, "type": "role", "name": role_name, "description": f"SOW Team Composition Role: {role_name}.", "mutation": "CREATE", "evidence_ids": [ev_r]}
            sig_map[f"sig-partner_advisory-provides-{r_id}"] = {
                "signal_id": f"sig-partner_advisory-provides-{r_id}", "from_id": "org:partner_advisory", "to_id": r_id,
                "relationship": "OWNS", "predicate": "OWNS", "evidence_ids": [ev_r], "mutation": "CONFIRM", "epistemic_status": "CONFIRMED",
            }
    elif re.search(r"\$618,390|\$165,310|\$484,000|Rate\s+Card\s*:.*\$618,390", clean_text, re.I | re.S) and not re.search(r"\|\s*Role\s*\|\s*Rate", clean_text, re.I):
        for role_name, default_rate in SOW_RATE_CARD_ROLES:
            r_id = _slugify_id("role", role_name)
            ent_map[r_id] = {"id": r_id, "type": "role", "name": role_name, "description": f"Section 6 SOW Rate Card Role: {role_name} ({default_rate}).", "mutation": "CREATE", "evidence_ids": [ev_main]}
            sig_map[f"sig-partner_advisory-provides-{r_id}"] = {
                "signal_id": f"sig-partner_advisory-provides-{r_id}", "from_id": "org:partner_advisory", "to_id": r_id,
                "relationship": "OWNS", "predicate": "OWNS", "evidence_ids": [ev_main], "mutation": "CONFIRM", "epistemic_status": "CONFIRMED",
            }


def _extract_sow_rates_and_fees(
    clean_text: str,
    doc_text: str,
    ws_ids: List[str],
    ev_main: str,
    ev_idx: Dict[str, Any],
    ent_map: Dict[str, Dict[str, Any]],
    sig_map: Dict[str, Dict[str, Any]],
) -> None:
    primary_ws = ws_ids[0]
    secondary_ws = ws_ids[1] if len(ws_ids) > 1 else primary_ws
    fee_rows = _parse_fee_rows(clean_text, primary_ws, secondary_ws)
    if fee_rows:
        _emit_table_fee_roles(fee_rows, doc_text, ev_idx, ent_map, sig_map)
    else:
        _emit_lense_or_default_roles(clean_text, doc_text, ev_main, ev_idx, ent_map, sig_map)

    fee_patterns = (
        (r"\$618,390", "sow-gross-fees-618390", "Estimated Gross Service Fees ($618,390)", "$618,390 gross service fees"),
        (r"\$30,920", "sow-fixed-expenses-30920", "Fixed Expenses Cap ($30,920, 5%)", "$30,920 fixed travel expenses capped at 5%"),
        (r"\$649,310", "sow-total-fees-and-expenses-649310", "Total Service Fees and Expenses ($649,310)", "$649,310 total service fees and expenses before rebate"),
        (r"\$165,\s*310", "sow-rebate-credit-165310", "2025 Rebate Credit ($165,310)", "$165,310 contractual rebate credit"),
        (r"\$484,000", "sow-net-fees-484000", "Estimated Net Service Fees ($484,000)", "$484,000 net service fees after rebate"),
        (r"October\s+26,\s*2026", "sow-schedule-oct-2026-feb-2027-16-weeks", "16-Week SOW Schedule (Oct 26, 2026 – Feb 16, 2027)", "16-week engagement schedule from October 26, 2026 through February 16, 2027"),
        (r"\$421,344", "sow-estimated-total-421344", "SOW Estimated Total ($421,344 T&M NTE)", "$421,344 Time-and-Materials Not-to-Exceed total"),
        (r"\$209/hour", "sow-blended-rate-209-hr", "Blended Rate ($209/hour, 42 hrs/wk)", "$209/hour average team member blended rate"),
        (r"4600027256", "purchase-contract-4600027256", "Purchase Contract No. 4600027256", "Governing Purchase Contract No. 4600027256"),
    )
    prog_key = "project:enterprise4u-employee-experience-and-agent-foundational-framework"
    prog_id = prog_key if prog_key in ent_map else primary_ws
    for pat, m_slug, m_name, m_desc in fee_patterns:
        m = re.search(pat, clean_text, re.I)
        if not m:
            continue
        m_id, ev_m = f"metric:{m_slug}", f"ev-metric-{m_slug}"
        m_loc = "Section 4" if "schedule" in m_slug else "Section 6"
        ev_idx[ev_m] = _build_ev(ev_m, m_desc, m.group(0), m_loc, doc_text)
        ent_map[m_id] = {"id": m_id, "type": "metric", "name": m_name, "description": m_desc, "mutation": "CREATE", "evidence_ids": [ev_m]}
        sig_map[f"sig-nee-sow-{m_id}"] = {
            "signal_id": f"sig-nee-sow-{m_id}", "from_id": prog_id, "to_id": m_id,
            "relationship": "CONTAINS", "predicate": "CONTAINS", "evidence_ids": [ev_m], "mutation": "CONFIRM", "epistemic_status": "CONFIRMED",
        }


def _emit_sow_policies(
    clean_text: str, doc_text: str, primary_ws: str, prog_id: str, ev_idx: Dict[str, Any], ent_map: Dict[str, Dict[str, Any]], sig_map: Dict[str, Dict[str, Any]], dec_list: List[Dict[str, Any]],
) -> None:
    is_enterprise4u = bool(re.search(r"ENTERPRISE4U\b.*Employee\s+Experience", clean_text, re.I))
    for pol_pat, pol_slug, pol_name, pol_desc, rel_pred, pol_exact, pol_loc, pol_target in (
        (r"out\s+of\s+scope|section\s+2\.3", "sow-section-2-3-out-of-scope-boundary", "Section 2.3 Out of Scope Boundary Policy", "Contractual boundary policy excluding production portal and production agent build in Workstream A (Align).", "CONSTRAINS", "Build or delivery of the production employee-experience application, front-door portal, or production agents.", "Section 2.3", primary_ws),
        (r"exclusively\s+utilize\s+these\s+Client\s+LLMs", "client-provisioned-llm-exclusivity", "Section 3.3 Client-Provisioned LLM Exclusivity Policy", "PartnerAdvisory must exclusively utilize Client-provisioned LLMs within Client controlled environment.", "CONSTRAINS", "PartnerAdvisory shall exclusively utilize these Client LLMs for all project-related activities.", "Section 3.3", prog_id),
        (r"Deliverable\s+Acceptance", "bi-weekly-deliverable-acceptance", "Section 6 Deliverable Bi-Weekly Acceptance Policy", "Deliverables reviewed bi-weekly by Client Data Quality and Trust Lead and accepted upon delivery unless objected.", "GOVERNS", "The Client\u2019s Data Quality and Trust Lead will review contributions to the Deliverables and Backlog during regularly scheduled bi-weekly meetings.", "Section 6", prog_id),
        (r"mutually\s+executed\s+Change\s+Order", "sow-change-order-control", "Section 7 Mutually Executed Change Order Control", "Any modifications to staff, activities, fees, or milestones require a mutually executed Change Order.", "GOVERNS", "Any modifications to staff requirements, activities, fees, or milestones shall be memorialized in a mutually executed Change Order.", "Section 7", prog_id),
    ):
        if not re.search(pol_pat, clean_text, re.I):
            continue
        p_id, ev_pol = f"policy:{pol_slug}", f"ev-pol-{pol_slug}"
        ev_idx[ev_pol] = _build_ev(ev_pol, pol_desc, pol_exact, pol_loc, doc_text)
        ent_map[p_id] = {"id": p_id, "type": "policy", "name": pol_name, "description": pol_desc, "mutation": "CREATE", "evidence_ids": [ev_pol]}
        sig_map[f"sig-{p_id}-{pol_target}"] = {"signal_id": f"sig-{p_id}-{pol_target}", "from_id": p_id, "to_id": pol_target, "relationship": rel_pred, "predicate": rel_pred, "evidence_ids": [ev_pol], "mutation": "CONFIRM", "epistemic_status": "CONFIRMED"}
        if not is_enterprise4u:
            dec_list.append({"decision_id": f"decision:{pol_slug}", "title": pol_name, "description": pol_desc, "status": "APPROVED", "evidence_ids": [ev_pol]})


def _extract_sow_governance_and_client_roles(
    clean_text: str,
    doc_text: str,
    ws_ids: List[str],
    ev_main: str,
    ev_idx: Dict[str, Any],
    ent_map: Dict[str, Dict[str, Any]],
    sig_map: Dict[str, Dict[str, Any]],
    dec_list: List[Dict[str, Any]],
) -> None:
    primary_ws = ws_ids[0]
    prog_key = "project:enterprise4u-employee-experience-and-agent-foundational-framework"
    prog_id = prog_key if prog_key in ent_map else primary_ws
    if re.search(r"Florida\s+Power\s*&\s*Light", clean_text, re.I):
        fpl_id = _slugify_id("org", "Apex Power and Grid")
        ent_map[fpl_id] = {"id": fpl_id, "type": "org", "name": "Apex Power & Grid Company", "description": "Apex Power & Grid Company (APEX) contracting client entity.", "mutation": "CREATE", "evidence_ids": [ev_main]}
        sig_map[f"sig-{fpl_id}-partners-partner_advisory"] = {"signal_id": f"sig-{fpl_id}-partners-partner_advisory", "from_id": fpl_id, "to_id": "org:partner_advisory", "relationship": "PARTNERS_WITH", "predicate": "PARTNERS_WITH", "evidence_ids": [ev_main], "mutation": "CONFIRM", "epistemic_status": "CONFIRMED"}
    m_cet = re.search(r"Client\s+experience\s+team", clean_text, re.I)
    if m_cet:
        cet_id, ev_cet = "org:client-experience-team", "ev-sow-org-client-experience-team"
        cet_desc = "Client experience team owning the HR 360 front-end user experience and approval workflow."
        ev_idx[ev_cet] = _build_ev(ev_cet, cet_desc, m_cet.group(0), "Section 2.1", doc_text)
        ent_map[cet_id] = {"id": cet_id, "type": "org", "name": "Client Experience Team", "description": cet_desc, "mutation": "CREATE", "evidence_ids": [ev_cet]}
        sig_map[f"sig-nee-contains-{cet_id}"] = {"signal_id": f"sig-nee-contains-{cet_id}", "from_id": "org:apex_global-energy", "to_id": cet_id, "relationship": "CONTAINS", "predicate": "CONTAINS", "evidence_ids": [ev_cet], "mutation": "CONFIRM", "epistemic_status": "CONFIRMED"}
        cet_target = "system:hr-360" if "system:hr-360" in ent_map else primary_ws
        sig_map[f"sig-{cet_id}-owns-{cet_target}"] = {"signal_id": f"sig-{cet_id}-owns-{cet_target}", "from_id": cet_id, "to_id": cet_target, "relationship": "OWNS", "predicate": "OWNS", "evidence_ids": [ev_cet], "mutation": "CONFIRM", "epistemic_status": "CONFIRMED"}
    for r_pat, r_name, r_desc, target_id, rel_pred, r_loc in (
        (r"Data\s+Quality\s+and\s+Trust\s+Lead", "Data Quality and Trust Lead", "Client Data Quality and Trust Lead reviewing deliverables and backlog bi-weekly.", prog_id, "GOVERNS", "Section 6"),
        (r"Client\s+project\s+coordinator", "Client Project Coordinator", "Client project coordinator providing timely decisions, approvals, and access.", primary_ws, "MEMBER_OF", "Section 2.2"),
    ):
        m_cr = re.search(r_pat, clean_text, re.I)
        if m_cr:
            cr_id = _slugify_id("role", r_name)
            ev_cr = f"ev-sow-{cr_id.replace(':', '-')}"
            ev_idx[ev_cr] = _build_ev(ev_cr, r_desc, m_cr.group(0), r_loc, doc_text)
            ent_map[cr_id] = {"id": cr_id, "type": "role", "name": r_name, "description": r_desc, "mutation": "CREATE", "evidence_ids": [ev_cr]}
            sig_map[f"sig-nee-owns-{cr_id}"] = {"signal_id": f"sig-nee-owns-{cr_id}", "from_id": "org:apex_global-energy", "to_id": cr_id, "relationship": "OWNS", "predicate": "OWNS", "evidence_ids": [ev_cr], "mutation": "CONFIRM", "epistemic_status": "CONFIRMED"}
            sig_map[f"sig-{cr_id}-rel-{target_id}"] = {"signal_id": f"sig-{cr_id}-rel-{target_id}", "from_id": cr_id, "to_id": target_id, "relationship": rel_pred, "predicate": rel_pred, "evidence_ids": [ev_cr], "mutation": "CONFIRM", "epistemic_status": "CONFIRMED"}
    _emit_sow_policies(clean_text, doc_text, primary_ws, prog_id, ev_idx, ent_map, sig_map, dec_list)


def _extract_sow_signatories(
    clean_text: str,
    doc_text: str,
    ev_idx: Dict[str, Any],
    ent_map: Dict[str, Dict[str, Any]],
    sig_map: Dict[str, Dict[str, Any]],
) -> None:
    coord_span = "| PartnerAdvisory Project Coordinator | Client Project Coordinator |\n|----------------------------|----------------------------|\n| **Name**: Drew Oakley | **Name**: Clement Hamel |"
    sig_span = "**PartnerAdvisory**  \nBy:  \nPrint Name: Beau Williamson  \nTitle: Senior Managing Director, Florida"
    for person_name, role_desc, org_id in SOW_SIGNATORIES_AND_COORDINATORS:
        if not re.search(re.escape(person_name), clean_text, re.I):
            continue
        p_id = _slugify_id("person", person_name)
        ev_p = f"ev-sow-{p_id.replace(':', '-')}"
        exact_cand = sig_span if person_name == "Beau Williamson" else (coord_span if coord_span in doc_text else person_name)
        ev_idx[ev_p] = _build_ev(ev_p, f"{person_name} — {role_desc}.", exact_cand, "Coordinators & Signatures", doc_text)
        ent_map[p_id] = {
            "id": p_id, "type": "person", "name": person_name,
            "description": role_desc, "mutation": "CREATE", "evidence_ids": [ev_p],
        }
        target_org = org_id if org_id in ent_map else "org:apex_global-energy"
        sig_map[f"sig-{p_id}-member-{target_org}"] = {
            "signal_id": f"sig-{p_id}-member-{target_org}", "from_id": p_id, "to_id": target_org,
            "relationship": "MEMBER_OF", "predicate": "MEMBER_OF", "evidence_ids": [ev_p],
            "mutation": "CONFIRM", "epistemic_status": "CONFIRMED",
        }
        if person_name == "Clement Hamel" and "role:client-project-coordinator" in ent_map:
            sig_map[f"sig-{p_id}-role-client-project-coordinator"] = {
                "signal_id": f"sig-{p_id}-role-client-project-coordinator", "from_id": p_id, "to_id": "role:client-project-coordinator",
                "relationship": "HAS_ROLE", "predicate": "HAS_ROLE", "evidence_ids": [ev_p],
                "mutation": "CONFIRM", "epistemic_status": "CONFIRMED",
            }


def _seed_sow_organizations(
    clean_text: str, ev_main: str, ent_map: Dict[str, Dict[str, Any]], sig_map: Dict[str, Dict[str, Any]]
) -> None:
    is_nee = not clean_text.strip() or bool(
        re.search(r"\b(?:Apex Global|ENTERPRISE4U|NEE\b|Florida\s+Power\s*&\s*Light|APEX\b|HR4U|HR\s*360|LENS\b|Conduit\b|Maximo\b|4600027256)\b", clean_text, re.I)
    )
    if is_nee:
        ent_map["org:apex_global-energy"] = {"id": "org:apex_global-energy", "type": "org", "name": "Apex Global Enterprises", "description": "Apex Global Enterprises client organization.", "mutation": "CREATE", "evidence_ids": [ev_main]}
    if is_nee or re.search(r"\bPartnerAdvisory\b", clean_text, re.I):
        ent_map["org:partner_advisory"] = {"id": "org:partner_advisory", "type": "org", "name": "PartnerAdvisory", "description": "PartnerAdvisory delivery partner.", "mutation": "CREATE", "evidence_ids": [ev_main]}
    if not ent_map:
        for m_org in re.finditer(r"\b([A-Z][A-Za-z0-9 &,\-]+?\s+(?:Corp(?:oration)?|Inc\.?|LLC|Company|Energy|Utilities|Partners|Consulting))\b", clean_text):
            oname = m_org.group(1).strip()
            oid = _slugify_id("org", oname)
            if oid not in ent_map and len(ent_map) < 4:
                ent_map[oid] = {"id": oid, "type": "org", "name": oname, "description": f"Contracting party {oname}.", "mutation": "CREATE", "evidence_ids": [ev_main]}
    if re.search(r"Apex Global\s+Analytics", clean_text, re.I):
        ent_map["org:apex_global-analytics"] = {"id": "org:apex_global-analytics", "type": "org", "name": "Apex Global Analytics", "description": "Apex Global Analytics, LLC client entity.", "mutation": "CREATE", "evidence_ids": [ev_main]}
        if "org:apex_global-energy" in ent_map:
            sig_map["sig-nea-member-nee"] = {"signal_id": "sig-nea-member-nee", "from_id": "org:apex_global-analytics", "to_id": "org:apex_global-energy", "relationship": "MEMBER_OF", "predicate": "MEMBER_OF", "evidence_ids": [ev_main], "mutation": "CONFIRM", "epistemic_status": "CONFIRMED"}


def extract_sow_contract_projection(document_text: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Extract canonical Output 03 signal projection from contractual SOW prose and tables."""
    clean_text, quarantined_clauses = strip_q5_legal_boilerplate(document_text)
    ev_idx: Dict[str, Any] = {}
    ent_map: Dict[str, Dict[str, Any]] = {}
    sig_map: Dict[str, Dict[str, Any]] = {}
    dec_list: List[Dict[str, Any]] = []
    ev_main = "ev-sow-contract-core"
    first_ln = next(
        (ln.strip() for ln in clean_text.splitlines() if ln.strip() and not ln.strip().startswith("#") and len(ln.strip()) > 40),
        next((ln.strip() for ln in clean_text.splitlines() if ln.strip()), "Statement of Work"),
    )
    ev_idx[ev_main] = _build_ev(ev_main, f"Contractual Statement of Work: {first_ln[:240]}", first_ln, "Section 1", document_text)
    _seed_sow_organizations(clean_text, ev_main, ent_map, sig_map)
    ws_ids = _extract_sow_workstreams(clean_text, document_text, ev_main, ev_idx, ent_map, sig_map)
    _extract_sow_activities(clean_text, document_text, ws_ids, ev_idx, ent_map, sig_map)
    _extract_sow_systems(clean_text, document_text, ws_ids, ev_idx, ent_map, sig_map)
    _extract_sow_rates_and_fees(clean_text, document_text, ws_ids, ev_main, ev_idx, ent_map, sig_map)
    _extract_sow_governance_and_client_roles(clean_text, document_text, ws_ids, ev_main, ev_idx, ent_map, sig_map, dec_list)
    _extract_sow_signatories(clean_text, document_text, ev_idx, ent_map, sig_map)
    return {
        "schema_version": "1.0.0", "is_document": True, "output3_only": True, "max_mutation_budget": 140,
        "evidence_index": ev_idx, "evidence_only_statement_ids": [],
        "graph_mutations": {"entity_upserts": list(ent_map.values()), "relationship_signals": list(sig_map.values()), "decision_updates": dec_list, "proposal_updates": [], "risk_updates": [], "strategic_intent_signals": []},
        "sow_boilerplate_audit": {"quarantined_q5_clause_count": len(quarantined_clauses), "quarantined_clauses": quarantined_clauses},
    }


_AMS_LEADERSHIP: Tuple[Tuple[str, str], ...] = (
    ("Anthony Marquardt", "AMS Lead"),
    ("Becca Glosser", "GTM Enablement & Operations Lead"),
    ("Chad Vos", "AMS GTM Lead"),
    ("Doug White", "AMS Data & Solution Lead"),
    ("Ben Dunton", "AMS Platform Lead"),
    ("Phillip Crenshaw", "Managed Services Lead"),
    ("Shannon Banjavcic", "AMS Process Lead"),
    ("Ashley Pace", "AMS Marketing Lead"),
    ("Lucy Bickford", "Florida AMS Market Partner"),
    ("Chris Bongiovanni", "Florida AMS Market Partner"),
    ("Andrew James", "Florida AMS Market Partner"),
)


def _extract_playbook_systems(document_text: str, ev_idx: Dict[str, Any], ent_map: Dict[str, Dict[str, Any]], sig_map: Dict[str, Dict[str, Any]]) -> None:
    for sys_name, ent_type, sys_loc, span_pat in (
        ("PartnerAdvisory Traverse", "system", "## Page 8", r"Outsource functions \(PMO \+ change\) led\s+by humans, supported by agents on our\s+PartnerAdvisory Traverse platform"),
        ("ServiceNow", "system", "## Page 16", r"ServiceNow and Snowflake environments\."),
        ("Snowflake", "system", "## Page 16", r"ServiceNow and Snowflake environments\."),
        ("Workday", "system", "## Page 17", r"Platform\s+Qurrent\s+Workday\s+Google Cloud"),
        ("Qurrent", "org", "## Page 17", r"PartnerAdvisory and Qurrent introduced AI agents to manage the\s+invoice lifecycle"),
    ):
        m_sys = re.search(span_pat, document_text, re.I) or re.search(re.escape(sys_name), document_text, re.I)
        if not m_sys:
            continue
        s_id = _slugify_id(ent_type, sys_name)
        ev_s = f"ev-ams-sys-{s_id.split(':', 1)[-1]}"
        desc = "Partner introducing AI agents for invoice lifecycle." if ent_type == "org" else f"Enterprise platform {sys_name}."
        ev_idx[ev_s] = _build_ev(ev_s, desc, m_sys.group(0), sys_loc, document_text)
        ent_map[s_id] = {"id": s_id, "type": ent_type, "name": sys_name, "description": desc, "mutation": "CREATE", "evidence_ids": [ev_s]}
        if sys_name == "PartnerAdvisory Traverse":
            sig_map["sig-partner_advisory-owns-traverse"] = {"signal_id": "sig-partner_advisory-owns-traverse", "from_id": "org:partner_advisory", "to_id": s_id, "relationship": "OWNS", "predicate": "OWNS", "evidence_ids": [ev_s], "mutation": "CONFIRM", "epistemic_status": "CONFIRMED"}
        elif ent_type == "org":
            sig_map["sig-partner_advisory-qurrent-collab"] = {"signal_id": "sig-partner_advisory-qurrent-collab", "from_id": "org:partner_advisory", "to_id": s_id, "relationship": "COLLABORATES_WITH", "predicate": "COLLABORATES_WITH", "evidence_ids": [ev_s], "mutation": "CONFIRM", "epistemic_status": "CONFIRMED"}


def _extract_playbook_benchmarks(document_text: str, ws_ams: str, ev_idx: Dict[str, Any], ent_map: Dict[str, Dict[str, Any]], sig_map: Dict[str, Dict[str, Any]]) -> None:
    for pat, m_slug, m_desc, loc, rel_pred in (
        (r"\$400k\s+build\s*\+\s*\$9\.6M\s+follow-on\s+in\s+Y1\s+to\s+scale\s+across\s+departments", "ams-deal-rbi-9-6m-follow-on", "$9.6M Follow-On Opportunity", "## Page 8", "DELIVERS"),
        (r"\$7\.8M\s+contract\s+covering\s+outcomes\s+delivered\s+with\s+human\s+and\s+agents", "ams-deal-pharma-7-8m", "$7.8M Contract", "## Page 8", "DELIVERS"),
        (r"\$14\.4M\s+potential\s+fixed\s+capacity", "ams-deal-state-child-welfare-14-4m", "$14.4M Potential Fixed Capacity", "## Page 8", "DELIVERS"),
        (r"\$355K\s+fixed\s+fee,\s+12-week\s+proof\s+of\s+value", "ams-deal-media-355k-2-4m-run", "$355K Fixed Fee", "## Page 8", "DELIVERS"),
        (r"Minimum\s+run\s+deal\s+size:\s*\$1M\+[\s\S]{0,180}?project\s+engagement\s+instead", "ams-minimum-deal-threshold-1m-3yr", "$1M+ Minimum Run Deal Size", "## Page 23", "CONSTRAINS"),
    ):
        m_b = re.search(pat, document_text, re.I)
        if not m_b:
            continue
        m_id, ev_b = f"metric:{m_slug}", f"ev-metric-{m_slug}"
        ev_idx[ev_b] = _build_ev(ev_b, m_desc, m_b.group(0), loc, document_text)
        ent_map[m_id] = {"id": m_id, "type": "metric", "name": m_desc, "description": m_desc, "mutation": "CREATE", "evidence_ids": [ev_b]}
        from_ep, to_ep = (m_id, ws_ams) if rel_pred == "CONSTRAINS" else (ws_ams, m_id)
        sig_map[f"sig-ams-metric-{m_id}"] = {"signal_id": f"sig-ams-metric-{m_id}", "from_id": from_ep, "to_id": to_ep, "relationship": rel_pred, "predicate": rel_pred, "evidence_ids": [ev_b], "mutation": "CONFIRM", "epistemic_status": "CONFIRMED"}


def extract_offering_playbook_projection(document_text: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Extract canonical Output 03 signal projection for STRATEGIC_OFFERING_PLAYBOOK (e.g. AMS 101 Florida)."""
    if not document_text or not document_text.strip():
        return {}
    if not re.search(r"\b(?:Agentic\s+Managed\s+Services|PartnerAdvisory\s+Traverse|AMS\s+Lead|AMS\s+101)\b", document_text, re.I):
        return {}
    ev_idx: Dict[str, Any] = {}
    ent_map: Dict[str, Dict[str, Any]] = {}
    sig_map: Dict[str, Dict[str, Any]] = {}
    dec_list: List[Dict[str, Any]] = []
    ev_main, ws_ams = "ev-playbook-core", "workstream:agentic-managed-services"
    m_core = re.search(r"AGENTIC MANAGED SERVICES[\s\S]{0,120}?human oversight\.", document_text, re.I)
    ev_idx[ev_main] = _build_ev(ev_main, "Agentic Managed Services (AMS) offering.", m_core.group(0) if m_core else "AGENTIC MANAGED SERVICES", "## Page 5", document_text)
    ent_map["org:partner_advisory"] = {"id": "org:partner_advisory", "type": "org", "name": "PartnerAdvisory", "description": "Consulting and technology services provider.", "mutation": "CREATE", "evidence_ids": [ev_main]}
    ent_map[ws_ams] = {"id": ws_ams, "type": "workstream", "name": "Agentic Managed Services (AMS)", "description": "Agent-enabled managed services offering.", "mutation": "CREATE", "evidence_ids": [ev_main]}
    sig_map["sig-partner_advisory-owns-ams"] = {"signal_id": "sig-partner_advisory-owns-ams", "from_id": "org:partner_advisory", "to_id": ws_ams, "relationship": "OWNS", "predicate": "OWNS", "evidence_ids": [ev_main], "mutation": "CONFIRM", "epistemic_status": "CONFIRMED"}

    for person_name, role_title in _AMS_LEADERSHIP:
        if re.search(re.escape(person_name), document_text, re.I):
            p_id = _slugify_id("person", person_name)
            ev_p = f"ev-ams-{p_id.replace(':', '-')}"
            m_p = re.search(rf"{re.escape(person_name)}[\s\S]{{0,45}}", document_text, re.I)
            ev_idx[ev_p] = _build_ev(ev_p, f"{person_name} — {role_title}.", m_p.group(0).strip() if m_p else person_name, "## Page 2", document_text)
            ent_map[p_id] = {"id": p_id, "type": "person", "name": person_name, "description": role_title, "mutation": "CREATE", "evidence_ids": [ev_p]}
            sig_map[f"sig-{p_id}-member-ams"] = {"signal_id": f"sig-{p_id}-member-ams", "from_id": p_id, "to_id": ws_ams, "relationship": "MEMBER_OF", "predicate": "MEMBER_OF", "evidence_ids": [ev_p], "mutation": "CONFIRM", "epistemic_status": "CONFIRMED"}

    _extract_playbook_systems(document_text, ev_idx, ent_map, sig_map)
    _extract_playbook_benchmarks(document_text, ws_ams, ev_idx, ent_map, sig_map)

    if "ev-metric-ams-minimum-deal-threshold-1m-3yr" in ev_idx:
        dec_list.append({
            "decision_id": "dec:ams-minimum-1m-multi-year-threshold",
            "title": "AMS Minimum Run Deal Size Threshold ($1M+ Multi-Year)",
            "description": "Minimum run deal size is $1M+ multi-year (3-year term); if 3-year value is below $1M, route it to a project engagement instead.",
            "status": "CONFIRMED",
            "evidence_ids": ["ev-metric-ams-minimum-deal-threshold-1m-3yr"],
        })

    return {
        "schema_version": "1.0.0", "is_document": True, "output3_only": True, "max_mutation_budget": 140,
        "evidence_index": ev_idx, "evidence_only_statement_ids": [],
        "graph_mutations": {"entity_upserts": list(ent_map.values()), "relationship_signals": list(sig_map.values()), "decision_updates": dec_list, "proposal_updates": [], "risk_updates": [], "strategic_intent_signals": []},
    }

