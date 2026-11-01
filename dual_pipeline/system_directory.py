"""Enterprise System Directory and Entity Disambiguation Service.

L0/L1 domain catalog providing deterministic lookup for Apex Global / APEX enterprise
systems of record, source systems, governing projects, and writeback postures.
Prevents substring collisions (e.g., LAMS vs Sanskriti Lamsal) and active-document
editor bias.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class SystemEntity:
    """Represents a governed enterprise system of record or operational tool."""

    system_id: str
    canonical_name: str
    acronym: str
    aliases: Tuple[str, ...]
    primary_governing_project: str
    governing_projects: Tuple[str, ...]
    domain_scope: str
    system_of_record_for: str
    integration_posture: str
    writeback_posture: str
    writeback_thesis: str
    key_evidence_refs: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "system_id": self.system_id,
            "canonical_name": self.canonical_name,
            "system_name": self.canonical_name,
            "acronym": self.acronym,
            "aliases": list(self.aliases),
            "primary_governing_project": self.primary_governing_project,
            "governing_project": self.primary_governing_project,
            "governing_projects": list(self.governing_projects),
            "domain_scope": self.domain_scope,
            "system_of_record_for": self.system_of_record_for,
            "integration_posture": self.integration_posture,
            "writeback_posture": self.writeback_posture,
            "writeback_support": "DEFERRED" not in self.writeback_posture and "READ_ONLY" not in self.writeback_posture,
            "writeback_thesis": self.writeback_thesis,
            "key_evidence_refs": list(self.key_evidence_refs),
        }


# Governed Catalog of Enterprise Systems of Record
ENTERPRISE_SYSTEMS: Dict[str, SystemEntity] = {
    "system:lams": SystemEntity(
        system_id="system:lams",
        canonical_name="Land Asset Management System",
        acronym="LAMS",
        aliases=("LAMS", "LAMs", "LAMs System", "Land Asset Management System", "Land Asset Management"),
        primary_governing_project="project_nexus",
        governing_projects=("project_nexus",),
        domain_scope="Land screening, parcel acreage, tract acquisition, leases, easements, and project boundaries.",
        system_of_record_for="Land, tract, and parcel assets",
        integration_posture="Power BI live table reporting; nightly batch sync to corporate GIS; developer technical sync scheduled 09/17/2026.",
        writeback_posture="DEFERRED_PHASE_1",
        writeback_thesis=(
            "Nexus Phase 1 operates strictly as a read-only workspace leveraging Power BI live tables and "
            "24-hr batch synchronization to GIS. Future agentic computer-use or API writeback to push parcel edits "
            "(e.g., updating tract acreage) is an exploratory requirement requiring API discovery, security review, "
            "and service key endpoint authorization (09/17/2026 sync with Sharon Diaram)."
        ),
        key_evidence_refs=(
            "output/project_nexus/09-10-2026/01_Project_Nexus_09-10-2026_0900AM_1000AM_ET_hitl_resolved_transcript.md",
            "output/project_nexus/09-10-2026/02_Project_Nexus_09-10-2026_0900AM_1000AM_ET_executive_recap.md",
            "output/project_nexus/09-14-2026/01_Project_Nexus_Draft_Charters_09-14-2026_0137PM_0210PM_ET_hitl_resolved_transcript.md",
            "output/project_nexus/09-14-2026/02_Project_Nexus_Draft_Charters_09-14-2026_0137PM_0210PM_ET_executive_recap.md",
            "output/project_nexus/09-14-2026/03_Project_Nexus_Draft_Charters_09-14-2026_0137PM_0210PM_ET_canonical_signal_projection.json",
        ),
    ),
    "system:maximo": SystemEntity(
        system_id="system:maximo",
        canonical_name="IBM Maximo",
        acronym="Maximo",
        aliases=("IBM Maximo", "Maximo", "Maximo Asset Management"),
        primary_governing_project="project_lens",
        governing_projects=("project_lens", "regai", "fcdb"),
        domain_scope="Physical asset inventory, turbine nameplate records, maintenance work orders.",
        system_of_record_for="Physical equipment, nameplates, and maintenance logs",
        integration_posture="Interim structured data store in SOW 1; IBM Maximo writeback designated for SOW 2.",
        writeback_posture="DEFERRED_SOW_2",
        writeback_thesis=(
            "Cleanly decoupled from SOW 1 image processing and extraction. Direct Maximo writeback is gated on Max Core "
            "endpoint delivery and asset hierarchy migration. Validated extraction data is stored in an interim structured store."
        ),
        key_evidence_refs=(
            "output/project_lens/05-26-2026/03_LENS_Phase_1_SOW_05-26-2026_0900AM_1000AM_ET_canonical_signal_projection.json",
            "output/project_lens/05-26-2026/source_document.txt",
        ),
    ),
    "system:successfactors": SystemEntity(
        system_id="system:successfactors",
        canonical_name="SAP SuccessFactors",
        acronym="SuccessFactors",
        aliases=("SuccessFactors", "Success Factors", "SAP SuccessFactors", "SF"),
        primary_governing_project="enterprise_architecture",
        governing_projects=("enterprise_architecture", "enterprise_account_planning"),
        domain_scope="HR transactions, employee and organizational hierarchy, manager self-service.",
        system_of_record_for="HR personnel and organizational transactions",
        integration_posture="Native API payload integration preserving event reasons and native approval workflows.",
        writeback_posture="GOVERNED_NATIVE_WORKFLOW",
        writeback_thesis=(
            "Apex Global HR PoC Rubric mandates native SuccessFactors writeback preserving event reasons and native approval workflows "
            "(Criterion 6), deterministic confidence-based decisioning independent of LLMs (Criterion 3), and field-level "
            "exception routing (Criterion 4), strictly prohibiting unmonitored shadow processes."
        ),
        key_evidence_refs=(
            "output/enterprise_architecture/08-11-2026/01_AI_Enterprise_Systems_of_Record_HR_PoC_08-11-2026_0153PM_0230PM_ET_hitl_resolved_transcript.md",
            "output/enterprise_architecture/08-11-2026/02_AI_Enterprise_Systems_of_Record_HR_PoC_08-11-2026_0153PM_0230PM_ET_executive_recap.md",
        ),
    ),
    "system:sap": SystemEntity(
        system_id="system:sap",
        canonical_name="SAP ERP",
        acronym="SAP",
        aliases=("SAP", "SAP ECC", "SAP S/4HANA"),
        primary_governing_project="enterprise_architecture",
        governing_projects=("enterprise_architecture", "project_nexus"),
        domain_scope="Enterprise resource planning, financial accounting, supply chain, procurement.",
        system_of_record_for="Core financials, procurement, purchase orders",
        integration_posture="Strict enterprise boundary; read/query permitted; autonomous writeback heavily restricted.",
        writeback_posture="ENTERPRISE_RESTRICTED",
        writeback_thesis=(
            "Strict enterprise risk boundary. Autonomous AI execution is prohibited from creating purchase orders or financial "
            "commitments without mandatory human approvals."
        ),
        key_evidence_refs=(
            "output/project_nexus/09-10-2026/01_Project_Nexus_09-10-2026_0900AM_1000AM_ET_hitl_resolved_transcript.md",
            "output/enterprise_architecture/08-11-2026/01_AI_Enterprise_Systems_of_Record_HR_PoC_08-11-2026_0153PM_0230PM_ET_hitl_resolved_transcript.md",
        ),
    ),
    "system:servicenow": SystemEntity(
        system_id="system:servicenow",
        canonical_name="ServiceNow",
        acronym="ServiceNow",
        aliases=("ServiceNow", "SNOW", "Service-Now"),
        primary_governing_project="enterprise_architecture",
        governing_projects=("enterprise_architecture",),
        domain_scope="IT service delivery, enterprise ticketing, HR service management via IT4U.",
        system_of_record_for="IT services, requests, and incidents",
        integration_posture="Evaluated for native platform additions via IT4U partner team.",
        writeback_posture="GOVERNED_ITSM_WORKFLOW",
        writeback_thesis="Under evaluation with IT4U for native agentic service workflows.",
        key_evidence_refs=(
            "output/enterprise_architecture/08-11-2026/01_AI_Enterprise_Systems_of_Record_HR_PoC_08-11-2026_0153PM_0230PM_ET_hitl_resolved_transcript.md",
        ),
    ),
    "system:unifier": SystemEntity(
        system_id="system:unifier",
        canonical_name="Primavera Unifier",
        acronym="Unifier",
        aliases=("Primavera Unifier", "Unifier"),
        primary_governing_project="project_nexus",
        governing_projects=("project_nexus",),
        domain_scope="Project repository for final engineering drawings and capital delivery tasks.",
        system_of_record_for="Final engineering drawings and construction management",
        integration_posture="Legacy siloed system requiring high manual clicks.",
        writeback_posture="AUTOMATION_CANDIDATE",
        writeback_thesis="Targeted for automated drawing upload agent to eliminate 30-click manual handoffs.",
        key_evidence_refs=(
            "output/project_nexus/09-10-2026/01_Project_Nexus_09-10-2026_0900AM_1000AM_ET_hitl_resolved_transcript.md",
        ),
    ),
    "system:p6": SystemEntity(
        system_id="system:p6",
        canonical_name="Primavera P6",
        acronym="P6",
        aliases=("Primavera P6", "P6", "Primavera P6 Scheduling"),
        primary_governing_project="project_nexus",
        governing_projects=("project_nexus", "ec_scheduling"),
        domain_scope="Project schedule and resource allocations.",
        system_of_record_for="Master project schedule",
        integration_posture="Read-only milestone and schedule extraction.",
        writeback_posture="READ_ONLY",
        writeback_thesis="Read-only scheduling data extraction.",
        key_evidence_refs=(
            "output/project_nexus/08-18-2026/01_APEX_Nexus_Rewire_08-18-2026_0900AM_1000AM_ET_hitl_resolved_transcript.md",
        ),
    ),
    "system:gis": SystemEntity(
        system_id="system:gis",
        canonical_name="Corporate GIS / Web Mapping",
        acronym="GIS",
        aliases=("GIS", "Web Map", "ArcGIS", "APEX GIS"),
        primary_governing_project="project_nexus",
        governing_projects=("project_nexus",),
        domain_scope="Spatial boundaries, site polygons, interactive web map visualization.",
        system_of_record_for="Spatial boundaries and project polygons",
        integration_posture="Nightly 24-hr batch synchronization with LAMS; read-only map navigation in Nexus.",
        writeback_posture="READ_ONLY_BATCH_SYNC",
        writeback_thesis="24-hr data staleness for GIS synchronization is operationally tolerable; Nexus embeds read-only web mapping.",
        key_evidence_refs=(
            "output/project_nexus/09-10-2026/01_Project_Nexus_09-10-2026_0900AM_1000AM_ET_hitl_resolved_transcript.md",
            "output/project_nexus/09-14-2026/03_Project_Nexus_Draft_Charters_09-14-2026_0137PM_0210PM_ET_canonical_signal_projection.json",
        ),
    ),
    "system:salesforce": SystemEntity(
        system_id="system:salesforce",
        canonical_name="Salesforce CRM",
        acronym="Salesforce",
        aliases=("Salesforce", "SFDC", "EVA Salesforce"),
        primary_governing_project="project_atlas",
        governing_projects=("project_atlas", "nea"),
        domain_scope="Customer accounts, customer service interactions, billing escalations.",
        system_of_record_for="Customer relationships and service escalations",
        integration_posture="Mobile app integration, write actions, and production hardening.",
        writeback_posture="HARDENED_WRITE_ACTIONS",
        writeback_thesis="Salesforce write actions and escalations actively deployed under production hardening in EVA.",
        key_evidence_refs=(
            "cards/NEE_02_Portfolio_Status_and_Project_Dossiers.md",
        ),
    ),
    "system:documentum": SystemEntity(
        system_id="system:documentum",
        canonical_name="OpenText Documentum",
        acronym="Documentum",
        aliases=("Documentum", "Documentum ECM"),
        primary_governing_project="regai",
        governing_projects=("regai", "dociq"),
        domain_scope="Enterprise content management, engineering drawings, compliance documentation.",
        system_of_record_for="Compliance documents and drawings",
        integration_posture="Document search and controlled writes.",
        writeback_posture="CONTROLLED_PRODUCTION_WRITES",
        writeback_thesis="DocIQ returned to green after cost/architecture approval and production Documentum writes.",
        key_evidence_refs=(
            "cards/NEE_02_Portfolio_Status_and_Project_Dossiers.md",
        ),
    ),
    "system:scada_ems": SystemEntity(
        system_id="system:scada_ems",
        canonical_name="SCADA / Energy Management System",
        acronym="SCADA",
        aliases=("SCADA", "EMS", "SCADA/EMS", "Protection Relays"),
        primary_governing_project="apex_trans",
        governing_projects=("apex_trans",),
        domain_scope="Substation automation, grid monitoring, protection relays.",
        system_of_record_for="Operational grid state and telemetry",
        integration_posture="Strictly read-only situational awareness.",
        writeback_posture="PROHIBITED_READ_ONLY",
        writeback_thesis="Strictly read-only situational awareness engine; writeback or control commands to SCADA, EMS, or protection relays are strictly prohibited.",
        key_evidence_refs=(
            "output/apex_trans/09-10-2026/02_NEET_Insights_Hub_Tech_Services_Interview_09-10-2026_0334PM_0500PM_ET_executive_recap.md",
        ),
    ),
}

# Known Person/Stakeholder collision patterns to protect from false system match
PERSON_COLLISION_PATTERNS = (
    re.compile(r"\bSanskriti\s+Lamsal\b", re.IGNORECASE),
    re.compile(r"\bLamsal\b", re.IGNORECASE),
)


def lookup_system(identifier: str) -> Optional[SystemEntity]:
    """Resolves an exact system ID, acronym, or alias to its SystemEntity.

    Enforces strict word boundary matching.
    """
    clean = identifier.strip().lower()
    if clean.startswith("system:"):
        return ENTERPRISE_SYSTEMS.get(clean)

    for entity in ENTERPRISE_SYSTEMS.values():
        if entity.acronym.lower() == clean:
            return entity
        if entity.canonical_name.lower() == clean:
            return entity
        for alias in entity.aliases:
            if alias.lower() == clean:
                return entity

    return None


def _entity_matches_text(
    entity: SystemEntity, text: str, is_person_context: bool
) -> bool:
    for token in (entity.acronym,) + entity.aliases:
        if token.lower() in ("lams", "lams system") and is_person_context:
            pattern = re.compile(r"\bLAM[sS]\b(?!\s*Lamsal\b)")
            if pattern.search(text):
                return True
        else:
            pattern = re.compile(rf"\b{re.escape(token)}\b", re.IGNORECASE)
            if pattern.search(text):
                return True
    return False


def find_systems_in_text(text: str) -> List[SystemEntity]:
    """Finds all distinct Enterprise Systems mentioned in the given text.

    Enforces word-boundary regex matching to eliminate collisions with person names
    (e.g., 'Sanskriti Lamsal' will NOT match 'LAMS').
    """
    if not text:
        return []

    is_person_context = any(p.search(text) for p in PERSON_COLLISION_PATTERNS)
    found: Dict[str, SystemEntity] = {}
    for entity in ENTERPRISE_SYSTEMS.values():
        if _entity_matches_text(entity, text, is_person_context):
            found[entity.system_id] = entity

    return list(found.values())


def resolve_system_project(text: str) -> Optional[str]:
    """Returns the primary governing project if enterprise systems are mentioned in text.

    If multiple systems are mentioned:
    - If all systems share the same governing project (e.g. LAMS and GIS -> project_nexus),
      returns that shared project.
    - If systems span projects, returns the project of the earliest appearing system in text.
    """
    systems = find_systems_in_text(text)
    if not systems:
        return None
    projects = {s.primary_governing_project for s in systems}
    if len(projects) == 1:
        return next(iter(projects))

    earliest_pos = len(text) + 1
    earliest_proj = None
    clean_text = text.lower()
    for sys in systems:
        for alias in (sys.acronym,) + sys.aliases:
            idx = clean_text.find(alias.lower())
            if idx != -1 and idx < earliest_pos:
                earliest_pos = idx
                earliest_proj = sys.primary_governing_project

    return earliest_proj or systems[0].primary_governing_project


def get_system_writeback_thesis(identifier: str) -> Optional[str]:
    """Returns the authoritative writeback thesis for the given system."""
    entity = lookup_system(identifier)
    return entity.writeback_thesis if entity else None


def get_all_systems() -> Dict[str, SystemEntity]:
    """Returns all registered enterprise systems."""
    return dict(ENTERPRISE_SYSTEMS)


def get_systems_for_project(project: str) -> list[SystemEntity]:
    """Returns all systems associated with a given governing project."""
    norm = project.lower().strip()
    return [
        sys for sys in ENTERPRISE_SYSTEMS.values()
        if norm in [p.lower() for p in sys.governing_projects]
    ]


__all__ = [
    "ENTERPRISE_SYSTEMS",
    "SystemEntity",
    "find_systems_in_text",
    "get_all_systems",
    "get_system_writeback_thesis",
    "get_systems_for_project",
    "lookup_system",
    "resolve_system_project",
]

