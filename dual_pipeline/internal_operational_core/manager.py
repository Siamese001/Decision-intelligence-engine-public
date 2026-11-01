"""Internal operational manager for qualification, status, recovery, and revocation."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from dual_pipeline.assurance import (
    DEFAULT_INTERNAL_OPERATIONAL_POLICY,
    INTERNAL_BACKEND_ID,
    INTERNAL_OPERATIONAL,
    internal_operational_status,
    load_internal_operational_policy,
    pair_assurance_tier,
    pair_binding,
    policy_digest,
    qualification_path,
    revocations_dir,
    validate_internal_backend_policy,
)
from dual_pipeline.contracts import create_artifact, validate_artifact
from dual_pipeline.core import (
    assert_safe_mutable_path,
    read_json,
    stable_id,
    utc_now,
    write_json_exclusive,
)
from dual_pipeline.index import EmbeddingBackend, HybridRetriever
from dual_pipeline.release import ReleaseManager

from .evaluation import (
    _directive_binding,
    _evaluate_target_suite,
    _recovery_evidence,
    _source_evaluation_binding,
)


class InternalOperationalManager:
    def __init__(
        self,
        runtime_root: Path,
        seed_graph: Path,
        *,
        policy_path: Path = DEFAULT_INTERNAL_OPERATIONAL_POLICY,
    ) -> None:
        self.manager = ReleaseManager(runtime_root, seed_graph)
        self.runtime_root = self.manager.runtime_root
        self.policy_path = Path(policy_path).expanduser().resolve()

    def status(self) -> dict[str, Any]:
        pair = self.manager.active_pair()
        return internal_operational_status(
            self.runtime_root,
            pair,
            policy_path=self.policy_path,
        )

    def qualify(
        self,
        *,
        directive_path: Path,
        source_evaluation_path: Path,
        suite_path: Path,
        backend: EmbeddingBackend,
        reviewer: str,
        rationale: str,
    ) -> dict[str, Any]:
        if not reviewer.strip() or not rationale.strip():
            raise ValueError("qualification reviewer and rationale are required")
        pair = self.manager.active_pair()
        pair_validation = self.manager.validate_pair(pair)
        policy = load_internal_operational_policy(self.policy_path)
        if pair_assurance_tier(pair) != INTERNAL_OPERATIONAL:
            raise PermissionError("internal operational qualification requires an internal pair")
        if pair["embedding_backend"] != INTERNAL_BACKEND_ID:
            raise PermissionError("active pair does not use the internal BGE-M3 backend")
        if pair["production_qualified"]:
            raise PermissionError("enterprise-production pairs cannot use internal qualification")
        if backend.backend_id != pair["embedding_backend"]:
            raise RuntimeError("qualification backend does not match the active pair")
        validate_internal_backend_policy(
            policy=policy,
            model_id=backend.model_id,
            model_revision=backend.model_revision,
            model_manifest_digest=backend.model_manifest_digest,
        )
        if pair.get("internal_operational_policy_digest") != policy_digest(policy):
            raise RuntimeError("active pair is not bound to the current internal policy")
        if (
            backend.model_id != pair["model_id"]
            or backend.model_revision != pair["model_revision"]
            or backend.model_manifest_digest != pair["model_manifest_digest"]
        ):
            raise RuntimeError("qualification backend model does not match active pair")
        directive, directive_binding = _directive_binding(directive_path)
        if directive["completion"]["graph_sha256_after"] != pair["graph_sha256"]:
            raise RuntimeError("completed directive graph does not match the internal target pair")
        source_evaluation = _source_evaluation_binding(
            source_evaluation_path,
            target_pair=pair,
        )
        retriever = HybridRetriever(
            self.manager._resolve(pair["sidecar_path"]),
            backend,
        )
        target_suite = _evaluate_target_suite(
            suite_path,
            retriever=retriever,
            graph_path=self.manager._resolve(pair["graph_path"]),
            policy=policy,
        )
        if target_suite["status"] != "PASS":
            raise RuntimeError(f"internal operational target suite failed: {target_suite['failed_case_ids']}")
        sidecar_validation = pair_validation["sidecar_validation"]
        counts = sidecar_validation["counts"]
        if (
            counts["records"] != counts["dense"]
            or counts["records"] != counts["sparse_records"]
            or counts["records"] != counts["colbert_records"]
            or sidecar_validation["lane_contamination_count"] != 0
            or sidecar_validation["retrieval_metadata_binding_contamination_count"] != 0
        ):
            raise RuntimeError("internal operational sidecar completeness gate failed")
        label_quality = pair_validation["label_quality"]
        if label_quality["status"] not in {"PASS", "PASS_WITH_LEGACY_DEBT"}:
            raise RuntimeError("internal operational graph label-quality gate failed")
        recovery = _recovery_evidence(self.runtime_root, pair, self.manager)
        qualification_id = stable_id(
            "internal-operational-qualification",
            pair["generation_id"],
            pair["graph_sha256"],
            pair["sidecar_sha256"],
            policy_digest(policy),
            directive["directive_digest"],
            source_evaluation["evaluation_digest"],
            target_suite["suite_digest"],
            length=28,
        )
        receipt = create_artifact(
            "internal-operational-qualification-receipt",
            qualification_id,
            qualification_id=qualification_id,
            status="QUALIFIED",
            assurance_tier=INTERNAL_OPERATIONAL,
            operational_status="qualified",
            deployment_scope="local_team",
            authorization_mode=policy["authorization_mode"],
            production_qualified=False,
            autonomous_graph_writes_allowed=False,
            pair=pair_binding(pair),
            policy_binding={
                "policy_id": policy["policy_id"],
                "policy_version": policy["policy_version"],
                "policy_path": str(self.policy_path),
                "policy_digest": policy_digest(policy),
            },
            directive_binding=directive_binding,
            source_evaluation_binding=source_evaluation,
            target_validation={
                "pair_validation_status": pair_validation["status"],
                "graph_integrity_status": pair_validation["graph_integrity"]["integrity_check"],
                "graph_foreign_key_violation_count": pair_validation["graph_integrity"]["foreign_key_violation_count"],
                "sidecar_validation_status": sidecar_validation["status"],
                "lane_contamination_count": sidecar_validation["lane_contamination_count"],
                "retrieval_metadata_binding_contamination_count": sidecar_validation["retrieval_metadata_binding_contamination_count"],
                "record_count": counts["records"],
                "dense_record_count": counts["dense"],
                "sparse_record_count": counts["sparse_records"],
                "colbert_record_count": counts["colbert_records"],
                "label_quality_status": label_quality["status"],
                "legacy_debt_count": label_quality["legacy_debt_count"],
                "blocking_violation_count": label_quality["blocking_violation_count"],
                "label_quality_report_digest": label_quality["report_digest"],
            },
            target_suite=target_suite,
            recovery_evidence=recovery,
            reviewer=reviewer.strip(),
            rationale=rationale.strip(),
            qualified_at=utc_now(),
        )
        output = qualification_path(self.runtime_root, pair["generation_id"])
        assert_safe_mutable_path(
            self.runtime_root,
            output,
            label="internal operational qualification receipt",
        )
        if output.exists():
            existing = read_json(output)
            validate_artifact(existing)
            if (
                existing["qualification_id"] != receipt["qualification_id"]
                or existing["pair"] != receipt["pair"]
                or existing["policy_binding"] != receipt["policy_binding"]
            ):
                raise FileExistsError("a different qualification already exists for the active generation")
            return {
                "status": "QUALIFIED",
                "idempotent_replay": True,
                "qualification": existing,
                "qualification_path": str(output),
            }
        output.parent.mkdir(parents=True, exist_ok=True)
        write_json_exclusive(output, receipt)
        return {
            "status": "QUALIFIED",
            "idempotent_replay": False,
            "qualification": receipt,
            "qualification_path": str(output),
        }

    def revoke(
        self,
        *,
        reviewer: str,
        reason: str,
    ) -> dict[str, Any]:
        if not reviewer.strip() or not reason.strip():
            raise ValueError("revocation reviewer and reason are required")
        pair = self.manager.active_pair()
        status = internal_operational_status(
            self.runtime_root,
            pair,
            policy_path=self.policy_path,
        )
        if status["operational_status"] != "QUALIFIED":
            raise RuntimeError("only a currently qualified internal operational pair can be revoked")
        qualification = read_json(Path(status["qualification_path"]))
        revocation_id = stable_id(
            "internal-operational-revocation",
            qualification["qualification_id"],
            qualification["canonical_digest"]["value"],
            reviewer.strip(),
            reason.strip(),
            length=28,
        )
        receipt = create_artifact(
            "internal-operational-revocation-receipt",
            revocation_id,
            revocation_id=revocation_id,
            qualification_id=qualification["qualification_id"],
            qualification_digest=qualification["canonical_digest"]["value"],
            pair=qualification["pair"],
            status="REVOKED",
            reviewer=reviewer.strip(),
            reason=reason.strip(),
            revoked_at=utc_now(),
        )
        output = revocations_dir(self.runtime_root) / f"revocation-{revocation_id}.json"
        assert_safe_mutable_path(
            self.runtime_root,
            output,
            label="internal operational revocation receipt",
        )
        if not output.exists():
            output.parent.mkdir(parents=True, exist_ok=True)
            write_json_exclusive(output, receipt)
        return {
            "status": "REVOKED",
            "revocation": read_json(output),
            "revocation_path": str(output),
        }
