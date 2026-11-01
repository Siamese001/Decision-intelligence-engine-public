"""Verification and structural audit functions for ingestion directives."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from ..batch_admission import binding_from_audit_record, policy_approval_id
from ..core import digest, stable_id
from ..hitl_governance import validate_approval_origin
from ..intake import INTAKE_STAGES
from ..release import RUN_ID_PATTERN
from .constants import (
    _BASELINE_PAIR_FIELDS,
    _BLOCK_REASON_PATTERN,
    _EMBEDDING_TARGET_FIELDS,
    DIRECTIVE_ID_PATTERN,
    DIRECTIVE_SCHEMA_VERSION,
    DIRECTIVE_STATUSES,
    TERMINAL_STATUSES,
    _is_nonblank_string,
    _is_nonnegative_int,
    _is_sha256,
    _package_path_key,
)


def _verify_pair_binding(value: object) -> None:
    if not isinstance(value, dict) or set(value) != set(_BASELINE_PAIR_FIELDS):
        raise ValueError("ingestion directive baseline pair is malformed")
    required_strings = (
        "generation_id",
        "graph_snapshot",
        "embedding_backend",
        "model_id",
        "model_revision",
    )
    if any(not _is_nonblank_string(value.get(field)) for field in required_strings):
        raise ValueError("ingestion directive baseline pair is malformed")
    if not _is_sha256(value.get("graph_sha256")) or not _is_sha256(
        value.get("sidecar_sha256")
    ):
        raise ValueError("ingestion directive baseline pair is malformed")
    manifest_digest = value.get("model_manifest_digest")
    if manifest_digest is not None and not _is_sha256(manifest_digest):
        raise ValueError("ingestion directive baseline pair is malformed")
    if not isinstance(value.get("production_qualified"), bool):
        raise ValueError("ingestion directive baseline pair is malformed")


def _verify_package_bindings(packages: object) -> list[dict[str, Any]]:
    if not isinstance(packages, list) or not packages:
        raise ValueError("ingestion directive requires package bindings")
    path_keys: set[str] = set()
    for binding in packages:
        if not isinstance(binding, dict) or set(binding) != {"path", "sha256", "bytes"}:
            raise ValueError("ingestion directive package binding is malformed")
        path = binding.get("path")
        if (
            not _is_nonblank_string(path)
            or not Path(path).is_absolute()
            or not _is_sha256(binding.get("sha256"))
            or not _is_nonnegative_int(binding.get("bytes"))
        ):
            raise ValueError("ingestion directive package binding is malformed")
        path_key = _package_path_key(path)
        if path_key in path_keys:
            raise ValueError("ingestion directive requires unique package paths")
        path_keys.add(path_key)
    return packages


def _verify_embedding_target(target: object) -> None:
    if target is None:
        return
    if (
        not isinstance(target, dict)
        or set(target) != set(_EMBEDDING_TARGET_FIELDS)
        or not _is_nonblank_string(target.get("runtime_root"))
        or not Path(target["runtime_root"]).is_absolute()
        or not _is_nonblank_string(target.get("embedding_backend"))
        or not _is_nonblank_string(target.get("model_id"))
        or not _is_nonblank_string(target.get("model_revision"))
        or (
            target.get("model_manifest_digest") is not None
            and not _is_sha256(target.get("model_manifest_digest"))
        )
        or not isinstance(target.get("production_qualified"), bool)
    ):
        raise ValueError("ingestion directive embedding target is malformed")


def _verify_approval_binding(value: dict[str, Any]) -> None:
    approval = value["approval"]
    if approval is None:
        return
    required = {
        "approval_id",
        "binding_type",
        "bound_at",
        "package_set_digest",
        "classification_digest",
        "classification_id",
        "decision",
        "reviewer",
        "rationale",
    }
    if not isinstance(approval, dict) or not required.issubset(approval):
        raise ValueError("ingestion directive approval binding is malformed")
    if (
        any(
            not _is_nonblank_string(approval.get(field))
            for field in (
                "approval_id",
                "binding_type",
                "bound_at",
                "classification_id",
                "reviewer",
                "rationale",
            )
        )
        or approval.get("decision") != "APPROVE"
        or approval.get("package_set_digest") != value["package_set_digest"]
        or not _is_sha256(approval.get("classification_digest"))
    ):
        raise ValueError("ingestion directive approval binding is malformed")
    binding_type = approval["binding_type"]
    if binding_type == "HITL_CLASSIFICATION_APPROVAL":
        is_valid, origin_reason = validate_approval_origin(approval)
        if not is_valid:
            raise ValueError(f"ingestion directive approval origin is invalid: {origin_reason}")
        expected_approval_id = stable_id(
            "hitl-approval",
            value["directive_id"],
            value["package_set_digest"],
            approval["classification_digest"],
            digest(value.get("required_embedding_target")),
            approval["reviewer"].strip(),
            approval["rationale"].strip(),
            length=28,
        )
        if (
            not _is_sha256(approval.get("embedding_target_digest"))
            or approval["embedding_target_digest"]
            != digest(value.get("required_embedding_target"))
            or approval["approval_id"] != expected_approval_id
        ):
            raise ValueError("ingestion directive HITL approval target is malformed")
    elif binding_type == "SIGNED_BATCH_POLICY_ADMISSION":
        batch = value.get("batch_admission")
        binding = (
            binding_from_audit_record(
                {key: item for key, item in batch.items() if key != "path"}
            )
            if isinstance(batch, dict)
            else None
        )
        if (
            binding is None
            or not _is_sha256(approval.get("batch_manifest_sha256"))
            or approval["batch_manifest_sha256"] != batch.get("manifest_file_sha256")
            or approval.get("policy_id") != binding.policy_id
            or approval.get("policy_version") != binding.policy_version
            or approval["approval_id"]
            != policy_approval_id(
                directive_id=value["directive_id"],
                package_set_digest=value["package_set_digest"],
                binding=binding,
            )
        ):
            raise ValueError("ingestion directive signed policy approval is malformed")
    else:
        raise ValueError("ingestion directive approval binding type is invalid")


def _verify_completion(value: dict[str, Any], completion: object) -> None:
    required = {
        "status", "completed_at", "approval_id", "package_set_digest",
        "run_ids", "run_receipts", "receipt_path", "receipt_id", "receipt_digest",
        "activation_path", "activation_id", "activation_digest",
        "generation_before", "generation_after", "graph_sha256_before", "graph_sha256_after",
        "graph_operation_count", "mutation_outcome", "required_embedding_target",
        "embedding_target_verified", "completion_digest",
    }
    if not isinstance(completion, dict) or not required.issubset(completion):
        raise ValueError("ingestion directive completion proof is malformed")
    declared = completion.get("completion_digest")
    actual = digest(
        {key: item for key, item in completion.items() if key != "completion_digest"}
    )
    approval = value.get("approval")
    run_ids = completion.get("run_ids")
    run_receipts = completion.get("run_receipts")
    if (
        completion.get("status") != "PASS"
        or not _is_nonblank_string(completion.get("completed_at"))
        or completion.get("completed_at") != value["updated_at"]
        or not isinstance(approval, dict)
        or completion.get("approval_id") != approval.get("approval_id")
        or completion.get("package_set_digest") != value["package_set_digest"]
        or not isinstance(run_ids, list)
        or not run_ids
        or any(
            not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id)
            for run_id in run_ids
        )
        or len(run_ids) != len(set(run_ids))
        or not isinstance(run_receipts, list)
        or [item.get("run_id") for item in run_receipts if isinstance(item, dict)]
        != run_ids
        or len(run_receipts) != len(run_ids)
        or not _is_nonblank_string(completion.get("receipt_path"))
        or not Path(completion["receipt_path"]).is_absolute()
        or not _is_nonblank_string(completion.get("receipt_id"))
        or not _is_sha256(completion.get("receipt_digest"))
        or not _is_nonblank_string(completion.get("generation_before"))
        or not _is_nonblank_string(completion.get("generation_after"))
        or not _is_sha256(completion.get("graph_sha256_before"))
        or not _is_sha256(completion.get("graph_sha256_after"))
        or not _is_nonnegative_int(completion.get("graph_operation_count"))
        or completion.get("required_embedding_target")
        != value.get("required_embedding_target")
        or completion.get("embedding_target_verified")
        is not (value.get("required_embedding_target") is not None)
        or not _is_sha256(declared)
        or declared != actual
    ):
        raise ValueError("ingestion directive completion proof is malformed")
    outcome = completion.get("mutation_outcome")
    if outcome == "GRAPH_CHANGED":
        if (
            completion["graph_operation_count"] == 0
            or completion["graph_sha256_before"] == completion["graph_sha256_after"]
            or completion["generation_before"] == completion["generation_after"]
            or not _is_nonblank_string(completion.get("activation_path"))
            or not Path(completion["activation_path"]).is_absolute()
            or not _is_nonblank_string(completion.get("activation_id"))
            or not _is_sha256(completion.get("activation_digest"))
        ):
            raise ValueError("graph-changing completion proof is malformed")
    elif outcome == "IDEMPOTENT_NOOP":
        not_applicable = completion.get("not_applicable_stages")
        if (
            completion["graph_operation_count"] != 0
            or completion["graph_sha256_before"] != completion["graph_sha256_after"]
            or completion["generation_before"] != completion["generation_after"]
            or completion.get("activation_path") is not None
            or completion.get("activation_id") is not None
            or completion.get("activation_digest") is not None
            or completion.get("executed_stages") != INTAKE_STAGES
            or not isinstance(not_applicable, dict)
            or not {"W0", "E0"}.issubset(not_applicable)
        ):
            raise ValueError("idempotent-noop completion proof is malformed")
    else:
        raise ValueError("ingestion directive completion outcome is invalid")
    if isinstance(completion, dict) and completion.get("speaker_role_coverage_receipt") is not None:
        from ..canonical_pipeline.speaker_role_coverage import verify_completion_speaker_role_coverage
        verify_completion_speaker_role_coverage(value, completion)


def _verify_state_history(value: dict[str, Any]) -> None:
    events = value["events"]
    if not isinstance(events, list) or not events:
        raise ValueError("ingestion directive has no state history")
    for sequence, event in enumerate(events, start=1):
        if (
            not isinstance(event, dict)
            or event.get("sequence") != sequence
            or isinstance(event.get("sequence"), bool)
            or not _is_nonblank_string(event.get("event_type"))
            or not _is_nonblank_string(event.get("recorded_at"))
        ):
            raise ValueError("ingestion directive state history is malformed")
    if (
        events[0]["event_type"] != "DIRECTIVE_OPENED"
        or value["opened_at"] != events[0]["recorded_at"]
        or value["updated_at"] != events[-1]["recorded_at"]
    ):
        raise ValueError("ingestion directive state history is inconsistent")
    terminal_event_types = {
        "BLOCKED": {"DIRECTIVE_BLOCKED", "HITL_CLASSIFICATION_REJECTED"},
        "CANCELLED": {"DIRECTIVE_CANCELLED"},
        "COMPLETE": {"DIRECTIVE_COMPLETED", "DIRECTIVE_COMPLETED_NOOP"},
    }
    allowed = terminal_event_types.get(value["status"])
    if allowed is not None and events[-1]["event_type"] not in allowed:
        raise ValueError("ingestion directive terminal event is inconsistent")


def _verify_approval_history(value: dict[str, Any]) -> None:
    approval = value["approval"]
    if approval is None:
        return
    event_type = {
        "HITL_CLASSIFICATION_APPROVAL": "HITL_CLASSIFICATION_APPROVED",
        "SIGNED_BATCH_POLICY_ADMISSION": "SIGNED_BATCH_POLICY_ADMITTED",
    }[approval["binding_type"]]
    matching = [
        event
        for event in value["events"]
        if (
            event["event_type"] == event_type
            and event.get("package_set_digest") == approval["package_set_digest"]
            and event.get("classification_digest")
            == approval["classification_digest"]
        )
    ]
    if not matching:
        raise ValueError("ingestion directive approval has no matching state event")
    latest = matching[-1]
    classification_ready = any(
        event["event_type"] == "CLASSIFICATION_READY"
        and event.get("package_set_digest") == approval["package_set_digest"]
        and event.get("classification_id") == approval["classification_id"]
        and event.get("classification_digest") == approval["classification_digest"]
        for event in value["events"]
    )
    if not classification_ready or latest["recorded_at"] != approval["bound_at"]:
        raise ValueError("ingestion directive approval history is inconsistent")
    if (
        approval["binding_type"] == "HITL_CLASSIFICATION_APPROVAL"
        and (
            latest.get("embedding_target_digest")
            != approval["embedding_target_digest"]
            or latest.get("reviewer_digest") != digest(approval["reviewer"])
        )
    ):
        raise ValueError("ingestion directive HITL approval event is inconsistent")
    if (
        approval["binding_type"] == "SIGNED_BATCH_POLICY_ADMISSION"
        and (
            latest.get("batch_manifest_sha256")
            != approval["batch_manifest_sha256"]
            or latest.get("batch_policy_id") != approval["policy_id"]
        )
    ):
        raise ValueError("ingestion directive signed approval event is inconsistent")


def _verify_terminal_state(value: dict[str, Any]) -> None:
    status = value["status"]
    block = value["block"]
    cancellation = value["cancellation"]
    completion = value["completion"]
    if status == "OPEN":
        if any(item is not None for item in (block, cancellation, completion)):
            raise ValueError("open ingestion directive has a terminal payload")
        return
    if status == "BLOCKED":
        if (
            not isinstance(block, dict)
            or cancellation is not None
            or completion is not None
            or not _is_nonblank_string(block.get("reason"))
            or not _is_nonblank_string(block.get("blocked_at"))
            or block["blocked_at"] != value["updated_at"]
            or not isinstance(block.get("reason_code"), str)
            or not _BLOCK_REASON_PATTERN.fullmatch(block["reason_code"])
        ):
            raise ValueError("blocked ingestion directive proof is malformed")
        event = value["events"][-1]
        if event["event_type"] == "DIRECTIVE_BLOCKED":
            if (
                event.get("reason_code") != block["reason_code"]
                or event.get("reason_digest") != digest(block["reason"])
            ):
                raise ValueError("blocked ingestion directive event is inconsistent")
        elif (
            block["reason_code"] != "HITL_REJECTED_CLASSIFICATION"
            or event.get("classification_digest")
            != block.get("classification_digest")
            or event.get("reviewer_digest") != digest(block.get("reviewer"))
        ):
            raise ValueError("HITL-rejected ingestion directive event is inconsistent")
        return
    if status == "CANCELLED":
        if (
            block is not None
            or not isinstance(cancellation, dict)
            or completion is not None
            or not _is_nonblank_string(cancellation.get("reason"))
            or not _is_nonblank_string(cancellation.get("cancelled_at"))
            or cancellation["cancelled_at"] != value["updated_at"]
        ):
            raise ValueError("cancelled ingestion directive proof is malformed")
        if value["events"][-1].get("reason_digest") != digest(
            cancellation["reason"]
        ):
            raise ValueError("cancelled ingestion directive event is inconsistent")
        return
    if block is not None or cancellation is not None:
        raise ValueError("completed ingestion directive has conflicting terminal payloads")
    _verify_completion(value, completion)
    event = value["events"][-1]
    if (
        event.get("completion_digest") != completion["completion_digest"]
        or event.get("receipt_digest") != completion["receipt_digest"]
        or event.get("mutation_outcome") != completion["mutation_outcome"]
    ):
        raise ValueError("ingestion directive completion event is inconsistent")


def _seal_directive(value: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(value)
    result.pop("directive_digest", None)
    result["directive_digest"] = digest(result)
    return result


def _verify_directive(value: dict[str, Any]) -> None:
    required = {
        "schema_version",
        "directive_id",
        "status",
        "terminal",
        "scope",
        "baseline_pair",
        "packages",
        "package_set_digest",
        "approval",
        "opened_at",
        "updated_at",
        "events",
        "block",
        "cancellation",
        "completion",
        "directive_digest",
    }
    allowed = required | {"batch_admission", "required_embedding_target"}
    if (
        not isinstance(value, dict)
        or not required.issubset(value)
        or set(value) - allowed
    ):
        raise ValueError("ingestion directive fields are malformed")
    if value["schema_version"] != DIRECTIVE_SCHEMA_VERSION:
        raise ValueError("unsupported ingestion directive schema")
    if not isinstance(value["directive_id"], str) or not DIRECTIVE_ID_PATTERN.fullmatch(
        value["directive_id"]
    ):
        raise ValueError("unsafe ingestion directive ID")
    if not isinstance(value["status"], str) or value["status"] not in DIRECTIVE_STATUSES:
        raise ValueError("invalid ingestion directive status")
    if value["terminal"] is not (value["status"] in TERMINAL_STATUSES):
        raise ValueError("ingestion directive terminal flag disagrees with status")
    if not _is_nonblank_string(value["scope"]):
        raise ValueError("ingestion directive scope cannot be blank")
    if (
        not _is_nonblank_string(value["opened_at"])
        or not _is_nonblank_string(value["updated_at"])
    ):
        raise ValueError("ingestion directive timestamps are malformed")
    declared = value.get("directive_digest")
    actual = digest({key: item for key, item in value.items() if key != "directive_digest"})
    if not _is_sha256(declared) or declared != actual:
        raise ValueError("ingestion directive digest mismatch")
    _verify_pair_binding(value["baseline_pair"])
    packages = _verify_package_bindings(value["packages"])
    package_digest = digest(packages)
    if (
        not _is_sha256(value["package_set_digest"])
        or value["package_set_digest"] != package_digest
    ):
        raise ValueError("ingestion directive package-set digest mismatch")
    if value.get("batch_admission") is not None:
        batch = value["batch_admission"]
        if (
            not isinstance(batch, dict)
            or not _is_nonblank_string(batch.get("path"))
            or not Path(batch["path"]).is_absolute()
        ):
            raise ValueError("ingestion directive batch admission is malformed")
        binding_from_audit_record(
            {key: item for key, item in batch.items() if key != "path"}
        )
    _verify_embedding_target(value.get("required_embedding_target"))
    _verify_approval_binding(value)
    _verify_state_history(value)
    _verify_approval_history(value)
    _verify_terminal_state(value)
