"""Automatic retrieval release suite builder."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..contracts import create_artifact, validate_artifact
from ..core import (
    assert_safe_mutable_path,
    digest,
    exact_file_copy,
    read_json,
    sha256_file,
    stable_id,
    write_json_exclusive,
)
from ..release_core.helpers import _verify_receipt
from ..release_core.manager import ReleaseManager
from ..retrieval_release_core.metrics import _records_by_id, _scoped_release_delta
from ..retrieval_release_core.policies import _load_policy, _policy_binding
from .cases import (
    _core_case,
    _delta_group_cases,
    _groups,
    _record,
    _removed_group_cases,
    _retrieval_expected,
)
from .exceptions import AutomaticSuiteBuildError
from .policy import _load_generation_policy


class AutomaticRetrievalReleaseSuiteBuilder:
    """Build and persist one deterministic suite for a prepared release."""

    def __init__(self, runtime_root: Path, seed_graph: Path | None = None):
        self.manager = ReleaseManager(runtime_root, seed_graph)
        self.runtime_root = self.manager.runtime_root
        self.suites_dir = assert_safe_mutable_path(
            self.runtime_root,
            self.runtime_root / "retrieval_evaluation_suites",
            label="retrieval evaluation suite directory",
        )

    def _automatic_path(self, receipt_id: str) -> Path:
        return assert_safe_mutable_path(
            self.runtime_root,
            self.suites_dir / "automatic" / f"retrieval-release-suite-{receipt_id}.json",
            label="automatic retrieval evaluation suite path",
        )

    def _explicit_path(self, suite_path: Path) -> Path:
        file_hash = sha256_file(suite_path)
        return assert_safe_mutable_path(
            self.runtime_root,
            self.suites_dir / "explicit" / f"retrieval-release-suite-{file_hash}.json",
            label="explicit retrieval evaluation suite path",
        )

    def persist_explicit(self, suite_path: Path) -> dict[str, Any]:
        source = Path(suite_path).expanduser().resolve()
        suite = read_json(source)
        validate_artifact(suite)
        if (
            suite.get("contract_id")
            != "urn:nee:dual-pipeline:retrieval-release-suite:1.0.0"
        ):
            raise AutomaticSuiteBuildError(
                "EXPLICIT_SUITE_CONTRACT",
                "explicit suite has the wrong contract",
            )
        target = self._explicit_path(source)
        if target.is_file():
            if sha256_file(target) != sha256_file(source):
                raise AutomaticSuiteBuildError(
                    "EXPLICIT_SUITE_COLLISION",
                    "persisted explicit suite hash does not match its source",
                )
        else:
            exact_file_copy(source, target)
        return {
            "source": "explicit",
            "path": str(target),
            "suite_id": suite["suite_id"],
            "suite_digest": suite["canonical_digest"]["value"],
            "suite_file_sha256": sha256_file(target),
        }

    def build(
        self,
        receipt_path: Path,
        *,
        output_path: Path | None = None,
    ) -> dict[str, Any]:
        """Build one sealed suite, optionally at an explicit runtime-local path.

        Explicit paths are for repairing an already persisted, failed automatic
        suite.  They are still deterministic from the prepared receipt and are
        copied into the governed explicit-suite directory before evaluation.
        """
        receipt_file = Path(receipt_path).expanduser().resolve()
        try:
            receipt_file.relative_to(self.manager.releases_dir.resolve())
        except ValueError as exc:
            raise AutomaticSuiteBuildError(
                "RECEIPT_SCOPE",
                "prepared receipt must be under the runtime releases directory",
            ) from exc
        receipt = read_json(receipt_file)
        _verify_receipt(receipt)
        if receipt.get("status") != "PREPARED":
            raise AutomaticSuiteBuildError(
                "RECEIPT_STATUS",
                "only a prepared release can receive an automatic suite",
            )
        baseline = _records_by_id(
            self.manager._resolve(receipt["previous_pair"]["sidecar_path"])
        )
        candidate = _records_by_id(
            self.manager._resolve(receipt["next_pair"]["sidecar_path"])
        )
        added_ids, removed_ids = _scoped_release_delta(
            self.runtime_root,
            receipt,
            baseline,
            candidate,
        )
        baseline_records = [_record(value) for value in baseline.values()]
        added_records = [_record(candidate[record_id]) for record_id in added_ids]
        removed_records = [_record(baseline[record_id]) for record_id in removed_ids]
        generation_policy = _load_generation_policy()
        cases = [
            _core_case(
                baseline_records,
                generation_policy,
                candidate_record_ids=set(candidate),
            )
        ]
        coverage: list[dict[str, Any]] = []
        added_groups = _groups(
            added_records,
            max_size=int(generation_policy["max_records_per_group"]),
        )
        for ordinal, records in enumerate(added_groups, 1):
            group_cases, case_ids, relationship = _delta_group_cases(
                records,
                ordinal=ordinal,
                generation_policy=generation_policy,
            )
            cases.extend(group_cases)
            coverage.extend(
                {
                    "record_id": record["record_id"],
                    "change_kind": "added_or_changed",
                    "retrieval_expected": _retrieval_expected(record),
                    "case_ids": case_ids,
                    "relationship_applicable": relationship,
                    "stale_or_superseded_applicable": False,
                }
                for record in records
            )
        removed_groups = _groups(
            removed_records,
            max_size=int(generation_policy["max_records_per_group"]),
        )
        for ordinal, records in enumerate(removed_groups, 1):
            group_cases, case_ids, relationship = _removed_group_cases(
                records,
                ordinal=ordinal,
                generation_policy=generation_policy,
            )
            cases.extend(group_cases)
            coverage.extend(
                {
                    "record_id": record["record_id"],
                    "change_kind": "removed",
                    "retrieval_expected": _retrieval_expected(record),
                    "case_ids": case_ids,
                    "relationship_applicable": relationship,
                    "stale_or_superseded_applicable": True,
                }
                for record in records
            )
        release_policy = _load_policy()
        generation_binding = {
            "policy_id": generation_policy["policy_id"],
            "policy_version": generation_policy["policy_version"],
            "policy_digest": digest(generation_policy),
            "owner": generation_policy["owner"],
            "generator_version": generation_policy["generator_version"],
        }
        suite_id = stable_id(
            "retrieval-release-suite",
            receipt["receipt_id"],
            receipt["receipt_digest"],
            generation_binding,
            length=28,
        )
        suite = create_artifact(
            "retrieval-release-suite",
            suite_id,
            suite_id=suite_id,
            execution_tier="E3",
            acceptance_policy=_policy_binding(release_policy),
            cases=cases,
            coverage=coverage,
            created_at=receipt["prepared_at"],
            extensions={
                "com.partner_advisory.nee.automatic-suite": {
                    "source": "governance_owned_templates",
                    "prepared_receipt_id": receipt["receipt_id"],
                    "prepared_receipt_digest": receipt["receipt_digest"],
                    "generation_policy": generation_binding,
                }
            },
        )
        validate_artifact(suite)
        target = (
            self._automatic_path(receipt["receipt_id"])
            if output_path is None
            else assert_safe_mutable_path(
                self.runtime_root,
                Path(output_path).expanduser().resolve(),
                label="generated retrieval evaluation suite path",
            )
        )
        if target.is_file():
            existing = read_json(target)
            validate_artifact(existing)
            if existing != suite:
                target.unlink(missing_ok=True)
                write_json_exclusive(target, suite)
        else:
            write_json_exclusive(target, suite)
        return {
            "source": "automatic" if output_path is None else "generated_override",
            "path": str(target),
            "suite_id": suite["suite_id"],
            "suite_digest": suite["canonical_digest"]["value"],
            "suite_file_sha256": sha256_file(target),
            "case_count": len(cases),
            "added_or_changed_record_count": len(added_ids),
            "removed_record_count": len(removed_ids),
            "generation_policy": generation_binding,
        }

    def resolve(
        self,
        receipt_path: Path,
        *,
        explicit_suite: Path | None = None,
    ) -> dict[str, Any]:
        return (
            self.persist_explicit(explicit_suite)
            if explicit_suite is not None
            else self.build(receipt_path)
        )
