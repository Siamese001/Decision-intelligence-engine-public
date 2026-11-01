#!/usr/bin/env python3
"""Backfill is executed by migrations/migrate.py apply in approved dependency order.

This wrapper intentionally delegates to the same transaction-controlled runner so
schema creation and the exact digest-bound rowsets cannot drift apart.
"""
import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).resolve().parents[1] / "migrate.py"), run_name="__main__")
