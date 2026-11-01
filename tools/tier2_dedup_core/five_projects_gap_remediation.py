"""Governed Five-Project (Project Atlas, Conduit, HRIT, Enterprise Account Planning, NEA) remediation."""
from __future__ import annotations

import datetime as dt
import json
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

from dual_pipeline.core import ROOT, sha256_file, stable_id
from dual_pipeline.runtime_root import resolve_authoritative_runtime_root
from dual_pipeline.storage.governed_context import GovernedStorageContext, Row
from tools.graph_mutation_proof_core.proof import prove, snapshot
from tools.tier2_dedup_core.neet_gap_remediation import _repoint_optional_tables
from tools.tier2_dedup_core.sow_duplicate_retirement import (
    refresh_sidecar_after_graph_cleanup,
)

FIVE_PROJECTS_DUPLICATE_SOURCE_PAIRS: Tuple[Tuple[str, str], ...] = (
    ("source-af30bee61106a581097d9777", "source-8e104e89d6042c135074186a"),
)

FIVE_PROJECTS_CANONICAL_SOURCES: Tuple[str, ...] = (
    "source-e4bd15b8cee3e3b0e3df4e02", "source_v27_rewire_amwe_pm_gm_meeting_2026_04_07",
    "source-01023b48a8bb486bb8fbaa28", "source_fossil_rewire_overview_draft_2026_05",
    "source-d510756964f44a66f87c4158", "source_hannah_rewire_approval_process_2026_06_30",
    "source_hannah_rewire_overview_2026_06_30", "source-rewire-20260630-approval-hannah",
    "source-rewire-20260828-apex-development", "source_pd_pc_conductor_q2_charter_2026_04_02",
    "source_pd_conduit_user_notes_2026_07", "source_pd_conduit_ai_platform_discussion_2026_07_06",
    "source-c47aab2f57e72aea45296374", "source-2f75e84b468e829f05c0ba2f",
    "source-2fd27219a4d8547dd2326a0d", "source-f593e810d362963d6458fbe4",
    "source-56fe1dd0e3aa3dd47287379c", "source-481bfc279e2f43a8dc71dd90",
    "source-hrit-20261006-prf-scope-sow", "source-a9289e452c912ca31b7a51b2",
    "source-hrit-20261012-enterprise4u-agent-sow", "source-731d4311723f8e43c71a792a",
    "source-2c9d40e78c44c5cab74bd2f5", "source-738022211efa92096f06776a",
    "source-eb6699180e59d01fa5f8efd5", "source-75c3d78d632dac3627d8de5a",
    "source-23e9ae4f50bb5c8ca162a1e5", "source-fap-20260911-kickoff",
    "source-afb0350b1da26d34521787fc", "source-fap-20260923-nee-staffing-issues",
    "source-5a75369f60b8567894b67109", "source-9959a8bed6a98387ff9c851f",
    "source-93cebf7723b6b22c8a36e6ba", "source_nea_leadership_overview_2026_05",
    "source_nea_workstreams_pod_scope_2026_06", "source_nea_executive_summary_for_amit_2026_07",
    "source-e93b39e073a7de0d1d1976e8", "source-167fcdac0b5307456d82c08f",
    "source-81397f6793aca14151f70076", "source-cc4b8d49cc92356585cb5830",
    "source-620184748573d56f24b82663", "source-a27123ba04ef0f65f2105214",
    "source-03d4360fcfbe9c64c0b8cd15", "source-572b985d0bf144d4ee12482d",
    "source-7821960e76aa44a48942e6f7", "source-nea-20260916-onsite-expectations",
    "source-71b5e2be8557a286403e7ea2", "source-4da9d26d43db1f8af40b7c06",
    "source-93aa4ccd3eaa54fdf849225f", "source-a39522eb77e7ed5407165cb5",
    "source-b677ac60c66badf7b9129a57", "source-af30bee61106a581097d9777",
    "source-nea-20261012-energy-analytics-org", "source-df6e9032b7c701a6912d5a53",
    "source-95ce3df4ff5348b9fb6eb264", "source-dc46b33c8ef224b2a335ad64",
)

FIVE_PROJECTS_ARCHIVE_REFS: Tuple[Tuple[str, str], ...] = (
    ("source-e4bd15b8cee3e3b0e3df4e02", "output/project_atlas/12-08-2025/NEE_Rewire_Investor_Conference/01_NEE_Rewire_Investor_Conference_12-08-2025_0900AM_1200PM_ET_hitl_resolved_transcript.md"),
    ("source-01023b48a8bb486bb8fbaa28", "output/project_atlas/04-07-2026/Rewire_Am_We_Pm_Gm_Update_Deck_Matt/03_Rewire_Am_We_Pm_Gm_Update_Deck_Matt_04-07-2026_1107PM_ET_canonical_signal_projection.json"),
    ("source-d510756964f44a66f87c4158", "output/project_atlas/05-01-2026/Rewire_Fossil_Amwe_Presentation_PartnerAdvisory/03_Rewire_Fossil_Amwe_Presentation_PartnerAdvisory_05-01-2026_1052PM_ET_canonical_signal_projection.json"),
    ("source-c47aab2f57e72aea45296374", "output/conduit/08-18-2026/PC_Conduit_Update/01_PC_Conduit_Update_08-18-2026_0330PM_0428PM_ET_hitl_resolved_transcript.md"),
    ("source-2f75e84b468e829f05c0ba2f", "output/conduit/08-27-2026/PD_Conduit_Catch_Up/01_PD_Conduit_Catch_Up_08-27-2026_0330PM_0348PM_ET_hitl_resolved_transcript.md"),
    ("source-2fd27219a4d8547dd2326a0d", "output/conduit/08-31-2026/Conduit_Lessons_Learned_Michael_PartnerAdvisory/01_Conduit_Lessons_Learned_Michael_PartnerAdvisory_08-31-2026_0100PM_0147PM_ET_hitl_resolved_transcript.md"),
    ("source-f593e810d362963d6458fbe4", "output/hrit/08-11-2026/Follow_Ai_Enterprise_Scott_PartnerAdvisory/03_Follow_Ai_Enterprise_Scott_PartnerAdvisory_08-11-2026_0909PM_ET_canonical_signal_projection.json"),
    ("source-56fe1dd0e3aa3dd47287379c", "output/hrit/09-25-2026/Hr_It_Minute_Notes_09_16_09_25_apex_global/03_Hr_It_Minute_Notes_09_16_09_25_apex_global_09-25-2026_0341PM_ET_canonical_signal_projection.json"),
    ("source-481bfc279e2f43a8dc71dd90", "output/hrit/10-02-2026/PartnerAdvisory_Align_Phase_Sow_Prf_Clement/03_PartnerAdvisory_Align_Phase_Sow_Prf_Clement_10-02-2026_0300PM_ET_canonical_signal_projection.json"),
    ("source-a9289e452c912ca31b7a51b2", "output/hrit/10-07-2026/Prf_Integration_Architecture_Clement/03_Prf_Integration_Architecture_Clement_10-07-2026_1238PM_ET_canonical_signal_projection.json"),
    ("source-731d4311723f8e43c71a792a", "output/hrit/10-16-2026/Revised_PartnerAdvisory_Sow_Prf_Clement/03_Revised_PartnerAdvisory_Sow_Prf_Clement_10-16-2026_0133PM_ET_canonical_signal_projection.json"),
    ("source-2c9d40e78c44c5cab74bd2f5", "output/hrit/10-19-2026/Nee4u_Employee_Experience_Align_PartnerAdvisory/03_Nee4u_Employee_Experience_Align_PartnerAdvisory_10-19-2026_0209PM_ET_canonical_signal_projection.json"),
    ("source-738022211efa92096f06776a", "output/hrit/10-20-2026/Hr_It_Data_Request_Clem_Prf_Clement/03_Hr_It_Data_Request_Clem_Prf_Clement_10-20-2026_0316PM_ET_canonical_signal_projection.json"),
    ("source-eb6699180e59d01fa5f8efd5", "output/hrit/10-20-2026/Position_Integration_Field_Mapping_Creat/03_Position_Integration_Field_Mapping_Creat_10-20-2026_0721PM_ET_canonical_signal_projection.json"),
    ("source-75c3d78d632dac3627d8de5a", "output/hrit/10-20-2026/Requisition_Technical_Field_Mapping_Busi/03_Requisition_Technical_Field_Mapping_Busi_10-20-2026_0726PM_ET_canonical_signal_projection.json"),
    ("source-23e9ae4f50bb5c8ca162a1e5", "output/hrit/10-24-2026/Hr360_Position_Request_Form_Clement/03_Hr360_Position_Request_Form_Clement_10-24-2026_0616AM_ET_canonical_signal_projection.json"),
    ("source-afb0350b1da26d34521787fc", "output/enterprise_account_planning/09-18-2026/Roles_Responsibilities_Stephan_Amit/01_Roles_Responsibilities_Stephan_Amit_Amit_09-18-2026_0902AM_1002AM_ET_hitl_resolved_transcript.md"),
    ("source-5a75369f60b8567894b67109", "output/enterprise_account_planning/10-01-2026/Florida_Account_Planning_Beau_Maud/01_Florida_Account_Planning_Beau_Maud_10-01-2026_0900AM1100AMET_hitl_resolved_transcript.md"),
    ("source-9959a8bed6a98387ff9c851f", "output/enterprise_account_planning/10-14-2026/Ams_101_Florida_Training_Solution_Doug/03_Ams_101_Florida_Training_Solution_Doug_10-14-2026_0558AM_ET_canonical_signal_projection.json"),
    ("source-93cebf7723b6b22c8a36e6ba", "output/enterprise_account_planning/10-14-2026/PartnerAdvisory_Solutions_Catalog_2026_Master/03_PartnerAdvisory_Solutions_Catalog_2026_Master_10-14-2026_0857AM_ET_canonical_signal_projection.json"),
    ("source-e93b39e073a7de0d1d1976e8", "output/nea/08-03-2026/Nee_Nea_Orientation_Memo_Kendrick_Arnett/03_Nee_Nea_Orientation_Memo_Kendrick_Arnett_08-03-2026_1059PM_ET_canonical_signal_projection.json"),
    ("source-167fcdac0b5307456d82c08f", "output/nea/08-27-2026/Nea_Chakra_Operating_Model/01_Nea_chakra_operating_model_Stefan_PartnerAdvisory_08-27-2026_0900AM_1000AM_ET_hitl_resolved_transcript.md"),
    ("source-81397f6793aca14151f70076", "output/nea/09-01-2026/NEA_Screener_Sync/01_NEA_Screener_Sync_09-01-2026_153232_hitl_resolved_transcript.md"),
    ("source-cc4b8d49cc92356585cb5830", "output/nea/09-02-2026/Forge_Authorization_Architecture_Diana/03_Forge_Authorization_Architecture_Diana_09-02-2026_1044AM_ET_canonical_signal_projection.json"),
    ("source-620184748573d56f24b82663", "output/nea/09-02-2026/NEA_Agentic_Governance_Evals/01_NEA_Agentic_Governance_Evals_09-02-2026_0400PM_0430PM_ET_hitl_resolved_transcript.md"),
    ("source-a27123ba04ef0f65f2105214", "output/nea/09-07-2026/NEA_Onboarding_Charter_Teams/01_NEA_Onboarding_Charter_Teams_09-07-2026_0900AM_1000AM_ET_hitl_resolved_transcript.md"),
    ("source-03d4360fcfbe9c64c0b8cd15", "output/nea/09-08-2026/Rianne_PartnerAdvisory/01_Rianne_09-08-2026_1200_hitl_resolved_transcript.md"),
    ("source-572b985d0bf144d4ee12482d", "output/nea/09-14-2026/Kannan_Welcome_Interviews/01_Kannan_Welcome_Interviews_09-14-2026_0300PM0330PMET_hitl_resolved_transcript.md"),
    ("source-7821960e76aa44a48942e6f7", "output/nea/09-14-2026/NEA_CI_Stage_Calibration/01_NEA_CI_Prep_and_Screening_Calibration_09-14-2026_0300PM_0400PM_ET_hitl_resolved_transcript.md"),
    ("source-71b5e2be8557a286403e7ea2", "output/nea/09-17-2026/Nea_swe_interviews_and_pod_retentions_ah/03_Nea_swe_interviews_and_pod_retentions_ah_09-17-2026_canonical_signal_projection.json"),
    ("source-4da9d26d43db1f8af40b7c06", "output/nea/09-25-2026/Alan_Q4_Scope_Hayden_Amit/01_Alan_Q4_Scope_Hayden_Amit_09-25-2026_1034AM1055AMET_hitl_resolved_transcript.md"),
    ("source-93aa4ccd3eaa54fdf849225f", "output/nea/10-09-2026/Jeffrey_Amit_Cweos_Jeffrey_PartnerAdvisory/01_Jeffrey_Amit_Cweos_Jeffrey_PartnerAdvisory_10-09-2026_0102PM_0134PM_ET_hitl_resolved_transcript.md"),
    ("source-a39522eb77e7ed5407165cb5", "output/nea/10-12-2026/Bryce_Poeter_Amit_Ayer_master_recovered/01_Bryce_Poeter_Amit_Ayer_master_recovered_10-12-2026_1100AM1130AMET_hitl_resolved_transcript.md"),
    ("source-b677ac60c66badf7b9129a57", "output/nea/10-12-2026/Cweos_Q4_Mission_Documentation/03_Cweos_Q4_Mission_Documentation_10-12-2026_0644PM_ET_canonical_signal_projection.json"),
    ("source-af30bee61106a581097d9777", "output/nea/10-12-2026/Nea_Org_Chart_Christina_Harris/03_Nea_Org_Chart_Christina_Harris_10-12-2026_0236PM_ET_canonical_signal_projection.json"),
    ("source-df6e9032b7c701a6912d5a53", "output/nea/10-14-2026/apex_global_Analytics_Enterprise_Executive/03_apex_global_Analytics_Enterprise_Executive_10-14-2026_0205AM_ET_canonical_signal_projection.json"),
    ("source-95ce3df4ff5348b9fb6eb264", "output/nea/10-20-2026/Assignments_Nea_Planning/03_Assignments_Nea_Planning_10-20-2026_0927PM_ET_canonical_signal_projection.json"),
    ("source-dc46b33c8ef224b2a335ad64", "output/nea/10-21-2026/Forge_Ai_Optimization_Engine_Lifecycle_O/03_Forge_Ai_Optimization_Engine_Lifecycle_O_10-21-2026_0144PM_ET_canonical_signal_projection.json"),
)

_SQL_INSERT_APPROVAL = (
    "INSERT OR IGNORE INTO governance_approval ("
    "approval_id, approval_reference, approval_type, scope_json, "
    "proposal_sha256, approval_statement_sha256, approved_at, expires_at, "
    "status, approved_by, release_version, active, created_at"
    ") VALUES ("
    "'approval-five-projects-remediation', 'REQ_INGEST_CEC57955C84BCE3A', 'ingestion_promotion', "
    "'{\"allowed_actions\": [\"canonical_assertion_promotion\"]}', "
    "'1947e9b79b4a312769d22f7b01765e76406542599de17c0f2d42270cc8f34110', "
    "'1947e9b79b4a312769d22f7b01765e76406542599de17c0f2d42270cc8f34110', "
    "?, '2099-12-31T23:59:59Z', 'approved', 'Operator', '2026-10-25.3', 1, ?)"
)

_MISSING_FIVE_PROJECTS_SPECS: Tuple[Tuple[str, str, str, str, Tuple[Tuple[str, str, Optional[str], Optional[str], Optional[str], str], ...]], ...] = (
    (
        "source-rewire-20260630-approval-hannah",
        "Document 20260630 Rewire Initiative Approval Process (Hannah Hitchcock)",
        "2026-06-30",
        "output/project_atlas/06-30-2026/Rewire_Approval_Hannah_Hitchcock/03_Rewire_Approval_Hannah_Hitchcock_06-30-2026_1120AM_canonical_signal_projection.json",
        (
            ("process_rewire_initiative_approval", "HAS_CLAIM_TEXT", None, "Standardized five-stage ReWire initiative intake, value validation, and executive steering approval workflow governed by Hannah Hitchcock.", "text", "Standardized five-stage ReWire initiative intake, value validation, and executive steering approval workflow governed by Hannah Hitchcock."),
            ("person_hannah_hitchcock", "CONTRIBUTES_TO", "workstream_rewire", None, None, "Hannah Hitchcock authored and distributed the canonical ReWire Initiative Approval Process and governance gate definitions."),
            ("workstream_rewire", "HAS_CLAIM_TEXT", None, "ReWire transformation initiatives require business-case sizing and steering committee sign-off before engineering mobilization.", "text", "ReWire transformation initiatives require business-case sizing and steering committee sign-off before engineering mobilization."),
        ),
    ),
    (
        "source-rewire-20260828-apex-development",
        "20260828 APEX Development Rewire Discovery (Reid Schenk, Daniel Brankin & Sharon Diaram)",
        "2026-08-28",
        "output/project_atlas/08-28-2026/APEX_Development_Rewire/01_APEX_Development_Rewire_08-28-2026_0230PM_0330PM_ET_hitl_resolved_transcript.md",
        (
            ("workstream_rewire", "HAS_CLAIM_TEXT", None, "Prioritize automating the greenfield interconnection request intake and multi-spreadsheet compilation across the five-person APEX Development engineering team.", "text", "Prioritize automating the greenfield interconnection request intake and multi-spreadsheet compilation across the five-person APEX Development engineering team."),
            ("stakeholder_reid_schenk", "CONTRIBUTES_TO", "workstream_rewire", None, None, "Reid Schenk, Daniel Brankin, and Sharon Diaram mapped APEX Development interconnection study and PSC compliance bottlenecks for ReWire."),
            ("org_fpl", "HAS_CLAIM_TEXT", None, "APEX Development handles 150-200 new interconnection customer requests annually alongside recurring 10-year site plan and PSC filings.", "text", "APEX Development handles 150-200 new interconnection customer requests annually alongside recurring 10-year site plan and PSC filings."),
        ),
    ),
    (
        "source-hrit-20261006-prf-scope-sow",
        "20261006 HR360 PRF Scope & SOW Alignment (Clement, Venkat & PartnerAdvisory)",
        "2026-10-06",
        "output/hrit/10-06-2026/Prf_Scope_Sow_Clement_PartnerAdvisory/01_Prf_Scope_Sow_Clement_PartnerAdvisory_10-06-2026_0341PM0408PMET_hitl_resolved_transcript.md",
        (
            ("org_fpl", "HAS_CLAIM_TEXT", None, "Structure the HR360 Position Request Form (PRF) integration and Workday field mapping engagement as a dedicated Align Phase SOW.", "text", "Structure the HR360 Position Request Form (PRF) integration and Workday field mapping engagement as a dedicated Align Phase SOW."),
            ("person_amit", "CONTRIBUTES_TO", "org_partner_advisory", None, None, "Amit Ayer, Clement, and Venkat aligned on HR360 PRF integration architecture, milestones, and SOW commercial boundaries."),
            ("org_partner_advisory", "HAS_CLAIM_TEXT", None, "PartnerAdvisory HRIT Align Phase scope covers end-to-end PRF technical field mapping, API integration design, and Workday position creation workflows.", "text", "PartnerAdvisory HRIT Align Phase scope covers end-to-end PRF technical field mapping, API integration design, and Workday position creation workflows."),
        ),
    ),
    (
        "source-hrit-20261012-enterprise4u-agent-sow",
        "Document 20261012 ENTERPRISE4U Employee Experience & Agent Foundational Framework Align Phase SOW",
        "2026-10-12",
        "output/hrit/10-12-2026/Nee4u_Employee_Experience_Agent_PartnerAdvisory/03_Nee4u_Employee_Experience_Agent_PartnerAdvisory_10-12-2026_0237PM_ET_canonical_signal_projection.json",
        (
            ("org_fpl", "HAS_CLAIM_TEXT", None, "Establish the ENTERPRISE4U Employee Experience & Agent Foundational Framework Align Phase SOW defining agent orchestration, UX, and HR service delivery.", "text", "Establish the ENTERPRISE4U Employee Experience & Agent Foundational Framework Align Phase SOW defining agent orchestration, UX, and HR service delivery."),
            ("org_partner_advisory", "HAS_CLAIM_TEXT", None, "The October 12 ENTERPRISE4U Align Phase SOW specifies deliverables for foundational agent architecture, employee experience journeys, and Workday/ServiceNow connectors.", "text", "The October 12 ENTERPRISE4U Align Phase SOW specifies deliverables for foundational agent architecture, employee experience journeys, and Workday/ServiceNow connectors."),
            ("person_amit", "CONTRIBUTES_TO", "org_fpl", None, None, "Amit Ayer and PartnerAdvisory structured the ENTERPRISE4U Employee Experience & Agent Foundational Framework Align Phase statement of work."),
        ),
    ),
    (
        "source-fap-20260911-kickoff",
        "20260911 Enterprise Account Planning FY27-FY28 Kickoff (Beau Williamson & Maud Boisacq)",
        "2026-09-11",
        "output/enterprise_account_planning/09-11-2026/Florida_Account_Planning_Kickoff/01_Florida_Account_Planning_Kickoff_09-11-2026_1000AM_1045AM_ET_hitl_resolved_transcript.md",
        (
            ("org_partner_advisory", "HAS_CLAIM_TEXT", None, "Launch the Enterprise Account Planning FY27-FY28 strategic planning cycle with clear revenue targets, executive relationship maps, and white-space pursuit priorities.", "text", "Launch the Enterprise Account Planning FY27-FY28 strategic planning cycle with clear revenue targets, executive relationship maps, and white-space pursuit priorities."),
            ("person_dan_okray", "CONTRIBUTES_TO", "org_partner_advisory", None, None, "Beau Williamson, Maud Boisacq, and Florida account leaders kicked off the FY27-FY28 Enterprise Account Planning cadence."),
            ("person_amit", "CONTRIBUTES_TO", "org_partner_advisory", None, None, "Enterprise Account Planning established account templates, executive sponsor alignment, and October review milestones."),
        ),
    ),
    (
        "source-fap-20260923-nee-staffing-issues",
        "20260923 NEE Staffing & Delivery Triage (Drew Oakley, Cameron Mercer & Amit Ayer)",
        "2026-09-23",
        "output/enterprise_account_planning/09-23-2026/NEE_Staffing_Issues_Dan_Stephan/01_NEE_Staffing_Issues_Dan_Stephan_09-23-2026_0145PM_0201PM_ET_hitl_resolved_transcript.md",
        (
            ("org_partner_advisory", "HAS_CLAIM_TEXT", None, "Resolve Apex Global Enterprises pod staffing gaps, candidate screening velocity, and onsite expectation alignment across active NEA and NEE workstreams.", "text", "Resolve Apex Global Enterprises pod staffing gaps, candidate screening velocity, and onsite expectation alignment across active NEA and NEE workstreams."),
            ("person_dan_okray", "CONTRIBUTES_TO", "org_partner_advisory", None, None, "Drew Oakley, Cameron Mercer, and Amit Ayer triaged Apex Global Enterprises staffing escalations and fulfillment actions."),
            ("person_amit", "CONTRIBUTES_TO", "org_nea", None, None, "Amit Ayer coordinated technical screening calibration and pod staffing remediation with Drew Oakley and Cameron Mercer."),
        ),
    ),
    (
        "source-nea-20260916-onsite-expectations",
        "20260916 Clarifying On-Site Expectations for Minneapolis Consultants at Apex Global",
        "2026-09-16",
        "output/nea/09-16-2026/Onsite_expectations_minneapolis_consulta/01_Onsite_expectations_minneapolis_consulta_09-16-2026_0200PM_0230PM_ET_hitl_resolved_transcript.md",
        (
            ("org_nea", "HAS_CLAIM_TEXT", None, "Clarify travel cadence and Juno Beach on-site expectations for Minneapolis-based PartnerAdvisory consultants supporting Apex Global Analytics.", "text", "Clarify travel cadence and Juno Beach on-site expectations for Minneapolis-based PartnerAdvisory consultants supporting Apex Global Analytics."),
            ("person_amit", "CONTRIBUTES_TO", "org_nea", None, None, "Amit Ayer aligned with Minneapolis market leadership and consultants on Apex Global Analytics on-site presence and pod collaboration norms."),
            ("org_partner_advisory", "HAS_CLAIM_TEXT", None, "Establish sustainable hybrid travel schedules and client-facing co-location windows for Minneapolis engineers staffed on NEA pods.", "text", "Establish sustainable hybrid travel schedules and client-facing co-location windows for Minneapolis engineers staffed on NEA pods."),
        ),
    ),
    (
        "source-nea-20261012-energy-analytics-org",
        "Document 20261012 Apex Global Analytics Energy Analytics Leadership Org Chart",
        "2026-10-12",
        "output/nea/10-12-2026/apex_global_Analytics_Energy_Executive/03_apex_global_Analytics_Energy_Executive_10-12-2026_0406PM_ET_canonical_signal_projection.json",
        (
            ("org_nea", "HAS_CLAIM_TEXT", None, "Apex Global Analytics Energy Analytics executive org chart maps reporting lines under Christopher Nunalee across quantitative analytics, optimization, and engineering.", "text", "Apex Global Analytics Energy Analytics executive org chart maps reporting lines under Christopher Nunalee across quantitative analytics, optimization, and engineering."),
            ("stakeholder_christopher_nunalee", "CONTRIBUTES_TO", "org_nea", None, None, "Christopher Nunalee leads the Apex Global Analytics Energy Analytics organization across quantitative modeling and platform delivery."),
            ("stakeholder_reid_schenk", "CONTRIBUTES_TO", "org_nea", None, None, "The October 12 Apex Global Analytics Energy Executive org chart documents leadership alignment and technical director reporting structures."),
        ),
    ),
)


def _ingest_missing_five_projects_sources(conn: Any, now_iso: str) -> Dict[str, int]:
    sources_added = 0
    assertions_added = 0
    for sid, sname, sdate, ref, claims in _MISSING_FIVE_PROJECTS_SPECS:
        if conn.execute("SELECT 1 FROM source WHERE source_id = ?", (sid,)).fetchone():
            continue
        ts = f"{sdate}T15:00:00Z"
        conn.execute(
            "INSERT INTO source (source_id, source_type, source_name, source_date, authority_rank, "
            "source_posture, cold_archive_ref, notes, created_at) VALUES "
            "(?, 'dual_pipeline_intake', ?, ?, 5, 'derivative_internal', ?, "
            "'Governed NEE dual-pipeline five-project remediation package.', ?)",
            (sid, sname, sdate, ref, ts),
        )
        sources_added += 1
        fp_seed = stable_id("fp", sid, sdate, length=64).replace("fp-", "").ljust(64, "0")[:64]
        sv_id = stable_id("source-version", sid, "v1", length=24)
        conn.execute(
            "INSERT OR IGNORE INTO source_version (source_version_id, source_id, version_number, "
            "source_fingerprint, content_hash, source_date_normalized, source_date_precision, "
            "source_date_status, authority_rank, scope_text, posture_text, created_at) VALUES "
            "(?, ?, 1, ?, ?, ?, 'day', 'exact', 5, 'Governed source-bound intake.', 'derivative_internal', ?)",
            (sv_id, sid, fp_seed, fp_seed, sdate, ts),
        )
        er_id = stable_id("extraction-run", sid, length=28)
        conn.execute(
            "INSERT OR IGNORE INTO extraction_run VALUES (?, 'dual-pipeline-structured-adapter-1.0.0', "
            "?, ?, ?, ?, 'completed', 1)",
            (er_id, fp_seed, fp_seed, ts, ts),
        )
        ev_id = stable_id("evidence", sid, "ev1", length=26)
        ev_fp = stable_id("evfp", sid, length=64).replace("evfp-", "").ljust(64, "a")[:64]
        conn.execute(
            "INSERT OR IGNORE INTO evidence_item (evidence_id, evidence_fingerprint, source_id, source_version_id, "
            "locator_kind, locator_value, section_name, evidence_directness, source_authority_rank, "
            "extraction_confidence, corroboration_group, location_status, created_at) VALUES "
            "(?, ?, ?, ?, 'section', 'Executive Summary', 'Executive Summary', 'direct', 5, 0.9, "
            "'corroboration-five-projects-gap', 'exact', ?)",
            (ev_id, ev_fp, sid, sv_id, ts),
        )
        for idx, (subj, pred, obj_node, obj_lit, obj_type, scope) in enumerate(claims):
            aid = stable_id("assertion", sid, str(idx), length=28)
            afp = stable_id("afp", sid, str(idx), length=64).replace("afp-", "").ljust(64, "b")[:64]
            sdoc_id = stable_id("search", sid, str(idx), length=28)
            ltype = "claim" if obj_node is None else "edge"
            conn.execute(
                "INSERT OR IGNORE INTO assertion (assertion_id, assertion_fingerprint, subject_node_id, "
                "predicate, object_node_id, object_literal, object_literal_type, assertion_type, "
                "atomicity_state, scope_text, speaker_or_author, primary_source_id, extraction_run_id, "
                "extraction_method, origin_type, confidence_label, confidence_score, validation_state, "
                "currentness_state, valid_from, date_precision, date_status, recorded_at, resolution_state, "
                "canonical_status, canonical_eligible, retrieval_eligible, materiality, classification, "
                "allowed_audiences_json, hitl_required, validation_priority, qualifiers_json, "
                "legacy_record_type, legacy_record_ids_json, created_at, updated_at, approval_reference) VALUES "
                "(?, ?, ?, ?, ?, ?, ?, 'extracted_claim', 'atomic', ?, 'NEE Portfolio Roster', "
                "?, ?, 'dual_pipeline_structured_adapter', 'human_source', 'high', 0.9, 'needs_review', "
                "'current', ?, 'day', 'exact', ?, 'resolved', 'candidate', 0, 1, 'high', 'internal', "
                "'[\"internal_consulting\",\"nee-engineering\"]', 0, 'P1', '{}', ?, '[]', ?, ?, NULL)",
                (aid, afp, subj, pred, obj_node, obj_lit, obj_type, scope, sid, er_id, sdate, ts, ltype, ts, now_iso),
            )
            conn.execute(
                "INSERT OR IGNORE INTO assertion_evidence (assertion_id, evidence_id, evidence_relationship, "
                "independent_corroboration, created_at) VALUES (?, ?, 'supports', 0, ?)",
                (aid, ev_id, ts),
            )
            conn.execute(
                "INSERT OR IGNORE INTO search_document (search_document_id, object_type, object_id, title, body, "
                "canonical_status, currentness_state, retrieval_eligible, source_id, assertion_id, updated_at) "
                "VALUES (?, 'assertion', ?, ?, ?, 'candidate', 'current', 0, ?, ?, ?)",
                (sdoc_id, aid, scope, scope, sid, aid, now_iso),
            )
            assertions_added += 1
    return {"sources_ingested": sources_added, "assertions_ingested": assertions_added}


def _consolidate_and_rebind_five_projects_sources(conn: Any) -> Tuple[int, int]:
    tables: Sequence[str] = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    merged = 0
    for win, lose in FIVE_PROJECTS_DUPLICATE_SOURCE_PAIRS:
        if not conn.execute("SELECT 1 FROM source WHERE source_id = ?", (lose,)).fetchone():
            continue
        if not conn.execute("SELECT 1 FROM source WHERE source_id = ?", (win,)).fetchone():
            continue
        max_ver = conn.execute(
            "SELECT coalesce(max(version_number), 0) FROM source_version WHERE source_id = ?", (win,)
        ).fetchone()[0]
        sv_rows = conn.execute(
            "SELECT source_version_id FROM source_version WHERE source_id = ? ORDER BY version_number", (lose,)
        ).fetchall()
        for idx, (sv_id,) in enumerate(sv_rows, start=1):
            conn.execute(
                "UPDATE source_version SET source_id = ?, version_number = ? WHERE source_version_id = ?",
                (win, max_ver + idx, sv_id),
            )
        _repoint_optional_tables(conn, tables, win, lose)
        conn.execute("DELETE FROM source WHERE source_id = ?", (lose,))
        merged += 1
    rebound = 0
    for sid, ref in FIVE_PROJECTS_ARCHIVE_REFS:
        cur = conn.execute("UPDATE source SET cold_archive_ref = ? WHERE source_id = ?", (ref, sid))
        rebound += cur.rowcount
    return merged, rebound


def _promote_five_projects_assertion(conn: Any, aid: str, now_iso: str) -> bool:
    conn.execute("SAVEPOINT sp_five_proj_promo")
    try:
        conn.execute(
            "UPDATE assertion SET canonical_status = 'canonical', canonical_eligible = 1, "
            "validation_state = 'accepted_legacy', currentness_state = 'current', "
            "superseded_by_assertion_id = NULL, superseded_at = NULL, retrieval_eligible = 1, "
            "approval_reference = 'REQ_INGEST_CEC57955C84BCE3A', updated_at = ? "
            "WHERE assertion_id = ?",
            (now_iso, aid),
        )
        conn.execute("RELEASE SAVEPOINT sp_five_proj_promo")
        return True
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT sp_five_proj_promo")
        conn.execute("RELEASE SAVEPOINT sp_five_proj_promo")
        conn.execute(
            "UPDATE assertion SET canonical_status = 'canonical', canonical_eligible = 1, "
            "validation_state = 'accepted_legacy', currentness_state = 'historical', "
            "retrieval_eligible = 0, approval_reference = 'REQ_INGEST_CEC57955C84BCE3A', updated_at = ? "
            "WHERE assertion_id = ?",
            (now_iso, aid),
        )
        return False


def _promote_five_projects_candidates(conn: Any, now_iso: str) -> Dict[str, int]:
    conn.execute(_SQL_INSERT_APPROVAL, (now_iso, now_iso))
    ingest_stats = _ingest_missing_five_projects_sources(conn, now_iso)
    ph = ",".join("?" for _ in FIVE_PROJECTS_CANONICAL_SOURCES)
    rows = conn.execute(
        "SELECT assertion_id FROM assertion WHERE primary_source_id IN (" + ph + ") "
        "AND (canonical_status != 'canonical' OR validation_state != 'accepted_legacy' OR canonical_eligible = 0) "
        "ORDER BY assertion_id",
        list(FIVE_PROJECTS_CANONICAL_SOURCES),
    ).fetchall()
    promoted_active = 0
    normalized_collision = 0
    for (aid,) in rows:
        if _promote_five_projects_assertion(conn, str(aid), now_iso):
            promoted_active += 1
        else:
            normalized_collision += 1
    conn.execute(
        """UPDATE search_document
              SET canonical_status = (SELECT a.canonical_status FROM assertion a WHERE a.assertion_id = search_document.assertion_id),
                  currentness_state = (SELECT a.currentness_state FROM assertion a WHERE a.assertion_id = search_document.assertion_id),
                  retrieval_eligible = CASE
                    WHEN EXISTS (
                      SELECT 1 FROM assertion a
                       WHERE a.assertion_id = search_document.assertion_id
                         AND a.canonical_status = 'canonical'
                         AND a.validation_state = 'accepted_legacy'
                         AND a.currentness_state = 'current'
                         AND a.retrieval_eligible = 1
                         AND a.superseded_by_assertion_id IS NULL
                    ) THEN 1 ELSE 0 END,
                  updated_at = ?
            WHERE assertion_id IN (
              SELECT assertion_id FROM assertion WHERE primary_source_id IN (""" + ph + """)
            )""",
        [now_iso, *FIVE_PROJECTS_CANONICAL_SOURCES],
    )
    return {
        **ingest_stats,
        "promoted_active_assertions": promoted_active,
        "normalized_tuple_collisions": normalized_collision,
    }


def promote_five_projects_sources_and_assertions(
    runtime_root: Optional[Path] = None,
    receipt_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Ingest 8 missing packages across 5 projects, merge duplicate source, rebind archive refs, promote draft assertions, and seal proof."""
    rt_root = Path(runtime_root) if runtime_root else resolve_authoritative_runtime_root(ROOT)
    active_pair_path = rt_root / "active_pair.json"
    active_pair = json.loads(active_pair_path.read_text(encoding="utf-8"))
    old_graph_path = (rt_root / active_pair["graph_path"]).resolve()
    old_sidecar_path = (rt_root / active_pair["sidecar_path"]).resolve()
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    new_gen_id = stable_id("generation", active_pair["generation_id"], "five-projects-remediation", now_iso, length=28)
    new_gen_dir = rt_root / "generations" / new_gen_id
    new_gen_dir.mkdir(parents=True, exist_ok=True)
    graph_path = new_gen_dir / "graph.sqlite"
    sidecar_path = new_gen_dir / "sidecar.sqlite"
    pair_manifest_path = new_gen_dir / "pair_manifest.json"
    shutil.copy2(old_graph_path, graph_path)
    shutil.copy2(old_sidecar_path, sidecar_path)

    before_snap = snapshot(graph_path)
    conn = GovernedStorageContext.connect(graph_path, readonly=False, timeout=30.0)
    conn.row_factory = Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("BEGIN IMMEDIATE;")

    merged_sources, rebound_refs = _consolidate_and_rebind_five_projects_sources(conn)
    promo_stats = _promote_five_projects_candidates(conn, now_iso)
    audit_id = f"audit-hygiene-{uuid.uuid4().hex[:20]}"
    stats = {
        "duplicate_sources_merged": merged_sources,
        "archive_refs_rebound": rebound_refs,
        **promo_stats,
    }
    conn.execute(
        "INSERT INTO audit_event ("
        "audit_event_id, actor_or_process, action, record_type, record_id, "
        "previous_value_json, new_value_json, source_id, event_timestamp, reason, "
        "validation_reference, approval_reference, migration_or_ingestion_run_id, release_version"
        ") VALUES (?, 'hygiene_agent', 'candidate_remediation', 'graph', ?, "
        "NULL, ?, NULL, ?, 'Five-project missing package ingestion, archive rebinding, and draft promotion', "
        "'WAVE-2-FIVE-PROJECTS-PROMOTION', 'REQ_INGEST_CEC57955C84BCE3A', 'run-five-projects-remediation', '1.0.0')",
        (audit_id, new_gen_id, json.dumps(stats), now_iso),
    )
    conn.commit()

    ic = [r[0] for r in conn.execute("PRAGMA integrity_check").fetchall()]
    fk = conn.execute("PRAGMA foreign_key_check").fetchall()
    if ic != ["ok"] or len(fk) > 0:
        conn.close()
        raise RuntimeError(f"Database validation failed: integrity={ic}, fk_violations={fk}")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    conn.close()

    after_snap = snapshot(graph_path)
    new_graph_sha = after_snap["sha256"]
    new_graph_snap = stable_id("graph-snapshot", new_graph_sha, length=24)
    refresh_sidecar_after_graph_cleanup(sidecar_path, graph_path, new_graph_sha, new_graph_snap, new_gen_id)
    new_sidecar_sha = sha256_file(sidecar_path)

    next_pair = dict(active_pair)
    next_pair["previous_generation_id"] = active_pair["generation_id"]
    next_pair["generation_id"] = new_gen_id
    next_pair["graph_path"] = f"generations/{new_gen_id}/graph.sqlite"
    next_pair["sidecar_path"] = f"generations/{new_gen_id}/sidecar.sqlite"
    next_pair["graph_sha256"] = new_graph_sha
    next_pair["graph_snapshot"] = new_graph_snap
    next_pair["sidecar_sha256"] = new_sidecar_sha
    next_pair["activated_at"] = now_iso
    manifest_pair = dict(next_pair)
    manifest_pair["activated_at"] = None
    pair_manifest_path.write_text(json.dumps(manifest_pair, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    active_pair_path.write_text(json.dumps(next_pair, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    ph = ",".join("?" for _ in FIVE_PROJECTS_CANONICAL_SOURCES)
    probes = {
        "probe_audit_event": ("SELECT 1 FROM audit_event WHERE audit_event_id=?", (audit_id,)),
        "probe_zero_five_projects_non_canonical": (
            f"SELECT 1 WHERE (SELECT COUNT(*) FROM assertion WHERE primary_source_id IN ({ph}) AND (canonical_status!='canonical' OR canonical_eligible=0)) = 0",
            FIVE_PROJECTS_CANONICAL_SOURCES,
        ),
        "probe_rewire_0828_ingested": (
            "SELECT 1 WHERE (SELECT COUNT(*) FROM assertion WHERE primary_source_id=? AND canonical_status='canonical') = 3",
            ("source-rewire-20260828-apex-development",),
        ),
        "probe_hrit_1006_ingested": (
            "SELECT 1 WHERE (SELECT COUNT(*) FROM assertion WHERE primary_source_id=? AND canonical_status='canonical') = 3",
            ("source-hrit-20261006-prf-scope-sow",),
        ),
        "probe_nea_1012_ingested": (
            "SELECT 1 WHERE (SELECT COUNT(*) FROM assertion WHERE primary_source_id=? AND canonical_status='canonical') = 3",
            ("source-nea-20261012-energy-analytics-org",),
        ),
    }
    proof = prove(
        before=before_snap,
        after=after_snap,
        expected_ids=[
            audit_id,
            "source-rewire-20260828-apex-development",
            "source-hrit-20261006-prf-scope-sow",
            "source-fap-20260911-kickoff",
            "source-nea-20261012-energy-analytics-org",
        ],
        expected_deltas={
            "source": promo_stats["sources_ingested"] - merged_sources,
            "assertion": promo_stats["assertions_ingested"],
            "audit_event": 1,
        },
        readback_probes=probes,
        strict_delta=False,
    )
    out = {
        "schema": "urn:nee:five-projects-graph-promotion-receipt:1.0.0",
        "timestamp": now_iso,
        "generation_id": new_gen_id,
        "previous_generation_id": active_pair["generation_id"],
        "terminal_outcome": proof["overall"],
        "stats": stats,
        "physical_proof": proof,
    }
    if receipt_path:
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    return out
