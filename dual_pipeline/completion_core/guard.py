"""Ingestion completion guard implementation."""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from ..authorization import AuthorizationContext
from ..batch_admission import (
    BATCH_ADMIT_CAPABILITY,
    BATCH_MANIFEST_RESOURCE,
    binding_from_audit_record,
    load_manifest,
    policy_approval_id,
    verify_manifest_packages,
)
from ..contracts import validate_artifact
from ..core import (
    ROOT,
    assert_safe_mutable_path,
    digest,
    file_lock,
    read_json,
    sha256_bytes,
    sha256_file,
    stable_id,
    utc_now,
    write_json,
    write_json_exclusive,
)
from ..graph_delta import (
    build_graph_mutation_summary,
    verify_structural_monotonicity,
)
from ..hitl_governance import validate_approval_origin
from ..intake import INTAKE_STAGES
from ..intake_classifier import classify_packages
from ..release import (
    RUN_ID_PATTERN,
    ReleaseManager,
    _verify_activation_binding,
    _verify_receipt,
)
from ..telemetry import RunRecorder
from .constants import (
    _BGE_M3_BACKENDS,
    _EMBEDDING_TARGET_FIELDS,
    DIRECTIVE_ID_PATTERN,
    DIRECTIVE_SCHEMA_VERSION,
    RELEASE_STAGES,
    _package_path_key,
)
from .verification import (
    _seal_directive,
    _verify_directive,
)


def _dispatch(name: str, fallback: Any) -> Any:
    curr = sys.modules.get(__name__)
    if curr and getattr(curr, name, fallback) is not fallback:
        return getattr(curr, name)
    mod = sys.modules.get("dual_pipeline.completion")
    return getattr(mod, name, fallback) if mod else fallback


class IngestionCompletionGuard:
    """Keep ingestion intent open until a valid terminal outcome is proven."""

    def __init__(self, runtime_root: Path, seed_graph: Path | None = None):
        self.runtime_root = Path(runtime_root).expanduser().absolute().resolve()
        if seed_graph is not None:
            self.seed_graph = Path(seed_graph).expanduser().resolve()
        elif (self.runtime_root / "active_pair.json").is_file():
            active = read_json(self.runtime_root / "active_pair.json")
            self.seed_graph = (self.runtime_root / active["graph_path"]).resolve()
        else:
            self.seed_graph = (ROOT / "graph" / "nee_graph.sqlite").resolve()
        self.manager = ReleaseManager(self.runtime_root, self.seed_graph)
        self.directive_dir = self.runtime_root / "ingestion_directives"
        self.classification_dir = self.directive_dir / "classifications"
        self.lock_path = self.runtime_root / "ingestion_directives.lock"
        assert_safe_mutable_path(
            self.runtime_root,
            self.directive_dir,
            label="ingestion directive directory",
        )
        assert_safe_mutable_path(
            self.runtime_root,
            self.lock_path,
            label="ingestion directive lock",
        )
        assert_safe_mutable_path(
            self.runtime_root,
            self.classification_dir,
            label="ingestion classification directory",
        )

    @staticmethod
    def _validate_directive_id(directive_id: str) -> str:
        if not DIRECTIVE_ID_PATTERN.fullmatch(directive_id):
            raise ValueError(f"unsafe ingestion directive ID: {directive_id!r}")
        return directive_id

    def _path(self, directive_id: str) -> Path:
        value = self._validate_directive_id(directive_id)
        path = self.directive_dir / f"{value}.json"
        return assert_safe_mutable_path(
            self.runtime_root,
            path,
            label="ingestion directive path",
        )

    def _classification_path(self, value: dict[str, Any]) -> Path:
        path = self.classification_dir / (
            f"{value['directive_id']}-{value['package_set_digest']}.json"
        )
        return assert_safe_mutable_path(
            self.runtime_root,
            path,
            label="ingestion classification path",
        )

    @staticmethod
    def _package_binding(path: Path) -> dict[str, Any]:
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"ingestion package does not exist: {resolved}")
        path_str = str(resolved).replace("\\", "/")
        if (
            "/cards/" in path_str
            or path_str.endswith("/cards")
            or (resolved.name.startswith("NEE_") and resolved.suffix == ".md")
        ):
            raise ValueError(
                f"ERR_EPISTEMIC_CIRCULARITY_BLOCKED: Ingestion package cannot target card markdown files: {resolved}"
            )
        before = resolved.stat()
        file_sha256 = _dispatch("sha256_file", sha256_file)(resolved)
        try:
            after = resolved.stat()
        except OSError as exc:
            raise RuntimeError(
                f"ingestion package changed while it was being bound: {resolved}"
            ) from exc
        stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
        if any(getattr(before, field) != getattr(after, field) for field in stable_fields):
            raise RuntimeError(f"ingestion package changed while it was being bound: {resolved}")
        return {
            "path": str(resolved),
            "sha256": file_sha256,
            "bytes": after.st_size,
        }

    @classmethod
    def _package_bindings(cls, paths: Iterable[Path]) -> list[dict[str, Any]]:
        bindings: list[dict[str, Any]] = []
        seen: set[str] = set()
        for path in paths:
            binding = cls._package_binding(path)
            path_key = _package_path_key(binding["path"])
            if path_key in seen:
                continue
            seen.add(path_key)
            bindings.append(binding)
        return bindings

    def _read(self, directive_id: str) -> dict[str, Any]:
        path = self._path(directive_id)
        if not path.is_file():
            raise FileNotFoundError(f"ingestion directive does not exist: {directive_id}")
        value = read_json(path)
        _verify_directive(value)
        if value["directive_id"] != directive_id:
            raise ValueError("ingestion directive ID does not match its immutable storage path")
        return value

    @staticmethod
    def _require_open(value: dict[str, Any]) -> None:
        if value["status"] != "OPEN":
            raise RuntimeError(
                f"ingestion directive is terminal and cannot continue: {value['status']}"
            )

    @staticmethod
    def _baseline_binding(pair: dict[str, Any]) -> dict[str, Any]:
        return {
            "generation_id": pair["generation_id"],
            "graph_sha256": pair["graph_sha256"],
            "sidecar_sha256": pair["sidecar_sha256"],
            "graph_snapshot": pair["graph_snapshot"],
            "embedding_backend": pair["embedding_backend"],
            "model_id": pair["model_id"],
            "model_revision": pair["model_revision"],
            "model_manifest_digest": pair.get("model_manifest_digest"),
            "production_qualified": pair["production_qualified"],
        }

    def _embedding_target_binding(self, pair: dict[str, Any]) -> dict[str, Any]:
        return {
            "runtime_root": str(self.runtime_root),
            "embedding_backend": pair["embedding_backend"],
            "model_id": pair["model_id"],
            "model_revision": pair["model_revision"],
            "model_manifest_digest": pair.get("model_manifest_digest"),
            "production_qualified": pair["production_qualified"],
        }

    @staticmethod
    def _require_bge_m3_target(target: dict[str, Any]) -> None:
        if (
            target["embedding_backend"] not in _BGE_M3_BACKENDS
            or target["model_id"] != "BAAI/bge-m3"  # noqa: SSOT_EXEMPT
            or not isinstance(target["model_revision"], str)
            or not re.fullmatch(r"[0-9a-f]{40}", target["model_revision"])
            or not isinstance(target["model_manifest_digest"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", target["model_manifest_digest"])
        ):
            raise PermissionError(
                "directive requires BGE-M3 embedding, but the selected runtime "
                "is not bound to a pinned BAAI/bge-m3 model and manifest"
            )

    def _assert_pair_matches_embedding_target(
        self,
        value: dict[str, Any],
        pair: dict[str, Any],
        *,
        stage: str,
    ) -> None:
        target = value.get("required_embedding_target")
        if target is None:
            return
        if Path(target["runtime_root"]).resolve() != self.runtime_root:
            raise RuntimeError(
                f"{stage} runtime root does not match the HITL-bound embedding target"
            )
        mismatches = {
            field: {"required": target[field], "actual": pair.get(field)}
            for field in _EMBEDDING_TARGET_FIELDS
            if field != "runtime_root" and pair.get(field) != target[field]
        }
        if mismatches:
            raise RuntimeError(
                f"{stage} pair does not match the HITL-bound embedding target: {mismatches}"
            )

    def _classification_report(self, value: dict[str, Any]) -> dict[str, Any]:
        return classify_packages(
            directive_id=value["directive_id"],
            package_set_digest=value["package_set_digest"],
            packages=value["packages"],
            batch_admission=value.get("batch_admission"),
        )

    @staticmethod
    def _classification_receipt_matches(report: object, value: dict[str, Any]) -> bool:
        """Accept an immutable historical classifier receipt for the exact package set.

        Classification reports are shown to a human and can already be bound to
        an open directive's approval.  A later classifier implementation must
        not silently invalidate that decision just because it would now emit a
        richer report.  Package-byte and digest binding remain mandatory.
        """

        if not isinstance(report, dict):
            return False
        declared = report.get("classification_digest")
        actual = digest(
            {key: item for key, item in report.items() if key != "classification_digest"}
        )
        return bool(
            isinstance(declared, str)
            and declared == actual
            and report.get("directive_id") == value["directive_id"]
            and report.get("package_set_digest") == value["package_set_digest"]
        )

    def _batch_admission_binding(
        self, manifest_path: Path, packages: Iterable[dict[str, Any]]
    ) -> dict[str, str]:
        resolved = Path(manifest_path).expanduser().resolve()
        manifest, binding = load_manifest(resolved)
        verify_manifest_packages(manifest, [Path(item["path"]) for item in packages])
        return {"path": str(resolved), **binding.audit_record()}

    def _assert_batch_admission_unchanged(self, value: dict[str, Any]) -> None:
        recorded = value.get("batch_admission")
        if recorded is None:
            return
        if not isinstance(recorded, dict):
            raise RuntimeError("ingestion directive batch admission is malformed")
        expected = self._batch_admission_binding(
            Path(str(recorded.get("path") or "")), value["packages"]
        )
        if recorded != expected:
            raise RuntimeError(
                "batch admission manifest changed or no longer matches the exact directive packages"
            )

    def _load_current_classification(self, value: dict[str, Any]) -> tuple[dict[str, Any], Path]:
        """Load a classification only when it binds current exact package bytes."""

        path = self._classification_path(value)
        if not path.is_file():
            raise PermissionError(
                "automatic classification has not been generated; run "
                "intake-directive-classify before HITL approval"
            )
        report = read_json(path)
        if not self._classification_receipt_matches(report, value):
            raise RuntimeError(
                "automatic classification is stale or does not bind the "
                "directive's exact package set"
            )
        if not any(
            event.get("event_type") == "CLASSIFICATION_READY"
            and event.get("classification_digest") == report["classification_digest"]
            and event.get("package_set_digest") == value["package_set_digest"]
            for event in value["events"]
        ):
            raise RuntimeError("automatic classification was not recorded in the directive ledger")
        return report, path

    @staticmethod
    def _human_approval_binding(
        value: dict[str, Any],
        *,
        classification: dict[str, Any],
        reviewer: str,
        rationale: str,
        bound_at: str,
    ) -> dict[str, Any]:
        if not reviewer.strip():
            raise ValueError("HITL reviewer cannot be blank")
        if not rationale.strip():
            raise ValueError("HITL rationale cannot be blank")
        approval_id = stable_id(
            "hitl-approval",
            value["directive_id"],
            value["package_set_digest"],
            classification["classification_digest"],
            digest(value.get("required_embedding_target")),
            reviewer.strip(),
            rationale.strip(),
            length=28,
        )
        return {
            # Kept only as an internal compatibility key for existing release
            # receipts and graph-change-set contracts. Operators never supply it.
            "approval_id": approval_id,
            "binding_type": "HITL_CLASSIFICATION_APPROVAL",
            "bound_at": bound_at,
            "package_set_digest": value["package_set_digest"],
            "classification_digest": classification["classification_digest"],
            "classification_id": classification["classification_id"],
            "decision": "APPROVE",
            "embedding_target_digest": digest(value.get("required_embedding_target")),
            "reviewer": reviewer.strip(),
            "rationale": rationale.strip(),
        }

    @staticmethod
    def _bound_approval_id(value: dict[str, Any], supplied_approval_id: str | None = None) -> str:
        approval = value["approval"]
        if approval is None:
            raise PermissionError(
                "ingestion directive has no recorded HITL approval or signed "
                "batch policy approval for graph/index embedding"
            )
        approval_id = approval.get("approval_id")
        if not isinstance(approval_id, str) or not approval_id:
            raise RuntimeError("ingestion directive approval has no internal release binding")
        if approval.get("package_set_digest") != value["package_set_digest"]:
            raise PermissionError(
                "recorded HITL approval is not bound to the directive's exact package set"
            )
        if supplied_approval_id is not None and supplied_approval_id != approval_id:
            raise PermissionError(
                "supplied approval ID does not match the directive's recorded HITL decision"
            )
        return approval_id

    def open(
        self,
        *,
        directive_id: str,
        scope: str,
        packages: Iterable[Path],
        batch_admission_manifest: Path | None = None,
        approval_id: str | None = None,
        require_bge_m3: bool = False,
    ) -> dict[str, Any]:
        """Open one immutable-baseline directive for exact package bytes."""

        if approval_id is not None:
            raise ValueError(
                "approval IDs are no longer accepted; review the automatic "
                "classification and record an HITL decision instead"
            )
        self._validate_directive_id(directive_id)
        if not scope.strip():
            raise ValueError("ingestion directive scope cannot be blank")
        bindings = self._package_bindings(packages)
        if not bindings:
            raise ValueError("ingestion directive requires at least one package")
        batch_admission = (
            self._batch_admission_binding(batch_admission_manifest, bindings)
            if batch_admission_manifest is not None
            else None
        )
        package_set_digest = digest(bindings)
        active = self.manager.active_pair()
        required_embedding_target = self._embedding_target_binding(active)
        if require_bge_m3:
            self._require_bge_m3_target(required_embedding_target)
        now = utc_now()
        value = _seal_directive(
            {
                "schema_version": DIRECTIVE_SCHEMA_VERSION,
                "directive_id": directive_id,
                "status": "OPEN",
                "terminal": False,
                "scope": scope.strip(),
                "baseline_pair": self._baseline_binding(active),
                "required_embedding_target": required_embedding_target,
                "packages": bindings,
                "package_set_digest": package_set_digest,
                "batch_admission": batch_admission,
                "approval": None,
                "opened_at": now,
                "updated_at": now,
                "events": [
                    {
                        "sequence": 1,
                        "event_type": "DIRECTIVE_OPENED",
                        "recorded_at": now,
                        "package_set_digest": package_set_digest,
                        "package_count": len(bindings),
                        "approval_bound": False,
                        "embedding_target_digest": digest(required_embedding_target),
                    }
                ]
                + (
                    [
                        {
                            "sequence": 2, "event_type": "BATCH_ADMISSION_BOUND", "recorded_at": now,
                            "package_set_digest": package_set_digest, "batch_manifest_sha256": batch_admission["manifest_file_sha256"],
                            "batch_policy_id": batch_admission["policy_id"], "batch_admission_mode": batch_admission["admission_mode"],
                        }
                    ] if batch_admission is not None else []
                ),
                "block": None,
                "cancellation": None,
                "completion": None,
            }
        )
        path = self._path(directive_id)
        with file_lock(self.lock_path):
            if path.is_file():
                existing = self._read(directive_id)
                comparable = (
                    existing["scope"] == value["scope"]
                    and existing["packages"] == value["packages"]
                    and existing.get("batch_admission") == value.get("batch_admission")
                    and (existing.get("required_embedding_target") in (None, value.get("required_embedding_target")))
                    and existing.get("baseline_pair") == value.get("baseline_pair")
                )

                if not comparable:
                    if (
                        existing.get("status") == "OPEN" and not existing.get("terminal", False)
                        and existing.get("completion") is None and existing.get("scope") == value["scope"]
                        and existing.get("batch_admission") == value.get("batch_admission")
                    ):
                        existing["packages"], existing["package_set_digest"] = value["packages"], value["package_set_digest"]
                        if value.get("required_embedding_target") is not None:
                            existing["required_embedding_target"] = value["required_embedding_target"]
                        existing["baseline_pair"] = value["baseline_pair"]
                        existing["approval"], existing["updated_at"] = None, now
                        existing["events"].append({
                            "sequence": len(existing["events"]) + 1, "event_type": "DIRECTIVE_PACKAGES_REBOUND",
                            "recorded_at": now, "package_set_digest": value["package_set_digest"],
                            "package_count": len(value["packages"]), "approval_bound": False,
                            "embedding_target_digest": digest(value.get("required_embedding_target")),
                        })
                        write_json(path, _seal_directive(existing))
                    else:
                        raise RuntimeError(
                            "ingestion directive ID already exists with different bindings"
                        )
            else:
                write_json_exclusive(path, value)
        classified = self.classify(directive_id)
        self.assert_embedding_requirement(directive_id, require_bge_m3=require_bge_m3)
        result = classified["directive"]
        result["classification"] = classified["classification"]
        result["classification_path"] = classified["classification_path"]
        return result

    def status(self, directive_id: str) -> dict[str, Any]:
        with file_lock(self.lock_path):
            return self._read(directive_id)

    @staticmethod
    def _pair_summary(pair: Any) -> dict[str, Any]:
        """Return the fixed pair identity used by the delivery projection.

        This is intentionally narrower than ``ReleaseManager.validate_pair``.
        The latter is the expensive release/activation gate that revalidates all
        graph and vector contents; status needs a receipt-bound pointer view,
        not another hidden embedding gate.
        """

        if not isinstance(pair, dict):
            raise ValueError("graph/index pair is not an object")
        required_strings = (
            "generation_id",
            "graph_sha256",
            "sidecar_sha256",
            "graph_path",
            "sidecar_path",
            "embedding_backend",
            "model_id",
            "model_revision",
        )
        if any(
            not isinstance(pair.get(field), str) or not pair[field] for field in required_strings
        ):
            raise ValueError("graph/index pair has incomplete identity fields")
        if not isinstance(pair.get("production_qualified"), bool):
            raise ValueError("graph/index pair has no production qualification flag")
        if pair.get("model_manifest_digest") is not None and not isinstance(
            pair["model_manifest_digest"], str
        ):
            raise ValueError("graph/index pair model manifest digest is malformed")
        return {
            field: pair[field]
            for field in (
                "generation_id",
                "graph_sha256",
                "sidecar_sha256",
                "graph_path",
                "sidecar_path",
                "embedding_backend",
                "model_id",
                "model_revision",
                "model_manifest_digest",
                "production_qualified",
            )
        }

    def _prepared_pair_artifacts(self, pair: Any) -> dict[str, Any]:
        """Hash-check the already-validated candidate artifacts for status use."""

        summary = self._pair_summary(pair)
        graph_path = self.manager._resolve(summary["graph_path"])
        sidecar_path = self.manager._resolve(summary["sidecar_path"])
        if not graph_path.is_file() or not sidecar_path.is_file():
            raise FileNotFoundError("prepared graph or sidecar artifact is missing")
        graph_sha256 = sha256_file(graph_path)
        sidecar_sha256 = sha256_file(sidecar_path)
        return {
            **summary,
            "graph_path": graph_path,
            "sidecar_path": sidecar_path,
            "actual_graph_sha256": graph_sha256,
            "actual_sidecar_sha256": sidecar_sha256,
            "graph_sha256_matches": graph_sha256 == summary["graph_sha256"],
            "sidecar_sha256_matches": sidecar_sha256 == summary["sidecar_sha256"],
        }

    def delivery_status(self, directive_id: str) -> dict[str, Any]:
        """Project one directive's sealed receipts into an operator status view."""
        from .delivery import compute_delivery_status
        return compute_delivery_status(self, directive_id)

    def classify(self, directive_id: str) -> dict[str, Any]:
        """Persist a deterministic classification briefing before any embedding."""

        with file_lock(self.lock_path):
            value = self._read(directive_id)
            self._require_open(value)
            self._assert_packages_unchanged(value)
            self._assert_batch_admission_unchanged(value)
            generated = self._classification_report(value)
            path = self._classification_path(value)
            if path.is_file():
                existing = read_json(path)
                if not self._classification_receipt_matches(existing, value):
                    raise RuntimeError(
                        "classification receipt is not bound to the directive package set"
                    )
                report = existing
            else:
                report = generated
                self.classification_dir.mkdir(parents=True, exist_ok=True)
                write_json_exclusive(path, report)
            if not any(
                event.get("event_type") == "CLASSIFICATION_READY"
                and event.get("classification_digest") == report["classification_digest"]
                and event.get("package_set_digest") == value["package_set_digest"]
                for event in value["events"]
            ):
                now = utc_now()
                value["updated_at"] = now
                value["events"].append(
                    {
                        "sequence": len(value["events"]) + 1,
                        "event_type": "CLASSIFICATION_READY",
                        "recorded_at": now,
                        "package_set_digest": value["package_set_digest"],
                        "classification_id": report["classification_id"],
                        "classification_digest": report["classification_digest"],
                        "recommended_classification": report["recommended_classification"],
                        "embedding_recommendation": report["embedding_recommendation"],
                    }
                )
                value = _seal_directive(value)
                write_json(self._path(directive_id), value)
            return {
                "status": "CLASSIFIED",
                "directive_id": directive_id,
                "directive": value,
                "classification": report,
                "classification_path": str(path),
            }

    def attach(self, directive_id: str, packages: Iterable[Path]) -> dict[str, Any]:
        """Add package bindings; an empty follow-on is an explicit no-op."""

        additions = self._package_bindings(packages)
        with file_lock(self.lock_path):
            value = self._read(directive_id)
            self._require_open(value)
            if value.get("batch_admission") is not None:
                raise RuntimeError(
                    "a batch-admission-bound directive cannot attach packages; "
                    "create a new exact batch manifest"
                )
            if not additions:
                return value
            existing = {
                _package_path_key(binding["path"]): binding for binding in value["packages"]
            }
            newly_added: list[dict[str, Any]] = []
            for binding in additions:
                path_key = _package_path_key(binding["path"])
                previous = existing.get(path_key)
                if previous is not None:
                    if (
                        previous["sha256"] != binding["sha256"]
                        or previous["bytes"] != binding["bytes"]
                    ):
                        raise RuntimeError(
                            "attached ingestion package path now has different bytes"
                        )
                    continue
                existing[path_key] = binding
                newly_added.append(binding)
            if not newly_added:
                return value
            prior_approval = value["approval"]
            value["packages"].extend(newly_added)
            value["package_set_digest"] = digest(value["packages"])
            value["approval"] = None
            now = utc_now()
            value["updated_at"] = now
            value["events"].append(
                {
                    "sequence": len(value["events"]) + 1,
                    "event_type": "PACKAGES_ATTACHED",
                    "recorded_at": now,
                    "package_set_digest": value["package_set_digest"],
                    "package_count": len(value["packages"]),
                    "added_package_sha256": [item["sha256"] for item in newly_added],
                    "approval_invalidated": prior_approval is not None,
                    "prior_approval_digest": (
                        digest(prior_approval) if prior_approval is not None else None
                    ),
                }
            )
            sealed = _seal_directive(value)
            write_json(self._path(directive_id), sealed)
        classified = self.classify(directive_id)
        result = classified["directive"]
        result["classification"] = classified["classification"]
        result["classification_path"] = classified["classification_path"]
        return result

    def record_human_decision(
        self,
        directive_id: str,
        *,
        decision: str,
        classification_digest: str,
        reviewer: str,
        rationale: str,
    ) -> dict[str, Any]:
        """Record a user-visible HITL decision instead of accepting an approval ID."""

        normalized_decision = decision.strip().upper()
        if normalized_decision not in {"APPROVE", "REJECT"}:
            raise ValueError("HITL decision must be APPROVE or REJECT")
        if not reviewer.strip():
            raise ValueError("HITL reviewer cannot be blank")
        if not rationale.strip():
            raise ValueError("HITL rationale cannot be blank")
        is_valid, origin_reason = validate_approval_origin(
            {"reviewer": reviewer, "rationale": rationale, "decision": decision}
        )
        if not is_valid:
            raise PermissionError(origin_reason)
        with file_lock(self.lock_path):
            value = self._read(directive_id)
            self._require_open(value)
            self._assert_packages_unchanged(value)
            self._assert_active_baseline(value)
            self._assert_batch_admission_unchanged(value)
            classification, _path = self._load_current_classification(value)
            if classification_digest != classification["classification_digest"]:
                raise PermissionError(
                    "HITL decision must bind the exact classification briefing "
                    "that was shown to the operator"
                )
            current = value["approval"]
            if current is not None:
                if (
                    normalized_decision == "APPROVE"
                    and current.get("classification_digest")
                    == classification["classification_digest"]
                    and current["package_set_digest"] == value["package_set_digest"]
                    and current.get("reviewer") == reviewer.strip()
                    and current.get("rationale") == rationale.strip()
                    and (
                        value.get("required_embedding_target") is None
                        or current.get("embedding_target_digest")
                        == digest(value.get("required_embedding_target"))
                    )
                ):
                    return value
                raise RuntimeError(
                    "ingestion directive already has a different HITL approval binding"
                )
            now = utc_now()
            if normalized_decision == "REJECT":
                value["status"] = "BLOCKED"
                value["terminal"] = True
                value["block"] = {
                    "reason_code": "HITL_REJECTED_CLASSIFICATION",
                    "reason": rationale.strip(),
                    "classification_digest": classification["classification_digest"],
                    "reviewer": reviewer.strip(),
                    "blocked_at": now,
                }
                event_type = "HITL_CLASSIFICATION_REJECTED"
            else:
                value["approval"] = self._human_approval_binding(
                    value,
                    classification=classification,
                    reviewer=reviewer,
                    rationale=rationale,
                    bound_at=now,
                )
                event_type = "HITL_CLASSIFICATION_APPROVED"
            value["updated_at"] = now
            value["events"].append(
                {
                    "sequence": len(value["events"]) + 1,
                    "event_type": event_type,
                    "recorded_at": now,
                    "package_set_digest": value["package_set_digest"],
                    "classification_digest": classification["classification_digest"],
                    "reviewer_digest": digest(reviewer.strip()),
                    "embedding_target_digest": digest(value.get("required_embedding_target")),
                }
            )
            sealed = _seal_directive(value)
            write_json(self._path(directive_id), sealed)
            return sealed

    def record_signed_batch_policy_admission(
        self,
        directive_id: str,
        *,
        authorization_context: AuthorizationContext,
    ) -> dict[str, Any]:
        """Bind a security-owner signed low-risk policy to one exact batch.

        The policy authorization is still source-bound: it must contain this
        manifest's file hash and the exact derived release approval resource.
        It is not a standing permission to ingest future package bytes.
        """

        with file_lock(self.lock_path):
            value = self._read(directive_id)
            self._require_open(value)
            self._assert_packages_unchanged(value)
            self._assert_active_baseline(value)
            self._assert_batch_admission_unchanged(value)
            recorded = value.get("batch_admission")
            if recorded is None:
                raise PermissionError(
                    "signed batch policy admission requires a bound batch admission manifest"
                )
            binding = binding_from_audit_record(
                {key: item for key, item in recorded.items() if key != "path"}
            )
            if binding.admission_mode != "SIGNED_POLICY":
                raise PermissionError(
                    "batch admission manifest is configured for explicit HITL approval"
                )
            classification, _path = self._load_current_classification(value)
            if (
                classification["recommended_classification"] not in {"public", "internal"}
                or classification["sensitive_signal_types"]
            ):
                raise PermissionError(
                    "signed batch policy admission is limited to non-sensitive "
                    "public/internal evidence"
                )
            approval_id = policy_approval_id(
                directive_id=directive_id,
                package_set_digest=value["package_set_digest"],
                binding=binding,
            )
            authorization_context.require(BATCH_ADMIT_CAPABILITY)
            authorization_context.require_resources(
                {
                    BATCH_MANIFEST_RESOURCE: binding.manifest_file_sha256,
                    "release_approval": sha256_bytes(approval_id.encode("utf-8")),
                }
            )
            current = value["approval"]
            if current is not None:
                if current.get("approval_id") == approval_id:
                    return value
                raise RuntimeError("ingestion directive already has a different approval binding")
            now = utc_now()
            value["approval"] = {
                "approval_id": approval_id,
                "binding_type": "SIGNED_BATCH_POLICY_ADMISSION",
                "bound_at": now,
                "package_set_digest": value["package_set_digest"],
                "classification_digest": classification["classification_digest"],
                "classification_id": classification["classification_id"],
                "decision": "APPROVE",
                "reviewer": authorization_context.subject,
                "rationale": (
                    f"Signed batch policy {binding.policy_id}@{binding.policy_version} "
                    "admitted this exact candidate-only package set."
                ),
                "batch_manifest_sha256": binding.manifest_file_sha256,
                "policy_id": binding.policy_id,
                "policy_version": binding.policy_version,
            }
            value["updated_at"] = now
            value["events"].append(
                {
                    "sequence": len(value["events"]) + 1,
                    "event_type": "SIGNED_BATCH_POLICY_ADMITTED",
                    "recorded_at": now,
                    "package_set_digest": value["package_set_digest"],
                    "classification_digest": classification["classification_digest"],
                    "batch_manifest_sha256": binding.manifest_file_sha256,
                    "batch_policy_id": binding.policy_id,
                    "authorization_context_digest": authorization_context.context_digest,
                }
            )
            sealed = _seal_directive(value)
            write_json(self._path(directive_id), sealed)
            return sealed

    def bind_approval(self, directive_id: str, approval_id: str) -> dict[str, Any]:
        """Compatibility failure for callers that still try to supply an approval ID."""

        raise RuntimeError(
            "approval IDs are no longer accepted; run intake-directive-classify "
            "and record a source-bound HITL decision"
        )

    def _assert_packages_unchanged(self, value: dict[str, Any]) -> None:
        for binding in value["packages"]:
            path = Path(binding["path"])
            try:
                current = self._package_binding(path)
            except (FileNotFoundError, RuntimeError) as exc:
                raise RuntimeError(
                    f"ingestion directive package bytes changed or disappeared: {path}"
                ) from exc
            if current["sha256"] != binding["sha256"] or current["bytes"] != binding["bytes"]:
                raise RuntimeError(
                    f"ingestion directive package bytes changed or disappeared: {path}"
                )

    def _assert_active_baseline(self, value: dict[str, Any]) -> None:
        active = self.manager.active_pair()
        baseline = value["baseline_pair"]
        if (
            active["generation_id"] != baseline["generation_id"]
            or active["graph_sha256"] != baseline["graph_sha256"]
            or active["sidecar_sha256"] != baseline["sidecar_sha256"]
        ):
            raise RuntimeError(
                "active graph/index pair drifted after the ingestion directive opened"
            )
        self._assert_pair_matches_embedding_target(value, active, stage="active baseline")

    def assert_embedding_requirement(
        self, directive_id: str, *, require_bge_m3: bool = False
    ) -> dict[str, Any]:
        """Verify the directive and current pair satisfy an operator requirement."""

        with file_lock(self.lock_path):
            value = self._read(directive_id)
            target = value.get("required_embedding_target")
            if require_bge_m3:
                if target is None:
                    raise PermissionError(
                        "existing directive is not bound to a required embedding target"
                    )
                self._require_bge_m3_target(target)
            self._assert_pair_matches_embedding_target(
                value, self.manager.active_pair(), stage="current active"
            )
            return {
                "status": "PASS",
                "directive_id": directive_id,
                "required_embedding_target": target,
                "bge_m3_required": require_bge_m3,
            }

    def authorize_intake(self, directive_id: str, package: Path) -> dict[str, Any]:
        binding = self._package_binding(package)
        with file_lock(self.lock_path):
            value = self._read(directive_id)
            self._require_open(value)
            self._assert_packages_unchanged(value)
            self._assert_batch_admission_unchanged(value)
            self._assert_active_baseline(value)
            expected = {_package_path_key(item["path"]): item for item in value["packages"]}.get(
                _package_path_key(binding["path"])
            )
            if (
                expected is None
                or expected["sha256"] != binding["sha256"]
                or expected["bytes"] != binding["bytes"]
            ):
                raise PermissionError("intake package is not bound to the open ingestion directive")
            return {
                "status": "PASS",
                "directive_id": directive_id,
                "package_sha256": binding["sha256"],
                "baseline_generation_id": value["baseline_pair"]["generation_id"],
            }

    def _run_evidence(
        self,
        run_id: str,
        *,
        required_stages: list[str],
        activation_id: str | None = None,
    ) -> dict[str, Any]:
        if not RUN_ID_PATTERN.fullmatch(run_id):
            raise ValueError(f"unsafe intake run ID: {run_id!r}")
        run_dir = assert_safe_mutable_path(
            self.runtime_root, self.runtime_root / "runs" / run_id, label="ingestion completion run directory"
        )
        summary = read_json(run_dir / "intake_summary.json")
        if summary.get("status") != "COMPLETED" or summary.get("run_id") != run_id:
            raise RuntimeError(f"intake run is not complete: {run_id}")
        event_paths = sorted((run_dir / "learning_events").glob("*.json"), key=lambda p: (int(p.name.split("-", 1)[0]) if p.name.split("-", 1)[0].isdigit() else 0, p.name))
        if not event_paths:
            raise RuntimeError(f"intake run has no learning ledger: {run_id}")
        events = [read_json(path) for path in event_paths]
        first = events[0]
        recorder = RunRecorder(
            self.runtime_root,
            run_id=run_id,
            trace_id=first["trace_id"],
            use_case=first["use_case"],
            graph_snapshot=first.get("graph_snapshot"),
            model_or_index_generation=first.get("model_or_index_generation"),
        )
        audit = recorder.audit(required_stages)
        if audit["status"] != "PASS":
            raise RuntimeError(f"intake run is missing required completion stages: {run_id}")
        activation_ledger_count = 0
        if activation_id is not None:
            for event in events:
                if event.get("event_type") != "release_receipt":
                    continue
                payload_name = event["payload_reference"].rsplit("/", 1)[-1]
                payload = read_json(recorder.payload_dir / payload_name)
                if payload.get("activation_id") == activation_id:
                    activation_ledger_count += 1
            if activation_ledger_count != 1:
                raise RuntimeError(f"intake run lacks the exact activation receipt event: {run_id}")
        return {
            "run_id": run_id,
            "trace_id": summary["trace_id"],
            "source_id": summary["source_id"],
            "package_file_sha256": summary["package_file_sha256"],
            "graph_sha256_before": summary["graph_sha256_before"],
            "graph_sha256_after": summary["graph_sha256_after"],
            "audit_status": audit["status"],
            "event_count": audit["event_count"],
            "event_chain_digest": audit["event_chain_digest"],
            "automatic_policy_change_count": audit["automatic_policy_change_count"],
            "activation_ledger_count": activation_ledger_count,
            "graph_operation_count": summary["graph_operation_count"],
            "graph_change_set_id": summary["graph_change_set_id"],
        }

    @staticmethod
    def _receipt_run_ids(receipt: dict[str, Any]) -> list[str]:
        components = receipt.get("component_runs") or [
            {
                "run_id": receipt["run_id"],
                "source_id": receipt["source_id"],
                "trace_id": receipt["trace_id"],
            }
        ]
        run_ids = [component["run_id"] for component in components]
        if not run_ids or len(run_ids) != len(set(run_ids)):
            raise RuntimeError("release receipt has missing or duplicate component runs")
        if any(not RUN_ID_PATTERN.fullmatch(run_id) for run_id in run_ids):
            raise RuntimeError("release receipt has an unsafe component run ID")
        return run_ids

    def _validate_release_request_state(
        self,
        value: dict[str, Any],
        *,
        run_ids: list[str],
        approval_id: str,
        required_stages: list[str],
    ) -> list[dict[str, Any]]:
        self._require_open(value)
        self._assert_packages_unchanged(value)
        self._assert_batch_admission_unchanged(value)
        self._bound_approval_id(value, approval_id)
        if len(run_ids) != len(set(run_ids)):
            raise ValueError("release run IDs must be unique")
        evidence = [
            self._run_evidence(run_id, required_stages=required_stages) for run_id in run_ids
        ]
        expected_hashes = Counter(binding["sha256"] for binding in value["packages"])
        actual_hashes = Counter(item["package_file_sha256"] for item in evidence)
        if actual_hashes != expected_hashes:
            raise RuntimeError("release runs do not cover the directive's exact package set")
        baseline_hash = value["baseline_pair"]["graph_sha256"]
        for item in evidence:
            if (
                item["graph_sha256_before"] != baseline_hash
                or item["graph_sha256_after"] != baseline_hash
            ):
                raise RuntimeError("intake run is not proposal-only against the directive baseline")
        return evidence

    def validate_release_request(
        self,
        directive_id: str,
        *,
        run_ids: list[str],
        approval_id: str | None = None,
    ) -> dict[str, Any]:
        with file_lock(self.lock_path):
            value = self._read(directive_id)
            self._assert_active_baseline(value)
            bound_approval_id = self._bound_approval_id(value, approval_id)
            evidence = self._validate_release_request_state(
                value,
                run_ids=run_ids,
                approval_id=bound_approval_id,
                required_stages=INTAKE_STAGES,
            )
            return {
                "status": "PASS",
                "directive_id": directive_id,
                "approval_id": bound_approval_id,
                "run_ids": run_ids,
                "run_event_chain_digests": {
                    item["run_id"]: item["event_chain_digest"] for item in evidence
                },
            }

    def _receipt_path(self, receipt_path: Path) -> Path:
        resolved = Path(receipt_path).expanduser().resolve()
        try:
            resolved.relative_to(self.manager.releases_dir.resolve())
        except ValueError as exc:
            raise ValueError("release receipt is outside the runtime release directory") from exc
        if not resolved.is_file():
            raise FileNotFoundError(f"release receipt does not exist: {resolved}")
        return resolved

    def _preflight_activation_state(
        self, value: dict[str, Any], receipt_path: Path
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        receipt = _dispatch("read_json", read_json)(self._receipt_path(receipt_path))
        _dispatch("_verify_receipt", _verify_receipt)(receipt)
        if receipt.get("status") != "PREPARED":
            raise RuntimeError("release receipt is not prepared")
        run_ids = self._receipt_run_ids(receipt)
        evidence = self._validate_release_request_state(
            value,
            run_ids=run_ids,
            approval_id=receipt["approval_id"],
            required_stages=RELEASE_STAGES,
        )
        baseline = value["baseline_pair"]
        previous = receipt["previous_pair"]
        if (
            previous["generation_id"] != baseline["generation_id"]
            or previous["graph_sha256"] != baseline["graph_sha256"]
            or previous["sidecar_sha256"] != baseline["sidecar_sha256"]
        ):
            raise RuntimeError("release receipt is not based on the directive baseline")
        if receipt.get("pair_validation", {}).get("status") != "PASS":
            raise RuntimeError("release receipt pair validation did not pass")
        self._assert_pair_matches_embedding_target(
            value, receipt["next_pair"], stage="prepared release"
        )
        next_validation = self.manager.validate_pair(receipt["next_pair"])
        operation_count = receipt["graph_write_receipt"]["operation_count"]
        before_hash = previous["graph_sha256"]
        after_hash = receipt["next_pair"]["graph_sha256"]
        if not isinstance(operation_count, int) or isinstance(operation_count, bool):
            raise RuntimeError("release operation count is malformed")
        if operation_count <= 0:
            raise RuntimeError(
                "graph release requires positive graph operations; use "
                "intake-completion-noop for an all-zero-operation directive"
            )
        if before_hash == after_hash:
            raise RuntimeError("graph operations were recorded without a graph hash change")
        component_operation_ids = [
            operation_id
            for component in receipt.get("component_runs", [])
            for operation_id in component.get("operation_ids", [])
        ]
        if len(component_operation_ids) != operation_count or len(component_operation_ids) != len(
            set(component_operation_ids)
        ):
            raise RuntimeError("release operation receipt does not match component runs")
        active = self.manager.active_pair()
        allowed_generations = {
            previous["generation_id"],
            receipt["next_pair"]["generation_id"],
        }
        if active["generation_id"] not in allowed_generations:
            raise RuntimeError("active generation is unrelated to the directive release")
        return receipt, evidence, next_validation

    def preflight_activation(self, directive_id: str, receipt_path: Path) -> dict[str, Any]:
        with file_lock(self.lock_path):
            value = self._read(directive_id)
            self._require_open(value)
            receipt, evidence, next_validation = self._preflight_activation_state(
                value, receipt_path
            )
            return {
                "status": "PASS",
                "directive_id": directive_id,
                "receipt_id": receipt["receipt_id"],
                "run_ids": [item["run_id"] for item in evidence],
                "target_generation_id": receipt["next_pair"]["generation_id"],
                "target_pair_validation": next_validation,
            }

    def complete(self, directive_id: str, receipt_path: Path) -> dict[str, Any]:
        """Close a directive only after exact activation and ledger proof."""

        with file_lock(self.lock_path):
            value = self._read(directive_id)
            if value["status"] == "COMPLETE":
                completion = value["completion"]
                if completion["mutation_outcome"] == "IDEMPOTENT_NOOP":
                    raise RuntimeError("completed directive used the idempotent-noop terminal path")
                receipt = _dispatch("read_json", read_json)(self._receipt_path(receipt_path))
                _dispatch("_verify_receipt", _verify_receipt)(receipt)
                if completion["receipt_digest"] != receipt["receipt_digest"]:
                    raise RuntimeError(
                        "completed directive is bound to a different release receipt"
                    )
                return {
                    "status": "PASS",
                    "directive_id": directive_id,
                    "directive_status": "COMPLETE",
                    "terminal": True,
                    "completion": completion,
                    "mutation_summary": completion.get("mutation_summary"),
                    "idempotent": True,
                }
            self._require_open(value)
            receipt, _prepared_evidence, next_validation = self._preflight_activation_state(
                value, receipt_path
            )
            generation_id = receipt["next_pair"]["generation_id"]
            activation_path = self.manager.releases_dir / f"activation-{generation_id}.json"
            if not activation_path.is_file():
                raise RuntimeError("release has not been activated")
            activation = read_json(activation_path)
            _verify_activation_binding(activation, receipt)
            active = self.manager.active_pair()
            if active != activation["active_pair"]:
                raise RuntimeError("active pair does not match the activation receipt")
            self._assert_pair_matches_embedding_target(value, active, stage="activated")
            active_validation = self.manager.validate_pair(active)
            run_evidence = [
                self._run_evidence(
                    run_id,
                    required_stages=RELEASE_STAGES,
                    activation_id=activation["activation_id"],
                )
                for run_id in self._receipt_run_ids(receipt)
            ]
            operation_count = receipt["graph_write_receipt"]["operation_count"]
            outcome = "GRAPH_CHANGED" if operation_count else "IDEMPOTENT_NOOP"
            mutation_summary = receipt.get("mutation_summary") or build_graph_mutation_summary(
                generation_before=receipt["previous_pair"]["generation_id"],
                generation_after=active["generation_id"],
                graph_sha256_before=receipt["previous_pair"]["graph_sha256"],
                graph_sha256_after=active["graph_sha256"],
                before_sidecar_path=(
                    self.runtime_root / receipt["previous_pair"]["sidecar_path"]
                    if receipt.get("previous_pair", {}).get("sidecar_path")
                    else None
                ),
                after_sidecar_path=self.runtime_root / active["sidecar_path"],
                sidecar_sha256_before=receipt["previous_pair"].get("sidecar_sha256"),
                sidecar_sha256_after=active.get("sidecar_sha256"),
                total_operations=operation_count,
                actual_graph_delta=receipt.get("graph_delta_summary"),
                status="GRAPH_MUTATED" if operation_count else "IDEMPOTENT_NOOP",
            )
            allow_deletions = bool(value.get("allow_deletions", False))
            monotonicity_report = None
            delta_summary = receipt.get("graph_delta_summary")
            if isinstance(delta_summary, dict) and isinstance(delta_summary.get("table"), list):
                delta_table = delta_summary["table"]
                before_counts = {
                    row["component_key"]: row.get("baseline", 0)
                    for row in delta_table
                    if "component_key" in row
                }
                after_counts = {
                    row["component_key"]: row.get("actual_after", 0)
                    for row in delta_table
                    if "component_key" in row
                }
                monotonicity_report = verify_structural_monotonicity(
                    before_counts, after_counts, allow_deletions=allow_deletions
                )
            v0_receipt = None
            if outcome == "GRAPH_CHANGED":
                graph_full_path = self.runtime_root / active["graph_path"]
                sidecar_full_path = self.runtime_root / active["sidecar_path"]
                if graph_full_path.is_file() and sidecar_full_path.is_file():
                    from ..index_core.v0_audit import verify_v0_fact_reachability
                    v0_receipt = verify_v0_fact_reachability(
                        graph_path=graph_full_path,
                        sidecar_path=sidecar_full_path,
                        generation_id=active["generation_id"],
                        directive_id=directive_id,
                        runtime_root=self.runtime_root,
                    )
                    if v0_receipt["status"] != "PASS":
                        raise RuntimeError(
                            f"V0_FACT_REACHABILITY audit failed: {v0_receipt.get('error', 'unreachable facts detected')}"
                        )

            proof = {
                "status": "PASS",
                "completed_at": utc_now(),
                "approval_id": receipt["approval_id"],
                "package_set_digest": value["package_set_digest"],
                "run_ids": [item["run_id"] for item in run_evidence],
                "run_receipts": run_evidence,
                "receipt_path": str(self._receipt_path(receipt_path)),
                "receipt_id": receipt["receipt_id"],
                "receipt_digest": receipt["receipt_digest"],
                "activation_path": str(activation_path.resolve()),
                "activation_id": activation["activation_id"],
                "activation_digest": activation["receipt_digest"],
                "generation_before": receipt["previous_pair"]["generation_id"],
                "generation_after": active["generation_id"],
                "graph_sha256_before": receipt["previous_pair"]["graph_sha256"],
                "graph_sha256_after": active["graph_sha256"],
                "graph_operation_count": operation_count,
                "mutation_outcome": outcome,
                "mutation_summary": mutation_summary,
                "monotonicity_verified": True if monotonicity_report else None,
                "monotonicity_report": monotonicity_report,
                "required_embedding_target": value.get("required_embedding_target"),
                "embedding_target_verified": (value.get("required_embedding_target") is not None),
                "target_pair_validation": next_validation,
                "active_pair_validation": active_validation,
                "v0_fact_reachability": v0_receipt,
            }
            proof["completion_digest"] = digest(proof)
            now = proof["completed_at"]
            value["status"] = "COMPLETE"
            value["terminal"] = True
            value["completion"] = proof
            value["updated_at"] = now
            value["events"].append(
                {
                    "sequence": len(value["events"]) + 1,
                    "event_type": "DIRECTIVE_COMPLETED",
                    "recorded_at": now,
                    "completion_digest": proof["completion_digest"],
                    "receipt_digest": receipt["receipt_digest"],
                    "activation_digest": activation["receipt_digest"],
                    "mutation_outcome": outcome,
                }
            )
            sealed = _seal_directive(value)
            write_json(self._path(directive_id), sealed)
            return {
                "status": "PASS",
                "directive_id": directive_id,
                "directive_status": "COMPLETE",
                "terminal": True,
                "completion": proof,
                "mutation_summary": mutation_summary,
                "idempotent": False,
            }

    def complete_noop(
        self,
        directive_id: str,
        *,
        run_ids: list[str],
        approval_id: str | None = None,
    ) -> dict[str, Any]:
        """Close an all-zero-operation directive without fabricating a release."""

        with file_lock(self.lock_path):
            value = self._read(directive_id)
            if value["status"] == "COMPLETE":
                completion = value["completion"]
                if completion["mutation_outcome"] != "IDEMPOTENT_NOOP":
                    raise RuntimeError("completed directive used the graph-release terminal path")
                if completion["run_ids"] != run_ids or (
                    approval_id is not None and completion["approval_id"] != approval_id
                ):
                    raise RuntimeError(
                        "completed no-op directive has different run or approval bindings"
                    )
                return {
                    "status": "PASS",
                    "directive_id": directive_id,
                    "directive_status": "COMPLETE",
                    "terminal": True,
                    "completion": completion,
                    "mutation_summary": completion.get("mutation_summary"),
                    "idempotent": True,
                }
            self._require_open(value)
            self._assert_active_baseline(value)
            bound_approval_id = self._bound_approval_id(value, approval_id)
            run_evidence = self._validate_release_request_state(
                value,
                run_ids=run_ids,
                approval_id=bound_approval_id,
                required_stages=INTAKE_STAGES,
            )
            if any(item["graph_operation_count"] != 0 for item in run_evidence):
                raise RuntimeError(
                    "idempotent-noop completion requires zero graph operations in every run"
                )
            active = self.manager.active_pair()
            active_validation = self.manager.validate_pair(active)
            baseline = value["baseline_pair"]
            if (
                active["generation_id"] != baseline["generation_id"]
                or active["graph_sha256"] != baseline["graph_sha256"]
                or active["sidecar_sha256"] != baseline["sidecar_sha256"]
            ):
                raise RuntimeError("idempotent-noop completion changed the active pair")
            now = utc_now()
            receipt_id = stable_id(
                "ingestion-noop-receipt",
                directive_id,
                value["package_set_digest"],
                run_ids,
                baseline["generation_id"],
                baseline["graph_sha256"],
                length=28,
            )
            noop_receipt = {
                "schema_version": "1.0.0",
                "receipt_id": receipt_id,
                "status": "IDEMPOTENT_NOOP",
                "directive_id": directive_id,
                "approval_id": bound_approval_id,
                "package_set_digest": value["package_set_digest"],
                "run_ids": run_ids,
                "graph_operation_count": 0,
                "active_generation_id": active["generation_id"],
                "active_graph_sha256": active["graph_sha256"],
                "created_at": now,
            }
            noop_receipt["receipt_digest"] = digest(noop_receipt)
            receipt_path = self.manager.releases_dir / f"noop-{receipt_id}.json"
            assert_safe_mutable_path(
                self.runtime_root,
                receipt_path,
                label="ingestion no-op receipt path",
            )
            if receipt_path.is_file():
                existing = read_json(receipt_path)
                declared = existing.get("receipt_digest")
                actual = digest(
                    {key: item for key, item in existing.items() if key != "receipt_digest"}
                )
                expected_bindings = {
                    key: item
                    for key, item in noop_receipt.items()
                    if key not in {"created_at", "receipt_digest"}
                }
                existing_bindings = {
                    key: item
                    for key, item in existing.items()
                    if key not in {"created_at", "receipt_digest"}
                }
                if declared != actual or existing_bindings != expected_bindings:
                    raise RuntimeError("idempotent-noop receipt collision")
                noop_receipt = existing
            else:
                write_json_exclusive(receipt_path, noop_receipt)
            mutation_summary = build_graph_mutation_summary(
                generation_before=active["generation_id"],
                generation_after=active["generation_id"],
                graph_sha256_before=active["graph_sha256"],
                graph_sha256_after=active["graph_sha256"],
                before_sidecar_path=self.runtime_root / active["sidecar_path"],
                after_sidecar_path=self.runtime_root / active["sidecar_path"],
                sidecar_sha256_before=active.get("sidecar_sha256"),
                sidecar_sha256_after=active.get("sidecar_sha256"),
                total_operations=0,
                status="IDEMPOTENT_NOOP",
            )
            proof = {
                "status": "PASS",
                "completed_at": now,
                "approval_id": bound_approval_id,
                "package_set_digest": value["package_set_digest"],
                "run_ids": run_ids,
                "run_receipts": run_evidence,
                "receipt_path": str(receipt_path.resolve()),
                "receipt_id": receipt_id,
                "receipt_digest": noop_receipt["receipt_digest"],
                "activation_path": None,
                "activation_id": None,
                "activation_digest": None,
                "activation_not_applicable_reason": (
                    "The exact intake runs proposed zero governed graph operations; "
                    "the release manager correctly forbids an empty release."
                ),
                "generation_before": active["generation_id"],
                "generation_after": active["generation_id"],
                "graph_sha256_before": active["graph_sha256"],
                "graph_sha256_after": active["graph_sha256"],
                "graph_operation_count": 0,
                "mutation_outcome": "IDEMPOTENT_NOOP",
                "mutation_summary": mutation_summary,
                "required_embedding_target": value.get("required_embedding_target"),
                "embedding_target_verified": (value.get("required_embedding_target") is not None),
                "executed_stages": INTAKE_STAGES,
                "not_applicable_stages": {
                    "W0": "No governed graph operations existed to write.",
                    "E0": "No new graph/index generation existed to activate.",
                    "V0": "No new graph facts existed to evaluate for reachability.",
                },
                "active_pair_validation": active_validation,
                "v0_fact_reachability": {
                    "status": "NOT_APPLICABLE",
                    "reason": "No new graph facts existed to evaluate for reachability.",
                },
            }
            proof["completion_digest"] = digest(proof)
            value["status"] = "COMPLETE"
            value["terminal"] = True
            value["completion"] = proof
            value["updated_at"] = now
            value["events"].append(
                {
                    "sequence": len(value["events"]) + 1,
                    "event_type": "DIRECTIVE_COMPLETED_NOOP",
                    "recorded_at": now,
                    "completion_digest": proof["completion_digest"],
                    "receipt_digest": noop_receipt["receipt_digest"],
                    "mutation_outcome": "IDEMPOTENT_NOOP",
                }
            )
            sealed = _seal_directive(value)
            write_json(self._path(directive_id), sealed)
            return {
                "status": "PASS",
                "directive_id": directive_id,
                "directive_status": "COMPLETE",
                "terminal": True,
                "completion": proof,
                "mutation_summary": mutation_summary,
                "idempotent": False,
            }

    def block(self, directive_id: str, *, reason_code: str, reason: str) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", reason_code):
            raise ValueError("block reason code must be uppercase snake case")
        if not reason.strip():
            raise ValueError("block reason cannot be blank")
        with file_lock(self.lock_path):
            value = self._read(directive_id)
            self._require_open(value)
            now = utc_now()
            block = {
                "reason_code": reason_code,
                "reason": reason.strip(),
                "blocked_at": now,
            }
            value["status"] = "BLOCKED"
            value["terminal"] = True
            value["block"] = block
            value["updated_at"] = now
            value["events"].append(
                {
                    "sequence": len(value["events"]) + 1,
                    "event_type": "DIRECTIVE_BLOCKED",
                    "recorded_at": now,
                    "reason_code": reason_code,
                    "reason_digest": digest(reason.strip()),
                }
            )
            sealed = _seal_directive(value)
            write_json(self._path(directive_id), sealed)
            return sealed

    def cancel(self, directive_id: str, *, reason: str) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("cancellation reason cannot be blank")
        with file_lock(self.lock_path):
            value = self._read(directive_id)
            self._require_open(value)
            now = utc_now()
            cancellation = {"reason": reason.strip(), "cancelled_at": now}
            value["status"] = "CANCELLED"
            value["terminal"] = True
            value["cancellation"] = cancellation
            value["updated_at"] = now
            value["events"].append(
                {
                    "sequence": len(value["events"]) + 1,
                    "event_type": "DIRECTIVE_CANCELLED",
                    "recorded_at": now,
                    "reason_digest": digest(reason.strip()),
                }
            )
            sealed = _seal_directive(value)
            write_json(self._path(directive_id), sealed)
            return sealed
