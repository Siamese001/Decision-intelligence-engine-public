"""Durable per-run evidence independent of replaceable deliverable directories."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


def write_immutable_evidence(path: Path, value: dict[str, Any]) -> None:
    payload = json.loads(json.dumps(value, default=str))
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    payload["evidence_digest"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


@contextmanager
def durable_execution_scope(tracker: Any, runtime_root: Path) -> Iterator[None]:
    """Retain every completed attempt and terminal run, including BaseException exits.

    Timing starts after project preflight and lock acquisition. Queue/lock waiting
    and initial metadata setup are explicitly outside this measurement boundary.
    """
    directory = runtime_root / "executions" / tracker.timing.run_id
    directory.mkdir(parents=True, exist_ok=False)
    tracker.timing.evidence_directory = directory
    tracker.metadata["execution_evidence_path"] = str(directory / "pipeline_execution_receipt.json")
    tracker.metadata["authoritative_receipt_path"] = str(directory / "pipeline_execution_receipt.json")
    try:
        with tracker.execution_scope():
            yield
    except BaseException:
        try:
            _persist_run(tracker, directory, "FAILED")
        except Exception:
            logging.getLogger(__name__).exception("Unable to persist failed run evidence")
        raise
    else:
        _persist_run(tracker, directory, tracker.timing.run_span.status)


def _collect_deliverable_targets(metadata: dict[str, Any]) -> list[Path]:
    deliverable_targets: list[Path] = []
    if metadata.get("deliverable_receipt_path"):
        deliverable_targets.append(Path(metadata["deliverable_receipt_path"]))
    if metadata.get("deliverable_dir"):
        deliverable_targets.append(Path(metadata["deliverable_dir"]) / "pipeline_execution_receipt.json")
    if metadata.get("output_dir"):
        out_p = Path(metadata["output_dir"])
        if out_p.is_dir():
            deliverable_targets.append(out_p / "pipeline_execution_receipt.json")
    return deliverable_targets


def _deliver_finalized_receipt(receipt: dict[str, Any], tracker: Any) -> None:
    for target in _collect_deliverable_targets(tracker.metadata):
        if target.parent.is_dir():
            try:
                with target.open("w", encoding="utf-8") as f:
                    json.dump(receipt, f, indent=2, ensure_ascii=False)
                    f.write("\n")
                    f.flush()
                    os.fsync(f.fileno())
            except Exception as write_err:
                logging.getLogger(__name__).warning("Unable to publish finalized receipt to %s: %s", target, write_err)


def _persist_run(tracker: Any, directory: Path, outcome: str) -> None:
    if "source_evidence" not in tracker.metadata:
        try:
            from dual_pipeline.canonical_pipeline.evidence_capture import snapshot_run_artifacts
            snapshot_run_artifacts(tracker, directory, directory, tracker.metadata, None)
        except Exception:
            pass
    receipt = tracker.build_receipt(directory, terminal_outcome=outcome).to_dict()
    receipt["measurement_state"] = "FINAL"
    receipt["is_preterminal_snapshot"] = False
    receipt["authoritative_receipt_path"] = str(directory / "pipeline_execution_receipt.json")
    receipt["authoritative_run_id"] = tracker.timing.run_id
    receipt["measurement_scope"] = {
        "start": "execution scope after preflight and lock acquisition",
        "end": "execution scope exit after publication and compliance checks",
        "excluded": ["metadata setup", "project preflight", "lock waiting",
                     "evidence directory setup", "terminal receipt persistence"],
        "start_state": tracker.metadata.get("start_state", "unclassified"),
        "duration_accounting": "inclusive; do not sum nested spans",
    }
    write_immutable_evidence(directory / "pipeline_execution_receipt.json", receipt)
    _deliver_finalized_receipt(receipt, tracker)


def _load_receipt_payload(receipt_source: str | Path | dict[str, Any]) -> dict[str, Any]:
    if isinstance(receipt_source, (str, Path)):
        p = Path(receipt_source)
        if not p.is_file():
            raise FileNotFoundError(f"Receipt file not found: {p}")
        return json.loads(p.read_text(encoding="utf-8"))
    return dict(receipt_source)


def _try_resolve_linked_authoritative(payload: dict[str, Any]) -> dict[str, Any] | None:
    auth_path = payload.get("authoritative_receipt_path")
    if not auth_path:
        auth_path = payload.get("execution_timing", {}).get("authoritative_receipt_path")
    if not auth_path:
        return None
    target = Path(auth_path)
    if not target.is_file():
        return None
    authoritative = json.loads(target.read_text(encoding="utf-8"))
    if authoritative.get("measurement_state") == "FINAL":
        return authoritative
    if authoritative.get("execution_timing", {}).get("measurement_state") == "FINAL":
        return authoritative
    return None


def _validate_receipt_finality(payload: dict[str, Any]) -> None:
    timing = payload.get("execution_timing", {})
    state = payload.get("measurement_state") or timing.get("measurement_state")
    finished_at = payload.get("ended_at") or timing.get("finished_at")

    if bool(payload.get("is_preterminal_snapshot")) or state in ("SNAPSHOT", "RUNNING") or finished_at is None:
        raise ValueError(
            "PRETERMINAL_SNAPSHOT_REJECTED: Pre-finalization snapshot cannot be presented as final receipt. "
            "Delivered references must resolve to an authoritative finalized receipt with finished timestamp and positive duration."
        )

    duration = payload.get("monotonic_duration_s")
    if duration is None:
        duration = timing.get("duration_ms", 0.0) / 1000.0

    if duration is not None and duration <= 0.0 and payload.get("terminal_outcome") in ("COMPLETE", "PASS"):
        raise ValueError("INVALID_RECEIPT: Terminal receipt must have positive elapsed duration when work occurred.")


def resolve_authoritative_receipt(
    receipt_source: str | Path | dict[str, Any], require_final: bool = True
) -> dict[str, Any]:
    """Resolves delivered receipt copies or references to the authoritative finalized execution record.

    Delivered copies or references cannot present a pre-finalization snapshot as final.
    """
    payload = _load_receipt_payload(receipt_source)
    authoritative = _try_resolve_linked_authoritative(payload)
    if authoritative is not None:
        return authoritative

    _validate_receipt_finality(payload)
    return payload

