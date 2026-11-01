#!/bin/sh
set -eu

# Shared Git hook helper: resolves repository root and python interpreter across worktrees
ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
COMMON_GIT_DIR=$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null || echo "$ROOT/.git")
COMMON_ROOT=${COMMON_GIT_DIR%/*}

# Unset Git hook environment variables so nested git commands operate hermetically
unset GIT_DIR GIT_WORK_TREE GIT_PREFIX GIT_INDEX_FILE GIT_COMMON_DIR

run_python() {
  if [ -f "$ROOT/.venv/Scripts/python.exe" ]; then
    "$ROOT/.venv/Scripts/python.exe" "$@"
  elif [ -x "$ROOT/.venv/bin/python" ]; then
    "$ROOT/.venv/bin/python" "$@"
  elif [ -f "$COMMON_ROOT/.venv/Scripts/python.exe" ]; then
    "$COMMON_ROOT/.venv/Scripts/python.exe" "$@"
  elif [ -x "$COMMON_ROOT/.venv/bin/python" ]; then
    "$COMMON_ROOT/.venv/bin/python" "$@"
  elif command -v python3 >/dev/null 2>&1; then
    python3 "$@"
  elif command -v py >/dev/null 2>&1; then
    py -3 "$@"
  else
    python "$@"
  fi
}
