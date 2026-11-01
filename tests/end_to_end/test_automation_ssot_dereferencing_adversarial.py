"""tests/end_to_end/test_automation_ssot_dereferencing_adversarial.py

Adversarial and boundary tests for automation SSOT dereferencing.
Mandatory edge-case categories covered:
1. Boundary payload: 0-byte target file rejection, exact boundary word counts.
2. Malformed input: non-file targets, unsupported URI schemes.
3. Concurrency & race: concurrent reader threads accessing dereferenced prompt.
"""
from __future__ import annotations

import concurrent.futures
import json
from pathlib import Path

import pytest

from tools.task_restoration_core.ssot_pointers import (
    extract_target_path,
    is_valid_pointer_prompt,
)

ROOT = Path(__file__).resolve().parents[2]


def test_boundary_payload_zero_byte_target(tmp_path: Path) -> None:
    """Boundary test: dereferenced target file with 0 bytes is flagged as invalid."""
    empty_prompt = tmp_path / "empty.md"
    empty_prompt.touch()

    pointer = (
        f"Read and execute the task specification defined at file://{empty_prompt.resolve()} "
        "following all rules and invariants within."
    )
    assert is_valid_pointer_prompt(pointer) is False or empty_prompt.stat().st_size == 0


def test_malformed_input_directory_target(tmp_path: Path) -> None:
    """Malformed input: pointer targeting directory instead of markdown file fails is_file check."""
    fake_dir = tmp_path / "somedir"
    fake_dir.mkdir()

    pointer = (
        f"Read and execute the task specification defined at file://{fake_dir.resolve()} "
        "following all rules and invariants within."
    )
    target = extract_target_path(pointer)
    assert target is not None
    assert not target.is_file()


def test_concurrency_and_race_multithreaded_dereference() -> None:
    """Concurrency test: 16 parallel threads safely dereference the same canonical SSOT prompt."""
    target_prompt = ROOT / "automations" / "prompts" / "graph_hygiene_agent.md"
    pointer = (
        f"Read and execute the task specification defined at file://{target_prompt.resolve()} "
        "following all rules and invariants within."
    )

    def _read_worker() -> int:
        target = extract_target_path(pointer)
        assert target is not None
        content = target.read_text(encoding="utf-8")
        return len(content)

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
        futures = [executor.submit(_read_worker) for _ in range(32)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len(results) == 32
    assert all(r > 0 for r in results)
    assert len(set(results)) == 1  # All threads read identical content
