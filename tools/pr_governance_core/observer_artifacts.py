"""Immutable run-bundle helpers for observer evidence."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict
from uuid import uuid4


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_exclusive(path: Path, payload: Any, json_data: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        if json_data:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
        else:
            stream.write(str(payload))
        stream.flush()
        os.fsync(stream.fileno())


def write_pointer(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    with temp.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def seal_manifest(run_dir: Path, members: tuple[str, ...]) -> Dict[str, Any]:
    manifest = {"manifest_version": "1.0.0", "members": {name: sha256_file(run_dir / name) for name in members}}
    write_exclusive(run_dir / "manifest.json", manifest)
    for name, expected in manifest["members"].items():
        if sha256_file(run_dir / name) != expected:
            raise ValueError(f"run bundle verification failed for {name}")
    return manifest


def recent_merges(repo_root: Path) -> list[dict[str, str]]:
    proc = subprocess.run(["git", "log", "main", "--merges", "-3", "--format=%H%x09%s"], cwd=repo_root, capture_output=True, text=True, check=False, timeout=10)
    if proc.returncode:
        return []
    result = []
    for line in proc.stdout.splitlines():
        sha, separator, subject = line.partition("\t")
        if separator:
            result.append({"sha": sha, "subject": subject})
    return result
