"""Deterministic contract validation issue and error types."""
from __future__ import annotations

from dataclasses import dataclass


class ContractError(ValueError):
    """Base class for deterministic contract failures."""


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    path: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


class ValidationFailure(ContractError):
    def __init__(self, issue: ValidationIssue):
        self.issue = issue
        super().__init__(f"{issue.code} at {issue.path}: {issue.message}")


def fail(code: str, path: str, message: str) -> None:
    raise ValidationFailure(ValidationIssue(code=code, path=path, message=message))

