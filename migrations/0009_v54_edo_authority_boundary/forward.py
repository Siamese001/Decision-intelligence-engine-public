#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path

raise SystemExit(subprocess.call([sys.executable,str(Path(__file__).with_name('migration.py')),'forward',*sys.argv[1:]]))
