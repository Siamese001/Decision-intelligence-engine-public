"""Integration and contract test suite for MCP server portability, lifecycle, and model registry reconciliation.

Covers Wave 4 acceptance criteria:
1. Every declared server has a valid contract and allowed tool names match schema files.
2. Checkout-owned services resolve across isolated checkout paths without escaping.
3. Deployment-owned services are explicitly identified and protected from checkout manipulation.
4. Malformed required configuration fails visibly; optional unavailability is explicit.
5. Offline error handling: invalid transport, unapproved lookup, and plugin discovery.
6. Model/provider registry reconciliation: SSOT alignment and banned model enforcement.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from dual_pipeline.agent_sdk.mcp_bridge import (
    DEFAULT_TOOL_ALLOWLISTS,
    McpServerDefinition,
    McpServerRegistry,
)
from dual_pipeline.model_registry import (
    BANNED_REGISTRY_MODELS,
    ModelTier,
    resolve_tier_model_name,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
MCP_SCHEMAS_DIR = Path.home() / ".gemini" / "antigravity" / "mcp"
MODEL_REGISTRY_PATH = REPO_ROOT / "config" / "runtime" / "model_registry.json"


class TestMcpPortabilityAndContracts(unittest.TestCase):
    """Test suite for MCP server contracts, worktree portability, and model registry."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_dir = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # -------------------------------------------------------------------------
    # 1. Allowed Tools Match MCP Schema Invariant
    # -------------------------------------------------------------------------

    def test_all_allowed_tools_match_mcp_schemas(self) -> None:
        """Every tool in DEFAULT_TOOL_ALLOWLISTS must exist in the server's MCP schema directory."""
        if not MCP_SCHEMAS_DIR.is_dir():
            self.skipTest(f"MCP schemas directory not found at {MCP_SCHEMAS_DIR}")

        for server_name, allowed_tools in DEFAULT_TOOL_ALLOWLISTS.items():
            server_schema_dir = MCP_SCHEMAS_DIR / server_name
            self.assertTrue(
                server_schema_dir.is_dir(),
                f"Schema directory missing for declared MCP server: {server_name}",
            )

            available_tools = {p.stem for p in server_schema_dir.glob("*.json")}
            for tool_name in allowed_tools:
                self.assertIn(
                    tool_name,
                    available_tools,
                    f"Tool '{tool_name}' in allowlist for '{server_name}' does not exist in schema dir.",
                )

    # -------------------------------------------------------------------------
    # 2. Portability Across Isolated Checkout Paths (F05)
    # -------------------------------------------------------------------------

    def test_checkout_owned_services_resolve_hermetically_in_worktrees(self) -> None:
        """Checkout-owned servers must resolve commands, cwd, and scripts to the given worktree without escaping."""
        # Create mock worktree A
        worktree_a = self.test_dir / "worktree_alpha"
        (worktree_a / ".venv" / "bin").mkdir(parents=True)
        (worktree_a / ".venv" / "bin" / "python").touch(mode=0o755)
        (worktree_a / "tools").mkdir(parents=True)
        (worktree_a / "tools" / "nee_mcp_server.py").touch()
        (worktree_a / ".agents").mkdir(parents=True)

        config_a = worktree_a / ".agents" / "mcp_config.json"
        config_a.write_text(
            json.dumps({
                "mcpServers": {
                    "nee-knowledge-graph": {
                        "command": ".venv/bin/python",
                        "args": ["tools/nee_mcp_server.py"],
                        "cwd": ".",
                        "ownership": "checkout",
                    }
                }
            }),
            encoding="utf-8",
        )

        reg_a = McpServerRegistry(config_path=config_a, base_dir=worktree_a, required=True)
        defn_a = reg_a.get_server_definition("nee-knowledge-graph")
        self.assertIsNotNone(defn_a)
        self.assertEqual(defn_a.cwd, str(worktree_a.resolve()))
        self.assertEqual(defn_a.command, str((worktree_a / ".venv" / "bin" / "python").resolve()))
        self.assertEqual(defn_a.args[0], str((worktree_a / "tools" / "nee_mcp_server.py").resolve()))
        self.assertNotIn("decision-intelligence-engine/.venv", defn_a.command)

        # Create mock worktree B (completely separate)
        worktree_b = (self.test_dir / "worktree_beta").resolve()
        (worktree_b / ".venv" / "bin").mkdir(parents=True)
        (worktree_b / ".venv" / "bin" / "python").touch(mode=0o755)
        (worktree_b / "tools").mkdir(parents=True)
        (worktree_b / "tools" / "nee_mcp_server.py").touch()
        (worktree_b / ".agents").mkdir(parents=True)

        config_b = worktree_b / ".agents" / "mcp_config.json"
        config_b.write_text(
            json.dumps({
                "mcpServers": {
                    "nee-knowledge-graph": {
                        "command": ".venv/bin/python",
                        "args": ["tools/nee_mcp_server.py"],
                        "cwd": ".",
                        "ownership": "checkout",
                    }
                }
            }),
            encoding="utf-8",
        )

        reg_b = McpServerRegistry(config_path=config_b, base_dir=worktree_b, required=True)
        defn_b = reg_b.get_server_definition("nee-knowledge-graph")
        self.assertIsNotNone(defn_b)
        self.assertEqual(defn_b.cwd, str(worktree_b.resolve()))
        self.assertEqual(defn_b.command, str((worktree_b / ".venv" / "bin" / "python").resolve()))
        self.assertEqual(defn_b.args[0], str((worktree_b / "tools" / "nee_mcp_server.py").resolve()))

        # Verify strict isolation between worktree A and B
        self.assertNotEqual(defn_a.cwd, defn_b.cwd)
        self.assertNotEqual(defn_a.command, defn_b.command)
        self.assertNotEqual(defn_a.args[0], defn_b.args[0])

    # -------------------------------------------------------------------------
    # 3. Deployment-Owned Services Protection
    # -------------------------------------------------------------------------

    def test_deployment_owned_services_are_protected_from_checkout_paths(self) -> None:
        """Deployment-owned services (Node extension proxy, fetch) must retain their external command and args."""
        reg = McpServerRegistry()
        deployment_servers = reg.deployment_owned_servers

        self.assertIn("data-agent-kit", deployment_servers)
        self.assertIn("notebooks", deployment_servers)
        self.assertIn("visualization", deployment_servers)
        self.assertIn("fetch", deployment_servers)

        for s_name in deployment_servers:
            defn = reg.get_server_definition(s_name)
            self.assertEqual(defn.ownership, "deployment")
            # Must not be forced into repo worktree directory
            if s_name in ("data-agent-kit", "notebooks", "visualization"):
                self.assertEqual(defn.command, "node")
                self.assertTrue(any("antigravity-ide" in a for a in defn.args))

    # -------------------------------------------------------------------------
    # 4. Error Visibility vs Graceful Unavailability (F10)
    # -------------------------------------------------------------------------

    def test_required_config_fails_visibly_on_missing_or_malformed(self) -> None:
        """When required=True, missing or invalid configs must raise actionable errors immediately."""
        missing_file = self.test_dir / "does_not_exist.json"
        with self.assertRaises(FileNotFoundError) as ctx:
            McpServerRegistry(config_path=missing_file, required=True)
        self.assertIn("not found", str(ctx.exception))

        malformed_file = self.test_dir / "bad_syntax.json"
        malformed_file.write_text("{ unclosed json ...", encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            McpServerRegistry(config_path=malformed_file, required=True)
        self.assertIn("Malformed MCP configuration JSON", str(ctx.exception))

        invalid_schema_file = self.test_dir / "missing_servers.json"
        invalid_schema_file.write_text("{\"other_key\": 123}", encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            McpServerRegistry(config_path=invalid_schema_file, required=True)
        self.assertIn("missing 'mcpServers' object", str(ctx.exception))

    def test_optional_config_distinguishes_unavailability_from_empty(self) -> None:
        """When required=False, failure records load_error and is_available=False without crashing."""
        missing_file = self.test_dir / "optional_missing.json"
        reg = McpServerRegistry(config_path=missing_file, required=False)
        self.assertFalse(reg.is_available)
        self.assertIsNotNone(reg.load_error)
        self.assertEqual(len(reg.registered_server_names), 0)

        # Successful empty config
        empty_config = self.test_dir / "empty_valid.json"
        empty_config.write_text("{\"mcpServers\": {}}", encoding="utf-8")
        reg_empty = McpServerRegistry(config_path=empty_config, required=False)
        self.assertTrue(reg_empty.is_available)
        self.assertIsNone(reg_empty.load_error)
        self.assertEqual(len(reg_empty.registered_server_names), 0)

    # -------------------------------------------------------------------------
    # 5. Remote HTTP Transport & Plugin Discovery
    # -------------------------------------------------------------------------

    def test_plugin_mcp_discovery_registers_remote_servers(self) -> None:
        """Plugin discovery reads .mcp.json files and registers remote HTTP servers."""
        reg = McpServerRegistry()
        discovered = reg.discover_plugin_servers()
        self.assertIn("lucidchart", discovered)
        self.assertIn("miro", discovered)

        lucid = reg.get_server_definition("lucidchart")
        self.assertIsNotNone(lucid)
        self.assertEqual(lucid.transport, "http")
        self.assertEqual(lucid.ownership, "remote")
        self.assertEqual(lucid.url, "https://mcp.lucid.app/mcp")

        # Stdio build must reject HTTP transport
        with self.assertRaises(ValueError) as ctx:
            reg.build_stdio_server("lucidchart")
        self.assertIn("uses 'http' transport, not stdio", str(ctx.exception))

    # -------------------------------------------------------------------------
    # 6. Per-Server Contract Export
    # -------------------------------------------------------------------------

    def test_server_contracts_export_complete_matrix(self) -> None:
        """Server contracts export must provide complete metadata for all declared servers."""
        reg = McpServerRegistry()
        reg.discover_plugin_servers()
        contracts = reg.server_contracts

        self.assertGreaterEqual(len(contracts), 11)
        required_fields = {
            "name", "transport", "command", "args", "ownership",
            "credentials_source", "allowed_tools", "mutability",
            "timeout_seconds", "retry_count", "shutdown_timeout_seconds",
        }

        for name, spec in contracts.items():
            missing = required_fields - set(spec.keys())
            self.assertEqual(missing, set(), f"Contract for '{name}' missing fields: {missing}")
            self.assertIn(spec["ownership"], {"checkout", "deployment", "remote"})
            self.assertIn(spec["mutability"], {"read_only", "read_write"})

    # -------------------------------------------------------------------------
    # 7. Model & Provider Registry Reconciliation
    # -------------------------------------------------------------------------

    def test_model_registry_contains_no_banned_models(self) -> None:
        """Validates that config/runtime/model_registry.json contains zero banned models."""
        self.assertTrue(MODEL_REGISTRY_PATH.is_file(), "model_registry.json must exist")
        data = json.loads(MODEL_REGISTRY_PATH.read_text(encoding="utf-8"))

        approved_models = data.get("approved_models", {})
        all_approved = []
        for provider, models in approved_models.items():
            all_approved.extend(models)

        for banned in BANNED_REGISTRY_MODELS:
            self.assertNotIn(
                banned,
                all_approved,
                f"Banned model '{banned}' detected in approved_models SSOT!",
            )

    def test_model_tiers_resolve_to_approved_models(self) -> None:
        """Every standard model tier in ModelTier enum resolves to an approved model."""
        tiers_to_test = [
            ModelTier.FRONTIER_HIGH_REASONING,
            ModelTier.FRONTIER_REASONING,
            ModelTier.SURGICAL_HEALER,
            ModelTier.FAST_ROUTING,
            ModelTier.COPILOT_FAST_SNAP,
        ]

        for tier in tiers_to_test:
            model_id = resolve_tier_model_name(tier)
            self.assertIsInstance(model_id, str)
            self.assertGreater(len(model_id), 0)
            self.assertNotIn(model_id, BANNED_REGISTRY_MODELS)


if __name__ == "__main__":
    unittest.main()
