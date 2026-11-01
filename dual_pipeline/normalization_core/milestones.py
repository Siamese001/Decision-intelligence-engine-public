"""
SSOT resolver and validation guard for project contractual milestones.
Prevents pipeline execution sequence from being conflated with contractual milestone IDs.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


def load_project_milestones(
    project: str = "project_lens",
    config_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Loads milestone definitions for the specified project from config SSOT."""
    base_dir = config_dir or _DEFAULT_CONFIG_DIR
    proj_slug = project.lower().replace("-", "_")
    candidates = [
        base_dir / proj_slug / "contract_milestones.json",
        base_dir / proj_slug / "milestones.json",
        base_dir / "governance" / f"{proj_slug}_milestones.json",
    ]
    for p in candidates:
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Failed to load milestone config from %s: %s", p, exc)
    return {}


def validate_milestone_metric(
    milestone_id: str,
    metric_name: str,
    project: str = "project_lens",
) -> Tuple[bool, Optional[str]]:
    """
    Validates whether a reported metric matches the contractual milestone SSOT.
    Returns (is_valid, error_message).
    """
    data = load_project_milestones(project)
    milestones = data.get("milestones", {})
    m_info = milestones.get(milestone_id.upper())
    if not m_info:
        return True, None

    expected_metric = m_info.get("metric_name", "").lower()
    provided_metric = metric_name.lower()

    if expected_metric and expected_metric not in provided_metric and provided_metric not in expected_metric:
        prohibited = m_info.get("prohibited_conflation", "")
        msg = (
            f"MILESTONE_INVERSION_DETECTED: {milestone_id} expects '{m_info.get('metric_name')}' "
            f"but received '{metric_name}'. {prohibited}"
        )
        return False, msg
    return True, None
