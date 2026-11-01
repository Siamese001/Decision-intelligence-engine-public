#!/usr/bin/env bash
# ==============================================================================
# Governed Document Ingestion Wrapper: scripts/ingest_document.sh
# Enforces Master Invariant #10 & Canonical Stage U0 Document Ingestion Contract.
# ==============================================================================
set -euo pipefail

function show_help() {
  cat << 'EOF'
Usage: ./scripts/ingest_document.sh [OPTIONS]

Required Options:
  --input, -i       Path to document file (.pdf, .docx, .txt, .md, .png, etc.)
  --project, -p     Governed project name (e.g. project_nexus, project_lens, nea)
  --meeting, -m     Document title or subject
  --date, -d        Document date (MM/DD/YYYY or MM-DD-YYYY)

Optional Options:
  --author          Document author or sender
  --classification  Classification tier (default: restricted)
  --help, -h        Show this help message

Any additional unrecognized options will be forwarded directly to 'nee ingest-document'.
EOF
}

INPUT=""
PROJECT=""
TITLE=""
DATE=""
AUTHOR=""
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --input|-i)
      INPUT="$2"
      shift 2
      ;;
    --project|-p)
      PROJECT="$2"
      shift 2
      ;;
    --meeting|-m|--title)
      TITLE="$2"
      shift 2
      ;;
    --date|-d)
      DATE="$2"
      shift 2
      ;;
    --author)
      AUTHOR="$2"
      shift 2
      ;;
    --help|-h)
      show_help
      exit 0
      ;;
    *)
      EXTRA_ARGS+=("$1")
      shift
      ;;
  esac
done

# Validate required inputs
if [[ -z "${INPUT}" ]]; then
  echo "Error: Missing required argument --input <file_path>" >&2
  exit 1
fi
if [[ ! -f "${INPUT}" ]]; then
  echo "Error: Input file does not exist: '${INPUT}'" >&2
  exit 1
fi
if [[ -z "${PROJECT}" ]]; then
  echo "Error: Missing required argument --project <project_name>" >&2
  exit 1
fi
if [[ -z "${TITLE}" ]]; then
  echo "Error: Missing required argument --title/--meeting <document_title>" >&2
  exit 1
fi
if [[ -z "${DATE}" ]]; then
  echo "Error: Missing required argument --date <MM-DD-YYYY>" >&2
  exit 1
fi

# Locate repo root and resolve 'nee' binary
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"
export PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}"

# Verify main working tree is clean before running ingestion to prevent dirty root contamination
CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
IS_WORKTREE="false"
if [[ "$(git rev-parse --git-dir 2>/dev/null)" == *".git/worktrees/"* ]]; then
  IS_WORKTREE="true"
fi

if [[ "${CURRENT_BRANCH}" == "main" && "${IS_WORKTREE}" != "true" ]]; then
  DIRTY_FILES="$(git status --porcelain 2>/dev/null || true)"
  if [[ -n "${DIRTY_FILES}" ]]; then
    echo "Error: Primary 'main' working tree has uncommitted modifications or untracked files." >&2
    echo "Document ingestion requires a clean main tree or execution from inside a governed worktree." >&2
    echo "Dirty entries:" >&2
    echo "${DIRTY_FILES}" >&2
    exit 1
  fi
fi

NEE_BIN=""
if [[ -x "${REPO_ROOT}/.venv/bin/nee" ]]; then
  NEE_BIN="${REPO_ROOT}/.venv/bin/nee"
elif command -v nee &>/dev/null; then
  NEE_BIN="$(command -v nee)"
else
  NEE_BIN="python3 ${REPO_ROOT}/nee.py"
fi

# Execute canonical governed document ingestion
exec ${NEE_BIN} ingest-document \
  --input "${INPUT}" \
  --project "${PROJECT}" \
  --title "${TITLE}" \
  --date "${DATE}" \
  ${AUTHOR:+--author "${AUTHOR}"} \
  "${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}"
