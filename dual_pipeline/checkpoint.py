"""Versioned, integrity-checked pipeline checkpoints with optional strict binding.

Legacy callers may keep unbound stage serialization. Document ingestion opts into
require_binding=True and binds current source/configuration before any reuse.
The enclosing ingestion lock protects workflow semantics; checkpoint file locks
add atomic, merge-safe storage for individual operations.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Union
from uuid import uuid4

from dual_pipeline.checkpoint_storage import (
    BINDING_VERSION, VERSION, CheckpointIntegrityError, atomic_write_checkpoint,
    binding_digest, checkpoint_lock, fsync_directory, read_checkpoint,
)

logger = logging.getLogger(__name__)


def _empty(binding: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    return {"version": VERSION, "binding_version": BINDING_VERSION,
            "binding": deepcopy(binding), "binding_digest": binding_digest(binding) if binding else None,
            "epoch": uuid4().hex, "revision": 0, "stages": {}, "metadata": {}, "invalidations": []}


class PipelineCheckpointManager:
    """Exact binding decisions plus durable stages; returned payloads are isolated copies."""

    def __init__(self, checkpoint_path: Union[str, Path], *, require_binding: bool = False):
        self.checkpoint_path = Path(checkpoint_path).resolve()
        self.require_binding = require_binding
        self._bound = False
        self._epoch: Optional[str] = None
        self._binding_digest: Optional[str] = None
        self._data: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        return read_checkpoint(self.checkpoint_path) or _empty()

    def _require_access(self) -> None:
        if (self.require_binding or self._data.get("binding") is not None) and not self._bound:
            raise CheckpointIntegrityError("BINDING_REQUIRED", "Checkpoint must be bound before stage access")

    def _current(self) -> dict[str, Any]:
        data = read_checkpoint(self.checkpoint_path)
        if self._bound:
            valid = (data is not None and data.get("version") == VERSION
                     and data.get("epoch") == self._epoch
                     and data.get("binding_digest") == self._binding_digest)
            if not valid:
                raise CheckpointIntegrityError("STALE_BINDING", "Checkpoint changed after binding; bind again")
        elif data is not None and data.get("binding") is not None:
            raise CheckpointIntegrityError("BINDING_REQUIRED", "Bound checkpoint requires explicit binding")
        self._data = data if data is not None else _empty()
        return self._data

    @staticmethod
    def _event(data: dict[str, Any], reason: str, stages: list[str], target_digest: Optional[str]) -> dict[str, Any]:
        return {"event_id": uuid4().hex, "reason": reason,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "previous_version": data.get("version", "1.0.0"),
                "previous_checksum": data.get("checksum"),
                "previous_binding_digest": data.get("binding_digest"),
                "binding_digest": target_digest, "invalidated_stages": stages}

    def _persist(self, data: dict[str, Any]) -> None:
        data["revision"] = data.get("revision", 0) + 1
        self._data = atomic_write_checkpoint(self.checkpoint_path, data)
        self._epoch = self._data["epoch"]

    def bind(self, binding: dict[str, Any], force: bool = False) -> dict[str, Any]:
        """Select exact reusable state, or durably invalidate it with an audit reason.

        Bindings must be non-empty JSON objects. Keys, values, list order, and the
        binding version all participate in equality; object key order does not.
        Corruption is never overridden by force.
        """
        if not isinstance(binding, dict) or not binding:
            raise ValueError("A non-empty checkpoint binding is required")
        safe = json.loads(json.dumps(binding, sort_keys=True, allow_nan=False))
        target_digest = binding_digest(safe)
        with checkpoint_lock(self.checkpoint_path):
            current = read_checkpoint(self.checkpoint_path)
            reason = self._bind_reason(current, target_digest, force)
            if reason == "EXACT_BINDING":
                self._data = current
                invalidated = []
            else:
                previous = current or _empty()
                invalidated = sorted(previous.get("stages", {}))
                replacement = _empty(safe)
                replacement["revision"] = previous.get("revision", 0)
                replacement["invalidations"] = deepcopy(previous.get("invalidations", []))
                replacement["invalidations"].append(self._event(previous, reason, invalidated, target_digest))
                self._persist(replacement)
            self._bound, self._binding_digest = True, target_digest
            self._epoch = self._data["epoch"]
            return {"reason": reason, "reused": reason == "EXACT_BINDING" and bool(self._data["stages"]),
                    "binding_digest": target_digest, "invalidated_stages": invalidated,
                    "revision": self._data["revision"], "epoch": self._epoch,
                    "audit": deepcopy(self._data["invalidations"])}

    @staticmethod
    def _bind_reason(data, target_digest, force):
        if force:
            return "FORCE_INVALIDATION"
        if data is None:
            return "NEW_CHECKPOINT"
        if data.get("version") != VERSION or data.get("binding") is None:
            return "LEGACY_UNBOUND"
        return "EXACT_BINDING" if data["binding_digest"] == target_digest else "BINDING_CHANGED"

    def has_stage(self, stage_name: str) -> bool:
        return self.get_stage(stage_name) is not None

    def get_stage(self, stage_name: str) -> Optional[Dict[str, Any]]:
        self._require_access()
        with checkpoint_lock(self.checkpoint_path):
            return deepcopy(self._current().get("stages", {}).get(stage_name))

    def save_stage(self, stage_name: str, payload: Dict[str, Any]) -> None:
        self._require_access()
        if not isinstance(stage_name, str) or not stage_name or not isinstance(payload, dict):
            raise ValueError("Checkpoint stages require a non-empty name and a dictionary payload")
        safe_payload = self._sanitize(payload)
        observed = deepcopy(self._data.get("stages", {}).get(stage_name))
        with checkpoint_lock(self.checkpoint_path):
            data = self._current()
            stored = data.get("stages", {}).get(stage_name)
            if self._bound and stored != observed and stored != safe_payload:
                raise CheckpointIntegrityError("CONCURRENT_STAGE_CHANGE", "Stage changed since it was observed")
            if data.get("version") != VERSION:
                replacement = _empty()
                replacement["stages"] = deepcopy(data.get("stages", {}))
                replacement["metadata"] = deepcopy(data.get("metadata", {}))
                data = replacement
            data.setdefault("stages", {})[stage_name] = safe_payload
            self._persist(data)

    def invalidate(self, stages: Optional[Iterable[str]] = None, *, reason: str) -> dict[str, Any]:
        """Discard explicit stages (None means all), preserving a durable audit trail."""
        self._require_access()
        if not isinstance(reason, str) or not reason:
            raise ValueError("Checkpoint invalidation requires an audit reason")
        if isinstance(stages, str):
            raise ValueError("Pass a collection of stage names, not a single string")
        with checkpoint_lock(self.checkpoint_path):
            data = self._current()
            if data.get("version") != VERSION:
                raise CheckpointIntegrityError("LEGACY_UNBOUND", "Bind legacy state before invalidation")
            selected = set(data["stages"]) if stages is None else set(stages)
            if any(not isinstance(stage, str) or not stage for stage in selected):
                raise ValueError("Invalidation stages must be non-empty names")
            removed = sorted(selected.intersection(data["stages"]))
            event = self._event(data, reason, removed, data["binding_digest"])
            for stage in removed:
                del data["stages"][stage]
            data["epoch"] = uuid4().hex
            data["invalidations"].append(event)
            self._persist(data)
            return deepcopy(event)

    def clear(self) -> None:
        """Explicit legacy cleanup; document ingestion retains completed checkpoints."""
        try:
            with checkpoint_lock(self.checkpoint_path):
                self._current()
                if self.checkpoint_path.is_file():
                    self.checkpoint_path.unlink()
                    fsync_directory(self.checkpoint_path.parent)
                self._data = _empty()
                self._bound = False
                self._epoch = self._binding_digest = None
        except Exception as exc:
            logger.error("Failed to remove checkpoint file '%s': %s", self.checkpoint_path, exc)
            raise RuntimeError(f"Fail-Fast: Failed to clear checkpoint file '{self.checkpoint_path}': {exc}") from exc

    @classmethod
    def _sanitize(cls, obj: Any) -> Any:
        if isinstance(obj, (str, int, float, bool)) or obj is None:
            return obj
        if isinstance(obj, Path):
            return str(obj)
        if is_dataclass(obj):
            return cls._sanitize(asdict(obj))
        if hasattr(obj, "to_dict"):
            return cls._sanitize(obj.to_dict())
        if isinstance(obj, dict):
            return {str(key): cls._sanitize(value) for key, value in obj.items()}
        if isinstance(obj, (list, tuple, set)):
            return [cls._sanitize(item) for item in obj]
        return str(obj)
