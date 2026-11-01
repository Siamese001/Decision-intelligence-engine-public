"""Unit tests for Phase 5 — Evidence Stratification."""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "tools" / "validate_dual_pipeline_contracts.py"
SPEC = importlib.util.spec_from_file_location("dual_pipeline_contracts", TOOL_PATH)
assert SPEC and SPEC.loader
contracts = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = contracts
SPEC.loader.exec_module(contracts)

CONTRACT_ROOT = ROOT / "contracts" / "dual_pipeline"
VALID = CONTRACT_ROOT / "fixtures" / "valid"


def resign(value: dict) -> dict:
    value["canonical_digest"] = {
        "algorithm": "sha256",
        "scope": "artifact_without_canonical_digest",
        "value": contracts.canonical_artifact_digest(value),
    }
    return value


class TestEvidenceStratificationSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = contracts.SchemaRegistry(CONTRACT_ROOT / "schemas")
        cls.registry.load()
        with open(VALID / "retrieval-record-candidate-action.json", "r", encoding="utf-8") as f:
            cls.valid_fixture = json.load(f)

    def test_fixture_without_stratum_is_valid(self) -> None:
        fixture = copy.deepcopy(self.valid_fixture)
        self.assertNotIn("evidence_stratum", fixture)
        issue = contracts.validate_artifact(fixture, self.registry)
        self.assertIsNone(issue)

    def test_fixture_with_valid_stratum_is_valid(self) -> None:
        for stratum in ["MUST_USE", "SUPPORTING", "CONTRADICTS", "BACKGROUND", "DEFINITIONS", "LINEAGE", "EXCLUDED"]:
            fixture = copy.deepcopy(self.valid_fixture)
            fixture["evidence_stratum"] = stratum
            resign(fixture)
            issue = contracts.validate_artifact(fixture, self.registry)
            self.assertIsNone(issue)

    def test_fixture_with_invalid_stratum_is_rejected(self) -> None:
        fixture = copy.deepcopy(self.valid_fixture)
        fixture["evidence_stratum"] = "INVALID_STRATUM"
        resign(fixture)
        with self.assertRaises(contracts.ValidationFailure) as raised:
            contracts.validate_artifact(fixture, self.registry)
        self.assertEqual(raised.exception.issue.code, "SCHEMA_ENUM")


if __name__ == "__main__":
    unittest.main()
