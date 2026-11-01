"""
dual_pipeline/prompts/contracts/output_contract.py

Core declarative models for Unified Output Contracts (SSOT).
Defines typed specifications for MeetingArchetype, SectionRequirement,
SectionSpecification, RubricDimension, and OutputContract.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Sequence
from xml.sax.saxutils import escape as xml_escape


class MeetingArchetype(str, Enum):
    """Governed meeting archetypes — canonical 5-value registry per Rubric V1 §1.3.

    These are the ONLY permitted meeting classifications across rubric, judge,
    generation prompt, and QA gates. No component may maintain an independent list.
    """
    # 5 canonical rubric values (SSOT per TRANSCRIPT_SYNTHESIS_EVALUATION_RUBRIC_V1.md §1.3)
    STRATEGIC_DISCOVERY = "STRATEGIC_DISCOVERY"
    ARCHITECTURE_DEEP_DIVE = "ARCHITECTURE_DEEP_DIVE"
    TACTICAL_ALIGNMENT = "TACTICAL_ALIGNMENT"
    GOVERNANCE_REVIEW = "GOVERNANCE_REVIEW"
    LESSONS_LEARNED = "LESSONS_LEARNED"
    # Safe fallback only — never used in scoring or judge prompts
    GENERAL = "GENERAL"

    @classmethod
    def from_string(cls, val: Optional[str]) -> "MeetingArchetype":
        """Resolve case-insensitively. Legacy strings mapped to canonical equivalents."""
        if not val:
            return cls.GENERAL
        normalized = str(val).strip().upper().replace(" ", "_").replace("-", "_")
        # Exact match (canonical values)
        for member in cls:
            if member.value == normalized:
                return member
        # Legacy -> canonical heuristic mappings
        # INTERNAL_TECHNICAL_SYNC / TECHNICAL_DEMONSTRATION / ARCHITECTURE variants
        if any(x in normalized for x in ("TECH", "ENG", "DEEP_DIVE", "ARCH", "DEMO", "INTERNAL")):
            return cls.ARCHITECTURE_DEEP_DIVE
        # TACTICAL_ALIGNMENT / OPERATIONAL_DISCOVERY / RELEASE variants
        # Check OPERATIONAL before DISCOV to prevent OPERATIONAL_DISCOVERY -> STRATEGIC_DISCOVERY
        if any(x in normalized for x in ("TACTICAL", "ALIGN", "OPERATIONAL", "RELEASE", "DEPLOY", "SPRINT")):
            return cls.TACTICAL_ALIGNMENT
        # GOVERNANCE_REVIEW / EXECUTIVE_DECISION_GATE variants
        # Check before STRATEGIC so EXECUTIVE_DECISION_GATE hits GATE/DECISION before EXECUTIVE
        if any(x in normalized for x in ("GOVERN", "DECISION", "GATE", "STEER", "REVIEW", "RATIF")):
            return cls.GOVERNANCE_REVIEW
        # STRATEGIC_DISCOVERY / STRATEGIC_EXECUTIVE variants
        if any(x in normalized for x in ("STRATEG", "DISCOV", "EXECUTIVE")):
            return cls.STRATEGIC_DISCOVERY
        # LESSONS_LEARNED / INCIDENT_POSTMORTEM variants
        if any(x in normalized for x in ("LESSON", "POSTMORTEM", "INCIDENT", "RETRO")):
            return cls.LESSONS_LEARNED
        return cls.GENERAL


class SectionRequirement(str, Enum):
    """Enforcement status of a section for a given meeting archetype."""
    MANDATORY = "MANDATORY"
    CONDITIONAL = "CONDITIONAL"
    OPTIONAL = "OPTIONAL"
    FORBIDDEN = "FORBIDDEN"


@dataclass(frozen=True)
class RubricDimension:
    """Evaluation dimension in an LLM Judge rubric."""
    name: str
    key: str
    max_points: int
    description: str
    criteria: Sequence[str] = field(default_factory=tuple)
    applicable_archetypes: Optional[Sequence[MeetingArchetype]] = None
    is_conditional: bool = False
    floor: Optional[float] = None

    def is_applicable_for(self, archetype: Optional[MeetingArchetype]) -> bool:
        if "dimension_2" in self.key or "knowledge_layer" in self.key or "attribution" in self.key or "technical_substance" in self.key:
            return True  # Dim 2 can never be omitted across any archetype
        if self.applicable_archetypes is None or archetype is None:
            return True
        return archetype in self.applicable_archetypes


@dataclass(frozen=True)
class SectionSpecification:
    """Structural definition of a deliverable section."""
    header: str
    description: str
    requirements_by_archetype: Mapping[MeetingArchetype, SectionRequirement]
    guidance: str = ""
    anti_patterns: Sequence[str] = field(default_factory=tuple)
    aliases: Sequence[str] = field(default_factory=tuple)

    def get_requirement(self, archetype: MeetingArchetype) -> SectionRequirement:
        """Resolve requirement level for the given archetype with GENERAL fallback."""
        return self.requirements_by_archetype.get(
            archetype,
            self.requirements_by_archetype.get(MeetingArchetype.GENERAL, SectionRequirement.OPTIONAL),
        )


from dual_pipeline.prompts.config import CANONICAL_PROMPT_CONFIG, EnginePromptConfigSSOT, Output2Config
from dual_pipeline.parameters import get_output2_section_score_specs
from dual_pipeline.parameters_ops import ReleaseGatesConfig

if not hasattr(EnginePromptConfigSSOT, "release_gates"):
    EnginePromptConfigSSOT.release_gates = property(lambda self: ReleaseGatesConfig())  # type: ignore[attr-defined]
if not hasattr(Output2Config, "section_specs"):
    Output2Config.section_specs = property(lambda self: get_output2_section_score_specs())  # type: ignore[attr-defined]
if not hasattr(Output2Config, "section_weights"):
    Output2Config.section_weights = property(lambda self: {k: mx for k, mx, _ in get_output2_section_score_specs()})  # type: ignore[attr-defined]
if not hasattr(Output2Config, "section_floors"):
    Output2Config.section_floors = property(lambda self: {k: flr for k, _, flr in get_output2_section_score_specs()})  # type: ignore[attr-defined]


@dataclass
class OutputContract:
    """Declarative SSOT contract governing generation, QA gating, and judge evaluation."""
    output_id: int
    name: str
    description: str
    sections: Dict[str, SectionSpecification]
    rubric_dimensions: Sequence[RubricDimension]
    prohibited_patterns: Sequence[str] = field(default_factory=tuple)
    min_word_count: int = field(default_factory=lambda: CANONICAL_PROMPT_CONFIG.output2.min_word_count)
    max_word_count: Optional[int] = field(default_factory=lambda: CANONICAL_PROMPT_CONFIG.output2.max_word_count)

    def get_dimension_floors(self, pipeline_mode: Optional[str] = None) -> Dict[str, float]:
        """Return mapping of dimension key to floor threshold for dimensions defining a floor.

        In DOCUMENT_MODE on Output 03, dimension_2_knowledge_layer_density has no mandatory
        entity-volume floor because narrative/section knowledge density belongs to Output 02,
        whereas Output 03 extracts high-signal graph mutations within the Gate 3.10 budget.
        """
        is_doc_mode = is_document_pipeline_mode(pipeline_mode)
        return {
            dim.key: float(dim.floor)
            for dim in self.rubric_dimensions
            if dim.floor is not None
            and not (is_doc_mode and self.output_id == 3 and dim.key == "dimension_2_knowledge_layer_density")
        }

    def get_section_topology(self, archetype: MeetingArchetype) -> Dict[str, SectionRequirement]:
        """Return resolved section requirements for an archetype."""
        return {
            sec_name: spec.get_requirement(archetype)
            for sec_name, spec in self.sections.items()
        }

    def is_section_forbidden(self, section_name: str, archetype: MeetingArchetype) -> bool:
        """True if the section is strictly prohibited for this meeting archetype."""
        spec = self.sections.get(section_name)
        if not spec:
            return False
        return spec.get_requirement(archetype) == SectionRequirement.FORBIDDEN

    def is_section_mandatory(self, section_name: str, archetype: MeetingArchetype) -> bool:
        """True if the section is unconditionally required for this meeting archetype."""
        spec = self.sections.get(section_name)
        if not spec:
            return False
        return spec.get_requirement(archetype) == SectionRequirement.MANDATORY

    @staticmethod
    def detect_archetype(title: str = "", text: str = "") -> MeetingArchetype:
        """Detect meeting archetype from title, headers, and text content."""
        clean_title = str(title or "")
        clean_text = str(text or "")
        combined = f"{clean_title}\n{clean_text[:2000]}".lower()
        # Explicit archetype tags in text/headers
        arch_match = re.search(r"\b(?:meeting_)?archetype:\s*([A-Za-z_-]+)", combined)
        if arch_match:
            return MeetingArchetype.from_string(arch_match.group(1))

        # Canonical rubric archetype inference (per TRANSCRIPT_SYNTHESIS_EVALUATION_RUBRIC_V1.md §1.3)
        # ARCHITECTURE_DEEP_DIVE: technical system reviews, deep dives, architecture sessions
        if (
            re.search(r"\b(tech(?:nical)?|architect(?:ure|ural)?|engineer(?:ing)?)\b.*?\b(sync|review|deep[\s_-]?dive|standup|meeting|status|session|alignment)\b", combined)
            or any(k in combined for k in [
                "internal tech", "lens tech", "lens deep dive", "internal team sync",
                "lens_status", "architectural trade-off", "architectural trade off",
                "pipeline review", "technical sync", "architecture sync", "technical review",
                "architecture review", "technical deep dive", "architecture deep dive",
            ])
            or "architectural trade-off matrix" in combined
            or "# architectural trade-off" in combined
        ):
            return MeetingArchetype.ARCHITECTURE_DEEP_DIVE
        # GOVERNANCE_REVIEW: decision gates, steering, ratification
        if any(k in combined for k in ["decision gate", "steering committee", "ratification", "executive decision", "governance"]):
            return MeetingArchetype.GOVERNANCE_REVIEW
        # STRATEGIC_DISCOVERY: discovery, working sessions, interviews
        if any(k in combined for k in ["discovery", "working session", "interview", "assessment"]):
            return MeetingArchetype.STRATEGIC_DISCOVERY
        # TACTICAL_ALIGNMENT: release planning, sprint, deployment
        if any(k in combined for k in [
            "release planning", "deployment plan", "sprint planning", "go-live", "tactical",
            "standup", "daily standup", "sprint", "scrum", "backlog", "status sync",
        ]):
            return MeetingArchetype.TACTICAL_ALIGNMENT
        # LESSONS_LEARNED: incidents, postmortems, retrospectives
        if any(k in combined for k in ["incident", "postmortem", "outage", "rca", "retrospective", "lessons"]):
            return MeetingArchetype.LESSONS_LEARNED
        return MeetingArchetype.from_string(title)

    def compile_governance_context(
        self,
        archetype: MeetingArchetype,
        extra_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Compile structured XML governance context envelope for prompt/judge parameter binding."""
        topo = self.get_section_topology(archetype)
        mandatory = [sec for sec, req in topo.items() if req == SectionRequirement.MANDATORY]
        forbidden = [sec for sec, req in topo.items() if req == SectionRequirement.FORBIDDEN]
        conditional = [sec for sec, req in topo.items() if req == SectionRequirement.CONDITIONAL]

        lines = [
            "<governance_context>",
            f"  <output_id>{self.output_id}</output_id>",
            f"  <output_name>{xml_escape(self.name)}</output_name>",
            f"  <meeting_archetype>{xml_escape(archetype.value)}</meeting_archetype>",
        ]
        if mandatory:
            lines.append("  <mandatory_sections>")
            for s in mandatory:
                lines.append(f"    <section>{xml_escape(s)}</section>")
            lines.append("  </mandatory_sections>")
        if forbidden:
            lines.append("  <forbidden_sections>")
            for s in forbidden:
                lines.append(f"    <section>{xml_escape(s)}</section>")
            lines.append("  </forbidden_sections>")
        if conditional:
            lines.append("  <conditional_sections>")
            for s in conditional:
                lines.append(f"    <section>{xml_escape(s)}</section>")
            lines.append("  </conditional_sections>")
        if self.prohibited_patterns:
            lines.append("  <prohibited_patterns>")
            for p in self.prohibited_patterns[:15]:
                lines.append(f"    <pattern>{xml_escape(p)}</pattern>")
            lines.append("  </prohibited_patterns>")
        if extra_context:
            for k, v in extra_context.items():
                if isinstance(v, (str, int, float, bool)):
                    lines.append(f"  <{k}>{xml_escape(str(v))}</{k}>")
        lines.append("</governance_context>")
        return "\n".join(lines)

    def compile_stage_prompt(self, archetype: MeetingArchetype, extra_context: Optional[Dict[str, Any]] = None) -> str:
        """Compile stage generation prompt from contract specifications."""
        raise NotImplementedError("Subclasses must implement compile_stage_prompt")

    def compile_judge_prompt(self, archetype: MeetingArchetype) -> str:
        """Compile LLM judge prompt rubric from contract specifications."""
        raise NotImplementedError("Subclasses must implement compile_judge_prompt")


def get_output_contract(output_id: int) -> Optional[OutputContract]:
    """Retrieve canonical OutputContract for given output ID (1, 2, or 3)."""
    if output_id == 1:
        from .output1_contract import OUTPUT1_CONTRACT
        return OUTPUT1_CONTRACT
    elif output_id == 2:
        from .output2_contract import OUTPUT2_CONTRACT
        return OUTPUT2_CONTRACT
    elif output_id == 3:
        from .output3_contract import OUTPUT3_CONTRACT
        return OUTPUT3_CONTRACT
    return None


def is_document_pipeline_mode(mode_or_archetype: Optional[Any]) -> bool:
    """Return True if the provided mode, modality, or archetype denotes document ingestion."""
    if not mode_or_archetype:
        return False
    norm = str(mode_or_archetype).strip().upper()
    return norm in ("DOCUMENT", "DOCUMENT_MODE", "DOC") or norm.startswith("DOC_")


def get_dimension_floors_for_output(output_id: int, pipeline_mode: Optional[str] = None) -> Dict[str, float]:
    """Retrieve dimension floors mapping for given output ID."""
    contract = get_output_contract(output_id)
    return contract.get_dimension_floors(pipeline_mode=pipeline_mode) if contract else {}


