"""Unit tests for CLI ingress automatic operator authorization token resolution."""
import argparse
import unittest
from pathlib import Path

from dual_pipeline.cli.ingest import _resolve_ingest_rbac_authorization
from dual_pipeline.rbac import RBACAuthorizationError


class TestCLIAutoAuth(unittest.TestCase):
    def test_auto_auth_resolves_for_recognized_operator(self):
        """CLI ingress auto-resolves valid operator token when reviewer is 'Amit Ayer'."""
        args = argparse.Namespace(auth_token=None, auto_auth=True)
        metadata = {}
        _resolve_ingest_rbac_authorization(
            args=args,
            reviewer="Amit Ayer",
            rationale="Test Ingestion",
            metadata=metadata,
        )
        self.assertIn("rbac_auth", metadata)
        auth = metadata["rbac_auth"]
        self.assertTrue(auth["authorized"])
        self.assertEqual(auth["subject"], "Amit Ayer")
        self.assertIn("knowledge:release", auth["capabilities"])

    def test_auto_auth_resolves_when_explicit_auto_token_passed(self):
        """Passing --auth-token auto triggers operator token resolution."""
        args = argparse.Namespace(auth_token=Path("auto"), auto_auth=True)
        metadata = {}
        _resolve_ingest_rbac_authorization(
            args=args,
            reviewer="Amit Ayer",
            rationale="Test Ingestion",
            metadata=metadata,
        )
        self.assertTrue(metadata["rbac_auth"]["authorized"])

    def test_auto_auth_disabled_fails_closed(self):
        """When auto_auth is explicitly False, missing token raises RBACAuthorizationError."""
        args = argparse.Namespace(auth_token=None, auto_auth=False)
        metadata = {}
        with self.assertRaises(RBACAuthorizationError) as ctx:
            _resolve_ingest_rbac_authorization(
                args=args,
                reviewer="Amit Ayer",
                rationale="Test Ingestion",
                metadata=metadata,
            )
        self.assertIn("MUTATION_REJECTED", str(ctx.exception))

    def test_unrecognized_reviewer_fails_closed(self):
        """Unrecognized reviewer without token fails closed with RBACAuthorizationError."""
        args = argparse.Namespace(auth_token=None, auto_auth=True)
        metadata = {}
        with self.assertRaises(RBACAuthorizationError) as ctx:
            _resolve_ingest_rbac_authorization(
                args=args,
                reviewer="Unknown Reviewer",
                rationale="Test Ingestion",
                metadata=metadata,
            )
        self.assertIn("MUTATION_REJECTED", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
