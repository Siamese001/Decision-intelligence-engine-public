"""
Regex patterns for speaker turn parsing.
"""

from __future__ import annotations

import re
from typing import List, Tuple

# Ordered patterns supporting numbered turns, annotations, timestamps before/after, bold, and unbolded formats
PATTERNS: List[Tuple[str, re.Pattern]] = [
    # 0a. Bold timestamp before speaker: **[00:01:10]** **Speaker:** or **[00:01:10]** Speaker:
    ("bold_ts_outside", re.compile(
        r"^(?P<num_prefix>(?:\d+\.\s+|[-*]\s+)?)(?P<ts_before>\*\*\[[0-9]{2}:[0-9]{2}(?::[0-9]{2})?(?:\s*[-–—]\s*[0-9]{2}:[0-9]{2}(?::[0-9]{2})?)?\]\*\*\s*)(?P<open_stars>\*\*)?\[?(?P<speaker>[^:\n*\]]+)\]?(?:\*\*)?(?P<colon>:\*\*|\*\*:?|:)\s*(?P<text>.*)$"
    )),
    # 0. Timestamp before bold speaker: [00:01:10] **Drew Oakley**: or [00:01:10 - 00:01:25] **[Speaker]**: text
    ("ts_outside_bold", re.compile(
        r"^(?P<num_prefix>(?:\d+\.\s+|[-*]\s+)?)(?P<ts_before>\[[0-9]{2}:[0-9]{2}(?::[0-9]{2})?(?:\s*[-–—]\s*[0-9]{2}:[0-9]{2}(?::[0-9]{2})?)?\]\s*)(?P<open_stars>\*\*)\[?(?P<speaker>[^:\n*\]]+)\]?(?:\*\*)?(?P<colon>:\*\*|\*\*:?|:)\s*(?P<text>.*)$"
    )),
    # 1. Annotated with colon: 21. **NEA/APEX (room)** *[self-identified: Sharon Diaram]*: text
    ("annotated", re.compile(
        r"^(?P<num_prefix>(?:\d+\.\s+|[-*]\s+)?)(?P<open_stars>\*\*)(?P<speaker>[^:\n*]+)\*\*\s*(?P<annotation>\*\[[^\]]+\]\*)\s*(?P<colon>:\s*)(?P<text>.*)$"
    )),
    # 2. Numbered with colon inside bold: 1. **NEA/APEX (room):** text
    ("colon_inside_stars", re.compile(
        r"^(?P<num_prefix>(?:\d+\.\s+|[-*]\s+)?)(?P<open_stars>\*\*)(?P<speaker>[^:\n*]+)(?P<colon>:\*\*\s*)(?P<text>.*)$"
    )),
    # 3. Bold speaker with timestamp after: **Drew Oakley** [00:01:10]: text
    ("bold_ts_after", re.compile(
        r"^(?P<num_prefix>(?:\d+\.\s+|[-*]\s+)?)(?P<open_stars>\*\*)(?P<speaker>[^:\n*]+)\*\*\s*(?P<ts_after>\[[0-9]{2}:[0-9]{2}(?::[0-9]{2})?\]|[0-9]{2}:[0-9]{2}(?::[0-9]{2})?)(?P<colon>\s*:\s*)(?P<text>.*)$"
    )),
    # 4. Bold with timestamp before: **[00:01:10] Drew Oakley**: text or **[00:01:10] Drew Oakley:** text
    ("bold_ts_before", re.compile(
        r"^(?P<num_prefix>(?:\d+\.\s+|[-*]\s+)?)(?P<open_stars>\*\*)(?P<ts_before>\[[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?\]\s+|[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?\s+)(?P<speaker>[^:\n*]+)(?P<colon>\*\*\s*:\s*|:\*\*\s*)(?P<text>.*)$"
    )),
    # 5. Bold speaker with colon: **Drew Oakley:** or **Drew Oakley**: text
    ("bold_colon", re.compile(
        r"^(?P<num_prefix>(?:\d+\.\s+|[-*]\s+)?)(?P<open_stars>\*\*)(?P<speaker>[^:\n*]+)\*\*(?P<colon>\s*:\s*)(?P<text>.*)$"
    )),
    # 6. Unbolded turn with optional timestamps: [00:01:10] Drew Oakley: or Drew Oakley [00:01:10]: or Drew Oakley:
    ("unbolded", re.compile(
        r"^(?P<num_prefix>(?:\d+\.\s+|[-*]\s+)?)(?:(?P<ts_before>\[[0-9]{2}:[0-9]{2}(?::[0-9]{2})?\]\s+|[0-9]{2}:[0-9]{2}(?::[0-9]{2})?\s+))?(?P<speaker>[A-Za-z0-9/ ()\-_]+?)(?:(?P<ts_after>\s+\[[0-9]{2}:[0-9]{2}(?::[0-9]{2})?\]|\s+[0-9]{2}:[0-9]{2}(?::[0-9]{2})?))?(?P<colon>\s*:\s*)(?P<text>.*)$"
    )),
]


def format_replaced_turn(orig_turn: dict, new_speaker: str) -> str:
    """Surgical replacement preserving prefix, numbering, timestamp positioning, colon, and spoken text."""
    num_prefix = orig_turn.get("num_prefix", "")
    open_stars = orig_turn.get("open_stars", "")
    ts_before = orig_turn.get("ts_before", "")
    ts_after = orig_turn.get("ts_after", "")
    annotation = orig_turn.get("annotation", "")
    colon = orig_turn.get("colon", ": ")
    text = orig_turn["text"]
    ptype = orig_turn.get("pattern_type")

    if ptype == "annotated":
        return f"{num_prefix}{open_stars}{new_speaker}** {annotation}{colon}{text}\n"
    if ptype == "colon_inside_stars":
        return f"{num_prefix}{open_stars}{new_speaker}{colon}{text}\n"
    if ptype == "bold_ts_after":
        return f"{num_prefix}{open_stars}{new_speaker}** {ts_after}{colon}{text}\n"
    if ptype == "bold_ts_before":
        return f"{num_prefix}{open_stars}{ts_before}{new_speaker}{colon}{text}\n"
    if ptype == "bold_colon":
        return f"{num_prefix}{open_stars}{new_speaker}**{colon}{text}\n"
    if ptype == "ts_outside_bold":
        return f"{num_prefix}{ts_before}**{new_speaker}:** {text}\n"
    return f"{num_prefix}{ts_before}{new_speaker}{ts_after}{colon}{text}\n"

