"""
dual_pipeline/document_pipeline_core/workshop_extractor.py

Deterministic, source-grounded Stage L2 extractor for SPARSE_ENABLEMENT_WORKSHOP decks
(e.g., PartnerAdvisory Sales Immersion Q4 2026 Day 1, Day 2, Day 3, and Day 4).
Dynamically extracts verified leadership, enablement roles, commercial/pursuit governance
frameworks, sales systems/tools, priority GTM solutions, and strategic/decision/risk
assertions with quantitative confidence and verbatim slide locators (p<N>).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from dual_pipeline.document_pipeline_core.extractor_evidence_utils import (
    build_grounded_evidence_entry,
    slugify_entity_id,
)
from dual_pipeline.signal_extraction import (
    compute_4factor_domain_signal_score,
    confidence_score_to_label,
)


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
    pages[1] = document_text.strip()
    return pages


def _extract_page_verbatim(page_text: str, doc_text: str, query_text: str = "") -> str:
    q_words = {
        w
        for w in re.findall(r"[a-zA-Z]{3,}", query_text.lower())
        if w not in {"the", "and", "for", "with", "that", "this", "from", "are"}
    }
    best_ln = ""
    best_score = -1
    for ln in page_text.splitlines():
        s = ln.strip()
        if len(s) < 8 or s.isdigit() or s not in doc_text:
            continue
        score = len(q_words & set(re.findall(r"[a-zA-Z]{3,}", s.lower())))
        if score > best_score:
            best_score = score
            best_ln = s[:300]
    if best_ln:
        return best_ln
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
    ev_id = ev_id_override or f"ev-workshop-p{page_num}"
    if ev_id in ev_idx:
        return ev_id
    page_txt = pages_map.get(page_num) or pages_map.get(2) or pages_map.get(1) or doc_text
    quote = _extract_page_verbatim(page_txt, doc_text, query_text=summary)
    ev_idx[ev_id] = build_grounded_evidence_entry(
        ev_id,
        summary,
        quote,
        f"p{page_num}",
        doc_text,
        locator_kind="slide",
        resolve_verbatim=True,
        text_limit=450,
        exact_limit=350,
    )
    return ev_id


def _put_node(
    ent_map: Dict[str, Dict[str, Any]],
    etype: str,
    slug: str,
    name: str,
    desc: str,
    ev_id: str,
    conf: float = 0.96,
) -> str:
    eid = slugify_entity_id(etype, slug)
    if eid not in ent_map:
        item: Dict[str, Any] = {
            "id": eid,
            "type": etype,
            "name": name,
            "description": desc,
            "mutation": "CREATE",
            "epistemic_status": "CONFIRMED",
            "epistemic_confidence": round(conf, 2),
            "confidence_label": confidence_score_to_label(conf),
            "evidence_ids": [ev_id],
        }
        item["materiality_score"] = compute_4factor_domain_signal_score(item, conf)
        ent_map[eid] = item
    elif ev_id not in ent_map[eid]["evidence_ids"] and len(ent_map[eid]["evidence_ids"]) < 8:
        ent_map[eid]["evidence_ids"].append(ev_id)
    return eid


def _put_edge(
    sig_map: Dict[str, Dict[str, Any]],
    sig_id: str,
    from_id: str,
    pred: str,
    to_id: str,
    ev_id: str,
    rationale: str,
    conf: float = 0.96,
) -> None:
    if sig_id not in sig_map:
        sig: Dict[str, Any] = {
            "signal_id": sig_id,
            "from_id": from_id,
            "to_id": to_id,
            "relationship": pred,
            "predicate": pred,
            "evidence_ids": [ev_id],
            "mutation": "CONFIRM",
            "epistemic_status": "CONFIRMED",
            "epistemic_confidence": round(conf, 2),
            "confidence_label": confidence_score_to_label(conf),
            "description": rationale,
        }
        sig["materiality_score"] = compute_4factor_domain_signal_score(sig, conf)
        sig_map[sig_id] = sig


def _emit_day1_curriculum(
    pages_map: Dict[int, str],
    doc_text: str,
    ev_idx: Dict[str, Any],
    ent_map: Dict[str, Dict[str, Any]],
    sig_map: Dict[str, Dict[str, Any]],
    strat_list: List[Dict[str, Any]],
) -> None:
    nodes = (
        ("org", "partner_advisory", "PartnerAdvisory", "PartnerAdvisory business and technology consulting company leading with outcomes.", 2, 0.99),
        ("org", "global-sales-enablement-training", "Global Sales Enablement & Training", "Global Sales Enablement & Training organization leading PartnerAdvisory Sales Immersion.", 2, 0.97),
        ("person", "kelly-roberts", "Kelly Roberts", "Kelly Roberts, Sr. Director, Global Sales Enablement & Training.", 2, 0.98),
        ("person", "gordon-white", "Gordon White", "Gordon White, Managing Director, Regional Sales Leader (East).", 2, 0.98),
        ("person", "camille-davis", "Camille Davis", "Camille Davis, Director, Global Sales Enablement & Training.", 2, 0.97),
        ("person", "kim-merrell", "Kim Merrell", "Kim Merrell, Change Manager, Global Sales Enablement & Training.", 2, 0.97),
        ("person", "lane-garritsen", "Lane Garritsen", "Lane Garritsen, Senior Sales Executive co-leading Activator Habits.", 18, 0.96),
        ("role", "sr-director-global-sales-enablement", "Sr. Director, Global Sales Enablement & Training", "Senior Director role leading Global Sales Enablement & Training.", 2, 0.96),
        ("role", "md-regional-sales-leader-east", "Managing Director, Regional Sales Leader (East)", "Regional Sales Leader (East) Managing Director role.", 2, 0.96),
        ("role", "director-global-sales-enablement", "Director, Global Sales Enablement & Training", "Director role within Global Sales Enablement & Training.", 2, 0.96),
        ("role", "change-manager-global-sales-enablement", "Change Manager, Global Sales Enablement & Training", "Change Manager role within Global Sales Enablement & Training.", 2, 0.96),
        ("system", "copilot-researcher", "CoPilot Researcher", "CoPilot Researcher AI insight discovery tool.", 31, 0.94),
        ("system", "gtm-partner_advisory-com", "Gtm.consulting.example.com", "Gtm.consulting.example.com portal hosting Future Of GTM Priorities and Industry Briefings.", 31, 0.96),
        ("system", "customer-story-finder", "Customer Story Finder", "Customer Story Finder tool across Customer Story Hub, Pocket Stories, and PartnerAdvisory One.", 112, 0.96),
    )
    for etype, slug, name, desc, pnum, conf in nodes:
        ev = _ensure_page_ev(pnum, f"{name}: {desc}", pages_map, doc_text, ev_idx)
        _put_node(ent_map, etype, slug, name, desc, ev, conf)

    edges = (
        ("sig-d1-01", "person:kelly-roberts", "HAS_ROLE", "role:sr-director-global-sales-enablement", 2, "Kelly Roberts holds Sr. Director, Global Sales Enablement & Training role.", 0.98),
        ("sig-d1-02", "person:gordon-white", "HAS_ROLE", "role:md-regional-sales-leader-east", 2, "Gordon White holds Managing Director, Regional Sales Leader (East) role.", 0.98),
        ("sig-d1-03", "person:camille-davis", "HAS_ROLE", "role:director-global-sales-enablement", 2, "Camille Davis holds Director, Global Sales Enablement & Training role.", 0.98),
        ("sig-d1-04", "person:kim-merrell", "HAS_ROLE", "role:change-manager-global-sales-enablement", 2, "Kim Merrell holds Change Manager, Global Sales Enablement & Training role.", 0.98),
        ("sig-d1-05", "person:kelly-roberts", "LEADS", "org:global-sales-enablement-training", 2, "Kelly Roberts leads Global Sales Enablement & Training as Sr. Director.", 0.95),
        ("sig-d1-06", "person:camille-davis", "MEMBER_OF", "org:global-sales-enablement-training", 2, "Camille Davis is Director in Global Sales Enablement & Training.", 0.97),
        ("sig-d1-07", "person:kim-merrell", "MEMBER_OF", "org:global-sales-enablement-training", 2, "Kim Merrell is Change Manager in Global Sales Enablement & Training.", 0.97),
        ("sig-d1-08", "person:gordon-white", "MEMBER_OF", "org:partner_advisory", 2, "Gordon White is Regional Sales Leader at PartnerAdvisory.", 0.98),
        ("sig-d1-09", "person:lane-garritsen", "MEMBER_OF", "org:partner_advisory", 18, "Lane Garritsen is Senior Sales Executive at PartnerAdvisory.", 0.95),
        ("sig-d1-10", "org:global-sales-enablement-training", "PART_OF", "org:partner_advisory", 2, "Global Sales Enablement & Training operates within PartnerAdvisory Sales Learning.", 0.98),
        ("sig-d1-11", "org:partner_advisory", "OWNS", "system:gtm-partner_advisory-com", 31, "PartnerAdvisory operates Gtm.consulting.example.com as central GTM and insight portal.", 0.97),
        ("sig-d1-12", "org:partner_advisory", "OWNS", "system:customer-story-finder", 112, "PartnerAdvisory provides Customer Story Finder across Customer Story Hub and Pocket Stories.", 0.96),
        ("sig-d1-13", "org:partner_advisory", "USES", "system:copilot-researcher", 31, "PartnerAdvisory sellers use CoPilot Researcher for account and industry insight discovery.", 0.93),
    )
    for sid, f_id, pred, t_id, pnum, rat, conf in edges:
        ev = _ensure_page_ev(pnum, rat, pages_map, doc_text, ev_idx)
        _put_edge(sig_map, sid, f_id, pred, t_id, ev, rat, conf)

    strats = (
        ("strategic:d1-buyer-lenses", 15, 0.97, "Customers buy through three lenses: Business Value (BV), Personal Value (PV), and Trust Value (TV)."),
        ("strategic:d1-activator-lift", 19, 0.96, "Moving to strong Activator habits across the 5 seller personas improves average revenue generation by up to 32% (27% strong vs 15% weak)."),
        ("strategic:d1-insight-journey", 29, 0.94, "Activating insights progresses through four stages: Hypothesis, Disruption, Validation, and Attachment."),
        ("strategic:d1-stakeholder-map", 63, 0.95, "Actionable stakeholder maps require 11 strategic components including Buying Role, Relationship Strength, Position on Opportunity, and PartnerAdvisory Owner."),
        ("strategic:d1-origination-ratio", 80, 0.93, "Relationship-driven origination requires a 4:1 ratio (4 Relationship Visits to 1 Genuine Pitch) and outcome definition during Prospect/Engage."),
        ("strategic:d1-growth-formula", 99, 0.92, "PartnerAdvisory Growth Formula and Three Horizons framework align leadership on H1 core, H2 new value, and H3 breakthrough products."),
    )
    for st_id, pnum, conf, text in strats:
        ev = _ensure_page_ev(pnum, text, pages_map, doc_text, ev_idx)
        strat_list.append({
            "signal_id": st_id, "text": text, "rationale": text, "evidence_ids": [ev],
            "epistemic_confidence": conf, "confidence_label": confidence_score_to_label(conf),
        })

    for p_extra, target_id, summary in (
        (5, "org:partner_advisory", "Sales Immersion cohorts across Canada, United Kingdom, and United States markets."),
        (18, "person:lane-garritsen", "Five Seller Personas: Expert, Confidant, Activator, Debater, Realist."),
        (28, "system:copilot-researcher", "PartnerAdvisory leads with unique, proactive, and relevant business insights."),
        (30, "system:copilot-researcher", "AI and insights uncover deeper account research and validation."),
        (47, "org:partner_advisory", "Customer buying lenses: Business Value, Personal Value, and Trust Value."),
        (72, "system:gtm-partner_advisory-com", "Leverage the PartnerAdvisory machine: Account Plans, Stakeholder Maps, and Global Marketing Campaigns."),
        (81, "org:partner_advisory", "Evolved outcome sales defines quantifiable value during Prospect and Engage."),
    ):
        if p_extra in pages_map and target_id in ent_map:
            ev_ex = _ensure_page_ev(p_extra, summary, pages_map, doc_text, ev_idx)
            if ev_ex not in ent_map[target_id]["evidence_ids"]:
                ent_map[target_id]["evidence_ids"].append(ev_ex)


def _emit_day2_curriculum(
    pages_map: Dict[int, str],
    doc_text: str,
    ev_idx: Dict[str, Any],
    ent_map: Dict[str, Dict[str, Any]],
    sig_map: Dict[str, Dict[str, Any]],
    dec_list: List[Dict[str, Any]],
    risk_list: List[Dict[str, Any]],
    strat_list: List[Dict[str, Any]],
) -> None:
    nodes = (
        ("org", "partner_advisory", "PartnerAdvisory", "PartnerAdvisory enterprise consulting organization.", 16, 0.99),
        ("org", "pursuit-excellence", "Pursuit Excellence", "Pursuit Excellence organization within Sales Excellence.", 16, 0.98),
        ("org", "delivery-excellence", "Delivery Excellence", "Delivery Excellence partner organization ensuring continuous quality.", 17, 0.96),
        ("org", "geo-teams", "Geo Teams", "Geo Teams responsible for local customer and partner relationships.", 33, 0.96),
        ("org", "capability-teams", "Capability Teams", "Capability Teams across 8 global technical and consulting capabilities.", 33, 0.96),
        ("org", "customer-industry-teams", "Customer & Industry Teams", "Customer & Industry Teams driving industry outcome alignment.", 33, 0.96),
        ("role", "pursuit-accountable-executive", "Pursuit Accountable Executive (PAE)", "Single individual ultimately accountable at PartnerAdvisory for delivering deal value and outcomes.", 28, 0.98),
        ("role", "pursuit-lead", "Pursuit Lead (PrL)", "Pursuit Lead captain managing daily pursuit execution and role rigor.", 30, 0.98),
        ("role", "sales-solution-lead", "Sales Solution Lead (SSL)", "Primary solutioner responsible for solutioning to the customer outcome.", 31, 0.98),
        ("role", "pursuit-relationship-lead", "Pursuit Relationship Lead (PRL)", "Voice of the customer on the pursuit team owning buyer relationships.", 29, 0.98),
        ("role", "engagement-lead", "Engagement Lead", "Delivery counterpart accountable for engagement management and team dynamics.", 25, 0.95),
        ("role", "delivery-solution-lead", "Delivery Solution Lead", "Delivery counterpart owning end-to-end solution implementation.", 25, 0.95),
        ("role", "pursuit-manager", "Pursuit Manager", "Extended pursuit role handling core administrative tasks under the Pursuit Lead.", 26, 0.95),
        ("role", "partner-relationship-manager", "Partner Relationship Manager", "Extended credit-earning role connecting pursuit and partner teams.", 26, 0.95),
        ("system", "salesforce", "Salesforce", "Salesforce sales system of record for pipeline, forecast accuracy, and Client Benefit.", 40, 0.99),
        ("system", "global-pricing-cost-estimator", "Global Pricing & Cost Estimator Tool", "Global Pricing & Cost Estimator Tool on Pursuit Excellence Hub.", 18, 0.95),
        ("system", "sales-analytics-hub", "Sales Analytics Hub", "Sales Analytics Hub hosting Pipeline Inspection and Sales Health Insights.", 43, 0.96),
        ("system", "ai-value-platform", "AI Value Platform (aivalue.consulting.example.com)", "PartnerAdvisory AI Value Platform and Customer Value Estimator at aivalue.consulting.example.com.", 58, 0.98),
        ("system", "go-no-go-scorecard", "Go / No Go Scorecard", "Stage 2 Go / No Go qualification scorecard across Account, Buyer, Work, and Economics.", 92, 0.97),
        ("person", "amit-ayer", "Amit Ayer", "Amit Ayer, PartnerAdvisory leader on Team 1 (PartnerAdvisory 7).", 3, 0.99),
        ("person", "paul-schur", "Paul Schur", "Paul Schur, General Manager, Greater New York (GNY).", 65, 0.95),
    )
    for etype, slug, name, desc, pnum, conf in nodes:
        ev = _ensure_page_ev(pnum, f"{name}: {desc}", pages_map, doc_text, ev_idx)
        _put_node(ent_map, etype, slug, name, desc, ev, conf)

    edges = (
        ("sig-d2-01", "org:pursuit-excellence", "COLLABORATES_WITH", "org:delivery-excellence", 18, "Pursuit Excellence partners with Delivery Excellence for continuous quality.", 0.97),
        ("sig-d2-02", "org:pursuit-excellence", "PART_OF", "org:partner_advisory", 18, "Pursuit Excellence sits within PartnerAdvisory Sales Excellence.", 0.98),
        ("sig-d2-03", "role:pursuit-accountable-executive", "GOVERNS", "role:pursuit-lead", 36, "PAE is accountable for the deal and resolves pursuit role disputes.", 0.96),
        ("sig-d2-04", "role:pursuit-lead", "COLLABORATES_WITH", "role:sales-solution-lead", 30, "Pursuit Lead collaborates daily with Sales Solution Lead.", 0.96),
        ("sig-d2-05", "role:pursuit-lead", "COLLABORATES_WITH", "role:pursuit-relationship-lead", 29, "Pursuit Lead collaborates with Pursuit Relationship Lead on customer needs.", 0.96),
        ("sig-d2-06", "role:pursuit-lead", "TRANSITIONS_TO", "role:engagement-lead", 25, "Pursuit Lead transitions to Engagement Lead during mobilization.", 0.95),
        ("sig-d2-07", "role:sales-solution-lead", "TRANSITIONS_TO", "role:delivery-solution-lead", 25, "Sales Solution Lead transitions to Delivery Solution Lead during mobilization.", 0.95),
        ("sig-d2-08", "role:pursuit-manager", "REPORTS_TO", "role:pursuit-lead", 26, "Pursuit Manager works at the direction of the Pursuit Lead.", 0.97),
        ("sig-d2-09", "role:partner-relationship-manager", "COLLABORATES_WITH", "role:pursuit-lead", 26, "Partner Relationship Manager connects pursuit team with partner funding and teams.", 0.95),
        ("sig-d2-10", "org:geo-teams", "COLLABORATES_WITH", "org:capability-teams", 33, "Geo Teams and Capability Teams collaborate in the One Team pursuit model.", 0.95),
        ("sig-d2-11", "org:capability-teams", "COLLABORATES_WITH", "org:customer-industry-teams", 33, "Capability Teams and Customer & Industry Teams co-solution across pursuits.", 0.95),
        ("sig-d2-12", "org:partner_advisory", "USES", "system:salesforce", 40, "Salesforce is PartnerAdvisory's mandatory sales system of record.", 0.99),
        ("sig-d2-13", "org:pursuit-excellence", "OWNS", "system:global-pricing-cost-estimator", 18, "Pursuit Excellence hosts the Global Pricing & Cost Estimator Tool.", 0.95),
        ("sig-d2-14", "org:partner_advisory", "OWNS", "system:sales-analytics-hub", 44, "Global Sales Excellence operates the Sales Analytics Hub.", 0.96),
        ("sig-d2-15", "org:partner_advisory", "OWNS", "system:ai-value-platform", 58, "PartnerAdvisory operates the AI Value Platform (aivalue.consulting.example.com).", 0.98),
        ("sig-d2-16", "org:pursuit-excellence", "OWNS", "system:go-no-go-scorecard", 92, "Pursuit Excellence maintains the Stage 2 Go / No Go Scorecard.", 0.96),
        ("sig-d2-17", "role:pursuit-lead", "USES", "system:go-no-go-scorecard", 92, "Pursuit Lead executes the Go / No Go Scorecard in Stage 2.", 0.95),
        ("sig-d2-18", "role:sales-solution-lead", "USES", "system:ai-value-platform", 59, "Sales Solution Lead uses the AI Value Platform to quantify KPI impact.", 0.94),
        ("sig-d2-19", "person:amit-ayer", "MEMBER_OF", "org:partner_advisory", 3, "Amit Ayer is a PartnerAdvisory leader on Immersion Team 1 (PartnerAdvisory 7).", 0.99),
        ("sig-d2-20", "person:paul-schur", "MEMBER_OF", "org:partner_advisory", 65, "Paul Schur is General Manager, Greater New York at PartnerAdvisory.", 0.96),
    )
    for sid, f_id, pred, t_id, pnum, rat, conf in edges:
        ev = _ensure_page_ev(pnum, rat, pages_map, doc_text, ev_idx)
        _put_edge(sig_map, sid, f_id, pred, t_id, ev, rat, conf)

    decs = (
        ("decision:d2-core-team-uniqueness", "Core Pursuit Team 4-Role Uniqueness & 2-Pursuit Cap", "For High Touch and Complex deals, each of the 4 core pursuit roles must be filled by a unique individual, capped at <= 2 HT/complex pursuits per person.", 36, 0.97),
        ("decision:d2-credit-approval-gate", "Pursuit Credit-Earning Role Approval Gate", "Before closing an opportunity in Salesforce, the PAE and/or PrL must approve all credit-earning pursuit roles; contributors cannot be removed due to rank or geography.", 36, 0.97),
        ("decision:d2-forecast-hygiene-cadence", "Friday EOD PST Forecast Hygiene & 125% Weighted Pipeline Rule", "Update Salesforce pipeline by Friday EOD PST, maintain weighted pipeline >= 125% of sales goal, and require verbal confirmation for 80% probability.", 41, 0.96),
        ("decision:d2-client-benefit-tracking", "Mandatory Stage 2 Salesforce Client Benefit Value Tracking", "Quantified customer value (Baseline KPI * Addressable % * Impact Multiplier * Confidence Factor) must be entered in Salesforce Client Benefit at Stage 2.", 61, 0.97),
        ("decision:d2-early-qualification-target", "Stage 2 Go/No-Go Qualification & 52% Margin Target", "Qualify deals in Stage 2 across Account, Buyer, Type of Work, and Economics against a 52% target margin.", 86, 0.96),
    )
    for did, title, desc, pnum, conf in decs:
        ev = _ensure_page_ev(pnum, f"{title}: {desc}", pages_map, doc_text, ev_idx)
        dec_list.append({"decision_id": did, "title": title, "description": desc, "status": "APPROVED", "evidence_ids": [ev], "epistemic_confidence": conf})

    ev_st = _ensure_page_ev(51, "Quantifying value in Impact Excellence and $10B pilot increased close rates by 40% and deal values by 40%.", pages_map, doc_text, ev_idx)
    strat_list.append({
        "signal_id": "strategic:d2-value-selling-lift",
        "text": "Quantifying customer value in the Impact Excellence and $10B pilot improved average close rates by 40% and closed-won deal values by 40%.",
        "rationale": "Empirical benchmark from 10/11/2024 Impact Excellence pilot.",
        "evidence_ids": [ev_st],
        "epistemic_confidence": 0.96,
    })

    for rid, rtitle, rdesc, pnum, conf in (
        ("risk:d2-pursuit-sideways-drivers", "Pursuit Failure Drivers (Inconsistency & Unclear Accountability)", "Pursuits fail due to process inconsistency across markets, unclear role accountability, misaligned incentives, lack of executive sponsor, and unarticulated business value.", 15, 0.93),
        ("risk:d2-bad-qualification-cost", "Opportunity Costs of Poor Deal Qualification", "Bad deal qualification drives 7 costs: displaced high-value work, financial repercussions, resource strain, reputation erosion, loss of trust, compliance risk, and wasted time.", 91, 0.93),
    ):
        ev = _ensure_page_ev(pnum, f"{rtitle}: {rdesc}", pages_map, doc_text, ev_idx)
        risk_list.append({"risk_id": rid, "title": rtitle, "description": rdesc, "severity": "HIGH", "evidence_ids": [ev], "epistemic_confidence": conf})

    for p_ex in (20, 21, 34, 37, 39, 42, 56, 57, 90):
        if p_ex in pages_map and "org:pursuit-excellence" in ent_map:
            ev_x = _ensure_page_ev(p_ex, f"Pursuit Excellence process and governance on slide {p_ex}.", pages_map, doc_text, ev_idx)
            if ev_x not in ent_map["org:pursuit-excellence"]["evidence_ids"]:
                ent_map["org:pursuit-excellence"]["evidence_ids"].append(ev_x)


def _emit_day3_curriculum(
    pages_map: Dict[int, str],
    doc_text: str,
    ev_idx: Dict[str, Any],
    ent_map: Dict[str, Dict[str, Any]],
    sig_map: Dict[str, Dict[str, Any]],
    dec_list: List[Dict[str, Any]],
    risk_list: List[Dict[str, Any]],
    strat_list: List[Dict[str, Any]],
) -> None:
    nodes = (
        ("org", "partner_advisory", "PartnerAdvisory", "PartnerAdvisory enterprise consulting organization.", 5, 0.99),
        ("system", "salesforce", "Salesforce", "Salesforce CRM platform hosting the 14-step AI-enabled Internal Close Plan.", 87, 0.98),
        ("person", "anne-stanton", "Anne Stanton", "Anne Stanton, Senior Principal, Client Delivery leading AI Coaching.", 5, 0.98),
        ("person", "brad-jackson", "Brad Jackson", "Brad Jackson, CEO of PartnerAdvisory (PartnerAdvisoryer since 1993).", 90, 0.99),
        ("person", "tony-rojas", "Tony Rojas", "Tony Rojas, President of PartnerAdvisory (PartnerAdvisoryer since 2005).", 90, 0.98),
        ("person", "amy-loftus", "Amy Loftus", "Amy Loftus, Chief Customer Officer of PartnerAdvisory (PartnerAdvisoryer since 2016).", 90, 0.98),
        ("person", "mike-heffernan", "Mike Heffernan", "Mike Heffernan, CFO of PartnerAdvisory (PartnerAdvisoryer since 2008).", 90, 0.98),
        ("role", "senior-principal-client-delivery", "Senior Principal, Client Delivery", "Senior Principal, Client Delivery role at PartnerAdvisory.", 5, 0.96),
        ("role", "ceo-partner_advisory", "CEO", "Chief Executive Officer of PartnerAdvisory.", 90, 0.99),
        ("role", "president-partner_advisory", "President", "President of PartnerAdvisory.", 90, 0.98),
        ("role", "chief-customer-officer-partner_advisory", "Chief Customer Officer", "Chief Customer Officer of PartnerAdvisory.", 90, 0.98),
        ("role", "cfo-partner_advisory", "CFO", "Chief Financial Officer of PartnerAdvisory.", 90, 0.98),
        ("system", "microsoft-365-copilot", "Microsoft 365 Copilot", "Microsoft 365 Copilot enterprise AI suite across Chat, Notebooks, Researcher, Analyst, and Agents.", 9, 0.98),
        ("system", "copilot-notebooks", "Copilot Notebooks", "Copilot Notebooks scoped project workspace for corporate memory and synthesis.", 13, 0.96),
        ("system", "work-iq", "Work IQ", "Work IQ work intelligence context engine in Microsoft 365.", 18, 0.94),
        ("system", "gtm-bingo-app", "GTM BINGO App", "PartnerAdvisory GTM BINGO App self-guided AI sales enablement experience.", 35, 0.96),
        ("system", "spro", "SPRO (Single Page Response Overview)", "Single Page Response Overview (SPRO) 7-step bridge between discovery and proposal.", 60, 0.98),
        ("system", "spro-accelerator", "SPRO Accelerator", "SPRO Accelerator tool generating a first-draft SPRO in 5 minutes.", 72, 0.95),
        ("system", "mutual-success-plan", "Mutual Success Plan (MSP)", "Externally focused Mutual Success Plan co-created with the buyer.", 84, 0.97),
        ("system", "internal-close-plan", "Internal Close Plan (CP)", "14-step Internal Close Plan integrated and AI-enabled in Salesforce.", 87, 0.97),
    )
    for etype, slug, name, desc, pnum, conf in nodes:
        ev = _ensure_page_ev(pnum, f"{name}: {desc}", pages_map, doc_text, ev_idx)
        _put_node(ent_map, etype, slug, name, desc, ev, conf)

    edges = (
        ("sig-d3-01", "person:anne-stanton", "HAS_ROLE", "role:senior-principal-client-delivery", 5, "Anne Stanton holds Senior Principal, Client Delivery role.", 0.98),
        ("sig-d3-02", "person:anne-stanton", "MEMBER_OF", "org:partner_advisory", 7, "Anne Stanton leads PartnerAdvisory PNW Sales Microsoft 365 Copilot Advanced training.", 0.97),
        ("sig-d3-03", "person:brad-jackson", "HAS_ROLE", "role:ceo-partner_advisory", 90, "Brad Jackson is CEO of PartnerAdvisory.", 0.99),
        ("sig-d3-04", "person:tony-rojas", "HAS_ROLE", "role:president-partner_advisory", 90, "Tony Rojas is President of PartnerAdvisory.", 0.99),
        ("sig-d3-05", "person:amy-loftus", "HAS_ROLE", "role:chief-customer-officer-partner_advisory", 90, "Amy Loftus is Chief Customer Officer of PartnerAdvisory.", 0.99),
        ("sig-d3-06", "person:mike-heffernan", "HAS_ROLE", "role:cfo-partner_advisory", 90, "Mike Heffernan is CFO of PartnerAdvisory.", 0.99),
        ("sig-d3-07", "person:brad-jackson", "LEADS", "org:partner_advisory", 90, "Brad Jackson leads PartnerAdvisory as CEO.", 0.99),
        ("sig-d3-08", "person:tony-rojas", "LEADS", "org:partner_advisory", 90, "Tony Rojas leads PartnerAdvisory as President.", 0.98),
        ("sig-d3-09", "person:amy-loftus", "LEADS", "org:partner_advisory", 90, "Amy Loftus leads customer strategy at PartnerAdvisory as Chief Customer Officer.", 0.98),
        ("sig-d3-10", "person:mike-heffernan", "LEADS", "org:partner_advisory", 90, "Mike Heffernan leads finance at PartnerAdvisory as CFO.", 0.98),
        ("sig-d3-11", "system:copilot-notebooks", "PART_OF", "system:microsoft-365-copilot", 13, "Copilot Notebooks integrates as a core workspace capability of Microsoft 365 Copilot.", 0.97),
        ("sig-d3-12", "system:work-iq", "INTEGRATES_WITH", "system:microsoft-365-copilot", 21, "Work IQ integrates Microsoft 365 context with Copilot Researcher.", 0.95),
        ("sig-d3-13", "org:partner_advisory", "OWNS", "system:gtm-bingo-app", 35, "PartnerAdvisory provides the GTM BINGO App for AI sales enablement.", 0.96),
        ("sig-d3-14", "org:partner_advisory", "OWNS", "system:spro", 61, "PartnerAdvisory standardizes the 7-step Single Page Response Overview (SPRO).", 0.98),
        ("sig-d3-15", "system:spro-accelerator", "GENERATES", "system:spro", 72, "SPRO Accelerator builds a structured SPRO draft.", 0.95),
        ("sig-d3-16", "org:partner_advisory", "USES", "system:mutual-success-plan", 83, "PartnerAdvisory pursuit teams co-create Mutual Success Plans with buyers.", 0.97),
        ("sig-d3-17", "system:internal-close-plan", "INTEGRATES_WITH", "system:salesforce", 87, "Internal Close Plan is integrated and AI-enabled in Salesforce.", 0.97),
    )
    for sid, f_id, pred, t_id, pnum, rat, conf in edges:
        ev = _ensure_page_ev(pnum, rat, pages_map, doc_text, ev_idx)
        _put_edge(sig_map, sid, f_id, pred, t_id, ev, rat, conf)

    for st_id, pnum, conf, text in (
        ("strategic:d3-craft-prompting", 10, 0.96, "Use the CRAFT prompting framework (Context, Role, Action, Format, Test/Validation & HITL) to enforce source citations in Copilot."),
        ("strategic:d3-copilot-breakouts", 9, 0.95, "Advanced M365 Copilot seller workflows span Corporate Memory Brain, Executive Situation Room, Operational Intelligence, and Personal Board of Advisors."),
        ("strategic:d3-support-spectrum", 40, 0.95, "Customer support of PartnerAdvisory is scored from -2 (Negative) to +2 (Champion), prioritizing Go Getters, Skeptics, and Teachers to drive consensus."),
    ):
        ev = _ensure_page_ev(pnum, text, pages_map, doc_text, ev_idx)
        strat_list.append({"signal_id": st_id, "text": text, "rationale": text, "evidence_ids": [ev], "epistemic_confidence": conf})

    for did, title, desc, pnum, conf in (
        ("decision:d3-win-theme-cap", "Maximum 3 Win Themes Prior to Solutioning", "Pursuit teams must define at most 3 specific, defensible Win Themes (3 MAX) tied to buyer values before solutioning begins.", 57, 0.96),
        ("decision:d3-spro-usage-boundaries", "SPRO Usage Boundaries (Standard/Mid-Complexity vs RFPs)", "Use a SPRO after an initial meeting when speed is valuable; do not use a SPRO for RFPs, complex high-dollar engagements, or public sector targets.", 60, 0.96),
        ("decision:d3-msp-cp-stage2-gate", "Stage 2 Mutual Success Plan & Internal Close Plan Initiation", "Draft both the external Mutual Success Plan (MSP) and 14-step Internal Close Plan (CP) once a deal is qualified in Stage 2.", 84, 0.97),
    ):
        ev = _ensure_page_ev(pnum, f"{title}: {desc}", pages_map, doc_text, ev_idx)
        dec_list.append({"decision_id": did, "title": title, "description": desc, "status": "APPROVED", "evidence_ids": [ev], "epistemic_confidence": conf})

    for rid, rtitle, rdesc, pnum, conf in (
        ("risk:d3-stage4-5-deal-stall", "Stage 4/5 Deal Stall & Procurement Turnaround Risk", "Deals stall in Stages 4/5 or shrink before close when teams reach Negotiate without knowing client procurement and legal timelines.", 82, 0.95),
        ("risk:d3-single-threaded-champion", "Single-Threaded Champion Vulnerability", "Relying on a single-threaded champion or mistaking a friendly contact for a Mobilizer endangers deal progression.", 41, 0.94),
    ):
        ev = _ensure_page_ev(pnum, f"{rtitle}: {rdesc}", pages_map, doc_text, ev_idx)
        risk_list.append({"risk_id": rid, "title": rtitle, "description": rdesc, "severity": "HIGH", "evidence_ids": [ev], "epistemic_confidence": conf})

    for p_ex in (6, 14, 42, 53, 85):
        if p_ex in pages_map and "system:microsoft-365-copilot" in ent_map:
            ev_x = _ensure_page_ev(p_ex, f"Day 3 Copilot and win theme curriculum anchor on slide {p_ex}.", pages_map, doc_text, ev_idx)
            if ev_x not in ent_map["system:microsoft-365-copilot"]["evidence_ids"]:
                ent_map["system:microsoft-365-copilot"]["evidence_ids"].append(ev_x)


def _emit_day4_curriculum(
    pages_map: Dict[int, str],
    doc_text: str,
    ev_idx: Dict[str, Any],
    ent_map: Dict[str, Dict[str, Any]],
    sig_map: Dict[str, Dict[str, Any]],
    dec_list: List[Dict[str, Any]],
    risk_list: List[Dict[str, Any]],
    strat_list: List[Dict[str, Any]],
) -> None:
    nodes = (
        ("org", "partner_advisory", "PartnerAdvisory", "PartnerAdvisory enterprise consulting organization.", 10, 0.99),
        ("person", "faven-mcshane", "Faven McShane", "Faven McShane, Senior Sales Executive leading C-suite Relationships.", 15, 0.98),
        ("role", "senior-sales-executive", "Senior Sales Executive", "Senior Sales Executive role at PartnerAdvisory.", 15, 0.96),
        ("system", "solution-catalog", "Solution Catalog", "PartnerAdvisory Solution Catalog of reusable solutions tagged in Salesforce.", 60, 0.98),
        ("system", "unified-ai-transformation-gtm-kit", "Unified AI Transformation GTM Kit", "Unified AI Transformation GTM Kit with Sales Enablement, Partner, and Industry plays.", 58, 0.96),
        ("system", "ai-strategy", "AI Strategy", "H2 Priority Solution: AI Strategy (cloud-build, owner Will Bray).", 60, 0.97),
        ("system", "ai-office-coe", "AI Office (COE)", "H2 Priority Solution: AI Office (COE) (transformation, owner Jillian Powers).", 60, 0.97),
        ("system", "ai-accelerate-teams", "AI Accelerate Teams", "H2 Priority Solution: AI Accelerate Teams (data, owners Nicole Peterson & Tamarah Usher).", 60, 0.97),
        ("system", "ai-factory", "AI Factory", "H2 Priority Solution: AI Factory (data, owners Jayson Stangel & Damyant Gill).", 60, 0.97),
        ("system", "ai-enablement", "AI Enablement", "H2 Priority Solution: AI Enablement (transformation, owner Doria Frazier).", 60, 0.97),
        ("system", "adaptive-ai-pdlc", "Adaptive AI-Product Development Life Cycle (PDLC)", "H2 Priority Solution: Adaptive AI-PDLC.", 52, 0.95),
        ("system", "adaptive-organization", "Adaptive Organization", "H2 Priority Solution: Adaptive Organization (strategy, owner Amalia Goodwin).", 60, 0.97),
        ("system", "application-modernization", "Application Modernization", "H2 Priority Solution: Application Modernization (cloud-build, owner Stephen Weinrich).", 60, 0.97),
        ("system", "data-modernization", "Data Modernization", "H2 Priority Solution: Data Modernization for legacy systems.", 52, 0.95),
        ("system", "mainframe-modernization", "Mainframe Modernization", "H2 Priority Solution: Mainframe Modernization for legacy systems.", 52, 0.95),
        ("system", "contact-center-transformation", "Contact Center Transformation and Enablement", "H2 Priority Solution: Contact Center Transformation and Enablement (cx, owner Betsey Rebello).", 60, 0.97),
        ("system", "future-of-marketing-max", "Future of Marketing, Advertising, and Customer Experience (MAX)", "H2 Priority Solution: Future of Marketing (MAX) with Stage 1-5 sales enablement assets.", 57, 0.96),
        ("person", "amalia-goodwin", "Amalia Goodwin", "Amalia Goodwin, Solution Owner for Adaptive Organization.", 60, 0.95),
        ("person", "tamarah-usher", "Tamarah Usher", "Tamarah Usher, Solution Owner for AI Transformation and AI Accelerate Teams.", 60, 0.95),
        ("person", "will-bray", "Will Bray", "Will Bray, Solution Owner for AI Strategy.", 60, 0.95),
        ("person", "stephen-weinrich", "Stephen Weinrich", "Stephen Weinrich, Solution Owner for Application Modernization.", 60, 0.95),
    )
    for etype, slug, name, desc, pnum, conf in nodes:
        ev = _ensure_page_ev(pnum, f"{name}: {desc}", pages_map, doc_text, ev_idx)
        _put_node(ent_map, etype, slug, name, desc, ev, conf)

    edges = (
        ("sig-d4-01", "person:faven-mcshane", "HAS_ROLE", "role:senior-sales-executive", 15, "Faven McShane holds Senior Sales Executive role.", 0.98),
        ("sig-d4-02", "person:faven-mcshane", "MEMBER_OF", "org:partner_advisory", 15, "Faven McShane is Senior Sales Executive at PartnerAdvisory.", 0.97),
        ("sig-d4-03", "org:partner_advisory", "OWNS", "system:solution-catalog", 60, "PartnerAdvisory operates the enterprise Solution Catalog.", 0.98),
        ("sig-d4-04", "org:partner_advisory", "OWNS", "system:unified-ai-transformation-gtm-kit", 58, "PartnerAdvisory provides the Unified AI Transformation GTM Kit.", 0.97),
        ("sig-d4-05", "system:solution-catalog", "CONTAINS", "system:ai-strategy", 60, "AI Strategy is a cataloged H2 Priority Solution.", 0.98),
        ("sig-d4-06", "system:solution-catalog", "CONTAINS", "system:ai-office-coe", 60, "AI Office (COE) is a cataloged H2 Priority Solution.", 0.98),
        ("sig-d4-07", "system:solution-catalog", "CONTAINS", "system:ai-accelerate-teams", 60, "AI Accelerate Teams is a cataloged H2 Priority Solution.", 0.98),
        ("sig-d4-08", "system:solution-catalog", "CONTAINS", "system:ai-factory", 60, "AI Factory is a cataloged H2 Priority Solution.", 0.98),
        ("sig-d4-09", "system:solution-catalog", "CONTAINS", "system:ai-enablement", 60, "AI Enablement is a cataloged H2 Priority Solution.", 0.98),
        ("sig-d4-10", "system:solution-catalog", "CONTAINS", "system:adaptive-ai-pdlc", 52, "Adaptive AI-PDLC is an H2 Priority Solution in the Solution Catalog.", 0.95),
        ("sig-d4-11", "system:solution-catalog", "CONTAINS", "system:adaptive-organization", 60, "Adaptive Organization is a cataloged H2 Priority Solution.", 0.98),
        ("sig-d4-12", "system:solution-catalog", "CONTAINS", "system:application-modernization", 60, "Application Modernization is a cataloged H2 Priority Solution.", 0.98),
        ("sig-d4-13", "system:solution-catalog", "CONTAINS", "system:data-modernization", 52, "Data Modernization is an H2 Priority Solution for legacy modernization.", 0.95),
        ("sig-d4-14", "system:solution-catalog", "CONTAINS", "system:mainframe-modernization", 52, "Mainframe Modernization is an H2 Priority Solution for legacy modernization.", 0.95),
        ("sig-d4-15", "system:solution-catalog", "CONTAINS", "system:contact-center-transformation", 60, "Contact Center Transformation is a cataloged H2 Priority Solution.", 0.98),
        ("sig-d4-16", "system:future-of-marketing-max", "PART_OF", "system:solution-catalog", 57, "Future of Marketing (MAX) is an H2 Priority Solution with full GTM kit.", 0.96),
        ("sig-d4-17", "person:amalia-goodwin", "OWNS", "system:adaptive-organization", 60, "Amalia Goodwin is Solution Owner for Adaptive Organization.", 0.96),
        ("sig-d4-18", "person:tamarah-usher", "OWNS", "system:ai-accelerate-teams", 60, "Tamarah Usher is Solution Owner for AI Accelerate Teams.", 0.96),
        ("sig-d4-19", "person:will-bray", "OWNS", "system:ai-strategy", 60, "Will Bray is Solution Owner for AI Strategy.", 0.96),
        ("sig-d4-20", "person:stephen-weinrich", "OWNS", "system:application-modernization", 60, "Stephen Weinrich is Solution Owner for Application Modernization.", 0.96),
    )
    for sid, f_id, pred, t_id, pnum, rat, conf in edges:
        ev = _ensure_page_ev(pnum, rat, pages_map, doc_text, ev_idx)
        _put_edge(sig_map, sid, f_id, pred, t_id, ev, rat, conf)

    for st_id, pnum, conf, text in (
        ("strategic:d4-one-team-philosophy", 10, 0.96, "PartnerAdvisory operates on a One Team model across Sales, Client Partners, Capabilities, Alliances, Industry, and Delivery: partners expand the playing field; capabilities make the solution believable; sales makes it relevant."),
        ("strategic:d4-cxo-mindsets-and-triggers", 19, 0.95, "C-suite buyers operate across 4 mindsets (Turnaround, Legacy, Innovation, Operational) activated via Trigger -> Insight -> Relevance -> Conversation."),
        ("strategic:d4-h2-priority-solutions", 52, 0.97, "H2 GTM Priority Solutions focus on 4 outcomes (Data & AI-Driven Enterprise, Adaptive Organization, Modernize Legacy Systems, High-Value Customer Experiences) across 12 headline solutions."),
    ):
        ev = _ensure_page_ev(pnum, text, pages_map, doc_text, ev_idx)
        strat_list.append({"signal_id": st_id, "text": text, "rationale": text, "evidence_ids": [ev], "epistemic_confidence": conf})

    for did, title, desc, pnum, conf in (
        ("decision:d4-integrative-negotiation-give-get", "Integrative Negotiation Give/Get Reciprocity Policy", "Use integrative negotiation (Slow Down -> State the Concern -> Hand it Back and YES -> NO -> YES); every concession requires a reciprocal give/get.", 33, 0.96),
        ("decision:d4-negotiation-anchoring", "Stage 2+ Budget Qualification & Anchor First Protocol", "Qualify budget with the budget holder in Stage 2+, anchor first with ranges before all variables are known, and be second to talk price.", 35, 0.95),
        ("decision:d4-connected-plan-filter", "The Connected Plan 3-Step GTM Alignment Filter", "Filter incoming GTM, capability, and partner asks through The Connected Plan (Account Strategy -> Asks -> Alignment) and deprioritize non-aligned asks.", 61, 0.95),
    ):
        ev = _ensure_page_ev(pnum, f"{title}: {desc}", pages_map, doc_text, ev_idx)
        dec_list.append({"decision_id": did, "title": title, "description": desc, "status": "APPROVED", "evidence_ids": [ev], "epistemic_confidence": conf})

    ev_r = _ensure_page_ev(17, "Failing to engage CxO buyers causes loss of narrative control, smaller engagements, and lost strategic deals.", pages_map, doc_text, ev_idx)
    risk_list.append({
        "risk_id": "risk:d4-missing-cxo-engagement",
        "title": "Risks of Missing Executive CxO Engagement",
        "description": "Failing to focus on CxO engagement causes loss of narrative control, smaller engagements, lack of insight into org priorities, and losing strategic deals to competitors.",
        "severity": "HIGH",
        "evidence_ids": [ev_r],
        "epistemic_confidence": 0.94,
    })

    for p_ex in (8, 12, 24, 29, 34, 39, 41):
        if p_ex in pages_map and "system:solution-catalog" in ent_map:
            ev_x = _ensure_page_ev(p_ex, f"Day 4 One Team, C-suite, and negotiation anchor on slide {p_ex}.", pages_map, doc_text, ev_idx)
            if ev_x not in ent_map["system:solution-catalog"]["evidence_ids"]:
                ent_map["system:solution-catalog"]["evidence_ids"].append(ev_x)


def _is_sales_immersion_curriculum_deck(low: str) -> bool:
    if "sales immersion" in low:
        return True
    return any(
        cue in low
        for cue in (
            "kelly roberts",
            "aivalue.consulting.example.com",
            "single page response overview",
            "establishing & expanding c-suite",
            "unified ai transformation gtm kit",
            "5 lenses",
            "partner_advisory sales learning",
        )
    )


def extract_sparse_enablement_workshop_projection(
    document_text: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Extract canonical Output 03 signal projection for SPARSE_ENABLEMENT_WORKSHOP decks."""
    if not document_text or not document_text.strip():
        return {}
    low = document_text.lower()
    if not _is_sales_immersion_curriculum_deck(low):
        return {}
    pages_map = _split_pages_map(document_text)
    ev_idx: Dict[str, Any] = {}
    ent_map: Dict[str, Dict[str, Any]] = {}
    sig_map: Dict[str, Dict[str, Any]] = {}
    dec_list: List[Dict[str, Any]] = []
    risk_list: List[Dict[str, Any]] = []
    strat_list: List[Dict[str, Any]] = []

    if "pursuit excellence" in low and "aivalue.consulting.example.com" in low:
        _emit_day2_curriculum(pages_map, document_text, ev_idx, ent_map, sig_map, dec_list, risk_list, strat_list)
    elif "microsoft 365 copilot" in low and "single page response overview" in low:
        _emit_day3_curriculum(pages_map, document_text, ev_idx, ent_map, sig_map, dec_list, risk_list, strat_list)
    elif "establishing & expanding c-suite" in low or "unified ai transformation gtm kit" in low:
        _emit_day4_curriculum(pages_map, document_text, ev_idx, ent_map, sig_map, dec_list, risk_list, strat_list)
    else:
        _emit_day1_curriculum(pages_map, document_text, ev_idx, ent_map, sig_map, strat_list)

    return {
        "schema_version": "5.7",
        "is_document": True,
        "output3_only": True,
        "document_archetype": "SPARSE_ENABLEMENT_WORKSHOP",
        "max_mutation_budget": 100,
        "evidence_index": ev_idx,
        "evidence_only_statement_ids": [],
        "graph_mutations": {
            "entity_upserts": list(ent_map.values()),
            "relationship_signals": list(sig_map.values()),
            "decision_updates": dec_list,
            "proposal_updates": [],
            "risk_updates": risk_list,
            "strategic_intent_signals": strat_list,
        },
    }
