"""
tests/fixtures/project_beacon_incident/loader.py

Typed loader and schema validator for the sanitized Project Beacon
incident fixtures and evidence bounds.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import jsonschema

PACKAGE_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = PACKAGE_DIR / "incident_schema.json"
FIXTURE_PATH = PACKAGE_DIR / "sanitized_incident_fixture.json"


def load_incident_schema() -> Dict[str, Any]:
    """Loads the canonical JSON schema for incident fixtures."""
    if not SCHEMA_PATH.is_file():
        raise FileNotFoundError(f"Incident schema not found at {SCHEMA_PATH}")
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def load_sanitized_incident_fixture() -> Dict[str, Any]:
    """Loads the sanitized Project Beacon recording incident fixture."""
    if not FIXTURE_PATH.is_file():
        raise FileNotFoundError(f"Sanitized incident fixture not found at {FIXTURE_PATH}")
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def validate_incident_fixture(
    fixture: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, List[str]]:
    """
    Validates an incident fixture against the schema.
    Returns (is_valid, error_messages).
    """
    schema = load_incident_schema()
    target = fixture if fixture is not None else load_sanitized_incident_fixture()

    validator = jsonschema.Draft7Validator(schema)
    errors = sorted(validator.iter_errors(target), key=lambda e: e.path)
    if errors:
        return False, [f"{'/'.join(map(str, e.path))}: {e.message}" for e in errors]
    return True, []


def get_pro_launch_steps() -> List[Dict[str, Any]]:
    """Returns the ordered launch steps observed during the Pro session."""
    data = load_sanitized_incident_fixture()
    return data["model_sessions"]["pro"]["launch_steps"]


def get_flash_launch_steps() -> List[Dict[str, Any]]:
    """Returns the ordered launch steps observed during the Flash session."""
    data = load_sanitized_incident_fixture()
    return data["model_sessions"]["flash"]["launch_steps"]


def get_attribution_test_cases() -> List[Dict[str, Any]]:
    """Returns the incident speaker hint attribution cases."""
    data = load_sanitized_incident_fixture()
    return data["attribution_cases"]


def get_spool_evidence() -> Dict[str, Any]:
    """Returns the unmanaged spool snapshot evidence."""
    data = load_sanitized_incident_fixture()
    return data["spool_evidence"]


def get_evidence_boundaries() -> Dict[str, Any]:
    """Returns explicit evidence boundaries and rules from the RCA."""
    data = load_sanitized_incident_fixture()
    return data["evidence_boundaries"]


def evaluate_claim_grounding(session_key: str) -> Dict[str, Any]:
    """
    Evaluates whether a model session's diagnosed claim was grounded in evidence.
    Returns structured verification metadata.
    """
    data = load_sanitized_incident_fixture()
    if session_key not in data["model_sessions"]:
        raise KeyError(f"Unknown session key: {session_key}")

    session = data["model_sessions"][session_key]
    return {
        "model_label": session["model_label"],
        "diagnosed_claim": session["diagnosed_claim"],
        "claim_grounded": session["claim_grounded"],
        "actual_root_cause": session["actual_root_cause"],
    }
