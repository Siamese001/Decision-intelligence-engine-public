"""
dual_pipeline/transcript_stages_core/deadline_executor.py

Re-exports execute_with_deadline_and_heartbeat from config.transport.
"""
from __future__ import annotations

from config.transport import execute_with_deadline_and_heartbeat

__all__ = ["execute_with_deadline_and_heartbeat"]
