"""
Unit tests for semantic merge driver core functions.
"""

from __future__ import annotations

from tools.semantic_merge_core import (
    merge_json_registries,
    merge_markdown_tables,
    merge_python_ast_sets,
)


def test_merge_python_ast_sets_clean():
    base = 'CONTROLLED_FILES: Set[str] = {"a.py", "b.py"}\n'
    ours = 'CONTROLLED_FILES: Set[str] = {\n    "a.py",\n    "b.py",\n    "c.py",\n}\n'
    theirs = 'CONTROLLED_FILES: Set[str] = {\n    "a.py",\n    "b.py",\n    "d.py",\n}\n'

    ok, merged = merge_python_ast_sets(base, ours, theirs)
    assert ok
    assert '"c.py"' in merged
    assert '"d.py"' in merged
    assert '"a.py"' in merged


def test_merge_json_registries_clean():
    base = '{"projects": {"nexus": "desc_nexus"}}\n'
    ours = '{"projects": {"nexus": "desc_nexus", "proj_a": "desc_a"}}\n'
    theirs = '{"projects": {"nexus": "desc_nexus", "proj_b": "desc_b"}}\n'

    ok, merged = merge_json_registries(base, ours, theirs)
    assert ok
    assert '"proj_a"' in merged
    assert '"proj_b"' in merged
    assert '"nexus"' in merged


def test_merge_json_registries_conflict():
    base = '{"key": "original"}\n'
    ours = '{"key": "version_ours"}\n'
    theirs = '{"key": "version_theirs"}\n'

    ok, merged = merge_json_registries(base, ours, theirs)
    assert not ok
    assert merged == ""


def test_merge_markdown_tables_clean():
    base = "| Branch | Status |\n| :--- | :--- |\n| feat/a | Pass |\n"
    ours = "| Branch | Status |\n| :--- | :--- |\n| feat/a | Pass |\n| feat/b | Pass |\n"
    theirs = "| Branch | Status |\n| :--- | :--- |\n| feat/a | Pass |\n| feat/c | Pass |\n"

    ok, merged = merge_markdown_tables(base, ours, theirs)
    assert ok
    assert "feat/b" in merged
    assert "feat/c" in merged
    assert "feat/a" in merged
