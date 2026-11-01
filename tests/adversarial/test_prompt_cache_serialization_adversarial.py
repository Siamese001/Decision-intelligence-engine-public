"""Adversarial and boundary test suite for prompt serialization determinism and prefix preservation."""
from __future__ import annotations

import hashlib
import json
import random
import string
import unittest

from dual_pipeline.batch_core.adapter import format_batch_jsonl
from dual_pipeline.token_optimization.profiler import TokenProfiler
from dual_pipeline.token_optimization.schemas import PromptComponents


class TestPromptCacheSerializationAdversarial(unittest.TestCase):
    def test_adv_deeply_nested_dict_key_permutations(self):
        """Boundary Test: Deeply nested dictionary permutations must produce identical SHA-256."""
        def make_nested(keys):
            res = {}
            for k in keys:
                res[k] = {
                    "sub_k": [1, 2, {"inner_b": "val_b", "inner_a": "val_a"}],
                    "flag": True,
                    "count": 42,
                }
            return res

        base_keys = ["delta", "alpha", "gamma", "beta", "epsilon"]
        hashes = set()

        for _ in range(20):
            shuffled = list(base_keys)
            random.shuffle(shuffled)
            d = make_nested(shuffled)
            s = TokenProfiler.compact_json(d)
            h = hashlib.sha256(s.encode("utf-8")).hexdigest()
            hashes.add(h)

        self.assertEqual(len(hashes), 1, "All permutations of keys must yield the exact same SHA-256 hash")

    def test_adv_massive_dict_sorting_stress(self):
        """Overflow/Stress Test: Large dictionary with 1000 randomized keys sorts deterministically."""
        random.seed(42)
        random_keys = [
            "".join(random.choices(string.ascii_letters, k=8)) for _ in range(1000)
        ]

        d1 = {k: idx for idx, k in enumerate(random_keys)}
        reversed_keys = list(reversed(random_keys))
        d2 = {k: d1[k] for k in reversed_keys}

        s1 = TokenProfiler.compact_json(d1)
        s2 = TokenProfiler.compact_json(d2)

        self.assertEqual(s1, s2)
        # Verify that parsed keys are strictly sorted
        parsed = json.loads(s1)
        self.assertEqual(list(parsed.keys()), sorted(list(parsed.keys())))

    def test_adv_prompt_components_empty_and_whitespace_boundaries(self):
        """Boundary Test: PromptComponents with empty strings, None, and whitespace boundaries."""
        # Case 1: Pure whitespace
        c1 = PromptComponents(
            static_system_prefix="   \n\t  ",
            dynamic_system_suffix="   ",
            user_payload="   \n  ",
        )
        msgs1 = c1.build_messages()
        self.assertEqual(msgs1, [])

        # Case 2: Only dynamic suffix, no static prefix
        c2 = PromptComponents(
            static_system_prefix="",
            dynamic_system_suffix="Dynamic Info Only",
            user_payload="User Question",
        )
        msgs2 = c2.build_messages()
        self.assertEqual(len(msgs2), 1)
        self.assertEqual(msgs2[0]["role"], "user")
        self.assertIn("Dynamic Info Only", msgs2[0]["content"])
        self.assertIn("User Question", msgs2[0]["content"])

        # Case 3: Only static prefix
        c3 = PromptComponents(
            static_system_prefix="Static System Instructions",
            dynamic_system_suffix="",
            user_payload="",
        )
        msgs3 = c3.build_messages()
        self.assertEqual(len(msgs3), 1)
        self.assertEqual(msgs3[0]["role"], "system")
        self.assertEqual(msgs3[0]["content"], "Static System Instructions")

    def test_adv_batch_jsonl_empty_and_unicode_boundary(self):
        """Boundary Test: Batch JSONL formatting with unicode, special escapes, and empty list."""
        self.assertEqual(format_batch_jsonl([]), "")

        req = {
            "custom_id": "req-1",
            "body": {
                "text": "Meeting: Über-Strategic AI \u2014 Café & Résumé 🎯",
                "nested": {"z": "\n\t", "a": 100},
            },
        }
        jsonl = format_batch_jsonl([req])
        self.assertTrue(jsonl.endswith("\n"))
        self.assertIn("Über-Strategic AI", jsonl)
        # Verify keys are sorted inside jsonl line
        line = jsonl.strip()
        parsed = json.loads(line)
        self.assertEqual(list(parsed["body"]["nested"].keys()), ["a", "z"])


if __name__ == "__main__":
    unittest.main()
