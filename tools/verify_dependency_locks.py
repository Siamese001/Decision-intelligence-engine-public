#!/usr/bin/env python3
"""Validate Phase 1 direct pins and hash-locked dependency artifacts."""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "requirements"
PIN = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)", re.MULTILINE)


def normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def pins(path: Path) -> dict[str, str]:
    return {
        normalized(name): version
        for name, version in PIN.findall(path.read_text(encoding="utf-8"))
    }


def validate_lock(path: Path, required: dict[str, str]) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    found = pins(path)
    issues: list[str] = []
    if "--index-url https://pypi.org/simple" not in text:
        issues.append("missing PyPI index declaration")
    if "--extra-index-url https://download.pytorch.org/whl/cpu" not in text:
        issues.append("missing CPU PyTorch wheel index declaration")
    for name, version in required.items():
        actual = found.get(normalized(name))
        if actual != version:
            issues.append(f"{name} expected {version}, found {actual}")
    requirement_lines = [line for line in text.splitlines() if PIN.match(line)]
    hash_count = text.count("--hash=sha256:")
    if hash_count < len(requirement_lines):
        issues.append("one or more resolved requirements lacks an artifact hash")
    if found.get("torch") != "2.11.0+cpu":
        issues.append("lock is not bound to the CPU PyTorch build")
    torch_block = re.search(r"^torch==.*?(?=^[A-Za-z0-9_.-]+==|\Z)", text, re.MULTILINE | re.DOTALL)
    if not torch_block or "# from https://download.pytorch.org/whl/cpu" not in torch_block.group():
        issues.append("torch is not source-annotated to the CPU wheel index")
    return {
        "path": str(path.relative_to(ROOT)),
        "package_count": len(found),
        "hash_count": hash_count,
        "issues": issues,
    }


def main() -> int:
    contract = json.loads((REQUIREMENTS / "runtime-direct.json").read_text(encoding="utf-8"))
    required = contract["distributions"]
    input_pins = pins(REQUIREMENTS / "runtime-cpu.in")
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project_dependencies = {
        normalized(name): version
        for dependency in project["project"]["dependencies"]
        for name, version in PIN.findall(dependency)
    }
    issues = [
        f"runtime input mismatch for {name}"
        for name, version in required.items()
        if input_pins.get(normalized(name)) != version
    ]
    issues.extend(
        f"pyproject dependency mismatch for {name}"
        for name, version in required.items()
        if project_dependencies.get(normalized(name)) != version
    )
    lock_results = [
        validate_lock(REQUIREMENTS / "runtime-cpu.lock.txt", required),
        validate_lock(REQUIREMENTS / "dev.lock.txt", required),
    ]
    for result in lock_results:
        issues.extend(f"{result['path']}: {issue}" for issue in result["issues"])
    report = {
        "status": "PASS" if not issues else "FAIL",
        "direct_pin_count": len(required),
        "locks": lock_results,
        "issues": issues,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
