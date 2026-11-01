"""
End-to-end lifecycle tests for the Pre-Execution Document Ingestion Planner
and PartnerAdvisory Career Framework Role Profiles catalog extractor.
"""
from __future__ import annotations

import json
from pathlib import Path

from dual_pipeline.canonical_pipeline.operation_contracts import CanonicalModality
from dual_pipeline.canonical_pipeline.pipeline import CanonicalAgenticPipeline
from dual_pipeline.document_pipeline_core.archetype_classifier import (
    DocumentArchetype,
    classify_document_archetype,
)
from dual_pipeline.document_pipeline_core.ingestion_planner import (
    HIGH_SIGNAL_ROLE_SPECS,
    build_document_ingestion_plan,
    extract_role_framework_catalog_projection,
    persist_document_ingestion_plan,
)


def _build_partner_advisory_role_catalog_fixture() -> str:
    return """## Page 1
me@partner_advisory: Career Framework Role Profiles
Last Updated: November 19, 2025
Understanding Role Profile Components: Role Description, Core Skills, Relationship to Other Roles, Role Milestones, Other Roles to Explore.

## Page 5
Functional Roles Index:
AI Coach, Agile Coach, Business Analyst, Product Manager, Product Owner, Program Manager, Project Manager, Scrum Master, Visual Designer, Customer Strategist, Talent Strategist.

## Page 9
AI Coach
Also Known As: AI Strategist
Role Description: Assesses organizational readiness and guides responsible AI adoption, governance, and workflow transformation.
Core Skills: Continuous Process Improvement, Learning Strategy, Organizational Culture Transformation, Responsible AI, Stakeholder Alignment & Buy-in, Workshop Facilitation, Leadership Coaching.
Relationship to Other Roles: Partners with Data Strategist and Data Scientist.

## Page 14
Agile Coach
Also Known As: Enterprise Agile Coach, Product Coach, Agile Consultant, Agile Facilitator, Scrum Coach, Kanban Coach
Role Description: Enables agile values, principles, and execution across multi-team pods and leadership.
Core Skills: Agile Methodologies, Agile Execution, Servant Leadership, Product Backlog Management & Facilitation.
Relationship to Other Roles: Partners with Engagement Lead, Scrum Master, Project Manager, Product Owner, and Product Manager.

## Page 18
Business Analyst
Role Description: Partners with stakeholders to elicit, analyze, and document requirements, user stories, and acceptance criteria.
Core Skills: Requirements Elicitation, Analysis, & Documentation, User Story Creation, Stakeholder Engagement & Communication, Issue-based Problem Solving.

## Page 75
Product Manager
Role Description: Owns multi-year product vision, roadmap, Epic-level requirements, and business outcome measurement.
Core Skills: Strategic Planning, Product Roadmapping, Benefits Measurement & Tracking, Market Analysis, Competitive Research & Analysis.

## Page 79
Product Owner
Role Description: Agile team member defining, prioritizing, and evaluating product backlog and user stories to maximize end-user value.
Core Skills: Agile Methodologies, User Story Creation, Product Backlog Management & Facilitation, Hypothesis-driven Research & Analysis, Cross-functional Team Leadership.

## Page 86
Program Manager
Role Description: Plans, budgets, oversees, and aligns multiple interdependent projects to program and business goals.
Core Skills: Conflict Management, Negotiation, Resource Management, Stakeholder Management, Change Leadership, Project & Portfolio Management (PPM) Tools.

## Page 90
Project Manager
Also Known As: Project Coordinator, Implementation Manager
Role Description: Plans and leads project execution across scheduling, scope, budget, resource management, and risk/issue resolution.
Core Skills: Schedule Management, Scope Management, Resource Management, Communication Management, Budgeting & Forecasting, Quality Management, Risk & Issue Management, Integration Management.

## Page 95
Scrum Master
Role Description: Facilitates Scrum ceremonies (Daily Standup, Sprint Planning, Refinement, Review, Retrospective) and removes team impediments.
Core Skills: Agile Ceremony Execution, Cross-functional Team Leadership, Agile Methodologies, Product Backlog Management & Facilitation.

## Page 104
Visual Designer
Role Description: Low-signal creative graphic design role with 4-tier Consultant/Senior/Principal/Director Role Milestones.

## Page 127
Data Architect
Also Known As: Data Solution Architect, Cloud Data Architect
Role Description: Designs solution architecture for analytical platforms and data-enabled solutions across data engineering and data science.
Core Skills: Strategic Planning, Data Privacy & Protection, Modern Data Architecture (Cloud/Big Data), Technology Strategy & Roadmap Development, Solution Architecture.

## Page 157
Engineering Lead
Also Known As: Dev Lead, Lead Developer, Squad Lead
Role Description: Leads an agile development squad, owning component architecture, code quality, risk management, and technical execution.
Core Skills: Engineering Domain Expertise, Agile Development Process, Quality Assurance, Cross-functional Team Leadership.

## Page 165
Machine Learning Architect
Also Known As: AI/ML Solution Architect, GenAI Architect, Full Stack Data Scientist
Role Description: Designs and deploys production ML and Generative AI systems, LLM integrations, prompt engineering, and MLOps pipelines.
Core Skills: Machine Learning, Scalable Machine Learning Deployment, Artificial Intelligence/Machine Learning Operations, Generative AI Application Development, Large Language Model (LLM) Integration, Prompt Engineering, Responsible AI.

## Page 192
Solution Architect
Also Known As: Architect, Technical Architect
Role Description: Owns end-to-end technical architecture, cross-domain system design, scope estimation, and engineering quality.
Core Skills: Quality Assurance, Cross-functional Team Leadership, Custom Development, Enterprise Application, Application Design, Application Integration, Technical Analysis.

## Page 197
Solution Owner
Also Known As: Solution Analyst, Senior Solution Owner
Role Description: Lead-from-the-front technology and agile delivery expert dedicated to a single project under PartnerAdvisory's Product Engineering Methodology (PEM).
Core Skills: Agile Software Delivery, Project Management, Requirements Management, Facilitation, Product Engineering Methodology (PEM).

## Page 203
Technology Strategist
Also Known As: Strategist
Role Description: Bridges business and IT across core technology domains to build enterprise technology strategy and execution roadmaps.
Core Skills: Solution Architecture, Capability Development, Strategic Planning, Strategic Roadmap & Execution Plans, Facilitation.

## Page 207
Capability Lead
Also Known As: Practice Lead, Practice Director
Role Description: Leads practice capability strategy, talent development, operational health, and delivery quality across engagements.

## Page 212
Client Partner
Also Known As: Account Lead
Role Description: Owns client account strategy, executive relationships, and commercial partnership alongside Accountable Executives.

## Page 223
Accountable Executive
Role Description: Ultimately accountable for engagement outcomes across client, team, and PartnerAdvisory across sales and delivery.
Core Skills: Executive Management & Communications, Program Management, Risk & Issue Management, Conflict Management, Procurement Management, Deal Strategy Development, Deal Structure.

## Page 234
Engagement Lead
Role Description: Day-to-day engagement glue ensuring client satisfaction, project operational health, and cross-leadership alignment.
Core Skills: Executive Management & Communication, Risk Management, Stakeholder Management, Budgeting and Forecasting, Scope Management, Schedule Management, Team Management, Procurement Management.

## Page 242
Salesforce Supplement
Role Milestones for Salesforce functional and technical tracks.
"""


def test_archetype_classifier_detects_role_framework_catalog() -> None:
    doc_text = _build_partner_advisory_role_catalog_fixture()
    arch = classify_document_archetype(doc_text, {})
    assert arch == DocumentArchetype.ROLE_FRAMEWORK_CATALOG


def test_pre_execution_plan_and_role_catalog_extraction(tmp_path: Path) -> None:
    doc_text = _build_partner_advisory_role_catalog_fixture()
    plan = build_document_ingestion_plan(doc_text, {"filename_slug": "PartnerAdvisory_Career_Roles"})
    assert plan["planned_before_execution"] is True
    assert plan["document_archetype"] == "ROLE_FRAMEWORK_CATALOG"
    assert plan["selected_high_signal_count"] == len(HIGH_SIGNAL_ROLE_SPECS)

    plan_path = persist_document_ingestion_plan(plan, tmp_path, slug="PartnerAdvisory_Career_Roles")
    assert plan_path.exists()
    persisted = json.loads(plan_path.read_text(encoding="utf-8"))
    assert persisted["plan_id"] == plan["plan_id"]

    projection = extract_role_framework_catalog_projection(doc_text, {"document_ingestion_plan": plan})
    upserts = projection["graph_mutations"]["entity_upserts"]
    entity_ids = {e["id"] for e in upserts}

    for spec in HIGH_SIGNAL_ROLE_SPECS:
        expected_id = f"role:{spec['name'].lower().replace(' ', '-')}"
        assert expected_id in entity_ids, f"Missing expected high-signal role: {expected_id}"

    assert "role:visual-designer" not in entity_ids
    assert "role:customer-strategist" not in entity_ids
    assert "role:talent-strategist" not in entity_ids
    assert "system:product-engineering-methodology-pem" in entity_ids


def test_canonical_pipeline_document_creates_plan_before_l2_e2e(tmp_path: Path) -> None:
    doc_text = _build_partner_advisory_role_catalog_fixture()
    src_file = tmp_path / "2025-11-19_PartnerAdvisory_Career_Framework_Role_Profiles.md"
    src_file.write_text(doc_text, encoding="utf-8")
    out_dir = tmp_path / "sandbox_out"

    pipeline = CanonicalAgenticPipeline(provider_type="offline", modality=CanonicalModality.DOCUMENT)
    meta = {
        "project": "internal_consulting",
        "meeting_name": "PartnerAdvisory_Career_Framework_Role_Profiles",
        "filename_slug": "2025-11-19_PartnerAdvisory_Career_Framework_Role_Profiles",
        "date": "11/19/2025",
        "is_document": True,
        "output3_only": True,
        "source_type": "document",
        "test_fast": True,
    }
    res = pipeline.run(
        input_path=src_file,
        metadata=meta,
        output_dir=str(out_dir),
        mutate_graph=False,
        auto_mutate_on_pass=False,
        test_fast=True,
    )

    plan_files = list(out_dir.rglob("00_*_ingestion_plan.json"))
    assert plan_files, "Stage C0-PA must persist 00_<slug>_ingestion_plan.json before Stage L2"
    plan_data = json.loads(plan_files[0].read_text(encoding="utf-8"))
    assert plan_data["document_archetype"] == "ROLE_FRAMEWORK_CATALOG"
    assert plan_data["selected_high_signal_count"] == 18

    out3_files = list(out_dir.rglob("03_*_canonical_signal_projection.json"))
    assert out3_files, "Pipeline must emit 03_*_canonical_signal_projection.json"
    out3 = json.loads(out3_files[0].read_text(encoding="utf-8"))
    ent_ids = {e["id"] for e in out3["graph_mutations"]["entity_upserts"]}
    assert "role:solution-owner" in ent_ids
    assert "role:project-manager" in ent_ids
    assert "role:machine-learning-architect" in ent_ids
    assert "role:visual-designer" not in ent_ids


def test_wave2_authoritative_partner_advisory_career_framework_intake_e2e(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parent.parent
    intake_file = repo_root / "intake" / "2025-11-19_PartnerAdvisory_Career_Framework_Role_Profiles.md"
    assert intake_file.is_file(), "Authoritative intake file must exist in intake/"
    out_dir = tmp_path / "wave2_sandbox_out"

    pipeline = CanonicalAgenticPipeline(provider_type="offline", modality=CanonicalModality.DOCUMENT)
    meta = {
        "project": "internal_consulting",
        "meeting_name": "PartnerAdvisory_Career_Framework_Role_Profiles",
        "filename_slug": "2025-11-19_PartnerAdvisory_Career_Framework_Role_Profiles",
        "date": "11/19/2025",
        "is_document": True,
        "output3_only": True,
        "source_type": "document",
        "test_fast": True,
    }
    res = pipeline.run(
        input_path=intake_file,
        metadata=meta,
        output_dir=str(out_dir),
        mutate_graph=False,
        auto_mutate_on_pass=False,
        test_fast=True,
    )
    assert res.get("status") in ("COMPLETE", "SUCCESS", "PASS", None)

    plan_files = list(out_dir.rglob("00_*_ingestion_plan.json"))
    assert len(plan_files) == 1
    plan_data = json.loads(plan_files[0].read_text(encoding="utf-8"))
    assert plan_data["document_archetype"] == "ROLE_FRAMEWORK_CATALOG"
    assert plan_data["total_pages_detected"] == 256
    assert plan_data["selected_high_signal_count"] == 18

    out3_files = [p for p in out_dir.rglob("03_*_canonical_signal_projection.json") if "attempts" not in p.parts]
    assert len(out3_files) == 1
    out3 = json.loads(out3_files[0].read_text(encoding="utf-8"))
    upserts = out3["graph_mutations"]["entity_upserts"]
    signals = out3["graph_mutations"]["relationship_signals"]
    ent_by_id = {e["id"]: e for e in upserts}

    for spec in HIGH_SIGNAL_ROLE_SPECS:
        rid = f"role:{spec['name'].lower().replace(' ', '-')}"
        assert rid in ent_by_id, f"Missing high-signal role {rid} in Wave 2 Output 03"
        assert ent_by_id[rid]["evidence_ids"], f"Role {rid} must cite source evidence"

    assert "role:visual-designer" not in ent_by_id
    assert "system:product-engineering-methodology-pem" in ent_by_id
    assert len(signals) >= 40

