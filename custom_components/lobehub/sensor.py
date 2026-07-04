"""Conversation sensor for the LobeHub Home Assistant integration."""

from __future__ import annotations

from typing import Any, Iterable, List

from homeassistant.components.sensor import SensorEntity

from .const import DOMAIN
from .runtime import LobeHubRuntime


class LobeHubConversationSensor(SensorEntity):
    """Expose one agent conversation as a sensor entity."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:chat"

    def __init__(self, runtime: LobeHubRuntime, agent_id: str) -> None:
        super().__init__()
        self._runtime = runtime
        self._agent_id = agent_id
        self._attr_unique_id = f"{DOMAIN}_{agent_id}"

    @property
    def name(self) -> str:
        snapshot = self._runtime.snapshot(self._agent_id)
        return snapshot.get("agent_title") or snapshot["agent_id"]

    @property
    def unique_id(self) -> str:
        return self._attr_unique_id

    @property
    def native_value(self) -> Any:
        snapshot = self._runtime.snapshot(self._agent_id)
        return snapshot.get("active_topic_id") or snapshot.get("title")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        snapshot = self._runtime.snapshot(self._agent_id)
        return {
            "agent_id": snapshot["agent_id"],
            "conversation_id": snapshot["conversation_id"],
            "active_topic_id": snapshot["active_topic_id"],
            "model": snapshot["model"],
            "provider": snapshot["provider"],
            "runtime": snapshot["runtime"],
            "title": snapshot["title"],
        }


async def async_setup_entry(hass: Any, entry: Any, async_add_entities) -> None:
    """Set up conversation sensors for the configured agents."""

    runtime = hass.data[DOMAIN]["runtime"]
    entities: List[LobeHubConversationSensor] = [
        LobeHubConversationSensor(runtime, agent_id) for agent_id in runtime.selected_agent_ids
    ]
    async_add_entities(entities)
