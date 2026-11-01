"""
dual_pipeline.facets_core

Modularized graph facet schema installation, domain assignment, topic extraction,
and coverage audits.
"""
from __future__ import annotations

from .constants import (
    CARDS_REFACTOR_MIGRATION_ID,
    CARDS_REFACTOR_POLICY_VERSION,
    FACET_RULE_VERSION,
    FACET_SCHEMA_SQL,
    SEMANTIC_DOMAIN_BY_NODE_TYPE,
    SEMANTIC_DOMAIN_RULE_ID,
    SEMANTIC_DOMAIN_VOCABULARY,
    SEMANTIC_DOMAINS,
    TOPIC_ROUTE_RULE_ID,
    TOPIC_VOCABULARY,
)
from .coverage import (
    _facet_coverage,
    audit_cards_refactor_coverage,
    audit_semantic_domain_coverage,
    normalize_audiences,
    open_graph,
    selector_digest,
)
from .domains import (
    _assignment_payload,
    build_semantic_domain_operations,
    semantic_domain_for_node_type,
)
from .schema import (
    _EXPECTED_COLUMNS,
    _EXPECTED_INDEXES,
    facet_schema_checksum,
    install_facet_schema,
    schema_is_installed,
    validate_facet_schema,
)
from .topics import (
    build_topic_operations,
    facets_for_object,
)
