"""Delivery status projection for IngestionCompletionGuard."""
from __future__ import annotations

from pathlib import Path
from typing import Any, TYPE_CHECKING

from ..contracts import validate_artifact
from ..core import file_lock, read_json
from ..release import _verify_activation_binding, _verify_receipt
from .constants import _BGE_M3_BACKENDS

if TYPE_CHECKING:
    from .guard import IngestionCompletionGuard


def _make_status_payload(
    directive_id: str,
    directive: dict[str, Any],
    active_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    active_pair = (
        {
            "generation_id": active_summary["generation_id"],
            "graph_sha256": active_summary["graph_sha256"],
            "sidecar_sha256": active_summary["sidecar_sha256"],
            "embedding_backend": active_summary["embedding_backend"],
            "model_id": active_summary["model_id"],
            "model_revision": active_summary["model_revision"],
        }
        if active_summary is not None
        else None
    )
    return {
        "schema_version": "1.0.0",
        "directive_id": directive_id,
        "directive_status": directive["status"],
        "terminal": directive["terminal"],
        "package_set_digest": directive["package_set_digest"],
        "approval_id": (
            directive["approval"]["approval_id"]
            if directive.get("approval") is not None
            else None
        ),
        "active_pair": active_pair,
        "release": None,
        "sidecar": None,
        "retrieval_evaluation": None,
        "activation": None,
        "completion": directive.get("completion"),
        "delivery_state": None,
        "blocker": None,
        "next_action": None,
    }


def _find_failed_evaluations(
    failed_root: Path,
    receipt: dict[str, Any],
) -> tuple[dict[str, Any] | None, Path | None, list[str]]:
    failed_paths = (
        sorted(
            failed_root.glob(
                f"retrieval-release-evaluation-{receipt['receipt_id']}-*.json"
            )
        )
        if failed_root.is_dir()
        else []
    )
    valid_failures: list[tuple[dict[str, Any], Path]] = []
    invalid_failures: list[str] = []
    for failed_path in failed_paths:
        try:
            failed = read_json(failed_path)
            validate_artifact(failed)
            if (
                failed.get("contract_id")
                != "urn:nee:dual-pipeline:retrieval-release-evaluation:1.0.0"
                or failed.get("status") != "FAIL"
                or failed.get("prepared_receipt_id") != receipt["receipt_id"]
                or failed.get("prepared_receipt_digest") != receipt["receipt_digest"]
            ):
                raise ValueError("failed evaluation receipt binding is invalid")
            valid_failures.append((failed, failed_path))
        except (OSError, TypeError, ValueError) as exc:
            invalid_failures.append(f"{failed_path}: {exc}")
    if valid_failures:
        failed, failed_path = max(
            valid_failures,
            key=lambda item: (
                item[0]["evaluated_at"],
                item[0]["evaluation_id"],
            ),
        )
        return failed, failed_path, invalid_failures
    return None, None, invalid_failures


def _inspect_retrieval_evaluation(
    guard: IngestionCompletionGuard,
    receipt: dict[str, Any],
    next_pair: dict[str, Any],
    result: dict[str, Any],
) -> bool:
    """Evaluate retrieval release evaluation requirement and state. Returns True if passed, False if blocked."""
    if next_pair["embedding_backend"] not in _BGE_M3_BACKENDS:
        return True

    evaluation_path = (
        guard.runtime_root
        / "retrieval_evaluations"
        / (f"retrieval-release-evaluation-{receipt['receipt_id']}.json")
    )
    result["retrieval_evaluation"] = {
        "required": True,
        "path": str(evaluation_path),
        "exists": evaluation_path.is_file(),
    }
    if not evaluation_path.is_file():
        failed_root = guard.runtime_root / "retrieval_evaluations" / "failed"
        failed, failed_path, invalid_failures = _find_failed_evaluations(failed_root, receipt)
        if failed is not None:
            result["retrieval_evaluation"].update(
                {
                    "latest_failed_evaluation_id": failed["evaluation_id"],
                    "latest_failed_evaluation_digest": failed["canonical_digest"]["value"],
                    "latest_failed_evaluation_path": str(failed_path),
                    "latest_failed_gates": failed["gates"],
                }
            )
            result["delivery_state"] = "EVALUATION_FAILED"
            result["blocker"] = "The latest receipt-bound retrieval evaluation failed."
            result["next_action"] = "Correct the candidate release or governed suite, then rerun the receipt-bound evaluation."
            return False
        if invalid_failures:
            result["delivery_state"] = "EVALUATION_INVALID"
            result["blocker"] = "Failed evaluation evidence exists but is invalid."
            result["invalid_failed_evaluations"] = invalid_failures
            result["next_action"] = "Repair the evaluation evidence path before activation."
            return False
        result["delivery_state"] = "EVALUATION_REQUIRED"
        result["blocker"] = "Prepared BGE-M3 release has no sealed retrieval evaluation."
        result["next_action"] = "Run the receipt-bound retrieval release evaluation."
        return False

    try:
        evaluation = read_json(evaluation_path)
        validate_artifact(evaluation)
    except (OSError, TypeError, ValueError) as exc:
        result["delivery_state"] = "EVALUATION_INVALID"
        result["blocker"] = f"Retrieval evaluation exists but is not a valid sealed artifact: {exc}"
        result["next_action"] = "Regenerate the receipt-bound retrieval evaluation; do not activate this prepared release."
        return False

    if (
        evaluation.get("prepared_receipt_id") != receipt["receipt_id"]
        or evaluation.get("prepared_receipt_digest") != receipt["receipt_digest"]
    ):
        result["delivery_state"] = "EVALUATION_BINDING_INVALID"
        result["blocker"] = "Retrieval evaluation is not bound to the prepared release."
        result["next_action"] = "Regenerate the retrieval evaluation for this receipt."
        return False

    result["retrieval_evaluation"].update(
        {
            "evaluation_id": evaluation["evaluation_id"],
            "digest": evaluation["canonical_digest"]["value"],
            "status": evaluation["status"],
            "gates": evaluation["gates"],
        }
    )
    if evaluation["status"] != "PASS" or not all(evaluation["gates"].values()):
        result["delivery_state"] = "EVALUATION_FAILED"
        result["blocker"] = "Retrieval evaluation did not pass every activation gate."
        result["next_action"] = "Correct the prepared release or suite failure, then create a new release."
        return False

    return True


def _find_approval_matching_release(
    guard: IngestionCompletionGuard,
    approval_id: str,
    result: dict[str, Any],
) -> tuple[Path, dict[str, Any]] | None:
    matching: list[tuple[Path, dict[str, Any]]] = []
    for receipt_path in sorted(guard.manager.releases_dir.glob("release-*.json")):
        try:
            receipt = read_json(receipt_path)
            from .guard import _dispatch
            _dispatch("_verify_receipt", _verify_receipt)(receipt)
        except (OSError, ValueError, TypeError) as exc:
            try:
                raw = read_json(receipt_path)
            except (OSError, ValueError, TypeError):
                continue
            if isinstance(raw, dict) and raw.get("approval_id") == approval_id:
                result["delivery_state"] = "RELEASE_RECEIPT_INVALID"
                result["blocker"] = f"Release receipt is invalid: {receipt_path.name}: {exc}"
                result["next_action"] = "Repair or roll back the invalid prepared release."
                return None
            continue
        if receipt.get("approval_id") == approval_id:
            matching.append((receipt_path, receipt))

    if not matching:
        result["delivery_state"] = "INGESTION_OR_RELEASE_PENDING"
        result["blocker"] = "No prepared release is bound to this directive approval."
        result["next_action"] = "Run the remaining deterministic intake and release stages."
        return None
    if len(matching) != 1:
        result["delivery_state"] = "RELEASE_RECEIPT_AMBIGUOUS"
        result["blocker"] = (
            "More than one prepared release is bound to this directive approval: "
            + ", ".join(path.name for path, _receipt in matching)
        )
        result["next_action"] = "Resolve the duplicate release binding before activation."
        return None

    return matching[0]


def _verify_prepared_artifacts(
    guard: IngestionCompletionGuard,
    receipt: dict[str, Any],
    result: dict[str, Any],
    receipt_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    next_pair = receipt["next_pair"]
    try:
        prepared_artifacts = guard._prepared_pair_artifacts(next_pair)
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        result["delivery_state"] = "PREPARED_PAIR_INVALID"
        result["blocker"] = f"Prepared graph/index artifacts are invalid or missing: {exc}"
        result["next_action"] = "Inspect the prepared generation; do not activate it."
        return None
    pair_validation = receipt.get("pair_validation")
    if not isinstance(pair_validation, dict) or pair_validation.get("status") != "PASS":
        result["delivery_state"] = "PREPARED_PAIR_INVALID"
        result["blocker"] = "Prepared release has no passing pair-validation receipt."
        result["next_action"] = "Prepare a new validated graph/index generation."
        return None
    if not (
        prepared_artifacts["graph_sha256_matches"]
        and prepared_artifacts["sidecar_sha256_matches"]
    ):
        result["delivery_state"] = "PREPARED_PAIR_INVALID"
        result["blocker"] = "Prepared graph or sidecar bytes do not match the sealed pair."
        result["next_action"] = "Inspect the prepared generation; do not activate it."
        return None

    result["release"] = {
        "receipt_id": receipt["receipt_id"],
        "receipt_digest": receipt["receipt_digest"],
        "status": receipt["status"],
        "path": str(receipt_path),
        "generation_id": prepared_artifacts["generation_id"],
        "graph_operation_count": receipt["graph_write_receipt"]["operation_count"],
        "pair_validation_status": pair_validation["status"],
    }
    result["sidecar"] = {
        "path": str(prepared_artifacts["sidecar_path"]),
        "exists": True,
        "expected_sha256": prepared_artifacts["sidecar_sha256"],
        "actual_sha256": prepared_artifacts["actual_sidecar_sha256"],
        "sha256_matches": prepared_artifacts["sidecar_sha256_matches"],
        "publication_state": receipt["embedding_generation_receipt"].get(
            "publication_state", "LEGACY_PUBLISHED"
        ),
        "record_count": receipt["embedding_generation_receipt"].get("record_count"),
    }
    return next_pair, prepared_artifacts


def _inspect_activation(
    guard: IngestionCompletionGuard,
    receipt: dict[str, Any],
    next_pair: dict[str, Any],
    active: dict[str, Any],
    directive: dict[str, Any],
    result: dict[str, Any],
) -> bool | None:
    """Returns True/False if activation terminates delivery check, or None if pending."""
    activation_path = guard.manager.releases_dir / f"activation-{next_pair['generation_id']}.json"
    if not activation_path.is_file():
        return None
    activation = read_json(activation_path)
    _verify_activation_binding(activation, receipt)
    result["activation"] = {
        "activation_id": activation["activation_id"],
        "receipt_digest": activation["receipt_digest"],
        "path": str(activation_path),
        "active_pair_matches": active == activation["active_pair"],
    }
    if active != activation["active_pair"]:
        result["delivery_state"] = "ACTIVE_PAIR_MISMATCH"
        result["blocker"] = "Activation receipt does not match the current active pair."
        result["next_action"] = "Investigate activation or rollback evidence."
        return False
    if directive["status"] == "COMPLETE":
        result["delivery_state"] = "COMPLETE"
        result["next_action"] = "None; directive has a terminal outcome."
        return True
    result["delivery_state"] = "ACTIVE_AWAITING_COMPLETION"
    result["next_action"] = "Run the terminal completion gate."
    return True


def compute_delivery_status(guard: IngestionCompletionGuard, directive_id: str) -> dict[str, Any]:
    """Project one directive's sealed receipts into an operator status view."""
    with file_lock(guard.lock_path):
        directive = guard._read(directive_id)
        try:
            active = read_json(guard.manager.active_path)
            active_summary = guard._pair_summary(active)
        except (OSError, TypeError, ValueError) as exc:
            result = _make_status_payload(directive_id, directive, None)
            result["delivery_state"] = "ACTIVE_PAIR_INVALID"
            result["blocker"] = f"Active graph/index pointer is invalid: {exc}"
            result["next_action"] = "Repair or roll back the active pair before further delivery work."
            return result

        result = _make_status_payload(directive_id, directive, active_summary)
        if directive["terminal"] and (
            directive["status"] != "COMPLETE"
            or (directive.get("completion") or {}).get("mutation_outcome") == "IDEMPOTENT_NOOP"
        ):
            result["delivery_state"] = directive["status"]
            result["next_action"] = "None; directive has a terminal outcome."
            return result

        approval = directive.get("approval")
        if approval is None:
            result["delivery_state"] = "ADMISSION_REQUIRED"
            result["blocker"] = "No exact-scope admission is bound to the directive."
            result["next_action"] = "Record the one required classification decision or bind a signed batch-admission policy."
            return result

        match = _find_approval_matching_release(guard, approval["approval_id"], result)
        if match is None:
            return result
        receipt_path, receipt = match

        artifacts = _verify_prepared_artifacts(guard, receipt, result, receipt_path)
        if artifacts is None:
            return result
        next_pair, _ = artifacts

        activation_done = _inspect_activation(guard, receipt, next_pair, active, directive, result)
        if activation_done is not None:
            return result

        if not _inspect_retrieval_evaluation(guard, receipt, next_pair, result):
            return result

        result["delivery_state"] = "READY_TO_ACTIVATE"
        result["next_action"] = "Activate the exact prepared release and complete the directive."
        return result
