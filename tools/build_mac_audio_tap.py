#!/usr/bin/env python3
"""
tools/build_mac_audio_tap.py

Automates compilation of native ScreenCaptureKit audio tap binary (mac_audio_tap),
embeds the canonical source SHA-256 and version into build_metadata.swift,
and seals the cryptographic manifest mac_audio_tap.manifest.json.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

VERSION = "1.2.0"


def compute_normalized_sha256(path: Path) -> str:
    content = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(content).hexdigest()


def compute_binary_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    tap_dir = repo_root / "tools" / "mac_audio_tap"
    swift_source = tap_dir / "mac_audio_tap.swift"
    metadata_swift = tap_dir / "build_metadata.swift"
    bin_dir = tap_dir / "bin"
    output_bin = bin_dir / "mac_audio_tap"
    manifest_path = tap_dir / "mac_audio_tap.manifest.json"

    if platform.system() != "Darwin":
        print(f"[BUILD_ABORT] Native compilation requires macOS Darwin; running on {platform.system()}.", file=sys.stderr)
        return 1

    if not swift_source.exists():
        print(f"[BUILD_ERROR] Source file missing: {swift_source}", file=sys.stderr)
        return 1

    # 1. Compute canonical source hash
    source_sha256 = compute_normalized_sha256(swift_source)
    print(f"[*] Canonical source SHA-256: {source_sha256}")

    # 2. Generate build_metadata.swift
    metadata_content = (
        "// tools/mac_audio_tap/build_metadata.swift\n"
        "// Generated automatically by tools/build_mac_audio_tap.py - DO NOT EDIT MANUALLY\n"
        f'let EMBEDDED_SOURCE_SHA256 = "{source_sha256}"\n'
        f'let EMBEDDED_VERSION = "{VERSION}"\n'
    )
    metadata_swift.write_text(metadata_content, encoding="utf-8")
    print(f"[*] Generated build metadata: {metadata_swift.name}")

    # 3. Ensure bin directory exists
    bin_dir.mkdir(parents=True, exist_ok=True)

    # 4. Invoke swiftc compiler
    swiftc_bin = "/usr/bin/swiftc"
    if not Path(swiftc_bin).exists():
        res = subprocess.run(["which", "swiftc"], capture_output=True, text=True)
        swiftc_bin = res.stdout.strip() or "swiftc"

    cmd = [
        swiftc_bin,
        "-O",
        str(swift_source),
        str(metadata_swift),
        "-o",
        str(output_bin),
    ]
    print(f"[*] Compiling: {' '.join(cmd)}")
    compile_res = subprocess.run(cmd, capture_output=True, text=True)
    if compile_res.returncode != 0:
        print(f"[BUILD_ERROR] swiftc compilation failed:\n{compile_res.stderr}", file=sys.stderr)
        return compile_res.returncode

    # 5. Set executable permissions
    os.chmod(output_bin, 0o755)

    # 6. Compute binary hash
    binary_sha256 = compute_binary_sha256(output_bin)
    print(f"[*] Compiled binary SHA-256: {binary_sha256}")

    # 7. Write cryptographic manifest
    manifest_data = {
        "$schema": "urn:nee:mac-audio-tap:manifest:1.0.0",
        "version": VERSION,
        "source_file": "tools/mac_audio_tap/mac_audio_tap.swift",
        "source_sha256": source_sha256,
        "metadata_file": "tools/mac_audio_tap/build_metadata.swift",
        "binary_file": "tools/mac_audio_tap/bin/mac_audio_tap",
        "binary_sha256": binary_sha256,
        "target_triple": "arm64-apple-macos13.0",
        "compiler_flags": ["-O"],
        "cli_flags": ["--probe", "--source-hash", "--version"],
        "sealed_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path.write_text(json.dumps(manifest_data, indent=2) + "\n", encoding="utf-8")
    print(f"[*] Sealed manifest: {manifest_path}")

    # 8. Smoke tests
    print("[*] Running post-compilation smoke tests...")
    # 8a. Test --version
    p_ver = subprocess.run([str(output_bin), "--version"], capture_output=True, text=True, timeout=2.0)
    assert p_ver.returncode == 0, f"--version failed: {p_ver.stderr}"
    assert VERSION in p_ver.stdout, f"--version output mismatch: {p_ver.stdout}"

    # 8b. Test --source-hash
    p_hash = subprocess.run([str(output_bin), "--source-hash"], capture_output=True, text=True, timeout=2.0)
    assert p_hash.returncode == 0, f"--source-hash failed: {p_hash.stderr}"
    assert p_hash.stdout.strip() == source_sha256, f"--source-hash mismatch: {p_hash.stdout.strip()} != {source_sha256}"

    # 8c. Test unknown flag fails closed with exit code 2
    p_unknown = subprocess.run([str(output_bin), "--unknown-flag-test"], capture_output=True, text=True, timeout=2.0)
    assert p_unknown.returncode == 2, f"Unknown flag should exit with code 2, got {p_unknown.returncode}"

    print("[SUCCESS] mac_audio_tap built, verified, and sealed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
