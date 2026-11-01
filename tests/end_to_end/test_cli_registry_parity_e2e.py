"""
tests/end_to_end/test_cli_registry_parity_e2e.py

End-to-end qualification suite for Wave 11 Code Architecture Structural Fixes:
- Validates 'nee --help' imports < 60 first-party modules in < 0.30s.
- Verifies heavy subsystems (torch, transformers, FlagEmbedding) are not imported at CLI start.
- Verifies 100% subcommand coverage parity between build_parser() and CommandRegistry.
- Tests decentralized, lazy command dispatch across core CLI handlers.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import unittest
from pathlib import Path

from dual_pipeline.cli.engine import build_parser, _init_command_registry
from dual_pipeline.cli.registry import CommandRegistry


class TestCLIRegistryParityE2E(unittest.TestCase):
    """End-to-end tests for decentralized CLI command registry and import performance."""

    def test_cli_help_performance_and_import_budget(self) -> None:
        """
        Verify 'nee --help' loads in < 0.30s and imports < 60 first-party dual_pipeline modules.
        Enforces that PyTorch, HuggingFace, and embedding weights are not eagerly loaded.
        """
        code = """
import sys
import dual_pipeline.cli.engine as eng
parser = eng.build_parser()
help_str = parser.format_help()
dp_mods = [m for m in sys.modules if m.startswith('dual_pipeline')]
heavy_mods = [m for m in sys.modules if any(h in m for h in ('torch', 'transformers', 'FlagEmbedding', 'sentencepiece'))]
print(f"COUNT={len(dp_mods)}")
print(f"HEAVY={len(heavy_mods)}")
"""
        start = time.perf_counter()
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
        )
        elapsed = time.perf_counter() - start

        # Extract counts
        lines = dict(line.split("=", 1) for line in proc.stdout.strip().splitlines() if "=" in line)
        dp_count = int(lines.get("COUNT", 999))
        heavy_count = int(lines.get("HEAVY", 999))

        self.assertLess(elapsed, 0.30, f"'nee --help' was too slow: {elapsed:.3f}s (budget: <0.30s)")
        self.assertLess(dp_count, 60, f"Too many first-party modules loaded: {dp_count} (budget: <60)")
        self.assertEqual(heavy_count, 0, f"Heavy AI/ML libraries eagerly loaded: {heavy_count}")

    def test_cli_subcommand_coverage_parity(self) -> None:
        """
        Verify 100% bidirectional parity between build_parser() choices and CommandRegistry.
        Dynamically derives the command inventory rather than relying on a static count.
        Note: Registration coverage verifies structural dispatch parity; behavioral coverage
        is separately asserted in behavioral/scenario tests.
        """
        parser = build_parser()
        subparsers_action = next(
            (action for action in parser._actions if isinstance(action, argparse._SubParsersAction)),
            None,
        )
        self.assertIsNotNone(subparsers_action, "Root parser must contain subparsers")

        parser_subcommands = set(subparsers_action.choices.keys())
        self.assertGreater(
            len(parser_subcommands),
            0,
            "Parser must expose at least one subcommand",
        )

        registry = _init_command_registry()
        registered_commands = set(registry.list_commands())

        # Bidirectional parity: no orphaned parser commands, no orphaned registry handlers
        missing_handlers = parser_subcommands - registered_commands
        self.assertEqual(
            missing_handlers,
            set(),
            f"Parser subcommands missing registry handlers: {missing_handlers}",
        )

        extra_handlers = registered_commands - parser_subcommands
        self.assertEqual(
            extra_handlers,
            set(),
            f"Registry commands missing from parser choices: {extra_handlers}",
        )

        # Verify each registered command returns a valid callable handler
        for cmd in parser_subcommands:
            handler = registry.get_handler(cmd)
            self.assertIsNotNone(handler, f"Handler for '{cmd}' should not be None")
            self.assertTrue(callable(handler), f"Handler for '{cmd}' must be callable")

    def test_cli_registry_lazy_dispatch_execution(self) -> None:
        """
        Verify decentralized lazy dispatch loads handlers and dispatches correctly.
        """
        registry = _init_command_registry()

        # Verify get_handler returns callable for semantic-cache-status and runtime-evidence
        handler_cache = registry.get_handler("semantic-cache-status")
        self.assertTrue(callable(handler_cache))

        handler_evidence = registry.get_handler("runtime-evidence")
        self.assertTrue(callable(handler_evidence))

        # Register a lightweight probe command on an isolated registry to verify dispatch end-to-end
        probe_registry = CommandRegistry()
        executed_payload = {}

        def sample_loader():
            def sample_handler(args):
                executed_payload["command"] = args.command
                executed_payload["status"] = "OK"
                return 0
            return sample_handler

        probe_registry.register("test-probe-cmd", sample_loader)
        probe_args = argparse.Namespace(command="test-probe-cmd")
        res = probe_registry.dispatch(probe_args)
        self.assertEqual(res, 0)
        self.assertEqual(executed_payload.get("status"), "OK")

    def test_cli_registry_unknown_command_raises(self) -> None:
        """Verify dispatching an unregistered command raises ValueError."""
        registry = _init_command_registry()
        bad_args = argparse.Namespace(command="nonexistent_subcommand_xyz")
        with self.assertRaises(ValueError) as ctx:
            registry.dispatch(bad_args)
        self.assertIn("Unknown CLI command", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
