"""Constants and policies for governed graph retrieval."""
from __future__ import annotations

from typing import Any

from ..core import digest

GRAPH_RETRIEVAL_POLICY_BODY: dict[str, Any] = {
    "policy_id": "typed-connected-evidence-v1",
    "policy_version": "1.0.0",
    "maximum_hops": 3,
    "maximum_seed_records": 20,
    "maximum_expanded_records": 24,
    "maximum_node_fanout": 12,
    "maximum_records_per_bridge_node": 3,
    "hub_degree_threshold": 12,
    "excluded_relationship_families": ["ROUTES_TO"],
    "excluded_node_types": ["card"],
    "terminal_node_types": ["source"],
}
GRAPH_RETRIEVAL_POLICY = {
    **GRAPH_RETRIEVAL_POLICY_BODY,
    "policy_digest": digest(GRAPH_RETRIEVAL_POLICY_BODY),
}

_QUERY_STOPWORDS = frozenset(
    {
        "about",
        "across",
        "am",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "do",
        "for",
        "from",
        "go",
        "he",
        "how",
        "if",
        "in",
        "into",
        "is",
        "it",
        "me",
        "my",
        "no",
        "of",
        "on",
        "or",
        "so",
        "the",
        "their",
        "this",
        "to",
        "up",
        "us",
        "we",
        "what",
        "which",
        "with",
    }
)
_BM25_STOPWORDS = frozenset(
    {
        "a",
        "am",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "do",
        "for",
        "go",
        "he",
        "how",
        "if",
        "in",
        "is",
        "it",
        "me",
        "my",
        "no",
        "of",
        "on",
        "or",
        "so",
        "the",
        "they",
        "to",
        "up",
        "us",
        "we",
        "what",
        "which",
        "why",
        "with",
    }
)
_MATERIALITY_BOOST = {
    "critical": 0.10,
    "high": 0.08,
    "medium-high": 0.06,
    "medium": 0.04,
    "low": 0.01,
}
_EXPANSION_LIMITS = {
    "factual": {"approved": 24, "candidate": 0, "external_public": 0},
    "guidance": {"approved": 24, "candidate": 0, "external_public": 0},
    "strategic": {"approved": 16, "candidate": 6, "external_public": 2},
    "candidate_review": {"approved": 14, "candidate": 10, "external_public": 0},
    "candidate_evaluation": {"approved": 0, "candidate": 24, "external_public": 0},
    "candidate_guidance": {"approved": 0, "candidate": 24, "external_public": 0},
}

# Split-identity and literal entity-mention synthesis relationships
SPLIT_IDENTITY_RELATIONSHIP = "SAME_ENTITY_AS"
LITERAL_MENTION_RELATIONSHIP = "MENTIONS_ENTITY"
BRIDGE_RELATIONSHIP_FAMILIES = frozenset(
    {
        "ROUTES_TO",
        SPLIT_IDENTITY_RELATIONSHIP,
        LITERAL_MENTION_RELATIONSHIP,
    }
)
from dual_pipeline.parameters import get_graph_retrieval_parameters  # noqa: lazy-decouple

_GRAPH_RETRIEVAL_PARAMS = get_graph_retrieval_parameters()
DEFAULT_CONFIDENCE_SCORE: float = float(_GRAPH_RETRIEVAL_PARAMS["default_confidence_score"])
TEMPORAL_DECAY_LAMBDA: float = float(_GRAPH_RETRIEVAL_PARAMS["temporal_decay_lambda"])
MAX_TEMPORAL_BOOST: float = float(_GRAPH_RETRIEVAL_PARAMS["max_temporal_boost"])
MAX_CONFIDENCE_BOOST: float = float(_GRAPH_RETRIEVAL_PARAMS["max_confidence_boost"])
