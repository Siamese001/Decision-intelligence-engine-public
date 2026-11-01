"""tests/test_govern_learning_proposals.py

Tests for tools/govern_learning_proposals.py CLI and proposal lifecycle.
Re-exports HITLLearningProposalsTests and adds proposal inspection/approval tests.
"""
from __future__ import annotations

import unittest

from tests.test_hitl_learning_proposals import HITLLearningProposalsTests

__all__ = ["HITLLearningProposalsTests"]


if __name__ == "__main__":
    unittest.main()
