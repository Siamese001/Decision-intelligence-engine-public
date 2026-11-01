#!/usr/bin/env bash
# scripts/test_audio_capture.sh
# Governed diagnostic probe script for macOS CoreAudio / FFmpeg audio capture.
# Invariant: Must always specify -t <seconds> on EVERY output stream and enforce a hard timeout.

set -euo pipefail

DURATION="${1:-3}"
DEVICE="${2:-:0}"
OUT_DIR="$(mktemp -d /tmp/audio_probe_XXXXXX)"

cleanup() {
    local pids
    pids=$(jobs -p 2>/dev/null || true)
    if [[ -n "${pids}" ]]; then
        kill -9 ${pids} 2>/dev/null || true
    fi
    rm -rf "${OUT_DIR}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "=== GOVERNED AUDIO CAPTURE DIAGNOSTIC ==="
echo "Target device: ${DEVICE}"
echo "Duration:      ${DURATION}s"
echo "Temp dir:      ${OUT_DIR}"

MASTER_OUT="${OUT_DIR}/probe_master.wav"
SEGMENT_OUT="${OUT_DIR}/chunk_%04d.wav"

# Hard timeout 30s wrapper guarantees process termination even if FFmpeg hangs on device lock
python3 -c "
import subprocess, sys, time, os, signal, shutil
from pathlib import Path

venv_ffmpeg = Path('.venv/bin/ffmpeg')
ffmpeg_bin = str(venv_ffmpeg) if venv_ffmpeg.exists() else (shutil.which('ffmpeg') or 'ffmpeg')

cmd = [
    ffmpeg_bin, '-y', '-f', 'avfoundation', '-i', '${DEVICE}',
    '-t', '${DURATION}', '-ar', '16000', '-ac', '1', '${MASTER_OUT}',
    '-t', '${DURATION}', '-ar', '16000', '-ac', '1', '-f', 'segment',
    '-segment_time', '1', '-segment_start_number', '0', '-reset_timestamps', '1',
    '${SEGMENT_OUT}'
]

try:
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)
    try:
        _, stderr = proc.communicate(timeout=25.0)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        print('ERROR: Audio probe timed out after 25s (device contention or CoreAudio stall)', file=sys.stderr)
        sys.exit(1)

    if proc.returncode != 0:
        print(f'FFmpeg failed with exit code {proc.returncode}: {stderr.decode(errors=\"replace\")[-300:]}', file=sys.stderr)
        sys.exit(proc.returncode)
except Exception as e:
    print(f'Audio probe execution error: {e}', file=sys.stderr)
    sys.exit(1)
"

echo "✅ Audio probe completed cleanly with zero orphaned subprocesses."
exit 0
