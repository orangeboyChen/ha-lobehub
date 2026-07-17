"""Minimal Home Assistant exception types."""


class HomeAssistantError(Exception):
    """Base Home Assistant error."""


class ConfigEntryError(HomeAssistantError):
    """Invalid config entry."""


class ConfigEntryNotReady(ConfigEntryError):
    """Config entry is temporarily unavailable."""
