"""Integration-specific exceptions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


class LobeHubError(RuntimeError):
    """Base class for all integration errors."""


class ValidationError(LobeHubError):
    """Raised when local configuration or arguments are invalid."""


@dataclass
class ApiError(LobeHubError):
    """Raised when the remote LobeHub API returns a non-success status."""

    status_code: int
    message: str
    payload: Optional[Any] = None

    def __str__(self) -> str:
        return f"{self.status_code}: {self.message}"

