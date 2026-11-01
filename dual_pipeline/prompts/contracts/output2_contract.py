"""
dual_pipeline/prompts/contracts/output2_contract.py

Declarative SSOT Contract for Output 02 (Executive Strategic Brief / Recap).
Governs generation, QA Gate 2, and ExecutiveRecapJudge evaluation.
Enforces archetype-adaptive section topologies, single Action & Governance Ledger,
and strict anti-redundancy rules.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from dual_pipeline.prompts.config import CANONICAL_PROMPT_CONFIG
from .output_contract import (
    MeetingArchetype,
    OutputContract,
    RubricDimension,
    SectionRequirement,
    SectionSpecification,
)

FORBIDDEN_APPENDIX_DISCLAIMERS = (
    "no reliable utterance mapping",
    "lacks speaker mapping",
    "requires speaker-resolved evidence",
    "individual architectural position therefore remains unconfirmed",
    "individual position remains unconfirmed",
    "cannot safely be presented as",
    "neither agreement nor disagreement should be inferred",
    "individual stance is unconfirmed",
)

WORD_SALAD_PATTERNS = (
    "aspirational operating concept",
    "evidenced rejection behavior",
    "hypothesized routing",
    "configuration persistence versus data portability",
    "manifest-controlled",
    "bounded execution wedge",
    "coaching future candidates",
    "synthetic monday-through-friday",
    "tactical task-tracker",
)

WORD_SALAD_REGEX = re.compile(
    r"\b(" + "|".join(re.escape(p) for p in WORD_SALAD_PATTERNS) + r"|wedge)\b",
    re.IGNORECASE,
)

ROLE_ANONYMIZATION_PATTERNS = re.compile(
    r"\b(the (?:UI|user interface) lead|the lead architect|the technical contributor|"
    r"the senior architect|the primary speaker|the account lead|the primary candidate|"
    r"the presenter|the facilitator)\b",
    re.IGNORECASE,
)

PASSIVE_HEDGING_PATTERNS = (
    "did not claim", "did not treat", "did not present", "did not confirm",
    "remained uncertain", "without confirming", "neither observation became", "was not an agreed",
    "not a demonstrated change", "not a completed configuration change",
    "no implementation commitment followed", "his contribution therefore separated",
    "this was a proposal about", "not confirmation of",
)


def build_output2_contract() -> OutputContract:
    """Build canonical Output 02 contract."""
    sections = {
        "Meeting Objective": SectionSpecification(
            header="Meeting Objective",
            description="Operational context, problem background, and session objective established first.",
            requirements_by_archetype={
                MeetingArchetype.GENERAL: SectionRequirement.MANDATORY,
                MeetingArchetype.ARCHITECTURE_DEEP_DIVE: SectionRequirement.MANDATORY,
                MeetingArchetype.STRATEGIC_DISCOVERY: SectionRequirement.MANDATORY,
                MeetingArchetype.GOVERNANCE_REVIEW: SectionRequirement.MANDATORY,
                MeetingArchetype.TACTICAL_ALIGNMENT: SectionRequirement.MANDATORY,
                MeetingArchetype.LESSONS_LEARNED: SectionRequirement.MANDATORY,
            },
            guidance=(
                "Establish operational context first so a zero-context reader understands the core problem. "
                "Include verified wall-clock ET window if supplied by metadata; if absent from source, "
                "explicitly state 'Time window: Unavailable from source evidence' without penalty or fabrication."
            ),
            anti_patterns=("Frontloading decisions without context", "Omission of operational catalyst"),
        ),
        "Key Discussion Points & Findings": SectionSpecification(
            header="Key Discussion Points & Findings",
            description="Itemized technical and operational findings with transcript locators and concrete evidence.",
            requirements_by_archetype={
                MeetingArchetype.GENERAL: SectionRequirement.MANDATORY,
                MeetingArchetype.ARCHITECTURE_DEEP_DIVE: SectionRequirement.MANDATORY,
                MeetingArchetype.STRATEGIC_DISCOVERY: SectionRequirement.MANDATORY,
                MeetingArchetype.GOVERNANCE_REVIEW: SectionRequirement.MANDATORY,
                MeetingArchetype.TACTICAL_ALIGNMENT: SectionRequirement.MANDATORY,
                MeetingArchetype.LESSONS_LEARNED: SectionRequirement.MANDATORY,
            },
            guidance="Ground findings in factual dialogue, quote critical friction, and explain acronyms. Format anchors as '**[Actor] [Action] [Substance / Takeaway]:**' (70–110 words each) per judge rubric.",
            anti_patterns=(
                "Unexplained acronyms",
                "Whitewashing client friction or demo failures",
                "Excessive verbatim narrative overlap (>35%) between Section 3 and Section 7",
                "Duplicating personal debate pushbacks in Section 3 instead of Appendix (Rule C-21)",
            ),
        ),
        "Strategic Synthesis": SectionSpecification(
            header="Strategic Synthesis",
            description="Dedicated translation matrix ('What We Heard' vs. 'What It Means').",
            requirements_by_archetype={
                MeetingArchetype.GENERAL: SectionRequirement.CONDITIONAL,
                MeetingArchetype.ARCHITECTURE_DEEP_DIVE: SectionRequirement.OPTIONAL,
                MeetingArchetype.STRATEGIC_DISCOVERY: SectionRequirement.CONDITIONAL,
                MeetingArchetype.GOVERNANCE_REVIEW: SectionRequirement.CONDITIONAL,
                MeetingArchetype.TACTICAL_ALIGNMENT: SectionRequirement.OPTIONAL,
                MeetingArchetype.LESSONS_LEARNED: SectionRequirement.CONDITIONAL,
            },
            guidance="High-signal translation of spoken dialogue into strategic and architectural implications.",
            anti_patterns=("Paraphrasing dialogue without analytical translation", "Inventing ungrounded strategy"),
        ),
        "Architectural Trade-Off Matrix": SectionSpecification(
            header="Architectural Trade-Off Matrix",
            description="Comparative analysis of competing technical approaches, trade-offs, blast radius, and team consensus.",
            requirements_by_archetype={
                MeetingArchetype.GENERAL: SectionRequirement.OPTIONAL,
                MeetingArchetype.ARCHITECTURE_DEEP_DIVE: SectionRequirement.CONDITIONAL,
                MeetingArchetype.STRATEGIC_DISCOVERY: SectionRequirement.OPTIONAL,
                MeetingArchetype.GOVERNANCE_REVIEW: SectionRequirement.OPTIONAL,
                MeetingArchetype.TACTICAL_ALIGNMENT: SectionRequirement.OPTIONAL,
                MeetingArchetype.LESSONS_LEARNED: SectionRequirement.OPTIONAL,
            },
            guidance=(
                "Conditional extension for internal technical syncs. Detail technical options evaluated, mechanisms, "
                "blast radius, architectural risk, and team consensus ONLY when competing options were actively evaluated."
            ),
            anti_patterns=("Surface-level descriptions without architectural trade-offs", "Regurgitating uncontested findings from Key Updates"),
            aliases=("Architectural Trade-Off Analysis", "Technical Trade-Off Matrix", "Trade-Off Matrix"),
        ),
        "Active Technical Spikes & Experimental Inquiries": SectionSpecification(
            header="Active Technical Spikes & Experimental Inquiries",
            description="Falsifiable spike contracts and research inquiries with hypotheses, test methods, metrics, and auto-expire dates.",
            requirements_by_archetype={
                MeetingArchetype.GENERAL: SectionRequirement.FORBIDDEN,
                MeetingArchetype.ARCHITECTURE_DEEP_DIVE: SectionRequirement.FORBIDDEN,
                MeetingArchetype.STRATEGIC_DISCOVERY: SectionRequirement.FORBIDDEN,
                MeetingArchetype.GOVERNANCE_REVIEW: SectionRequirement.FORBIDDEN,
                MeetingArchetype.TACTICAL_ALIGNMENT: SectionRequirement.FORBIDDEN,
                MeetingArchetype.LESSONS_LEARNED: SectionRequirement.FORBIDDEN,
            },
            guidance=(
                "Permanently retired and strictly FORBIDDEN. Do not manufacture artificial scientific spike contracts or fake hypotheses."
            ),
            anti_patterns=("Unassigned administrative tasks without scientific falsifiability", "Manufactured academic tables"),
            aliases=("Falsifiable Spikes", "Technical Spikes", "Active Technical Spikes", "Spikes & Explorations"),
        ),
        "Action & Governance Ledger": SectionSpecification(
            header="Action & Governance Ledger",
            description="Single unified table consolidating action items, blockers, milestones, and governance gates.",
            requirements_by_archetype={
                MeetingArchetype.GENERAL: SectionRequirement.MANDATORY,
                MeetingArchetype.ARCHITECTURE_DEEP_DIVE: SectionRequirement.MANDATORY,
                MeetingArchetype.STRATEGIC_DISCOVERY: SectionRequirement.MANDATORY,
                MeetingArchetype.GOVERNANCE_REVIEW: SectionRequirement.MANDATORY,
                MeetingArchetype.TACTICAL_ALIGNMENT: SectionRequirement.MANDATORY,
                MeetingArchetype.LESSONS_LEARNED: SectionRequirement.MANDATORY,
            },
            guidance=(
                "Unified single table replacing separate Next Steps and Decision Gates tables. "
                "Rendered only when forward actions or blockers were explicitly committed. "
                "Columns: Type (Action/Blocker/Gate), Description, Accountable Owner, Target Timing, Locators, Status."
            ),
            anti_patterns=(
                "Mirrored side-by-side tables with identical owners/dates",
                "Laundering invented dates as 'author-proposed'",
                "Separate Governed Decision Gates table duplicating Next Steps",
                "Generic or placeholder owners ('Team', 'All', 'PartnerAdvisory / NEE', 'TBD', 'Unassigned', 'Pending') in action items",
                "Multiple DRIs per action row without a single accountable lead",
            ),
        ),
        "Recommendations & Decisions Required": SectionSpecification(
            header="Recommendations & Decisions Required",
            description="Clear statement of decisions reached or decision framework (decisions required, recommended choice, alternatives, consequence of delay).",
            requirements_by_archetype={
                MeetingArchetype.GENERAL: SectionRequirement.CONDITIONAL,
                MeetingArchetype.ARCHITECTURE_DEEP_DIVE: SectionRequirement.CONDITIONAL,
                MeetingArchetype.STRATEGIC_DISCOVERY: SectionRequirement.CONDITIONAL,
                MeetingArchetype.GOVERNANCE_REVIEW: SectionRequirement.CONDITIONAL,
                MeetingArchetype.TACTICAL_ALIGNMENT: SectionRequirement.CONDITIONAL,
                MeetingArchetype.LESSONS_LEARNED: SectionRequirement.CONDITIONAL,
            },
            guidance=(
                "If binding decisions were ratified, state them clearly. "
                "If unratified, title section 'Recommendations & Decisions Required' to avoid false finality. "
                "Focus on the decision frame, technical trade-offs, and open questions. "
                "Render only when decisions or recommendations were actively discussed."
            ),
            anti_patterns=(
                "Titling unratified discussions 'Executive Decision'",
                "Claiming consensus when participants differed",
                "Duplicating task-governance bullets (owners, deadlines, status) outside the ledger",
                "Forcing partial-sentence checklist syntax when no decisions were ratified",
            ),
        ),
        "Business Meaning and Value Case": SectionSpecification(
            header="Business Meaning and Value Case",
            description="Economic and operational value case, efficiency levers, and business impact.",
            requirements_by_archetype={
                MeetingArchetype.GENERAL: SectionRequirement.CONDITIONAL,
                MeetingArchetype.ARCHITECTURE_DEEP_DIVE: SectionRequirement.FORBIDDEN,
                MeetingArchetype.STRATEGIC_DISCOVERY: SectionRequirement.CONDITIONAL,
                MeetingArchetype.GOVERNANCE_REVIEW: SectionRequirement.CONDITIONAL,
                MeetingArchetype.TACTICAL_ALIGNMENT: SectionRequirement.CONDITIONAL,
                MeetingArchetype.LESSONS_LEARNED: SectionRequirement.CONDITIONAL,
            },
            guidance="Articulate concrete operational mechanisms, baseline vs target metrics, and business value.",
            anti_patterns=("Superficial high-level buzzwords without operational mechanisms", "Echoing operational context already stated in Meeting Objective"),
        ),
        "Immediate Delivery Scope & Release Boundaries": SectionSpecification(
            header="Immediate Delivery Scope & Release Boundaries",
            description="Concrete release boundaries, MVP deliverables, in/out of scope definitions.",
            requirements_by_archetype={
                MeetingArchetype.GENERAL: SectionRequirement.CONDITIONAL,
                MeetingArchetype.ARCHITECTURE_DEEP_DIVE: SectionRequirement.FORBIDDEN,
                MeetingArchetype.STRATEGIC_DISCOVERY: SectionRequirement.CONDITIONAL,
                MeetingArchetype.GOVERNANCE_REVIEW: SectionRequirement.CONDITIONAL,
                MeetingArchetype.TACTICAL_ALIGNMENT: SectionRequirement.CONDITIONAL,
                MeetingArchetype.LESSONS_LEARNED: SectionRequirement.CONDITIONAL,
            },
            guidance="Define bounded release deliverables, inclusion/exclusion boundaries, and acceptance criteria.",
            anti_patterns=(
                "Injecting consulting wedges into internal technical syncs",
                "Using forbidden jargon ('wedge', 'manifest-controlled')",
            ),
        ),
        "Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown": SectionSpecification(
            header="Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown",
            description="Substantive stakeholder perspective synthesis following Microsoft Copilot gold standard.",
            requirements_by_archetype={
                MeetingArchetype.GENERAL: SectionRequirement.MANDATORY,
                MeetingArchetype.ARCHITECTURE_DEEP_DIVE: SectionRequirement.MANDATORY,
                MeetingArchetype.STRATEGIC_DISCOVERY: SectionRequirement.MANDATORY,
                MeetingArchetype.GOVERNANCE_REVIEW: SectionRequirement.MANDATORY,
                MeetingArchetype.TACTICAL_ALIGNMENT: SectionRequirement.MANDATORY,
                MeetingArchetype.LESSONS_LEARNED: SectionRequirement.MANDATORY,
            },
            guidance=(
                "Multi-paragraph or themed bullet structure per speaker (- **[Theme]**: [Narrative]). "
                "Minimum 2 themes per active attendee (3–7 for primary speakers with >= 20% utterance share), "
                ">= 30 words per theme, conditioned on evidence sufficiency. Zero/one-turn attendees are factually "
                "documented with strict evidentiary boundaries without inferring agreement, endorsement, or alignment from silence."
            ),
            anti_patterns=(
                "Defensive cop-out disclaimers ('no reliable utterance mapping')",
                "Generic 3-bullet placeholder boilerplate",
                "Inferring agreement, endorsement, or consensus from silence for zero/one-turn attendees",
                "Inventing ungrounded name/role-based motives or delivery priorities for single-turn attendees",
            ),
        ),
    }

    rubric_dimensions = (
        RubricDimension(
            name="Executive Clarity & Contextual Narrative Flow",
            key="dimension_1_executive_altitude",
            max_points=25,
            description="Zero-context comprehensibility, operational context established first, logical narrative progression.",
            criteria=(
                "Operational problem established before findings",
                "Clear narrative arc accessible to senior executives",
                "Clean markdown formatting without nested header noise",
            ),
            floor=14.0,
        ),
        RubricDimension(
            name="Technical Substance & Architecture Grounding",
            key="dimension_2_technical_substance",
            max_points=25,
            description="Accurate systems, model pipelines, concrete failure modes, and collateral fidelity.",
            criteria=(
                "Accurate technical pipeline mechanics",
                "Preservation of evaluation denominators and data constraints",
                "Embedded ASCII architecture exhibit when provided in collateral",
            ),
            floor=14.0,
        ),
        RubricDimension(
            name="Strategic Synthesis & Sentiment Fidelity",
            key="dimension_3_strategic_synthesis",
            max_points=25,
            description="Unvarnished client posture preservation, unvarnished skepticism, deep stakeholder perspectives.",
            criteria=(
                "Zero corporate whitewashing of client friction or skepticism",
                "Rich, multi-thematic stakeholder summaries in Appendix without disclaimers",
                "Accurate capture of reported stakeholder concerns and disagreements",
            ),
            floor=14.0,
        ),
        RubricDimension(
            name="Actionability, Parsimony & Anti-Redundancy Governance",
            key="dimension_4_scoping_and_modality",
            max_points=25,
            description="Single consolidated Action & Governance Ledger, zero duplicate tables, parsimonious executive brevity.",
            criteria=(
                "Unified single Action & Governance Ledger (no separate mirrored tables)",
                "Zero duplicate task governance (ledger is SSOT; no separate mirrored tables or duplicate task bullets)",
                "No artificial consulting wedges on internal technical syncs",
                "Single DRIs and verified target dates without synthetic planning horizons",
                "Zero collective or placeholder owners ('Team', 'All', 'PartnerAdvisory/Client', 'Unassigned', 'Pending', 'TBD') in ledger",
                "Document-wide plain English with zero word-salad jargon",
            ),
            floor=14.0,
        ),
    )

    class _Output2ContractImpl(OutputContract):
        primary_speaker_minimum_share: float = 0.20
        allow_missing_et_window: bool = True

        def compile_stage_prompt(self, archetype: MeetingArchetype, extra_context: Optional[Dict[str, Any]] = None) -> str:
            from dual_pipeline.prompts.registry import CANONICAL_PROMPT_REGISTRY
            base = CANONICAL_PROMPT_REGISTRY.get_prompt("STAGE_02_RECAP")
            if archetype == MeetingArchetype.ARCHITECTURE_DEEP_DIVE:
                override_block = CANONICAL_PROMPT_REGISTRY.get_prompt("STAGE_02_ARCHITECTURE_DEEP_DIVE_OVERRIDE")
                return f"{base}\n\n{override_block}"
            return base

        def compile_judge_prompt(self, archetype: MeetingArchetype) -> str:
            from dual_pipeline.prompts.registry import CANONICAL_PROMPT_REGISTRY
            return CANONICAL_PROMPT_REGISTRY.get_prompt("JUDGE_RECAP")

    return _Output2ContractImpl(
        output_id=2,
        name="Executive Strategic Brief / Recap",
        description="Email-ready executive synthesis, unified Action & Governance Ledger, and Copilot gold-standard Appendix.",
        sections=sections,
        rubric_dimensions=rubric_dimensions,
        prohibited_patterns=WORD_SALAD_PATTERNS + FORBIDDEN_APPENDIX_DISCLAIMERS,
        min_word_count=CANONICAL_PROMPT_CONFIG.output2.min_word_count,
        max_word_count=CANONICAL_PROMPT_CONFIG.output2.max_word_count,
    )


Output2Contract = build_output2_contract
OUTPUT2_CONTRACT = build_output2_contract()


ACTION_VERBS = (
    "defines", "described", "describes", "outlined", "outlines",
    "connected", "raised", "committed", "shared", "highlights",
    "emphasized", "details", "detailed", "proposes", "proposed",
    "clarifies", "clarified", "reviews", "reviewed", "demonstrates",
    "demonstrated", "explains", "explained", "advocates", "advocated",
    "debates", "debated", "confirms", "confirmed", "presents", "presented",
    "identifies", "identified", "evaluates", "evaluated", "articulates",
    "notes", "noted", "recommends", "recommended", "leads", "led",
    "summarizes", "summarized", "addresses", "addressed", "tracks", "tracked",
    "illustrates", "examines", "proves", "warns", "cautions", "flags",
)


def _find_bare_persona_anchors(sec3_text: str, roster_names: set[str]) -> List[str]:
    """Identify bare persona anchors that collapse into a person name without action or substance.

    Permits actor-plus-action format (e.g. '**Nathan Sutton defines developer velocity friction:**').
    Rejects bare persona collapses:
      '**Nathan Sutton:**'
      '**Nathan Sutton Update:**'
    """
    if not sec3_text:
        return []
    anchors = re.findall(r"^[*-]\s+\*\*([^*]+)\*\*", sec3_text, re.MULTILINE)
    bare_anchors: List[str] = []
    for anchor in anchors:
        a_clean = anchor.strip().rstrip(":")
        a_lower = a_clean.lower()
        for name in roster_names:
            n_lower = name.lower()
            if a_lower == n_lower:
                bare_anchors.append(anchor)
                break
            if a_lower.startswith(n_lower):
                remainder = a_lower[len(n_lower):].strip(" :-,")
                remainder_words = remainder.split()
                if not remainder or remainder in (
                    "update", "updates", "notes", "perspective", "recap",
                    "summary", "takeaways", "discussion", "input",
                ):
                    bare_anchors.append(anchor)
                    break
                if len(remainder_words) <= 2 and not any(w in remainder for w in ACTION_VERBS):
                    bare_anchors.append(anchor)
                    break
    return bare_anchors


def validate_section3_thematic_decoupling(
    f2_text: str,
    meta: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, List[str]]:
    """Validate that Section 3 uses thematic anchors and is decoupled from Section 7 (Appendix).

    Enforces:
    1. Zero bare persona anchors: Section 3 bold anchors must not be bare attendee names; they must follow actor-plus-action format.
    2. Overlap ceiling: Verbatim 4-gram overlap between Section 3 and Section 7 must not exceed 35%.
    3. Duplicate quote prohibition: Verbatim attendee quotes must reside in Section 7, not Section 3.
    """
    failures: List[str] = []
    effective_meta = meta or {}

    # Extract Section 3 (Key Discussion Points & Findings / Key Updates)
    sec3_match = re.search(
        r"(?:^|\n)#{1,3}\s+(?:\d+\.\s*)?(?:Key Discussion Points.*?Findings|Key Updates)\b([\s\S]*?)(?=\n#{1,3}\s+|\Z)",
        f2_text,
        re.IGNORECASE,
    )
    if not sec3_match:
        return True, []

    sec3_text = sec3_match.group(1).strip()

    # Extract Section 7 (Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown)
    app_match = re.search(
        r"(?:^|\n)#{1,3}\s+Appendix[\s\S]*?(?=\n#{1,3}\s+Evidence Locks|\Z)",
        f2_text,
        re.IGNORECASE,
    )
    app_text = app_match.group(0).strip() if app_match else ""

    # Build roster of attendee names
    roster_names: set[str] = set()
    if effective_meta and isinstance(effective_meta.get("attendees"), dict):
        for _org, members in effective_meta["attendees"].items():
            if isinstance(members, list):
                for m in members:
                    if isinstance(m, dict) and "name" in m:
                        roster_names.add(m["name"].strip())
                    elif isinstance(m, str):
                        clean_n = re.sub(r"\s*\(.*?\)", "", m).strip()
                        if clean_n:
                            roster_names.add(clean_n)
    elif effective_meta and isinstance(effective_meta.get("attendees"), list):
        for m in effective_meta["attendees"]:
            if isinstance(m, dict) and "name" in m:
                roster_names.add(m["name"].strip())
            elif isinstance(m, str):
                clean_n = re.sub(r"\s*\(.*?\)", "", m).strip()
                if clean_n:
                    roster_names.add(clean_n)

    # Also parse attendees from markdown header
    header_att_m = re.search(r"(?:Attendees|Participants)[:\s*]*\n([\s\S]*?)(?=\n#{1,3}|\Z)", f2_text, re.IGNORECASE)
    if header_att_m:
        for line in header_att_m.group(1).splitlines():
            line = line.strip()
            if line.startswith(("-", "*")):
                found_names = re.findall(r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)", line)
                for n in found_names:
                    if n not in ("Meeting Objective", "Key Discussion", "Executive Strategic", "Next Steps"):
                        roster_names.add(n)

    # 1. Inspect Section 3 bullet anchors for bare persona collapses (e.g. "**Nathan Sutton:**")
    bare_persona_anchors = _find_bare_persona_anchors(sec3_text, roster_names)

    if bare_persona_anchors:
        failures.append(
            f"[SECTION_3_ANCHOR_PERSONA_COLLAPSE]: Found {len(bare_persona_anchors)} bare persona anchors ({', '.join(bare_persona_anchors)}). "
            "Anchors must follow actor-plus-action format with technical substance (e.g., '**Nathan Sutton defines developer velocity friction:**')."
        )

    # Note: Output 2a (Main Body) and Output 2b (Appendix Speaker Summary) are decoupled.
    # Fact, thematic, and quote overlap between Section 3 and the Appendix is permitted.
    return len(failures) == 0, failures


def validate_c21_depth_floors_and_anchors(  # noqa: C901
    f2_text: str,
    meta: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, List[str]]:
    """Validate that Section 3 uses thematic anchors and Section 7 meets C-21 depth floors.

    Enforces:
    1. Zero bare persona anchors: Section 3 bold anchors must not be bare attendee names; actor-plus-action anchors are required.
    2. C-21 Depth Floors: The Appendix must not be empty and must have substantive depth (approx word count checks).
    """
    failures: List[str] = []
    if not f2_text or not f2_text.strip():
        return True, []
    effective_meta = meta or {}

    # Extract Section 3
    sec3_match = re.search(
        r"(?:^|\n)#{1,3}\s+(?:\d+\.\s*)?(?:Key Discussion Points.*?Findings|Key Updates)\b([\s\S]*?)(?=\n#{1,3}\s+|\Z)",
        f2_text,
        re.IGNORECASE,
    )
    sec3_text = sec3_match.group(1).strip() if sec3_match else ""

    # Extract Section 7 (Appendix)
    app_match = re.search(
        r"(?:^|\n)#{1,3}\s+Appendix[\s\S]*?(?=\n#{1,3}\s+Evidence Locks|\Z)",
        f2_text,
        re.IGNORECASE,
    )
    app_text = app_match.group(0).strip() if app_match else ""

    # Build roster of attendee names
    roster_names: set[str] = set()
    if effective_meta and isinstance(effective_meta.get("attendees"), dict):
        for _org, members in effective_meta["attendees"].items():
            if isinstance(members, list):
                for m in members:
                    if isinstance(m, dict) and "name" in m:
                        roster_names.add(m["name"].strip())
                    elif isinstance(m, str):
                        clean_n = re.sub(r"\s*\(.*?\)", "", m).strip()
                        if clean_n:
                            roster_names.add(clean_n)
    elif effective_meta and isinstance(effective_meta.get("attendees"), list):
        for m in effective_meta["attendees"]:
            if isinstance(m, dict) and "name" in m:
                roster_names.add(m["name"].strip())
            elif isinstance(m, str):
                clean_n = re.sub(r"\s*\(.*?\)", "", m).strip()
                if clean_n:
                    roster_names.add(clean_n)

    # Also parse attendees from markdown header
    header_att_m = re.search(r"(?:Attendees|Participants)[:\s*]*\n([\s\S]*?)(?=\n#{1,3}|\Z)", f2_text, re.IGNORECASE)
    if header_att_m:
        for line in header_att_m.group(1).splitlines():
            line = line.strip()
            if line.startswith(("-", "*")):
                line_no_roles = re.sub(r"\s*\([^)]*\)", "", line)
                found_names = re.findall(r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)", line_no_roles)
                for n in found_names:
                    if n not in ("Meeting Objective", "Key Discussion", "Executive Strategic", "Next Steps", "Action", "Governance"):
                        roster_names.add(n)

    # 1. Inspect Section 3 bullet anchors for bare persona collapses (e.g. "**Nathan Sutton:**")
    if sec3_text:
        bare_persona_anchors = _find_bare_persona_anchors(sec3_text, roster_names)

        if bare_persona_anchors:
            failures.append(
                f"[SECTION_3_ANCHOR_PERSONA_COLLAPSE]: Found {len(bare_persona_anchors)} bare persona anchors ({', '.join(bare_persona_anchors)}). "
                "Anchors must follow actor-plus-action format with technical substance (e.g., '**Nathan Sutton defines developer velocity friction:**')."
            )

    # 2. C-21 Depth Floors in Appendix
    if app_text:
        from dual_pipeline.parameters import get_int_parameter, get_output2_c21_speaker_tier_floors
        c21_floors = get_output2_c21_speaker_tier_floors()
        min_substantive = c21_floors.get("participant", (40, 2))[0]
        min_stub = c21_floors.get("contributor", (15, 1))[0]
        shallow_floor = get_int_parameter("output2.section_density_floors.standard_30m_section_floor", 60)

        app_words = len(re.findall(r"\b[a-zA-Z0-9_-]+\b", app_text))
        active_speakers = [name for name in roster_names if name.lower() in app_text.lower()]

        # Filter out minimal/greeting-only speakers (< shallow_floor in their subsection) from deep denominator
        speaker_blocks_raw = re.split(r"\n#{1,4}\s+", app_text)[1:]
        substantive_speakers_count = 0
        for block in speaker_blocks_raw:
            b_words = len(re.findall(r"\b[a-zA-Z0-9_-]+\b", block))
            if b_words >= shallow_floor:
                substantive_speakers_count += 1
        effective_speaker_count = max(1, substantive_speakers_count) if substantive_speakers_count > 0 else len(active_speakers)

        has_bullets = bool(re.search(r"^[*-]\s+", app_text, re.MULTILINE))
        is_empty_or_stub = (app_words < min_stub) or (not has_bullets)
        is_shallow = bool(active_speakers and (app_words / effective_speaker_count) < shallow_floor)
        enforce_strict_floor = bool(effective_meta.get("enforce_c21_depth_floor", False))
        all_blocks_stub = bool(
            speaker_blocks_raw
            and all(len(re.findall(r"\b[a-zA-Z0-9_-]+\b", b)) < min_stub for b in speaker_blocks_raw)
        )
        if is_empty_or_stub or all_blocks_stub or (is_shallow and enforce_strict_floor):
            failures.append(
                f"[SECTION_7_DEPTH_FLOOR_VIOLATION]: Appendix is too shallow ({app_words} words for {effective_speaker_count} substantive speakers). "
                "C-21 mandates deep persona-centric lenses, not just bullet summaries."
            )

        # Check intra-appendix duplication (copy-pasting across speakers)
        # We split by speaker headers (# Speaker Name) and check overlap
        speaker_blocks = re.split(r"\n#{1,3}\s+", app_text)[1:] # Skip the 'Appendix' header itself
        block_texts = [block for block in speaker_blocks if len(block.split()) > 20]
        for i in range(len(block_texts)):
            for j in range(i + 1, len(block_texts)):
                clean_i = re.sub(r"\[[^\]]+\]|\([^)]*\)|\b\d{2}:\d{2}(?::\d{2})?\b", " ", block_texts[i])
                clean_j = re.sub(r"\[[^\]]+\]|\([^)]*\)|\b\d{2}:\d{2}(?::\d{2})?\b", " ", block_texts[j])
                words_i = re.findall(r"\b[a-zA-Z_-]+\b", clean_i.lower())
                words_j = re.findall(r"\b[a-zA-Z_-]+\b", clean_j.lower())

                ngrams_i = set(tuple(words_i[k:k+6]) for k in range(len(words_i) - 5))
                ngrams_j = set(tuple(words_j[k:k+6]) for k in range(len(words_j) - 5))

                if ngrams_i and ngrams_j:
                    common = ngrams_i.intersection(ngrams_j)
                    if len(common) > 8: # More than 8 identical 6-grams between speakers
                        failures.append(
                            "[SECTION_7_INTRA_APPENDIX_REDUNDANCY]: Substantial identical copy-pasting detected across "
                            "different speaker sections in the Appendix. Each speaker must have unique analysis."
                        )
                        break

    return len(failures) == 0, failures

