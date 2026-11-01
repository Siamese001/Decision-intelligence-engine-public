#!/usr/bin/env python3
"""Focused contracts for immutable, answer-use-bound retrieval metadata."""
from __future__ import annotations

import copy
import json
import sqlite3
import struct
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from dual_pipeline.contracts import validate_artifact  # noqa: E402
from dual_pipeline.core import digest, seal_artifact  # noqa: E402
from dual_pipeline.index import (  # noqa: E402
    DENSE_DIMENSIONS,
    build_sidecar,
    resolve_retrieval_mode,
    retrieval_metadata_for_record,
    validate_sidecar,
)
from tests.embedding_fixture import BgeM3TestBackend  # noqa: E402

FIXTURE = (
    ROOT
    / "contracts"
    / "dual_pipeline"
    / "fixtures"
    / "valid"
    / "retrieval-record-candidate-action.json"
)

LEGACY_SIDECAR_SCHEMA = """
CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE retrieval_record (
    record_id TEXT PRIMARY KEY,
    lane TEXT NOT NULL CHECK (lane IN ('approved','candidate','external_public')),
    record_type TEXT NOT NULL,
    namespace TEXT NOT NULL,
    retrieval_text_hash TEXT NOT NULL,
    artifact_json TEXT NOT NULL CHECK (json_valid(artifact_json))
) WITHOUT ROWID;
CREATE TABLE dense_vector (
    record_id TEXT PRIMARY KEY REFERENCES retrieval_record(record_id) ON DELETE CASCADE,
    dimensions INTEGER NOT NULL CHECK (dimensions=1024),
    vector BLOB NOT NULL
) WITHOUT ROWID;
CREATE TABLE sparse_weight (
    record_id TEXT NOT NULL REFERENCES retrieval_record(record_id) ON DELETE CASCADE,
    token TEXT NOT NULL,
    weight REAL NOT NULL,
    PRIMARY KEY (record_id,token)
) WITHOUT ROWID;
CREATE TABLE colbert_token (
    record_id TEXT NOT NULL REFERENCES retrieval_record(record_id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    token TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    vector BLOB NOT NULL,
    PRIMARY KEY (record_id,position)
) WITHOUT ROWID;
"""


def _load_fixture() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _pack(values: list[float]) -> bytes:
    return struct.pack(f"<{len(values)}f", *values)


class RetrievalMetadataTests(unittest.TestCase):
    def test_metadata_is_complete_for_new_artifacts_and_optional_for_legacy(self) -> None:
        current = _load_fixture()
        validate_artifact(current)

        legacy = copy.deepcopy(current)
        for field in (
            "retrieval_kind",
            "epistemic_status",
            "provenance_kind",
            "answer_use",
        ):
            legacy.pop(field)
        validate_artifact(seal_artifact(legacy))

        incomplete = copy.deepcopy(legacy)
        incomplete["retrieval_kind"] = "guidance"
        with self.assertRaisesRegex(ValueError, "RETRIEVAL_METADATA_INCOMPLETE"):
            validate_artifact(seal_artifact(incomplete))

    def test_current_record_type_mapping_is_orthogonal(self) -> None:
        self.assertEqual(
            {
                "retrieval_kind": "assertion",
                "epistemic_status": "approved",
                "provenance_kind": "source_bound",
                "answer_use": "factual_answer",
            },
            retrieval_metadata_for_record("assertion", "approved"),
        )
        self.assertEqual(
            "relationship_assertion",
            retrieval_metadata_for_record("edge", "candidate")["retrieval_kind"],
        )
        self.assertEqual(
            "candidate_hypothesis",
            retrieval_metadata_for_record("architecture_relationship", "candidate")["answer_use"],
        )
        self.assertEqual(
            "entity_facet",
            retrieval_metadata_for_record("architecture_component", "approved")["retrieval_kind"],
        )
        self.assertEqual(
            "guidance",
            retrieval_metadata_for_record("action_item", "candidate")["answer_use"],
        )
        self.assertEqual(
            "citation_only",
            retrieval_metadata_for_record("source_chunk", "candidate")["answer_use"],
        )

    def test_fixed_retrieval_modes_are_lane_and_answer_use_bound(self) -> None:
        name, policy = resolve_retrieval_mode(["approved"], "factual")
        self.assertEqual("factual", name)
        self.assertEqual(frozenset({"factual_answer"}), policy["approved"])

        default_name, _ = resolve_retrieval_mode(["approved"], None)
        self.assertEqual("factual", default_name)

        name, policy = resolve_retrieval_mode(["approved"], "guidance")
        self.assertEqual("guidance", name)
        self.assertEqual(
            frozenset({"factual_answer", "guidance"}), policy["approved"]
        )

        name, policy = resolve_retrieval_mode(
            ["approved", "candidate"], "candidate_review"
        )
        self.assertEqual("candidate_review", name)
        self.assertEqual(
            frozenset({"candidate_hypothesis", "citation_only"}),
            policy["candidate"],
        )

        name, policy = resolve_retrieval_mode(
            ["approved", "candidate", "external_public"],
            "strategic",
        )
        self.assertEqual("strategic", name)
        self.assertIn("strategic_context", policy["approved"])
        self.assertIn("decision_constraint", policy["approved"])
        self.assertIn("external_analogy", policy["external_public"])

        name, policy = resolve_retrieval_mode(
            ["candidate"], "candidate_guidance"
        )
        self.assertEqual("candidate_guidance", name)
        self.assertEqual(frozenset({"guidance"}), policy["candidate"])
        with self.assertRaisesRegex(ValueError, "requires lanes"):
            resolve_retrieval_mode(["candidate"], "factual")
        with self.assertRaisesRegex(ValueError, "retrieval_mode is required"):
            resolve_retrieval_mode(["external_public"], None)

    def test_new_sidecar_binds_metadata_and_legacy_sidecar_still_validates(self) -> None:
        backend = BgeM3TestBackend()
        with tempfile.TemporaryDirectory(prefix="nee-retrieval-metadata-") as directory:
            root = Path(directory)
            sidecar = root / "metadata.sqlite"
            build_sidecar(
                graph_path=ROOT / "graph" / "nee_graph.sqlite",
                sidecar_path=sidecar,
                generation_id="generation-retrieval-metadata-test",
                graph_snapshot="graph-snapshot-retrieval-metadata-test",
                backend=backend,
            )
            validation = validate_sidecar(sidecar)
            self.assertTrue(validation["retrieval_metadata_columns_present"])
            self.assertEqual(0, validation["retrieval_metadata_binding_contamination_count"])
            connection = sqlite3.connect(sidecar)
            try:
                row = connection.execute(
                    """SELECT retrieval_kind,epistemic_status,provenance_kind,
                              answer_use,artifact_json
                       FROM retrieval_record ORDER BY record_id LIMIT 1"""
                ).fetchone()
                assert row is not None
                artifact = json.loads(row[4])
                self.assertEqual(
                    tuple(artifact[field] for field in (
                        "retrieval_kind",
                        "epistemic_status",
                        "provenance_kind",
                        "answer_use",
                    )),
                    row[:4],
                )
                connection.execute(
                    "UPDATE retrieval_record SET answer_use='guidance' WHERE record_id=?",
                    (artifact["record_id"],),
                )
                connection.commit()
            finally:
                connection.close()
            with self.assertRaisesRegex(RuntimeError, "metadata manifest binding mismatch"):
                validate_sidecar(sidecar)
            connection = sqlite3.connect(sidecar)
            try:
                metadata_rows = [
                    {
                        "record_id": item[0],
                        "retrieval_kind": item[1],
                        "epistemic_status": item[2],
                        "provenance_kind": item[3],
                        "answer_use": item[4],
                    }
                    for item in connection.execute(
                        """SELECT record_id,retrieval_kind,epistemic_status,
                                  provenance_kind,answer_use
                           FROM retrieval_record ORDER BY record_id"""
                    )
                ]
                connection.execute(
                    "UPDATE metadata SET value=? WHERE key='retrieval_metadata_manifest_digest'",
                    (digest(metadata_rows),),
                )
                connection.commit()
            finally:
                connection.close()
            with self.assertRaisesRegex(RuntimeError, "metadata binding contamination"):
                validate_sidecar(sidecar)

            legacy_sidecar = root / "legacy.sqlite"
            self._write_legacy_sidecar(legacy_sidecar, backend)
            legacy_validation = validate_sidecar(legacy_sidecar)
            self.assertFalse(legacy_validation["retrieval_metadata_columns_present"])

    def _write_legacy_sidecar(self, path: Path, backend: BgeM3TestBackend) -> None:
        artifact = _load_fixture()
        for field in (
            "retrieval_kind",
            "epistemic_status",
            "provenance_kind",
            "answer_use",
        ):
            artifact.pop(field)
        artifact = seal_artifact(artifact)
        encoded = backend.encode(str(artifact["retrieval_text"]))
        connection = sqlite3.connect(path)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.executescript(LEGACY_SIDECAR_SCHEMA)
            connection.execute(
                "INSERT INTO retrieval_record VALUES (?,?,?,?,?,?)",
                (
                    artifact["record_id"],
                    artifact["lane"],
                    artifact["record_type"],
                    artifact["namespace"],
                    artifact["retrieval_text_hash"],
                    json.dumps(artifact, ensure_ascii=False, sort_keys=True),
                ),
            )
            connection.execute(
                "INSERT INTO dense_vector VALUES (?,?,?)",
                (artifact["record_id"], DENSE_DIMENSIONS, _pack(encoded["dense"])),
            )
            connection.executemany(
                "INSERT INTO sparse_weight VALUES (?,?,?)",
                [
                    (artifact["record_id"], token, float(weight))
                    for token, weight in sorted(encoded["sparse"].items())
                ],
            )
            connection.executemany(
                "INSERT INTO colbert_token VALUES (?,?,?,?,?)",
                [
                    (
                        artifact["record_id"],
                        position,
                        encoded["colbert_tokens"][position],
                        len(vector),
                        _pack(vector),
                    )
                    for position, vector in enumerate(encoded["colbert"])
                ],
            )
            manifest_rows = [
                {
                    "record_id": artifact["record_id"],
                    "lane": artifact["lane"],
                    "retrieval_text_hash": artifact["retrieval_text_hash"],
                }
            ]
            metadata = {
                "schema_version": "1.0.0",
                "generation_id": "legacy-generation",
                "graph_snapshot": "legacy-graph-snapshot",
                "graph_sha256": "0" * 64,
                "model_id": backend.model_id,
                "model_revision": backend.model_revision,
                "model_manifest_digest": "not-applicable",
                "embedding_backend": backend.backend_id,
                "production_qualified": "false",
                "dense_dimensions": str(DENSE_DIMENSIONS),
                "colbert_dimensions": str(backend.colbert_dimensions),
                "record_count": "1",
                "approved_record_count": "0",
                "candidate_record_count": "1",
                "external_public_record_count": "0",
                "retrieval_text_manifest_digest": digest(manifest_rows),
                "retrieval_configuration_digest": digest(
                    {
                        "dense_weight": 0.4,
                        "sparse_weight": 0.2,
                        "colbert_weight": 0.4,
                        "rrf_k": 60,
                        "lane_blending": False,
                    }
                ),
                "built_at": "2026-07-25T00:00:00Z",
            }
            connection.executemany("INSERT INTO metadata VALUES (?,?)", sorted(metadata.items()))
            connection.commit()
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
