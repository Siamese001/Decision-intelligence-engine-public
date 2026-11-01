#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

from migration import apply

p=argparse.ArgumentParser(); p.add_argument('--database',type=Path,required=True); p.add_argument('--package-root',type=Path,required=True)
a=p.parse_args(); print(json.dumps(apply(a.database,a.package_root),indent=2,sort_keys=True))
