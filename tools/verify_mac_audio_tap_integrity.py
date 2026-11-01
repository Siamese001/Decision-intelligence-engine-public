#!/usr/bin/env python3
"""
tools/verify_mac_audio_tap_integrity.py

Cryptographic integrity verifier for mac_audio_tap.
Verifies that:
1. tools/mac_audio_tap/mac_audio_tap.swift matches the sealed source_sha256 in mac_audio_tap.manifest.json.
2. If tools/mac_audio_tap/bin/mac_audio_tap exists, its binary SHA-256 matches manifest binary_sha256.
3. On Darwin, asserts that mac_audio_tap --source-hash outputs the manifest source_sha256 and unrecognized flags exit with code 2 in <100ms.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path


def compute_normalized_sha256(path: Path) -> str:
    content = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(content).hexdigest()


def compute_binary_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_integrity(repo_root: Path) -> tuple[bool, str]:
    tap_dir = repo_root / "tools" / "mac_audio_tap"
    swift_source = tap_dir / "mac_audio_tap.swift"
    manifest_path = tap_dir / "mac_audio_tap.manifest.json"
    bin_path = tap_dir / "bin" / "mac_audio_tap"

    if not swift_source.exists():
        return False, f"Missing Swift source: {swift_source}"

    if not manifest_path.exists():
        return False, f"Missing manifest file: {manifest_path}. Run python tools/build_mac_audio_tap.py."

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"Malformed manifest JSON: {exc}"

    expected_source_hash = manifest.get("source_sha256")
    if not expected_source_hash:
        return False, "Manifest missing 'source_sha256' field."

    actual_source_hash = compute_normalized_sha256(swift_source)
    if actual_source_hash != expected_source_hash:
        return (
            False,
            f"DRIFT_DETECTED: Swift source hash mismatch!\n"
            f"  Actual:   {actual_source_hash}\n"
            f"  Manifest: {expected_source_hash}\n"
            f"Recompile and re-seal via: python tools/build_mac_audio_tap.py",
        )

    if bin_path.exists():
        expected_bin_hash = manifest.get("binary_sha256")
        if not expected_bin_hash:
            return False, "Manifest missing 'binary_sha256' field while binary exists."
        actual_bin_hash = compute_binary_sha256(bin_path)
        if actual_bin_hash != expected_bin_hash:
            return (
                False,
                f"DRIFT_DETECTED: Binary hash mismatch!\n"
                f"  Actual:   {actual_bin_hash}\n"
                f"  Manifest: {expected_bin_hash}\n"
                f"Recompile and re-seal via: python tools/build_mac_audio_tap.py",
            )

        if platform.system() == "Darwin":
            # Test self-reported hash
            try:
                p_hash = subprocess.run(
                    [str(bin_path), "--source-hash"],
                    capture_output=True,
                    text=True,
                    timeout=1.0,
                )
                if p_hash.returncode != 0:
                    return False, f"Binary --source-hash failed (exit {p_hash.returncode}): {p_hash.stderr}"
                if p_hash.stdout.strip() != expected_source_hash:
                    return (
                        False,
                        f"Binary self-reported hash mismatch:\n"
                        f"  Reported: {p_hash.stdout.strip()}\n"
                        f"  Expected: {expected_source_hash}",
                    )
            except subprocess.TimeoutExpired:
                return False, "Binary --source-hash timed out (>1.0s)."
            except Exception as e:
                return False, f"Binary invocation error: {e}"

            # Test fail-closed argument parser
            try:
                p_invalid = subprocess.run(
                    [str(bin_path), "--adversarial-invalid-flag"],
                    capture_output=True,
                    text=True,
                    timeout=1.0,
                )
                if p_invalid.returncode != 2:
                    return False, f"Binary failed to fail-closed on unknown flag; expected code 2, got {p_invalid.returncode}"
            except subprocess.TimeoutExpired:
                return False, "Binary hung indefinitely on unknown CLI flag!"
            except Exception as e:
                return False, f"Binary argument check error: {e}"

    return True, "mac_audio_tap cryptographic integrity verified."


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify mac_audio_tap cryptographic integrity")
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[1], help="Repository root directory")
    args = parser.parse_args()

    ok, msg = verify_integrity(args.repository_root)
    if ok:
        print(f"[INTEGRITY_OK] {msg}")
        return 0
    else:
        print(f"[INTEGRITY_FAIL] {msg}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
