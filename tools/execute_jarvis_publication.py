#!/usr/bin/env python3
"""Governed GraphDB publication for JARVIS STANDUP under project_jarvis."""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

worktree_root = Path(__file__).resolve().parent.parent
if str(worktree_root) not in sys.path:
    sys.path.insert(0, str(worktree_root))

from dual_pipeline.transcript_pipeline_core.release_runner import publish_release_to_graph

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def execute_publication() -> dict[str, str]:
    root = Path(".").resolve()
    base_dir = root / "output/project_jarvis/10-09-2026/Jarvis_Standup_Jason_PartnerAdvisory"
    package_path = (base_dir / "intake_package.json").resolve()
    metadata_path = (base_dir / "metadata.json").resolve()
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    written = {
        "01_transcript": str(
            (
                base_dir
                / "01_Jarvis_Standup_Jason_PartnerAdvisory_10-09-2026_0900AM0930AMET_hitl_resolved_transcript.md"
            ).resolve()
        ),
        "02_recap": str(
            (
                base_dir
                / "02_Jarvis_Standup_Jason_PartnerAdvisory_10-09-2026_0900AM0930AMET_executive_recap.md"
            ).resolve()
        ),
        "03_graph": str(
            (
                base_dir
                / "03_Jarvis_Standup_Jason_PartnerAdvisory_10-09-2026_0900AM0930AMET_canonical_signal_projection.json"
            ).resolve()
        ),
        "intake_package": str(package_path),
    }

    os.environ.setdefault("NEE_REVIEWER", "Amit Ayer")
    directive_id = "directive-project_jarvis-jarvis_standup-10-09-2026-v2"
    reviewer = "Amit Ayer"
    rationale = "Ingest JARVIS STANDUP into project_jarvis canonical knowledge graph"
    embedding_backend = "bge-m3-dev"

    logger.info("Executing publish_release_to_graph for directive: %s", directive_id)
    pub_res = publish_release_to_graph(
        package_path=package_path,
        metadata=metadata,
        written=written,
        directive_id=directive_id,
        reviewer=reviewer,
        rationale=rationale,
        embedding_backend=embedding_backend,
    )
    logger.info("Publication completed: %s", pub_res)
    return pub_res


if __name__ == "__main__":
    result = execute_publication()
    print(json.dumps(result, indent=2, default=str))
