#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path

root=Path(__file__).resolve().parents[1]
raise SystemExit(subprocess.call([sys.executable,str(root/'migrations/0009_v54_edo_authority_boundary/migration.py'),'rollback',*sys.argv[1:]]))
