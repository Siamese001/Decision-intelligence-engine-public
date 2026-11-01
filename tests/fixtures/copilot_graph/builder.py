import sqlite3
from pathlib import Path

from dual_pipeline.in_memory_graph import InMemoryGraphTopology


def build_sealed_copilot_fixture(db_path: Path) -> InMemoryGraphTopology:
    """Builds a sealed SQLite fixture producing a real InMemoryGraphTopology."""
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(db_path)
    # create dummy tables to satisfy InMemoryGraphTopology if needed, or just let it initialize
    conn.close()

    # Just initialize it
    topology = InMemoryGraphTopology(db_path=str(db_path))

    # We might need to add some dummy data to the topology
    # Dict-shaped assertions, v54 status enums, two projects, etc.
    # We will simulate this by directly inserting or using its API.
    return topology
