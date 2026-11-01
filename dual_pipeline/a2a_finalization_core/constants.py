"""Constants, roles, and regex patterns for A2A finalization."""
from __future__ import annotations

import re

FINALIZER_NAME = "nee-a2a-source-finalizer"
FINALIZER_VERSION = "1.1.0"
FINALIZATION_RECEIPT_CONTRACT = "urn:nee:dual-pipeline:a2a-finalization-receipt:1.0.0"
ARCHIVED_SOURCE_BUNDLE_FILENAME = "source_bundle.zip"
_REQUIRED_ARCHIVE_ROLES = frozenset(
    {"source_bundle", "capture_manifest", "span_ledger", "selection_ledger"}
)
_ARCHIVE_ROLES = _REQUIRED_ARCHIVE_ROLES | {"raw_capture"}
_FINALIZATION_BINDING_FIELDS = frozenset(
    {
        "receipt_id",
        "receipt_canonical_digest",
        "receipt_file_sha256",
        "package_file_sha256",
        "package_canonical_digest",
        "archive_root_id",
        "archive_binding_digest",
        "verification_state",
    }
)
_FINALIZATION_REFERENCE_FIELDS = frozenset(
    {
        "run_id",
        "package_relative_path",
        "receipt_relative_path",
        "package_file_sha256",
        "receipt_file_sha256",
        "archive_root_id",
    }
)
_VERIFIER_CAPABILITY = object()
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
_WINDOWS_RESERVED_PATH_SEGMENT = re.compile(
    r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$", re.IGNORECASE
)
