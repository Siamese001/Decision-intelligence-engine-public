"""
tools/pr_governance_core/queue_render.py

PR Queue Table & Diagnostic Ledger Renderer (Backward-Compatibility Forwarder).
Forwards to tools.pr_governance_core.render.
"""
from __future__ import annotations

from .render import render_queue_table

__all__ = ["render_queue_table"]
