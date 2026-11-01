"""CLI command handler for `nee answer-eval`."""
from __future__ import annotations

import argparse
from typing import Any, Dict

from dual_pipeline.evals.answer_eval import run_answer_eval


def handle_answer_eval(args: argparse.Namespace) -> Dict[str, Any]:
    """Handles `nee answer-eval` invocation."""
    return run_answer_eval(args)
