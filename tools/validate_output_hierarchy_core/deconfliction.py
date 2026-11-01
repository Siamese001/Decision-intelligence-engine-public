"""
Duplicate meeting collision detection for output hierarchy governance.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional


def _is_override_authorized(container: Path) -> bool:
    """Checks if container has documented duplicate override in metadata.json."""
    meta_p = container / "metadata.json"
    if meta_p.exists():
        try:
            d = json.loads(meta_p.read_text(encoding="utf-8"))
            return bool(d.get("allow_duplicate") and d.get("collision_override_rationale"))
        except Exception:
            pass
    return False


def detect_meeting_duplicates(
    meeting_subfolders: List[Path],
    proj_name: str,
    meeting_date: str,
    date_dir: Path,
    target_slug: Optional[str] = None,
) -> List[str]:
    """Audits sibling subfolders for duplicate meeting collisions."""
    violations: List[str] = []
    if len(meeting_subfolders) <= 1:
        return violations

    try:
        from dual_pipeline.meeting_deconfliction import MeetingCollisionDetector

        repo_root = date_dir.parents[2] if len(date_dir.parents) >= 3 else None
        detector = MeetingCollisionDetector(repo_root=repo_root)

        for i in range(len(meeting_subfolders)):
            for j in range(i + 1, len(meeting_subfolders)):
                sa, sb = meeting_subfolders[i], meeting_subfolders[j]
                if target_slug and sa.name != target_slug and sb.name != target_slug:
                    continue
                if _is_override_authorized(sa) or _is_override_authorized(sb):
                    continue

                tb, attb, txtb = detector._read_sibling_context(sb)
                res = detector.check_candidate_collision(
                    project=proj_name,
                    date_str=meeting_date,
                    candidate_slug=sb.name,
                    candidate_time=tb,
                    candidate_attendees=attb,
                    candidate_text=txtb,
                )
                if res.is_collision and res.existing_slug == sa.name:
                    violations.append(
                        f"[UNRESOLVED_DUPLICATE_MEETING_PAIR] Date '{proj_name}/{meeting_date}' contains "
                        f"colliding duplicate meeting subfolders '{sa.name}' and '{sb.name}' "
                        f"({res.confidence * 100:.0f}% confidence). Reasons: {'; '.join(res.reasons[:2])}"
                    )
    except Exception:
        pass

    return violations
