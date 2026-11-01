"""
dual_pipeline/strategic_reasoning_core/constants.py

Strategic-reasoning constants, roles, scopes, modalities, and channel mappings.
"""
from __future__ import annotations

STRATEGIC_PROFILE_QUALIFIER = "com.partner_advisory.nee.strategic-profile"

KNOWLEDGE_ROLES = frozenset(
    {
        "current_state_fact",
        "official_intent",
        "business_problem",
        "design_principle",
        "target_outcome",
        "capability",
        "option",
        "evaluation_criterion",
        "constraint",
        "risk",
        "dependency",
        "external_pattern",
        "decision",
        "observed_outcome",
        "metric",
        "source_position",
        "assumption",
        "open_question",
    }
)
MODALITIES = frozenset(
    {"descriptive", "normative", "intended", "proposed", "predictive", "observed"}
)
AUTHORITY_SCOPES = frozenset(
    {
        "nee_current_state",
        "official_project_intent",
        "source_position",
        "external_reference",
        "stakeholder_perspective",
    }
)
APPLICABILITY_SCOPES = frozenset(
    {"nee_specific", "client_specific", "generalizable", "analogous_only"}
)
REALIZATION_STATES = frozenset(
    {"not_applicable", "intended", "planned", "in_progress", "observed", "measured"}
)
STRATEGIC_ANSWER_USES = frozenset(
    {"strategic_context", "decision_constraint", "external_analogy"}
)
PROFILE_FIELDS = (
    "knowledge_role",
    "modality",
    "authority_scope",
    "applicability_scope",
    "realization_state",
)
STRATEGIC_CHANNELS = (
    "objectives",
    "current_state",
    "diagnostics",
    "principles",
    "options",
    "internal_context",
    "external_patterns",
    "outcomes",
    "counterevidence",
    "gaps",
)

_CHANNEL_BY_ROLE = {
    "official_intent": "objectives",
    "business_problem": "objectives",
    "target_outcome": "objectives",
    "current_state_fact": "current_state",
    "capability": "current_state",
    "decision": "current_state",
    "risk": "diagnostics",
    "dependency": "diagnostics",
    "open_question": "gaps",
    "design_principle": "principles",
    "evaluation_criterion": "principles",
    "constraint": "principles",
    "option": "options",
    "source_position": "internal_context",
    "external_pattern": "external_patterns",
    "observed_outcome": "outcomes",
    "metric": "outcomes",
    "assumption": "gaps",
}
