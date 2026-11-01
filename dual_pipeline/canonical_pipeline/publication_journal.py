"""Persistent stop-before-repeat guard around the existing publication writer.

This journal does not implement an exactly-once graph transaction or authorize
cached success. Every prior attempt requires authoritative reconciliation.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Callable

from dual_pipeline.completion_core.constants import DIRECTIVE_ID_PATTERN
from dual_pipeline.core import (
    assert_safe_mutable_path, digest, file_lock, utc_now, write_json, write_json_exclusive,
)

logger = logging.getLogger(__name__)


class PublicationReconciliationRequired(RuntimeError):
    """A prior publication may have mutated the graph; do not execute it again."""

    def __init__(self, directive_id: str, journal: Path, state: str, retry_safe: bool | None = None):
        self.state = state
        is_safe = retry_safe if retry_safe is not None else (state in ("ROLLED_BACK", "PENDING"))
        self.details = {
            "code": "PUBLICATION_RECONCILIATION_REQUIRED",
            "directive_id": directive_id,
            "journal_root": str(journal),
            "prior_state": state,
            "retry_safe": is_safe,
            "reason": (
                "An existing directive has no matching publication journal."
                if state == "LEGACY_DIRECTIVE" else
                ("Publication rolled back cleanly; safe to retry."
                 if (state == "ROLLED_BACK" and is_safe) else
                 "A prior publication cannot be repeated or accepted as current authorization.")
            ),
        }
        super().__init__(
            "PUBLICATION_RECONCILIATION_REQUIRED: "
            f"directive {directive_id!r} has prior state {state}; reconcile its "
            "authoritative directive, activation receipts and current generation "
            "before proceeding. Force rerun cannot clear this guard."
        )


def _sync_directory(path: Path) -> None:
    # File contents are fsynced by write_json_exclusive on all platforms.
    # POSIX additionally supports persisting the new directory entries.
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _store_record(path: Path, payload: dict[str, Any], runtime: Path, overwrite: bool = False) -> None:
    record = {**payload, "record_sha256": digest(payload)}
    if overwrite:
        write_json(path, record)
    else:
        write_json_exclusive(path, record)
    for directory in (path.parent, runtime, runtime.parent):
        _sync_directory(directory)


def _record_state(path: Path) -> str:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        checksum = record.pop("record_sha256")
        if digest(record) != checksum:
            return "CORRUPT_JOURNAL"
        return str(record["state"])
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return "CORRUPT_JOURNAL"


def _is_legacy_directive_completed(legacy: Path) -> bool:
    try:
        leg_data = json.loads(legacy.read_text(encoding="utf-8"))
        return bool(leg_data.get("completion") or leg_data.get("status") == "COMPLETE")
    except Exception:
        return True


def _assert_no_prior_attempt(
    directive_id: str, intent: Path, result: Path, allow_resume: bool = False,
) -> None:
    if not intent.exists() and not result.exists():
        legacy = intent.parent.parent / "ingestion_directives" / f"{directive_id}.json"
        if legacy.exists() and _is_legacy_directive_completed(legacy):
            raise PublicationReconciliationRequired(directive_id, intent.parent, "LEGACY_DIRECTIVE")
        return
    if not result.exists() and intent.exists():
        st = _record_state(intent)
        if st in ("ROLLED_BACK", "PENDING", "AWAITING_APPROVAL"):
            if st in ("PENDING", "AWAITING_APPROVAL") and not allow_resume:
                logger.warning("Reconciling interrupted %s publication intent for directive %s", st, directive_id)
                intent.unlink(missing_ok=True)
                return
            if st == "ROLLED_BACK":
                try:
                    idata = json.loads(intent.read_text(encoding="utf-8"))
                    if not idata.get("zero_delta", True):
                        raise PublicationReconciliationRequired(
                            directive_id, intent.parent, "PARTIAL_MUTATION_ROLLBACK_FAILED", retry_safe=False
                        )
                except (OSError, ValueError) as exc:
                    logger.debug("Rolled-back publication intent parse skipped for %s: %s", directive_id, exc)
                return
            if st in ("PENDING", "AWAITING_APPROVAL") and allow_resume:
                return
    if allow_resume:
        if result.exists() and _record_state(result) == "RESULT_RECORDED":
            return
        if intent.exists() and _record_state(intent) in ("PENDING", "AWAITING_APPROVAL", "ROLLED_BACK"):
            return
    if not intent.exists():
        state = "ORPHANED_RESULT"
    elif result.exists():
        state = _record_state(result)
    else:
        state = _record_state(intent)
    raise PublicationReconciliationRequired(directive_id, intent.parent, state)


def _check_resume_reconciliation(
    directive_id: str, journal: Path, intent: Path, result_path: Path, binding_sha: str
) -> tuple[bool, Any]:
    if result_path.exists() and _record_state(result_path) == "RESULT_RECORDED":
        try:
            res_rec = json.loads(result_path.read_text(encoding="utf-8"))
            if res_rec.get("bindings_sha256") == binding_sha:
                return True, res_rec.get("result")
            res_result = res_rec.get("result") or {}
            res_gen = (
                res_result.get("generation_id")
                or res_result.get("active_pair", {}).get("generation_id")
                or res_result.get("release", {}).get("activation", {}).get("active_pair", {}).get("generation_id")
            )
            if res_gen:
                from dual_pipeline.runtime_root import resolve_active_pair
                curr_pair, _ = resolve_active_pair(journal.parent, enforce_integrity=False)
                if curr_pair and curr_pair.get("generation_id") == res_gen:
                    logger.info("Reusing already activated publication result for generation %s", res_gen)
                    return True, res_rec.get("result")
            raise PublicationReconciliationRequired(directive_id, journal, "RESULT_BINDINGS_MISMATCH")
        except PublicationReconciliationRequired:
            raise
        except Exception as exc:
            logger.debug("Publication result journal check skipped for %s: %s", directive_id, exc)

    if intent.exists():
        try:
            intent_rec = json.loads(intent.read_text(encoding="utf-8"))
            if (
                intent_rec.get("state") not in ("ROLLED_BACK", "AWAITING_APPROVAL")
                and intent_rec.get("bindings_sha256") != binding_sha
            ):
                raise PublicationReconciliationRequired(directive_id, journal, "INTENT_BINDINGS_MISMATCH")
            if intent_rec.get("state") in ("PENDING", "AWAITING_APPROVAL") and not result_path.exists():
                logger.warning("Reconciling interrupted %s publication intent on resume for directive %s", intent_rec.get("state"), directive_id)
                intent.unlink(missing_ok=True)
        except PublicationReconciliationRequired:
            raise
        except Exception as exc:
            logger.debug("Publication intent journal check skipped for %s: %s", directive_id, exc)
    return False, None


def guarded_publication(
    runtime_root: Path,
    directive_id: str,
    bindings_factory: Callable[[], dict[str, Any]],
    publish: Callable[[], Any],
    result_context: Callable[[], dict[str, Any]] | None = None,
    allow_resume: bool = False,
) -> Any:
    """Record intent before calling the sole writer once for this directive.

    The key excludes package paths, force flags and model/settings fingerprints.
    The factory performs read-only preflight and captures exact input bindings
    under the operation lock. All post-intent errors leave a persistent guard.
    Result persistence means the writer returned, not proof of graph activation.
    """
    if not isinstance(directive_id, str) or not DIRECTIVE_ID_PATTERN.fullmatch(directive_id):
        raise ValueError("Publication requires a safe explicit directive_id")
    runtime = Path(runtime_root).resolve()
    journal = runtime / "publication_journal"
    paths = [journal / f"{directive_id}.{suffix}" for suffix in ("lock", "intent.json", "result.json")]
    for path in paths:
        assert_safe_mutable_path(runtime, path, label="publication journal")
    lock, intent, result_path = paths
    with file_lock(lock):
        _assert_no_prior_attempt(directive_id, intent, result_path, allow_resume=allow_resume)
        bindings = bindings_factory()
        # Require a strict JSON snapshot before establishing an attempt.
        frozen = json.loads(json.dumps(bindings, allow_nan=False))
        binding_sha = digest(frozen)

        if allow_resume:
            has_cached, cached_res = _check_resume_reconciliation(
                directive_id, journal, intent, result_path, binding_sha
            )
            if has_cached:
                return cached_res

        try:
            prior_state = _record_state(intent) if intent.exists() else None
            if not (allow_resume and intent.exists() and prior_state in ("PENDING", "AWAITING_APPROVAL")):
                _store_record(intent, {
                    "schema_version": 1, "state": "PENDING", "created_at": utc_now(),
                    "directive_id": directive_id, "bindings": frozen,
                    "bindings_sha256": binding_sha,
                }, runtime, overwrite=bool(intent.exists()))
            result = publish()
            if isinstance(result, dict) and result.get("status") == "AWAITING_APPROVAL":
                _store_record(intent, {
                    "schema_version": 1, "state": "AWAITING_APPROVAL", "created_at": utc_now(),
                    "directive_id": directive_id, "bindings": frozen,
                    "bindings_sha256": binding_sha,
                }, runtime, overwrite=True)
                return result
            _store_record(result_path, {
                "schema_version": 1, "state": "RESULT_RECORDED", "created_at": utc_now(),
                "directive_id": directive_id, "bindings_sha256": binding_sha,
                "intent_sha256": digest(json.loads(intent.read_text(encoding="utf-8"))),
                "result": result, "post_publication": result_context() if result_context else {},
            }, runtime)
        except Exception as exc:
            if not intent.exists():
                raise
            import logging
            logging.getLogger("CanonicalAgenticPipeline.publication_journal").error("guarded_publication failed: %s", exc, exc_info=True)
            st = _record_state(intent)
            is_zero_delta = True
            if st == "ROLLED_BACK":
                try:
                    is_zero_delta = bool(json.loads(intent.read_text(encoding="utf-8")).get("zero_delta", True))
                except Exception:
                    is_zero_delta = True
            raise PublicationReconciliationRequired(
                directive_id, journal, st, retry_safe=(st == "ROLLED_BACK" and is_zero_delta)
            ) from exc
        return result


def get_publication_checkpoint(runtime_root: Path, directive_id: str) -> dict[str, Any]:
    """Inspect the publication journal checkpoint for a directive."""
    runtime = Path(runtime_root).resolve()
    journal = runtime / "publication_journal"
    intent = journal / f"{directive_id}.intent.json"
    result_path = journal / f"{directive_id}.result.json"

    intent_rec = None
    if intent.is_file():
        try:
            intent_rec = json.loads(intent.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            intent_rec = None

    result_rec = None
    if result_path.is_file():
        try:
            result_rec = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            result_rec = None

    intent_state = _record_state(intent) if intent.is_file() else None

    return {
        "directive_id": directive_id,
        "exists": bool(intent.exists() or result_path.exists()),
        "intent_exists": intent.is_file(),
        "intent_state": intent_state,
        "intent_bindings_sha256": (intent_rec or {}).get("bindings_sha256"),
        "result_exists": result_path.is_file(),
        "result_state": _record_state(result_path) if result_path.is_file() else None,
        "result_bindings_sha256": (result_rec or {}).get("bindings_sha256"),
        "retryable": bool(
            not result_path.is_file()
            and (
                not intent.is_file()
                or (intent_state == "ROLLED_BACK" and (intent_rec or {}).get("zero_delta", True))
            )
        ),
    }


def mark_publication_rolled_back(
    runtime_root: Path,
    directive_id: str,
    *,
    reason: str = "Pre-commit or cascade rollback",
    zero_delta: bool = True,
) -> dict[str, Any]:
    """Mark an interrupted/failed publication intent as ROLLED_BACK so it is retryable."""
    runtime = Path(runtime_root).resolve()
    journal = runtime / "publication_journal"
    lock = journal / f"{directive_id}.lock"
    intent = journal / f"{directive_id}.intent.json"
    result_path = journal / f"{directive_id}.result.json"

    with file_lock(lock):
        if not intent.is_file():
            return {"status": "NOOP", "directive_id": directive_id, "reason": "No intent file found"}
        if result_path.is_file() and _record_state(result_path) == "RESULT_RECORDED":
            return {"status": "ERROR", "directive_id": directive_id, "reason": "Cannot roll back committed result"}

        try:
            record = json.loads(intent.read_text(encoding="utf-8"))
            record.pop("record_sha256", None)
            record["state"] = "ROLLED_BACK"
            record["rolled_back_at"] = utc_now()
            record["rollback_reason"] = reason
            record["zero_delta"] = bool(zero_delta)
            record["retryable"] = bool(zero_delta)
            _store_record(intent, record, runtime, overwrite=True)
            return {
                "status": "ROLLED_BACK",
                "directive_id": directive_id,
                "zero_delta": bool(zero_delta),
                "retryable": bool(zero_delta),
            }
        except Exception as exc:
            return {"status": "ERROR", "directive_id": directive_id, "error": str(exc)}


def reconcile_publication_checkpoint(
    runtime_root: Path,
    directive_id: str,
    *,
    action: str = "reconcile",
) -> dict[str, Any]:
    """Reconcile an interrupted publication checkpoint safely without manual file deletion."""
    runtime = Path(runtime_root).resolve()
    journal = runtime / "publication_journal"
    lock = journal / f"{directive_id}.lock"
    intent = journal / f"{directive_id}.intent.json"
    result_path = journal / f"{directive_id}.result.json"

    with file_lock(lock):
        chk = get_publication_checkpoint(runtime, directive_id)
        if action in ("mark_rolled_back", "rollback"):
            return mark_publication_rolled_back(
                runtime, directive_id, reason="Operator/system rollback reconciliation", zero_delta=True
            )
        if action == "reset_interrupted":
            if intent.is_file() and not result_path.is_file() and chk["intent_state"] in ("PENDING", "CORRUPT_JOURNAL", "ROLLED_BACK"):
                archived = journal / f"{directive_id}.intent.interrupted_{utc_now().replace(':', '-')}.json"
                intent.rename(archived)
                return {
                    "status": "RECONCILED",
                    "action": action,
                    "directive_id": directive_id,
                    "archived_path": str(archived),
                    "prior_state": chk["intent_state"],
                }
            return {
                "status": "NOOP",
                "action": action,
                "directive_id": directive_id,
                "reason": "Checkpoint not in an interrupted reset-eligible state",
                "checkpoint": chk,
            }
        return {
            "status": "RECONCILED",
            "action": action,
            "directive_id": directive_id,
            "checkpoint": chk,
        }

