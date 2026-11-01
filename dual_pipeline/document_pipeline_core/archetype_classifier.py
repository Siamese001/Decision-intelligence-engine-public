"""
dual_pipeline/document_pipeline_core/archetype_classifier.py

Deterministic and fast-heuristic document archetype classifier.
Classifies multi-format document intakes into canonical regimes:
1. TABULAR_PORTFOLIO_EXPORT (e.g. NEA Team Assignments 21pp export)
2. STRATEGIC_OFFERING_PLAYBOOK (e.g. AMS 101 Training 25 slides)
3. SPARSE_ENABLEMENT_WORKSHOP (e.g. PartnerAdvisory Sales Immersion Day 1 112 slides)
4. PURSUIT_ORALS_PROPOSAL (e.g. NEE Infrastructure Automation Orals 111 slides)
5. CONTRACTUAL_SOW_PROSE (e.g. ENTERPRISE4U Align + PRF SOW)
6. HYBRID_PORTFOLIO_AND_TEMPLATE_DECK (e.g. 2027 FL Account Planning Playbook 74 slides)
7. POPULATED_ACCOUNT_PLAN_DASHBOARD (e.g. Populated Apex Global 2027 Plan Summary)
8. VISUAL_TOPOLOGY_AND_ORG_CHART (e.g. Apex Global Analytics Org Chart)
9. FINANCIAL_STAFFING_WORKBOOK (e.g. Global Pricing v2.0 Workbook)
10. AUTHORITATIVE_MEMO_OR_SPEC (Fallback general prose/spec)
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Any, Dict, List, Optional


class DocumentArchetype(str, Enum):
    TABULAR_PORTFOLIO_EXPORT = "TABULAR_PORTFOLIO_EXPORT"
    STRATEGIC_OFFERING_PLAYBOOK = "STRATEGIC_OFFERING_PLAYBOOK"
    SPARSE_ENABLEMENT_WORKSHOP = "SPARSE_ENABLEMENT_WORKSHOP"
    PURSUIT_ORALS_PROPOSAL = "PURSUIT_ORALS_PROPOSAL"
    CONTRACTUAL_SOW_PROSE = "CONTRACTUAL_SOW_PROSE"
    HYBRID_PORTFOLIO_AND_TEMPLATE_DECK = "HYBRID_PORTFOLIO_AND_TEMPLATE_DECK"
    POPULATED_ACCOUNT_PLAN_DASHBOARD = "POPULATED_ACCOUNT_PLAN_DASHBOARD"
    VISUAL_TOPOLOGY_AND_ORG_CHART = "VISUAL_TOPOLOGY_AND_ORG_CHART"
    FINANCIAL_STAFFING_WORKBOOK = "FINANCIAL_STAFFING_WORKBOOK"
    ROLE_FRAMEWORK_CATALOG = "ROLE_FRAMEWORK_CATALOG"
    AUTHORITATIVE_MEMO_OR_SPEC = "AUTHORITATIVE_MEMO_OR_SPEC"


_ROLE_CATALOG_CUES = re.compile(
    r"\b(?:me@partner_advisory|career\s+framework\s+role\s+profiles|understanding\s+role\s+profile\s+components|"
    r"relationship\s+to\s+other\s+roles|role\s+milestones|other\s+roles\s+to\s+explore)\b",
    re.IGNORECASE,
)


_WORKSHOP_CUES = re.compile(
    r"(?:\bdiscussion\s*\(document\)|\blet'?s\s+play\s+a\s+game\b|\bfreaky\s+friday\b|\bsmall\s+group\s+dinners\b|"
    r"\bpre-assessment\b|\bpost-assessment\b|\bactivity:\s*draft\b|\bstand\s*&\s*deliver\b|\bbreak!|\bbe\s+back\s+at\b)",
    re.IGNORECASE,
)

_ORALS_PROPOSAL_CUES = re.compile(
    r"\b(?:rfp\s+response|infrastructure\s+automation\s+factory|segment\s+[ab]\b|two\s+fixed-price\s+paths|"
    r"experience\s+modification\s+rate|proposal\s+validity|our\s+energy\s+experience|client\s+references|"
    r"representative\s+team\s+profiles)\b",
    re.IGNORECASE,
)

_SOW_CUES = re.compile(
    r"\b(?:statement\s+of\s+work|negotiated\s+purchaser\s+terms|hourly\s+rate|estimated\s+service\s+fees|"
    r"rebate\s+credit|client\s+responsibility|partner_advisory\s+responsibility|nature\s+of\s+the\s+engagement)\b",
    re.IGNORECASE,
)

_STAFFING_WORKBOOK_CUES = re.compile(
    r"\b(?:global\s+pricing|staffing\s+plan|rack\s+rate\s+revenue|rate\s+card\s+revenue|"
    r"mandatory\s+cost\s+contingency|salesforce\s+staff\s+plan\s+upload|tbd\d+)\b",
    re.IGNORECASE,
)

_ACCOUNT_PLAN_DASHBOARD_CUES = re.compile(
    r"\b(?:plan\s+summary:\s*20\d\d|plan\s+summary\s+by:\s*capability|business\s+unit\s+buyers|"
    r"gtm\s+priority\s+solutions|multi-year\s+revenue|weighted\s+pipeline|account\s+ambition)\b",
    re.IGNORECASE,
)

_TEMPLATE_PLAYBOOK_CUES = re.compile(
    r"\b(?:read\s+me\s+then\s+delete|\[account\s+name\]|account\s+planning\s+playbook|geo-pod:|"
    r"industry-pod:|c-suite\s+options)\b",
    re.IGNORECASE,
)

_OFFERING_PLAYBOOK_CUES = re.compile(
    r"\b(?:agentic\s+managed\s+services|ams\s+101|anatomy\s+of\s+.*deals|targeting\s+criteria\s+for\s+ams|"
    r"partner_advisory\s+traverse|how\s+to\s+tell\s+if\s+the\s+client\s+is\s+ready|who\s+to\s+bring\s+to\s+the\s+table)\b",
    re.IGNORECASE,
)

_SPOOF_TEXT_RE = re.compile(
    r"\b(?:test-spoof|mock-url|fake-citation|synthetic-benchmark-spoof|placeholder\s+link|lorem\s+ipsum)\b",
    re.IGNORECASE,
)


def _is_visual_org_or_workbook(document_text: str, meta: Dict[str, Any]) -> Optional[DocumentArchetype]:
    low = document_text.lower()
    if meta.get("is_visual_document") or meta.get("is_org_chart"):
        return DocumentArchetype.VISUAL_TOPOLOGY_AND_ORG_CHART
    if re.search(r"\breporting\s+to\s+[a-z\s,.'-]+(?:\(\d+\))?", document_text, re.I):
        return DocumentArchetype.VISUAL_TOPOLOGY_AND_ORG_CHART
    if len(re.findall(r"\(reports\s+to\s+[^)]+\)", document_text, re.I)) >= 2:
        return DocumentArchetype.VISUAL_TOPOLOGY_AND_ORG_CHART
    if re.search(r"\b(?:org\s+chart|organization\s+chart|organizational\s+hierarchy|team\s+topology\s+tree)\b", document_text, re.I):
        if ("tree" in low and "radial" in low) or "reporting" in low or "direct reports" in low:
            return DocumentArchetype.VISUAL_TOPOLOGY_AND_ORG_CHART
    if _STAFFING_WORKBOOK_CUES.search(document_text) or ("tbd1" in low and "hourly rate" in low and "margin" in low):
        return DocumentArchetype.FINANCIAL_STAFFING_WORKBOOK
    return None


def _is_tabular_sow_or_dashboard(document_text: str) -> Optional[DocumentArchetype]:
    low = document_text.lower()
    is_spoof = bool(_SPOOF_TEXT_RE.search(low))
    sm_count = len(re.findall(r"SM:\s*[^\n]+", document_text))
    pm_count = len(re.findall(r"PM:\s*[^\n]+", document_text))
    if (sm_count >= 5 and pm_count >= 5) or ("planning.apex_globalanalytics.com" in document_text and not is_spoof):
        return DocumentArchetype.TABULAR_PORTFOLIO_EXPORT
    if _SOW_CUES.search(document_text) and ("statement of work" in low or re.search(r"\bsow\b", low)):
        return DocumentArchetype.CONTRACTUAL_SOW_PROSE
    page_count = len(re.findall(r"(?:^|\n)\s*(?:#+\s*)?(?:Page|Slide)\s+\d+\b", document_text, re.I)) or 1
    if _ACCOUNT_PLAN_DASHBOARD_CUES.search(document_text) and "[account name]" not in low and "lorem ipsum" not in low and page_count <= 6:
        return DocumentArchetype.POPULATED_ACCOUNT_PLAN_DASHBOARD
    return None


def _resolve_explicit_archetype(meta: Dict[str, Any]) -> Optional[DocumentArchetype]:
    explicit = meta.get("document_archetype") or meta.get("archetype")
    if not explicit:
        return None
    norm = str(explicit).strip().upper()
    if norm in DocumentArchetype.__members__:
        return DocumentArchetype[norm]
    for member in DocumentArchetype:
        if member.value.upper() == norm:
            return member
    return None


def classify_document_archetype(
    document_text: Optional[str],
    metadata: Optional[Dict[str, Any]] = None,
) -> DocumentArchetype:
    """Classify input document into an authoritative archetype regime."""
    meta = metadata or {}
    explicit_arch = _resolve_explicit_archetype(meta)
    if explicit_arch is not None:
        return explicit_arch

    text = str(document_text or "").strip()
    if not text and meta:
        from dual_pipeline.document_pipeline_core.signal_value_router import (
            reconstruct_document_text_from_metadata,
        )
        text = reconstruct_document_text_from_metadata(document_text, meta)
    if not text:
        return DocumentArchetype.AUTHORITATIVE_MEMO_OR_SPEC

    early = _is_visual_org_or_workbook(text, meta) or _is_tabular_sow_or_dashboard(text)
    if early is not None:
        return early

    if len(_ROLE_CATALOG_CUES.findall(text)) >= 2:
        return DocumentArchetype.ROLE_FRAMEWORK_CATALOG
    if _TEMPLATE_PLAYBOOK_CUES.search(text):
        return DocumentArchetype.HYBRID_PORTFOLIO_AND_TEMPLATE_DECK
    if len(_ORALS_PROPOSAL_CUES.findall(text)) >= 2:
        return DocumentArchetype.PURSUIT_ORALS_PROPOSAL

    workshop_matches = len(_WORKSHOP_CUES.findall(text))
    if workshop_matches >= 3 or "sales immersion" in text.lower():
        return DocumentArchetype.SPARSE_ENABLEMENT_WORKSHOP

    if _ORALS_PROPOSAL_CUES.search(text):
        return DocumentArchetype.PURSUIT_ORALS_PROPOSAL
    if _OFFERING_PLAYBOOK_CUES.search(text):
        return DocumentArchetype.STRATEGIC_OFFERING_PLAYBOOK
    return DocumentArchetype.AUTHORITATIVE_MEMO_OR_SPEC


def classify_and_route_document_extraction(
    document_text: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Classify document archetype and attach intelligent signal value routing controls."""
    from dual_pipeline.document_pipeline_core.signal_value_router import apply_signal_profile_to_metadata

    target_meta = metadata if isinstance(metadata, dict) else {}
    profile = apply_signal_profile_to_metadata(document_text, target_meta)
    return profile.to_dict()

