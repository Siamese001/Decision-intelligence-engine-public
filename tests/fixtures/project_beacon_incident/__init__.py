"""tests/fixtures/project_beacon_incident package.

Provides sanitized fixtures, schemas, and evidence loaders for the
Project Beacon recording startup and attribution incident RCA.
"""
from .loader import (
    load_incident_schema,
    load_sanitized_incident_fixture,
    validate_incident_fixture,
)

__all__ = [
    "load_incident_schema",
    "load_sanitized_incident_fixture",
    "validate_incident_fixture",
]
