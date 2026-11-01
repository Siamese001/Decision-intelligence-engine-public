#!/usr/bin/env python3
"""CLI: snapshot a graph database, or prove a mutation between two snapshots.

  prove_graph_mutation.py snapshot --db graph.sqlite --out before.json
  prove_graph_mutation.py prove --before before.json --db graph.sqlite \
      --id asrt_x --delta assertion=1 --probe "SELECT 1 FROM assertion WHERE assertion_id=?" asrt_x

Exit code 0 only when all five checks pass.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from tools.graph_mutation_proof_core import prove, snapshot  # noqa: E402


def _parse_deltas(items: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        table, _, value = item.partition("=")
        out[table] = int(value)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    snap = sub.add_parser("snapshot")
    snap.add_argument("--db", required=True)
    snap.add_argument("--out", required=True)
    prv = sub.add_parser("prove")
    prv.add_argument("--before", required=True)
    prv.add_argument("--db", required=True)
    prv.add_argument("--id", action="append", default=[], dest="ids")
    prv.add_argument("--delta", action="append", default=[])
    prv.add_argument("--probe", nargs=2, action="append", default=[], metavar=("SQL", "PARAM"))
    prv.add_argument("--out")
    args = parser.parse_args(argv)

    if args.cmd == "snapshot":
        Path(args.out).write_text(json.dumps(snapshot(args.db), indent=2), encoding="utf-8")
        print(f"snapshot written: {args.out}")
        return 0

    before = json.loads(Path(args.before).read_text(encoding="utf-8"))
    after = snapshot(args.db, tables=list(before["row_counts"]))
    probes = {f"probe_{i}": (sql, (param,)) for i, (sql, param) in enumerate(args.probe)}
    receipt = prove(before, after, args.ids, _parse_deltas(args.delta), probes)
    text = json.dumps(receipt, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0 if receipt["overall"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
