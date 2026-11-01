"""Unit and boundary tests for tools/lint_graph_write_paths.py (Gate 32)."""

from __future__ import annotations

from pathlib import Path

from tools.lint_graph_write_paths import (
    inspect_file_for_direct_graph_writes,
    is_approved_module,
    lint_graph_write_paths,
)


def test_is_approved_module() -> None:
    assert is_approved_module("dual_pipeline/graph_writer_core/writer.py")
    assert is_approved_module("dual_pipeline/graph_writer.py")
    assert is_approved_module("dual_pipeline/uwg_core/gateway.py")
    assert is_approved_module("dual_pipeline/graph_mutation_proof.py")
    assert is_approved_module("tools/graph_gc.py")
    assert is_approved_module("tools/lint_graph_write_paths.py")
    assert not is_approved_module("scripts/custom_mutation.py")
    assert not is_approved_module("dual_pipeline/arbitrary_new_module.py")


def test_scripts_direct_write_prohibited(tmp_path: Path) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(parents=True)
    bad_script = scripts_dir / "bad_mutation.py"
    bad_script.write_text(
        "import sqlite3\n"
        "conn = sqlite3.connect('test.db')\n"
        "conn.execute(\"INSERT INTO some_table VALUES (1)\")\n",
        encoding="utf-8",
    )
    violations = inspect_file_for_direct_graph_writes(bad_script, repo_root=tmp_path)
    assert len(violations) == 1
    assert "Prohibited direct SQL write `INSERT INTO some_table` in scripts/" in violations[0]


def test_graph_table_write_in_unapproved_module_prohibited(tmp_path: Path) -> None:
    dp_dir = tmp_path / "dual_pipeline" / "unapproved_subfolder"
    dp_dir.mkdir(parents=True)
    bad_module = dp_dir / "rogue_writer.py"
    bad_module.write_text(
        "def mutate(conn):\n"
        "    conn.execute(\"UPDATE assertion SET confidence = 0.9\")\n"
        "    conn.execute(\"DELETE FROM node WHERE node_id = 'n1'\")\n",
        encoding="utf-8",
    )
    violations = inspect_file_for_direct_graph_writes(bad_module, repo_root=tmp_path)
    assert len(violations) == 2
    assert any("UPDATE assertion" in v for v in violations)
    assert any("DELETE FROM node" in v for v in violations)


def test_non_graph_table_write_in_tools_allowed(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir(parents=True)
    ok_tool = tools_dir / "scratch_tool.py"
    ok_tool.write_text(
        "def run(conn):\n"
        "    conn.execute(\"INSERT INTO local_scratch_cache VALUES ('k', 'v')\")\n",
        encoding="utf-8",
    )
    violations = inspect_file_for_direct_graph_writes(ok_tool, repo_root=tmp_path)
    assert violations == []


def test_approved_module_exempt(tmp_path: Path) -> None:
    writer_dir = tmp_path / "dual_pipeline" / "graph_writer_core"
    writer_dir.mkdir(parents=True)
    writer_file = writer_dir / "writer.py"
    writer_file.write_text(
        "def apply(conn):\n"
        "    conn.execute(\"INSERT INTO node (node_id) VALUES ('n1')\")\n",
        encoding="utf-8",
    )
    violations = inspect_file_for_direct_graph_writes(writer_file, repo_root=tmp_path)
    assert violations == []


def test_test_files_exempt(tmp_path: Path) -> None:
    test_dir = tmp_path / "tests"
    test_dir.mkdir(parents=True)
    test_file = test_dir / "test_fixture.py"
    test_file.write_text(
        "def test_something(conn):\n"
        "    conn.execute(\"INSERT INTO node (node_id) VALUES ('n1')\")\n",
        encoding="utf-8",
    )
    violations = inspect_file_for_direct_graph_writes(test_file, repo_root=tmp_path)
    assert violations == []


def test_lint_graph_write_paths_runner(tmp_path: Path) -> None:
    clean_dir = tmp_path / "tools"
    clean_dir.mkdir(parents=True)
    clean_file = clean_dir / "clean_helper.py"
    clean_file.write_text("def helper(): pass\n", encoding="utf-8")

    code, violations = lint_graph_write_paths([clean_file], repo_root=tmp_path)
    assert code == 0
    assert violations == []
