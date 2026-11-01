#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path

root=Path(__file__).resolve().parents[1]
raise SystemExit(subprocess.call([sys.executable,str(root/'migrations/0008_v53_sherpa_eval_evidence/migration.py'),'validate',*sys.argv[1:]]))
