#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path

root=Path(__file__).resolve().parents[1]
raise SystemExit(subprocess.call([sys.executable,str(root/'migrations/0006_v51_identity_role_standards/migration.py'),'forward',*sys.argv[1:]]))
