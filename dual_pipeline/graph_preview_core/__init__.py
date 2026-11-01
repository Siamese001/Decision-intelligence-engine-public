"""
dual_pipeline/graph_preview_core

Module for calculating and rendering graph delta previews for dry-run and backtest modes.
"""
from __future__ import annotations

from .preview import generate_graph_delta_preview

__all__ = ["generate_graph_delta_preview"]
