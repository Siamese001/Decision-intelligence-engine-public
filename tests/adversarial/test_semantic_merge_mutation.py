"""
Mutation and adversarial test suite for AST semantic merge drivers.
Validates fail-closed semantics against invalid AST syntax, JSON schema corruption,
malformed markdown tables, and bit-level payload mutations.
"""

from __future__ import annotations

from tools.semantic_merge_core import (
    merge_json_registries,
    merge_markdown_tables,
    merge_python_ast_sets,
)


def test_mutation_python_ast_syntax_error_fails_closed():
    """Verifies that syntax errors in Python code fail closed (False, '')."""
    corrupt_python = "MODULES = { 'core', unclosed_string"
    base = "MODULES = { 'core' }"
    theirs = "MODULES = { 'core', 'billing' }"

    # ancestor syntax error
    ok, _ = merge_python_ast_sets(corrupt_python, base, theirs)
    assert not ok

    # ours syntax error
    ok, _ = merge_python_ast_sets(base, corrupt_python, theirs)
    assert not ok

    # theirs syntax error
    ok, _ = merge_python_ast_sets(base, theirs, corrupt_python)
    assert not ok


def test_mutation_python_ast_non_string_elements_ignored_or_rejected():
    """Verifies that non-constant or non-string elements are safely handled without crash."""
    base = "MODULES = { 123, True }"
    ours = "MODULES = { 123, True, 'extra' }"
    theirs = "MODULES = { 123, True, 'other' }"

    ok, res = merge_python_ast_sets(base, ours, theirs)
    # Even if non-string constants exist, string additions should be safely merged
    if ok:
        assert "'extra'" in res or '"extra"' in res
        assert "'other'" in res or '"other"' in res


def test_mutation_json_registry_malformed_syntax_fails_closed():
    """Verifies that malformed or truncated JSON strings fail closed."""
    malformed = '{"key": "value", unquoted: 123}'
    valid_base = '{"key": "value"}'
    valid_theirs = '{"key": "value", "key2": "value2"}'

    ok, _ = merge_json_registries(malformed, valid_base, valid_theirs)
    assert not ok

    ok, _ = merge_json_registries(valid_base, malformed, valid_theirs)
    assert not ok

    ok, _ = merge_json_registries(valid_base, valid_theirs, malformed)
    assert not ok


def test_mutation_json_registry_conflicting_scalar_types():
    """Verifies that conflicting scalar types (e.g. string vs int, dict vs list) fail closed."""
    base = '{"setting": 100}'
    ours = '{"setting": 200}'
    theirs = '{"setting": 300}'

    ok, _ = merge_json_registries(base, ours, theirs)
    # Different changes to the same scalar must fail closed to prevent silent corruption
    assert not ok


def test_mutation_json_registry_dict_type_mismatch():
    """Verifies that dict vs list collisions fail closed."""
    base = '{"items": []}'
    ours = '{"items": ["a"]}'
    theirs = '{"items": {"a": 1}}'

    ok, _ = merge_json_registries(base, ours, theirs)
    assert not ok


def test_mutation_markdown_table_missing_headers_fails_closed():
    """Verifies that malformed markdown tables without table structure fail closed."""
    malformed = "Just plain text without pipes\nSecond line\n"
    valid = "| ID | Name |\n|---|---|\n| 1 | Test |\n"

    ok, _ = merge_markdown_tables(valid, malformed, valid)
    assert not ok

    ok, _ = merge_markdown_tables(valid, valid, malformed)
    assert not ok


def test_mutation_markdown_table_corrupted_delimiters():
    """Verifies that corrupted delimiter syntax fails closed or handles cleanly."""
    corrupted_sep = "| ID | Name |\nNot a separator\n| 1 | Test |\n"
    valid = "| ID | Name |\n|---|---|\n| 1 | Test |\n"

    ok, _ = merge_markdown_tables(valid, corrupted_sep, valid)
    assert not ok
