"""Universal Write Gateway core package."""
from __future__ import annotations

from .constants import (
    _A2A_FINALIZATION_BINDING_FIELDS,
    DIRECT_PROMOTION_POSTURES,
    PAIR_FIELDS,
    UWG_FACT_PROMOTION_GOVERNANCE,
    UWG_GOVERNANCE_APPROVAL_CONTRACT,
    UWG_POLICY_THRESHOLD_FIELDS,
    UWG_PROMOTION_POLICY_PATH,
    UWG_RELEASE_KINDS,
)
from .gateway import (
    UniversalWriteGateway,
)
from .lineage_bridge import (
    MANDATORY_DELTA_TABLES,
    AssertionReconciler,
    SourceLineageBridge,
    compute_granular_delta_table,
    format_delta_table_markdown,
)
from .policy import (
    _assert_backend_matches,
    _correlation,
    _iso_date,
    _load_uwg_promotion_policy,
    _nonempty_text,
    _number,
    _pair_binding,
    _policy_binding,
    _policy_error,
    _replayed_a2a_payload_governance,
    _same_pair,
    _seal_receipt,
    _table_exists,
    _utc_timestamp,
    _verify_sealed_receipt,
)

__all__ = [
    "DIRECT_PROMOTION_POSTURES",
    "PAIR_FIELDS",
    "UWG_FACT_PROMOTION_GOVERNANCE",
    "UWG_GOVERNANCE_APPROVAL_CONTRACT",
    "UWG_POLICY_THRESHOLD_FIELDS",
    "UWG_PROMOTION_POLICY_PATH",
    "UWG_RELEASE_KINDS",
    "UniversalWriteGateway",
    "SourceLineageBridge",
    "AssertionReconciler",
    "compute_granular_delta_table",
    "format_delta_table_markdown",
    "MANDATORY_DELTA_TABLES",
    "_A2A_FINALIZATION_BINDING_FIELDS",
    "_assert_backend_matches",
    "_correlation",
    "_iso_date",
    "_load_uwg_promotion_policy",
    "_nonempty_text",
    "_number",
    "_pair_binding",
    "_policy_binding",
    "_policy_error",
    "_replayed_a2a_payload_governance",
    "_same_pair",
    "_seal_receipt",
    "_table_exists",
    "_utc_timestamp",
    "_verify_sealed_receipt",
]
