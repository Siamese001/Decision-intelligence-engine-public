"""Universal Write Gateway for post-runtime candidate promotion.

Backward-compatible facade module delegating to dual_pipeline.uwg_core.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import time
from pathlib import Path
from typing import Any

from ._validators import table_exists
from .a2a_finalization import (
    reverify_ingress_finalization_normalized_bundle,
    validated_ingress_finalization_reference,
    verified_finalization_binding,
)
from .authorization import AuthorizationContext
from .contracts import create_artifact, validate_artifact
from .core import (
    ROOT,
    assert_safe_mutable_path,
    connect_sqlite,
    digest,
    exact_file_copy,
    file_lock,
    read_json,
    sha256_bytes,
    sha256_file,
    sqlite_integrity,
    stable_id,
    utc_now,
    write_json,
    write_json_exclusive,
)
from .index import (
    EmbeddingBackend,
    HybridRetriever,
    build_sidecar,
    retrieval_metadata_from_artifact,
)
from .label_quality import approved_validation_state, assert_label_quality_no_regression
from .release import ReleaseManager, release_action_resources
from .uwg_core import (
    _A2A_FINALIZATION_BINDING_FIELDS,
    DIRECT_PROMOTION_POSTURES,
    PAIR_FIELDS,
    UWG_FACT_PROMOTION_GOVERNANCE,
    UWG_GOVERNANCE_APPROVAL_CONTRACT,
    UWG_POLICY_THRESHOLD_FIELDS,
    UWG_PROMOTION_POLICY_PATH,
    UWG_RELEASE_KINDS,
    UniversalWriteGateway,
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

__all__ = (
    "Any",
    "AuthorizationContext",
    "DIRECT_PROMOTION_POSTURES",
    "EmbeddingBackend",
    "HybridRetriever",
    "PAIR_FIELDS",
    "Path",
    "ROOT",
    "ReleaseManager",
    "UWG_FACT_PROMOTION_GOVERNANCE",
    "UWG_GOVERNANCE_APPROVAL_CONTRACT",
    "UWG_POLICY_THRESHOLD_FIELDS",
    "UWG_PROMOTION_POLICY_PATH",
    "UWG_RELEASE_KINDS",
    "UniversalWriteGateway",
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
    "annotations",
    "approved_validation_state",
    "assert_label_quality_no_regression",
    "assert_safe_mutable_path",
    "build_sidecar",
    "connect_sqlite",
    "create_artifact",
    "digest",
    "dt",
    "exact_file_copy",
    "file_lock",
    "json",
    "math",
    "read_json",
    "release_action_resources",
    "retrieval_metadata_from_artifact",
    "reverify_ingress_finalization_normalized_bundle",
    "sha256_bytes",
    "sha256_file",
    "sqlite_integrity",
    "stable_id",
    "table_exists",
    "time",
    "utc_now",
    "validate_artifact",
    "validated_ingress_finalization_reference",
    "verified_finalization_binding",
    "write_json",
    "write_json_exclusive",
)

from .retrieval_release_core import validate_activation_evaluation

__all__ += ("validate_activation_evaluation",)
