"""Adversarial and boundary tests for semantic unit policy validation and tamper-resistance."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

import jsonschema

from dual_pipeline.index_core.policy import (
    DEFAULT_POLICY_PATH,
    DEFAULT_SCHEMA_PATH,
    load_semantic_unit_policy,
    verify_policy_ledger_alignment,
)


class TestSemanticUnitPolicyAdversarial(unittest.TestCase):
    def setUp(self):
        with open(DEFAULT_POLICY_PATH, encoding="utf-8") as f:
            self.valid_policy = json.load(f)
        with open(DEFAULT_SCHEMA_PATH, encoding="utf-8") as f:
            self.schema = json.load(f)

    def test_01_tampered_canonical_digest_rejected(self):
        tampered = copy.deepcopy(self.valid_policy)
        tampered["canonical_digest"]["value"] = "a" * 64

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            json.dump(tampered, tf)
            tf_path = Path(tf.name)

        try:
            with self.assertRaises(ValueError) as ctx:
                load_semantic_unit_policy(tf_path)
            self.assertIn("canonical digest mismatch", str(ctx.exception))
        finally:
            tf_path.unlink(missing_ok=True)

    def test_02_unknown_field_rejected_by_schema(self):
        tampered = copy.deepcopy(self.valid_policy)
        tampered["unauthorized_autonomous_mutation"] = True

        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(instance=tampered, schema=self.schema)

    def test_03_missing_decision_in_ledger_fails_alignment(self):
        tampered = copy.deepcopy(self.valid_policy)
        tampered["ledger_reference"]["decisions"].append({
            "decision_id": "op-unauthorized-fake-decision",
            "action": "APPROVE",
            "selected_option": "fake_option",
            "signature": "b" * 64,
        })

        aligned, err = verify_policy_ledger_alignment(tampered)
        self.assertFalse(aligned)
        self.assertIn("missing from ledger", err)

    def test_04_tampered_decision_signature_fails_alignment(self):
        tampered = copy.deepcopy(self.valid_policy)
        tampered["ledger_reference"]["decisions"][0]["signature"] = "c" * 64

        aligned, err = verify_policy_ledger_alignment(tampered)
        self.assertFalse(aligned)
        self.assertIn("signature mismatch", err)

    def test_05_tampered_decision_option_fails_alignment(self):
        tampered = copy.deepcopy(self.valid_policy)
        tampered["ledger_reference"]["decisions"][0]["selected_option"] = "unauthorized_option_override"

        aligned, err = verify_policy_ledger_alignment(tampered)
        self.assertFalse(aligned)
        self.assertIn("option mismatch", err)

    def test_06_nonexistent_policy_file_fails_closed(self):
        with self.assertRaises(FileNotFoundError):
            load_semantic_unit_policy(Path("/tmp/nonexistent_policy_12345.json"))


if __name__ == "__main__":
    unittest.main()
