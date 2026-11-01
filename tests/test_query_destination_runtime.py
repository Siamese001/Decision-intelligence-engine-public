from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.query import QueryEngine
from dual_pipeline.release import ReleaseManager
from tests.embedding_fixture import BgeM3TestBackend

ROOT = Path(__file__).resolve().parents[1]


class CountingEmbeddingBackend(BgeM3TestBackend):
    def __init__(self) -> None:
        self.encode_count = 0

    def encode(self, text: str):
        self.encode_count += 1
        return super().encode(text)


class NeverProviderFactory:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, model_policy):
        del model_policy
        self.calls += 1
        raise AssertionError("external provider must not initialize for a routed-out request")


class QueryDestinationRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory(prefix="nee-query-destination-")
        cls.runtime = Path(cls.temp.name) / "runtime"
        cls.graph = ROOT / "graph" / "nee_graph.sqlite"
        cls.backend = CountingEmbeddingBackend()
        ReleaseManager(cls.runtime, cls.graph).bootstrap(cls.backend)
        cls.backend.encode_count = 0

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def test_general_summary_stops_before_embeddings_and_l2(self) -> None:
        provider = NeverProviderFactory()
        result = QueryEngine(
            self.runtime,
            self.graph,
            self.backend,
            provider_factory=provider,
        ).ask("Summarize these meeting notes into actions.")
        self.assertEqual("ROUTED", result["status"])
        self.assertEqual("GENERAL_ASSISTANT_HANDOFF", result["destination"])
        self.assertEqual("HANDOFF_REQUIRED", result["terminal_outcome"])
        self.assertEqual(0, result["embedding_call_count"])
        self.assertEqual(0, result["total_external_model_call_count"])
        self.assertEqual(0, result["automatic_egress_count"])
        self.assertEqual(0, provider.calls)
        self.assertEqual(0, self.backend.encode_count)
        artifact = (
            self.runtime
            / "runs"
            / result["run_id"]
            / "query_destination_decision.json"
        )
        validate_artifact(json.loads(artifact.read_text(encoding="utf-8")))

    def test_ambiguous_request_blocks_without_embedding_or_provider(self) -> None:
        provider = NeverProviderFactory()
        result = QueryEngine(
            self.runtime,
            self.graph,
            self.backend,
            provider_factory=provider,
        ).ask("Tell me about Chakra runtime ambiguity.")
        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("ROUTE_AMBIGUOUS", result["destination"])
        self.assertEqual(0, result["total_external_model_call_count"])
        self.assertEqual(0, provider.calls)
        self.assertEqual(0, self.backend.encode_count)


if __name__ == "__main__":
    unittest.main()
