#!/usr/bin/env python3
"""Fail-closed controls for trusted-team internal BGE-M3 operation."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dual_pipeline.assurance import (
    DEFAULT_INTERNAL_OPERATIONAL_POLICY,
    DEVELOPMENT,
    ENTERPRISE_PRODUCTION,
    INTERNAL_OPERATIONAL,
    assurance_status_label,
    internal_operational_status,
    pair_binding,
    policy_digest,
    qualification_path,
    require_internal_operational_use,
)
from dual_pipeline.cli import build_parser
from dual_pipeline.core import read_json, seal_artifact
from dual_pipeline.index import (
    InternalOperationalFlagEmbeddingBackend,
    get_backend,
)
from dual_pipeline.query import QueryEngine
from dual_pipeline.release import ReleaseManager
from tests.embedding_fixture import BgeM3TestBackend

ROOT = Path(__file__).resolve().parents[1]
QUALIFICATION_FIXTURE = (
    ROOT
    / "contracts"
    / "dual_pipeline"
    / "fixtures"
    / "valid"
    / "internal-operational-qualification-receipt.json"
)
REVOCATION_FIXTURE = (
    ROOT
    / "contracts"
    / "dual_pipeline"
    / "fixtures"
    / "valid"
    / "internal-operational-revocation-receipt.json"
)


class InternalBgeStub(BgeM3TestBackend):
    """Fast test implementation carrying the exact governed internal identity."""

    backend_id = "flagembedding-bge-m3-internal"
    model_id = "BAAI/bge-m3"
    model_revision = "5617a9f61b028005a4858fdac845db406aefb181"
    model_manifest_digest = (
        "a598d96187ae03e498914543ca2637b95ef61c496faf4d5d9e471764f893905e"
    )
    production_qualified = False
    assurance_tier = INTERNAL_OPERATIONAL

    def __init__(self) -> None:
        policy = read_json(DEFAULT_INTERNAL_OPERATIONAL_POLICY)
        self.internal_operational_policy_path = str(
            DEFAULT_INTERNAL_OPERATIONAL_POLICY.resolve()
        )
        self.internal_operational_policy_digest = policy_digest(policy)
        self.snapshot_checks = 0

    def verify_snapshot(self) -> dict:
        self.snapshot_checks += 1
        verified = super().verify_snapshot()
        return {
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "manifest_digest": verified["manifest_digest"],
        }


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _qualification_for_pair(pair: dict) -> dict:
    value = copy.deepcopy(read_json(QUALIFICATION_FIXTURE))
    policy = read_json(DEFAULT_INTERNAL_OPERATIONAL_POLICY)
    value["pair"] = pair_binding(pair)
    value["policy_binding"] = {
        "policy_id": policy["policy_id"],
        "policy_version": policy["policy_version"],
        "policy_path": str(DEFAULT_INTERNAL_OPERATIONAL_POLICY.resolve()),
        "policy_digest": policy_digest(policy),
    }
    return seal_artifact(value)


class InternalOperationalAssuranceTests(unittest.TestCase):
    def test_operator_facing_assurance_labels_do_not_overload_production_flag(self) -> None:
        self.assertEqual(
            "internal-operational qualified",
            assurance_status_label(INTERNAL_OPERATIONAL, "QUALIFIED"),
        )
        self.assertEqual(
            "internal-operational qualification pending",
            assurance_status_label(INTERNAL_OPERATIONAL, "UNQUALIFIED"),
        )
        self.assertEqual(
            "enterprise-production authorization boundary",
            assurance_status_label(ENTERPRISE_PRODUCTION),
        )
        self.assertEqual("development", assurance_status_label(DEVELOPMENT))

    def test_internal_backend_and_cli_require_explicit_authorization(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "--allow-internal-operational"):
            get_backend("bge-m3-internal")

        with mock.patch.object(
            InternalOperationalFlagEmbeddingBackend,
            "__init__",
            return_value=None,
        ):
            backend = get_backend(
                "bge-m3-internal",
                allow_internal_backend=True,
            )
        self.assertEqual(
            "flagembedding-bge-m3-internal",
            backend.backend_id,
        )
        self.assertEqual(INTERNAL_OPERATIONAL, backend.assurance_tier)
        self.assertFalse(backend.production_qualified)

        args = build_parser().parse_args(
            [
                "internal-operational-qualify",
                "--directive",
                "directive.json",
                "--source-evaluation",
                "evaluation.json",
                "--suite",
                "suite.json",
                "--embedding-backend",
                "bge-m3-internal",
                "--allow-internal-operational",
            ]
        )
        self.assertTrue(args.allow_internal_operational)
        self.assertEqual("bge-m3-internal", args.embedding_backend)

    def test_query_fails_closed_until_exact_pair_is_qualified_and_after_revocation(
        self,
    ) -> None:
        backend = InternalBgeStub()
        with tempfile.TemporaryDirectory(
            prefix="nee-bge-m3-internal-"
        ) as directory:
            runtime = Path(directory) / "runtime"
            graph = ROOT / "graph" / "nee_graph.sqlite"
            pair = ReleaseManager(runtime, graph).bootstrap(backend)

            status = internal_operational_status(runtime, pair)
            self.assertEqual("UNQUALIFIED", status["operational_status"])
            self.assertEqual(
                "internal-operational qualification pending",
                status["assurance_status"],
            )
            self.assertEqual(
                "QUALIFICATION_RECEIPT_NOT_FOUND",
                status["reason_code"],
            )
            with self.assertRaisesRegex(
                PermissionError,
                "QUALIFICATION_RECEIPT_NOT_FOUND",
            ):
                QueryEngine(runtime, graph, backend).ask(
                    "Which exact governed PDLC records are available?",
                    query_type="exact_lookup",
                    audience="internal_consulting,nee-engineering",
                )

            qualification = _qualification_for_pair(pair)
            _write_json(
                qualification_path(runtime, pair["generation_id"]),
                qualification,
            )
            status = require_internal_operational_use(
                runtime,
                pair,
                classification="internal",
                audiences=("internal_consulting", "nee-engineering"),
            )
            self.assertEqual("QUALIFIED", status["operational_status"])
            self.assertEqual(
                "internal-operational qualified",
                status["assurance_status"],
            )
            with self.assertRaisesRegex(
                PermissionError,
                "audiences are outside",
            ):
                require_internal_operational_use(
                    runtime,
                    pair,
                    classification="internal",
                    audiences=("external-client",),
                )

            result = QueryEngine(runtime, graph, backend).ask(
                "Which exact governed PDLC records are available?",
                query_type="exact_lookup",
                audience="internal_consulting,nee-engineering",
            )
            self.assertEqual(INTERNAL_OPERATIONAL, result["assurance_tier"])
            self.assertEqual("QUALIFIED", result["operational_status"])
            self.assertEqual(
                "internal-operational qualified",
                result["assurance_status"],
            )
            self.assertEqual(
                qualification["qualification_id"],
                result["qualification_id"],
            )
            self.assertFalse(result["production_qualified"])

            revocation = copy.deepcopy(read_json(REVOCATION_FIXTURE))
            revocation["pair"] = pair_binding(pair)
            revocation["qualification_id"] = qualification["qualification_id"]
            revocation["qualification_digest"] = qualification[
                "canonical_digest"
            ]["value"]
            revocation = seal_artifact(revocation)
            _write_json(
                runtime
                / "internal_operational"
                / "revocations"
                / f"revocation-{revocation['revocation_id']}.json",
                revocation,
            )
            revoked = internal_operational_status(runtime, pair)
            self.assertEqual("REVOKED", revoked["operational_status"])
            with self.assertRaisesRegex(PermissionError, "QUALIFICATION_REVOKED"):
                QueryEngine(runtime, graph, backend).ask(
                    "Which exact governed PDLC records are available?",
                    query_type="exact_lookup",
                    audience="internal_consulting,nee-engineering",
                )

    def test_policy_change_invalidates_the_exact_pair_qualification(self) -> None:
        policy = read_json(DEFAULT_INTERNAL_OPERATIONAL_POLICY)
        pair = {
            "generation_id": "generation-policy-drift",
            "graph_snapshot": "graph-snapshot-policy-drift",
            "graph_sha256": "1" * 64,
            "sidecar_sha256": "2" * 64,
            "embedding_backend": "flagembedding-bge-m3-internal",
            "model_id": policy["model_binding"]["model_id"],
            "model_revision": policy["model_binding"]["model_revision"],
            "model_manifest_digest": policy["model_binding"][
                "model_manifest_digest"
            ],
            "production_qualified": False,
            "assurance_tier": INTERNAL_OPERATIONAL,
            "internal_operational_policy_digest": policy_digest(policy),
        }
        with tempfile.TemporaryDirectory(
            prefix="nee-internal-policy-drift-"
        ) as directory:
            root = Path(directory)
            _write_json(
                qualification_path(root, pair["generation_id"]),
                _qualification_for_pair(pair),
            )
            changed_policy = copy.deepcopy(policy)
            changed_policy["policy_version"] = "1.0.1"
            changed_policy = seal_artifact(changed_policy)
            changed_policy_path = root / "changed-policy.json"
            _write_json(changed_policy_path, changed_policy)

            status = internal_operational_status(
                root,
                pair,
                policy_path=changed_policy_path,
            )
            self.assertEqual("UNQUALIFIED", status["operational_status"])
            self.assertEqual(
                "ACTIVE_PAIR_POLICY_BINDING_MISMATCH",
                status["reason_code"],
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
