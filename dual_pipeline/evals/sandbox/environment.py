"""
Hermetic Sandboxed Runtime Environment for Agentic Evaluations.

Provisions isolated runtime roots, sandboxed SQLite databases,
and scratch directories to ensure tests never mutate baseline graph data.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from storage.connection import get_connection


@dataclass
class SandboxConfig:
    """Configuration for a provisioned sandbox runtime."""

    sandbox_id: str
    sandbox_root: Path
    graph_path: Path
    output_dir: Path
    artifacts_dir: Path
    mock_mode: bool = False


class SandboxEnvironment:
    """Manages the lifecycle of an isolated, hermetic evaluation sandbox."""

    def __init__(
        self,
        base_graph_path: Optional[Path] = None,
        prefix: str = "agentic-eval-sandbox-",
        mock_mode: bool = False,
    ):
        self.base_graph_path = (
            Path(base_graph_path).resolve()
            if base_graph_path and Path(base_graph_path).exists()
            else None
        )
        self.prefix = prefix
        self.mock_mode = mock_mode
        self._temp_dir: Optional[tempfile.TemporaryDirectory] = None
        self.config: Optional[SandboxConfig] = None

    def __enter__(self) -> SandboxConfig:
        return self.provision()

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.cleanup()

    def provision(self) -> SandboxConfig:
        """Provision the sandbox directory hierarchy and isolated SQLite database."""
        self._temp_dir = tempfile.TemporaryDirectory(prefix=self.prefix)
        root = Path(self._temp_dir.name).resolve()

        graph_dir = root / "graph"
        graph_dir.mkdir(parents=True, exist_ok=True)
        sandboxed_graph = graph_dir / "nee_graph.sqlite"

        output_dir = root / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        artifacts_dir = root / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        if self.base_graph_path:
            # Copy base graph into sandbox
            shutil.copy2(self.base_graph_path, sandboxed_graph)
        else:
            # Initialize minimal compliant SQLite schema
            self._init_empty_graph_schema(sandboxed_graph)

        sandbox_id = root.name
        self.config = SandboxConfig(
            sandbox_id=sandbox_id,
            sandbox_root=root,
            graph_path=sandboxed_graph,
            output_dir=output_dir,
            artifacts_dir=artifacts_dir,
            mock_mode=self.mock_mode,
        )
        return self.config

    def cleanup(self) -> None:
        """Tear down the temporary sandbox."""
        if self._temp_dir:
            try:
                self._temp_dir.cleanup()
            except Exception:
                pass
            self._temp_dir = None
            self.config = None

    @staticmethod
    def _init_empty_graph_schema(db_path: Path) -> None:
        """Initialize standard governing tables in a new SQLite file."""
        conn = get_connection(db_path)
        try:
            cur = conn.cursor()
            cur.executescript("""
                PRAGMA journal_mode = WAL;
                PRAGMA busy_timeout = 30000;

                CREATE TABLE IF NOT EXISTS node (
                    node_id TEXT PRIMARY KEY,
                    node_type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    properties_json TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS source (
                    source_id TEXT PRIMARY KEY,
                    source_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    location TEXT,
                    cold_archive_ref TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS source_version (
                    version_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS extraction_run (
                    run_id TEXT PRIMARY KEY,
                    source_version_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS evidence_item (
                    evidence_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    locator TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS assertion (
                    assertion_id TEXT PRIMARY KEY,
                    subject_node_id TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object_value TEXT NOT NULL,
                    object_node_id TEXT,
                    status TEXT NOT NULL,
                    valid_from TEXT,
                    valid_to TEXT,
                    superseded_at TEXT,
                    superseded_by_assertion_id TEXT,
                    qualifiers_json TEXT,
                    allowed_audiences_json TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(subject_node_id) REFERENCES node(node_id) ON DELETE CASCADE,
                    FOREIGN KEY(object_node_id) REFERENCES node(node_id) ON DELETE CASCADE,
                    FOREIGN KEY(superseded_by_assertion_id) REFERENCES assertion(assertion_id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS assertion_evidence (
                    assertion_id TEXT NOT NULL,
                    evidence_id TEXT NOT NULL,
                    PRIMARY KEY (assertion_id, evidence_id),
                    FOREIGN KEY(assertion_id) REFERENCES assertion(assertion_id) ON DELETE CASCADE,
                    FOREIGN KEY(evidence_id) REFERENCES evidence_item(evidence_id) ON DELETE RESTRICT
                );

                CREATE INDEX IF NOT EXISTS idx_assertion_subj_pred 
                    ON assertion(subject_node_id, predicate);

                CREATE TABLE IF NOT EXISTS audit_event (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)
            conn.commit()
        finally:
            conn.close()
