"""Persistent ledger tracking GraphDB mutations, package preparation, and scheduled scout cadence."""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .core import ROOT, file_lock, read_json, utc_now

logger = logging.getLogger(__name__)

DEFAULT_LEDGER_PATH = ROOT / ".local" / "dual_pipeline" / "graph_mutation_ledger.json"


from .storage.governed_context import ProductionPurityViolationError


class GraphMutationLedger:
    """Manages the cumulative count and audit history of graph mutations and package preparations."""

    def __init__(self, ledger_path: Optional[Path] = None):
        self.ledger_path = (
            Path(ledger_path).resolve()
            if ledger_path is not None
            else DEFAULT_LEDGER_PATH.resolve()
        )

    def _is_production_ledger(self) -> bool:
        """Check if this ledger resolves to the canonical repository production ledger."""
        try:
            p_str = str(self.ledger_path.resolve())
            if p_str.startswith("/private/Users/"):
                p_str = p_str[len("/private"):]
            def_str = str(DEFAULT_LEDGER_PATH.resolve())
            if def_str.startswith("/private/Users/"):
                def_str = def_str[len("/private"):]
            return p_str == def_str
        except Exception:
            return False

    def _check_production_protection(self) -> None:
        """Enforce mechanical barrier against unauthorized mutation of production ledger."""
        if self._is_production_ledger():
            raise ProductionPurityViolationError(
                f"Mechanical barrier: Production mutation ledger at '{self.ledger_path}' cannot be mutated directly."
            )

    def _load(self) -> Dict[str, Any]:
        """Load ledger state or initialize default schema 1.1.0."""
        default_data: Dict[str, Any] = {
            "schema_version": "1.1.0",
            "cumulative_mutation_count": 0,
            "cumulative_preparation_count": 0,
            "last_mutation_timestamp": None,
            "last_preparation_timestamp": None,
            "last_full_scan_timestamp": None,
            "last_full_scan_mutation_count": 0,
            "physical_runtime_path": str(self.ledger_path.parent.resolve()),
            "mutation_history": [],
            "preparation_history": [],
            "activation_history": [],
        }
        if self.ledger_path.is_file():
            try:
                data = read_json(self.ledger_path)
                if isinstance(data, dict):
                    # Ensure backward compatibility with 1.0.0
                    for k, v in default_data.items():
                        if k not in data:
                            data[k] = v
                    data["schema_version"] = "1.1.0"
                    data["physical_runtime_path"] = str(self.ledger_path.parent.resolve())
                    return data
            except Exception as exc:
                logger.warning("Could not read mutation ledger at %s: %s", self.ledger_path, exc)

        return default_data

    def _lock_path(self) -> Path:
        return self.ledger_path.with_suffix(".json.lock")

    def _atomic_write(self, data: Dict[str, Any]) -> None:
        """Write ledger to disk atomically (tmp -> fsync -> os.replace). Caller holds lock."""
        import json as _json

        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        payload = (_json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
        tmp_path = self.ledger_path.with_suffix(".json.tmp")
        with tmp_path.open("wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, self.ledger_path)

    # Backward-compatible alias for existing tests calling _save directly
    _save = _atomic_write

    def record_package_preparation(
        self,
        run_id: str,
        proposed_node_count: int = 0,
        proposed_assertion_count: int = 0,
        target_graph_path: Optional[Path | str] = None,
        runtime_root: Optional[Path | str] = None,
        generation_id: Optional[str] = None,
    ) -> int:
        """Record package preparation WITHOUT claiming committed database mutations."""
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._lock_path()
        resolved_graph = str(Path(target_graph_path).resolve()) if target_graph_path else None
        resolved_root = str(Path(runtime_root).resolve()) if runtime_root else str(self.ledger_path.parent.resolve())
        now_iso = utc_now()

        event = {
            "run_id": run_id,
            "timestamp": now_iso,
            "event_type": "PREPARED_PACKAGE",
            "proposed_node_count": int(proposed_node_count),
            "proposed_assertion_count": int(proposed_assertion_count),
            "target_graph_path": resolved_graph,
            "runtime_root": resolved_root,
            "generation_id": generation_id,
        }

        with file_lock(lock_path):
            data = self._load()
            data["cumulative_preparation_count"] = int(data.get("cumulative_preparation_count", 0)) + 1
            data["last_preparation_timestamp"] = now_iso
            history: List[Dict[str, Any]] = data.get("preparation_history", [])
            history.append(event)
            data["preparation_history"] = history[-100:]
            self._atomic_write(data)

        logger.info(
            "GraphMutationLedger: recorded package preparation '%s'. Cumulative preparations: %d",
            run_id,
            data["cumulative_preparation_count"],
        )
        return data["cumulative_preparation_count"]

    def record_mutation(
        self,
        run_id: str,
        node_count: int = 0,
        assertion_count: int = 0,
        target_graph_path: Optional[Path | str] = None,
        runtime_root: Optional[Path | str] = None,
        generation_id: Optional[str] = None,
    ) -> int:
        """Record a committed graph mutation and increment the counter."""
        self._check_production_protection()

        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._lock_path()
        resolved_graph = str(Path(target_graph_path).resolve()) if target_graph_path else None
        resolved_root = str(Path(runtime_root).resolve()) if runtime_root else str(self.ledger_path.parent.resolve())
        now_iso = utc_now()

        event = {
            "run_id": run_id,
            "timestamp": now_iso,
            "event_type": "COMMITTED_MUTATION",
            "node_count": int(node_count),
            "assertion_count": int(assertion_count),
            "target_graph_path": resolved_graph,
            "runtime_root": resolved_root,
            "generation_id": generation_id,
        }

        with file_lock(lock_path):
            data = self._load()
            data["cumulative_mutation_count"] = int(data.get("cumulative_mutation_count", 0)) + 1
            data["last_mutation_timestamp"] = now_iso
            history: List[Dict[str, Any]] = data.get("mutation_history", [])
            history.append(event)
            data["mutation_history"] = history[-100:]
            self._atomic_write(data)

        logger.info(
            "GraphMutationLedger: recorded mutation '%s'. Cumulative count: %d",
            run_id,
            data["cumulative_mutation_count"],
        )
        return data["cumulative_mutation_count"]

    def record_release_activation(
        self,
        run_id: str,
        generation_id: str,
        graph_sha256: Optional[str] = None,
        target_graph_path: Optional[Path | str] = None,
        runtime_root: Optional[Path | str] = None,
    ) -> int:
        """Record an activated release in the ledger."""
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._lock_path()
        resolved_graph = str(Path(target_graph_path).resolve()) if target_graph_path else None
        resolved_root = str(Path(runtime_root).resolve()) if runtime_root else str(self.ledger_path.parent.resolve())
        now_iso = utc_now()

        event = {
            "run_id": run_id,
            "timestamp": now_iso,
            "event_type": "ACTIVATED_RELEASE",
            "generation_id": generation_id,
            "graph_sha256": graph_sha256,
            "target_graph_path": resolved_graph,
            "runtime_root": resolved_root,
        }

        with file_lock(lock_path):
            data = self._load()
            history: List[Dict[str, Any]] = data.get("activation_history", [])
            history.append(event)
            data["activation_history"] = history[-100:]
            self._atomic_write(data)

        logger.info(
            "GraphMutationLedger: recorded release activation '%s' (gen: %s)",
            run_id,
            generation_id,
        )
        return len(data["activation_history"])

    def detect_shared_runtime(self, other_path: Path | str) -> bool:
        """Detect if another path resolves to the same physical runtime directory."""
        p_other = Path(other_path).resolve()
        my_dir = str(self.ledger_path.parent.resolve())
        other_dir = str(p_other if p_other.is_dir() else p_other.parent.resolve())
        if my_dir.startswith("/private/Users/"):
            my_dir = my_dir[len("/private"):]
        if other_dir.startswith("/private/Users/"):
            other_dir = other_dir[len("/private"):]
        return my_dir == other_dir

    def get_total_mutations(self) -> int:
        """Return the current cumulative mutation count."""
        data = self._load()
        return int(data.get("cumulative_mutation_count", 0))

    def get_total_preparations(self) -> int:
        """Return the current cumulative package preparation count."""
        data = self._load()
        return int(data.get("cumulative_preparation_count", 0))

    def get_total_activations(self) -> int:
        """Return the current cumulative release activation count."""
        data = self._load()
        return len(data.get("activation_history", []))

    def get_last_mutation(self) -> Optional[Dict[str, Any]]:
        """Return the most recent committed mutation record."""
        data = self._load()
        history = data.get("mutation_history", [])
        return history[-1] if history else None

    def get_last_preparation(self) -> Optional[Dict[str, Any]]:
        """Return the most recent package preparation record."""
        data = self._load()
        history = data.get("preparation_history", [])
        return history[-1] if history else None

    def get_last_activation(self) -> Optional[Dict[str, Any]]:
        """Return the most recent release activation record."""
        data = self._load()
        history = data.get("activation_history", [])
        return history[-1] if history else None

    def should_trigger_scheduled_scout(
        self,
        cadence_mutations: int = 10,
        cadence_days: int = 7,
    ) -> bool:
        """Check if scheduled full-graph health scout is due."""
        data = self._load()
        current_count = int(data.get("cumulative_mutation_count", 0))
        last_scan_count = int(data.get("last_full_scan_mutation_count", 0))

        # Check mutation cadence delta
        mutations_since_last = current_count - last_scan_count
        if mutations_since_last >= cadence_mutations:
            return True

        # Check time cadence
        last_scan_ts = data.get("last_full_scan_timestamp")
        if last_scan_ts:
            try:
                dt_last = datetime.fromisoformat(last_scan_ts.replace("Z", "+00:00"))
                dt_now = datetime.now(timezone.utc)
                if (dt_now - dt_last).total_seconds() >= cadence_days * 86400:
                    return True
            except Exception:
                pass
        elif current_count > 0 and mutations_since_last > 0:
            # If never scanned before and there are mutations
            return True

        return False

    def record_full_scan(self, run_id: str) -> None:
        """Record that a full-graph scheduled scout run has completed."""
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._lock_path()
        now_iso = utc_now()
        with file_lock(lock_path):
            data = self._load()
            data["last_full_scan_timestamp"] = now_iso
            data["last_full_scan_mutation_count"] = int(data.get("cumulative_mutation_count", 0))
            self._atomic_write(data)

        logger.info(
            "GraphMutationLedger: recorded full scout scan '%s' at mutation count %d",
            run_id,
            data["last_full_scan_mutation_count"],
        )


