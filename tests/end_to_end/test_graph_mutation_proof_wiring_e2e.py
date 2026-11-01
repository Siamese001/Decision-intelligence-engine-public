"""Wave 2 E2E: a real GovernedGraphWriter.apply returns and stores a passing physical proof."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import tests.end_to_end.test_wave1_mutation_hardening_e2e as _wave1_fixture
from dual_pipeline.core import sha256_file
from dual_pipeline.graph_writer import GovernedGraphWriter
from tools.graph_mutation_proof_core import raw_byte_scan

TARGET = "as-wave2-e2e-01"


@pytest.fixture()
def fixture_case():
    case = _wave1_fixture.Wave1MutationHardeningE2ETests("test_e2e_mutation_wal_checkpoint_and_hash_change")
    case.setUp()
    try:
        yield case
    finally:
        case.tearDown()


@pytest.mark.e2e
def test_writer_apply_returns_passing_physical_proof(fixture_case):
    graph = fixture_case.graph_path
    result = GovernedGraphWriter(graph).apply(fixture_case._make_change_set("op-wave2-01", TARGET))

    proof = result["physical_proof"]
    assert proof["overall"] == "PASS"
    assert set(proof["checks"]) == {"file_changed", "raw_bytes", "integrity", "readback", "row_delta"}
    assert all(status == "PASS" for status in proof["checks"].values())

    # Independent verification path: read the receipt file and the raw database bytes ourselves.
    receipt_path = Path(proof["receipt_path"])
    assert receipt_path.parent.resolve() == (graph.parent / "graph_mutation_proofs").resolve()
    receipt = json.loads(receipt_path.read_text())
    file_check = next(c for c in receipt["checks"] if c["check"] == "file_changed")
    assert file_check["detail"]["after_sha256"] == sha256_file(graph) == result["graph_sha256_after"]
    assert file_check["detail"]["before_sha256"] == result["graph_sha256_before"]
    assert raw_byte_scan(graph, [TARGET])[TARGET] is not None
    assert receipt["label"].startswith("graph-writer:")
    assert receipt["record_ids"] == [TARGET]
