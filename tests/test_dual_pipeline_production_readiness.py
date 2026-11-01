#!/usr/bin/env python3
"""Adversarial acceptance for the production qualification boundary."""
from __future__ import annotations

import base64
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.authorization import (  # noqa: E402
    load_authorization_context,
    load_production_authorization_context,
)
from dual_pipeline.core import (  # noqa: E402
    digest,
    exact_file_copy,
    sha256_bytes,
    sha256_file,
    write_json,
)
from dual_pipeline.identity import GraphEntityCatalog  # noqa: E402
from dual_pipeline.index import (  # noqa: E402
    validate_bge_model_manifest,
)
from dual_pipeline.intake import IntakeEngine, compare_candidates  # noqa: E402
from dual_pipeline.query import QueryEngine  # noqa: E402
from dual_pipeline.release import ReleaseManager  # noqa: E402
from tests.embedding_fixture import BgeM3TestBackend  # noqa: E402


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class ProductionBackendStub:
    backend_id = "production-stub"
    model_id = "BAAI/bge-m3"
    model_revision = "a" * 40
    model_manifest_digest = "b" * 64
    production_qualified = True
    colbert_dimensions = 1024

    def encode(self, text: str):  # pragma: no cover - authorization must fail first
        raise AssertionError("encode must not be reached without authorization")


class RetrieverStub:
    backend = ProductionBackendStub()


class SuccessfulProductionRetrieverStub:
    backend = ProductionBackendStub()

    def __init__(self, graph: Path):
        self.validation = {
            "metadata": {
                "generation_id": "generation-production-test",
                "graph_sha256": sha256_file(graph),
            }
        }

    @staticmethod
    def retrieve(*args, **kwargs):
        return {
            "lanes": {
                lane: {
                    "query_representation_digest": digest(["query", lane]),
                    "results": [],
                }
                for lane in ("approved", "candidate")
            }
        }


class ProductionReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="nee-production-readiness-")
        self.root = Path(self.temp.name)
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.public_key_path = self.root / "public.pem"
        self.public_key_path.write_bytes(
            self.private_key.public_key().public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        )
        now = int(time.time())
        self.claims = {
            "iss": "https://identity.example/nee",
            "aud": "nee-production",
            "sub": "operator-123",
            "jti": "token-123",
            "iat": now - 5,
            "nbf": now - 5,
            "exp": now + 300,
            "nee_classification_clearance": "confidential",
            "nee_audiences": ["nee-engineering", "internal_consulting"],
            "nee_capabilities": [
                "knowledge:intake",
                "knowledge:query",
                "knowledge:release",
                "knowledge:production-qualify",
            ],
            "nee_authorized_resources": {"copilot_package": "c" * 64},
            "nee_model_manifest_digest": "d" * 64,
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _token(self, claims=None) -> Path:
        header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
        payload = _b64url(json.dumps(claims or self.claims, separators=(",", ":")).encode())
        signature = self.private_key.sign(
            f"{header}.{payload}".encode("ascii"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        path = self.root / "token.jwt"
        path.write_text(f"{header}.{payload}.{_b64url(signature)}", encoding="utf-8")
        return path

    def _load(self, claims=None, capabilities=("knowledge:query",)):
        return load_authorization_context(
            self._token(claims),
            self.public_key_path,
            expected_issuer="https://identity.example/nee",
            expected_audience="nee-production",
            required_capabilities=capabilities,
        )

    def test_01_valid_signed_context_is_bounded_and_auditable(self) -> None:
        context = self._load()
        self.assertEqual("operator-123", context.subject)
        self.assertEqual("confidential", context.classification_clearance)
        self.assertEqual(("nee-engineering", "internal_consulting"), context.audiences)
        audit = context.audit_record()
        self.assertNotIn("raw_token", audit)
        self.assertEqual(64, len(audit["token_digest"]))
        self.assertEqual(64, len(audit["context_digest"]))

    def test_02_forged_token_is_rejected(self) -> None:
        token = self._token()
        parts = token.read_text(encoding="utf-8").split(".")
        parts[1] = parts[1][:-1] + ("A" if parts[1][-1] != "A" else "B")
        token.write_text(".".join(parts), encoding="utf-8")
        with self.assertRaises((PermissionError, ValueError)):
            load_authorization_context(
                token,
                self.public_key_path,
                expected_issuer="https://identity.example/nee",
                expected_audience="nee-production",
            )

    def test_03_expired_wrong_audience_and_missing_capability_are_rejected(self) -> None:
        expired = dict(self.claims, iat=int(time.time()) - 500, nbf=int(time.time()) - 500, exp=int(time.time()) - 100)
        with self.assertRaisesRegex(PermissionError, "expired"):
            self._load(expired)
        with self.assertRaisesRegex(PermissionError, "audience mismatch"):
            load_authorization_context(
                self._token(),
                self.public_key_path,
                expected_issuer="https://identity.example/nee",
                expected_audience="wrong-audience",
            )
        with self.assertRaisesRegex(PermissionError, "lacks capabilities"):
            self._load(capabilities=("knowledge:admin",))

    def test_04_resource_digest_binding_fails_closed(self) -> None:
        context = self._load()
        context.require_resources({"copilot_package": "c" * 64})
        with self.assertRaisesRegex(PermissionError, "not bound"):
            context.require_resources({"copilot_package": "e" * 64})

    def _model_manifest(self, name: str = "model") -> tuple[Path, Path, str]:
        model = self.root / name
        model.mkdir()
        (model / "config.json").write_text("{}\n", encoding="utf-8")
        (model / "model.safetensors").write_bytes(b"pinned-test-weights")
        revision = "a" * 40
        files = [
            {
                "path": path.name,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path in sorted(model.iterdir())
        ]
        value = {
            "schema_version": "1.0.0",
            "model_id": "BAAI/bge-m3",
            "model_revision": revision,
            "files": files,
        }
        value["manifest_digest"] = digest(value)
        manifest = self.root / f"{name}-manifest.json"
        write_json(manifest, value)
        return model, manifest, revision

    def test_05_complete_model_manifest_validates(self) -> None:
        model, manifest, revision = self._model_manifest()
        result = validate_bge_model_manifest(model, manifest, revision)
        self.assertEqual("PASS", result["status"])
        self.assertEqual(2, result["file_count"])

    def test_06_tampered_or_uninventoried_model_file_is_rejected(self) -> None:
        model, manifest, revision = self._model_manifest("model-untracked")
        (model / "model.safetensors").write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "verification failed"):
            validate_bge_model_manifest(model, manifest, revision)
        model, manifest, revision = self._model_manifest()
        (model / "untracked.txt").write_text("untracked", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "exactly cover"):
            validate_bge_model_manifest(model, manifest, revision)
        model, manifest, revision = self._model_manifest("model-contained-manifest")
        contained_manifest = model / "manifest.json"
        contained_manifest.write_bytes(manifest.read_bytes())
        with self.assertRaisesRegex(ValueError, "outside the immutable snapshot"):
            validate_bge_model_manifest(model, contained_manifest, revision)

    def test_07_direct_production_query_and_intake_bypass_is_rejected(self) -> None:
        with self.assertRaisesRegex(PermissionError, "production query"):
            QueryEngine(
                self.root / "runtime", ROOT / "graph" / "nee_graph.sqlite", ProductionBackendStub()
            ).ask("What are the strongest PDLC opportunities?")
        engine = IntakeEngine(
            ROOT / "graph" / "nee_graph.sqlite",
            self.root / "runtime",
            retriever=RetrieverStub(),
        )
        with self.assertRaisesRegex(PermissionError, "production intake"):
            engine.ingest(
                ROOT / "contracts" / "dual_pipeline" / "fixtures" / "valid" / "evidence-envelope-intake.json"
            )

    def test_08_direct_production_release_bypass_is_rejected(self) -> None:
        manager = ReleaseManager(
            self.root / "runtime", ROOT / "graph" / "nee_graph.sqlite"
        )
        with self.assertRaisesRegex(PermissionError, "production release"):
            manager.bootstrap(ProductionBackendStub())

    def test_09_shipped_production_trust_policy_fails_closed(self) -> None:
        with self.assertRaisesRegex(PermissionError, "unconfigured"):
            load_production_authorization_context(
                self._token(),
                self.public_key_path,
                supplied_issuer="https://identity.example/nee",
                supplied_audience="nee-production",
            )

    def _configured_trust_policy(self) -> Path:
        policy = self.root / "production-trust-policy.json"
        write_json(
            policy,
            {
                "schema_version": "1.0.0",
                "status": "CONFIGURED",
                "issuer": "https://identity.example/nee",
                "audience": "nee-production",
                "public_key_sha256": sha256_file(self.public_key_path),
                "owner": "test deployment identity owner",
            },
        )
        return policy

    def test_10_configured_production_trust_policy_accepts_exact_anchor(self) -> None:
        context = load_production_authorization_context(
            self._token(),
            self.public_key_path,
            supplied_issuer="https://identity.example/nee",
            supplied_audience="nee-production",
            required_capabilities=("knowledge:release",),
            trust_policy_path=self._configured_trust_policy(),
        )
        self.assertEqual("operator-123", context.subject)

    def test_11_production_trust_policy_rejects_key_issuer_and_audience_drift(self) -> None:
        policy = self._configured_trust_policy()
        value = json.loads(policy.read_text(encoding="utf-8"))
        value["public_key_sha256"] = "0" * 64
        write_json(policy, value)
        with self.assertRaisesRegex(PermissionError, "trust anchor"):
            load_production_authorization_context(
                self._token(),
                self.public_key_path,
                supplied_issuer="https://identity.example/nee",
                supplied_audience="nee-production",
                trust_policy_path=policy,
            )

        policy = self._configured_trust_policy()
        for issuer, audience in (
            ("https://attacker.example/nee", "nee-production"),
            ("https://identity.example/nee", "wrong-audience"),
        ):
            with self.subTest(issuer=issuer, audience=audience):
                with self.assertRaisesRegex(PermissionError, "issuer/audience"):
                    load_production_authorization_context(
                        self._token(),
                        self.public_key_path,
                        supplied_issuer=issuer,
                        supplied_audience=audience,
                        trust_policy_path=policy,
                    )

    def test_12_production_operations_require_exact_input_resources(self) -> None:
        claims = dict(self.claims)
        claims["nee_model_manifest_digest"] = ProductionBackendStub.model_manifest_digest
        context = self._load(
            claims, capabilities=("knowledge:intake", "knowledge:query")
        )
        intake = IntakeEngine(
            ROOT / "graph" / "nee_graph.sqlite",
            self.root / "runtime-intake",
            retriever=RetrieverStub(),
            authorization_context=context,
        )
        with self.assertRaisesRegex(PermissionError, "requested resources"):
            intake.ingest(
                ROOT
                / "contracts"
                / "dual_pipeline"
                / "fixtures"
                / "valid"
                / "evidence-envelope-intake.json"
            )
        with self.assertRaisesRegex(PermissionError, "requested resources"):
            QueryEngine(
                self.root / "runtime-query",
                ROOT / "graph" / "nee_graph.sqlite",
                ProductionBackendStub(),
            ).ask("What is the governed PDLC opportunity?", authorization_context=context)

    def test_13_production_bootstrap_requires_approved_seed_graph(self) -> None:
        claims = dict(self.claims)
        claims["nee_model_manifest_digest"] = ProductionBackendStub.model_manifest_digest
        context = self._load(claims, capabilities=("knowledge:release",))
        unapproved_seed = self.root / "unapproved-seed.sqlite"
        unapproved_seed.write_bytes(b"not-the-approved-seed")
        manager = ReleaseManager(self.root / "runtime-seed", unapproved_seed)
        with self.assertRaisesRegex(RuntimeError, "approved repository contract"):
            manager.bootstrap(
                ProductionBackendStub(), authorization_context=context
            )

    def test_14_production_runtime_cannot_mutate_model_snapshot(self) -> None:
        claims = dict(self.claims)
        claims["nee_model_manifest_digest"] = ProductionBackendStub.model_manifest_digest
        context = self._load(claims, capabilities=("knowledge:release",))
        model_root = self.root / "immutable-model"
        model_root.mkdir()
        runtime = model_root / "runtime"
        backend = ProductionBackendStub()
        backend.model_path = str(model_root)
        manager = ReleaseManager(
            runtime, ROOT / "graph" / "nee_graph.sqlite"
        )
        with self.assertRaisesRegex(RuntimeError, "overlaps"):
            manager.bootstrap(backend, authorization_context=context)
        self.assertFalse(runtime.exists())

    def test_15_nonproduction_intake_cannot_be_promoted_by_production_prepare(self) -> None:
        runtime = self.root / "runtime-replay"
        package = (
            ROOT
            / "contracts"
            / "dual_pipeline"
            / "fixtures"
            / "valid"
            / "evidence-envelope-intake.json"
        )
        summary = IntakeEngine(
            ROOT / "graph" / "nee_graph.sqlite", runtime
        ).ingest(package)
        approval_id = "production-replay-test-approval"
        claims = dict(self.claims)
        claims["nee_model_manifest_digest"] = ProductionBackendStub.model_manifest_digest
        claims["nee_authorized_resources"] = {
            "evidence_envelope_package": sha256_file(package),
            "release_approval": sha256_bytes(approval_id.encode("utf-8")),
        }
        context = self._load(claims, capabilities=("knowledge:release",))
        with self.assertRaisesRegex(
            PermissionError, "production-authenticated intake"
        ):
            ReleaseManager(runtime, ROOT / "graph" / "nee_graph.sqlite").prepare(
                run_id=summary["run_id"],
                approval_id=approval_id,
                backend=ProductionBackendStub(),
                authorization_context=context,
            )

    def test_16_release_rejects_summary_only_intake_authorization_mutation(self) -> None:
        """The U0-sealed authorization audit, not the mutable summary, is authoritative."""

        runtime = self.root / "runtime-sealed-intake-authorization"
        package = (
            ROOT
            / "contracts"
            / "dual_pipeline"
            / "fixtures"
            / "valid"
            / "evidence-envelope-intake.json"
        )
        approval_id = "sealed-intake-authorization-approval"
        claims = dict(self.claims)
        claims["nee_model_manifest_digest"] = ProductionBackendStub.model_manifest_digest
        claims["nee_authorized_resources"] = {
            "evidence_envelope_package": sha256_file(package),
            "release_approval": sha256_bytes(approval_id.encode("utf-8")),
        }
        context = self._load(
            claims, capabilities=("knowledge:intake", "knowledge:release")
        )
        summary = IntakeEngine(
            ROOT / "graph" / "nee_graph.sqlite",
            runtime,
            retriever=SuccessfulProductionRetrieverStub(ROOT / "graph" / "nee_graph.sqlite"),
            retrieval_audiences=list(context.audiences),
            authorization_context=context,
        ).ingest(package)

        summary_path = runtime / "runs" / summary["run_id"] / "intake_summary.json"
        tampered = json.loads(summary_path.read_text(encoding="utf-8"))
        forged_audit = deepcopy(tampered["intake_authorization"])
        forged_audit["capabilities"].append("knowledge:source-attest")
        forged_audit["authorized_resource_names"].extend(
            ["a2a_finalization_receipt", "a2a_finalization_archive"]
        )
        # Both summary fields are mutable after the ledger is sealed.  Altering
        # them must not make the run look source-attested at release time.
        tampered["intake_authorization"] = forged_audit
        tampered["authorization"] = deepcopy(forged_audit)
        write_json(summary_path, tampered)

        exact_file_copy(ROOT / "graph" / "nee_graph.sqlite", runtime / "active.sqlite")
        manager = ReleaseManager(runtime, ROOT / "graph" / "nee_graph.sqlite")
        with patch.object(manager, "active_pair", return_value={"graph_path": "active.sqlite"}):
            with self.assertRaisesRegex(
                RuntimeError, "intake summary package binding differs from the sealed ledger"
            ):
                manager.prepare(
                    run_id=summary["run_id"],
                    approval_id=approval_id,
                    backend=ProductionBackendStub(),
                    authorization_context=context,
                )

    def test_17_production_intake_enforces_clearance_and_candidate_audience(self) -> None:
        graph = ROOT / "graph" / "nee_graph.sqlite"
        package = (
            ROOT
            / "contracts"
            / "dual_pipeline"
            / "fixtures"
            / "valid"
            / "evidence-envelope-intake.json"
        )
        for label, clearance, audiences in (
            ("low-clearance", "public", ["nee-engineering"]),
            ("disjoint-audience", "confidential", ["unrelated-audience"]),
        ):
            with self.subTest(label=label):
                claims = dict(self.claims)
                claims["nee_model_manifest_digest"] = (
                    ProductionBackendStub.model_manifest_digest
                )
                claims["nee_classification_clearance"] = clearance
                claims["nee_audiences"] = audiences
                claims["nee_authorized_resources"] = {
                    "evidence_envelope_package": sha256_file(package)
                }
                context = self._load(claims, capabilities=("knowledge:intake",))
                runtime = self.root / f"runtime-{label}"
                engine = IntakeEngine(
                    graph,
                    runtime,
                    retriever=SuccessfulProductionRetrieverStub(graph),
                    retrieval_audiences=list(context.audiences),
                    authorization_context=context,
                )
                with self.assertRaisesRegex(
                    PermissionError, "clearance or audiences"
                ):
                    engine.ingest(package)
                self.assertFalse((runtime / "runs").exists())

    def test_18_intake_comparison_and_entity_catalog_exclude_disallowed_graph_data(self) -> None:
        graph = ROOT / "graph" / "nee_graph.sqlite"
        connection = sqlite3.connect(graph)
        connection.row_factory = sqlite3.Row
        try:
            confidential = connection.execute(
                """SELECT sd.search_document_id,sd.body
                   FROM search_document sd
                   JOIN assertion a ON a.assertion_id=sd.assertion_id
                   WHERE a.classification='confidential'
                     AND a.allowed_audiences_json LIKE '%internal_working_team%'
                   ORDER BY sd.search_document_id LIMIT 1"""
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(confidential)
        bundle = {
            "run_id": "run-access-filter-test",
            "candidates": [
                {
                    "candidate_id": "candidate-access-filter-test",
                    "normalized_text": confidential["body"],
                    "classification": "confidential",
                    "audiences": ["internal_consulting"],
                }
            ]
        }
        comparison = compare_candidates(
            bundle, graph, retrieval_audiences=["internal_consulting"]
        )["comparisons"]["candidate-access-filter-test"]
        observed = {
            item["record_id"]
            for key in ("exact_matches", "semantic_matches")
            for item in comparison[key]
        }
        self.assertNotIn(confidential["search_document_id"], observed)

        denied_catalog = GraphEntityCatalog(
            graph,
            classification="confidential",
            audiences=["internal_consulting"],
        )
        admitted_catalog = GraphEntityCatalog(
            graph,
            classification="confidential",
            audiences=["internal_working_team"],
        )
        disjoint_entity = "person_alberto_castagnara"
        self.assertNotIn(disjoint_entity, denied_catalog.entities)
        self.assertIn(disjoint_entity, admitted_catalog.entities)

    def test_19_query_output_cannot_target_model_or_generation_content(self) -> None:
        runtime = self.root / "runtime-output"
        backend = BgeM3TestBackend()
        manager = ReleaseManager(runtime, ROOT / "graph" / "nee_graph.sqlite")
        manager.bootstrap(backend)
        model_root = self.root / "immutable-output-model"
        model_root.mkdir()
        sentinel = model_root / "sentinel.txt"
        sentinel.write_text("unchanged", encoding="utf-8")
        backend.model_path = str(model_root)
        with self.assertRaisesRegex(RuntimeError, "protected runtime or model"):
            QueryEngine(
                runtime, ROOT / "graph" / "nee_graph.sqlite", backend
            ).ask("What is the governed PDLC opportunity?", output_dir=model_root)
        self.assertEqual("unchanged", sentinel.read_text(encoding="utf-8"))
        self.assertFalse((runtime / "runs").exists())

    def test_20_nested_runtime_reparse_is_rejected_before_intake_write(self) -> None:
        runtime = self.root / "runtime-junction"
        redirected = self.root / "redirected-model"
        runtime.mkdir()
        redirected.mkdir()
        link = runtime / "runs"
        try:
            os.symlink(redirected, link, target_is_directory=True)
        except OSError:
            if os.name != "nt":
                self.skipTest("directory symlinks are unavailable")
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(redirected)],
                capture_output=True,
                text=True,
            )
            if result.returncode:
                self.skipTest("directory junctions are unavailable")
        try:
            with self.assertRaisesRegex(
                RuntimeError, "link or reparse point|resolves outside"
            ):
                IntakeEngine(
                    ROOT / "graph" / "nee_graph.sqlite", runtime
                ).ingest(
                    ROOT
                    / "contracts"
                    / "dual_pipeline"
                    / "fixtures"
                    / "valid"
                    / "evidence-envelope-intake.json"
                )
            self.assertEqual([], list(redirected.iterdir()))
        finally:
            if link.exists() or link.is_symlink():
                if os.name == "nt":
                    os.rmdir(link)
                else:
                    link.unlink()

    def test_21_generation_reparse_is_rejected_before_bootstrap_write(self) -> None:
        runtime = self.root / "runtime-generation-junction"
        redirected = self.root / "redirected-generation-model"
        runtime.mkdir()
        redirected.mkdir()
        link = runtime / "generations"
        try:
            os.symlink(redirected, link, target_is_directory=True)
        except OSError:
            if os.name != "nt":
                self.skipTest("directory symlinks are unavailable")
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(redirected)],
                capture_output=True,
                text=True,
            )
            if result.returncode:
                self.skipTest("directory junctions are unavailable")
        try:
            with self.assertRaisesRegex(
                RuntimeError, "link or reparse point|resolves outside"
            ):
                ReleaseManager(
                    runtime, ROOT / "graph" / "nee_graph.sqlite"
                ).bootstrap(BgeM3TestBackend())
            self.assertEqual([], list(redirected.iterdir()))
        finally:
            if link.exists() or link.is_symlink():
                if os.name == "nt":
                    os.rmdir(link)
                else:
                    link.unlink()


if __name__ == "__main__":
    unittest.main(verbosity=2)
