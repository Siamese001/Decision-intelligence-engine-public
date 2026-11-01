"""
dual_pipeline/transcript_metadata_core/entity_resolver_write.py

Universal Write Gateway role persistence re-export for GraphDB Single Source of Truth (SSOT).
"""

from __future__ import annotations

from dual_pipeline.uwg_core.universal_write_gateway_role import persist_validated_role

__all__ = ["persist_validated_role"]
