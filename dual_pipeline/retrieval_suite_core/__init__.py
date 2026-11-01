"""Retrieval suite core package."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from ..contracts import create_artifact, validate_artifact
from ..core import (
    ROOT,
    assert_safe_mutable_path,
    digest,
    exact_file_copy,
    read_json,
    sha256_file,
    stable_id,
    write_json_exclusive,
)
from ..exceptions import DualPipelineRuntimeError
from ..index import CLASSIFICATION_RANK, RETRIEVAL_MODE_POLICY
from ..release_core.helpers import _verify_receipt
from ..release_core.manager import ReleaseManager
from ..retrieval_release_core.metrics import (
    _records_by_id,
    _scoped_release_delta,
)
from ..retrieval_release_core.policies import (
    _load_policy,
    _policy_binding,
)
from .builder import AutomaticRetrievalReleaseSuiteBuilder
from .cases import (
    _acl_boundary,
    _bounded,
    _case,
    _core_case,
    _delta_group_cases,
    _group_key,
    _groups,
    _lane_isolation,
    _record,
    _removed_group_cases,
    _render,
    _retrieval_expected,
    _retrieval_mode,
    _scope_ids,
    _template_values,
)
from .constants import (
    _CLASSIFICATIONS,
    _MAX_QUERY_CHARACTERS,
    _RELATIONSHIP_RECORD_TYPES,
    GENERATOR_VERSION,
    RETRIEVAL_SUITE_GENERATION_POLICY_PATH,
)
from .exceptions import AutomaticSuiteBuildError
from .policy import _load_generation_policy

__all__ = [
    "Any",
    "AutomaticRetrievalReleaseSuiteBuilder",
    "AutomaticSuiteBuildError",
    "CLASSIFICATION_RANK",
    "DualPipelineRuntimeError",
    "GENERATOR_VERSION",
    "Path",
    "RETRIEVAL_MODE_POLICY",
    "RETRIEVAL_SUITE_GENERATION_POLICY_PATH",
    "ROOT",
    "ReleaseManager",
    "_CLASSIFICATIONS",
    "_MAX_QUERY_CHARACTERS",
    "_RELATIONSHIP_RECORD_TYPES",
    "_acl_boundary",
    "_bounded",
    "_case",
    "_core_case",
    "_delta_group_cases",
    "_group_key",
    "_groups",
    "_lane_isolation",
    "_load_generation_policy",
    "_load_policy",
    "_policy_binding",
    "_record",
    "_records_by_id",
    "_removed_group_cases",
    "_render",
    "_retrieval_expected",
    "_retrieval_mode",
    "_scope_ids",
    "_scoped_release_delta",
    "_template_values",
    "_verify_receipt",
    "annotations",
    "assert_safe_mutable_path",
    "create_artifact",
    "defaultdict",
    "digest",
    "exact_file_copy",
    "json",
    "read_json",
    "sha256_file",
    "stable_id",
    "validate_artifact",
    "write_json_exclusive",
]
