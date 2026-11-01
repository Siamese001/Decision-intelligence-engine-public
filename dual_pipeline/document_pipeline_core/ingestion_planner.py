"""
dual_pipeline/document_pipeline_core/ingestion_planner.py

Pre-execution document ingestion planner and high-signal PartnerAdvisory Career Framework
Role Profiles catalog extractor. Generates a deterministic ingestion plan
(`00_<slug>_ingestion_plan.json`) in Stage C0-PA before Stage L2 synthesis,
and extracts curated high-signal role entities, aliases, skills, and inter-role
relationships while excluding low-signal roles and 4-tier milestone bloat.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from dual_pipeline.document_pipeline_core.archetype_classifier import DocumentArchetype, classify_document_archetype
from dual_pipeline.document_pipeline_core.extractor_evidence_utils import build_grounded_evidence_entry, slugify_entity_id

HIGH_SIGNAL_ROLE_SPECS: Tuple[Dict[str, Any], ...] = (
    {
        "name": "AI Coach",
        "category": "Functional",
        "tier": "Tier 2: AI, Data & Technical Architecture Core",
        "default_page": 9,
        "aliases": ("AI Strategist",),
        "skills": (
            "Continuous Process Improvement", "Learning Strategy",
            "Organizational Culture Transformation", "Responsible AI",
            "Stakeholder Alignment & Buy-in", "Workshop Facilitation", "Leadership Coaching",
        ),
        "partners": ("Data Strategist", "Data Scientist"),
        "summary": "Assesses organizational readiness and guides responsible AI adoption, governance, and workflow transformation.",
    },
    {
        "name": "Agile Coach",
        "category": "Functional",
        "tier": "Tier 1: Pod Delivery & Backfill Core",
        "default_page": 14,
        "aliases": ("Enterprise Agile Coach", "Product Coach", "Agile Consultant", "Agile Facilitator", "Scrum Coach", "Kanban Coach"),
        "skills": ("Agile Methodologies", "Agile Execution", "Servant Leadership", "Product Backlog Management & Facilitation"),
        "partners": ("Engagement Lead", "Scrum Master", "Project Manager", "Product Owner", "Product Manager"),
        "summary": "Enables agile values, principles, and execution across multi-team pods and leadership.",
    },
    {
        "name": "Business Analyst",
        "category": "Functional",
        "tier": "Tier 1: Pod Delivery & Backfill Core",
        "default_page": 18,
        "aliases": ("BA",),
        "skills": ("Requirements Elicitation, Analysis, & Documentation", "User Story Creation", "Stakeholder Engagement & Communication", "Issue-based Problem Solving"),
        "partners": ("Project Manager", "Data Engineer", "Data Scientist"),
        "summary": "Partners with stakeholders to elicit, analyze, and document requirements, user stories, and acceptance criteria.",
    },
    {
        "name": "Product Manager",
        "category": "Functional",
        "tier": "Tier 1: Pod Delivery & Backfill Core",
        "default_page": 75,
        "aliases": ("PM",),
        "skills": ("Strategic Planning", "Product Roadmapping", "Benefits Measurement & Tracking", "Market Analysis", "Competitive Research & Analysis"),
        "partners": ("Product Owner", "Solution Owner"),
        "summary": "Owns multi-year product vision, roadmap, Epic-level requirements, and business outcome measurement.",
    },
    {
        "name": "Product Owner",
        "category": "Functional",
        "tier": "Tier 1: Pod Delivery & Backfill Core",
        "default_page": 79,
        "aliases": ("PO",),
        "skills": ("Agile Methodologies", "User Story Creation", "Product Backlog Management & Facilitation", "Hypothesis-driven Research & Analysis", "Cross-functional Team Leadership"),
        "partners": ("Product Manager", "Solution Owner"),
        "summary": "Agile team member defining, prioritizing, and evaluating product backlog and user stories to maximize end-user value.",
    },
    {
        "name": "Program Manager",
        "category": "Functional",
        "tier": "Tier 1: Pod Delivery & Backfill Core",
        "default_page": 86,
        "aliases": ("PgM",),
        "skills": ("Conflict Management", "Negotiation", "Resource Management", "Stakeholder Management", "Change Leadership", "Project & Portfolio Management (PPM) Tools"),
        "partners": ("Accountable Executive", "Engagement Lead", "Project Manager"),
        "summary": "Plans, budgets, oversees, and aligns multiple interdependent projects to program and business goals.",
    },
    {
        "name": "Project Manager",
        "category": "Functional",
        "tier": "Tier 1: Pod Delivery & Backfill Core",
        "default_page": 90,
        "aliases": ("Project Coordinator", "Implementation Manager"),
        "skills": ("Schedule Management", "Scope Management", "Resource Management", "Communication Management", "Budgeting & Forecasting", "Quality Management", "Risk & Issue Management", "Integration Management"),
        "partners": ("Accountable Executive", "Engagement Lead", "Program Manager", "Product Owner", "Business Analyst"),
        "summary": "Plans and leads project execution across scheduling, scope, budget, resource management, and risk/issue resolution.",
    },
    {
        "name": "Scrum Master",
        "category": "Functional",
        "tier": "Tier 1: Pod Delivery & Backfill Core",
        "default_page": 95,
        "aliases": ("SM",),
        "skills": ("Agile Ceremony Execution", "Cross-functional Team Leadership", "Agile Methodologies", "Product Backlog Management & Facilitation"),
        "partners": ("Product Owner", "Solution Owner", "Agile Coach"),
        "summary": "Facilitates Scrum ceremonies (Daily Standup, Sprint Planning, Refinement, Review, Retrospective) and removes team impediments.",
    },
    {
        "name": "Data Architect",
        "category": "Technical",
        "tier": "Tier 2: AI, Data & Technical Architecture Core",
        "default_page": 127,
        "aliases": ("Data Solution Architect", "Cloud Data Architect"),
        "skills": ("Strategic Planning", "Data Privacy & Protection", "Modern Data Architecture (Cloud/Big Data)", "Technology Strategy & Roadmap Development", "Solution Architecture"),
        "partners": ("Solution Architect", "Machine Learning Architect"),
        "summary": "Designs solution architecture for analytical platforms and data-enabled solutions across data engineering and data science.",
    },
    {
        "name": "Engineering Lead",
        "category": "Technical",
        "tier": "Tier 2: AI, Data & Technical Architecture Core",
        "default_page": 157,
        "aliases": ("Dev Lead", "Lead Developer", "Squad Lead"),
        "skills": ("Engineering Domain Expertise", "Agile Development Process", "Quality Assurance", "Cross-functional Team Leadership"),
        "partners": ("Solution Architect", "Solution Owner", "Scrum Master"),
        "summary": "Leads an agile development squad, owning component architecture, code quality, risk management, and technical execution.",
    },
    {
        "name": "Machine Learning Architect",
        "category": "Technical",
        "tier": "Tier 2: AI, Data & Technical Architecture Core",
        "default_page": 165,
        "aliases": ("AI/ML Solution Architect", "GenAI Architect", "Full Stack Data Scientist"),
        "skills": ("Machine Learning", "Scalable Machine Learning Deployment", "Artificial Intelligence/Machine Learning Operations", "Generative AI Application Development", "Large Language Model (LLM) Integration", "Prompt Engineering", "Responsible AI"),
        "partners": ("Data Architect", "Solution Architect", "AI Coach"),
        "summary": "Designs and deploys production ML and Generative AI systems, LLM integrations, prompt engineering, and MLOps pipelines.",
    },
    {
        "name": "Solution Architect",
        "category": "Technical",
        "tier": "Tier 2: AI, Data & Technical Architecture Core",
        "default_page": 192,
        "aliases": ("Architect", "Technical Architect"),
        "skills": ("Quality Assurance", "Cross-functional Team Leadership", "Custom Development", "Enterprise Application", "Application Design", "Application Integration", "Technical Analysis"),
        "partners": ("Solution Owner", "Engineering Lead", "Data Architect"),
        "summary": "Owns end-to-end technical architecture, cross-domain system design, scope estimation, and engineering quality.",
    },
    {
        "name": "Solution Owner",
        "category": "Technical",
        "tier": "Tier 1: Pod Delivery & Backfill Core",
        "default_page": 197,
        "aliases": ("Solution Analyst", "Senior Solution Owner", "Delivery Lead"),
        "skills": ("Agile Software Delivery", "Project Management", "Requirements Management", "Facilitation", "Product Engineering Methodology (PEM)"),
        "partners": ("Product Owner", "Product Manager", "Scrum Master", "Solution Architect", "Engineering Lead"),
        "summary": "Lead-from-the-front technology and agile delivery expert dedicated to a single project under PartnerAdvisory's Product Engineering Methodology (PEM).",
    },
    {
        "name": "Technology Strategist",
        "category": "Technical",
        "tier": "Tier 2: AI, Data & Technical Architecture Core",
        "default_page": 203,
        "aliases": ("Strategist",),
        "skills": ("Solution Architecture", "Capability Development", "Strategic Planning", "Strategic Roadmap & Execution Plans", "Facilitation"),
        "partners": ("Product Manager", "Program Manager", "Solution Architect"),
        "summary": "Bridges business and IT across core technology domains to build enterprise technology strategy and execution roadmaps.",
    },
    {
        "name": "Capability Lead",
        "category": "Business",
        "tier": "Tier 3: Account & Engagement Governance",
        "default_page": 207,
        "aliases": ("Practice Lead", "Practice Director"),
        "skills": ("Capability Strategy", "People Development", "Solution Delivery Governance", "Operational & Financial Health"),
        "partners": ("Client Partner", "Engagement Lead", "Accountable Executive"),
        "summary": "Leads practice capability strategy, talent development, operational health, and delivery quality across engagements.",
    },
    {
        "name": "Client Partner",
        "category": "Business",
        "tier": "Tier 3: Account & Engagement Governance",
        "default_page": 212,
        "aliases": ("Account Lead",),
        "skills": ("Account Strategy", "Executive Relationship Management", "Opportunity Identification", "Commercial Growth"),
        "partners": ("Engagement Lead", "Capability Lead", "Accountable Executive"),
        "summary": "Owns client account strategy, executive relationships, and commercial partnership alongside Accountable Executives.",
    },
    {
        "name": "Accountable Executive",
        "category": "Pursuit & Engagement",
        "tier": "Tier 3: Account & Engagement Governance",
        "default_page": 223,
        "aliases": ("AE",),
        "skills": ("Executive Management & Communications", "Program Management", "Risk & Issue Management", "Conflict Management", "Procurement Management", "Deal Strategy Development", "Deal Structure"),
        "partners": ("Engagement Lead", "Client Partner", "Capability Lead", "Program Manager", "Project Manager"),
        "summary": "Ultimately accountable for engagement outcomes across client, team, and PartnerAdvisory across sales and delivery.",
    },
    {
        "name": "Engagement Lead",
        "category": "Pursuit & Engagement",
        "tier": "Tier 3: Account & Engagement Governance",
        "default_page": 234,
        "aliases": ("EL",),
        "skills": ("Executive Management & Communication", "Risk Management", "Stakeholder Management", "Budgeting and Forecasting", "Scope Management", "Schedule Management", "Team Management", "Procurement Management"),
        "partners": ("Client Partner", "Accountable Executive", "Capability Lead", "Program Manager", "Project Manager", "Agile Coach"),
        "summary": "Day-to-day engagement glue ensuring client satisfaction, project operational health, and cross-leadership alignment.",
    },
)

EXCLUDED_ROLE_CATALOG_ITEMS: Tuple[Dict[str, str], ...] = (
    {"target": "Role Milestones (Consultant / Senior / Principal / Director 4-tier matrices)", "reason": "HR rubric bloat (~65% of pages); zero project graph topology value"},
    {"target": "Visual Designer / Experience Designer / Service Designer / Content Strategist", "reason": "Zero active pod bindings in NEE/NEA/Jarvis graph"},
    {"target": "Customer Strategist / Talent Strategist / Learning & Development Specialist", "reason": "Non-active consulting specializations outside current delivery pods"},
    {"target": "Salesforce Supplement (pp. 242-256)", "reason": "Platform-specific supplement outside NEE/Jarvis AI & data stack"},
)


def _extract_unit_page_num(u: Dict[str, Any], default_idx: int) -> int:
    raw_num = u.get("unit_number") or u.get("page") or u.get("slide_number")
    if isinstance(raw_num, int) and raw_num > 0:
        return raw_num
    loc = u.get("locator")
    if isinstance(loc, str):
        m_loc = re.search(r"(\d+)", loc)
        if m_loc:
            return int(m_loc.group(1))
    return default_idx


def _extract_pages_from_units(units: List[Any]) -> Dict[int, str]:
    pages: Dict[int, str] = {}
    for idx, u in enumerate(units, start=1):
        if not isinstance(u, dict):
            continue
        content = str(u.get("content") or u.get("text") or "").strip()
        if not content:
            continue
        pnum = _extract_unit_page_num(u, idx)
        pages[pnum] = f"{pages[pnum]}\n{content}".strip() if pnum in pages else content
    return pages


def _split_explicit_page_headers(document_text: str) -> Dict[int, str]:
    pages: Dict[int, str] = {}
    parts = list(re.finditer(r"(?:^|\n)\s*##\s*(?:Page|Slide)\s+(\d+)\b", document_text or "", re.I))
    for idx, m in enumerate(parts):
        pnum = int(m.group(1))
        end = parts[idx + 1].start() if idx + 1 < len(parts) else len(document_text)
        pages[pnum] = document_text[m.end():end].strip()
    return pages


def _split_pages_map(document_text: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[int, str]:
    header_pages = _split_explicit_page_headers(document_text)
    if header_pages:
        return header_pages
    meta = metadata or {}
    for key in ("source_units", "slide_units"):
        units = meta.get(key)
        if isinstance(units, list) and units:
            unit_pages = _extract_pages_from_units(units)
            if unit_pages:
                return unit_pages
    return {1: document_text.strip()} if (document_text and document_text.strip()) else {}


def _find_role_page(role_name: str, default_page: int, pages_map: Dict[int, str], doc_text: str) -> int:
    if default_page in pages_map and re.search(rf"\b{re.escape(role_name)}\b", pages_map[default_page], re.I):
        return default_page
    pat = re.compile(rf"(?:^|\n)(?:#+\s*)?{re.escape(role_name)}\b", re.I)
    for pnum, ptxt in sorted(pages_map.items()):
        if pnum > 8 and pat.search(ptxt):
            return pnum
    for pnum, ptxt in sorted(pages_map.items()):
        if re.search(rf"\b{re.escape(role_name)}\b", ptxt, re.I):
            return pnum
    return default_page if default_page in pages_map else (min(pages_map.keys()) if pages_map else 1)


def _build_role_catalog_targets(pages_map: Dict[int, str], doc_text: str) -> List[Dict[str, Any]]:
    targets: List[Dict[str, Any]] = []
    for spec in HIGH_SIGNAL_ROLE_SPECS:
        rname = spec["name"]
        if doc_text and re.search(rf"\b{re.escape(rname)}\b", doc_text, re.I):
            pnum = _find_role_page(rname, spec["default_page"], pages_map, doc_text)
            ptxt = pages_map.get(pnum) or doc_text
            grounded_aliases = [
                a for a in spec["aliases"] if re.search(rf"\b{re.escape(a)}\b", ptxt, re.I) or re.search(rf"\b{re.escape(a)}\b", doc_text, re.I)
            ]
            grounded_partners = [
                p for p in spec["partners"] if re.search(rf"\b{re.escape(p)}\b", doc_text, re.I)
            ]
            targets.append({
                "entity_id": slugify_entity_id("role", rname),
                "name": rname,
                "category": spec["category"],
                "tier": spec["tier"],
                "page": f"p{pnum}",
                "aliases": grounded_aliases,
                "core_skills_count": len(spec["skills"]),
                "partner_roles": grounded_partners,
            })
    return targets


def _resolve_deterministic_planned_at(meta: Dict[str, Any]) -> str:
    explicit = meta.get("planned_at") or meta.get("as_of_timestamp") or meta.get("date")
    if explicit and str(explicit).strip():
        return str(explicit).strip()
    return "deterministic"


def _build_general_unit_targets(pages_map: Dict[int, str]) -> List[Dict[str, Any]]:
    from dual_pipeline.document_pipeline_core.unit_decomposition import classify_unit_sieve
    targets: List[Dict[str, Any]] = []
    for p, ptxt in sorted(pages_map.items()):
        stripped_unit = ptxt.strip()
        if len(stripped_unit) < 20:
            continue
        first_line = stripped_unit.splitlines()[0] if stripped_unit.splitlines() else f"Unit {p}"
        sieve_cls, _, is_sub = classify_unit_sieve(first_line, stripped_unit)
        if is_sub or len(pages_map) == 1:
            targets.append({"unit": f"p{p}", "chars": len(ptxt), "sieve_class": sieve_cls})
    return targets


def _resolve_plan_mutation_budget(meta: Dict[str, Any], default_budget: int) -> int:
    try:
        raw_budget = int(meta.get("max_mutation_budget") or 0)
    except (TypeError, ValueError):
        raw_budget = 0
    return raw_budget if raw_budget >= 30 else int(default_budget or 60)


def _compute_total_pages(text: str, pages_map: Dict[int, str], profile_page_count: int) -> int:
    has_explicit = bool(re.search(r"(?:^|\n)\s*##\s*(?:Page|Slide)\s+\d+\b", text or "", re.I))
    if has_explicit:
        return max(pages_map.keys(), default=1)
    if len(pages_map) > 1:
        return max(max(pages_map.keys(), default=0), profile_page_count)
    return profile_page_count if profile_page_count > 0 else (1 if pages_map else 0)


def build_document_ingestion_plan(
    document_text: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a deterministic pre-execution ingestion plan before Stage L2 document extraction."""
    from dual_pipeline.document_pipeline_core.signal_value_router import (
        assess_document_signal_profile,
        reconstruct_document_text_from_metadata,
    )

    meta = dict(metadata or {})
    text = reconstruct_document_text_from_metadata(document_text, meta)
    source_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    profile = assess_document_signal_profile(text, meta)
    arch = DocumentArchetype(profile.archetype)
    pages_map = _split_pages_map(text, meta)
    total_pages = _compute_total_pages(text, pages_map, profile.page_count)

    if arch == DocumentArchetype.ROLE_FRAMEWORK_CATALOG:
        high_signal_targets = _build_role_catalog_targets(pages_map, text)
        excluded_sections = list(EXCLUDED_ROLE_CATALOG_ITEMS)
        extraction_strategy = "CURATED_ROLE_CATALOG_SIEVE"
        mutation_budget = max(90, len(high_signal_targets) * 4, profile.dynamic_mutation_budget)
    else:
        high_signal_targets = _build_general_unit_targets(pages_map)
        excluded_sections = [{"target": "Divider/Title/Boilerplate units", "reason": "Quarantined by deterministic Q1-Q7 sieve"}]
        extraction_strategy = f"ARCHETYPE_{arch.value}"
        mutation_budget = _resolve_plan_mutation_budget(meta, profile.dynamic_mutation_budget)

    return {
        "schema_version": "1.0.0",
        "plan_id": f"doc-ingest-plan-{source_hash}",
        "source_hash": source_hash,
        "document_archetype": arch.value,
        "signal_tier": profile.signal_tier,
        "strategic_value_score": profile.strategic_value_score,
        "perspective_origin": profile.perspective_origin,
        "recommended_extraction_mode": profile.recommended_extraction_mode,
        "extraction_strategy": extraction_strategy,
        "total_pages_detected": total_pages,
        "selected_high_signal_count": len(high_signal_targets),
        "high_signal_targets": high_signal_targets,
        "excluded_sections": excluded_sections,
        "recommended_mutation_budget": mutation_budget,
        "planned_before_execution": True,
        "planned_at": _resolve_deterministic_planned_at(meta),
    }


def persist_document_ingestion_plan(
    plan: Dict[str, Any],
    output_dir: Union[str, Path],
    slug: Optional[str] = None,
) -> Path:
    """Persist the pre-execution document ingestion plan to `00_<slug>_ingestion_plan.json`."""
    out_p = Path(output_dir)
    out_p.mkdir(parents=True, exist_ok=True)
    clean_slug = re.sub(r"[^a-zA-Z0-9_\-]+", "_", (slug or "document").strip()).strip("_") or "document"
    if clean_slug.startswith(("00_", "01_", "02_", "03_")):
        clean_slug = clean_slug[3:]
    plan_path = out_p / f"00_{clean_slug}_ingestion_plan.json"
    plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    return plan_path


def _build_role_evidence(
    role_name: str,
    pnum: int,
    pages_map: Dict[int, str],
    doc_text: str,
) -> Dict[str, Any]:
    ev_id = f"ev-role-{slugify_entity_id('role', role_name).split(':', 1)[-1]}"
    ptxt = pages_map.get(pnum) or ""
    exact = role_name
    for source_block in (ptxt, doc_text):
        if exact != role_name:
            break
        for ln in source_block.splitlines():
            s = ln.strip()
            if role_name.lower() in s.lower() and s in doc_text:
                exact = s[:300]
                break
    return build_grounded_evidence_entry(
        ev_id,
        f"Role Profile: {role_name} (p{pnum}).",
        exact,
        f"p{pnum}",
        doc_text,
        locator_kind="slide",
        resolve_verbatim=True,
    )


def _add_role_entity_and_edges(
    spec: Dict[str, Any],
    pnum: int,
    ev_id: str,
    ent_map: Dict[str, Dict[str, Any]],
    sig_map: Dict[str, Dict[str, Any]],
    valid_role_ids: set[str],
    doc_text: str = "",
    has_partner_advisory_org: bool = True,
) -> None:
    rname = spec["name"]
    rid = slugify_entity_id("role", rname)
    grounded_aliases = [
        a for a in spec["aliases"]
        if not doc_text or bool(re.search(rf"\b{re.escape(a)}\b", doc_text, re.I))
    ]
    grounded_skills = [
        s for s in spec["skills"]
        if not doc_text or bool(re.search(rf"\b{re.escape(s)}\b", doc_text, re.I))
    ][:6]
    org_prefix = "PartnerAdvisory " if has_partner_advisory_org else ""
    parts = [f"{org_prefix}{spec['category']} Role ({spec['tier']}). {spec['summary']}"]
    if grounded_aliases:
        parts.append(f"Also Known As: {', '.join(grounded_aliases)}.")
    if grounded_skills:
        parts.append(f"Core Skills: {', '.join(grounded_skills)}.")
    ent_map[rid] = {
        "id": rid, "type": "role", "name": rname, "aliases": grounded_aliases,
        "description": " ".join(parts)[:450], "mutation": "CREATE",
        "epistemic_status": "CONFIRMED", "evidence_ids": [ev_id],
    }
    if has_partner_advisory_org:
        sig_org = f"sig-partner_advisory-owns-{rid.split(':', 1)[-1]}"
        sig_map[sig_org] = {
            "signal_id": sig_org, "from_id": "org:partner_advisory", "to_id": rid,
            "relationship": "OWNS", "predicate": "OWNS", "evidence_ids": [ev_id],
            "mutation": "CONFIRM", "epistemic_status": "CONFIRMED",
        }
    for partner_name in spec["partners"]:
        pid = slugify_entity_id("role", partner_name)
        if pid in valid_role_ids and pid != rid:
            sig_id = f"sig-{rid.split(':', 1)[-1]}-partners-{pid.split(':', 1)[-1]}"
            sig_map[sig_id] = {
                "signal_id": sig_id, "from_id": rid, "to_id": pid,
                "relationship": "PARTNERS_WITH", "predicate": "PARTNERS_WITH", "evidence_ids": [ev_id],
                "mutation": "CONFIRM", "epistemic_status": "CONFIRMED",
            }


def _seed_partner_advisory_root_entity(document_text: str, ev_idx: Dict[str, Any], ent_map: Dict[str, Dict[str, Any]]) -> None:
    root_quote = "Career Framework Role Profiles" if "career framework role profiles" in document_text.lower() else "PartnerAdvisory"
    ev_root = build_grounded_evidence_entry(
        "ev-partner_advisory-career-framework-root",
        "me@partner_advisory: Career Framework Role Profiles authoritative catalog.",
        root_quote, "p1", document_text, locator_kind="slide", resolve_verbatim=True,
    )
    ev_idx[ev_root["evidence_id"]] = ev_root
    ent_map["org:partner_advisory"] = {
        "id": "org:partner_advisory", "type": "org", "name": "PartnerAdvisory",
        "description": "PartnerAdvisory consulting organization owning the me@partner_advisory Career Framework Role Profiles.",
        "mutation": "CONFIRM", "epistemic_status": "CONFIRMED", "evidence_ids": [ev_root["evidence_id"]],
    }


def _seed_pem_methodology_entity(
    document_text: str, ent_map: Dict[str, Dict[str, Any]], sig_map: Dict[str, Dict[str, Any]]
) -> None:
    if not (re.search(r"Product\s+Engineering\s+Methodology\s*\(PEM\)", document_text, re.I) and "role:solution-owner" in ent_map):
        return
    so_ev = ent_map["role:solution-owner"]["evidence_ids"][0]
    ent_map["system:product-engineering-methodology-pem"] = {
        "id": "system:product-engineering-methodology-pem", "type": "system",
        "name": "Product Engineering Methodology (PEM)",
        "description": "PartnerAdvisory's agile delivery methodology led by Solution Owners across multi-functional pods.",
        "mutation": "CREATE", "epistemic_status": "CONFIRMED", "evidence_ids": [so_ev],
    }
    sig_map["sig-solution-owner-uses-pem"] = {
        "signal_id": "sig-solution-owner-uses-pem", "from_id": "role:solution-owner",
        "to_id": "system:product-engineering-methodology-pem", "relationship": "USES",
        "predicate": "USES", "evidence_ids": [so_ev], "mutation": "CONFIRM", "epistemic_status": "CONFIRMED",
    }


def extract_role_framework_catalog_projection(
    document_text: str,
    document_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Deterministic extractor for ROLE_FRAMEWORK_CATALOG documents guided by the pre-execution plan."""
    meta = dict(document_metadata or {})
    if "document_ingestion_plan" not in meta:
        meta["document_ingestion_plan"] = build_document_ingestion_plan(document_text, meta)
    plan = meta["document_ingestion_plan"] if isinstance(meta.get("document_ingestion_plan"), dict) else {}
    max_budget = max(1, int(plan.get("recommended_mutation_budget") or 150))
    pages_map = _split_pages_map(document_text, meta)
    ev_idx: Dict[str, Any] = {}
    ent_map: Dict[str, Dict[str, Any]] = {}
    sig_map: Dict[str, Dict[str, Any]] = {}

    matched_specs = [spec for spec in HIGH_SIGNAL_ROLE_SPECS if re.search(rf"\b{re.escape(spec['name'])}\b", document_text, re.I)]
    valid_role_ids = {slugify_entity_id("role", s["name"]) for s in matched_specs}
    has_partner_advisory_org = bool(re.search(r"\bpartner_advisory\b|Career\s+Framework\s+Role\s+Profiles", document_text, re.I))
    if has_partner_advisory_org:
        _seed_partner_advisory_root_entity(document_text, ev_idx, ent_map)

    for spec in matched_specs:
        pnum = _find_role_page(spec["name"], spec["default_page"], pages_map, document_text)
        ev_entry = _build_role_evidence(spec["name"], pnum, pages_map, document_text)
        ev_id = ev_entry["evidence_id"]
        ev_idx[ev_id] = ev_entry
        _add_role_entity_and_edges(spec, pnum, ev_id, ent_map, sig_map, valid_role_ids, doc_text=document_text, has_partner_advisory_org=has_partner_advisory_org)

    _seed_pem_methodology_entity(document_text, ent_map, sig_map)
    entity_upserts = list(ent_map.values())
    relationship_signals = list(sig_map.values())
    max_budget = max(max_budget, len(entity_upserts) + len(relationship_signals))

    return {
        "schema_version": "1.0.0", "is_document": True, "output3_only": True,
        "max_mutation_budget": max_budget, "evidence_index": ev_idx,
        "evidence_only_statement_ids": [],
        "graph_mutations": {
            "entity_upserts": entity_upserts, "relationship_signals": relationship_signals,
            "decision_updates": [], "proposal_updates": [], "risk_updates": [], "strategic_intent_signals": [],
        },
    }
