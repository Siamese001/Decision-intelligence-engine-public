"""
Adversarial Evaluation Package.

Exposes CanaryEngine, PromptInjectionDefenseTester, and SyntheticAdversarialGenerator.
"""

from __future__ import annotations

from .canary import CanaryEngine, CanaryLeakageReport, CanaryToken
from .generator import SyntheticAdversarialGenerator
from .prompt_injector import (
    InjectionDefenseVerdict,
    PromptInjectionDefenseTester,
)

__all__ = [
    "CanaryEngine",
    "CanaryLeakageReport",
    "CanaryToken",
    "InjectionDefenseVerdict",
    "PromptInjectionDefenseTester",
    "SyntheticAdversarialGenerator",
]
