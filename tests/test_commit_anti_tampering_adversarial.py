"""Wave 5 Adversarial Tests: Test Assertion Deletion, Healer Growth, Hotfix Bypass & Malformed Inputs."""
from __future__ import annotations

from pathlib import Path

from tools.lint_test_and_healer_ratchet import (
    check_healer_ratchet,
    check_test_ratchet,
    count_healer_metrics,
    count_test_assertions,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_adversarial_test_assertion_deletion_and_trivial_assert_true() -> None:
    """Blocks deleting test functions, reducing assertion counts, or inserting trivial `assert True`."""
    main_src = (
        "def test_critical_behavior():\n"
        "    assert compute(2) == 4\n"
        "    assert compute(3) == 6\n"
        "def test_edge_case():\n"
        "    assert compute(0) == 0\n"
    )
    # 1. Deleted assertion + trivial assert True
    tampered_src = (
        "def test_critical_behavior():\n"
        "    assert True\n"
        "def test_edge_case():\n"
        "    assert compute(0) == 0\n"
    )
    v1 = check_test_ratchet(main_src, tampered_src, "tests/test_sample.py")
    assert any("trivial `assert True`" in v for v in v1)
    assert any("assertion count reduced from 2 on main to 0" in v for v in v1)

    # 2. Deleted entire test function
    deleted_func_src = (
        "def test_critical_behavior():\n"
        "    assert compute(2) == 4\n"
        "    assert compute(3) == 6\n"
    )
    v2 = check_test_ratchet(main_src, deleted_func_src, "tests/test_sample.py")
    assert any("test_edge_case: existing test function deleted" in v for v in v2)


def test_adversarial_healer_regex_and_line_growth_blocked() -> None:
    """Blocks net regex operation or code line growth in transcript_qa_core/*healer*.py."""
    main_healer = (
        "import re\n"
        "PAT = re.compile(r'foo')\n"
        "def heal(text: str) -> str:\n"
        "    return re.sub(r'bar', 'baz', text)\n"
    )
    grown_healer = (
        "import re\n"
        "PAT = re.compile(r'foo')\n"
        "PAT2 = re.compile(r'qux')\n"
        "def heal(text: str) -> str:\n"
        "    t = re.sub(r'bar', 'baz', text)\n"
        "    return re.sub(r'qux', 'ok', t)\n"
    )
    violations = check_healer_ratchet(
        main_healer,
        grown_healer,
        "dual_pipeline/transcript_qa_core/graph_healer.py",
    )
    assert any("healer regex operations grew" in v for v in violations)
    assert any("healer code lines grew" in v for v in violations)


def test_adversarial_boundary_and_malformed_ast_inputs() -> None:
    """Handles empty and syntax-invalid Python payloads gracefully without crashing."""
    funcs, triv = count_test_assertions("")
    assert funcs == {} and triv == 0

    funcs_bad, triv_bad = count_test_assertions("def test_broken(:\n    assert 1 == 1")
    assert funcs_bad == {} and triv_bad == 0

    lines, rx = count_healer_metrics("def broken(:\n  line1\n")
    assert lines == 2 and rx == 0


def test_adversarial_pre_commit_retires_governed_hotfix_bypass() -> None:
    """Verifies .githooks/pre-commit no longer allows GOVERNED_HOTFIX=1 direct commits to main."""
    hook_text = (REPO_ROOT / ".githooks" / "pre-commit").read_text(encoding="utf-8")
    assert 'if [ "${GOVERNED_HOTFIX:-0}" = "1" ]' not in hook_text
    assert "GOVERNED_HOTFIX bypass retired" in hook_text
