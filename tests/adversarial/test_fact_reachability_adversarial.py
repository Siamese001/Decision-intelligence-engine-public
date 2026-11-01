import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from dual_pipeline.core import connect_sqlite, seal_artifact, sha256_bytes
from dual_pipeline.index_core.constants import _pack
from dual_pipeline.index_core.differential import inspect_prior_sidecar
from dual_pipeline.index_core.embedding_space_contract import (
    EmbeddingSpaceMismatch,
    assert_query_vector_contract,
    validate_embedding_space_contract,
)
from dual_pipeline.index_core.sidecar import validate_sidecar
from dual_pipeline.index_core.v0_audit import verify_v0_fact_reachability

ROOT = Path(__file__).resolve().parent.parent.parent


def _create_minimal_sidecar(
    db_path: Path,
    *,
    record_ids: list[str],
    record_type: str = "assertion",
    vector_dim: int = 1024,
    set_unit_index_meta: bool = True,
    dense_id_override: str | None = None,
    corrupt_zero_norm: bool = False,
    corrupt_nan: bool = False,
) -> None:
    """Helper to create a fully formed sidecar SQLite database with optional defects."""
    # Load sample artifact template
    sample_sc = ROOT / ".local" / "dual_pipeline" / "generations" / "generation-6018bfb52eee82779373da629ce6" / "sidecar.sqlite"
    tmpl_conn = connect_sqlite(sample_sc, readonly=True)
    try:
        sample_json = tmpl_conn.execute("SELECT artifact_json FROM retrieval_record WHERE lane='approved' LIMIT 1").fetchone()[0]
        tmpl = json.loads(sample_json)
    finally:
        tmpl_conn.close()

    conn = connect_sqlite(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(
            """
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE retrieval_record (
                record_id TEXT PRIMARY KEY,
                lane TEXT NOT NULL,
                record_type TEXT NOT NULL,
                entity_id TEXT,
                retrieval_text TEXT NOT NULL,
                retrieval_text_hash TEXT NOT NULL,
                artifact_json TEXT NOT NULL
            );
            CREATE TABLE dense_vector (
                record_id TEXT PRIMARY KEY,
                dimensions INTEGER NOT NULL,
                vector BLOB NOT NULL
            );
            CREATE TABLE sparse_weight (
                record_id TEXT NOT NULL,
                token TEXT NOT NULL,
                weight REAL NOT NULL,
                PRIMARY KEY (record_id, token)
            );
            CREATE TABLE colbert_token (
                record_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                token TEXT NOT NULL,
                dimensions INTEGER NOT NULL,
                vector BLOB NOT NULL,
                PRIMARY KEY (record_id, position)
            );
            """
        )
        rng = np.random.RandomState(42)
        for i, rid in enumerate(record_ids):
            art = copy.deepcopy(tmpl)
            art["record_id"] = rid
            art["lane"] = "approved"
            art["record_type"] = record_type
            art["retrieval_text"] = f"Text for {rid}"
            art["retrieval_text_hash"] = sha256_bytes(art["retrieval_text"].encode("utf-8"))
            art.setdefault("extensions", {})["com.partner_advisory.nee.graph-object-id"] = rid
            art["extensions"]["com.partner_advisory.nee.graph-assertion-ids"] = [rid]
            art = seal_artifact(art)

            conn.execute(
                """
                INSERT INTO retrieval_record VALUES (?, 'approved', ?, NULL, ?, ?, ?)
                """,
                (
                    rid,
                    record_type,
                    art["retrieval_text"],
                    art["retrieval_text_hash"],
                    json.dumps(art),
                ),
            )
            v = rng.randn(vector_dim).astype(np.float32)
            v /= np.linalg.norm(v)
            if corrupt_zero_norm and i == 0:
                v = np.zeros(vector_dim, dtype=np.float32)
            elif corrupt_nan and i == 0:
                v[0] = np.nan

            dense_rid = dense_id_override if (dense_id_override and i == 0) else rid
            conn.execute(
                "INSERT INTO dense_vector VALUES (?, ?, ?)",
                (dense_rid, vector_dim, _pack(v)),
            )
            conn.execute(
                "INSERT INTO sparse_weight VALUES (?, 'token', 1.0)",
                (rid,),
            )
            c_vec = rng.randn(vector_dim).astype(np.float32)
            c_vec /= np.linalg.norm(c_vec)
            conn.execute(
                "INSERT INTO colbert_token VALUES (?, 0, 'token', ?, ?)",
                (rid, vector_dim, _pack(c_vec)),
            )

        meta = {
            "schema_version": "1.0.0",
            "generation_id": "gen-test",
            "graph_snapshot": "snap-test",
            "graph_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
            "record_count": str(len(record_ids)),
            "approved_record_count": str(len(record_ids)),
            "candidate_record_count": "0",
            "external_public_record_count": "0",
            "colbert_dimensions": str(vector_dim),
            "dense_dimensions": str(vector_dim),
            "embedding_backend": "bge-m3",
            "model_id": "BAAI/bge-m3",
            "model_revision": "5617a9f61b028005a4858fdac845db406aefb181",
        }
        if set_unit_index_meta:
            meta["semantic_unit_index"] = "true"
        for k, val in meta.items():
            conn.execute("INSERT INTO metadata VALUES (?, ?)", (k, val))
        conn.commit()
    finally:
        conn.close()


class TestFactReachabilityAdversarial(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.tmp_dir.name)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_case_1_adversarial_record_id_set_mismatch(self) -> None:
        """Case 1: Same row count but different record_id set must fail activation."""
        sc_path = self.work_dir / "sidecar_set_mismatch.sqlite"
        _create_minimal_sidecar(
            sc_path,
            record_ids=["rec-1", "rec-2"],
            dense_id_override="rec-mismatched",  # count is 2, but dense has rec-mismatched
        )
        with self.assertRaises(RuntimeError) as ctx:
            validate_sidecar(sc_path)
        self.assertIn("record_id set mismatch", str(ctx.exception))

    def test_case_2_adversarial_unreachable_fact_refuses_activation(self) -> None:
        """Case 2: A new current fact that is unreachable must refuse V0 activation."""
        graph_path = self.work_dir / "graph_unreachable.sqlite"
        sc_path = self.work_dir / "sidecar_valid.sqlite"
        _create_minimal_sidecar(sc_path, record_ids=["assertion:1"])

        conn = connect_sqlite(graph_path)
        try:
            conn.executescript(
                """
                CREATE TABLE assertion (
                    assertion_id TEXT PRIMARY KEY,
                    subject_node_id TEXT,
                    object_node_id TEXT,
                    predicate TEXT,
                    object_literal TEXT,
                    currentness_state TEXT,
                    canonical_status TEXT,
                    validation_state TEXT,
                    classification TEXT,
                    allowed_audiences_json TEXT,
                    assertion_type TEXT,
                    epistemic_status TEXT
                );
                CREATE TABLE assertion_evidence (
                    assertion_id TEXT,
                    evidence_id TEXT,
                    evidence_relationship TEXT
                );
                """
            )
            # Insert assertion:1 (directly embedded)
            conn.execute(
                """
                INSERT INTO assertion VALUES (
                    'assertion:1', 'node:A', NULL, 'CONFIRMED', 'literal value',
                    'current', 'canonical', 'approved', 'INTERNAL_ADVISORY', '["internal_consulting"]',
                    'direct_statement', 'factual'
                )
                """
            )
            # Insert assertion:unreachable (NOT in sidecar, supporting evidence NOT in sidecar, NOT policy-excluded)
            conn.execute(
                """
                INSERT INTO assertion VALUES (
                    'assertion:unreachable_orphan', 'node:Orphan', NULL, 'HAS_NOTE', 'orphan note literal',
                    'current', 'canonical', 'approved', 'INTERNAL_ADVISORY', '["internal_consulting"]',
                    'direct_statement', 'factual'
                )
                """
            )
            conn.execute(
                "INSERT INTO assertion_evidence VALUES ('assertion:unreachable_orphan', 'ev:not_in_sidecar', 'supports')"
            )
            conn.commit()
        finally:
            conn.close()

        v0 = verify_v0_fact_reachability(
            graph_path=graph_path,
            sidecar_path=sc_path,
            generation_id="gen-adversarial",
        )
        self.assertEqual(v0["status"], "FAIL")
        self.assertGreater(v0["unreachable_count"], 0)
        self.assertIn("assertion:unreachable_orphan", v0["unreachable_ids"])

    def test_case_3_adversarial_edge_records_reintroduced(self) -> None:
        """Case 3: Edge-type records reintroduced into the unit index must fail sidecar validation."""
        sc_path = self.work_dir / "sidecar_edge_reintroduced.sqlite"
        _create_minimal_sidecar(
            sc_path,
            record_ids=["edge:A:DEPENDS_ON:B"],
            record_type="edge",
            set_unit_index_meta=True,
        )
        with self.assertRaises(RuntimeError) as ctx:
            validate_sidecar(sc_path)
        self.assertIn("forbidden record_type", str(ctx.exception))

    def test_case_4_adversarial_reused_vectors_different_model_revision(self) -> None:
        """Case 4: Reused vectors from a different model revision must be refused."""
        sc_path = self.work_dir / "sidecar_outdated_rev.sqlite"
        _create_minimal_sidecar(sc_path, record_ids=["rec-1"])
        # Update metadata to have outdated revision
        conn = connect_sqlite(sc_path)
        try:
            conn.execute(
                "UPDATE metadata SET value = 'outdated-model-revision-999' WHERE key = 'model_revision'"
            )
            conn.commit()
        finally:
            conn.close()

        current_backend = SimpleNamespace(
            backend_id="bge-m3",
            model_id="BAAI/bge-m3",
            model_revision="5617a9f61b028005a4858fdac845db406aefb181",
        )
        eligible, hashes, colbert_dim = inspect_prior_sidecar(sc_path, current_backend)
        self.assertFalse(eligible, "Must refuse vector reuse from an outdated model revision!")

    def test_case_5_adversarial_zero_or_nan_vectors_refused(self) -> None:
        """Case 5: Zero or NaN dense vectors must refuse sidecar validation."""
        # 5a: Zero norm
        sc_zero = self.work_dir / "sidecar_zero.sqlite"
        _create_minimal_sidecar(sc_zero, record_ids=["rec-zero"], corrupt_zero_norm=True)
        with self.assertRaises(RuntimeError) as ctx:
            validate_sidecar(sc_zero)
        self.assertIn("zero", str(ctx.exception).lower())

        # 5b: NaN values
        sc_nan = self.work_dir / "sidecar_nan.sqlite"
        _create_minimal_sidecar(sc_nan, record_ids=["rec-nan"], corrupt_nan=True)
        with self.assertRaises(RuntimeError) as ctx:
            validate_sidecar(sc_nan)
        self.assertIn("nan", str(ctx.exception).lower())

    def test_case_6_adversarial_64d_query_vector_refused(self) -> None:
        """Case 6: A 64-d query vector compared against 1024-d units must raise EMBEDDING_SPACE_MISMATCH."""
        query_64 = np.ones(64, dtype=np.float32) / np.sqrt(64)
        with self.assertRaises(EmbeddingSpaceMismatch) as ctx:
            assert_query_vector_contract(query_64)
        self.assertIn("EMBEDDING_SPACE_MISMATCH", str(ctx.exception))
        self.assertIn("64", str(ctx.exception))

        sidecar_meta = {
            "embedding_backend": "bge-m3",
            "model_id": "BAAI/bge-m3",
            "model_revision": "5617a9f61b028005a4858fdac845db406aefb181",
            "dense_dimensions": 1024,
        }
        backend = SimpleNamespace(
            backend_id="bge-m3",
            model_id="BAAI/bge-m3",
            model_revision="5617a9f61b028005a4858fdac845db406aefb181",
        )
        with self.assertRaises(EmbeddingSpaceMismatch) as ctx2:
            validate_embedding_space_contract(
                backend=backend,
                sidecar_metadata=sidecar_meta,
                query_vector=query_64,
            )
        self.assertIn("EMBEDDING_SPACE_MISMATCH", str(ctx2.exception))


if __name__ == "__main__":
    unittest.main()
