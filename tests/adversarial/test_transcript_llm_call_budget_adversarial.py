import unittest


class TranscriptLLMCallBudgetAdversarialTest(unittest.TestCase):
    @unittest.expectedFailure
    def test_baseline_llm_call_budget_adversarial(self):
        # We expect the current pipeline to make more calls than the future budget allows.
        self.fail("LLM budget currently over limit (baseline)")

if __name__ == "__main__":
    unittest.main()
