import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List


def _extract_record(line: str, project: str, source_name: str, lane: str) -> Dict[str, Any]:
    record_id = hashlib.sha256(line.encode()).hexdigest()[:16]
    return {
        "record_id": record_id,
        "text": line,
        "project": project,
        "source_file": source_name,
        "lane": lane,
    }


def _process_recap_file(md_file: Path) -> List[Dict[str, Any]]:
    project = md_file.parent.parent.name
    content = md_file.read_text(encoding="utf-8")
    records: List[Dict[str, Any]] = []
    state = "OTHER"

    for line in content.split("\n"):
        line = line.strip()
        if not line:
            continue

        if line.startswith("# Key Discussion Points"):
            state = "KEY_POINTS"
            continue
        if line.startswith("# Action & Governance Ledger"):
            state = "ACTION_LEDGER"
            continue
        if line.startswith("# "):
            state = "OTHER"
            continue

        if state == "KEY_POINTS" and line.startswith("- **"):
            records.append(_extract_record(line, project, md_file.name, "key_point"))
        elif state == "ACTION_LEDGER" and line.startswith("|") and not line.startswith("| #") and not line.startswith("|---"):
            records.append(_extract_record(line, project, md_file.name, "action_item"))

    return records


def harvest() -> None:
    root = Path("../..").resolve()
    output_dir = root / "output"
    pool: List[Dict[str, Any]] = []

    for md_file in output_dir.rglob("02_*_executive_recap.md"):
        pool.extend(_process_recap_file(md_file))

    out_file = Path("evals") / "authentic_candidate_pool.json"
    out_file.parent.mkdir(exist_ok=True, parents=True)
    out_file.write_text(json.dumps(pool, indent=2))
    print(f"Harvested {len(pool)} candidates to {out_file}")


if __name__ == "__main__":
    harvest()
