#!/usr/bin/env python3
"""Unit, adversarial, and E2E governance tests for 6-Wave SSOT & Config Consolidation."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class SSOTConfigConsolidationGovernanceTests(unittest.TestCase):
    """Verifies Waves 1-6 of Repository-Wide SSOT, Model Registry, Parameter, and Anti-Hardcoding Consolidation."""

    def test_wave1_model_registry_ssot_parity(self) -> None:
        """Wave 1: Verify FALLBACK_TIERS in model_registry.py matches model_registry.json with zero drift."""
        from dual_pipeline.model_registry import FALLBACK_TIERS, ModelRegistry, ModelTier

        registry_json_path = REPO_ROOT / "config" / "runtime" / "model_registry.json"
        self.assertTrue(registry_json_path.is_file())
        data = json.loads(registry_json_path.read_text(encoding="utf-8"))
        tiers_json = data.get("model_tiers", {})

        frontier_json = tiers_json.get("FRONTIER_HIGH_REASONING", {})
        frontier_spec = FALLBACK_TIERS[ModelTier.FRONTIER_HIGH_REASONING.value]
        self.assertEqual(frontier_spec["default_model"], frontier_json.get("default_model"))
        self.assertEqual(
            list(frontier_spec["allowed_fallbacks"]),
            list(frontier_json.get("allowed_fallbacks", [])),
        )

        # Verify ModelRegistry resolves canonical model IDs without hardcoded fallbacks
        reg = ModelRegistry()
        self.assertEqual(
            reg.get_binding(ModelTier.FRONTIER_HIGH_REASONING).model_id,
            frontier_json.get("default_model"),
        )
        emb_id = reg.get_binding(ModelTier.EMBEDDING_MULTI_VECTOR).model_id
        self.assertIn(emb_id, ("bge-m3", "BAAI/bge-m3"))

    def test_wave1_model_neutral_and_retired_model_gates(self) -> None:
        """Wave 1: Verify lint_retired_models.py and lint_model_neutral_naming.py --all exit 0."""
        res_retired = subprocess.run(
            [sys.executable, str(REPO_ROOT / "tools" / "lint_retired_models.py")],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            res_retired.returncode,
            0,
            f"lint_retired_models.py failed:\n{res_retired.stdout}\n{res_retired.stderr}",
        )

        res_neutral = subprocess.run(
            [sys.executable, str(REPO_ROOT / "tools" / "lint_model_neutral_naming.py"), "--all"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            res_neutral.returncode,
            0,
            f"lint_model_neutral_naming.py --all failed:\n{res_neutral.stdout}\n{res_neutral.stderr}",
        )

    def test_wave2_parameter_schema_and_zero_dead_keys(self) -> None:
        """Wave 2: Verify parameters.schema.json validation and lint_parameter_references.py --check-dead."""
        schema_path = REPO_ROOT / "config" / "runtime" / "parameters.schema.json"
        self.assertTrue(schema_path.is_file(), "config/runtime/parameters.schema.json must exist")
        schema_data = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertIn("properties", schema_data)

        res_refs = subprocess.run(
            [sys.executable, str(REPO_ROOT / "tools" / "lint_parameter_references.py"), "--check-dead"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            res_refs.returncode,
            0,
            f"lint_parameter_references.py --check-dead failed:\n{res_refs.stdout}\n{res_refs.stderr}",
        )

    def test_wave3_canonical_paths_and_socket_hygiene(self) -> None:
        """Wave 3: Verify config/paths.py resolvers, .env.example existence, and zero socket timeout side-effects."""
        from config.paths import (
            get_active_pair_path,
            get_graph_sqlite_path,
            get_hitl_ledger_paths,
            get_learning_sqlite_path,
            get_primary_repo_root,
            get_repo_root,
            get_speaker_memory_path,
            get_voiceprint_sqlite_path,
        )

        self.assertTrue(get_repo_root().is_dir())
        self.assertTrue(get_primary_repo_root().is_dir())
        self.assertTrue(str(get_graph_sqlite_path()).endswith(".sqlite"))
        self.assertTrue(str(get_active_pair_path()).endswith("active_pair.json"))
        self.assertTrue(str(get_learning_sqlite_path()).endswith(".sqlite"))
        sql_p, json_p = get_hitl_ledger_paths()
        self.assertTrue(str(sql_p).endswith(".sqlite"))
        self.assertTrue(str(json_p).endswith(".json"))
        self.assertTrue(str(get_voiceprint_sqlite_path()).endswith(".sqlite"))
        self.assertTrue(str(get_speaker_memory_path()).endswith(".json"))

        env_example = REPO_ROOT / ".env.example"
        self.assertTrue(env_example.is_file(), ".env.example must exist")

        # Verify CredentialManager does not contain socket.setdefaulttimeout
        cred_src = (REPO_ROOT / "config" / "credentials.py").read_text(encoding="utf-8")
        self.assertNotIn("socket.setdefaulttimeout", cred_src)
        self.assertIsNone(socket.getdefaulttimeout())

    def test_wave4_prompt_contracts_and_judge_scorecard_parity(self) -> None:
        """Wave 4: Verify prompt contracts and judge scorecards derive from CANONICAL_PROMPT_CONFIG."""
        from dual_pipeline.l0.prompt_contract import (
            CANONICAL_OUTPUT1_PROMPT_CONTRACT,
            CANONICAL_OUTPUT2_PROMPT_CONTRACT,
            CANONICAL_OUTPUT3_PROMPT_CONTRACT,
        )
        from dual_pipeline.prompts.config import CANONICAL_PROMPT_CONFIG
        from dual_pipeline.prompts.contracts.output2_contract import Output2Contract

        self.assertEqual(
            CANONICAL_OUTPUT1_PROMPT_CONTRACT.thresholds["min_passing_score"],
            float(CANONICAL_PROMPT_CONFIG.release_gates.output1_min_score),
        )
        self.assertEqual(
            CANONICAL_OUTPUT2_PROMPT_CONTRACT.thresholds["min_passing_score"],
            float(CANONICAL_PROMPT_CONFIG.release_gates.output2_min_score),
        )
        self.assertEqual(
            CANONICAL_OUTPUT3_PROMPT_CONTRACT.thresholds["min_passing_score"],
            float(CANONICAL_PROMPT_CONFIG.release_gates.output3_min_score),
        )

        c2 = Output2Contract()
        self.assertEqual(c2.min_word_count, CANONICAL_PROMPT_CONFIG.output2.min_word_count)
        self.assertEqual(c2.max_word_count, CANONICAL_PROMPT_CONFIG.output2.max_word_count)

    def test_wave5_unified_ast_anti_hardcoding_counterfactual(self) -> None:
        """Wave 5: Counterfactual test proving hardcoded dot-version models and unapproved # noqa fail."""
        from tools.lint_hardcoded_parameters import check_file

        bad_source = (
            'def run_task(model="gpt-6.1-sol", ambiguity_margin_threshold=0.20):\n'
            '    x = "gemini-3.8-flash"  # noqa: E501\n'
            '    return x\n'
        )
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as tf:
            tf.write(bad_source)
            tf_path = Path(tf.name)

        try:
            violations = check_file(tf_path, tf_path.parent)
            self.assertGreaterEqual(
                len(violations),
                2,
                f"Expected at least 2 violations for hardcoded model & blanket noqa, got: {violations}",
            )
        finally:
            tf_path.unlink(missing_ok=True)

    def test_wave6_34_gate_readiness_parity_and_ssot_scope_protection(self) -> None:
        """Wave 6: Verify 34-gate index parity and non-skippable SSOT config scope."""
        from tools.readiness_core import _build_stages
        from tools.readiness_scope_collector import is_empty_scope_skippable

        manifest_path = REPO_ROOT / "config" / "governance" / "gate_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        stages = _build_stages(sys.executable, ["dual_pipeline/parameters.py"], ["tests.test_parameters"])
        self.assertEqual(len(stages), manifest.get("total_registered_stages"))

        # Modifying parameters.json alone (no .py files) must NOT skip Gate 13, 14, or 25
        self.assertFalse(is_empty_scope_skippable(13, ["config/runtime/parameters.json"]))
        self.assertFalse(is_empty_scope_skippable(14, ["config/runtime/parameters.json"]))
        self.assertFalse(is_empty_scope_skippable(25, ["config/runtime/model_registry.json"]))


if __name__ == "__main__":
    unittest.main()
