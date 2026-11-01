#!/usr/bin/env python3
import runpy
import sys
from pathlib import Path

p=Path(__file__).resolve().parent/'0003_v48_2_partner_advisory_nea_partnership'/'migration.py'
sys.argv=[str(p),'validate',*sys.argv[1:]]
runpy.run_path(str(p),run_name='__main__')
