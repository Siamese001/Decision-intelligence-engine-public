#!/usr/bin/env python3
"""Production release regression for sealed A2A source-attestation evidence.

The normal ingress method rejects a selected-span packet without
``knowledge:source-attest``.  This focused test deliberately bypasses that
one ingress check to model a legacy/direct producer, then verifies that the
durable release boundary still rejects the sealed absence of the capability
and cannot be fooled by changing only the mutable intake summary.
"""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.authorization import AuthorizationContext  # noqa: E402
from dual_pipeline.core import (  # noqa: E402
    digest,
    read_json,
    seal_artifact,
    sha256_bytes,
    write_json,
)
from dual_pipeline.index import (  # noqa: E402
    HybridRetriever,
)
from dual_pipeline.intake import IntakeEngine  # noqa: E402
from dual_pipeline.normalization import normalize_a2a_fact_vector_packet  # noqa: E402
from dual_pipeline.release import ReleaseManager  # noqa: E402
from tests.embedding_fixture import BgeM3TestBackend  # noqa: E402


class _ProductionTestBackend(BgeM3TestBackend):
    """Real BGE-M3 executor that drives the production authorization branch."""

    backend_id = "flagembedding-bge-m3"
    production_qualified = True


class _LegacySourceAttestBypassContext(AuthorizationContext):
    """Model a legacy/direct producer that skipped only source-attest at ingress."""

    def require(self, *capabilities: str) -> None:
        if capabilities == ("knowledge:source-attest",):
            return
        super().require(*capabilities)


class A2AProductionReleaseAuthorizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="nee-a2a-production-release-")
        self.root = Path(self.temp.name)
        self.runtime = self.root / "runtime"
        self.graph = ROOT / "graph" / "nee_graph.sqlite"
        self.backend = _ProductionTestBackend()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _authorization(
        self,
        *,
        token_id: str,
        capabilities: tuple[str, ...],
        resources: dict[str, str],
        context_type: type[AuthorizationContext] = AuthorizationContext,
    ) -> AuthorizationContext:
        now = int(time.time())
        bounded = {
            "subject": "a2a-production-test-operator",
            "issuer": "https://identity.example/nee",
            "relying_party": "nee-production",
            "token_id": token_id,
            "issued_at": now - 5,
            "expires_at": now + 300,
            "classification_clearance": "confidential",
            "audiences": ("internal_consulting",),
            "capabilities": capabilities,
            "authorized_resources": resources,
            "approved_model_manifest_digest": self.backend.model_manifest_digest,
            "token_digest": digest(["test-token", token_id]),
        }
        return context_type(
            **bounded,
            context_digest=digest(["test-authorization-context", bounded]),
        )

    def _selected_packet(self) -> tuple[dict, dict]:
        """Return a schema-valid selected marker plus its ordinary A2A bundle.

        The retained source finalization is mocked below because this test is
        about durable authorization evidence, not the separately covered
        archive-replay finalizer.  The ordinary normalized packet preserves a
        real A2A candidate bundle and real pipeline ledger stages.
        """

        fixture_path = (
            ROOT
            / "contracts"
            / "dual_pipeline"
            / "fixtures"
            / "valid"
            / "a2a-fact-vector-packet-intake.json"
        )
        base_packet = read_json(fixture_path)
        selected_packet = deepcopy(base_packet)
        selected_packet["extensions"] = {
            "com.partner_advisory.nee.a2a-evidence-spans": {}
        }
        return seal_artifact(selected_packet), base_packet

    def test_prepare_many_rejects_forged_summary_source_attestation(self) -> None:
        manager = ReleaseManager(self.runtime, self.graph)
        bootstrap_authorization = self._authorization(
            token_id="bootstrap",
            capabilities=("knowledge:release",),
            resources={},
        )
        active = manager.bootstrap(
            self.backend, authorization_context=bootstrap_authorization
        )

        packet, ordinary_packet = self._selected_packet()
        packet_path = self.root / "selected-a2a-packet.json"
        write_json(packet_path, packet)
        packet_bytes = packet_path.read_bytes()
        receipt_bytes = b"test finalization receipt"
        receipt_path = self.root / "finalization-receipt.json"
        receipt_path.write_bytes(receipt_bytes)
        binding = {
            "receipt_id": "a2a-production-test-receipt",
            "receipt_canonical_digest": "1" * 64,
            "receipt_file_sha256": sha256_bytes(receipt_bytes),
            "package_file_sha256": sha256_bytes(packet_bytes),
            "package_canonical_digest": packet["canonical_digest"]["value"],
            "archive_root_id": "a2a-production-test-archive",
            "archive_binding_digest": "2" * 64,
            "verification_state": "finalization_archive_replayed",
        }
        intake_authorization = self._authorization(
            token_id="legacy-direct-ingress",
            capabilities=("knowledge:intake",),
            resources={
                "a2a_fact_vector_packet_package": sha256_bytes(packet_bytes),
                "a2a_finalization_receipt": binding["receipt_file_sha256"],
                "a2a_finalization_archive": binding["archive_binding_digest"],
            },
            context_type=_LegacySourceAttestBypassContext,
        )

        def normalize_ordinary_packet(
            _packet: dict,
            *,
            run_context: str,
            finalization_proof: object | None = None,
        ) -> dict:
            del finalization_proof
            return normalize_a2a_fact_vector_packet(
                ordinary_packet, run_context=run_context
            )

        def stage_reference(**kwargs: object) -> dict[str, str]:
            run_id = str(kwargs["run_id"])
            return {
                "run_id": run_id,
                "package_relative_path": (
                    f"runs/{run_id}/a2a_finalization/finalized_packet.json"
                ),
                "receipt_relative_path": (
                    f"runs/{run_id}/a2a_finalization/finalization_receipt.json"
                ),
                "package_file_sha256": binding["package_file_sha256"],
                "receipt_file_sha256": binding["receipt_file_sha256"],
                "archive_root_id": binding["archive_root_id"],
            }

        intake = IntakeEngine(
            manager._resolve(active["graph_path"]),
            self.runtime,
            retriever=HybridRetriever(
                manager._resolve(active["sidecar_path"]), self.backend
            ),
            retrieval_audiences=["internal_consulting"],
            authorization_context=intake_authorization,
            active_pair=active,
        )
        with patch(
            "dual_pipeline.intake.verify_finalized_a2a_package_bytes",
            return_value=object(),
        ), patch(
            "dual_pipeline.intake.verified_finalization_binding",
            return_value=binding,
        ), patch(
            "dual_pipeline.intake.stage_ingress_finalization_evidence",
            side_effect=stage_reference,
        ), patch(
            "dual_pipeline.intake.verify_runtime_a2a_capture",
            return_value={"status": "PASS"},
        ), patch(
            "dual_pipeline.intake.normalize_a2a_fact_vector_packet",
            side_effect=normalize_ordinary_packet,
        ):
            summary = intake.ingest(
                packet_path, a2a_finalization_receipt=receipt_path
            )

        self.assertIsNotNone(summary["a2a_finalization_binding"])
        self.assertNotIn(
            "knowledge:source-attest", summary["intake_authorization"]["capabilities"]
        )
        approval_id = "a2a-production-release-approval"
        release_authorization = self._authorization(
            token_id="release",
            capabilities=("knowledge:release",),
            resources={
                "release_approval": sha256_bytes(approval_id.encode("utf-8")),
            },
        )

        # The unmodified sealed audit itself proves the production release
        # rejects the missing source-attest capability.
        with patch.object(manager, "_reverify_a2a_component_finalization"):
            with self.assertRaisesRegex(
                PermissionError, "production-authenticated intake"
            ):
                manager.prepare_many(
                    run_ids=[summary["run_id"]],
                    approval_id=approval_id,
                    backend=self.backend,
                    authorization_context=release_authorization,
                )

        # A mutable summary can claim source-attest, but it cannot change the
        # U0 payload that the release boundary now treats as authoritative.
        summary_path = self.runtime / "runs" / summary["run_id"] / "intake_summary.json"
        forged_summary = read_json(summary_path)
        forged_audit = deepcopy(forged_summary["intake_authorization"])
        forged_audit["capabilities"] = [
            "knowledge:intake",
            "knowledge:source-attest",
        ]
        forged_summary["intake_authorization"] = forged_audit
        forged_summary["authorization"] = deepcopy(forged_audit)
        write_json(summary_path, forged_summary)
        with patch.object(manager, "_reverify_a2a_component_finalization"):
            with self.assertRaisesRegex(
                RuntimeError, "intake summary package binding differs from the sealed ledger"
            ):
                manager.prepare_many(
                    run_ids=[summary["run_id"]],
                    approval_id=approval_id,
                    backend=self.backend,
                    authorization_context=release_authorization,
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
