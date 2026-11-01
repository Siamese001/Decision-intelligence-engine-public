"""Semantic Merge Core package for syntax-aware 3-way conflict resolution."""

from __future__ import annotations

from .ast_set_merge import merge_python_ast_sets
from .json_registry_merge import merge_json_registries
from .markdown_table_merge import merge_markdown_tables

__all__ = [
    "merge_json_registries",
    "merge_markdown_tables",
    "merge_python_ast_sets",
]
