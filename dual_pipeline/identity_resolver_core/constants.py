"""
dual_pipeline/identity_resolver_core/constants.py

SSOT constants for identity resolution, dialogue entity tracking,
phonetic ASR repair rules, and silence hallucination filters.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

TRACKED_DIALOGUE_ENTITIES: Dict[str, Dict[str, Any]] = {
    "mandy": {
        "canonical_name": "harper Hoffman",
        "org": "NEA",
        "role": "Product Manager",
        "location": "Saint Paul / Minneapolis, Minnesota",
        "charter": "PD Conduit / Conductor",
        "affected_stmt": "SOW-1 Completion Gating",
        "basis": "Colleen Barbato Charter Roster row 2; PM location roster; corroborated by Jen Pan at Line 285 (\"She's in St. Paul\")",
    },
    "tony": {
        "canonical_name": "Tony Kablan",
        "org": "PartnerAdvisory",
        "role": "Former Lens Engagement Lead / PM",
        "location": "Miami, Florida",
        "charter": "LENS / Visual Asset Intelligence System",
        "affected_stmt": "Initial Lens SOW Construction",
        "basis": "Colleen Barbato Charter Roster; Core workstream lead map; cited by Cameron Mercer as original SOW author",
    },
    "dan": {
        "canonical_name": "Drew Oakley",
        "org": "PartnerAdvisory",
        "role": "Client Partner / Account Relationship Lead",
        "location": "Chicago / Virtual",
        "charter": "Account Governance",
        "affected_stmt": "Executive Relationship Alignment",
        "basis": "urn:nee:card-view:NEE_10; cited by Cameron Mercer and Amit Ayer for executive outreach",
    },
    "ed": {
        "canonical_name": "Ed (NEA Lens Reviewer)",
        "org": "NEA",
        "role": "Client Validation / Testing Reviewer",
        "location": "NEA Operating Facility",
        "charter": "Lens Client Validation",
        "affected_stmt": "Rule Creation & Testing Feedback Loops",
        "basis": "In-dialogue testing participant; cited by Jen Pan and Alex Sanchez (rule creation bug)",
    },
    "jared": {
        "canonical_name": "Jared (NEA Client Stakeholder)",
        "org": "NEA / Corporate Services",
        "role": "Stakeholder Coverage / Client Alignment",
        "location": "Juno Beach / Virtual",
        "charter": "Corporate Services / Asset Optimization",
        "affected_stmt": "Executive Relationship Management",
        "basis": "urn:nee:card-view:NEE_10; cited by Cameron Mercer as stakeholder alongside Mandy",
    },
}

REPAIR_RULES: List[Tuple[str, str, float, str, str]] = [
    # Attendee vocatives / names
    (r"\bHey,\s+Mitt\b", "Hey, Amit", 0.98, "attendee_vocative", "Confirmed in-room attendee vocative: Alex Sanchez addressing Amit Ayer"),
    (r"\bHey\s+Mitt\b", "Hey Amit", 0.98, "attendee_vocative", "Confirmed in-room attendee vocative: Alex Sanchez addressing Amit Ayer"),
    (r"\bAmin\s+and\s+I\b", "Amit and I", 0.98, "attendee_vocative", "ASR phonetic homophone for in-room attendee Amit Ayer in first-person delivery update"),
    (r"\bAmin\b(?=\s+and\s+Jen)", "Amit", 0.95, "attendee_vocative", "In-room attendee Amit Ayer"),

    # SOW 1 & SOW 2 contract acronyms
    (r"\bSAO\s*2\b", "SOW 2", 0.97, "contract_acronym", "ASR phonetic distortion of Statement of Work Phase 2"),
    (r"\bSAO\s*1\b", "SOW 1", 0.97, "contract_acronym", "ASR phonetic distortion of Statement of Work Phase 1"),
    (r"\bSal\s*1\b", "SOW 1", 0.97, "contract_acronym", "ASR phonetic homophone /saʊ/ for Statement of Work Phase 1"),
    (r"\bSal\s*2\b", "SOW 2", 0.97, "contract_acronym", "ASR phonetic homophone /saʊ/ for Statement of Work Phase 2"),
    (r"\bSal,\s*One\b", "SOW 1", 0.97, "contract_acronym", "ASR split utterance for SOW 1"),
    (r"\bSal,\s*1\b", "SOW 1", 0.97, "contract_acronym", "ASR split utterance for SOW 1"),
    (r"\bin the SAL\b", "in the SOW", 0.96, "contract_acronym", "Technical requirements scope in Statement of Work"),
    (r"\bSound\s*1\b", "SOW 1", 0.96, "contract_acronym", "Contractual phase duration context: 'last day of SOW 1'"),
    (r"\bSound\s*2\b", "SOW 2", 0.96, "contract_acronym", "Contractual phase duration context: 'start SOW 2'"),
    (r"\bsound\s*one\b", "SOW 1", 0.96, "contract_acronym", "ASR phonetic homophone /saʊnd/ for SOW 1"),
    (r"\bsound\s*two\b", "SOW 2", 0.96, "contract_acronym", "ASR phonetic homophone /saʊnd/ for SOW 2"),
    (r"\bcell\s*one\b", "SOW 1", 0.95, "contract_acronym", "ASR phonetic homophone for SOW 1 completion ('call SOW 1 done')"),

    # Attendee identity phonetic distortions
    (r"\bAlston\s+Cassio\b", "Alex Sanchez", 0.98, "attendee_identity", "ASR phonetic distortion of in-room attendee Alex Sanchez"),
    (r"\bAustin\s+Cassio\b", "Alex Sanchez", 0.98, "attendee_identity", "ASR phonetic distortion of in-room attendee Alex Sanchez"),

    # Enterprise & Architecture Lexicon
    (r"\bSloan\b", "PartnerAdvisory", 0.97, "organization_lexicon", "ASR phonetic distortion of PartnerAdvisory consulting"),
    (r"\bFallon\b", "PartnerAdvisory", 0.97, "organization_lexicon", "Google Meet ASR phonetic distortion of PartnerAdvisory consulting"),
    (r"\b(?:NextA|Nexa)\b", "Apex Global", 0.98, "organization_lexicon", "Google Meet ASR phonetic distortion of Apex Global Enterprises"),
    (r"\bBungie\b", "Bunge", 0.97, "organization_lexicon", "Google Meet ASR phonetic distortion of Bunge client account"),
    (r"\bPuja\b", "Pooja", 0.98, "attendee_identity", "Google Meet ASR spelling distortion of Pooja"),
    (r"\bOracle\s+unifier\b", "Oracle Unifier", 0.98, "architecture_lexicon", "Google Meet ASR casing normalization for Oracle Unifier"),
    (r"\bF3\b", "AWS S3", 0.96, "architecture_lexicon", "ASR phonetic distortion of AWS S3 storage bucket"),
    (r"\b[Oo]ne[- ][Tt]urban\b", "one turbine", 0.96, "domain_lexicon", "ASR phonetic distortion of one turbine denominator"),
    (
        r"\bSarah\b(?=\s+(?:would\s+know\s+to\s+start|recommends\s+the\b|router\b|agent\b)|(?=.*\b(?:SAA|SAGE|architecture\s+review)\b))",
        "Sherpa",
        0.98,
        "product_homophone",
        "ASR phonetic homophone of Project Beacon product name ('Sarah' -> 'Sherpa') in SAA architecture review flow",
    ),
]

SILENCE_HALLUCINATION_PATTERNS: List[re.Pattern] = [
    re.compile(r"^\s*(?:thank\s+you(?:\s+very\s+much)?|thanks\s+for\s+watching|please\s+subscribe|subtitles\s+by|transcript\s+by)[\s.!,]*$", re.IGNORECASE),
    re.compile(r"^\s*\[\s*(?:applause|music|silence|laughter)\s*\]\s*$", re.IGNORECASE),
]
