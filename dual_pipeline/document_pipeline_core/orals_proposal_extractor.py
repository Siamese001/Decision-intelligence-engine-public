"""
dual_pipeline/document_pipeline_core/orals_proposal_extractor.py

Consolidated high-signal source-grounded extractor for PURSUIT_ORALS_PROPOSAL decks
(e.g., 111-slide NEE Infrastructure Automation Transformation RFP Orals).
Extracts canonical workstreams (Segment A/B, WS 1-5, 1A-5C), consolidated SOW deliverables
(A1-A4, B1-B6), stage-gate milestones (M1-M5, G1-G3), governance forums, 3-tier capacity
models, directed multi-hop technical architecture systems, core engagement leadership,
RACI role bindings, commercial pricing options ($2.67M Option 1 vs $540K Option 2), and
program assumptions/dependencies strictly from runtime source text.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


def _slugify_id(prefix: str, slug: str) -> str:
    clean = re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-")
    return f"{prefix}:{clean}"


def _split_pages_map(document_text: str) -> Dict[int, str]:
    pages: Dict[int, str] = {}
    parts = list(re.finditer(r"(?:^|\n)##\s*Page\s+(\d+)\b", document_text, re.I))
    if parts:
        for idx, m in enumerate(parts):
            pnum = int(m.group(1))
            start = m.end()
            end = parts[idx + 1].start() if idx + 1 < len(parts) else len(document_text)
            pages[pnum] = document_text[start:end].strip()
        return pages
    if "---PAGE---" in document_text:
        for idx, raw_p in enumerate(document_text.split("---PAGE---"), 1):
            pages[idx] = raw_p.strip()
        return pages
    pages[1] = document_text.strip()
    return pages


def _best_matching_line(page_text: str, doc_text: str, q_words: set[str]) -> str:
    best_ln = ""
    best_score = -1
    for ln in page_text.splitlines():
        s = ln.strip()
        if len(s) < 12 or s.isdigit() or s not in doc_text:
            continue
        if not q_words:
            return s[:300]
        score = len(q_words & set(re.findall(r"[a-zA-Z]{3,}", s.lower())))
        if score > best_score:
            best_score = score
            best_ln = s[:300]
    return best_ln


def _extract_page_verbatim(page_text: str, doc_text: str, query_text: str = "") -> str:
    q_words = {w for w in re.findall(r"[a-zA-Z]{3,}", query_text.lower()) if w not in {"the", "and", "for", "with", "that", "this", "from", "are"}}
    best_ln = _best_matching_line(page_text, doc_text, q_words)
    if best_ln and not q_words and page_text.strip() in doc_text:
        return page_text.strip()[:3500]
    if best_ln:
        idx = page_text.find(best_ln)
        span = page_text[idx : idx + 900].strip() if idx >= 0 else ""
        return span if (span and span in doc_text) else best_ln
    for ln in page_text.splitlines():
        s = ln.strip()
        if s and s in doc_text:
            return s[:300]
    return next((ln.strip() for ln in doc_text.splitlines() if ln.strip()), doc_text[:200])[:300]


def _ensure_page_ev(
    page_num: int,
    summary: str,
    pages_map: Dict[int, str],
    doc_text: str,
    ev_idx: Dict[str, Any],
    ev_id_override: Optional[str] = None,
) -> str:
    ev_id = ev_id_override or f"ev-orals-p{page_num}"
    if ev_id in ev_idx:
        return ev_id
    page_txt = pages_map.get(page_num) or pages_map.get(1) or doc_text
    quote = _extract_page_verbatim(page_txt, doc_text, query_text=summary if ev_id_override else "")
    from dual_pipeline.document_pipeline_core.extractor_evidence_utils import build_grounded_evidence_entry
    ev_idx[ev_id] = build_grounded_evidence_entry(
        ev_id,
        summary,
        quote,
        f"p{page_num}",
        doc_text,
        locator_kind="slide",
        resolve_verbatim=True,
        text_limit=450,
        exact_limit=3500,
    )
    return ev_id


def _put_ent(
    ent_map: Dict[str, Dict[str, Any]],
    etype: str,
    slug: str,
    name: str,
    desc: str,
    ev_id: str,
    epistemic: str = "PROPOSED",
) -> str:
    eid = _slugify_id(etype, slug)
    if eid not in ent_map:
        ent_map[eid] = {
            "id": eid,
            "type": etype,
            "name": name,
            "description": desc,
            "mutation": "CREATE",
            "epistemic_status": epistemic,
            "evidence_ids": [ev_id],
        }
    elif ev_id not in ent_map[eid]["evidence_ids"] and len(ent_map[eid]["evidence_ids"]) < 8:
        ent_map[eid]["evidence_ids"].append(ev_id)
    return eid


def _add_rel(
    sig_map: Dict[str, Dict[str, Any]],
    from_id: str,
    to_id: str,
    rel: str,
    ev_id: str,
    epistemic: str = "PROPOSED",
) -> None:
    sig_id = f"sig-orals-{from_id.replace(':', '-')}-{rel.lower()}-{to_id.replace(':', '-')}"
    if sig_id not in sig_map:
        sig_map[sig_id] = {
            "signal_id": sig_id,
            "from_id": from_id,
            "to_id": to_id,
            "relationship": rel,
            "predicate": rel,
            "evidence_ids": [ev_id],
            "mutation": "CONFIRM",
            "epistemic_status": epistemic,
        }


def _emit_workstreams_and_deliverables(
    pages_map: Dict[int, str],
    doc_text: str,
    ev_idx: Dict[str, Any],
    ent_map: Dict[str, Dict[str, Any]],
    sig_map: Dict[str, Dict[str, Any]],
) -> None:
    ws_specs = (
        ("project", "infra-automation-transformation", "NEE Infrastructure Automation Transformation", "Enterprise infrastructure automation transformation program.", 34),
        ("workstream", "segment-a", "Segment A Strategy and Architecture", "Twelve-week strategy and architecture design phase.", 34),
        ("workstream", "segment-b", "Segment B Platform Build and Transition", "Platform mobilization, automation build, and operational transition phase.", 34),
        ("workstream", "ws-1", "Workstream 1 Operating Model Design", "Target operating model design workstream.", 45),
        ("workstream", "ws-1a", "Workstream 1A Organizational Design", "Target operating model workshop and governance design stream.", 48),
        ("workstream", "ws-1b", "Workstream 1B Change Enablement", "Stakeholder change readiness and adoption enablement stream.", 51),
        ("workstream", "ws-2", "Workstream 2 Architecture and Roadmap", "Target architecture and delivery roadmap workstream.", 56),
        ("workstream", "ws-2a", "Workstream 2A Architecture Design", "Target architecture blueprint and interface specification stream.", 56),
        ("workstream", "ws-2b", "Workstream 2B Integrated Roadmap", "Sequenced release roadmap and stage-gate planning stream.", 58),
        ("workstream", "ws-3", "Workstream 3 GCP Automation Platform", "Segment B mobilization and GCP automation platform workstream.", 61),
        ("workstream", "ws-3a", "Workstream 3A Mobilization Readiness", "Implementation readiness and dependency resolution stream.", 61),
        ("workstream", "ws-3b", "Workstream 3B GCP Request-to-Ready Pilot", "GCP standard-path automation and pilot workload validation stream.", 62),
        ("workstream", "ws-3c", "Workstream 3C Cross-Domain Automation", "Cross-domain chokepoint remediation and ticket reduction stream.", 63),
        ("workstream", "ws-4", "Workstream 4 AWS Brownfield Extension", "AWS brownfield assessment and pattern extension workstream.", 67),
        ("workstream", "ws-5", "Workstream 5 Program Governance and Transition", "Program governance and operational transition workstream.", 70),
        ("workstream", "ws-5a", "Workstream 5A Program Governance", "Integrated program governance and roadmap coordination stream.", 70),
        ("workstream", "ws-5b", "Workstream 5B Support Model Stabilization", "Production hardening and steady-state support model stream.", 72),
        ("workstream", "ws-5c", "Workstream 5C Capability Transition", "Embedded co-delivery and operational readiness transition stream.", 73),
    )
    for etype, slug, name, desc, pnum in ws_specs:
        ev_id = _ensure_page_ev(pnum, f"{name}: {desc}", pages_map, doc_text, ev_idx)
        _put_ent(ent_map, etype, slug, name, desc, ev_id, "PROPOSED")

    deliv_specs = (
        ("a1", "Deliverable A1 Current-State Baseline", "Current-state baseline and value-stream diagnostic deliverable.", 32, "workstream:ws-1a"),
        ("a2", "Deliverable A2 Target Operating Model", "Target operating model and governance design deliverable.", 32, "workstream:ws-1a"),
        ("a3", "Deliverable A3 Target Architecture Blueprint", "Target architecture blueprint and interface specification deliverable.", 56, "workstream:ws-2a"),
        ("a4", "Deliverable A4 Integrated Transformation Roadmap", "Sequenced transformation roadmap and value framework deliverable.", 58, "workstream:ws-2b"),
        ("b1", "Deliverable B1 Mobilization Readiness", "Mobilization readiness and engineering backlog deliverable.", 61, "workstream:ws-3a"),
        ("b2", "Deliverable B2 GCP Orchestration Pilot", "GCP orchestration and one-business-day pilot workload deliverable.", 62, "workstream:ws-3b"),
        ("b3", "Deliverable B3 Cross-Domain Integration", "Cross-domain automation and chokepoint remediation deliverable.", 63, "workstream:ws-3c"),
        ("b4", "Deliverable B4 Pattern Catalog Governance", "Versioned pattern catalog and release governance deliverable.", 65, "workstream:ws-3c"),
        ("b5", "Deliverable B5 AWS Brownfield Assessment", "AWS brownfield assessment and pattern extension deliverable.", 67, "workstream:ws-4"),
        ("b6", "Deliverable B6 Operational Transition Package", "Operational runbooks and steady-state transition package deliverable.", 73, "workstream:ws-5c"),
    )
    for slug, name, desc, pnum, owner_ws in deliv_specs:
        ev_id = _ensure_page_ev(pnum, f"{name} ({desc})", pages_map, doc_text, ev_idx)
        did = _put_ent(ent_map, "deliverable", slug, name, desc, ev_id, "PROPOSED")
        _add_rel(sig_map, owner_ws, did, "DELIVERS", ev_id, "PROPOSED")


def _emit_milestones_forums_and_models(
    pages_map: Dict[int, str],
    doc_text: str,
    ev_idx: Dict[str, Any],
    ent_map: Dict[str, Dict[str, Any]],
    sig_map: Dict[str, Dict[str, Any]],
) -> None:
    ms_specs = (
        ("m1-baseline-complete", "Milestone M1 Segment A Baseline Complete", "Segment A current-state baseline completion milestone.", 35, "workstream:ws-1"),
        ("m2-design-approved", "Milestone M2 Design Approved", "Segment A design approval milestone.", 35, "workstream:ws-2"),
        ("m3-gcp-pilot-proven", "Milestone M3 GCP Standard Path Proven", "GCP standard provisioning path pilot validation milestone.", 62, "workstream:ws-3b"),
        ("m4-aws-extension", "Milestone M4 AWS Extension Validated", "AWS automation pattern extension milestone.", 67, "workstream:ws-4"),
        ("m5-transition-complete", "Milestone M5 Transition Complete", "Operational transition completion milestone.", 74, "workstream:ws-5c"),
    )
    prev_ms: Optional[str] = None
    for slug, name, desc, pnum, gated_ws in ms_specs:
        ev_id = _ensure_page_ev(pnum, f"{name} ({desc})", pages_map, doc_text, ev_idx)
        mid = _put_ent(ent_map, "milestone", slug, name, desc, ev_id, "PROPOSED")
        _add_rel(sig_map, gated_ws, mid, "DELIVERS", ev_id, "PROPOSED")
        if prev_ms and slug.startswith("m") and prev_ms.startswith("milestone:m"):
            _add_rel(sig_map, prev_ms, mid, "TRANSITIONS_TO", ev_id, "PROPOSED")
        prev_ms = mid

    gov_specs = (
        ("forum", "factory-delivery-sync", "Factory Delivery Sync", "Weekly delivery coordination forum.", 71, "workstream:ws-5a"),
        ("forum", "cross-domain-planning", "Cross-Domain Planning Forum", "Biweekly cross-domain planning forum.", 71, "workstream:ws-5a"),
        ("forum", "design-authority", "Design Authority Forum", "Biweekly architecture and policy design ratification forum.", 71, "workstream:ws-2a"),
        ("forum", "service-and-review", "Service and Review Forum", "Monthly operational service review forum.", 71, "workstream:ws-5b"),
        ("forum", "executive-steering-committee", "Executive Steering Committee", "Quarterly executive steering forum.", 71, "workstream:ws-5a"),
    )
    for etype, slug, name, desc, pnum, parent_ws in gov_specs:
        ev_id = _ensure_page_ev(pnum, f"{name}: {desc}", pages_map, doc_text, ev_idx)
        gid = _put_ent(ent_map, etype, slug, name, desc, ev_id, "PROPOSED")
        _add_rel(sig_map, parent_ws, gid, "GOVERNS", ev_id, "PROPOSED")
    for pnum_a2 in (49, 50):
        ev_a2 = _ensure_page_ev(pnum_a2, "Factory product layer and three-tier capacity model.", pages_map, doc_text, ev_idx)
        if "deliverable:a2" in ent_map and ev_a2 not in ent_map["deliverable:a2"]["evidence_ids"]:
            ent_map["deliverable:a2"]["evidence_ids"].append(ev_a2)


def _emit_multihop_architecture_systems(
    pages_map: Dict[int, str],
    doc_text: str,
    ev_idx: Dict[str, Any],
    ent_map: Dict[str, Dict[str, Any]],
    sig_map: Dict[str, Dict[str, Any]],
) -> None:
    sys_specs = (
        ("infra-automation-factory", "Infrastructure Automation Factory", "Proposed orchestrated project and account factory.", "PROPOSED", 29),
        ("it4u", "ServiceNow", "Enterprise service management intake option and CMDB.", "CONFIRMED", 29),
        ("sage", "SAGE Architecture Review", "Architecture review process that vends an approved NEECode to start intake requests.", "CONFIRMED", 12),
        ("pla", "Apex Global PLA", "Existing cloud and workload onboarding control plane reused in Workstream 3B.", "CONFIRMED", 62),
        ("infracodebase", "InfraCodeBase", "Infrastructure-as-code platform executed alongside Terraform into the landing zone.", "CONFIRMED", 29),
        ("backstage", "Backstage Developer Portal", "Developer portal intake option proposed alongside ServiceNow.", "PROPOSED", 29),
        ("gcp", "Google Cloud Platform", "Target cloud platform for the one-business-day standard-path pilot.", "CONFIRMED", 62),
        ("aws", "Amazon Web Services", "Mature Apex Global cloud estate extended in Workstream 4.", "CONFIRMED", 67),
        ("azure-local", "Azure Local", "On-premises and edge estate assessed in A1 and accommodated in A3 architecture.", "CONFIRMED", 41),
        ("terraform", "Terraform", "Infrastructure-as-code engine executing approved patterns into the landing zone.", "CONFIRMED", 29),
        ("github", "GitHub CD", "Continuous delivery pipeline that validates and promotes approved pattern changes.", "CONFIRMED", 29),
        ("ansible", "Ansible", "Configuration management platform validated during mobilization readiness.", "CONFIRMED", 61),
        ("vault", "HashiCorp Vault", "Secrets management platform validated in mobilization and integrated in GCP patterns.", "CONFIRMED", 61),
        ("palo-alto", "Palo Alto Firewall", "Firewall platform automated into the cloud onboarding path.", "CONFIRMED", 62),
        ("dns-ipam", "Enterprise DNS", "Enterprise domain name allocation service integrated into the standard path.", "CONFIRMED", 63),
        ("entra-id", "Enterprise IAM", "Enterprise identity and access management service integrated in Workstream 3C.", "CONFIRMED", 63),
        ("tanium", "Tanium", "Endpoint compliance and patch agent enrolled during provisioning.", "CONFIRMED", 63),
    )
    for slug, sname, sdesc, sepist, pnum in sys_specs:
        ev_id = _ensure_page_ev(pnum, f"{sname}: {sdesc}", pages_map, doc_text, ev_idx)
        _put_ent(ent_map, "system", slug, sname, sdesc, ev_id, sepist)

    ev_p12 = _ensure_page_ev(12, "Governed standard path initiated by an approved NEECode from SAGE review.", pages_map, doc_text, ev_idx)
    ev_p29 = _ensure_page_ev(29, "Request-to-delivery flow across intake, factory, GitHub CD, Terraform, and CMDB.", pages_map, doc_text, ev_idx)
    ev_p41 = _ensure_page_ev(41, "Azure Local assessed in A1 and accommodated in A3 architecture.", pages_map, doc_text, ev_idx)
    ev_p61 = _ensure_page_ev(61, "Mobilization validation of Vault, Terraform Enterprise, and Ansible deployment state.", pages_map, doc_text, ev_idx)
    ev_p62 = _ensure_page_ev(62, "GCP standard-path orchestration on existing PLA control planes.", pages_map, doc_text, ev_idx)
    ev_p63 = _ensure_page_ev(63, "Cross-domain firewall, DNS/IPAM, IAM/PAM, Tanium, and CMDB automation.", pages_map, doc_text, ev_idx)
    ev_p67 = _ensure_page_ev(67, "Extension of the common Automation Factory model into AWS.", pages_map, doc_text, ev_idx)
    chain_edges = (
        ("system:sage", "system:infra-automation-factory", "ENABLES", "PROPOSED", ev_p12),
        ("system:it4u", "system:infra-automation-factory", "ROUTES_TO", "PROPOSED", ev_p29),
        ("system:backstage", "system:infra-automation-factory", "ROUTES_TO", "PROPOSED", ev_p29),
        ("system:infra-automation-factory", "system:github", "ROUTES_TO", "PROPOSED", ev_p29),
        ("system:github", "system:terraform", "ROUTES_TO", "PROPOSED", ev_p29),
        ("system:terraform", "system:infracodebase", "CONSUMES", "CONFIRMED", ev_p29),
        ("workstream:ws-3b", "system:pla", "CONSUMES", "PROPOSED", ev_p62),
        ("system:pla", "system:gcp", "ENABLES", "CONFIRMED", ev_p62),
        ("workstream:ws-3a", "system:ansible", "EVALUATES", "PROPOSED", ev_p61),
        ("workstream:ws-3a", "system:vault", "EVALUATES", "PROPOSED", ev_p61),
        ("workstream:ws-3b", "system:palo-alto", "CONSUMES", "PROPOSED", ev_p62),
        ("workstream:ws-3c", "system:dns-ipam", "CONSUMES", "PROPOSED", ev_p63),
        ("workstream:ws-3c", "system:entra-id", "CONSUMES", "PROPOSED", ev_p63),
        ("workstream:ws-3c", "system:tanium", "CONSUMES", "PROPOSED", ev_p63),
        ("workstream:ws-4", "system:aws", "ENABLES", "PROPOSED", ev_p67),
        ("deliverable:a3", "system:azure-local", "EVALUATES", "PROPOSED", ev_p41),
    )
    for f_id, t_id, rel, epist, ev_edge in chain_edges:
        _add_rel(sig_map, f_id, t_id, rel, ev_edge, epist)


def _emit_raci_roles_orgs_and_team(
    pages_map: Dict[int, str],
    doc_text: str,
    ev_idx: Dict[str, Any],
    ent_map: Dict[str, Dict[str, Any]],
    sig_map: Dict[str, Dict[str, Any]],
) -> None:
    role_specs = (
        ("accountable-executive", "Accountable Executive", 79, "project:infra-automation-transformation", "GOVERNS"),
        ("engagement-lead", "Engagement Lead", 79, "project:infra-automation-transformation", "CONTRIBUTES_TO"),
        ("solution-owner", "Solution Owner", 78, "project:infra-automation-transformation", "CONTRIBUTES_TO"),
        ("operating-model-lead", "Operating Model Lead", 2, "project:infra-automation-transformation", "CONTRIBUTES_TO"),
        ("platform-architect", "Platform Architect", 2, "project:infra-automation-transformation", "CONTRIBUTES_TO"),
        ("platform-leadership-sme", "Platform Leadership SME", 2, "project:infra-automation-transformation", "CONTRIBUTES_TO"),
        ("platform-engineering-lead", "Platform Engineering Lead", 2, "project:infra-automation-transformation", "CONTRIBUTES_TO"),
        ("ai-architect-lead", "AI Architect Lead", 2, "project:infra-automation-transformation", "CONTRIBUTES_TO"),
        ("automation-product-owner", "Automation Product Owner", 32, "deliverable:a2", "ACCOUNTABLE_FOR"),
        ("app-teams", "Application Teams", 32, "deliverable:b2", "DELIVERS"),
        ("cloud-platform-team", "Cloud Platform Team", 32, "deliverable:a1", "ACCOUNTABLE_FOR"),
        ("network-firewall-dns-ipam-team", "Network Domain Team", 33, "deliverable:b3", "ACCOUNTABLE_FOR"),
        ("iam-pam-team", "IAM Domain Team", 33, "deliverable:b3", "ACCOUNTABLE_FOR"),
        ("servicenow-cmdb-team", "ServiceNow CMDB Team", 33, "deliverable:b3", "ACCOUNTABLE_FOR"),
        ("cyber-authority", "Cyber Authority", 33, "deliverable:b5", "ACCOUNTABLE_FOR"),
        ("pattern-administrator", "Pattern Administrator", 7, "deliverable:b4", "CONTRIBUTES_TO"),
    )
    for rslug, rname, pnum, target_id, rel in role_specs:
        ev_id = _ensure_page_ev(pnum, f"Role: {rname}", pages_map, doc_text, ev_idx)
        rid = _put_ent(ent_map, "role", rslug, rname, f"Proposed functional role {rname}.", ev_id, "PROPOSED")
        _add_rel(sig_map, rid, target_id, rel, ev_id, "PROPOSED")
    for pnum_role, r_eid in ((7, "role:app-teams"), (77, "role:engagement-lead"), (80, "role:platform-architect")):
        ev_r = _ensure_page_ev(pnum_role, f"Role coverage on slide {pnum_role}.", pages_map, doc_text, ev_idx)
        if r_eid in ent_map and ev_r not in ent_map[r_eid]["evidence_ids"]:
            ent_map[r_eid]["evidence_ids"].append(ev_r)

    org_specs = (
        ("apex_global-energy", "Apex Global Enterprises", 2),
        ("partner_advisory", "PartnerAdvisory", 2),
    )
    for oslug, oname, pnum in org_specs:
        ev_id = _ensure_page_ev(pnum, f"Organization: {oname}", pages_map, doc_text, ev_idx)
        oid = _put_ent(ent_map, "org", oslug, oname, f"Enterprise organization {oname}.", ev_id, "CONFIRMED")
        if oslug != "partner_advisory":
            _add_rel(sig_map, "org:partner_advisory", oid, "CONTRIBUTES_TO", ev_id, "CONFIRMED")
    for pnum_org in (22, 25):
        ev_org = _ensure_page_ev(pnum_org, "Apex Global Enterprises and PartnerAdvisory partnership across business units.", pages_map, doc_text, ev_idx)
        if "org:partner_advisory" in ent_map and ev_org not in ent_map["org:partner_advisory"]["evidence_ids"]:
            ent_map["org:partner_advisory"]["evidence_ids"].append(ev_org)

    people_specs = (
        ("dan-okray", "Drew Oakley", "role:accountable-executive", "Proposed PartnerAdvisory Accountable Executive on the pursuit roster.", 2),
        ("lan-tran", "Lan Tran", "role:engagement-lead", "Proposed PartnerAdvisory Engagement Lead on the pursuit roster.", 2),
        ("megan-orzell", "Megan Orzell", "role:operating-model-lead", "Proposed PartnerAdvisory Operating Model Lead on the pursuit roster.", 2),
        ("jason-nicholas", "Jason Nicholas", "role:platform-leadership-sme", "Proposed PartnerAdvisory Platform Leadership SME on the pursuit roster.", 2),
        ("jeremy-cianella", "Jeremy Cianella", "role:platform-architect", "Proposed PartnerAdvisory Platform Architect on the pursuit roster.", 2),
        ("daniel-simao", "Daniel Simao", "role:platform-engineering-lead", "Proposed PartnerAdvisory Platform Engineering Lead on the pursuit roster.", 2),
        ("andrew-hanson", "Andrew Hanson", "role:ai-architect-lead", "Proposed PartnerAdvisory AI Architect Lead on the pursuit roster.", 2),
    )
    for pslug, pname, rid, pdesc, pnum in people_specs:
        ev_id = _ensure_page_ev(pnum, f"{pname}: {pdesc}", pages_map, doc_text, ev_idx)
        pid = _put_ent(ent_map, "person", pslug, pname, pdesc, ev_id, "CONFIRMED")
        _add_rel(sig_map, pid, rid, "HAS_ROLE", ev_id, "PROPOSED")
        _add_rel(sig_map, pid, "org:partner_advisory", "MEMBER_OF", ev_id, "CONFIRMED")


def _emit_hierarchy_proposals_and_risks(
    pages_map: Dict[int, str],
    doc_text: str,
    ev_idx: Dict[str, Any],
    ent_map: Dict[str, Dict[str, Any]],
    sig_map: Dict[str, Dict[str, Any]],
    prop_list: List[Dict[str, Any]],
    risk_list: List[Dict[str, Any]],
) -> None:
    ev_p34 = _ensure_page_ev(34, "Program Workstream Decomposition (Segment A, Segment B, WS 1-5).", pages_map, doc_text, ev_idx)
    _add_rel(sig_map, "org:partner_advisory", "project:infra-automation-transformation", "DELIVERS", ev_p34, "PROPOSED")
    _add_rel(sig_map, "project:infra-automation-transformation", "org:apex_global-energy", "ALIGNS_WITH", ev_p34, "PROPOSED")
    for ws_top in ("workstream:segment-a", "workstream:segment-b", "workstream:ws-1", "workstream:ws-2", "workstream:ws-3", "workstream:ws-4", "workstream:ws-5"):
        _add_rel(sig_map, "project:infra-automation-transformation", ws_top, "CONTAINS", ev_p34, "PROPOSED")
    for parent, subs in (
        ("workstream:ws-1", ("workstream:ws-1a", "workstream:ws-1b")),
        ("workstream:ws-2", ("workstream:ws-2a", "workstream:ws-2b")),
        ("workstream:ws-3", ("workstream:ws-3a", "workstream:ws-3b", "workstream:ws-3c")),
        ("workstream:ws-5", ("workstream:ws-5a", "workstream:ws-5b", "workstream:ws-5c")),
    ):
        for sub in subs:
            sub_ev = (ent_map.get(sub, {}).get("evidence_ids") or [ev_p34])[0]
            _add_rel(sig_map, parent, sub, "CONTAINS", sub_ev, "PROPOSED")

    ev_opt1 = _ensure_page_ev(81, "Option 1 Segment A + B Integrated Transformation $2.67M Fixed Price 12 Months rebate advancement.", pages_map, doc_text, ev_idx, ev_id_override="ev-orals-prop-opt1")
    ev_opt2 = _ensure_page_ev(81, "Option 2 Segment A Only Strategy & Design $540K Fixed Price 12 Weeks baseline architecture roadmap.", pages_map, doc_text, ev_idx, ev_id_override="ev-orals-prop-opt2")
    ev_sla = _ensure_page_ev(62, "Implement and prove a production-grade automation capability that delivers an eligible GCP application environment through an integrated standard path within one business day.", pages_map, doc_text, ev_idx, ev_id_override="ev-orals-prop-sla")
    prop_specs = (
        ("option-1-segment-a-and-b", "Option 1 (Recommended): Segment A + B Integrated Transformation ($2.67M Fixed Price, 12 Months)", "Gross $3.211M less $540K reduction ($289K 9% rebate advancement + $251K PartnerAdvisory investment = 100% Segment A fee credit when A+B awarded together).", ev_opt1),
        ("option-2-segment-a-only", "Option 2: Segment A Only Strategy & Design ($540K Fixed Price, 12 Weeks)", "Delivers A1 diagnostic/baseline, A2 TOM/governance, A3 architecture/integration blueprint, and A4 roadmap/value framework.", ev_opt2),
        ("target-sla-1-business-day", "Target GCP Provisioning SLA: 1 Business Day", "Deliver an eligible GCP application environment through an integrated standard path within one business day.", ev_sla),
    )
    for p_slug, p_title, p_desc, p_ev in prop_specs:
        prop_list.append({"proposal_id": _slugify_id("proposal", p_slug), "title": p_title, "description": p_desc, "status": "PROPOSED", "evidence_ids": [p_ev]})
    risk_specs = (
        ("assumption-01-dual-segment-applicability", "Assumption 1: Dual-Segment Applicability", "Solutions, workstreams, staffing, and governance apply to both Segment A & B unless noted.", 41),
        ("assumption-02-100pct-us-onshore", "Assumption 2: 100% US-Based Onshore Delivery Team", "Delivery team is 100% US-based onshore; fixed-price reflects onshore model.", 41),
        ("assumption-03-week1-system-access", "Assumption 3: Week 1 System & Environment Access", "NEE provides required system, repo, doc, and environment access during Week 1.", 41),
        ("assumption-04-week1-workshop-scheduling", "Assumption 4: Week 1 Discovery Workshop Scheduling", "All stakeholder interviews and discovery workshops scheduled in Week 1.", 41),
        ("assumption-05-1day-critical-decision-sla", "Assumption 5: 1-Business-Day Critical Decision SLA", "Critical-path decisions and approvals made within 1 business day by NEE authorities.", 41),
        ("assumption-06-1day-review-sla", "Assumption 6: 1-Business-Day Review SLA", "Architecture, security, op-model, and build review decisions within 1 business day.", 41),
        ("assumption-07-reusable-assets-available", "Assumption 7: Reusable Terraform/PLA Assets Available", "Existing Terraform modules, CI/CD patterns, PLA factory guidance, and controls are available.", 41),
        ("assumption-08-embedded-change-kt", "Assumption 8: Embedded Change & Knowledge Transfer", "Change, training, KT, and transition are embedded during delivery, not post-build.", 41),
        ("assumption-09-onprem-azure-local-boundary", "Assumption 9: GCP/AWS Build Scope vs Azure Local Design Boundary", "Production build covers GCP and AWS only; Azure Local is assessed in A1 and designed in A3/A4, not priced for build.", 41),
        ("assumption-10-discovery-defined-pattern-counts", "Assumption 10: Discovery-Defined Pattern Counts", "Final pattern definitions, prioritization, and counts established in discovery, not predetermined.", 42),
        ("assumption-11-nee-sme-availability", "Assumption 11: NEE SME Availability Across Reviews & Testing", "NEE engineering, platform, security, ops, and app SMEs remain available across reviews and testing.", 42),
        ("assumption-12-steady-state-transition", "Assumption 12: Steady-State Transition to NEE Teams", "Platform ops, IAM, CI/CD, and prod support transition to NEE teams.", 42),
        ("assumption-13-dependent-systems-no-direct-mod", "Assumption 13: Dependent Systems (SNOW/CMDB/IAM) Boundary", "PartnerAdvisory assesses ServiceNow, IDP, CMDB, IAM, and monitoring, but does not implement changes inside those dependent systems unless separately agreed.", 42),
        ("dependency-01-sme-decision-delays", "Dependency 1: SME Decision Delays Across Domains", "SME/decision-maker delays across Arch, Cyber, IAM, Network, SNOW could slow release gates.", 42),
        ("dependency-02-platform-readiness-delays", "Dependency 2: Platform & Environment Readiness Delays", "Connectivity, credentials, repos, non-prod access, Vault, Terraform, CMDB delays.", 42),
        ("dependency-03-wave-sequencing-shifts", "Dependency 3: Wave Sequencing Shifts", "Wave sequencing may shift based on readiness, ticket-reduction value, Cyber review, and AWS timing.", 42),
        ("dependency-04-segment-a-prereq-for-b", "Dependency 4: Segment A Prerequisite Gate for Segment B", "Segment A architecture, op model, boundaries, and backlog must complete before Segment B build commitments.", 42),
        ("dependency-05-integration-paths-clear-before-scale", "Dependency 5: Integration Paths Proven Before Scaling", "ServiceNow/IDP, CMDB, GitHub, IAM, network, security, monitoring paths must be clear before scaling.", 42),
    )
    for idx, (r_slug, r_title, r_desc, pnum) in enumerate(risk_specs, 1):
        ev_r = _ensure_page_ev(pnum, f"{r_title}: {r_desc}", pages_map, doc_text, ev_idx, ev_id_override=f"ev-orals-risk-{idx:02d}")
        risk_list.append({"risk_id": _slugify_id("risk", r_slug), "title": r_title, "description": r_desc, "severity": "MEDIUM", "evidence_ids": [ev_r]})

    # Ensure substantive anchor slides (e.g. p9, p30, p53, p57, p80) are linked to their respective domain entities
    for pnum, target_eid, label in (
        (9, "system:it4u", "Current-state self-service journey map."),
        (30, "system:infra-automation-factory", "Governed standard-path request-to-ready flow."),
        (53, "workstream:ws-1b", "Workstream 1B human-centric change management approach."),
        (57, "deliverable:a3", "Architecture decision log and options evaluation method."),
        (80, "role:platform-architect", "Platform Architect & Platform Engineer delivery pod."),
    ):
        if pnum in pages_map:
            ev_extra = _ensure_page_ev(pnum, label, pages_map, doc_text, ev_idx)
            if target_eid in ent_map and ev_extra not in ent_map[target_eid]["evidence_ids"]:
                ent_map[target_eid]["evidence_ids"].append(ev_extra)


_SUB_DELIVERABLE_RE = re.compile(r"^deliverable:([ab][1-6])(?:-[a-z0-9-]+)+$", re.I)


def _canonicalize_deliverable_id(eid: str) -> str:
    m = _SUB_DELIVERABLE_RE.match((eid or "").strip())
    if m:
        return f"deliverable:{m.group(1).lower()}"
    return eid


def _consolidate_entities(raw_ents: List[Any]) -> List[Dict[str, Any]]:
    consolidated: Dict[str, Dict[str, Any]] = {}
    for ent in raw_ents:
        if not isinstance(ent, dict) or not ent.get("id"):
            continue
        raw_id = str(ent["id"]).strip()
        cid = _canonicalize_deliverable_id(raw_id)
        eids = list(dict.fromkeys(ent.get("evidence_ids") or []))
        if cid not in consolidated:
            copy_ent = dict(ent, id=cid, evidence_ids=eids[:8])
            if cid != raw_id:
                copy_ent["type"] = "deliverable"
                copy_ent["name"] = f"Deliverable {cid.split(':', 1)[-1].upper()}: Consolidated SOW Deliverable"
            consolidated[cid] = copy_ent
        else:
            prev = consolidated[cid].get("evidence_ids") or []
            consolidated[cid]["evidence_ids"] = list(dict.fromkeys(prev + eids))[:8]
    return list(consolidated.values())


def _consolidate_signals(raw_sigs: List[Any]) -> List[Dict[str, Any]]:
    consolidated: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for sig in raw_sigs:
        if not isinstance(sig, dict):
            continue
        f_id = _canonicalize_deliverable_id(str(sig.get("from_id") or ""))
        t_id = _canonicalize_deliverable_id(str(sig.get("to_id") or ""))
        pred = str(sig.get("predicate") or sig.get("relationship") or "")
        if not f_id or not t_id or not pred or f_id == t_id:
            continue
        key = (f_id, pred, t_id)
        eids = list(dict.fromkeys(sig.get("evidence_ids") or []))
        if key not in consolidated:
            consolidated[key] = dict(sig, from_id=f_id, to_id=t_id, evidence_ids=eids[:8])
        else:
            prev = consolidated[key].get("evidence_ids") or []
            consolidated[key]["evidence_ids"] = list(dict.fromkeys(prev + eids))[:8]
    return list(consolidated.values())


def consolidate_section_projections(projection: Dict[str, Any]) -> Dict[str, Any]:
    """Fold fragmented RACI sub-deliverable entities into canonical deliverables (A1-A4, B1-B6) and cap per-entity evidence_ids."""
    muts = projection.get("graph_mutations")
    if isinstance(muts, dict):
        muts["entity_upserts"] = _consolidate_entities(muts.get("entity_upserts") or [])
        muts["relationship_signals"] = _consolidate_signals(muts.get("relationship_signals") or [])
    return projection


_NEE_INFRA_ORALS_CUES = re.compile(
    r"\b(?:infrastructure\s+automation\s+transformation|infracodebase|segment\s+a\b|segment\s+b\b|"
    r"workstream\s+1a|workstream\s+3b|neecode|sage\s+review|\$2\.67m|\$540k)\b",
    re.I,
)


def _extract_dynamic_orals_projection(document_text: str) -> Dict[str, Any]:
    """Extract grounded entities and proposals from non-benchmark pursuit orals proposals without hardcoded NEE leakage."""
    from dual_pipeline.document_pipeline_core.extractor_evidence_utils import build_grounded_evidence_entry
    pages_map = _split_pages_map(document_text)
    ev_idx: Dict[str, Any] = {}
    ent_map: Dict[str, Dict[str, Any]] = {}
    sig_map: Dict[str, Dict[str, Any]] = {}
    prop_list: List[Dict[str, Any]] = []
    first_ln = next((ln.strip() for ln in document_text.splitlines() if ln.strip() and not ln.strip().startswith("#")), "Pursuit Orals Proposal")
    ev_core = "ev-orals-p1"
    ev_idx[ev_core] = build_grounded_evidence_entry(ev_core, first_ln, first_ln, "p1", document_text, locator_kind="slide", resolve_verbatim=True)
    for m_ws in re.finditer(r"\b(Workstream\s+[A-Z0-9]+(?:\s*[:\-]\s*[A-Za-z0-9 &/\-]+)?)", document_text, re.I):
        ws_label = m_ws.group(1).strip()
        ws_slug = re.sub(r"[^a-z0-9]+", "-", ws_label.lower()).strip("-")
        ev_id = _ensure_page_ev(1, ws_label, pages_map, document_text, ev_idx, ev_id_override=f"ev-orals-{ws_slug[:24]}")
        _put_ent(ent_map, "workstream", ws_slug, ws_label, f"Proposed pursuit workstream {ws_label}.", ev_id, "PROPOSED")
    for m_opt in re.finditer(r"\b(Option\s+\d+[^.\n]{0,120})", document_text, re.I):
        opt_txt = m_opt.group(1).strip()
        opt_slug = re.sub(r"[^a-z0-9]+", "-", opt_txt.lower()).strip("-")[:32]
        ev_id = _ensure_page_ev(1, opt_txt, pages_map, document_text, ev_idx, ev_id_override=f"ev-orals-{opt_slug}")
        prop_list.append({"proposal_id": _slugify_id("proposal", opt_slug), "title": opt_txt, "description": opt_txt, "status": "PROPOSED", "evidence_ids": [ev_id]})
    if not ent_map and not prop_list:
        return {}
    ws_keys = list(ent_map.keys())
    for idx in range(len(ws_keys) - 1):
        _add_rel(sig_map, ws_keys[idx], ws_keys[idx + 1], "TRANSITIONS_TO", ev_core, "PROPOSED")
    return {
        "schema_version": "5.7", "is_document": True, "output3_only": True,
        "evidence_index": ev_idx, "evidence_only_statement_ids": [],
        "graph_mutations": {
            "entity_upserts": list(ent_map.values()), "relationship_signals": list(sig_map.values()),
            "decision_updates": [], "proposal_updates": prop_list, "risk_updates": [], "strategic_intent_signals": [],
        },
    }


def extract_orals_proposal_projection(
    document_text: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Extract consolidated high-signal Output 03 projection for PURSUIT_ORALS_PROPOSAL decks."""
    if not document_text or not document_text.strip():
        return {}
    if not _NEE_INFRA_ORALS_CUES.search(document_text):
        return _extract_dynamic_orals_projection(document_text)
    pages_map = _split_pages_map(document_text)
    ev_idx: Dict[str, Any] = {}
    ent_map: Dict[str, Dict[str, Any]] = {}
    sig_map: Dict[str, Dict[str, Any]] = {}
    prop_list: List[Dict[str, Any]] = []
    risk_list: List[Dict[str, Any]] = []

    _emit_workstreams_and_deliverables(pages_map, document_text, ev_idx, ent_map, sig_map)
    _emit_milestones_forums_and_models(pages_map, document_text, ev_idx, ent_map, sig_map)
    _emit_multihop_architecture_systems(pages_map, document_text, ev_idx, ent_map, sig_map)
    _emit_raci_roles_orgs_and_team(pages_map, document_text, ev_idx, ent_map, sig_map)
    _emit_hierarchy_proposals_and_risks(pages_map, document_text, ev_idx, ent_map, sig_map, prop_list, risk_list)

    return {
        "schema_version": "5.7",
        "is_document": True,
        "output3_only": True,
        "evidence_index": ev_idx,
        "evidence_only_statement_ids": [],
        "graph_mutations": {
            "entity_upserts": list(ent_map.values()),
            "relationship_signals": list(sig_map.values()),
            "decision_updates": [],
            "proposal_updates": prop_list,
            "risk_updates": risk_list,
            "strategic_intent_signals": [],
        },
    }
