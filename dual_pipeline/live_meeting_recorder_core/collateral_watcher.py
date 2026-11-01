"""
dual_pipeline/live_meeting_recorder_core/collateral_watcher.py

Lightweight filesystem watcher polling referenced_artifacts/ during live recording.
Detects dropped slide decks, architectural diagrams, and agendas, annotating elapsed
timestamps for multi-modal evidence correlation.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.collateral_watcher")


class CollateralFsWatcher:
    """
    Lightweight filesystem watcher that polls referenced_artifacts/ every 2 seconds.
    When a new file appears, it records the current session elapsed_seconds and
    stores a CollateralDrop entry. Serializes collateral_drop_log.json on stop().
    """

    POLL_INTERVAL: float = 2.0

    def __init__(
        self,
        ref_dir: Path,
        get_elapsed_fn: Any,
        log_path: Optional[Path] = None,
        on_drop_callback: Optional[Any] = None,
    ) -> None:
        """Args:
            ref_dir: Path to the referenced_artifacts/ directory to watch.
            get_elapsed_fn: Zero-arg callable returning current session elapsed seconds (float).
            log_path: Optional path to collateral_drop_log.json for immediate serialization.
            on_drop_callback: Optional callback invoked on each new collateral drop.
        """
        self._ref_dir = Path(ref_dir).resolve()
        self._get_elapsed = get_elapsed_fn
        self._log_path = Path(log_path).resolve() if log_path else None
        self._on_drop_callback = on_drop_callback
        self._seen: Dict[str, tuple[int, int]] = {}
        self._drops: List[Dict[str, Any]] = []
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def _elapsed_to_hms(self, seconds: float) -> str:
        s = int(seconds)
        return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"

    def _scan(self) -> None:
        """Scans for new or modified files and records timestamped CollateralDrop entries."""
        if not self._ref_dir.is_dir():
            return
        new_found = False
        for f in sorted(self._ref_dir.iterdir()):
            if not f.is_file() or f.name.startswith("."):
                continue
            try:
                st = f.stat()
                sig = (int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))), int(st.st_size))
            except OSError:
                continue
            if self._seen.get(f.name) == sig:
                continue
            is_update = f.name in self._seen
            self._seen[f.name] = sig
            try:
                sha = hashlib.sha256(f.read_bytes()).hexdigest()
            except OSError:
                sha = ""
            if is_update:
                for existing in self._drops:
                    if existing.get("filename") == f.name:
                        existing["sha256"] = sha
                        existing["drop_event_id"] = f"{existing['drop_event_id'].rsplit('-', 1)[0]}-{sha[:8]}"
                        new_found = True
                        break
                continue
            elapsed_hms = self._elapsed_to_hms(self._get_elapsed())
            drop_event_id = f"drop-{len(self._drops) + 1:04d}-{sha[:8]}"
            drop_entry = {
                "drop_event_id": drop_event_id,
                "filename": f.name,
                "dropped_at_elapsed": elapsed_hms,
                "sha256": sha,
                "attribution_mode": "COLLATERAL_REVIEWED",
                "source": "live_drop",
            }
            self._drops.append(drop_entry)
            new_found = True
            logger.info(
                "Collateral drop detected: %s at elapsed %s", f.name, elapsed_hms
            )
            if self._on_drop_callback:
                with contextlib.suppress(Exception):
                    self._on_drop_callback(drop_entry)

        if new_found and self._log_path and self._drops:
            try:
                tmp_p = self._log_path.with_suffix(".tmp")
                tmp_p.write_text(
                    json.dumps(self._drops, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                os.replace(tmp_p, self._log_path)
            except OSError as exc:
                logger.warning("Failed to immediately write collateral_drop_log.json: %s", exc)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._scan()
            self._stop_event.wait(timeout=self.POLL_INTERVAL)
        self._scan()  # final scan on stop

    def start(self) -> None:
        """Starts the background polling thread idempotently."""
        if self._thread is not None and self._thread.is_alive() and not self._stop_event.is_set():
            return
        self._stop_event.clear()
        self._ref_dir.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="collateral_fs_watcher"
        )
        self._thread.start()

    def stop(self, log_path: Optional[Path] = None) -> List[Dict[str, Any]]:
        """Stops polling and optionally serializes collateral_drop_log.json."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        if log_path and self._drops:
            try:
                log_path.write_text(
                    json.dumps(self._drops, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                logger.info("collateral_drop_log.json written: %d drops", len(self._drops))
            except OSError as exc:
                logger.warning("Failed to write collateral_drop_log.json: %s", exc)
        return list(self._drops)

    @property
    def drops(self) -> List[Dict[str, Any]]:
        return list(self._drops)

    def repoint(self, new_ref_dir: Path, new_log_path: Optional[Path] = None) -> None:
        """Migrates watcher to a new referenced_artifacts directory and drop log destination."""
        old_dir = self._ref_dir
        self._ref_dir = Path(new_ref_dir).resolve()
        self._ref_dir.mkdir(parents=True, exist_ok=True)
        if new_log_path:
            self._log_path = Path(new_log_path).resolve()
            self._log_path.parent.mkdir(parents=True, exist_ok=True)

        if old_dir != self._ref_dir and old_dir.is_dir():
            import shutil
            for f in old_dir.iterdir():
                if f.is_file() and not f.name.startswith("."):
                    dest = self._ref_dir / f.name
                    if not dest.exists():
                        try:
                            shutil.copy2(f, dest)
                        except OSError as exc:
                            logger.warning("Failed migrating dropped collateral %s: %s", f.name, exc)

        if self._log_path and self._drops:
            try:
                self._log_path.write_text(
                    json.dumps(self._drops, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            except OSError as exc:
                logger.warning("Failed flushing drops to new log path %s: %s", self._log_path, exc)
