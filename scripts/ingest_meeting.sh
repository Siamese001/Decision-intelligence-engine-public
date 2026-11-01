#!/usr/bin/env bash
# ==============================================================================
# Governed Ingestion Wrapper: scripts/ingest_meeting.sh
# Enforces Master Invariant #10 & Canonical Stage U0 Ingestion Contract.
# ==============================================================================
set -euo pipefail

function show_help() {
  cat << 'EOF'
Usage: ./scripts/ingest_meeting.sh [OPTIONS]

Required Options:
  --input, -i       Path to raw transcript or audio file (.txt, .md, .docx, .vtt, etc.)
  --project, -p     Governed project name (e.g. project_nexus, project_lens, nea)
  --meeting, -m     Meeting title / scope (e.g. LENS_CatchUp_Mandy_Jen)
  --date, -d        Meeting date (MM/DD/YYYY or MM-DD-YYYY)

Optional Options:
  --attendees, -a   Attendee roster (comma-separated or JSON string)
  --time, -t        Meeting time range (e.g. '03:00 PM - 03:30 PM ET')
  --help, -h        Show this help message

Any additional unrecognized options will be forwarded directly to 'nee ingest-meeting'.
EOF
}

INPUT=""
PROJECT=""
TITLE=""
DATE=""
ATTENDEES=""
TIME_VAL=""
DIRECTIVE_ID=""
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
    --attendees|-a)
      ATTENDEES="$2"
      shift 2
      ;;
    --time|-t)
      TIME_VAL="$2"
      shift 2
      ;;
    --directive-id)
      DIRECTIVE_ID="$2"
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
  echo "Error: Missing required argument --meeting <meeting_title>" >&2
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

if [[ -f "${REPO_ROOT}/env_di" ]]; then
  set -a
  source "${REPO_ROOT}/env_di"
  set +a
fi

NEE_BIN=""
if [[ -x "${REPO_ROOT}/.venv/bin/nee" ]]; then
  NEE_BIN="${REPO_ROOT}/.venv/bin/nee"
elif command -v nee &>/dev/null; then
  NEE_BIN="$(command -v nee)"
else
  NEE_BIN="python3 ${REPO_ROOT}/nee.py"
fi

# Execute canonical governed meeting ingestion
exec ${NEE_BIN} ingest-meeting \
  --input "${INPUT}" \
  --project "${PROJECT}" \
  --meeting "${TITLE}" \
  --date "${DATE}" \
  ${TIME_VAL:+--time "${TIME_VAL}"} \
  ${ATTENDEES:+--attendees "${ATTENDEES}"} \
  ${DIRECTIVE_ID:+--directive-id "${DIRECTIVE_ID}"} \
  "${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}"
