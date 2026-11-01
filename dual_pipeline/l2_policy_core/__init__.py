"""Core subpackage for governed conditional external-L2 policy definitions and config."""
from __future__ import annotations

import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from .._validators import require_positive_int, require_strict_keys, require_text
from ..contracts import create_artifact
from ..core import (
    ROOT,
    canonical_json,
    digest,
    is_link_or_reparse,
    read_json,
    stable_id,
)
from ..exceptions import DualPipelineRuntimeError, DualPipelineValueError
from ..provider_request import (
    measure_responses_request,
    provider_request_budget_verdict,
)
from .constants import (
    _BOUNDED_LOOKUP,
    _BOUNDED_RECORD_LOOKUP,
    _CANDIDATE,
    _COMPARISON,
    _DEFENSE,
    _DIRECT_REASONING_VERB,
    _EXPLANATION,
    _HIGH_MATERIALITY,
    _HOW_EXPLANATION,
    _PRIORITIZATION,
    _RECOMMENDATION,
    _RISK,
    _STRUCTURED_DELIVERABLE,
    L2_POLICY_PATH,
    QUERY_TYPE_POLICY_MATRIX,
    RETRIEVAL_MODE_LANES,
)
from .helpers import (
    _load_model_policy,
    _positive_int,
    _price,
    _strict_keys,
    external_provider_offline,
    resolve_openai_model_id,
)
from .intent import (
    analyze_request_intent,
    canonical_query_type,
    determine_materiality,
)
from .models import (
    CallPurpose,
    L2Config,
    L2Disposition,
    L2ExecutionDecision,
    L2Policy,
    L2ReasonCode,
    ModelPolicy,
    OutputPolicy,
    PricingPolicy,
    ProviderFailureCode,
    QualityFailureCode,
    QueryAssuranceProfile,
    QueryTypePolicy,
    ReliabilityPolicy,
    RequestBudgetPolicy,
    RequestIntent,
    SchemaFailureCode,
)
from .policy import (
    load_l2_config,
    query_model_policy_for_assurance,
    query_model_policy_for_purpose,
    query_retrieval_policy,
    query_type_policy,
    retrieval_lanes_for_mode,
)

__all__ = (
    "Any",
    "CallPurpose",
    "DualPipelineRuntimeError",
    "DualPipelineValueError",

    "L2Config",
    "L2Disposition",
    "L2ExecutionDecision",
    "L2Policy",
    "L2ReasonCode",
    "L2_POLICY_PATH",
    "Mapping",
    "MappingProxyType",
    "ModelPolicy",
    "OutputPolicy",
    "Path",
    "PricingPolicy",
    "ProviderFailureCode",
    "QUERY_TYPE_POLICY_MATRIX",
    "QualityFailureCode",
    "QueryAssuranceProfile",
    "QueryTypePolicy",
    "RETRIEVAL_MODE_LANES",
    "ROOT",
    "ReliabilityPolicy",
    "RequestBudgetPolicy",
    "RequestIntent",
    "SchemaFailureCode",
    "Sequence",
    "StrEnum",
    "_BOUNDED_LOOKUP",
    "_BOUNDED_RECORD_LOOKUP",
    "_CANDIDATE",
    "_COMPARISON",
    "_DEFENSE",
    "_DIRECT_REASONING_VERB",
    "_EXPLANATION",
    "_HIGH_MATERIALITY",
    "_HOW_EXPLANATION",
    "_PRIORITIZATION",
    "_RECOMMENDATION",
    "_RISK",
    "_STRUCTURED_DELIVERABLE",
    "_load_model_policy",
    "_positive_int",
    "_price",
    "_strict_keys",
    "analyze_request_intent",
    "canonical_json",
    "canonical_query_type",
    "create_artifact",
    "dataclass",
    "determine_materiality",
    "digest",
    "external_provider_offline",
    "is_link_or_reparse",
    "json",
    "load_l2_config",
    "measure_responses_request",
    "os",
    "provider_request_budget_verdict",
    "query_model_policy_for_assurance",
    "query_model_policy_for_purpose",
    "query_retrieval_policy",
    "query_type_policy",
    "re",
    "read_json",
    "require_positive_int",
    "require_strict_keys",
    "require_text",
    "resolve_openai_model_id",
    "retrieval_lanes_for_mode",
    "socket",
    "stable_id",
    "time",
    "urllib",
)

