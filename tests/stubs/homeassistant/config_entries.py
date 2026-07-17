"""Minimal config entry stubs for local testing."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class ConfigEntryState(Enum):
    """Subset of Home Assistant config entry lifecycle states."""

    LOADED = "loaded"


ConfigFlowResult = Dict[str, Any]


@dataclass
class ConfigEntry:
    """Simplified config entry model."""

    entry_id: str = "test-entry"
    title: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    options: Dict[str, Any] = field(default_factory=dict)
    domain: str = ""
    unique_id: str | None = None
    source: str = "user"
    discovery_keys: Dict[str, Any] = field(default_factory=dict)
    version: int = 1
    minor_version: int = 1
    subentries_data: tuple[Any, ...] = ()

    def __class_getitem__(cls, item):
        return cls

    def async_on_unload(self, callback):
        return callback


class _FlowBase:
    """Shared flow helpers."""

    def async_show_form(self, *, step_id: str, data_schema=None, errors=None, description_placeholders=None):
        return {
            "type": "form",
            "step_id": step_id,
            "data_schema": data_schema,
            "errors": errors or {},
            "description_placeholders": description_placeholders or {},
        }

    def async_create_entry(self, *, title: str, data: Dict[str, Any]):
        return {"type": "create_entry", "title": title, "data": data}

    def async_abort(self, *, reason: str):
        return {"type": "abort", "reason": reason}


class ConfigFlow(_FlowBase):
    """Config flow stub."""

    MINOR_VERSION = 1

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__()

    async def async_set_unique_id(self, unique_id: str):
        self.unique_id = unique_id

    def _abort_if_unique_id_configured(self):
        return None


class OptionsFlow(_FlowBase):
    """Options flow stub."""

    def __init__(self, config_entry: Optional[ConfigEntry] = None) -> None:
        self.config_entry = config_entry
