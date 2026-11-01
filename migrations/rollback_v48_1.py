#!/usr/bin/env python3
import argparse
import importlib.util
import json
from pathlib import Path

p=argparse.ArgumentParser(); p.add_argument('--database',type=Path,required=True); p.add_argument('--package-root',type=Path,required=True); a=p.parse_args()
path=a.package_root/'migrations'/'0002_v48_1_conformance'/'migration.py'; spec=importlib.util.spec_from_file_location('v481migration',path); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
print(json.dumps(m.rollback(a.database,a.package_root),indent=2,sort_keys=True))
