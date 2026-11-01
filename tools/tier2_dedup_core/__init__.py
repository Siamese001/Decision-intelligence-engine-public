"""
tools/tier2_dedup_core

Modular core package for Tier 2 GraphDB semantic concept deduplication.
Each module is strictly bounded under the 400-line budget and conforms
to unidirectional architectural layering without direct raw sqlite3 imports.
"""
from __future__ import annotations

from .lineage import execute_edge_lineage_fixes
from .merges import execute_node_merges
from .predicates import execute_predicate_consolidation
from .search_fts import execute_search_and_fts_rebuild
from .supersessions import execute_assertion_supersessions
from .validation import execute_signal_quality_validation

__all__ = [
    "execute_edge_lineage_fixes",
    "execute_node_merges",
    "execute_predicate_consolidation",
    "execute_search_and_fts_rebuild",
    "execute_assertion_supersessions",
    "execute_signal_quality_validation",
]
