"""
dual_pipeline/speaker_memory_core/bootstrap.py

Bootstrap speaker profiles seeded from runtime configuration.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from config.credentials import CredentialManager


def load_bootstrap_profiles() -> Dict[str, Dict[str, Any]]:
    """Loads default speaker profiles from config/runtime/speaker_memory.json."""
    env_path = CredentialManager.get_credential("NEE_SPEAKER_MEMORY_PATH")
    if env_path and Path(env_path).is_file():
        cfg_path = Path(env_path)
    else:
        anchor = Path(__file__).resolve()
        parts = anchor.parts
        if ".worktrees" in parts:
            wt_idx = parts.index(".worktrees")
            cfg_path = Path(*parts[:wt_idx]) / "config" / "runtime" / "speaker_memory.json"
        else:
            cfg_path = anchor.parents[2] / "config" / "runtime" / "speaker_memory.json"

    if cfg_path.is_file():
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("profiles", {})
        except Exception:
            return {}
    return {}


BOOTSTRAP_PROFILES: Dict[str, Dict[str, Any]] = load_bootstrap_profiles()
