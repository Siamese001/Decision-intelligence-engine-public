#!/usr/bin/env python3
"""Focused regression tests for the BGE-M3 retrieval release gate."""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.authorization import AuthorizationContext  # noqa: E402
from dual_pipeline.cli import build_parser  # noqa: E402
from dual_pipeline.contracts import create_artifact, validate_artifact  # noqa: E402
from dual_pipeline.core import (  # noqa: E402
    connect_sqlite,
    exact_file_copy,
    read_json,
    seal_artifact,
    sha256_file,
    stable_id,
    write_json,
)
from dual_pipeline.index import (  # noqa: E402
    build_sidecar,
)
from dual_pipeline.release import (  # noqa: E402
    ReleaseManager,
    _seal_receipt,
    release_action_resources,
)
from dual_pipeline.retrieval_release import (  # noqa: E402
    RETRIEVAL_RELEASE_POLICY_PATH,
    RetrievalReleaseEvaluator,
    _assert_real_bge_backend,
    _assert_suite_authorized,
    _critical_case_failed,
    _evaluate_case,
    _load_policy,
    _max_rank_regression,
    _policy_binding,
    _reciprocal_rank,
    _records_by_id,
    _repeat_rank_drift,
    _scoped_release_delta,
    validate_activation_evaluation,
)
from dual_pipeline.retrieval_suite import (  # noqa: E402
    RETRIEVAL_SUITE_GENERATION_POLICY_PATH,
    AutomaticRetrievalReleaseSuiteBuilder,
    AutomaticSuiteBuildError,
    _load_generation_policy,
    _removed_group_cases,
)
from dual_pipeline.runtime_evidence import discover_inventory  # noqa: E402
from tests.embedding_fixture import (  # noqa: E402
    BgeM3TestBackend,
    MetadataOnlyBgeM3Backend,
)
from tools.build_release_action_request import main as build_release_action_request  # noqa: E402


class LocalBgeM3Backend(BgeM3TestBackend):
    """Pinned real BGE-M3 executor used by retrieval-release tests."""


_RUN_BGE_RELEASE_TESTS = os.environ.get("NEE_RUN_BGE_RELEASE_TESTS") == "1"


def _is_approved_factual_retrieval_record(artifact: dict[str, object]) -> bool:
    """Accept both atomic and relationship facts in a release-eval seed."""

    return (
        artifact.get("record_type") in {"assertion", "edge"}
        and artifact.get("retrieval_kind")
        in {"assertion", "relationship_assertion"}
        and artifact.get("answer_use") == "factual_answer"
    )


def _retrieval_projection(
    *,
    record_type: str = "assertion",
    lane: str = "approved",
    classification: str = "internal",
    audiences: list[str] | None = None,
) -> str:
    return json.dumps(
        {
            "retrieval_record": {
                "record_id": "record-added",
                "record_type": record_type,
                "lane": lane,
                "classification": classification,
                "audiences": audiences or ["internal_consulting"],
            },
            "object_facets": [],
        },
        sort_keys=True,
        separators=(",", ":"),
    )


@unittest.skipUnless(
    _RUN_BGE_RELEASE_TESTS,
    "real-BGE release tests disabled; use tools/run_ci_gates.py --profile bge",
)
class RetrievalReleaseEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        """Build a real BGE-M3 active/candidate template once per test class."""

        cls._template_temp = tempfile.TemporaryDirectory(
            prefix="nee-retrieval-release-template-"
        )
        cls._template_root = Path(cls._template_temp.name)
        cls._template_runtime = cls._template_root / "runtime"
        cls._template_seed_graph = ROOT / "graph" / "nee_graph.sqlite"
        cls._template_backend = LocalBgeM3Backend()
        cls._template_manager = ReleaseManager(
            cls._template_runtime, cls._template_seed_graph
        )
        cls._template_active = cls._template_manager.bootstrap(cls._template_backend)
        cls._template_candidate = cls._build_candidate_pair(
            manager=cls._template_manager,
            active=cls._template_active,
            backend=cls._template_backend,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._template_temp.cleanup()

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="nee-retrieval-release-")
        self.root = Path(self.temp.name)
        self.runtime = self.root / "runtime"
        shutil.copytree(self._template_runtime, self.runtime)
        self.seed_graph = ROOT / "graph" / "nee_graph.sqlite"
        self.backend = LocalBgeM3Backend()
        self.manager = ReleaseManager(self.runtime, self.seed_graph)
        self.active = self.manager.active_pair()
        self.candidate = self._template_candidate
        self.receipt = self._prepared_receipt()
        self.receipt_path = (
            self.runtime / "releases" / f"release-{self.candidate['generation_id']}.json"
        )
        write_json(self.receipt_path, self.receipt)

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _build_candidate_pair(
        *,
        manager: ReleaseManager,
        active: dict,
        backend: LocalBgeM3Backend,
    ) -> dict:
        generation_id = stable_id(
            "generation-test-candidate", active["generation_id"], length=28
        )
        generation_dir = manager.runtime_root / "generations" / generation_id
        generation_dir.mkdir(parents=True)
        graph_path = generation_dir / "graph.sqlite"
        exact_file_copy(manager._resolve(active["graph_path"]), graph_path)
        # This intentionally non-retrieval mutation produces a different graph
        # snapshot without changing the governed retrieval projection.  Delta
        # coverage tests below separately prove that projection deltas cannot
        # be omitted.
        connection = connect_sqlite(graph_path)
        try:
            node_id = connection.execute(
                "SELECT node_id FROM node ORDER BY node_id LIMIT 1"
            ).fetchone()[0]
            connection.execute(
                "UPDATE node SET updated_at='2026-07-28T00:00:00Z' WHERE node_id=?",
                (node_id,),
            )
            connection.commit()
        finally:
            connection.close()
        graph_hash = sha256_file(graph_path)
        graph_snapshot = stable_id("graph-snapshot", graph_hash, length=24)
        sidecar_path = generation_dir / "sidecar.sqlite"
        index_receipt = build_sidecar(
            graph_path=graph_path,
            sidecar_path=sidecar_path,
            generation_id=generation_id,
            graph_snapshot=graph_snapshot,
            backend=backend,
        )
        pair = {
            "schema_version": "1.0.0",
            "generation_id": generation_id,
            "graph_snapshot": graph_snapshot,
            "graph_sha256": graph_hash,
            "sidecar_sha256": index_receipt["sidecar_sha256"],
            "graph_path": manager._relative(graph_path),
            "sidecar_path": manager._relative(sidecar_path),
            "embedding_backend": backend.backend_id,
            "model_id": backend.model_id,
            "model_revision": backend.model_revision,
            "model_manifest_digest": backend.model_manifest_digest,
            "production_qualified": False,
            "previous_generation_id": active["generation_id"],
            "activated_at": None,
        }
        manager.validate_pair(pair)
        write_json(generation_dir / "pair_manifest.json", pair)
        return pair

    def _prepared_receipt(self) -> dict:
        return _seal_receipt(
            {
                "schema_version": "1.0.0",
                "receipt_id": "release-receipt-retrieval-evaluation-test",
                "status": "PREPARED",
                "run_id": "run-retrieval-evaluation-test",
                "trace_id": "a" * 32,
                "source_id": "source-retrieval-evaluation-test",
                "approval_id": "approval-retrieval-evaluation-test",
                "change_set_id": "change-set-retrieval-evaluation-test",
                "change_set_digest": "b" * 64,
                "component_runs": [],
                "previous_pair": self.active,
                "next_pair": self.candidate,
                "graph_write_receipt": {},
                "graph_delta_summary": {},
                "label_quality_validation": {},
                "embedding_generation_receipt": {},
                "pair_validation": {},
                "production_qualified": False,
                "authorization": None,
                "prepared_at": "2026-07-28T00:00:00Z",
            }
        )

    def _approved_record(self) -> tuple[str, str, str, list[str]]:
        connection = connect_sqlite(
            self.manager._resolve(self.active["sidecar_path"]), readonly=True
        )
        try:
            for row in connection.execute(
                "SELECT record_id,artifact_json FROM retrieval_record "
                "WHERE lane='approved' ORDER BY record_id"
            ):
                artifact = json.loads(row["artifact_json"])
                if _is_approved_factual_retrieval_record(artifact):
                    return (
                        row["record_id"],
                        artifact["retrieval_text"],
                        artifact["classification"],
                        artifact["audiences"],
                    )
        finally:
            connection.close()
        self.fail("seed graph has no approved factual retrieval record")

    def _suite_path(self, *, coverage: list[dict] | None = None) -> Path:
        record_id, query, classification, audiences = self._approved_record()
        policy = read_json(RETRIEVAL_RELEASE_POLICY_PATH)
        suite = create_artifact(
            "retrieval-release-suite",
            "retrieval-release-suite-test",
            suite_id="retrieval-release-suite-test",
            execution_tier="E3",
            acceptance_policy=_policy_binding(policy),
            cases=[
                {
                    "case_id": "core-retrieval-test",
                    "scope": "core",
                    "query_kind": "semantic_paraphrase",
                    "critical": True,
                    "query": query,
                    "classification": classification,
                    "audiences": audiences,
                    "lane": "approved",
                    "retrieval_mode": "factual",
                    "top_k": 5,
                    "expected_record_ids": [record_id],
                    "forbidden_record_ids": [],
                },
            ],
            coverage=[] if coverage is None else coverage,
            created_at="2026-07-28T00:00:00Z",
            extensions={},
        )
        path = self.root / "suite.json"
        write_json(path, suite)
        return path

    def test_passing_evaluation_binds_exact_receipt_and_allows_activation_evidence(self) -> None:
        evaluator = RetrievalReleaseEvaluator(self.runtime, self.seed_graph)
        with self.assertRaisesRegex(RuntimeError, "requires a sealed retrieval release evaluation"):
            validate_activation_evaluation(self.runtime, self.receipt)
        with self.assertRaisesRegex(RuntimeError, "requires a sealed retrieval release evaluation"):
            self.manager.activate(self.receipt_path)
        result = evaluator.evaluate(self.receipt_path, self._suite_path(), backend=self.backend)
        self.assertEqual("PASS", result["evaluation"]["status"])
        self.assertTrue(result["evaluation"]["gates"]["real_bge_m3"])
        inventory = discover_inventory(self.root, extra_roots=[self.runtime])
        self.assertEqual(
            [result["evaluation"]["evaluation_id"]],
            [
                item["evaluation_id"]
                for item in inventory["retrieval_evaluations"]
            ],
        )
        binding = validate_activation_evaluation(self.runtime, self.receipt)
        self.assertTrue(binding["required"])
        self.assertEqual(
            result["evaluation"]["canonical_digest"]["value"],
            binding["evaluation_digest"],
        )
        stale = dict(result["evaluation"])
        stale["prepared_receipt_digest"] = "d" * 64
        write_json(Path(result["evaluation_path"]), seal_artifact(stale))
        with self.assertRaisesRegex(RuntimeError, "stale, incomplete, or failed"):
            validate_activation_evaluation(self.runtime, self.receipt)
        with self.assertRaisesRegex(RuntimeError, "stale, incomplete, or failed"):
            self.manager.activate(self.receipt_path)

    def test_automatic_suite_is_deterministic_persisted_and_evaluable(self) -> None:
        builder = AutomaticRetrievalReleaseSuiteBuilder(
            self.runtime,
            self.seed_graph,
        )
        first = builder.build(self.receipt_path)
        second = builder.build(self.receipt_path)
        self.assertEqual(first, second)
        self.assertEqual("automatic", first["source"])
        self.assertEqual(1, first["case_count"])
        self.assertEqual(0, first["added_or_changed_record_count"])
        self.assertEqual(0, first["removed_record_count"])
        suite_path = Path(first["path"])
        self.assertTrue(suite_path.is_file())
        self.assertEqual(
            self.runtime.resolve(),
            next(
                parent
                for parent in suite_path.parents
                if parent == self.runtime.resolve()
            ),
        )
        result = RetrievalReleaseEvaluator(self.runtime, self.seed_graph).evaluate(
            self.receipt_path,
            suite_path,
            backend=self.backend,
        )
        self.assertEqual("PASS", result["evaluation"]["status"])

    def test_explicit_suite_is_copied_into_runtime_before_evaluation(self) -> None:
        source = self._suite_path()
        resolution = AutomaticRetrievalReleaseSuiteBuilder(
            self.runtime,
            self.seed_graph,
        ).persist_explicit(source)
        target = Path(resolution["path"])
        self.assertTrue(target.is_file())
        self.assertNotEqual(source.resolve(), target.resolve())
        self.assertEqual(sha256_file(source), sha256_file(target))
        self.assertEqual("explicit", resolution["source"])

    def test_generation_policy_failure_has_a_stable_reason_code(self) -> None:
        invalid_policy = read_json(RETRIEVAL_SUITE_GENERATION_POLICY_PATH)
        invalid_policy["semantic_templates"] = ["Only one template"]
        invalid_path = self.root / "invalid-generation-policy.json"
        write_json(invalid_path, invalid_policy)
        with self.assertRaises(AutomaticSuiteBuildError) as raised:
            _load_generation_policy(invalid_path)
        self.assertEqual(
            "GENERATION_POLICY_SEMANTIC_TEMPLATES",
            raised.exception.code,
        )

    def test_failed_attempt_is_retained_without_blocking_corrected_pass(self) -> None:
        passing_path = self._suite_path()
        failing = deepcopy(read_json(passing_path))
        record_id = failing["cases"][0]["expected_record_ids"][0]
        failing["cases"][0]["forbidden_record_ids"] = [record_id]
        failing = seal_artifact(failing)
        failing_path = self.root / "suite-failing.json"
        write_json(failing_path, failing)

        failed = RetrievalReleaseEvaluator(self.runtime, self.seed_graph).evaluate(
            self.receipt_path,
            failing_path,
            backend=self.backend,
        )
        self.assertEqual("FAIL", failed["evaluation"]["status"])
        self.assertIn(
            str(Path("retrieval_evaluations") / "failed"),
            failed["evaluation_path"],
        )
        canonical = (
            self.runtime
            / "retrieval_evaluations"
            / f"retrieval-release-evaluation-{self.receipt['receipt_id']}.json"
        )
        self.assertFalse(canonical.exists())

        passed = RetrievalReleaseEvaluator(self.runtime, self.seed_graph).evaluate(
            self.receipt_path,
            passing_path,
            backend=self.backend,
        )
        self.assertEqual("PASS", passed["evaluation"]["status"])
        self.assertTrue(canonical.is_file())
        replay = RetrievalReleaseEvaluator(self.runtime, self.seed_graph).evaluate(
            self.receipt_path,
            passing_path,
            backend=self.backend,
        )
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(
            passed["evaluation"]["canonical_digest"],
            replay["evaluation"]["canonical_digest"],
        )

    def test_activation_action_request_requires_and_binds_bge_evaluation(self) -> None:
        output = self.root / "activate-request.json"
        command = [
            "build_release_action_request.py",
            "--receipt",
            str(self.receipt_path),
            "--action",
            "activate",
            "--runtime-root",
            str(self.runtime),
            "--output",
            str(output),
        ]
        with patch.object(sys, "argv", command), self.assertRaisesRegex(
            RuntimeError, "requires a sealed retrieval release evaluation"
        ):
            build_release_action_request()

        evaluation = RetrievalReleaseEvaluator(self.runtime, self.seed_graph).evaluate(
            self.receipt_path,
            self._suite_path(),
            backend=self.backend,
        )
        self.assertEqual(
            "PASS",
            evaluation["evaluation"]["status"],
            evaluation["evaluation"]["gates"],
        )
        with patch.object(sys, "argv", command), redirect_stdout(io.StringIO()):
            self.assertEqual(0, build_release_action_request())

        request = read_json(output)
        self.assertEqual(
            evaluation["evaluation"]["canonical_digest"]["value"],
            request["nee_authorized_resources"]["retrieval_release_evaluation"],
        )
        self.assertEqual(
            evaluation["evaluation"]["evaluation_id"],
            request["retrieval_release_evaluation"]["evaluation_id"],
        )

class RetrievalReleaseStaticGateTests(unittest.TestCase):
    def test_approved_factual_record_selection_accepts_relationship_assertions(self) -> None:
        self.assertTrue(
            _is_approved_factual_retrieval_record(
                {
                    "record_type": "edge",
                    "retrieval_kind": "relationship_assertion",
                    "answer_use": "factual_answer",
                }
            )
        )
        self.assertFalse(
            _is_approved_factual_retrieval_record(
                {
                    "record_type": "edge",
                    "retrieval_kind": "relationship_assertion",
                    "answer_use": "guidance",
                }
            )
        )

    def test_removed_relationship_group_has_required_relationship_coverage(self) -> None:
        cases, case_ids, relationship = _removed_group_cases(
            [
                {
                    "record_id": "removed-relationship",
                    "record_type": "edge",
                    "lane": "approved",
                    "answer_use": "factual_answer",
                    "classification": "internal",
                    "audiences": ["internal_consulting"],
                    "title": "Removed historical relationship",
                    "canonical_entities": ["EDO"],
                    "retrieval_text": "Historical portfolio relationship to remove.",
                    "extensions": {},
                }
            ],
            ordinal=1,
            generation_policy=_load_generation_policy(),
        )
        self.assertTrue(relationship)
        self.assertEqual(
            ["stale_or_superseded", "relationship"],
            [case["query_kind"] for case in cases],
        )
        self.assertEqual(
            ["auto-removed-0001-stale", "auto-removed-0001-relationship"],
            case_ids,
        )
        self.assertTrue(
            all(
                case["forbidden_record_ids"] == ["removed-relationship"]
                for case in cases
            )
        )

    def test_receipt_contract_requires_pinned_bge_and_all_hybrid_components(self) -> None:
        fixture = read_json(
            ROOT
            / "contracts"
            / "dual_pipeline"
            / "fixtures"
            / "valid"
            / "retrieval-release-evaluation.json"
        )
        mutations = {
            "missing_manifest": lambda value: value["candidate_pair"].__setitem__(
                "model_manifest_digest", None
            ),
            "wrong_model": lambda value: value["candidate_pair"].__setitem__(
                "model_id", "BAAI/not-bge-m3"
            ),
            "missing_bm25_component": lambda value: value["case_results"][0][
                "component_execution"
            ].pop("bm25"),
            "missing_bm25_gate": lambda value: value["gates"].pop(
                "bm25_execution"
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                value = deepcopy(fixture)
                mutate(value)
                with self.assertRaises(ValueError):
                    validate_artifact(seal_artifact(value))

    def test_activation_refuses_unbound_or_non_bge_pair_before_lookup(self) -> None:
        pair = {
            "generation_id": "generation-fixture",
            "graph_snapshot": "snapshot-fixture",
            "graph_sha256": "a" * 64,
            "sidecar_sha256": "b" * 64,
            "embedding_backend": "flagembedding-bge-m3-dev",
            "model_id": "BAAI/bge-m3",
            "model_revision": "0" * 40,
            "model_manifest_digest": "c" * 64,
            "production_qualified": False,
        }
        missing_manifest = deepcopy(pair)
        missing_manifest["model_manifest_digest"] = None
        with self.assertRaisesRegex(ValueError, "incomplete retrieval-release binding"):
            validate_activation_evaluation(
                ROOT / ".local" / "unused-runtime",
                _seal_receipt(
                    {
                        "production_qualified": False,
                        "previous_pair": pair,
                        "next_pair": missing_manifest,
                    }
                ),
            )

        non_bge = deepcopy(pair)
        non_bge["embedding_backend"] = "retired-embedding-backend"
        with self.assertRaisesRegex(PermissionError, "approved BGE-M3"):
            validate_activation_evaluation(
                ROOT / ".local" / "unused-runtime",
                _seal_receipt(
                    {
                        "production_qualified": False,
                        "previous_pair": pair,
                        "next_pair": non_bge,
                    }
                ),
            )

    def test_negative_only_case_has_full_non_leakage_reciprocal_rank(self) -> None:
        self.assertIsNone(_reciprocal_rank(None, set()))
        self.assertEqual(0.0, _reciprocal_rank(None, {"record-expected"}))

    def test_repeat_rank_drift_detects_reordering_and_missing_hits(self) -> None:
        self.assertEqual(
            2,
            _repeat_rank_drift(
                [["record-a", "record-b"], ["record-b"]],
                2,
            ),
        )
        self.assertEqual(
            0,
            _repeat_rank_drift(
                [["record-a", "record-b"], ["record-a", "record-b"]],
                2,
            ),
        )

    def test_case_forwards_facet_and_assertion_scope(self) -> None:
        captured: dict = {}

        class Retriever:
            def retrieve_lane(self, query: str, lane: str, **kwargs: object) -> dict:
                captured.update({"query": query, "lane": lane, **kwargs})
                return {
                    "results": [],
                    "dense_candidate_count": 0,
                    "sparse_candidate_count": 0,
                    "colbert_candidate_count": 0,
                }

        case = {
            "query": "scoped architecture",
            "lane": "approved",
            "query_kind": "semantic_paraphrase",
            "classification": "internal",
            "audiences": ["internal_consulting"],
            "top_k": 5,
            "retrieval_mode": "factual",
            "facet_filters": {"semantic_domain": ["architecture"]},
            "allowed_assertion_ids": ["assertion-scoped"],
        }
        _evaluate_case(Retriever(), case)  # type: ignore[arg-type]
        self.assertEqual(case["facet_filters"], captured["facet_filters"])
        self.assertEqual(
            case["allowed_assertion_ids"], captured["allowed_assertion_ids"]
        )

    def test_facet_projection_change_requires_delta_coverage(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nee-retrieval-facet-") as temporary:
            root = Path(temporary)

            def build(path: Path, value: str) -> None:
                connection = connect_sqlite(path)
                try:
                    connection.executescript(
                        """
                        CREATE TABLE retrieval_record (
                            record_id TEXT PRIMARY KEY,
                            artifact_json TEXT NOT NULL
                        );
                        CREATE TABLE retrieval_record_object_facet (
                            record_id TEXT NOT NULL,
                            assignment_id TEXT NOT NULL,
                            object_type TEXT NOT NULL,
                            object_id TEXT NOT NULL,
                            vocabulary TEXT NOT NULL,
                            value TEXT NOT NULL,
                            assignment_method TEXT NOT NULL,
                            review_state TEXT NOT NULL
                        );
                        """
                    )
                    connection.execute(
                        "INSERT INTO retrieval_record VALUES (?,?)",
                        (
                            "record-facet",
                            json.dumps(
                                {
                                    "record_id": "record-facet",
                                    "retrieval_text": "Architecture control",
                                }
                            ),
                        ),
                    )
                    connection.execute(
                        """INSERT INTO retrieval_record_object_facet
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (
                            "record-facet",
                            "assignment-facet",
                            "assertion",
                            "assertion-facet",
                            "semantic_domain",
                            value,
                            "deterministic",
                            "approved",
                        ),
                    )
                    connection.commit()
                finally:
                    connection.close()

            baseline = root / "baseline.sqlite"
            candidate = root / "candidate.sqlite"
            build(baseline, "architecture")
            build(candidate, "delivery")
            coverage = RetrievalReleaseEvaluator._coverage(
                {"coverage": [], "cases": []},
                _records_by_id(baseline),
                _records_by_id(candidate),
                read_json(RETRIEVAL_RELEASE_POLICY_PATH),
            )
            self.assertFalse(coverage["complete"])
            self.assertEqual(
                ["record-facet"], coverage["added_or_changed_record_ids"]
            )

    def test_rank_regression_covers_every_expected_record(self) -> None:
        self.assertEqual(
            7,
            _max_rank_regression(
                ["stable", "secondary"],
                ["stable", "other-1", "other-2", "other-3"],
                {"stable", "secondary"},
                8,
            ),
        )

    def test_critical_negative_control_passes_when_forbidden_item_stays_absent(
        self,
    ) -> None:
        result = {
            "critical": True,
            "expected_record_ids": [],
            "forbidden_leakage": [],
            "candidate_retrieved_record_ids": ["allowed"],
        }
        self.assertFalse(_critical_case_failed(result, 5))
        result["forbidden_leakage"] = ["forbidden"]
        self.assertTrue(_critical_case_failed(result, 5))

    def test_production_suite_cannot_exceed_token_data_scope(self) -> None:
        now = int(time.time())
        context = AuthorizationContext(
            subject="retrieval-release-test",
            issuer="test-issuer",
            relying_party="test-audience",
            token_id="retrieval-release-token",
            issued_at=now - 10,
            expires_at=now + 600,
            classification_clearance="confidential",
            audiences=("internal_consulting",),
            capabilities=("knowledge:query",),
            authorized_resources={},
            approved_model_manifest_digest="c" * 64,
            token_digest="a" * 64,
            context_digest="b" * 64,
        )
        base_case = {
            "case_id": "authorized-case",
            "classification": "confidential",
            "audiences": ["internal_consulting"],
        }
        _assert_suite_authorized({"cases": [base_case]}, context)
        with self.assertRaisesRegex(PermissionError, "authorization"):
            _assert_suite_authorized(
                {
                    "cases": [
                        {
                            **base_case,
                            "case_id": "restricted-case",
                            "classification": "restricted",
                        }
                    ]
                },
                context,
            )
        with self.assertRaisesRegex(PermissionError, "authorization"):
            _assert_suite_authorized(
                {
                    "cases": [
                        {
                            **base_case,
                            "case_id": "wrong-audience-case",
                            "audiences": ["client"],
                        }
                    ]
                },
                context,
            )

    def test_cli_exposes_retrieval_release_evaluation(self) -> None:
        args = build_parser().parse_args(
            [
                "retrieval-release-evaluate",
                "--receipt",
                "receipt.json",
                "--suite",
                "suite.json",
                "--embedding-backend",
                "bge-m3-dev",
                "--allow-dev-bge-m3",
            ]
        )
        self.assertEqual("retrieval-release-evaluate", args.command)
        automatic = build_parser().parse_args(
            [
                "retrieval-release-evaluate",
                "--receipt",
                "receipt.json",
                "--embedding-backend",
                "bge-m3-dev",
                "--allow-dev-bge-m3",
            ]
        )
        self.assertIsNone(automatic.suite)

    def test_batch_release_accepts_the_suite_but_prepare_does_not(self) -> None:
        batch = build_parser().parse_args(
            [
                "intake-batch-release",
                "--directive-id",
                "directive-batch-suite",
                "--run-id",
                "run-batch-suite",
                "--retrieval-release-suite",
                "suite.json",
            ]
        )
        self.assertEqual(Path("suite.json"), batch.retrieval_release_suite)
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            build_parser().parse_args(
                [
                    "intake-release-prepare",
                    "--directive-id",
                    "directive-prepare-suite",
                    "--run-id",
                    "run-prepare-suite",
                    "--retrieval-release-suite",
                    "suite.json",
                ]
            )

    def test_coverage_rejects_unmapped_retrieval_projection(self) -> None:
        coverage = RetrievalReleaseEvaluator._coverage(
            {"coverage": [], "cases": []},
            {"record-core": "baseline"},
            {"record-core": "baseline", "record-added": "candidate"},
            read_json(RETRIEVAL_RELEASE_POLICY_PATH),
        )
        self.assertFalse(coverage["complete"])
        self.assertEqual(["record-added"], coverage["added_or_changed_record_ids"])

    def test_scoped_release_delta_isolates_run_changes_from_baseline_debt(self) -> None:
        # 1. Unscoped fallback when receipt or runtime_root is missing
        added, removed = _scoped_release_delta(
            None, None, {"rec-1": "v1"}, {"rec-2": "v2"}
        )
        self.assertEqual(["rec-2"], added)
        self.assertEqual(["rec-1"], removed)

        # 2. Scoped to receipt component runs with phantom baseline drift
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            run_dir = temp_root / "runs" / "run-intake-test-01"
            run_dir.mkdir(parents=True)
            write_json(
                run_dir / "graph_change_set.json",
                {
                    "operations": [
                        {
                            "operation": "add_candidate_assertion",
                            "target_id": "asrt-new-01",
                        },
                        {
                            "operation": "supersede_assertion",
                            "target_id": "asrt-old-01",
                        },
                    ]
                },
            )

            expected_add_rid = stable_id("retrieval-record", "asrt-new-01", length=28)
            expected_rem_rid = stable_id("retrieval-record", "asrt-old-01", length=28)
            phantom_rem_rid = "retrieval-record-phantom-legacy-drift"
            phantom_add_rid = "retrieval-record-phantom-added-drift"
            facet_add_rid = "retrieval-record-facet-entity-01"

            baseline = {
                expected_rem_rid: json.dumps({"retrieval_record": {"title": "Old"}}),
                phantom_rem_rid: json.dumps({"retrieval_record": {"title": "Drift"}}),
                "retrieval-record-unchanged": json.dumps({"retrieval_record": {"title": "Stable"}}),
            }
            candidate = {
                expected_add_rid: json.dumps({"retrieval_record": {"title": "New"}}),
                phantom_add_rid: json.dumps({"retrieval_record": {"title": "Phantom"}}),
                facet_add_rid: json.dumps(
                    {
                        "retrieval_record": {
                            "record_type": "entity_facet",
                            "title": "Facet",
                            "extensions": {
                                "com.partner_advisory.nee.entity-facet-source-record-ids": [expected_add_rid]
                            },
                        }
                    }
                ),
                "retrieval-record-unchanged": json.dumps({"retrieval_record": {"title": "Stable"}}),
            }

            receipt = {"component_runs": [{"run_id": "run-intake-test-01"}]}
            scoped_added, scoped_removed = _scoped_release_delta(
                temp_root, receipt, baseline, candidate
            )

            # Phantom deletion and phantom addition are filtered out;
            # genuine added assertion and its dependent entity facet are preserved.
            self.assertEqual([expected_add_rid, facet_add_rid], scoped_added)
            self.assertEqual([expected_rem_rid], scoped_removed)

    def test_evaluate_case_uses_cached_query_encoding(self) -> None:
        captured: dict[str, Any] = {}

        class MockRetriever:
            def retrieve_lane(
                self, question: str, lane: str, **kwargs: Any
            ) -> dict[str, Any]:
                captured["encoded_query"] = kwargs.get("encoded_query")
                return {
                    "results": [
                        {
                            "record_id": "record-1",
                            "fused_rank": 1,
                            "dense_rank": 1,
                            "sparse_rank": 1,
                            "colbert_rank": 1,
                        }
                    ],
                    "components": {"bm25": True},
                }

        case = {
            "query": "cached query text",
            "lane": "approved",
            "query_kind": "semantic_paraphrase",
            "classification": "internal",
            "audiences": ["internal_consulting"],
            "top_k": 5,
            "retrieval_mode": "factual",
        }
        test_encoded = {"dense": [0.1, 0.2], "sparse": {"1": 0.5}, "colbert": [[0.1, 0.2]]}
        retrieved_ids, rank_diag, components, latency = _evaluate_case(
            MockRetriever(), case, encoded_query=test_encoded  # type: ignore[arg-type]
        )
        self.assertEqual(["record-1"], retrieved_ids)
        self.assertIs(captured["encoded_query"], test_encoded)

    def test_delta_coverage_rejects_an_underpowered_case_mix(self) -> None:
        suite = {
            "cases": [
                {
                    "case_id": "only-one-paraphrase",
                    "scope": "delta",
                    "query_kind": "semantic_paraphrase",
                    "query": "What does the added record establish?",
                    "lane": "approved",
                    "classification": "internal",
                    "audiences": ["internal_consulting"],
                    "expected_record_ids": ["record-added"],
                    "forbidden_record_ids": [],
                }
            ],
            "coverage": [
                {
                    "record_id": "record-added",
                    "change_kind": "added_or_changed",
                    "case_ids": ["only-one-paraphrase"],
                    "relationship_applicable": False,
                    "stale_or_superseded_applicable": False,
                }
            ],
        }
        coverage = RetrievalReleaseEvaluator._coverage(
            suite,
            {},
            {"record-added": _retrieval_projection()},
            read_json(RETRIEVAL_RELEASE_POLICY_PATH),
        )
        self.assertFalse(coverage["complete"])

    def test_policy_loader_rejects_relaxed_release_criteria(self) -> None:
        current = read_json(RETRIEVAL_RELEASE_POLICY_PATH)
        relaxations = [
            ("recall", ("thresholds", "min_core_recall_at_k"), 0.99),
            ("rank", ("thresholds", "max_rank_regression"), 1),
            ("latency", ("thresholds", "max_latency_ms"), 7501),
            (
                "delta-case-count",
                (
                    "delta_case_requirements",
                    "added_or_changed",
                    "semantic_paraphrase",
                ),
                2,
            ),
        ]
        for label, keys, relaxed_value in relaxations:
            with self.subTest(label=label):
                policy = deepcopy(current)
                target = policy
                for key in keys[:-1]:
                    target = target[key]
                target[keys[-1]] = relaxed_value
                with patch(
                    "dual_pipeline.retrieval_release.read_json",
                    return_value=policy,
                ), self.assertRaises(ValueError):
                    _load_policy()

    def test_delta_coverage_enforces_required_blind_and_boundary_mix(self) -> None:
        kinds = [
            "semantic_paraphrase",
            "semantic_paraphrase",
            "semantic_paraphrase",
            "alias",
            "hard_negative",
            "lane_isolation",
            "acl_boundary",
        ]
        cases = []
        for index, query_kind in enumerate(kinds, 1):
            positive = query_kind in {"semantic_paraphrase", "alias"}
            lane = "candidate" if query_kind == "lane_isolation" else "approved"
            audiences = (
                ["external_public"]
                if query_kind == "acl_boundary"
                else ["internal_consulting"]
            )
            cases.append(
                {
                    "case_id": f"delta-required-{index}",
                    "scope": "delta",
                    "query_kind": query_kind,
                    "query": f"Distinct {query_kind} query {index}",
                    "lane": lane,
                    "classification": "internal",
                    "audiences": audiences,
                    "expected_record_ids": ["record-added"] if positive else [],
                    "forbidden_record_ids": [] if positive else ["record-added"],
                }
            )
        suite = {
            "cases": cases,
            "coverage": [
                {
                    "record_id": "record-added",
                    "change_kind": "added_or_changed",
                    "case_ids": [case["case_id"] for case in cases],
                    "relationship_applicable": False,
                    "stale_or_superseded_applicable": False,
                }
            ],
        }
        coverage = RetrievalReleaseEvaluator._coverage(
            suite,
            {},
            {"record-added": _retrieval_projection()},
            read_json(RETRIEVAL_RELEASE_POLICY_PATH),
        )
        self.assertTrue(coverage["complete"])
        cases_by_kind = {case["query_kind"]: case for case in cases}
        invalid_variants = [
            (cases_by_kind["hard_negative"], "lane", "candidate"),
            (cases_by_kind["lane_isolation"], "lane", "approved"),
            (
                cases_by_kind["lane_isolation"],
                "audiences",
                ["external_public"],
            ),
            (
                cases_by_kind["acl_boundary"],
                "audiences",
                ["internal_consulting"],
            ),
            (cases_by_kind["acl_boundary"], "lane", "candidate"),
        ]
        for case, field, invalid_value in invalid_variants:
            with self.subTest(query_kind=case["query_kind"], field=field):
                original_value = case[field]
                case[field] = invalid_value
                invalid_coverage = RetrievalReleaseEvaluator._coverage(
                    suite,
                    {},
                    {"record-added": _retrieval_projection()},
                    read_json(RETRIEVAL_RELEASE_POLICY_PATH),
                )
                self.assertFalse(invalid_coverage["complete"])
                case[field] = original_value

        paraphrases = [
            case for case in cases if case["query_kind"] == "semantic_paraphrase"
        ]
        original_queries = [case["query"] for case in paraphrases]
        for case in paraphrases:
            case["query"] = "Duplicate semantic paraphrase"
        duplicate_coverage = RetrievalReleaseEvaluator._coverage(
            suite,
            {},
            {"record-added": _retrieval_projection()},
            read_json(RETRIEVAL_RELEASE_POLICY_PATH),
        )
        self.assertFalse(duplicate_coverage["complete"])
        for case, original_query in zip(paraphrases, original_queries, strict=True):
            case["query"] = original_query

        relationship_coverage = RetrievalReleaseEvaluator._coverage(
            suite,
            {},
            {"record-added": _retrieval_projection(record_type="edge")},
            read_json(RETRIEVAL_RELEASE_POLICY_PATH),
        )
        self.assertFalse(relationship_coverage["complete"])

    def test_metadata_only_backend_cannot_qualify_a_bge_pair(self) -> None:
        pair = {
            "embedding_backend": "flagembedding-bge-m3-dev",
            "model_id": "BAAI/bge-m3",
            "model_revision": LocalBgeM3Backend.model_revision,
            "model_manifest_digest": LocalBgeM3Backend.model_manifest_digest,
            "production_qualified": False,
        }
        with self.assertRaisesRegex(PermissionError, "real BGE-M3 encoder"):
            _assert_real_bge_backend(pair, MetadataOnlyBgeM3Backend())

    def test_production_activation_authorization_binds_evaluation_digest(self) -> None:
        manifest_digest = "c" * 64
        receipt = _seal_receipt(
            {
                "production_qualified": True,
                "previous_pair": {
                    "generation_id": "generation-previous",
                    "production_qualified": True,
                },
                "next_pair": {
                    "generation_id": "generation-next",
                    "model_manifest_digest": manifest_digest,
                    "production_qualified": True,
                },
            }
        )
        resources = release_action_resources(receipt, "activate")
        now = int(time.time())

        def context(values: dict[str, str]) -> AuthorizationContext:
            return AuthorizationContext(
                subject="retrieval-release-test",
                issuer="test-issuer",
                relying_party="test-audience",
                token_id="retrieval-release-token",
                issued_at=now - 10,
                expires_at=now + 600,
                classification_clearance="restricted",
                audiences=("internal_consulting",),
                capabilities=("knowledge:release",),
                authorized_resources=values,
                approved_model_manifest_digest=manifest_digest,
                token_digest="a" * 64,
                context_digest="b" * 64,
            )

        with self.assertRaisesRegex(PermissionError, "requested resources"):
            ReleaseManager._require_receipt_action_authorization(
                receipt,
                context(resources),
                action="activate",
                additional_resources={"retrieval_release_evaluation": "e" * 64},
            )
        ReleaseManager._require_receipt_action_authorization(
            receipt,
            context({**resources, "retrieval_release_evaluation": "e" * 64}),
            action="activate",
            additional_resources={"retrieval_release_evaluation": "e" * 64},
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
