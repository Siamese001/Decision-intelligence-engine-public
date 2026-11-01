"""Constants and basic validators for ingestion directives and completion controls."""
from __future__ import annotations

import os
import re
from pathlib import Path

from ..intake import INTAKE_STAGES

DIRECTIVE_SCHEMA_VERSION = "1.0.0"
DIRECTIVE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
DIRECTIVE_STATUSES = frozenset({"OPEN", "BLOCKED", "CANCELLED", "COMPLETE"})
TERMINAL_STATUSES = frozenset({"BLOCKED", "CANCELLED", "COMPLETE"})
RELEASE_STAGES = [*INTAKE_STAGES, "W0", "E0"]
_BGE_M3_BACKENDS = frozenset(
    {
        "flagembedding-bge-m3",
        "flagembedding-bge-m3-dev",
        "flagembedding-bge-m3-internal",
    }
)
_EMBEDDING_TARGET_FIELDS = (
    "runtime_root",
    "embedding_backend",
    "model_id",
    "model_revision",
    "model_manifest_digest",
    "production_qualified",
)
_BASELINE_PAIR_FIELDS = (
    "generation_id",
    "graph_sha256",
    "sidecar_sha256",
    "graph_snapshot",
    "embedding_backend",
    "model_id",
    "model_revision",
    "model_manifest_digest",
    "production_qualified",
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_BLOCK_REASON_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")


def _is_nonblank_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_PATTERN.fullmatch(value) is not None


def _package_path_key(path: str | Path) -> str:
    """Return the platform identity key for an already-resolved package path."""
    return os.path.normcase(os.path.normpath(str(path)))
