"""Backward-compatibility facade for dual_pipeline.llm_judge_core.archive -> judges."""
from __future__ import annotations

import sys
from . import judges

# Allow imports like: from dual_pipeline.llm_judge_core.archive.graph import GraphExtractionJudge
sys.modules[__name__ + ".enterprise"] = judges.enterprise
sys.modules[__name__ + ".forensic"] = judges.forensic
sys.modules[__name__ + ".graph"] = judges.graph
sys.modules[__name__ + ".holistic"] = judges.holistic
sys.modules[__name__ + ".leverage"] = judges.leverage
sys.modules[__name__ + ".recap"] = judges.recap

from .judges import *  # noqa: F401, F403
