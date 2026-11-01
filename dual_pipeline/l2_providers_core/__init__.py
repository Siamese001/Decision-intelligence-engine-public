"""Core components and re-exports for L2 providers and execution."""

from __future__ import annotations

import fnmatch
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
from typing import Any, Callable, Mapping, Protocol, Sequence

from dual_pipeline._validators import require_positive_int, require_strict_keys, require_text
from dual_pipeline.contracts import create_artifact
from dual_pipeline.core import (
    ROOT,
    canonical_json,
    digest,
    is_link_or_reparse,
    read_json,
    stable_id,
)
from dual_pipeline.exceptions import DualPipelineRuntimeError, DualPipelineValueError
from dual_pipeline.l2_output_contract import (
    STRATEGIC_RECOMMENDATION_ANSWER_USES,
    SYSTEM_INSTRUCTION_EXTENSION,
    material_citation_obligations,
    output_contract_digest,
    record_bound_decision_schema,
    record_evidence_binding_map,
)
from dual_pipeline.l2_policy import *
from dual_pipeline.l2_policy import (
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
    _load_model_policy,
    _positive_int,
    _price,
    _strict_keys,
)
from dual_pipeline.provider_request import (
    aggregate_token_usage,
    build_responses_request,
    measure_responses_request,
    provider_request_budget_verdict,
)

from .auth import (
    _ignored_by_repository,
    _key_from_ignored_local_env,
    resolve_google_api_key,
    resolve_openai_api_key,
)
from .constants import (
    _CITATION_ROW,
    _PROMPT_SLOT_NAMES,
    _REPAIR_ROW_VALUE_FIELDS,
    DECISION_MODEL_OUTPUT_SCHEMA,
    DECISION_OUTPUT_CONTRACT_DIGEST,
)
from .decision import (
    _EXPECTED_ROUTE,
    _ROUTE_REASON,
    _artifact_digest,
    _direct_approved_citations_available,
    _intent_reason,
    _verified_evidence_packet_hash,
    decide_l2_execution,
)
from .execution import (
    _failure_result,
    _invoke_provider,
    execute_conditional_l2,
    execute_content_repair,
)
from .google_provider import (
    GoogleProviderFactory,
    GoogleReasoningProvider,
    UnifiedL2ProviderFactory,
    select_l2_provider,
)
from .payload import (
    _prompt_evidence_rows,
    _verify_governed_prompt,
    build_l2_provider_body,
    l2_system_instruction,
    l2_user_payload,
    provider_quality_requirements,
)
from .provider import (
    OpenAIProviderFactory,
    OpenAIReasoningProvider,
    build_call_record,
)
from .types import (
    ContentRepairResult,
    L2CallRecord,
    L2ExecutionResult,
    L2ProviderError,
    L2ProviderRequest,
    L2ProviderResponse,
    QualityDeficiency,
    ReasoningProvider,
    ReasoningProviderFactory,
    SchemaIssue,
    SchemaValidationResult,
    TokenUsage,
    _CallBudget,
)
from .validation import (
    _packet_identifiers,
    _recover_truncated_json,
    _repair_atom,
    _repair_role_values,
    _required_material_record_ids_by_lane,
    _schema_repair_preserves_facts,
    evaluate_semantic_quality,
    validate_decision_model_output,
)
