#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
cmd=[sys.executable,str(ROOT/'migrations'/'0004_v49_external_evidence_gap_routing'/'migration.py'),'rollback',*sys.argv[1:]]
raise SystemExit(subprocess.call(cmd))
