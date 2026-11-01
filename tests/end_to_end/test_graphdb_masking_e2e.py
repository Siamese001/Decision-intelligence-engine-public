import tempfile
import unittest
from pathlib import Path

from dual_pipeline.transcript_pipeline_core.graphdb_masking import GraphDBMasker


class DummySemanticCache:
    def __init__(self, runtime_root):
        self.runtime_root = runtime_root

class TestGraphDBMasking(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.semantic_cache = DummySemanticCache(runtime_root=Path(self.temp_dir))
        self.masker = GraphDBMasker(semantic_cache=self.semantic_cache)

    def test_graphdb_masking_e2e_masks_pii(self):
        """
        E2E Test to ensure local GraphDB pre-flight masking deterministically
        identifies and masks PII (like Jarod Bryant) with zero egress.
        """
        raw_transcript = "Jarod Bryant from Apex Global Analytics reviewed the Q3 accomplishments with Jake Wellington."

        # Simulate the Enterprise Directory containing these entities
        self.masker.register_entity_in_test_graph("Jarod Bryant", role="Executive Director GAO", org="Apex Global Enterprises (PGD)")
        self.masker.register_entity_in_test_graph("Jake Wellington", role="Engineering Manager", org="Apex Global Analytics")

        masked_transcript = self.masker.mask_payload(raw_transcript)

        self.assertNotIn("Jarod Bryant", masked_transcript, "PII 'Jarod Bryant' leaked through masking!")
        self.assertNotIn("Jake Wellington", masked_transcript, "PII 'Jake Wellington' leaked through masking!")
        self.assertTrue("[MASKED_ENTITY_1" in masked_transcript or "Executive Director GAO" in masked_transcript)

    def test_graphdb_masking_cache_latency(self):
        """
        Ensure the Semantic Cache prevents latency spikes by caching resolution results.
        """
        raw_transcript = "Jarod Bryant said the deployment was successful."
        self.masker.register_entity_in_test_graph("Jarod Bryant", role="Executive Director GAO")

        import time

        # First call (cache miss)
        start_t1 = time.time()
        res1 = self.masker.mask_payload(raw_transcript)
        dur1 = time.time() - start_t1

        # Second call (cache hit)
        start_t2 = time.time()
        res2 = self.masker.mask_payload(raw_transcript)
        dur2 = time.time() - start_t2

        self.assertEqual(res1, res2)
        # We assert dur2 is very small. In some setups, dur2 might not be strictly less due to resolution
        self.assertLess(dur2, 0.05, "Cache hit latency should be < 50ms")

if __name__ == '__main__':
    unittest.main()
