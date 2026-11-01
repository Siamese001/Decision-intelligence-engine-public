"""Deferred artifact writer for query pipeline performance optimization.

Batches non-critical intermediate stage artifact writes during query execution,
eliminating synchronous disk I/O latency while preserving all audit invariants.
"""
from __future__ import annotations

import concurrent.futures
import threading
from pathlib import Path
from typing import Any, Mapping

from dual_pipeline.core import read_json, write_json

__all__ = [
    "DeferredArtifactWriter",
    "mirror_artifacts_to_output_dir",
]


class DeferredArtifactWriter:
    """Thread-safe batch writer for query execution artifacts.

    Accumulates intermediate stage artifacts in memory during query execution
    and flushes them in a single batch, eliminating dozens of per-stage
    synchronous file open/write calls.
    """

    def __init__(self, run_dir: Path | str, *, auto_mkdir: bool = True) -> None:
        self.run_dir = Path(run_dir).resolve()
        if auto_mkdir:
            self.run_dir.mkdir(parents=True, exist_ok=True)
        self._staged_json: dict[Path, Any] = {}
        self._staged_text: dict[Path, tuple[str, str]] = {}
        self._written: set[Path] = set()
        self._lock = threading.Lock()
        self._created_parents: set[Path] = {self.run_dir}

    def _resolve_path(self, path_or_name: Path | str) -> Path:
        p = Path(path_or_name)
        if not p.is_absolute():
            return (self.run_dir / p).resolve()
        return p.resolve()

    def stage(self, path_or_name: Path | str, payload: Any) -> Path:
        """Stage a JSON artifact in memory for deferred writing."""
        target = self._resolve_path(path_or_name)
        with self._lock:
            self._staged_json[target] = payload
            self._staged_text.pop(target, None)
        return target

    def stage_text(
        self,
        path_or_name: Path | str,
        text: str,
        encoding: str = "utf-8",
    ) -> Path:
        """Stage a text/markdown artifact in memory for deferred writing."""
        target = self._resolve_path(path_or_name)
        with self._lock:
            self._staged_text[target] = (text, encoding)
            self._staged_json.pop(target, None)
        return target

    def write_now(self, path_or_name: Path | str, payload: Any) -> Path:
        """Synchronously write a critical JSON artifact immediately."""
        target = self._resolve_path(path_or_name)
        with self._lock:
            parent = target.parent
            if parent not in self._created_parents:
                parent.mkdir(parents=True, exist_ok=True)
                self._created_parents.add(parent)
            write_json(target, payload)
            self._staged_json.pop(target, None)
            self._written.add(target)
        return target

    def write_text_now(
        self,
        path_or_name: Path | str,
        text: str,
        encoding: str = "utf-8",
    ) -> Path:
        """Synchronously write a critical text/markdown artifact immediately."""
        target = self._resolve_path(path_or_name)
        with self._lock:
            parent = target.parent
            if parent not in self._created_parents:
                parent.mkdir(parents=True, exist_ok=True)
                self._created_parents.add(parent)
            target.write_text(text, encoding=encoding)
            self._staged_text.pop(target, None)
            self._written.add(target)
        return target

    def get(self, path_or_name: Path | str) -> Any | None:
        """Transparent in-memory readback of a staged or written artifact."""
        target = self._resolve_path(path_or_name)
        with self._lock:
            if target in self._staged_json:
                return self._staged_json[target]
            if target in self._staged_text:
                return self._staged_text[target][0]
        if target.is_file():
            try:
                return read_json(target)
            except Exception:
                return target.read_text(encoding="utf-8")
        return None

    def has(self, path_or_name: Path | str) -> bool:
        """Check if an artifact is staged in memory or exists on disk."""
        target = self._resolve_path(path_or_name)
        with self._lock:
            if target in self._staged_json or target in self._staged_text:
                return True
        return target.is_file()

    def flush(self, *, parallel: bool = False, max_workers: int = 4) -> int:
        """Write all pending staged artifacts to disk in a single batch.

        Returns the number of artifacts written.
        """
        with self._lock:
            pending_json = list(self._staged_json.items())
            pending_text = list(self._staged_text.items())
            self._staged_json.clear()
            self._staged_text.clear()

        if not pending_json and not pending_text:
            return 0

        # Ensure parent directories exist
        parents_to_create = {path.parent for path, _ in pending_json} | {
            path.parent for path, _ in pending_text
        }
        for parent in parents_to_create:
            if parent not in self._created_parents:
                parent.mkdir(parents=True, exist_ok=True)
                self._created_parents.add(parent)

        written_count = 0

        def _write_one_json(item: tuple[Path, Any]) -> Path:
            path, payload = item
            write_json(path, payload)
            return path

        def _write_one_text(item: tuple[Path, tuple[str, str]]) -> Path:
            path, (text, enc) = item
            path.write_text(text, encoding=enc)
            return path

        total_items = len(pending_json) + len(pending_text)
        if parallel and total_items > 4:
            from config.request_deadline import remaining_timeout
            t_budget = remaining_timeout(30.0)
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
                json_futures = [pool.submit(_write_one_json, item) for item in pending_json]
                text_futures = [pool.submit(_write_one_text, item) for item in pending_text]
                for fut in concurrent.futures.as_completed(json_futures + text_futures, timeout=t_budget):
                    path = fut.result(timeout=t_budget)
                    with self._lock:
                        self._written.add(path)
                    written_count += 1
        else:
            for item in pending_json:
                path = _write_one_json(item)
                with self._lock:
                    self._written.add(path)
                written_count += 1
            for t_item in pending_text:
                path = _write_one_text(t_item)
                with self._lock:
                    self._written.add(path)
                written_count += 1

        return written_count

    def flush_async(
        self,
        executor: concurrent.futures.Executor | None = None,
    ) -> concurrent.futures.Future[int]:
        """Flush pending artifacts asynchronously, returning a Future."""
        if executor is None:
            pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            future = pool.submit(self.flush)
            pool.shutdown(wait=False)
            return future
        return executor.submit(self.flush)

    @property
    def pending_count(self) -> int:
        """Count of staged artifacts pending disk write."""
        with self._lock:
            return len(self._staged_json) + len(self._staged_text)

    @property
    def written_count(self) -> int:
        """Count of artifacts written to disk through this writer."""
        with self._lock:
            return len(self._written)

    @property
    def staged_paths(self) -> list[Path]:
        """List of target paths currently staged in memory."""
        with self._lock:
            return sorted(set(self._staged_json.keys()) | set(self._staged_text.keys()))

    @property
    def is_flushed(self) -> bool:
        """True if no pending artifacts remain unstaged."""
        return self.pending_count == 0


def mirror_artifacts_to_output_dir(
    output_dir: Path | str,
    artifacts: Mapping[str, Any],
    *,
    text_artifacts: Mapping[str, str] | None = None,
    writer: DeferredArtifactWriter | None = None,
) -> None:
    """Mirror artifacts to output_dir, staging if writer is provided or writing directly."""
    target_dir = Path(output_dir).resolve()
    if writer is not None:
        for filename, payload in artifacts.items():
            if payload is not None:
                writer.stage(target_dir / filename, payload)
        if text_artifacts:
            for filename, text in text_artifacts.items():
                if text is not None:
                    writer.stage_text(target_dir / filename, text)
    else:
        target_dir.mkdir(parents=True, exist_ok=True)
        for filename, payload in artifacts.items():
            if payload is not None:
                write_json(target_dir / filename, payload)
        if text_artifacts:
            for filename, text in text_artifacts.items():
                if text is not None:
                    (target_dir / filename).write_text(text, encoding="utf-8")

