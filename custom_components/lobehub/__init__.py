"""Home Assistant entry point for the LobeHub integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_DEFAULT_RUNTIME,
    DEFAULT_RUNTIME,
    DOMAIN,
    SYNC_INTERVAL,
)
from .entry_state import (
    build_options_with_runtime_state,
    get_persisted_binding,
    get_persisted_conversation,
)
from .exceptions import ApiError
from .models import AgentBinding, ConversationState, IntegrationConfig
from .runtime import LobeHubRuntime, build_runtime
from .services import async_register_services

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
_PLATFORMS: list[Platform] = [Platform.CONVERSATION]

type LobeHubConfigEntry = ConfigEntry[LobeHubRuntime]


def _persist_runtime_state(entry: LobeHubConfigEntry) -> dict[str, object]:
    """Persist the single-agent runtime state into config-entry options."""

    return build_options_with_runtime_state(entry.options, entry.runtime_data)


def _load_runtime_from_entry(
    entry: LobeHubConfigEntry,
) -> tuple[
    LobeHubRuntime,
    AgentBinding,
    ConversationState | None,
    dict[str, object] | None,
    dict[str, object] | None,
]:
    """Load the local runtime inputs from one config entry."""

    data = entry.data or {}
    runtime_config = IntegrationConfig(
        api_key=data[CONF_API_KEY],
        base_url=data[CONF_BASE_URL],
        default_runtime=data.get(CONF_DEFAULT_RUNTIME, DEFAULT_RUNTIME),
    )
    runtime = build_runtime(runtime_config)

    binding, binding_item = get_persisted_binding(entry)
    if binding is None:
        raise ConfigEntryError("No LobeHub agent is configured")

    conversation, conversation_item = get_persisted_conversation(entry)
    if conversation is not None and conversation.agent_id != binding.agent_id:
        conversation = None
    return runtime, binding, conversation, binding_item, conversation_item


def _validate_and_configure_runtime(runtime: LobeHubRuntime) -> None:
    """Run startup validation off the event loop."""

    runtime.integration.client.validate_auth()


async def _async_sync_runtime(hass: HomeAssistant, entry: LobeHubConfigEntry) -> None:
    """Refresh runtime state without reloading the config entry."""

    if entry.state is not ConfigEntryState.LOADED:
        return

    runtime = entry.runtime_data
    try:
        changed = await hass.async_add_executor_job(runtime.refresh_remote_state)
    except ApiError as err:
        if runtime.is_remote_agent_missing(err):
            _LOGGER.warning(
                "Removing LobeHub entry %s because agent %s no longer exists remotely",
                entry.entry_id,
                runtime.agent_id,
            )
            await hass.config_entries.async_remove(entry.entry_id)
            return
        _LOGGER.exception("LobeHub periodic sync failed for entry %s", entry.entry_id)
        return
    except Exception:
        _LOGGER.exception("LobeHub periodic sync failed for entry %s", entry.entry_id)
        return

    if changed:
        hass.config_entries.async_update_entry(
            entry,
            options=_persist_runtime_state(entry),
        )


@callback
def _async_remove_stale_devices(hass: HomeAssistant, entry: LobeHubConfigEntry) -> None:
    """Remove leftover device entries from older LobeHub entity versions."""

    device_registry = dr.async_get(hass)
    for device_entry in dr.async_entries_for_config_entry(
        device_registry, entry.entry_id
    ):
        device_registry.async_remove_device(device_entry.id)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the domain."""

    # Services are domain-wide and outlive individual config entries. Let the
    # registration helper fill in any missing services after an integration
    # reload or an in-place upgrade.
    async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: LobeHubConfigEntry) -> bool:
    """Set up a config entry."""

    try:
        (
            runtime,
            binding,
            conversation,
            binding_item,
            conversation_item,
        ) = await hass.async_add_executor_job(_load_runtime_from_entry, entry)
    except KeyError as err:
        raise ConfigEntryError("Missing required LobeHub config entry data") from err

    try:
        await hass.async_add_executor_job(runtime.configure, binding, conversation)
        await hass.async_add_executor_job(_validate_and_configure_runtime, runtime)
    except ApiError as err:
        if runtime.is_remote_agent_missing(err):
            _LOGGER.warning(
                "Removing LobeHub entry %s because agent %s no longer exists remotely",
                entry.entry_id,
                binding.agent_id,
            )
            hass.async_create_task(hass.config_entries.async_remove(entry.entry_id))
            return False
        _LOGGER.exception(
            "LobeHub startup validation failed for base_url=%s: %s",
            entry.data.get(CONF_BASE_URL),
            err,
        )
        raise ConfigEntryNotReady("Unable to connect to LobeHub") from err
    except Exception as err:
        _LOGGER.exception(
            "LobeHub startup validation failed for base_url=%s: %s",
            entry.data.get(CONF_BASE_URL),
            err,
        )
        raise ConfigEntryNotReady("Unable to connect to LobeHub") from err

    entry.runtime_data = runtime
    _async_remove_stale_devices(hass, entry)

    changed = (
        runtime.dump_binding() != binding_item
        or runtime.dump_conversation() != conversation_item
    )
    if changed:
        hass.config_entries.async_update_entry(
            entry,
            options=_persist_runtime_state(entry),
        )

    @callback
    def _schedule_runtime_sync(now) -> None:
        del now
        hass.async_create_task(_async_sync_runtime(hass, entry))

    entry.async_on_unload(
        async_track_time_interval(
            hass,
            _schedule_runtime_sync,
            SYNC_INTERVAL,
        )
    )

    await hass.config_entries.async_forward_entry_setups(entry, _PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LobeHubConfigEntry) -> bool:
    """Unload a config entry."""

    return await hass.config_entries.async_unload_platforms(entry, _PLATFORMS)
