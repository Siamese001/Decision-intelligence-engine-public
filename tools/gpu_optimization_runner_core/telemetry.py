"""Hardware, software, model, and configuration telemetry collection."""
from __future__ import annotations

import hashlib
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional


def compute_file_sha256(path: Path) -> str:
    """Computes SHA-256 hash of a file if it exists, otherwise returns NONE."""
    if not path.is_file():
        return "NONE"
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def get_git_commit(repo_root: Path) -> str:
    """Retrieves current git HEAD commit SHA."""
    try:
        res = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return res.stdout.strip()
    except Exception:
        return "UNKNOWN_COMMIT"


def _detect_chip_name() -> str:
    brand = ""
    try:
        res = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], check=False, capture_output=True, text=True, timeout=5)
        brand = res.stdout.strip()
    except Exception:
        pass
    if brand:
        return brand
    try:
        res_mod = subprocess.run(["sysctl", "-n", "hw.model"], check=False, capture_output=True, text=True, timeout=5)
        return res_mod.stdout.strip() or "Apple Silicon"
    except Exception:
        return "Apple Silicon"


def _detect_gpu_cores() -> Optional[int]:
    try:
        res_sp = subprocess.run(["system_profiler", "SPDisplaysDataType"], check=False, capture_output=True, text=True, timeout=5)
        for line in res_sp.stdout.splitlines():
            if any(k in line for k in ("Total Number of Cores", "GPU Cores", "Cores:")):
                parts = line.split(":")
                if len(parts) == 2:
                    try:
                        return int(parts[1].strip())
                    except ValueError:
                        pass
    except Exception:
        pass
    return None


def _collect_package_versions() -> Dict[str, str]:
    installed_packages: Dict[str, str] = {}
    for pkg_name in ("torch", "mlx", "mlx_lm", "mlx_whisper", "FlagEmbedding", "numpy"):
        try:
            mod = __import__(pkg_name)
            installed_packages[pkg_name] = str(getattr(mod, "__version__", "INSTALLED"))
        except ImportError:
            installed_packages[pkg_name] = "NOT_INSTALLED"
        except Exception as e:
            installed_packages[pkg_name] = f"ERROR: {e}"
    return installed_packages


def collect_hardware_software_telemetry() -> Dict[str, Any]:
    """Collects host hardware architecture and Python package environment telemetry."""
    metal_supported = sys.platform == "darwin"
    chip_name = _detect_chip_name() if metal_supported else "Standard Host"
    gpu_cores = _detect_gpu_cores() if metal_supported else None
    installed_packages = _collect_package_versions()

    return {
        "host": {
            "platform": sys.platform,
            "architecture": platform.machine(),
            "processor": platform.processor(),
            "chip": chip_name,
            "gpu_cores": gpu_cores or 20,
            "metal_supported": metal_supported,
        },
        "software": {
            "python_version": sys.version.split()[0],
            "executable": sys.executable,
            "packages": installed_packages,
        },
    }


def collect_model_config_corpus_digests(repo_root: Path) -> Dict[str, Any]:
    """Collects cryptographic digests of model configurations, policy SSOTs, and corpus manifests."""
    model_policy_path = repo_root / "requirements" / "bge-m3-model.json"
    local_models_path = repo_root / "dual_pipeline" / "config" / "local_models.yaml"

    try:
        from dual_pipeline.index_core.constants import HYBRID_RETRIEVAL_POLICY_DIGEST
    except Exception:
        HYBRID_RETRIEVAL_POLICY_DIGEST = "UNKNOWN"

    return {
        "bge_m3_model_policy_sha256": compute_file_sha256(model_policy_path),
        "local_models_config_sha256": compute_file_sha256(local_models_path),
        "hybrid_retrieval_policy_digest": HYBRID_RETRIEVAL_POLICY_DIGEST,
    }
