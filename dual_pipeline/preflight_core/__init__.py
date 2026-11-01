"""Preflight core package."""
from __future__ import annotations

import importlib
import importlib.metadata
import math
import os
import platform
import re
import struct
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterator

from packaging.utils import canonicalize_name

from ..authorization import load_production_authorization_context
from ..contracts import validate_artifact, validate_file
from ..core import (
    ROOT,
    digest,
    normalize_space,
    read_json,
    sha256_bytes,
    sha256_file,
    sqlite_integrity,
    utc_now,
    write_json_exclusive,
)
from ..index import (
    DENSE_DIMENSIONS,
    get_backend,
    is_link_or_reparse,
    validate_bge_model_manifest,
)
from .checks import (
    check_cpu,
    check_locked_environment,
    check_model_manifest,
    check_offline_bge_m3_smoke,
    check_path_separation,
    check_pip_dependencies,
    check_production_authorization,
    check_report_destination,
    check_runtime_contract,
    check_runtime_root,
    check_seed_graph,
    check_tracked_secrets,
    load_seed_contract,
)
from .constants import (
    _SECRET_PATTERNS,
    MODEL_PIN_CONTRACT,
    PACKAGE_MANIFEST,
    PIP_CHECK_TIMEOUT_SECONDS,
    RELEASE_STATE,
    REQUIRED_QUALIFICATION_CAPABILITIES,
    RUNTIME_CONTRACT,
    SMOKE_TEXT,
)
from .helpers import (
    _backend_devices,
    _blocked,
    _fail,
    _offline_environment,
    _pass,
    _qualification_resources,
    _safe_check,
    _tracked_paths,
    _validate_qualification_resource_contracts,
    _within,
    _write_probe,
)
from .models import PreflightConfig
from .runner import run_preflight

__all__ = [
    "Any",
    "Callable",
    "DENSE_DIMENSIONS",
    "Iterator",
    "MODEL_PIN_CONTRACT",
    "PACKAGE_MANIFEST",
    "PIP_CHECK_TIMEOUT_SECONDS",
    "Path",
    "PreflightConfig",
    "RELEASE_STATE",
    "REQUIRED_QUALIFICATION_CAPABILITIES",
    "ROOT",
    "RUNTIME_CONTRACT",
    "SMOKE_TEXT",
    "_SECRET_PATTERNS",
    "_backend_devices",
    "_blocked",
    "_fail",
    "_offline_environment",
    "_pass",
    "_qualification_resources",
    "_safe_check",
    "_tracked_paths",
    "_validate_qualification_resource_contracts",
    "_within",
    "_write_probe",
    "annotations",
    "canonicalize_name",
    "check_cpu",
    "check_locked_environment",
    "check_model_manifest",
    "check_offline_bge_m3_smoke",
    "check_path_separation",
    "check_pip_dependencies",
    "check_production_authorization",
    "check_report_destination",
    "check_runtime_contract",
    "check_runtime_root",
    "check_seed_graph",
    "check_tracked_secrets",
    "contextmanager",
    "dataclass",
    "digest",
    "get_backend",
    "importlib",
    "is_link_or_reparse",
    "load_production_authorization_context",
    "load_seed_contract",
    "math",
    "normalize_space",
    "os",
    "platform",
    "re",
    "read_json",
    "replace",
    "run_preflight",
    "sha256_bytes",
    "sha256_file",
    "sqlite_integrity",
    "struct",
    "subprocess",
    "sys",
    "threading",
    "time",
    "utc_now",
    "uuid",
    "validate_artifact",
    "validate_bge_model_manifest",
    "validate_file",
    "write_json_exclusive",
]
