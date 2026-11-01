"""Core implementation for combined Sherpa/status remediation."""
from __future__ import annotations

from .constants import (
    _PAIR_FIELDS,
    _REQUIRED_CONTROL_KEYS,
    COMBINED_REMEDIATION_CONTROL_KEY,
    COMBINED_REMEDIATION_POLICY_VERSION,
)
from .execution import (
    apply_combined_remediation_plan,
    prepare_combined_remediation_release,
    record_combined_remediation_release_events,
)
from .plan import build_combined_remediation_plan
from .validation import (
    _aggregate_counts,
    _assert_active_binding,
    _assert_plan_digest,
    _pair_binding,
    _require_sha256,
    _require_text,
    combined_remediation_control_from_package,
    validate_combined_remediation_control,
)

__all__ = [
    "COMBINED_REMEDIATION_CONTROL_KEY",
    "COMBINED_REMEDIATION_POLICY_VERSION",
    "_PAIR_FIELDS",
    "_REQUIRED_CONTROL_KEYS",
    "_aggregate_counts",
    "_assert_active_binding",
    "_assert_plan_digest",
    "_pair_binding",
    "_require_sha256",
    "_require_text",
    "apply_combined_remediation_plan",
    "build_combined_remediation_plan",
    "combined_remediation_control_from_package",
    "prepare_combined_remediation_release",
    "record_combined_remediation_release_events",
    "validate_combined_remediation_control",
]
