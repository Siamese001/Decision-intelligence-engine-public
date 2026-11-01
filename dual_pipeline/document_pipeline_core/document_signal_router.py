"""Two-Stage Hybrid 7-Archetype Document Signal Router (DEC-INGEST-01 / Wave 5).

Stage 1 (U0/L0): Fast structural and metadata fingerprinting into 7 document archetypes.
Stage 2 (C0): Section-level signal scoring to separate high-signal binding or architectural
clauses from boilerplate collateral within the same document, enforcing archetype-specific
authority ranks, inverted C-17 density caps, temporal decay, and disposition floors.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence


class DocumentArchetype(str, Enum):
    """Canonical 7-archetype document taxonomy for governed signal calibration."""
    SOW_CONTRACT = "SOW_CONTRACT"
    ARCH_DESIGN_SPEC = "ARCH_DESIGN_SPEC"
    EXEC_STEERING_DECK = "EXEC_STEERING_DECK"
    TECH_REFERENCE_DOC = "TECH_REFERENCE_DOC"
    PROJECT_STATUS_SNAPSHOT = "PROJECT_STATUS_SNAPSHOT"
    WORKING_NOTES_BRAINSTORM = "WORKING_NOTES_BRAINSTORM"
    BOILERPLATE_COLLATERAL = "BOILERPLATE_COLLATERAL"


@dataclass(frozen=True)
class ArchetypeSignalPolicy:
    """Signal calibration contract for a single document archetype."""
    archetype: DocumentArchetype
    authority_rank: int
    source_posture: str
    auto_approve_eligible: bool
    fact_promotion_eligible: bool
    max_promoted_mutations_per_1k_words: int
    unlimited_evidence_only: bool
    stale_after_days: Optional[int]
    default_disposition: str

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["archetype"] = self.archetype.value
        return data


ARCHETYPE_POLICIES: Dict[DocumentArchetype, ArchetypeSignalPolicy] = {
    DocumentArchetype.SOW_CONTRACT: ArchetypeSignalPolicy(
        archetype=DocumentArchetype.SOW_CONTRACT,
        authority_rank=9,
        source_posture="authoritative_governed",
        auto_approve_eligible=True,
        fact_promotion_eligible=True,
        max_promoted_mutations_per_1k_words=25,
        unlimited_evidence_only=True,
        stale_after_days=None,
        default_disposition="APPROVED",
    ),
    DocumentArchetype.ARCH_DESIGN_SPEC: ArchetypeSignalPolicy(
        archetype=DocumentArchetype.ARCH_DESIGN_SPEC,
        authority_rank=8,
        source_posture="authoritative_governed",
        auto_approve_eligible=True,
        fact_promotion_eligible=True,
        max_promoted_mutations_per_1k_words=25,
        unlimited_evidence_only=True,
        stale_after_days=None,
        default_disposition="APPROVED",
    ),
    DocumentArchetype.EXEC_STEERING_DECK: ArchetypeSignalPolicy(
        archetype=DocumentArchetype.EXEC_STEERING_DECK,
        authority_rank=7,
        source_posture="authoritative_governed",
        auto_approve_eligible=True,
        fact_promotion_eligible=True,
        max_promoted_mutations_per_1k_words=20,
        unlimited_evidence_only=True,
        stale_after_days=None,
        default_disposition="APPROVED",
    ),
    DocumentArchetype.TECH_REFERENCE_DOC: ArchetypeSignalPolicy(
        archetype=DocumentArchetype.TECH_REFERENCE_DOC,
        authority_rank=6,
        source_posture="reference_manual",
        auto_approve_eligible=False,
        fact_promotion_eligible=False,
        max_promoted_mutations_per_1k_words=3,
        unlimited_evidence_only=True,
        stale_after_days=None,
        default_disposition="CANDIDATE",
    ),
    DocumentArchetype.PROJECT_STATUS_SNAPSHOT: ArchetypeSignalPolicy(
        archetype=DocumentArchetype.PROJECT_STATUS_SNAPSHOT,
        authority_rank=5,
        source_posture="citation_only_weekly_snapshot",
        auto_approve_eligible=False,
        fact_promotion_eligible=False,
        max_promoted_mutations_per_1k_words=10,
        unlimited_evidence_only=True,
        stale_after_days=14,
        default_disposition="CANDIDATE",
    ),
    DocumentArchetype.WORKING_NOTES_BRAINSTORM: ArchetypeSignalPolicy(
        archetype=DocumentArchetype.WORKING_NOTES_BRAINSTORM,
        authority_rank=3,
        source_posture="exploratory_draft",
        auto_approve_eligible=False,
        fact_promotion_eligible=False,
        max_promoted_mutations_per_1k_words=8,
        unlimited_evidence_only=True,
        stale_after_days=30,
        default_disposition="CANDIDATE",
    ),
    DocumentArchetype.BOILERPLATE_COLLATERAL: ArchetypeSignalPolicy(
        archetype=DocumentArchetype.BOILERPLATE_COLLATERAL,
        authority_rank=1,
        source_posture="boilerplate_source_only",
        auto_approve_eligible=False,
        fact_promotion_eligible=False,
        max_promoted_mutations_per_1k_words=0,
        unlimited_evidence_only=True,
        stale_after_days=None,
        default_disposition="SOURCE_ONLY",
    ),
}

_BOILERPLATE_SECTION_RE = re.compile(
    r"\b(?:about\s+partner_advisory|corporate\s+overview|disclaimer|confidentiality\s+notice|"
    r"all\s+rights\s+reserved|indemnification|governing\s+law|force\s+majeure|"
    r"table\s+of\s+contents|appendix\s+boilerplate|terms\s+and\s+conditions|"
    r"equal\s+opportunity|safe\s+harbor)\b",
    re.IGNORECASE,
)

_HIGH_SIGNAL_CLAUSE_RE = re.compile(
    r"\b(?:deliverable|milestone|architecture|cutover|sla|commitment|decision|"
    r"target\s+state|data\s+contract|owner|accountable|budget|timeline|scope\s+of\s+work|"
    r"production|integration|dependency|risk\s+mitigation)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SectionSignalScore:
    """Stage 2 section-level signal evaluation within a document."""
    section_heading: str
    word_count: int
    signal_score: float
    is_boilerplate: bool
    recommended_lane: str
    section_archetype: DocumentArchetype

    def to_dict(self) -> Dict[str, Any]:
        return {
            "section_heading": self.section_heading,
            "word_count": self.word_count,
            "signal_score": round(self.signal_score, 4),
            "is_boilerplate": self.is_boilerplate,
            "recommended_lane": self.recommended_lane,
            "section_archetype": self.section_archetype.value,
        }


@dataclass(frozen=True)
class DocumentSignalRoutingDecision:
    """Combined Stage 1 + Stage 2 routing decision for a document."""
    archetype: DocumentArchetype
    policy: ArchetypeSignalPolicy
    word_count: int
    max_promoted_mutations: int
    sections: List[SectionSignalScore] = field(default_factory=list)
    rationale: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": "1.0.0",
            "router_architecture": "two_stage_hybrid_v1",
            "archetype": self.archetype.value,
            "policy": self.policy.to_dict(),
            "word_count": self.word_count,
            "max_promoted_mutations": self.max_promoted_mutations,
            "sections": [s.to_dict() for s in self.sections],
            "rationale": list(self.rationale),
        }


def _match_stage1_archetype(title_low: str, body_low: str, meta: Mapping[str, Any]) -> tuple[DocumentArchetype, str]:
    explicit = str(meta.get("document_archetype") or meta.get("archetype") or "").strip().upper()
    if explicit in DocumentArchetype.__members__:
        return DocumentArchetype[explicit], f"explicit metadata archetype={explicit}"

    combined = f"{title_low}\n{body_low[:4000]}"
    meta_type = str(meta.get("type") or meta.get("document_type") or "").strip().lower()
    if meta_type in {"program", "product", "mission", "portfolio", "charter"} or re.search(
        r"\b(?:statement\s+of\s+work|master\s+services\s+agreement|\bsow\b|change\s+order|contract\s+amendment|program\s+planning\s+-\s+products|financial\s+summary\s*\(5-year|program\s+charter|mission\s+charter|portfolio\s+headcount)\b",
        combined,
    ):
        return DocumentArchetype.SOW_CONTRACT, "matched SOW/contract or authoritative portfolio charter markers"
    if re.search(r"\b(?:weekly\s+status|status\s+report|sprint\s+status|rag\s+status|weekly\s+snapshot|project\s+status)\b", combined):
        return DocumentArchetype.PROJECT_STATUS_SNAPSHOT, "matched periodic status/RAG snapshot markers"
    if re.search(r"\b(?:architecture\s+design|technical\s+design\s+document|\btdd\b|\badr\b|architecture\s+decision|system\s+design\s+spec|target\s+architecture)\b", combined):
        return DocumentArchetype.ARCH_DESIGN_SPEC, "matched architecture/design specification markers"
    if re.search(r"\b(?:steering\s+committee|executive\s+readout|board\s+deck|qbr\b|executive\s+briefing|account\s+planning|exec\s+steering)\b", combined):
        return DocumentArchetype.EXEC_STEERING_DECK, "matched executive steering/readout markers"
    if re.search(r"\b(?:api\s+reference|sdk\s+documentation|reference\s+manual|runbook|cli\s+reference|configuration\s+guide|user\s+manual)\b", combined):
        return DocumentArchetype.TECH_REFERENCE_DOC, "matched technical reference manual markers"
    if re.search(r"\b(?:brainstorm|working\s+notes|scratchpad|whiteboard|rough\s+notes|ideation\s+session|draft\s+notes)\b", combined):
        return DocumentArchetype.WORKING_NOTES_BRAINSTORM, "matched exploratory working notes/brainstorm markers"
    if re.search(r"\b(?:corporate\s+brochure|company\s+credentials|marketing\s+collateral|standard\s+boilerplate|nda\s+template)\b", combined):
        return DocumentArchetype.BOILERPLATE_COLLATERAL, "matched boilerplate/marketing collateral markers"
    return DocumentArchetype.ARCH_DESIGN_SPEC, "defaulted to structured design/specification profile"


def _split_markdown_sections(text: str) -> List[tuple[str, str]]:
    raw_chunks = re.split(r"(?m)^(#{1,4}\s+.+)$", text or "")
    sections: List[tuple[str, str]] = []
    curr_heading = "## Document Root"
    curr_body: List[str] = []
    for part in raw_chunks:
        s = part.strip()
        if re.match(r"^#{1,4}\s+", s):
            if curr_body:
                sections.append((curr_heading, "\n".join(curr_body)))
                curr_body = []
            curr_heading = s
        elif s:
            curr_body.append(s)
    if curr_body or not sections:
        sections.append((curr_heading, "\n".join(curr_body)))
    return sections


def _score_single_section(
    heading: str, body: str, base_archetype: DocumentArchetype
) -> SectionSignalScore:
    combined = f"{heading}\n{body}"
    w_count = len(combined.split())
    is_bp = bool(_BOILERPLATE_SECTION_RE.search(heading)) or (
        w_count < 120
        and bool(_BOILERPLATE_SECTION_RE.search(body))
        and not bool(_HIGH_SIGNAL_CLAUSE_RE.search(body))
    )
    high_hits = len(_HIGH_SIGNAL_CLAUSE_RE.findall(combined))
    density = min(1.0, (high_hits / max(1.0, w_count / 25.0)))
    if is_bp or base_archetype == DocumentArchetype.BOILERPLATE_COLLATERAL:
        score, lane, sec_arch = (
            round(min(0.15, density * 0.2), 4),
            "SOURCE_ONLY",
            DocumentArchetype.BOILERPLATE_COLLATERAL,
        )
    elif base_archetype == DocumentArchetype.TECH_REFERENCE_DOC and density < 0.35:
        score, lane, sec_arch = (
            round(max(0.25, density), 4),
            "EVIDENCE_ONLY",
            DocumentArchetype.TECH_REFERENCE_DOC,
        )
    else:
        score, lane, sec_arch = (
            round(max(0.55, min(1.0, 0.5 + density * 0.5)), 4),
            ARCHETYPE_POLICIES[base_archetype].default_disposition,
            base_archetype,
        )
    return SectionSignalScore(
        section_heading=heading,
        word_count=w_count,
        signal_score=score,
        is_boilerplate=is_bp,
        recommended_lane=lane,
        section_archetype=sec_arch,
    )


def score_document_sections(
    text: str,
    base_archetype: DocumentArchetype,
) -> List[SectionSignalScore]:
    """Stage 2 (C0): Split document into sections and score signal density vs boilerplate."""
    return [
        _score_single_section(h, b, base_archetype)
        for h, b in _split_markdown_sections(text)
    ]


def evaluate_document_signal_routing(
    text: str,
    *,
    title: str = "",
    metadata: Optional[Mapping[str, Any]] = None,
) -> DocumentSignalRoutingDecision:
    """Execute Two-Stage Hybrid Document Signal Router (Stage 1 U0/L0 + Stage 2 C0)."""
    meta = metadata or {}
    archetype, reason = _match_stage1_archetype((title or str(meta.get("title") or "")).lower(), (text or "").lower(), meta)
    policy = ARCHETYPE_POLICIES[archetype]
    sections = score_document_sections(text or "", archetype)
    total_words = max(1, len((text or "").split()))

    if policy.max_promoted_mutations_per_1k_words == 0:
        max_mutations = 0
    else:
        scaled = int((total_words / 1000.0) * policy.max_promoted_mutations_per_1k_words)
        floor = 3 if policy.max_promoted_mutations_per_1k_words <= 3 else 5
        max_mutations = max(floor, scaled)

    return DocumentSignalRoutingDecision(
        archetype=archetype,
        policy=policy,
        word_count=total_words,
        max_promoted_mutations=max_mutations,
        sections=sections,
        rationale=[reason],
    )


def apply_document_signal_budget_to_candidates(
    candidates: Sequence[Dict[str, Any]],
    routing_decision: DocumentSignalRoutingDecision,
) -> List[Dict[str, Any]]:
    """Calibrate candidate materiality, source_only flags, and mutation caps per archetype policy."""
    policy = routing_decision.policy
    bp_headings = {
        s.section_heading.lstrip("#").strip().lower()
        for s in routing_decision.sections
        if s.is_boilerplate
    }
    promoted_count = 0
    calibrated: List[Dict[str, Any]] = []

    for cand in candidates:
        c_copy = dict(cand)
        loc_val = str((c_copy.get("source_locator") or {}).get("value") or "").lstrip("#").strip().lower()
        in_boilerplate_sec = bool(loc_val and any(bh and bh in loc_val for bh in bp_headings))

        if policy.archetype == DocumentArchetype.BOILERPLATE_COLLATERAL or in_boilerplate_sec:
            c_copy["document_signal_disposition"] = "SOURCE_ONLY"
            c_copy["materiality"] = "low"
            calibrated.append(c_copy)
            continue

        is_structural = c_copy.get("candidate_type") in {
            "entity_mention",
            "architecture_relationship",
            "architecture_component",
            "decision",
            "action",
            "risk",
        }
        has_grounded_ev = bool(c_copy.get("evidence_ids")) and policy.auto_approve_eligible
        if (promoted_count < routing_decision.max_promoted_mutations or has_grounded_ev) and (
            is_structural or policy.archetype != DocumentArchetype.TECH_REFERENCE_DOC
        ):
            promoted_count += 1
            c_copy["document_signal_disposition"] = policy.default_disposition
        else:
            c_copy["document_signal_disposition"] = "EVIDENCE_ONLY"
            c_copy["materiality"] = "low"
        calibrated.append(c_copy)
    return calibrated
