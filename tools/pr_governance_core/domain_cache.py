"""Cryptographic Content-Addressed Cache for Domain Classifications."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from tools.pr_governance_core.domain_models import DomainClassificationEnvelope

_CACHE_FILE = Path(__file__).resolve().parents[2] / "reports" / "domain_classification_cache.json"
_LOCK = threading.Lock()


_CACHE_SCHEMA_VERSION = "3.0.3"


def _make_key(branch: str, changed_files: Iterable[str]) -> str:
    sorted_files = sorted([f for f in changed_files if f])
    raw = f"v{_CACHE_SCHEMA_VERSION}::{branch.strip()}::{'|'.join(sorted_files)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_cached_envelope(
    branch: str,
    changed_files: Iterable[str],
    cache_path: Optional[Path] = None,
) -> Optional[DomainClassificationEnvelope]:
    """Retrieve cached classification envelope if exact key exists."""
    target = cache_path or _CACHE_FILE
    if not target.exists():
        return None
    with _LOCK:
        try:
            data: Dict[str, Any] = json.loads(target.read_text(encoding="utf-8"))
            key = _make_key(branch, changed_files)
            entry = data.get(key)
            if entry:
                return DomainClassificationEnvelope.model_validate(entry)
        except Exception:
            pass
    return None


def store_cached_envelope(
    branch: str,
    changed_files: Iterable[str],
    envelope: DomainClassificationEnvelope,
    cache_path: Optional[Path] = None,
) -> None:
    """Persist classification envelope to atomic JSON cache."""
    target = cache_path or _CACHE_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    key = _make_key(branch, changed_files)

    with _LOCK:
        data: Dict[str, Any] = {}
        if target.exists():
            try:
                data = json.loads(target.read_text(encoding="utf-8"))
            except Exception:
                data = {}

        data[key] = envelope.model_dump()
        unique_suffix = f".tmp.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex[:8]}"
        tmp_path = target.with_suffix(unique_suffix)
        try:
            tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp_path.replace(target)
        except Exception:
            if tmp_path.exists():
                with contextlib.suppress(Exception):
                    tmp_path.unlink()

