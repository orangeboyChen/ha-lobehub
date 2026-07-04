"""Minimal sensor entity stub for local tests."""

from __future__ import annotations

from typing import Any, Dict


class SensorEntity:
    """Minimal Home Assistant SensorEntity."""

    _attr_has_entity_name = False
    _attr_icon = None

    def __init__(self) -> None:
        self.hass = None
        self._attr_unique_id = None

    @property
    def unique_id(self):
        return self._attr_unique_id

    @property
    def name(self):
        return None

    @property
    def native_value(self):
        return None

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        return {}

