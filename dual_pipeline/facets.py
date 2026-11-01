#!/usr/bin/env python3
"""Governed semantic facets for graph objects.

Backward-compatibility facade delegating to `dual_pipeline.facets_core`.
Preserves 100% symbol parity, schema operations, and facet extraction rules.
"""
from __future__ import annotations

from .facets_core import (
    _EXPECTED_COLUMNS,
    _EXPECTED_INDEXES,
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
    _assignment_payload,
    _facet_coverage,
    audit_cards_refactor_coverage,
    audit_semantic_domain_coverage,
    build_semantic_domain_operations,
    build_topic_operations,
    facet_schema_checksum,
    facets_for_object,
    install_facet_schema,
    normalize_audiences,
    open_graph,
    schema_is_installed,
    selector_digest,
    semantic_domain_for_node_type,
    validate_facet_schema,
)

__all__ = [
    "CARDS_REFACTOR_MIGRATION_ID",
    "CARDS_REFACTOR_POLICY_VERSION",
    "FACET_RULE_VERSION",
    "FACET_SCHEMA_SQL",
    "SEMANTIC_DOMAINS",
    "SEMANTIC_DOMAIN_BY_NODE_TYPE",
    "SEMANTIC_DOMAIN_RULE_ID",
    "SEMANTIC_DOMAIN_VOCABULARY",
    "TOPIC_ROUTE_RULE_ID",
    "TOPIC_VOCABULARY",
    "_EXPECTED_COLUMNS",
    "_EXPECTED_INDEXES",
    "_assignment_payload",
    "_facet_coverage",
    "audit_cards_refactor_coverage",
    "audit_semantic_domain_coverage",
    "build_semantic_domain_operations",
    "build_topic_operations",
    "facet_schema_checksum",
    "facets_for_object",
    "install_facet_schema",
    "normalize_audiences",
    "open_graph",
    "schema_is_installed",
    "selector_digest",
    "semantic_domain_for_node_type",
    "validate_facet_schema",
]
