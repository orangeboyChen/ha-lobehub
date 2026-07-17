"""Minimal entity base class for tests."""

from __future__ import annotations

from typing import Any, Dict


class Entity:
    """Small subset of Home Assistant Entity."""

    _attr_has_entity_name = False

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

    async def async_added_to_hass(self) -> None:
        return None

    async def async_will_remove_from_hass(self) -> None:
        return None

    def async_write_ha_state(self) -> None:
        return None
