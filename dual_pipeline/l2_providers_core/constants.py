"""Constants, output schemas, and digests for external L2 reasoning providers."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Mapping

from dual_pipeline.l2_output_contract import output_contract_digest
from dual_pipeline.l2_policy import QUERY_TYPE_POLICY_MATRIX

_CITATION_ROW = {
    "type": "object",
    "additionalProperties": False,
    "required": ["statement", "citations", "evidence_ids", "lane"],
    "properties": {
        "statement": {"type": "string", "minLength": 1},
        "citations": {"type": "array", "minItems": 1, "items": {"type": "string"}},
        "evidence_ids": {"type": "array", "minItems": 1, "items": {"type": "string"}},
        "lane": {"type": "string", "enum": ["approved", "candidate", "external_public"]},
    },
}


DECISION_MODEL_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "query_type",
        "current_state",
        "patterns",
        "pain_points",
        "opportunities",
        "guidance",
        "hypotheses",
        "source_evidence",
        "next_best_actions",
        "rationale",
        "counterevidence",
        "missing_information",
        "assumptions",
        "what_would_change_the_conclusion",
        "proposed_assertions",
        "source_evidence",
        "automatic_graph_mutation_count",
    ],
    "properties": {
        "schema_version": {"type": "string", "const": "nee-governed-answer-1.0.0"},
        "query_type": {"type": "string", "enum": list(QUERY_TYPE_POLICY_MATRIX)},
        "current_state": {
            "type": "array",
            "maxItems": 6,
            "items": _CITATION_ROW,
        },
        "patterns": {
            "type": "array",
            "maxItems": 6,
            "items": {"type": "string", "minLength": 1},
        },
        "pain_points": {
            "type": "array",
            "maxItems": 4,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["pain_point", "citations", "evidence_ids", "lane"],
                "properties": {
                    "pain_point": {"type": "string", "minLength": 1},
                    "citations": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                    "evidence_ids": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                    "lane": {"type": "string", "const": "approved"},
                },
            },
        },
        "opportunities": {
            "type": "array",
            "maxItems": 4,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "opportunity",
                    "why_prioritize",
                    "addresses",
                    "citations",
                    "evidence_ids",
                    "lane",
                ],
                "properties": {
                    "opportunity": {"type": "string", "minLength": 1},
                    "why_prioritize": {"type": "string", "minLength": 1},
                    "addresses": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "minLength": 1},
                    },
                    "citations": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                    "evidence_ids": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                    "lane": {
                        "type": "string",
                        "enum": ["approved", "external_public"],
                    },
                },
            },
        },
        "guidance": {
            "type": "array",
            "maxItems": 4,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["label", "guidance", "citations", "evidence_ids", "lane"],
                "properties": {
                    "label": {"type": "string", "const": "GUIDANCE_NOT_FACT"},
                    "guidance": {"type": "string", "minLength": 1},
                    "citations": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                    "evidence_ids": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                    "lane": {"type": "string", "const": "approved"},
                },
            },
        },
        "hypotheses": {
            "type": "array",
            "maxItems": 4,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["hypothesis", "citations", "evidence_ids", "lane"],
                "properties": {
                    "hypothesis": {
                        "type": "string",
                        "minLength": 1,
                        "pattern": "^CANDIDATE:",
                    },
                    "citations": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                    "evidence_ids": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                    "lane": {"type": "string", "const": "candidate"},
                },
            },
        },
        "source_evidence": {
            "type": "array",
            "maxItems": 4,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["label", "excerpt", "citations", "evidence_ids", "lane"],
                "properties": {
                    "label": {"type": "string", "const": "CANDIDATE_SOURCE_EVIDENCE"},
                    "excerpt": {"type": "string", "minLength": 1},
                    "citations": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                    "evidence_ids": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                    "lane": {"type": "string", "const": "candidate"},
                },
            },
        },
        "next_best_actions": {
            "type": "array",
            "maxItems": 4,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["action", "basis_citations"],
                "properties": {
                    "action": {"type": "string", "minLength": 1},
                    "basis_citations": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string"},
                    },
                },
            },
        },
        "rationale": {"type": "string", "maxLength": 4000},
        "counterevidence": {
            "type": "array",
            "maxItems": 4,
            "items": _CITATION_ROW,
        },
        "missing_information": {
            "type": "array",
            "maxItems": 6,
            "items": {"type": "string", "minLength": 1},
        },
        "assumptions": {
            "type": "array",
            "maxItems": 4,
            "items": {"type": "string"},
        },
        "what_would_change_the_conclusion": {
            "type": "array",
            "maxItems": 4,
            "items": {"type": "string"},
        },
        "proposed_assertions": {
            "type": "array",
            "maxItems": 4,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["proposal", "citations", "evidence_ids", "lane", "proposal_only"],
                "properties": {
                    "proposal": {
                        "type": "string",
                        "minLength": 1,
                        "pattern": "^CANDIDATE:",
                    },
                    "citations": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                    "evidence_ids": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                    "lane": {"type": "string", "const": "candidate"},
                    "proposal_only": {"type": "boolean", "const": True},
                },
            },
        },
        "automatic_graph_mutation_count": {"type": "integer", "const": 0},
    },
}

DECISION_OUTPUT_CONTRACT_DIGEST = output_contract_digest(
    DECISION_MODEL_OUTPUT_SCHEMA
)

_REPAIR_ROW_VALUE_FIELDS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "current_state": ("statement", "citations", "evidence_ids"),
        "pain_points": ("pain_point", "citations", "evidence_ids"),
        "opportunities": (
            "opportunity",
            "why_prioritize",
            "addresses",
            "citations",
            "evidence_ids",
        ),
        "guidance": ("guidance", "citations", "evidence_ids"),
        "hypotheses": ("hypothesis", "citations", "evidence_ids"),
        "source_evidence": ("excerpt", "citations", "evidence_ids"),
        "counterevidence": ("statement", "citations", "evidence_ids"),
        "proposed_assertions": ("proposal", "citations", "evidence_ids"),
        "next_best_actions": ("action", "basis_citations"),
    }
)

_PROMPT_SLOT_NAMES = frozenset(
    {"S0", "D0", "I0", "C0A", "C0C", "C0W", "C0M", "G0", "U0", "R0"}
)
