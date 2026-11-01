"""Model Context Protocol (MCP) Bridge for Google Antigravity SDK agents.

Enables Antigravity agents to discover, configure, filter, and connect to
the 11 existing MCP servers in the Decision Intelligence ecosystem:
1. nee-knowledge-graph
2. graphdb-inspector
3. judge-retrieval-eval
4. contract-governance
5. speech-transcript-synthesizer
6. test-orchestrator
7. deepwiki
8. data-agent-kit
9. notebooks
10. visualization
11. fetch
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence

from google.antigravity.types import McpStdioServer

# Canonical server profile definitions for scoping tool exposure
PROFILE_PRESETS: Mapping[str, Sequence[str]] = {
    "graph": ("nee-knowledge-graph", "graphdb-inspector"),
    "governance": ("contract-governance", "test-orchestrator"),
    "retrieval": ("judge-retrieval-eval", "nee-knowledge-graph"),
    "data_science": ("notebooks", "visualization", "data-agent-kit"),
    "synthesis": ("speech-transcript-synthesizer", "nee-knowledge-graph"),
    "default_safe": ("nee-knowledge-graph", "contract-governance"),
}

import sys

# Default tool allowlists per server to protect LLM context windows
DEFAULT_TOOL_ALLOWLISTS: Mapping[str, Sequence[str]] = {
    "nee-knowledge-graph": (
        "nee_query",
        "nee_intake_status",
        "nee_active_pair",
        "nee_semantic_cache_status",
    ),
    "graphdb-inspector": (
        "graphdb_snapshot_state",
        "graphdb_read_query",
        "graphdb_verify_sidecar_sync",
        "graphdb_itemized_assertion_delta",
    ),
    "judge-retrieval-eval": (
        "judge_evaluate_candidate",
        "eval_retrieval_11gates",
        "seal_retrieval_receipt",
    ),
    "contract-governance": (
        "schema_validate_payload",
        "rfc8259_canonical_hash",
        "contract_inventory_verify",
    ),
    "test-orchestrator": (
        "run_governed_test_suite",
        "extract_test_failure_diagnostics",
    ),
    "deepwiki": (
        "read_wiki_structure",
        "read_wiki_contents",
    ),
    "data-agent-kit": (
        "get_active_editor_context",
        "list_resource_templates",
    ),
    "notebooks": (
        "get_notebook_info",
        "read_cell",
        "list_cells",
    ),
    "visualization": (
        "render_chart",
    ),
    "speech-transcript-synthesizer": (
        "validate_output_hierarchy_compliance",
    ),
    "fetch": (
        "fetch",
    ),
}


@dataclass(frozen=True)
class McpServerDefinition:
    """Discovered or configured MCP server declaration and governance contract."""

    name: str
    command: str = ""
    args: Sequence[str] = field(default_factory=tuple)
    cwd: Optional[str] = None
    env: Mapping[str, str] = field(default_factory=dict)
    timeout_seconds: Optional[int] = 60
    enabled_tools: Optional[Sequence[str]] = None
    disabled_tools: Optional[Sequence[str]] = None
    # Wave 4 extended contract attributes
    transport: str = "stdio"
    url: Optional[str] = None
    ownership: str = "checkout"  # "checkout", "deployment", "remote"
    credentials_source: Optional[str] = None
    mutability: str = "read_only"  # "read_only", "read_write"
    retry_count: int = 0
    shutdown_timeout_seconds: int = 5


class McpServerRegistry:
    """Manages discovery, filtering, and instantiation of MCP server configs for SDK agents."""

    def __init__(
        self,
        config_path: Optional[Path | str] = None,
        base_dir: Optional[Path | str] = None,
        required: bool = False,
    ) -> None:
        self._base_dir = Path(base_dir).resolve() if base_dir else Path.cwd().resolve()
        self._required = required
        self._config_path = (
            Path(config_path) if config_path else self._resolve_default_config_path()
        )
        self._servers: Dict[str, McpServerDefinition] = {}
        self._is_available: bool = False
        self._load_error: Optional[str] = None
        self._load_registry()

    @property
    def is_available(self) -> bool:
        """Indicates whether MCP configuration was loaded successfully."""
        return self._is_available

    @property
    def load_error(self) -> Optional[str]:
        """Contains actionable error message if configuration loading failed."""
        return self._load_error

    @property
    def registered_server_names(self) -> List[str]:
        return sorted(self._servers.keys())

    @property
    def checkout_owned_servers(self) -> List[str]:
        return sorted([k for k, v in self._servers.items() if v.ownership == "checkout"])

    @property
    def deployment_owned_servers(self) -> List[str]:
        return sorted([k for k, v in self._servers.items() if v.ownership == "deployment"])

    @property
    def remote_servers(self) -> List[str]:
        return sorted([k for k, v in self._servers.items() if v.ownership == "remote"])

    @property
    def server_contracts(self) -> Dict[str, Dict[str, Any]]:
        """Returns the full governance contract dictionary for all registered servers."""
        contracts = {}
        for name, d in self._servers.items():
            contracts[name] = {
                "name": d.name,
                "transport": d.transport,
                "command": d.command,
                "args": list(d.args),
                "cwd": d.cwd,
                "url": d.url,
                "ownership": d.ownership,
                "credentials_source": d.credentials_source,
                "allowed_tools": list(d.enabled_tools) if d.enabled_tools else [],
                "mutability": d.mutability,
                "timeout_seconds": d.timeout_seconds,
                "retry_count": d.retry_count,
                "shutdown_timeout_seconds": d.shutdown_timeout_seconds,
            }
        return contracts

    def _resolve_default_config_path(self) -> Path:
        """Locate the canonical .agents/mcp_config.json searching upwards from base_dir."""
        curr = self._base_dir.resolve()
        for candidate_parent in [curr, *curr.parents]:
            cand = candidate_parent / ".agents" / "mcp_config.json"
            if cand.exists():
                return cand
        return self._base_dir / ".agents" / "mcp_config.json"

    def _resolve_checkout_command(self, raw_cmd: str) -> str:
        """Resolves Python command hermetically targeting local checkout venv."""
        if ".venv" in raw_cmd or raw_cmd.endswith("python"):
            cand = self._base_dir / ".venv" / "bin" / "python"
            if cand.is_file():
                return str(cand)
            return sys.executable
        if not Path(raw_cmd).is_absolute():
            return str((self._base_dir / raw_cmd).resolve())
        return raw_cmd

    def _resolve_checkout_args(self, raw_args: Sequence[str]) -> Tuple[str, ...]:
        """Resolves tool script paths hermetically within the active checkout."""
        resolved = []
        for arg in raw_args:
            if arg.endswith(".py") or "tools/" in arg:
                script_name = Path(arg).name
                cand = self._base_dir / "tools" / script_name
                if cand.is_file():
                    resolved.append(str(cand))
                    continue
            resolved.append(arg)
        return tuple(resolved)

    def _handle_load_error(self, msg: str, exc_type: type = ValueError) -> None:
        if self._required:
            raise exc_type(msg)
        self._load_error = msg
        self._is_available = False

    def _parse_server(self, name: str, spec: dict) -> McpServerDefinition:
        transport = spec.get("transport") or spec.get("type", "stdio")
        dep_servers = ("fetch", "data-agent-kit", "notebooks", "visualization")
        is_node = spec.get("command") == "node" or "antigravity-ide" in str(spec.get("args", []))
        ownership = spec.get("ownership") or (
            "deployment" if is_node or name in dep_servers
            else "remote" if transport in ("http", "sse") else "checkout"
        )
        is_co = (ownership == "checkout")
        cmd = self._resolve_checkout_command(spec.get("command", "")) if is_co else spec.get("command", "")
        args = self._resolve_checkout_args(tuple(spec.get("args", []))) if is_co else tuple(spec.get("args", []))
        cwd = str(self._base_dir) if is_co else spec.get("cwd")
        return McpServerDefinition(
            name=name, command=cmd, args=args, cwd=cwd,
            env=spec.get("env", {}), timeout_seconds=spec.get("timeout_seconds", 60),
            enabled_tools=DEFAULT_TOOL_ALLOWLISTS.get(name), transport=transport,
            url=spec.get("url") or spec.get("serverUrl"), ownership=ownership,
            credentials_source=spec.get("credentials_source"),
            mutability=spec.get("mutability", "read_only"),
            retry_count=spec.get("retry_count", 0),
            shutdown_timeout_seconds=spec.get("shutdown_timeout_seconds", 5),
        )

    def _load_registry(self) -> None:
        """Load and parse server declarations from configuration JSON."""
        if not self._config_path.exists():
            return self._handle_load_error(f"Configuration file not found: {self._config_path}", FileNotFoundError)
        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            return self._handle_load_error(f"Malformed MCP configuration JSON in {self._config_path}: {exc}")
        if not isinstance(data, dict) or "mcpServers" not in data or not isinstance(data["mcpServers"], dict):
            return self._handle_load_error(f"Invalid MCP configuration in {self._config_path}: missing 'mcpServers' object.")

        for name, spec in data["mcpServers"].items():
            self._servers[name] = self._parse_server(name, spec)
        self._is_available = True
        self._load_error = None

    def discover_plugin_servers(self, plugins_dir: Optional[Path | str] = None) -> List[str]:
        """Discovers plugin-declared MCP servers (e.g. Lucidchart, Miro HTTP endpoints)."""
        p_dir = Path(plugins_dir).resolve() if plugins_dir else self._base_dir / ".agents" / "plugins"
        if not p_dir.is_dir():
            return []

        discovered = []
        for plugin_path in sorted(p_dir.iterdir()):
            if not plugin_path.is_dir():
                continue
            mcp_file = plugin_path / ".mcp.json"
            if not mcp_file.is_file():
                continue
            try:
                data = json.loads(mcp_file.read_text(encoding="utf-8"))
                for s_name, s_spec in data.get("mcpServers", {}).items():
                    server_url = s_spec.get("serverUrl") or s_spec.get("url")
                    defn = McpServerDefinition(
                        name=s_name,
                        command="",
                        args=(),
                        transport=s_spec.get("type", "http"),
                        url=server_url,
                        ownership="remote",
                        credentials_source="OAUTH",
                        mutability="read_write",
                    )
                    self._servers[s_name] = defn
                    discovered.append(s_name)
            except Exception:
                continue
        return discovered

    def register_server(self, definition: McpServerDefinition) -> None:
        """Manually register or override an MCP server definition."""
        self._servers[definition.name] = definition

    def get_server_definition(self, name: str) -> Optional[McpServerDefinition]:
        """Fetch server definition by name."""
        return self._servers.get(name)

    def build_stdio_server(
        self,
        name: str,
        enabled_tools: Optional[Sequence[str]] = None,
        disabled_tools: Optional[Sequence[str]] = None,
        timeout_seconds: Optional[int] = None,
    ) -> McpStdioServer:
        """Instantiate a contract-valid McpStdioServer for the Antigravity SDK."""
        defn = self._servers.get(name)
        if not defn:
            raise KeyError(f"MCP server '{name}' is not registered in registry.")

        if defn.transport in ("http", "sse"):
            raise ValueError(f"Server '{name}' uses '{defn.transport}' transport, not stdio.")

        final_enabled = (
            list(enabled_tools)
            if enabled_tools is not None
            else (list(defn.enabled_tools) if defn.enabled_tools else None)
        )
        final_disabled = (
            list(disabled_tools)
            if disabled_tools is not None
            else (list(defn.disabled_tools) if defn.disabled_tools else None)
        )

        return McpStdioServer(
            name=defn.name,
            command=defn.command,
            args=list(defn.args),
            env=dict(defn.env) if defn.env else None,
            timeout_seconds=timeout_seconds or defn.timeout_seconds,
            enabled_tools=final_enabled,
            disabled_tools=final_disabled,
        )

    def build_profile_servers(
        self,
        profile_name: str,
        custom_allowlists: Optional[Mapping[str, Sequence[str]]] = None,
    ) -> List[McpStdioServer]:
        """Build a bounded list of McpStdioServer instances scoped to a functional profile."""
        server_names = PROFILE_PRESETS.get(profile_name)
        if not server_names:
            raise ValueError(
                f"Unknown profile '{profile_name}'. Available: {list(PROFILE_PRESETS.keys())}"
            )

        servers: List[McpStdioServer] = []
        for s_name in server_names:
            if s_name in self._servers:
                allowlist = (
                    custom_allowlists.get(s_name) if custom_allowlists else None
                )
                servers.append(
                    self.build_stdio_server(name=s_name, enabled_tools=allowlist)
                )

        return servers

    def build_all_available_servers(
        self,
        restrict_tools: bool = True,
    ) -> List[McpStdioServer]:
        """Build all registered servers, optionally with context-safe tool allowlists."""
        servers: List[McpStdioServer] = []
        for name in sorted(self._servers.keys()):
            if self._servers[name].transport not in ("http", "sse"):
                allowlist = DEFAULT_TOOL_ALLOWLISTS.get(name) if restrict_tools else None
                servers.append(self.build_stdio_server(name=name, enabled_tools=allowlist))
        return servers
