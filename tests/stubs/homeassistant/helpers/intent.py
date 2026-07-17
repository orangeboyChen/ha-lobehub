"""Minimal intent response types for conversation tests."""

from __future__ import annotations

from enum import Enum


class IntentResponseErrorCode(Enum):
    """Error code subset."""

    FAILED_TO_HANDLE = "failed_to_handle"


class IntentResponse:
    """Captures speech or errors set by a conversation agent."""

    def __init__(self, *, language: str) -> None:
        self.language = language
        self.speech: str | None = None
        self.error: tuple[IntentResponseErrorCode, str] | None = None

    def async_set_speech(self, speech: str) -> None:
        self.speech = speech

    def async_set_error(self, code: IntentResponseErrorCode, message: str) -> None:
        self.error = (code, message)
