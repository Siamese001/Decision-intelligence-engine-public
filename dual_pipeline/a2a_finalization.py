"""Trusted archive replay for finalized OpenAI/Anthropic A2A packets.

A package's canonical digest proves self-consistency, not that a human span
selection was made against the captured bytes.  This module makes the retained
archive a mandatory ingress proof for selected-span packets: it validates a
sealed receipt, hashes every retained input, deterministically re-runs the
finalizer, and compares the resulting package bytes.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from dual_pipeline.contracts import create_artifact, validate_artifact
from dual_pipeline.core import (
    assert_safe_mutable_path,
    digest,
    sha256_bytes,
    sha256_file,
    stable_id,
)
from dual_pipeline.exceptions import DualPipelineValueError

from .a2a_finalization_core import (
    _ARCHIVE_ROLES,
    _FINALIZATION_BINDING_FIELDS,
    _FINALIZATION_REFERENCE_FIELDS,
    _REQUIRED_ARCHIVE_ROLES,
    _RUN_ID_PATTERN,
    _VERIFIER_CAPABILITY,
    _WINDOWS_RESERVED_PATH_SEGMENT,
    ARCHIVED_SOURCE_BUNDLE_FILENAME,
    FINALIZATION_RECEIPT_CONTRACT,
    FINALIZER_NAME,
    FINALIZER_VERSION,
    A2AFinalizationError,
    VerifiedA2AFinalization,
    _archive_path_key,
    _copy_archive_file,
    _read_object,
    _read_object_bytes,
    _relative_path,
    _reverify_ingress_finalization_evidence,
    _safe_archive_file,
    _safe_archive_root_id,
    _stage_exact_bytes,
    _verify_capture_inventory,
    _verify_receipt_archive,
    a2a_normalized_content_projection,
    create_finalization_receipt,
    reverify_ingress_finalization_evidence,
    reverify_ingress_finalization_normalized_bundle,
    stage_finalization_archive,
    stage_ingress_finalization_evidence,
    validated_ingress_finalization_reference,
    verified_finalization_binding,
    verify_finalized_a2a_package,
    verify_finalized_a2a_package_bytes,
)

__all__ = [
    "A2AFinalizationError",
    "ARCHIVED_SOURCE_BUNDLE_FILENAME",
    "Any",
    "DualPipelineValueError",
    "FINALIZATION_RECEIPT_CONTRACT",
    "FINALIZER_NAME",
    "FINALIZER_VERSION",
    "Mapping",
    "MappingProxyType",
    "Path",
    "VerifiedA2AFinalization",
    "_ARCHIVE_ROLES",
    "_FINALIZATION_BINDING_FIELDS",
    "_FINALIZATION_REFERENCE_FIELDS",
    "_REQUIRED_ARCHIVE_ROLES",
    "_RUN_ID_PATTERN",
    "_VERIFIER_CAPABILITY",
    "_WINDOWS_RESERVED_PATH_SEGMENT",
    "_archive_path_key",
    "_copy_archive_file",
    "_read_object",
    "_read_object_bytes",
    "_relative_path",
    "_reverify_ingress_finalization_evidence",
    "_safe_archive_file",
    "_safe_archive_root_id",
    "_stage_exact_bytes",
    "_verify_capture_inventory",
    "_verify_receipt_archive",
    "a2a_normalized_content_projection",
    "annotations",
    "assert_safe_mutable_path",
    "create_artifact",
    "create_finalization_receipt",
    "dataclass",
    "digest",
    "json",
    "re",
    "reverify_ingress_finalization_evidence",
    "reverify_ingress_finalization_normalized_bundle",
    "sha256_bytes",
    "sha256_file",
    "shutil",
    "stable_id",
    "stage_finalization_archive",
    "stage_ingress_finalization_evidence",
    "tempfile",
    "validate_artifact",
    "validated_ingress_finalization_reference",
    "verified_finalization_binding",
    "verify_finalized_a2a_package",
    "verify_finalized_a2a_package_bytes",
]
