"""Test suite for direct programmatic UWG submission and Pipeline C intake."""
from __future__ import annotations

from dual_pipeline.l0.contracts import (
    INTERNAL_ADVISORY_NAMESPACE,
    CanonicalGraphDelta,
    CanonicalGraphNode,
    GraphEdge,
)
from pipeline_b_extraction.uwg_gateway import ProgrammaticUwgGateway
from pipeline_c_retrieval.uwg_intake import UwgIntakeProcessor


def test_programmatic_uwg_submission_accepted():
    """Verifies that a valid delta is accepted directly in-memory without spawning subprocesses."""
    gateway = ProgrammaticUwgGateway(target_generation_id="gen_002")
    delta = CanonicalGraphDelta(
        delta_id="delta_test_001",
        source_evidence_ids=["ev_100"],
        nodes_created=[
            CanonicalGraphNode(
                node_id="node_auth",
                evidence_id="ev_100",
                name="AWS IAM Governance",
                entity_type="SYSTEM",
            )
        ],
        edges_created=[],
    )

    receipt = gateway.submit_delta(delta)
    assert receipt.status == "ACCEPTED"
    assert receipt.nodes_admitted == 1
    assert receipt.digest != ""

    # Pipeline C intake admits the submission
    intake = UwgIntakeProcessor(target_generation="gen_002")
    res = intake.admit_delta_for_indexing(receipt, delta)
    assert res["admitted"] is True
    assert intake.get_admitted_count() == 1


def test_programmatic_uwg_submission_rejected_on_invariants():
    """Verifies that a delta violating C-19 (leaked advisory node) is rejected."""
    gateway = ProgrammaticUwgGateway()
    invalid_delta = CanonicalGraphDelta(
        delta_id="delta_leaked",
        source_evidence_ids=["ev_100"],
        nodes_created=[
            CanonicalGraphNode(
                node_id="node_leaked",
                evidence_id="ev_100",
                namespace=INTERNAL_ADVISORY_NAMESPACE,  # Violates C-19
                name="Confidential Advisory Item",
            )
        ],
    )

    receipt = gateway.submit_delta(invalid_delta)
    assert receipt.status == "REJECTED"
    assert len(receipt.rejection_reasons) > 0


def test_programmatic_uwg_submission_with_db_persistence():
    """Verifies that when db_manager is provided, nodes and edges are physically persisted."""
    class MockDbManager:
        def __init__(self):
            self.nodes = []
            self.edges = []

        def upsert_canonical_node(self, node):
            self.nodes.append(node)

        def insert_canonical_edge(self, edge):
            self.edges.append(edge)

    mock_db = MockDbManager()
    gateway = ProgrammaticUwgGateway(target_generation_id="gen_persist", db_manager=mock_db)
    delta = CanonicalGraphDelta(
        delta_id="delta_test_persist",
        source_evidence_ids=["ev_200"],
        nodes_created=[
            CanonicalGraphNode(
                node_id="node_db_persist",
                evidence_id="ev_200",
                name="Persisted DB Item",
                entity_type="SYSTEM",
            )
        ],
        edges_created=[
            GraphEdge(
                edge_id="edge_db_persist",
                source_node_id="node_db_persist",
                target_node_id="node_other",
                predicate="DEPENDS_ON",
                evidence_id="ev_200",
            )
        ],
    )

    receipt = gateway.submit_delta(delta)
    assert receipt.status == "ACCEPTED"
    assert receipt.nodes_admitted == 1
    assert receipt.edges_admitted == 1
    assert len(mock_db.nodes) == 1
    assert mock_db.nodes[0].node_id == "node_db_persist"
    assert len(mock_db.edges) == 1
    assert mock_db.edges[0].edge_id == "edge_db_persist"

