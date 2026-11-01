from __future__ import annotations

import base64
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from dual_pipeline.authorization import load_authorization_context
from dual_pipeline.contracts import create_artifact, validate_artifact
from dual_pipeline.core import (
    digest,
    exact_file_copy,
    sha256_bytes,
    sha256_file,
    utc_now,
    write_json,
)
from dual_pipeline.index import HybridRetriever
from dual_pipeline.release import ReleaseManager
from dual_pipeline.retrieval_release import RetrievalReleaseEvaluator
from dual_pipeline.retrieval_suite import AutomaticRetrievalReleaseSuiteBuilder
from dual_pipeline.uwg import UniversalWriteGateway, _number
from tests.embedding_fixture import BgeM3TestBackend

ROOT = Path(__file__).resolve().parents[1]


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class UniversalWriteGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="nee-uwg-")
        self.root = Path(self.temp.name)
        self.seed_graph = self.root / "seed.sqlite"
        exact_file_copy(ROOT / "graph" / "nee_graph.sqlite", self.seed_graph)
        self.assertion_id = "assertion-uwg-runtime"
        self.evidence_id = "evidence-uwg-runtime"
        self.decoy_assertion_id = "assertion-uwg-negative-control"
        self.decoy_evidence_id = "evidence-uwg-negative-control"
        self.approval_id = "uwg-approval-runtime"
        self.source_id = "source-uwg-runtime"
        self.source_version_id = "source-version-uwg-runtime"
        self.extraction_run_id = "extract-uwg-runtime-source-bound"
        self.source_content_hash = ""
        self._insert_candidate_fixture(directness="direct")
        self.backend = BgeM3TestBackend()
        self.runtime = self.root / "runtime"
        self.manager = ReleaseManager(self.runtime, self.seed_graph)
        self.candidate_pair = self.manager.bootstrap(self.backend)
        self.gateway = UniversalWriteGateway(self.runtime, self.seed_graph)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _evaluate_bge_release(self, receipt_path: Path) -> dict:
        """Create the required sealed E3 evidence before activating a BGE pair."""

        suite = AutomaticRetrievalReleaseSuiteBuilder(
            self.runtime, self.seed_graph
        ).build(receipt_path)
        result = RetrievalReleaseEvaluator(self.runtime, self.seed_graph).evaluate(
            receipt_path,
            Path(suite["path"]),
            backend=self.backend,
        )
        self.assertEqual("PASS", result["evaluation"]["status"])
        return result

    def _insert_candidate_fixture(
        self,
        *,
        directness: str,
        approval_snapshot_hash: str | None = None,
        approval_expires_at: str | None = "2099-01-01T00:00:00Z",
        approval_approved_at: str | None = None,
        exact_approval_scope: bool = True,
        contradictory_evidence: bool = False,
        insert_approval: bool = True,
        candidate_qualifiers: dict[str, object] | None = None,
    ) -> None:
        now = utc_now()
        baseline_graph_sha256 = sha256_file(self.seed_graph)
        connection = sqlite3.connect(self.seed_graph)
        connection.row_factory = sqlite3.Row
        try:
            subject = connection.execute(
                "SELECT node_id FROM node WHERE active=1 ORDER BY node_id LIMIT 1"
            ).fetchone()
            if subject is None:
                self.fail("seed graph has no active subject node")
            connection.execute(
                """INSERT INTO source
                   (source_id,source_type,source_name,source_date,authority_rank,source_posture,
                    cold_archive_ref,notes,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    self.source_id,
                    "official_web_snapshot",
                    "Official UWG fixture source",
                    "2026-07-25",
                    9,
                    "external_first_party",
                    "fixture://uwg/direct-source",
                    "Test-only direct source snapshot.",
                    now,
                ),
            )
            source_hash = digest(
                {"source": self.source_id, "version": self.source_version_id}
            )
            self.source_content_hash = source_hash
            connection.execute(
                """INSERT INTO source_version
                   (source_version_id,source_id,version_number,source_fingerprint,content_hash,
                    source_date_normalized,source_date_precision,source_date_status,authority_rank,
                    scope_text,posture_text,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    self.source_version_id,
                    self.source_id,
                    1,
                    source_hash,
                    source_hash,
                    "2026-07-25",
                    "day",
                    "exact",
                    9,
                    "Official direct test source.",
                    "external_first_party",
                    now,
                ),
            )
            connection.execute(
                """INSERT INTO extraction_run
                   (extraction_run_id,algorithm_version,baseline_graph_sha256,
                    input_manifest_sha256,started_at,completed_at,status,deterministic)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    self.extraction_run_id,
                    "uwg-test-fixture-structured-adapter-v1",
                    baseline_graph_sha256,
                    source_hash,
                    now,
                    now,
                    "completed",
                    1,
                ),
            )
            connection.execute(
                """INSERT INTO evidence_item
                   (evidence_id,evidence_fingerprint,source_id,source_version_id,locator_kind,
                    locator_value,page_number,section_name,paragraph_number,row_reference,slide_number,
                    transcript_timestamp,direct_quote,evidence_directness,source_authority_rank,
                    extraction_confidence,corroboration_group,location_status,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    self.evidence_id,
                    digest({"evidence": self.evidence_id}),
                    self.source_id,
                    self.source_version_id,
                    "section",
                    "UWG official assertion",
                    None,
                    "UWG official assertion",
                    None,
                    None,
                    None,
                    None,
                    "The official source supports the fixture assertion.",
                    directness,
                    9,
                    1.0,
                    "uwg-fixture-corroboration",
                    "exact",
                    now,
                ),
            )
            connection.execute(
                """INSERT INTO assertion
                   (assertion_id,assertion_fingerprint,subject_node_id,predicate,legacy_predicate,
                    object_node_id,object_literal,object_literal_type,assertion_type,atomicity_state,
                    scope_text,speaker_or_author,primary_source_id,extraction_run_id,extraction_method,
                    origin_type,confidence_label,confidence_score,validation_state,currentness_state,
                    valid_from,valid_to,date_precision,date_status,recorded_at,superseded_at,
                    superseded_by_assertion_id,resolution_state,canonical_status,canonical_eligible,
                    retrieval_eligible,materiality,classification,allowed_audiences_json,hitl_required,
                    validation_priority,validation_owner,qualifiers_json,legacy_record_type,
                    legacy_record_ids_json,created_at,updated_at,approval_reference)
                   VALUES (""" + ",".join("?" for _ in range(43)) + ")",
                (
                    self.assertion_id,
                    digest({"assertion": self.assertion_id}),
                    subject["node_id"],
                    "HAS_CLAIM_TEXT",
                    None,
                    None,
                    "UWG runtime fixture direct fact",
                    "text",
                    "extracted_claim",
                    "atomic",
                    "UWG runtime fixture direct fact",
                    None,
                    self.source_id,
                    self.extraction_run_id,
                    "uwg-test-fixture",
                    "human_source",
                    "high",
                    0.99,
                    "needs_review",
                    "current",
                    "2026-07-25",
                    None,
                    "day",
                    "exact",
                    now,
                    None,
                    None,
                    "resolved",
                    "candidate",
                    0,
                    1,
                    "medium",
                    "internal",
                    json.dumps(["nee-engineering"]),
                    1,
                    "P1",
                    "nee-engineering-review",
                    json.dumps(
                        candidate_qualifiers
                        or {
                            "dual_pipeline_candidate_type": "assertion",
                            "retrieval_kind": "assertion",
                            "answer_use": "candidate_hypothesis",
                            "fact_promotion_eligible": True,
                        },
                        sort_keys=True,
                    ),
                    "claim",
                    "[]",
                    now,
                    now,
                    None,
                ),
            )
            connection.execute(
                "INSERT INTO assertion_evidence VALUES (?,?,?,?,?)",
                (self.assertion_id, self.evidence_id, "supports", 1, now),
            )
            if contradictory_evidence:
                contradiction_id = "evidence-uwg-runtime-contradiction"
                connection.execute(
                    """INSERT INTO evidence_item
                       (evidence_id,evidence_fingerprint,source_id,source_version_id,locator_kind,
                        locator_value,page_number,section_name,paragraph_number,row_reference,slide_number,
                        transcript_timestamp,direct_quote,evidence_directness,source_authority_rank,
                        extraction_confidence,corroboration_group,location_status,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        contradiction_id,
                        digest({"evidence": contradiction_id}),
                        self.source_id,
                        self.source_version_id,
                        "section",
                        "UWG contradictory assertion",
                        None,
                        "UWG contradictory assertion",
                        None,
                        None,
                        None,
                        None,
                        "The direct source contradicts the fixture assertion.",
                        "direct",
                        9,
                        1.0,
                        "uwg-fixture-contradiction",
                        "exact",
                        now,
                    ),
                )
                connection.execute(
                    "INSERT INTO assertion_evidence VALUES (?,?,?,?,?)",
                    (self.assertion_id, contradiction_id, "contradicts", 0, now),
                )
            connection.execute(
                """INSERT INTO search_document
                   (search_document_id,object_type,object_id,title,body,canonical_status,
                    currentness_state,retrieval_eligible,source_id,assertion_id,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    "search-uwg-runtime",
                    "assertion",
                    self.assertion_id,
                    "UWG runtime fixture",
                    "UWG runtime fixture direct fact",
                    "candidate",
                    "current",
                    0,
                    self.source_id,
                    self.assertion_id,
                    now,
                ),
            )
            connection.execute(
                """INSERT INTO evidence_item
                   (evidence_id,evidence_fingerprint,source_id,source_version_id,locator_kind,
                    locator_value,page_number,section_name,paragraph_number,row_reference,slide_number,
                    transcript_timestamp,direct_quote,evidence_directness,source_authority_rank,
                    extraction_confidence,corroboration_group,location_status,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    self.decoy_evidence_id,
                    digest({"evidence": self.decoy_evidence_id}),
                    self.source_id,
                    self.source_version_id,
                    "section",
                    "UWG negative-control assertion",
                    None,
                    "UWG negative-control assertion",
                    None,
                    None,
                    None,
                    None,
                    "The direct source supports the negative-control assertion.",
                    "direct",
                    9,
                    1.0,
                    "uwg-negative-control",
                    "exact",
                    now,
                ),
            )
            connection.execute(
                """INSERT INTO assertion
                   (assertion_id,assertion_fingerprint,subject_node_id,predicate,legacy_predicate,
                    object_node_id,object_literal,object_literal_type,assertion_type,atomicity_state,
                    scope_text,speaker_or_author,primary_source_id,extraction_run_id,extraction_method,
                    origin_type,confidence_label,confidence_score,validation_state,currentness_state,
                    valid_from,valid_to,date_precision,date_status,recorded_at,superseded_at,
                    superseded_by_assertion_id,resolution_state,canonical_status,canonical_eligible,
                    retrieval_eligible,materiality,classification,allowed_audiences_json,hitl_required,
                    validation_priority,validation_owner,qualifiers_json,legacy_record_type,
                    legacy_record_ids_json,created_at,updated_at,approval_reference)
                   VALUES (""" + ",".join("?" for _ in range(43)) + ")",
                (
                    self.decoy_assertion_id,
                    digest({"assertion": self.decoy_assertion_id}),
                    subject["node_id"],
                    "HAS_CLAIM_TEXT",
                    None,
                    None,
                    "UWG negative control unrelated topic",
                    "text",
                    "extracted_claim",
                    "atomic",
                    "UWG negative control unrelated topic",
                    None,
                    self.source_id,
                    self.extraction_run_id,
                    "uwg-test-fixture",
                    "human_source",
                    "high",
                    0.99,
                    "needs_review",
                    "current",
                    "2026-07-25",
                    None,
                    "day",
                    "exact",
                    now,
                    None,
                    None,
                    "resolved",
                    "candidate",
                    0,
                    1,
                    "medium",
                    "internal",
                    json.dumps(["nee-engineering"]),
                    1,
                    "P1",
                    "nee-engineering-review",
                    json.dumps(
                        {
                            "dual_pipeline_candidate_type": "assertion",
                            "retrieval_kind": "assertion",
                            "answer_use": "candidate_hypothesis",
                            "fact_promotion_eligible": True,
                        },
                        sort_keys=True,
                    ),
                    "claim",
                    "[]",
                    now,
                    now,
                    None,
                ),
            )
            connection.execute(
                "INSERT INTO assertion_evidence VALUES (?,?,?,?,?)",
                (self.decoy_assertion_id, self.decoy_evidence_id, "supports", 1, now),
            )
            connection.execute(
                """INSERT INTO search_document
                   (search_document_id,object_type,object_id,title,body,canonical_status,
                    currentness_state,retrieval_eligible,source_id,assertion_id,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    "search-uwg-negative-control",
                    "assertion",
                    self.decoy_assertion_id,
                    "UWG negative control",
                    "UWG negative control unrelated topic",
                    "candidate",
                    "current",
                    0,
                    self.source_id,
                    self.decoy_assertion_id,
                    now,
                ),
            )
            approval_snapshot = {
                "evidence_id": self.evidence_id,
                "source_id": self.source_id,
                "source_version_id": self.source_version_id,
                "source_content_hash": approval_snapshot_hash or self.source_content_hash,
            }
            self.approval_scope = self._approval_scope(
                "uwg-decision-runtime", approval_snapshot
            )
            if not exact_approval_scope:
                self.approval_scope = {"allowed_actions": ["canonical_assertion_promotion"]}
            approval_scope = json.dumps(self.approval_scope, sort_keys=True)
            if insert_approval:
                connection.execute(
                    """INSERT INTO governance_approval
                       (approval_id,approval_reference,approval_type,scope_json,baseline_zip_sha256,
                        baseline_graph_sha256,proposal_sha256,decision_record_sha256,card_proposal_sha256,
                        gate_o1_sha256,approval_statement_sha256,approved_at,expires_at,status,approved_by,
                        release_version,active,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        self.approval_id,
                        "uwg-approval-runtime-reference",
                        "ingestion_promotion",
                        approval_scope,
                        None,
                        None,
                        digest({"proposal": self.approval_id}),
                        None,
                        None,
                        None,
                        digest({"approval": self.approval_id}),
                        approval_approved_at or now,
                        approval_expires_at,
                        "approved",
                        "uwg-test-reviewer",
                        "uwg-1.0.0",
                        1,
                        now,
                    ),
                )
            connection.commit()
        finally:
            connection.close()

    def _reset_runtime_fixture(
        self, *, directness: str = "direct", **fixture_options: object
    ) -> None:
        self.temp.cleanup()
        self.temp = tempfile.TemporaryDirectory(prefix="nee-uwg-reset-")
        self.root = Path(self.temp.name)
        self.seed_graph = self.root / "seed.sqlite"
        exact_file_copy(ROOT / "graph" / "nee_graph.sqlite", self.seed_graph)
        self._insert_candidate_fixture(directness=directness, **fixture_options)
        self.runtime = self.root / "runtime"
        self.manager = ReleaseManager(self.runtime, self.seed_graph)
        self.candidate_pair = self.manager.bootstrap(self.backend)
        self.gateway = UniversalWriteGateway(self.runtime, self.seed_graph)

    def _policy(self) -> dict:
        return json.loads(
            (ROOT / "requirements" / "uwg-promotion-policy.json").read_text(encoding="utf-8")
        )

    def _policy_binding(self) -> dict:
        policy = self._policy()
        return {
            "policy_id": policy["policy_id"],
            "policy_version": policy["policy_version"],
            "policy_digest": digest(policy),
            "owner": policy["owner"],
        }

    def _benchmark_path(
        self,
        *,
        thresholds: dict | None = None,
        negative_query: str = "UWG negative control unrelated topic",
        acl_classification: str = "public",
        acl_audiences: list[str] | None = None,
        acceptance_policy: dict | None = None,
    ) -> Path:
        policy = self._policy()
        benchmark = create_artifact(
            "uwg-benchmark",
            "uwg-benchmark-runtime",
            benchmark_id="uwg-benchmark-runtime",
            origin_run_id="run-uwg-runtime",
            candidate_assertion_ids=[self.assertion_id],
            cases=[
                {
                    "case_id": "case-uwg-runtime",
                    "query": "UWG runtime fixture direct fact",
                    "expected_assertion_ids": [self.assertion_id],
                    "classification": "internal",
                    "audiences": ["nee-engineering"],
                }
            ],
            negative_cases=[
                {
                    "case_id": "negative-uwg-runtime",
                    "query": negative_query,
                    "forbidden_assertion_ids": [self.assertion_id],
                    "classification": "internal",
                    "audiences": ["nee-engineering"],
                }
            ],
            lane_isolation_cases=[
                {
                    "case_id": "lane-uwg-runtime",
                    "query": "UWG runtime fixture direct fact",
                    "forbidden_assertion_ids": [self.assertion_id],
                    "classification": "internal",
                    "audiences": ["nee-engineering"],
                }
            ],
            acl_cases=[
                {
                    "case_id": "acl-uwg-runtime",
                    "query": "UWG runtime fixture direct fact",
                    "forbidden_assertion_ids": [self.assertion_id],
                    "classification": acl_classification,
                    "audiences": acl_audiences or ["public-reader"],
                }
            ],
            thresholds=thresholds or dict(policy["thresholds"]),
            acceptance_policy=acceptance_policy or self._policy_binding(),
            created_at=utc_now(),
            extensions={},
        )
        path = self.root / "benchmark.json"
        write_json(path, benchmark)
        return path

    def _source_snapshot(self, *, source_content_hash: str | None = None) -> dict:
        return {
            "evidence_id": self.evidence_id,
            "source_id": self.source_id,
            "source_version_id": self.source_version_id,
            "source_content_hash": source_content_hash or self.source_content_hash,
        }

    def _approval_scope(self, promotion_id: str, snapshot: dict) -> dict:
        return {
            "allowed_actions": ["canonical_assertion_promotion"],
            "uwg_promotion": {
                "promotion_id": promotion_id,
                "candidate_assertion_ids": [self.assertion_id],
                "source_snapshot_bindings": [
                    {"assertion_id": self.assertion_id, **snapshot}
                ],
            },
        }

    def _decision_path(
        self,
        evaluation: dict,
        *,
        stale_pair: bool = False,
        source_content_hash: str | None = None,
    ) -> Path:
        promotion_id = "uwg-decision-runtime"
        snapshot = self._source_snapshot(source_content_hash=source_content_hash)
        pair = dict(evaluation["candidate_pair"])
        if stale_pair:
            pair["graph_sha256"] = "f" * 64
        decision = create_artifact(
            "uwg-promotion-decision",
            "uwg-decision-runtime",
            promotion_id=promotion_id,
            evaluation_id=evaluation["evaluation_id"],
            evaluation_digest=evaluation["canonical_digest"]["value"],
            origin_run_id=evaluation["origin_run_id"],
            candidate_pair=pair,
            candidate_assertion_ids=[self.assertion_id],
            evidence_bindings=[
                {"assertion_id": self.assertion_id, "evidence_snapshots": [snapshot]}
            ],
            decision="PROMOTE_APPROVED",
            promotion_mode="ONE_TIME_EXACT_SCOPE",
            scope_confirmation="PROMOTE_EXACT_CANDIDATES_ONLY",
            approval_id=self.approval_id,
            approval_scope_digest=digest(self.approval_scope),
            reviewer="uwg-test-reviewer",
            decided_at=utc_now(),
            rationale="Direct evidence and policy-bound candidate-lane retrieval passed.",
            extensions={},
        )
        validate_artifact(decision)
        path = self.root / ("stale-decision.json" if stale_pair else "decision.json")
        write_json(path, decision)
        return path

    def _approval_request_path(
        self,
        *,
        source_content_hash: str | None = None,
        approved_by: str = "uwg-operator-123",
        expires_at: str = "2099-01-01T00:00:00Z",
    ) -> Path:
        request = create_artifact(
            "uwg-governance-approval",
            "uwg-governance-approval-runtime",
            approval_id=self.approval_id,
            approval_reference="uwg-approval-runtime-reference",
            approval_type="ingestion_promotion",
            promotion_id="uwg-decision-runtime",
            candidate_pair={
                key: self.manager.active_pair()[key]
                for key in (
                    "generation_id",
                    "graph_snapshot",
                    "graph_sha256",
                    "sidecar_sha256",
                    "embedding_backend",
                    "model_id",
                    "model_revision",
                    "model_manifest_digest",
                    "production_qualified",
                )
            },
            candidate_assertion_ids=[self.assertion_id],
            source_snapshot_bindings=[
                {
                    "assertion_id": self.assertion_id,
                    "evidence_id": self.evidence_id,
                    "source_id": self.source_id,
                    "source_version_id": self.source_version_id,
                    "source_content_hash": (
                        source_content_hash or self.source_content_hash
                    ),
                }
            ],
            approved_by=approved_by,
            approved_at=utc_now(),
            expires_at=expires_at,
            release_version="uwg-1.0.0",
            scope_confirmation="REGISTER_EXACT_UWG_PROMOTION_SCOPE",
            rationale="Signed test operator approved only the exact candidate and source snapshot.",
            extensions={},
        )
        path = self.root / "approval-request.json"
        write_json(path, request)
        return path

    def _signed_registration_context(
        self,
        request_path: Path,
        *,
        subject: str = "uwg-operator-123",
        tamper_resource: bool = False,
    ):
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_key_path = self.root / "uwg-registration-public.pem"
        public_key_path.write_bytes(
            private_key.public_key().public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        )
        resources = {
            "uwg_governance_approval": sha256_file(request_path),
            "active_generation": sha256_bytes(
                self.manager.active_pair()["generation_id"].encode("utf-8")
            ),
            "uwg_policy": sha256_file(
                ROOT / "requirements" / "uwg-promotion-policy.json"
            ),
            "release_approval": sha256_bytes(self.approval_id.encode("utf-8")),
        }
        if tamper_resource:
            resources["uwg_governance_approval"] = "f" * 64
        now = int(time.time())
        claims = {
            "iss": "https://identity.example/nee",
            "aud": "nee-uwg-test",
            "sub": subject,
            "jti": "uwg-registration-token",
            "iat": now - 5,
            "nbf": now - 5,
            "exp": now + 300,
            "nee_classification_clearance": "internal",
            "nee_audiences": ["nee-engineering"],
            "nee_capabilities": ["knowledge:release", "knowledge:governance"],
            "nee_authorized_resources": resources,
        }
        header = _b64url(
            json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode()
        )
        payload = _b64url(json.dumps(claims, separators=(",", ":")).encode())
        signature = private_key.sign(
            f"{header}.{payload}".encode("ascii"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        token_path = self.root / "uwg-registration.jwt"
        token_path.write_text(
            f"{header}.{payload}.{_b64url(signature)}", encoding="utf-8"
        )
        return load_authorization_context(
            token_path,
            public_key_path,
            expected_issuer="https://identity.example/nee",
            expected_audience="nee-uwg-test",
            required_capabilities=("knowledge:release", "knowledge:governance"),
        )

    def test_promotes_only_after_runtime_evaluation_and_rebuilds_sidecar(self) -> None:
        evaluation_result = self.gateway.evaluate(
            self._benchmark_path(), backend=self.backend, evaluation_id="uwg-evaluation-runtime"
        )
        evaluation = evaluation_result["evaluation"]
        self.assertEqual("PASS", evaluation["status"])
        self.assertEqual(0, evaluation["aggregate"]["negative_leakage_rate"])
        self.assertEqual(0, evaluation["aggregate"]["lane_leakage_rate"])
        self.assertEqual(0, evaluation["aggregate"]["acl_leakage_rate"])
        receipt = self.gateway.prepare(
            Path(evaluation_result["evaluation_path"]),
            self._decision_path(evaluation),
            backend=self.backend,
        )
        self.assertEqual(
            self.candidate_pair["generation_id"], self.manager.active_pair()["generation_id"]
        )
        self.assertNotEqual(
            self.candidate_pair["graph_sha256"], receipt["next_pair"]["graph_sha256"]
        )
        self.assertEqual("PASS", receipt["uwg"]["w0_audit"]["status"])
        self.assertEqual("PASS", receipt["uwg"]["e0_audit"]["status"])
        graph_connection = sqlite3.connect(
            self.manager._resolve(receipt["next_pair"]["graph_path"])
        )
        try:
            row = graph_connection.execute(
                "SELECT canonical_status,canonical_eligible,retrieval_eligible "
                "FROM assertion WHERE assertion_id=?",
                (self.assertion_id,),
            ).fetchone()
            self.assertEqual(("canonical", 1, 1), tuple(row))
        finally:
            graph_connection.close()
        approved = HybridRetriever(
            self.manager._resolve(receipt["next_pair"]["sidecar_path"]), self.backend
        ).retrieve_lane(
            "UWG runtime fixture direct fact",
            "approved",
            classification="internal",
            audiences=["nee-engineering"],
            top_k=5,
            retrieval_mode="factual",
        )
        approved_ids = {
            item["record"]["extensions"]["com.partner_advisory.nee.graph-object-id"]
            for item in approved["results"]
        }
        self.assertIn(self.assertion_id, approved_ids)
        receipt_path = Path(receipt["receipt_path"])
        self._evaluate_bge_release(receipt_path)
        activation = self.gateway.activate(receipt_path)
        self.assertEqual("ACTIVE", activation["status"])
        self.assertEqual(
            receipt["next_pair"]["generation_id"], self.manager.active_pair()["generation_id"]
        )
        rollback = self.gateway.rollback(Path(receipt["receipt_path"]))
        self.assertEqual("ROLLED_BACK", rollback["status"])
        self.assertEqual(
            self.candidate_pair["generation_id"], self.manager.active_pair()["generation_id"]
        )

    def test_signed_registration_creates_an_approval_pair_before_evaluation(self) -> None:
        self._reset_runtime_fixture(insert_approval=False)
        request_path = self._approval_request_path()
        authorization = self._signed_registration_context(request_path)
        original_pair = self.manager.active_pair()
        receipt = self.gateway.register_governance_approval(
            request_path,
            backend=self.backend,
            authorization_context=authorization,
        )
        self.assertEqual(
            original_pair["generation_id"], self.manager.active_pair()["generation_id"]
        )
        self.assertNotEqual(
            original_pair["graph_sha256"], receipt["next_pair"]["graph_sha256"]
        )
        self.assertEqual(
            "uwg_governance_approval_registration", receipt["release_kind"]
        )
        self.assertEqual("PASS", receipt["uwg"]["w0_audit"]["status"])
        self.assertEqual("PASS", receipt["uwg"]["e0_audit"]["status"])
        graph = self.manager._resolve(receipt["next_pair"]["graph_path"])
        connection = sqlite3.connect(graph)
        try:
            row = connection.execute(
                """SELECT approval_type,scope_json,status,active FROM governance_approval
                   WHERE approval_id=?""",
                (self.approval_id,),
            ).fetchone()
            self.assertEqual("ingestion_promotion", row[0])
            self.assertEqual(self.approval_scope, json.loads(row[1]))
            self.assertEqual(("approved", 1), tuple(row[2:]))
        finally:
            connection.close()
        receipt_path = Path(receipt["receipt_path"])
        self._evaluate_bge_release(receipt_path)
        activation = self.gateway.activate(receipt_path, authorization_context=authorization)
        self.assertEqual("ACTIVE", activation["status"])
        self.assertEqual(
            receipt["next_pair"]["generation_id"], self.manager.active_pair()["generation_id"]
        )
        evaluation_result = self.gateway.evaluate(
            self._benchmark_path(),
            backend=self.backend,
            evaluation_id="uwg-evaluation-after-signed-registration",
        )
        prepared = self.gateway.prepare(
            Path(evaluation_result["evaluation_path"]),
            self._decision_path(evaluation_result["evaluation"]),
            backend=self.backend,
        )
        self.assertEqual("PREPARED", prepared["status"])

    def test_registration_rejects_unsigned_or_wrongly_bound_authorization(self) -> None:
        self._reset_runtime_fixture(insert_approval=False)
        request_path = self._approval_request_path()
        with self.assertRaisesRegex(PermissionError, "signed authorization"):
            self.gateway.register_governance_approval(
                request_path,
                backend=self.backend,
                authorization_context=None,
            )
        with self.assertRaisesRegex(PermissionError, "not bound"):
            self.gateway.register_governance_approval(
                request_path,
                backend=self.backend,
                authorization_context=self._signed_registration_context(
                    request_path, tamper_resource=True
                ),
            )
        with self.assertRaisesRegex(PermissionError, "approved_by"):
            self.gateway.register_governance_approval(
                request_path,
                backend=self.backend,
                authorization_context=self._signed_registration_context(
                    request_path, subject="different-signed-operator"
                ),
            )

    def test_registration_rejects_a_source_snapshot_that_drifted_from_the_graph(self) -> None:
        self._reset_runtime_fixture(insert_approval=False)
        request_path = self._approval_request_path(source_content_hash="f" * 64)
        authorization = self._signed_registration_context(request_path)
        active = self.manager.active_pair()
        with self.assertRaisesRegex(RuntimeError, "source snapshot binding"):
            self.gateway.register_governance_approval(
                request_path,
                backend=self.backend,
                authorization_context=authorization,
            )
        self.assertEqual(active["generation_id"], self.manager.active_pair()["generation_id"])
        connection = sqlite3.connect(self.manager._resolve(active["graph_path"]))
        try:
            self.assertIsNone(
                connection.execute(
                    "SELECT approval_id FROM governance_approval WHERE approval_id=?",
                    (self.approval_id,),
                ).fetchone()
            )
        finally:
            connection.close()

    def test_rejects_derivative_evidence_even_when_retrieval_passes(self) -> None:
        self._reset_runtime_fixture(directness="derivative")
        evaluation_result = self.gateway.evaluate(
            self._benchmark_path(), backend=self.backend, evaluation_id="uwg-evaluation-derivative"
        )
        self.assertEqual("PASS", evaluation_result["evaluation"]["status"])
        with self.assertRaisesRegex(RuntimeError, "direct, exact, high-authority"):
            self.gateway.prepare(
                Path(evaluation_result["evaluation_path"]),
                self._decision_path(evaluation_result["evaluation"]),
                backend=self.backend,
            )

    def test_rejects_nonfactual_candidates_even_when_the_sidecar_looks_factual(self) -> None:
        """The graph qualifier gate must defeat a misleading candidate sidecar."""

        for label, qualifiers in {
            "citation_only": {
                # Keeping the graph candidate type as ``assertion`` means the
                # sidecar projection is still an assertion/candidate_hypothesis
                # record.  UWG must read the persisted source governance too.
                "dual_pipeline_candidate_type": "assertion",
                "retrieval_kind": "evidence_span",
                "answer_use": "citation_only",
                "fact_promotion_eligible": False,
            },
            "guidance": {
                "dual_pipeline_candidate_type": "assertion",
                "retrieval_kind": "guidance",
                "answer_use": "guidance",
                "fact_promotion_eligible": False,
            },
            "source_chunk": {
                "dual_pipeline_candidate_type": "source_chunk",
                "retrieval_kind": "evidence_span",
                "answer_use": "citation_only",
                "fact_promotion_eligible": False,
            },
            "action": {
                "dual_pipeline_candidate_type": "action",
                "retrieval_kind": "guidance",
                "answer_use": "guidance",
                "fact_promotion_eligible": False,
            },
        }.items():
            with self.subTest(label=label):
                self._reset_runtime_fixture(candidate_qualifiers=qualifiers)
                with self.assertRaisesRegex(RuntimeError, "UWG target.*factual candidate"):
                    self.gateway.evaluate(
                        self._benchmark_path(),
                        backend=self.backend,
                        evaluation_id=f"uwg-evaluation-{label}",
                    )

    def test_intake_payload_preserves_source_bound_fact_promotion_governance(self) -> None:
        """The writer receives the exact retrieval controls that UWG later reads."""

        from dual_pipeline.intake import build_graph_change_set

        connection = sqlite3.connect(self.seed_graph)
        try:
            subject_id = connection.execute(
                "SELECT node_id FROM node WHERE active=1 ORDER BY node_id LIMIT 1"
            ).fetchone()[0]
        finally:
            connection.close()
        candidate_id = "candidate-uwg-governance-propagation"
        governance = {
            "source_item_id": "FACT-UWG-PROPAGATION",
            "retrieval_kind": "assertion",
            "answer_use": "candidate_hypothesis",
            "fact_promotion_eligible": True,
            "review_questions": ["Does the direct source support this assertion?"],
            "promotion_status": "candidate_evidence_bound_hitl_required",
            "force_hitl": True,
        }
        bundle = {
            "run_id": "run-uwg-governance-propagation",
            "trace_id": "a" * 32,
            "source_id": self.source_id,
            "source_version_id": self.source_version_id,
            "evidence": [],
            "candidates": [
                {
                    "candidate_id": candidate_id,
                    "candidate_type": "assertion",
                    "assertion_type": "candidate_assertion",
                    "normalized_text": "UWG source-bound governance propagation fixture.",
                    "materiality": "medium",
                    "entity_mentions": [{"mention_id": "mention-uwg-propagation"}],
                    "evidence_ids": [self.evidence_id],
                    "source_locator": {"kind": "section", "value": "fixture"},
                    "classification": "internal",
                    "audiences": ["nee-engineering"],
                    "extraction_confidence": 0.9,
                }
            ],
            "extensions": {
                "com.partner_advisory.nee.a2a-packet-candidate-governance": {
                    candidate_id: governance
                },
                "com.partner_advisory.nee.source": {},
                "com.partner_advisory.nee.context": {},
            },
        }
        change_set = build_graph_change_set(
            bundle,
            [{"candidate_id": candidate_id, "disposition": "CANDIDATE"}],
            [
                {
                    "mention_id": "mention-uwg-propagation",
                    "selected_entity_id": subject_id,
                    "outcome": "AUTO_LINK_EXACT",
                }
            ],
            self.seed_graph,
        )
        self.assertIsNotNone(change_set)
        assert change_set is not None
        operation_id = change_set["operations"][0]["operation_id"]
        payload = change_set["extensions"]["com.partner_advisory.nee.operation-payloads"][
            operation_id
        ]
        self.assertEqual(
            {
                "source_item_id": governance["source_item_id"],
                "retrieval_kind": "assertion",
                "answer_use": "candidate_hypothesis",
                "fact_promotion_eligible": True,
                "promotion_status": "candidate_evidence_bound_hitl_required",
            },
            payload["retrieval_governance"],
        )

    def test_uwg_rejects_a2a_fact_metadata_without_reverifiable_run_evidence(self) -> None:
        """A graph-persisted receipt-shaped dict is not evidence of archive replay."""

        self._reset_runtime_fixture(
            candidate_qualifiers={
                "dual_pipeline_candidate_type": "assertion",
                "retrieval_kind": "assertion",
                "answer_use": "candidate_hypothesis",
                "fact_promotion_eligible": True,
                "source_kind": "a2a_fact_vector_packet",
                "a2a_finalization_binding": {
                    "receipt_id": "a2a-receipt-fixture",
                    "receipt_canonical_digest": "1" * 64,
                    "receipt_file_sha256": "2" * 64,
                    "package_file_sha256": "3" * 64,
                    "package_canonical_digest": "4" * 64,
                    "archive_root_id": "a2a-archive-fixture",
                    "archive_binding_digest": "5" * 64,
                    "verification_state": "finalization_archive_replayed",
                },
            }
        )
        with self.assertRaisesRegex(RuntimeError, "no re-verifiable finalization evidence"):
            self.gateway.evaluate(
                self._benchmark_path(),
                backend=self.backend,
                evaluation_id="uwg-evaluation-a2a-unreverifiable",
            )

    def test_rejects_a_decision_bound_to_a_stale_pair(self) -> None:
        evaluation_result = self.gateway.evaluate(
            self._benchmark_path(), backend=self.backend, evaluation_id="uwg-evaluation-stale"
        )
        with self.assertRaisesRegex(ValueError, "different candidate pairs"):
            self.gateway.prepare(
                Path(evaluation_result["evaluation_path"]),
                self._decision_path(evaluation_result["evaluation"], stale_pair=True),
                backend=self.backend,
            )

    def test_rejects_a_benchmark_with_weakened_or_unowned_thresholds(self) -> None:
        thresholds = dict(self._policy()["thresholds"])
        thresholds["min_mrr"] = 0.5
        with self.assertRaisesRegex(ValueError, "thresholds must exactly match"):
            self.gateway.evaluate(
                self._benchmark_path(thresholds=thresholds),
                backend=self.backend,
                evaluation_id="uwg-evaluation-policy-drift",
            )
        binding = self._policy_binding()
        binding["owner"] = "unapproved-owner"
        with self.assertRaisesRegex(ValueError, "acceptance policy"):
            self.gateway.evaluate(
                self._benchmark_path(acceptance_policy=binding),
                backend=self.backend,
                evaluation_id="uwg-evaluation-policy-owner-drift",
            )

    def test_rejects_nonfinite_policy_numbers(self) -> None:
        for value in (float("inf"), -float("inf"), float("nan")):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "must be finite"):
                    _number(
                        value,
                        label="thresholds.max_latency_ms",
                        minimum=1,
                        maximum=float("inf"),
                    )

    def test_runtime_evaluation_fails_negative_and_acl_leakage_checks(self) -> None:
        evaluation = self.gateway.evaluate(
            self._benchmark_path(negative_query="UWG runtime fixture direct fact"),
            backend=self.backend,
            evaluation_id="uwg-evaluation-negative-leak",
        )["evaluation"]
        self.assertEqual("FAIL", evaluation["status"])
        self.assertEqual(1, evaluation["aggregate"]["negative_leakage_rate"])
        self.assertFalse(evaluation["negative_results"][0]["pass"])
        acl_evaluation = self.gateway.evaluate(
            self._benchmark_path(
                acl_classification="internal", acl_audiences=["nee-engineering"]
            ),
            backend=self.backend,
            evaluation_id="uwg-evaluation-acl-leak",
        )["evaluation"]
        self.assertEqual("FAIL", acl_evaluation["status"])
        self.assertEqual(1, acl_evaluation["aggregate"]["acl_leakage_rate"])
        self.assertFalse(acl_evaluation["acl_results"][0]["pass"])

    def test_rejects_source_snapshot_hash_mismatch(self) -> None:
        self._reset_runtime_fixture(approval_snapshot_hash="f" * 64)
        evaluation_result = self.gateway.evaluate(
            self._benchmark_path(), backend=self.backend, evaluation_id="uwg-evaluation-snapshot"
        )
        with self.assertRaisesRegex(RuntimeError, "source snapshot binding"):
            self.gateway.prepare(
                Path(evaluation_result["evaluation_path"]),
                self._decision_path(
                    evaluation_result["evaluation"], source_content_hash="f" * 64
                ),
                backend=self.backend,
            )

    def test_rejects_expired_or_nonexact_graph_approval_scope(self) -> None:
        self._reset_runtime_fixture(
            approval_approved_at="2020-01-01T00:00:00Z",
            approval_expires_at="2020-01-02T00:00:00Z",
        )
        expired = self.gateway.evaluate(
            self._benchmark_path(), backend=self.backend, evaluation_id="uwg-evaluation-expired"
        )
        with self.assertRaisesRegex(RuntimeError, "approval is expired"):
            self.gateway.prepare(
                Path(expired["evaluation_path"]),
                self._decision_path(expired["evaluation"]),
                backend=self.backend,
            )
        self._reset_runtime_fixture(exact_approval_scope=False)
        generic = self.gateway.evaluate(
            self._benchmark_path(), backend=self.backend, evaluation_id="uwg-evaluation-generic"
        )
        with self.assertRaisesRegex(RuntimeError, "exact one-time promotion scope"):
            self.gateway.prepare(
                Path(generic["evaluation_path"]),
                self._decision_path(generic["evaluation"]),
                backend=self.backend,
            )

    def test_rejects_contradictory_evidence_before_promotion(self) -> None:
        self._reset_runtime_fixture(directness="direct", contradictory_evidence=True)
        evaluation_result = self.gateway.evaluate(
            self._benchmark_path(),
            backend=self.backend,
            evaluation_id="uwg-evaluation-contradiction",
        )
        self.assertEqual("PASS", evaluation_result["evaluation"]["status"])
        with self.assertRaisesRegex(RuntimeError, "contradictory evidence"):
            self.gateway.prepare(
                Path(evaluation_result["evaluation_path"]),
                self._decision_path(evaluation_result["evaluation"]),
                backend=self.backend,
            )

    def test_rejects_a_noncurrent_target_on_the_staged_graph(self) -> None:
        evaluation_result = self.gateway.evaluate(
            self._benchmark_path(), backend=self.backend, evaluation_id="uwg-evaluation-currentness"
        )
        decision_path = self._decision_path(evaluation_result["evaluation"])
        staged_graph = self.root / "currentness-check.sqlite"
        active_graph = self.manager._resolve(self.manager.active_pair()["graph_path"])
        exact_file_copy(active_graph, staged_graph)
        connection = sqlite3.connect(staged_graph)
        try:
            connection.execute(
                "UPDATE assertion SET currentness_state='historical' WHERE assertion_id=?",
                (self.assertion_id,),
            )
            connection.commit()
        finally:
            connection.close()
        decision = json.loads(decision_path.read_text(encoding="utf-8"))
        with self.assertRaisesRegex(RuntimeError, "resolved, current, unsuperseded"):
            self.gateway._validate_evidence_bindings(
                staged_graph,
                [self.assertion_id],
                decision,
                evaluation_result["evaluation"],
            )

    def test_private_stage_helpers_reject_the_active_generation_before_any_write(self) -> None:
        active = self.manager.active_pair()
        active_graph = self.manager._resolve(active["graph_path"])
        before = sha256_file(active_graph)
        with self.assertRaisesRegex(RuntimeError, "active graph"):
            self.gateway._stage_graph_promotion(
                generation_id=active["generation_id"],
                expected_active=active,
                candidate_ids=[self.assertion_id],
                evaluation={},
                decision={},
            )
        with self.assertRaisesRegex(RuntimeError, "active graph"):
            self.gateway._stage_governance_approval(
                generation_id=active["generation_id"],
                expected_active=active,
                request={},
                scope={},
                source_ids=[],
                authorization_context=None,  # type: ignore[arg-type]
            )
        self.assertEqual(before, sha256_file(active_graph))

    def test_seed_graph_is_never_mutated_by_uwg(self) -> None:
        before = sha256_file(self.seed_graph)
        evaluation_result = self.gateway.evaluate(
            self._benchmark_path(), backend=self.backend, evaluation_id="uwg-evaluation-seed"
        )
        self.gateway.prepare(
            Path(evaluation_result["evaluation_path"]),
            self._decision_path(evaluation_result["evaluation"]),
            backend=self.backend,
        )
        self.assertEqual(before, sha256_file(self.seed_graph))


if __name__ == "__main__":
    unittest.main()
