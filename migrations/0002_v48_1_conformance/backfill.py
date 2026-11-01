#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

from migration import validate

p=argparse.ArgumentParser(); p.add_argument('--database',type=Path,required=True); p.add_argument('--package-root',type=Path,required=True)
a=p.parse_args(); r=validate(a.database,a.package_root); r['backfill_status']='integrated_in_forward_migration'; print(json.dumps(r,indent=2,sort_keys=True))
