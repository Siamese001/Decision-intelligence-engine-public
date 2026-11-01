"""
Adversarial and mutation tests for Semantic Merge Driver.
Verifies failure-closed security on malformed syntax, schema corruption, and true collisions.
"""

from __future__ import annotations

from tools.semantic_merge_core import (
    merge_json_registries,
    merge_markdown_tables,
    merge_python_ast_sets,
)


def test_adversarial_python_syntax_error():
    base = "INVALID PYTHON CODE {{{("
    ours = "def foo(): pass"
    theirs = "def bar(): pass"

    ok, merged = merge_python_ast_sets(base, ours, theirs)
    assert not ok
    assert merged == ""


def test_adversarial_json_malformed():
    base = '{"valid": true}'
    ours = '{"valid": true, "corrupted": }'
    theirs = '{"valid": true}'

    ok, merged = merge_json_registries(base, ours, theirs)
    assert not ok
    assert merged == ""


def test_adversarial_json_type_mismatch():
    base = '{"key": "string_val"}'
    ours = '{"key": [1, 2, 3]}'
    theirs = '{"key": {"nested": "obj"}}'

    ok, merged = merge_json_registries(base, ours, theirs)
    assert not ok
    assert merged == ""


def test_adversarial_markdown_empty_and_corrupt():
    base = "Not a table at all."
    ours = "| Only | One | Header |"
    theirs = "Corrupted line"

    ok, merged = merge_markdown_tables(base, ours, theirs)
    assert not ok
    assert merged == ""


def test_adversarial_massive_set_scaling():
    base_elts = [f'"item_{i}"' for i in range(100)]
    ours_elts = base_elts + [f'"ours_{i}"' for i in range(100)]
    theirs_elts = base_elts + [f'"theirs_{i}"' for i in range(100)]

    base = f"S = {{\n{', '.join(base_elts)}\n}}\n"
    ours = f"S = {{\n{', '.join(ours_elts)}\n}}\n"
    theirs = f"S = {{\n{', '.join(theirs_elts)}\n}}\n"

    ok, merged = merge_python_ast_sets(base, ours, theirs)
    assert ok
    assert '"ours_99"' in merged
    assert '"theirs_99"' in merged
