#!/usr/bin/env python3
"""Verify the pinned NEE CPU runtime without loading the BGE-M3 model."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "requirements" / "runtime-direct.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()

    expected = json.loads(CONTRACT.read_text(encoding="utf-8"))
    issues: list[str] = []
    compute_contract = expected.get("compute", {})
    if (
        compute_contract.get("platform") != "cpu"
        or compute_contract.get("wheel_channel") != "cpu"
        or compute_contract.get("device") != "cpu"
    ):
        issues.append("runtime compute contract must select CPU execution")
    python_contract = expected["python"]
    if platform.python_implementation() != python_contract["implementation"]:
        issues.append("Python implementation does not match the runtime contract")
    if sys.version_info[:2] != (python_contract["major"], python_contract["minor"]):
        issues.append("Python must be CPython 3.12")
    if platform.architecture()[0] != python_contract["architecture"]:
        issues.append("Python must be a 64-bit build")

    installed: dict[str, str] = {}
    for distribution, required_version in expected["distributions"].items():
        try:
            actual_version = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            issues.append(f"Missing distribution: {distribution}")
            continue
        installed[distribution] = actual_version
        if actual_version != required_version:
            # On macOS (Darwin), PyPI distribution wheels omit the '+cpu' local identifier suffix
            if (
                distribution == "torch"
                and sys.platform == "darwin"
                and required_version.endswith("+cpu")
                and actual_version == required_version[:-4]
            ):
                pass
            else:
                issues.append(
                    f"{distribution} version mismatch: expected {required_version}, "
                    f"got {actual_version}"
                )

    compute: dict[str, object] = {"device": "cpu"}
    try:
        import torch

        probe = torch.ones(4, device="cpu")
        compute.update(
            {
                "available": True,
                "torch_version": str(torch.__version__),
                "tensor_probe": float(probe.sum().item()),
                "thread_count": int(torch.get_num_threads()),
            }
        )
        if compute["tensor_probe"] != 4.0:
            issues.append("CPU tensor probe returned an unexpected result")
    except ImportError:
        compute["available"] = False
        issues.append("torch could not be imported")

    report = {
        "status": "PASS" if not issues else "FAIL",
        "contract": str(CONTRACT),
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "architecture": platform.architecture()[0],
        },
        "installed_direct_dependencies": installed,
        "compute": compute,
        "issues": issues,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
