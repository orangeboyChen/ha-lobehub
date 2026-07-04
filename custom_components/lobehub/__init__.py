"""Home Assistant entry point for the LobeHub integration."""

from __future__ import annotations

from typing import Any, Iterable, List

from .const import (
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_DEFAULT_RUNTIME,
    CONF_SELECTED_AGENTS,
    DEFAULT_RUNTIME,
    DOMAIN,
    PLATFORMS,
)
from .models import IntegrationConfig
from .runtime import (
    LobeHubRuntime,
    binding_from_data,
    conversation_from_data,
    build_runtime,
)
from .services import async_register_services


async def async_setup(hass: Any, config: Any) -> bool:
    """Set up the domain."""

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault("services_registered", False)
    return True


def _load_runtime_from_entry(entry: Any) -> LobeHubRuntime:
    data = entry.data or {}
    options = entry.options or {}
    runtime_config = IntegrationConfig(
        api_key=data[CONF_API_KEY],
        base_url=data[CONF_BASE_URL],
        default_runtime=data.get(CONF_DEFAULT_RUNTIME, DEFAULT_RUNTIME),
    )
    runtime = build_runtime(runtime_config)

    conversations = options.get("conversations") or data.get("conversations") or []
    by_agent = {conversation["agent_id"]: conversation for conversation in conversations if conversation.get("agent_id")}
    for agent_id, conversation_data in by_agent.items():
        runtime.conversations_by_agent[agent_id] = conversation_from_data(conversation_data)

    selected_agents = options.get(CONF_SELECTED_AGENTS) or data.get(CONF_SELECTED_AGENTS) or []
    bindings = [binding_from_data(item) for item in selected_agents]
    runtime.configure_agents(bindings)

    return runtime


async def async_setup_entry(hass: Any, entry: Any) -> bool:
    """Set up a config entry."""

    runtime = _load_runtime_from_entry(entry)
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["runtime"] = runtime

    if not hass.data[DOMAIN].get("services_registered"):
        async_register_services(hass)
        hass.data[DOMAIN]["services_registered"] = True

    if hasattr(hass, "config_entries") and hasattr(hass.config_entries, "async_forward_entry_setups"):
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: Any, entry: Any) -> bool:
    """Unload a config entry."""

    if hasattr(hass, "config_entries") and hasattr(hass.config_entries, "async_unload_platforms"):
        await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    hass.data.get(DOMAIN, {}).pop("runtime", None)
    return True
