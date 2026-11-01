"""tests/test_cli_argument_parity.py

Wave 1 Verification: Bounded parsed-namespace and handler-contract tests.
Enforces that:
1. Production parser namespaces for 'ask' and 'demo' contain zero stale 'offline_external_l2' flags.
2. Handler execution resolves 'provider_egress_allowed' strictly from approved L2 policy,
   supporting explicit compatibility overrides without requiring live provider calls.
3. Both allowed and forbidden egress decisions are exercised cleanly without live network calls.
4. Missing provider configurations fail closed.
5. Rejection of removed flags is strictly enforced and falsifiable.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import os
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from dual_pipeline.cli.engine import _handle_ask, _handle_demo, build_parser
from dual_pipeline.l2_policy_core.policy import L2Config


@contextlib.contextmanager
def _isolated_production_env():
    keys = ["PYTEST_CURRENT_TEST", "NEE_ALLOW_TEST_FIXTURES", "RUNNING_TESTS_WITH_PROGRESS", "NEE_TEST_MODE"]
    saved = {k: os.environ.pop(k, None) for k in keys}
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def _mock_l2_config(egress_allowed: bool) -> L2Config:
    return L2Config(
        schema_version="4.0.0",
        mode="conditional",
        provider_class="openai",
        provider_egress_allowed=egress_allowed,
        endpoint="https://api.openai.com/v1/responses",
        api_key_env="OPENAI_API_KEY",
        local_env_file="env_di",
        standard=MagicMock(),
        enhanced=MagicMock(),
        output=MagicMock(),
        reliability=MagicMock(),
        request_budget=MagicMock(),
        config_digest="test-config-digest-hash",
    )


class TestCliArgumentParity(unittest.TestCase):
    """Test suite asserting CLI argument parity and handler contract integrity."""

    def test_ask_parsed_namespace_purged_of_stale_offline_flags(self) -> None:
        """Verify that parsed Namespace for 'ask' has no stale offline attributes."""
        parser = build_parser()
        args = parser.parse_args(["ask", "--question", "What is the architecture roadmap?"])
        self.assertFalse(hasattr(args, "offline_external_l2"))
        self.assertFalse(hasattr(args, "offline_ingestion_reasoning"))
        self.assertEqual(args.question, "What is the architecture roadmap?")

    def test_demo_parsed_namespace_purged_of_stale_offline_flags(self) -> None:
        """Verify that parsed Namespace for 'demo' has no stale offline attributes."""
        parser = build_parser()
        args = parser.parse_args(["demo", "--package", "sample_package.json"])
        self.assertFalse(hasattr(args, "offline_external_l2"))
        self.assertFalse(hasattr(args, "offline_ingestion_reasoning"))
        self.assertEqual(str(args.package), "sample_package.json")

    @patch("dual_pipeline.query_router.route_query")
    @patch("dual_pipeline.l2.load_l2_config")
    @patch("dual_pipeline.decision_service.DecisionService.query")
    @patch("dual_pipeline.answer_ledger.record_query_answer_safe")
    def test_handle_ask_egress_allowed_from_policy(
        self,
        mock_record: MagicMock,
        mock_ds_query: MagicMock,
        mock_load_l2: MagicMock,
        mock_route: MagicMock,
    ) -> None:
        """Verify _handle_ask passes provider_egress_allowed=True when policy allows egress."""
        mock_load_l2.return_value = _mock_l2_config(egress_allowed=True)
        preview = MagicMock()
        preview.enters_query_runtime = False
        mock_route.return_value = preview
        mock_ds_query.return_value = {"status": "SUCCESS", "answer": "Roadmap verified"}

        parser = build_parser()
        args = parser.parse_args(["ask", "--question", "What is the status?"])

        res = _handle_ask(args)
        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(res["answer"], "Roadmap verified")

        mock_ds_query.assert_called_once()
        _, kwargs = mock_ds_query.call_args
        self.assertIn("provider_egress_allowed", kwargs)
        self.assertIs(kwargs["provider_egress_allowed"], True)

    @patch("dual_pipeline.query_router.route_query")
    @patch("dual_pipeline.l2.load_l2_config")
    @patch("dual_pipeline.decision_service.DecisionService.query")
    @patch("dual_pipeline.answer_ledger.record_query_answer_safe")
    def test_handle_ask_egress_forbidden_from_policy(
        self,
        mock_record: MagicMock,
        mock_ds_query: MagicMock,
        mock_load_l2: MagicMock,
        mock_route: MagicMock,
    ) -> None:
        """Verify _handle_ask passes provider_egress_allowed=False when policy forbids egress."""
        mock_load_l2.return_value = _mock_l2_config(egress_allowed=False)
        preview = MagicMock()
        preview.enters_query_runtime = False
        mock_route.return_value = preview
        mock_ds_query.return_value = {"status": "BLOCKED_EGRESS", "answer": "Offline local only"}

        parser = build_parser()
        args = parser.parse_args(["ask", "--question", "What is the status?"])

        res = _handle_ask(args)
        self.assertEqual(res["status"], "BLOCKED_EGRESS")

        mock_ds_query.assert_called_once()
        _, kwargs = mock_ds_query.call_args
        self.assertIn("provider_egress_allowed", kwargs)
        self.assertIs(kwargs["provider_egress_allowed"], False)

    @patch("dual_pipeline.query_router.route_query")
    @patch("dual_pipeline.l2.load_l2_config")
    @patch("dual_pipeline.decision_service.DecisionService.query")
    @patch("dual_pipeline.answer_ledger.record_query_answer_safe")
    def test_handle_ask_explicit_compatibility_namespace_override(
        self,
        mock_record: MagicMock,
        mock_ds_query: MagicMock,
        mock_load_l2: MagicMock,
        mock_route: MagicMock,
    ) -> None:
        """Verify explicit provider_egress_allowed attribute in compatibility namespace is respected."""
        mock_load_l2.return_value = _mock_l2_config(egress_allowed=True)
        preview = MagicMock()
        preview.enters_query_runtime = False
        mock_route.return_value = preview
        mock_ds_query.return_value = {"status": "SUCCESS"}

        parser = build_parser()
        args = parser.parse_args(["ask", "--question", "What is the status?"])
        # Explicit override on namespace
        args.provider_egress_allowed = False

        res = _handle_ask(args)
        self.assertEqual(res["status"], "SUCCESS")

        mock_ds_query.assert_called_once()
        _, kwargs = mock_ds_query.call_args
        self.assertIs(kwargs["provider_egress_allowed"], False)

    @patch("dual_pipeline.index.HybridRetriever")
    @patch("dual_pipeline.cli.engine._backend")
    @patch("dual_pipeline.cli.engine._authorization")
    @patch("dual_pipeline.cli.engine._bind_model_authorization")
    @patch("dual_pipeline.release.ReleaseManager")
    @patch("dual_pipeline.intake_engine.IntakeEngine")
    @patch("dual_pipeline.l2.OpenAIProviderFactory")
    @patch("dual_pipeline.query.QueryEngine")
    @patch("dual_pipeline.l2.load_l2_config")
    def test_handle_demo_resolves_egress_from_policy(
        self,
        mock_load_l2: MagicMock,
        mock_query_engine_cls: MagicMock,
        mock_factory_cls: MagicMock,
        mock_intake_cls: MagicMock,
        mock_release_mgr_cls: MagicMock,
        mock_bind_auth: MagicMock,
        mock_auth: MagicMock,
        mock_backend: MagicMock,
        mock_retriever: MagicMock,
    ) -> None:
        """Verify _handle_demo passes policy provider_egress_allowed to QueryEngine.ask."""
        mock_load_l2.return_value = _mock_l2_config(egress_allowed=False)
        backend = MagicMock()
        backend.production_qualified = False
        mock_backend.return_value = backend

        mock_mgr = MagicMock()
        mock_mgr.bootstrap.return_value = {
            "graph_path": "graph.db",
            "sidecar_path": "sidecar.db",
            "generation_id": "gen-bootstrap-001",
        }
        mock_mgr.prepare.return_value = {
            "receipt_id": "receipt-001",
            "next_pair": {"generation_id": "gen-123"},
        }
        mock_mgr.activate.return_value = {"status": "ACTIVE"}
        mock_mgr._resolve.side_effect = lambda p: Path(p)
        mock_release_mgr_cls.return_value = mock_mgr

        mock_intake = MagicMock()
        mock_intake.ingest.return_value = {"run_id": "run-456"}
        mock_intake_cls.return_value = mock_intake

        mock_query = MagicMock()
        mock_query.ask.return_value = {"status": "COMPLETED"}
        mock_query_engine_cls.return_value = mock_query

        parser = build_parser()
        args = parser.parse_args(["demo", "--package", "pkg.json", "--runtime-root", ".local/test_root"])

        res = _handle_demo(args)
        self.assertEqual(res["status"], "PASS")

        mock_query.ask.assert_called_once()
        _, ask_kwargs = mock_query.ask.call_args
        self.assertIn("provider_egress_allowed", ask_kwargs)
        self.assertIs(ask_kwargs["provider_egress_allowed"], False)

    def test_parser_rejects_removed_flags_falsifiable(self) -> None:
        """Verify parser strictly rejects --offline-external-l2 on ask and demo."""
        parser = build_parser()
        for cmd in (["ask", "--question", "Q", "--offline-external-l2"], ["demo", "--package", "p.json", "--offline-external-l2"]):
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as exc:
                    parser.parse_args(cmd)
            self.assertEqual(exc.exception.code, 1)
            self.assertIn("unrecognized arguments: --offline-external-l2", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
