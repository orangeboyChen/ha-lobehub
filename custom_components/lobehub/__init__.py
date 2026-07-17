"""Home Assistant entry point for the LobeHub integration."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import replace
from typing import Any, cast

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
    CONF_CONVERSATION,
    CONF_DEFAULT_RUNTIME,
    CONF_SELECTED_AGENT,
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
from .models import (
    AgentBinding,
    ConversationState,
    IntegrationConfig,
    binding_from_data,
    binding_to_data,
    conversation_from_data,
    conversation_to_data,
)
from .runtime import LobeHubRuntime, build_runtime
from .services import async_register_services

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
_PLATFORMS: list[Platform] = [Platform.CONVERSATION]

type LobeHubConfigEntry = ConfigEntry[LobeHubRuntime]
_CONFIG_ENTRY_VERSION = 2
_LEGACY_SELECTED_AGENTS = "selected_agents"
_LEGACY_CONVERSATIONS = "conversations"
_SYNC_GROUPS = "sync_groups"


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate legacy multi-agent state into one entry per agent."""

    if entry.version >= _CONFIG_ENTRY_VERSION:
        return True

    raw_bindings = entry.options.get(_LEGACY_SELECTED_AGENTS) or entry.data.get(
        _LEGACY_SELECTED_AGENTS
    )
    if isinstance(raw_bindings, list):
        binding_items = raw_bindings
    else:
        # Version 1 already stored one binding under the current singular key.
        # Treat it as a one-item legacy collection rather than rejecting it.
        raw_binding = entry.options.get(CONF_SELECTED_AGENT) or entry.data.get(
            CONF_SELECTED_AGENT
        )
        binding_items = [raw_binding] if isinstance(raw_binding, dict) else []

    bindings: list[AgentBinding] = []
    seen_agent_ids: set[str] = set()
    for raw_binding in binding_items:
        if not isinstance(raw_binding, dict):
            continue
        binding = binding_from_data(raw_binding)
        if not binding.agent_id or binding.agent_id in seen_agent_ids:
            continue
        seen_agent_ids.add(binding.agent_id)
        bindings.append(binding)
    if not bindings:
        _LOGGER.error("Cannot migrate LobeHub entry %s: no agent bindings", entry.entry_id)
        return False

    raw_conversations = entry.options.get(_LEGACY_CONVERSATIONS) or entry.data.get(
        _LEGACY_CONVERSATIONS
    )
    conversations: dict[str, ConversationState] = {}
    if isinstance(raw_conversations, list):
        for raw_conversation in raw_conversations:
            if not isinstance(raw_conversation, dict):
                continue
            conversation = conversation_from_data(raw_conversation)
            if conversation.agent_id and conversation.id:
                conversations[conversation.agent_id] = conversation
    else:
        raw_conversation = entry.options.get(CONF_CONVERSATION) or entry.data.get(
            CONF_CONVERSATION
        )
        if isinstance(raw_conversation, dict):
            conversation = conversation_from_data(raw_conversation)
            if conversation.agent_id and conversation.id:
                conversations[conversation.agent_id] = conversation

    connection_data = {
        key: value
        for key, value in entry.data.items()
        if key not in {_LEGACY_SELECTED_AGENTS, _LEGACY_CONVERSATIONS, CONF_SELECTED_AGENT}
    }
    base_url = str(connection_data.get(CONF_BASE_URL, ""))
    existing_unique_ids = {
        configured.unique_id
        for configured in hass.config_entries.async_entries(DOMAIN)
        if configured.entry_id != entry.entry_id
    }

    primary = bindings[0]
    primary_unique_id = f"{base_url}::{primary.agent_id}"
    if primary_unique_id in existing_unique_ids:
        _LOGGER.error(
            "Cannot migrate LobeHub entry %s: agent %s is already configured",
            entry.entry_id,
            primary.agent_id,
        )
        return False

    def entry_options(binding: AgentBinding) -> dict[str, object]:
        options = {
            key: value
            for key, value in entry.options.items()
            if key not in {
                _LEGACY_SELECTED_AGENTS,
                _LEGACY_CONVERSATIONS,
                CONF_SELECTED_AGENT,
                CONF_CONVERSATION,
            }
        }
        options[CONF_SELECTED_AGENT] = binding_to_data(binding)
        if conversation := conversations.get(binding.agent_id):
            options[CONF_CONVERSATION] = conversation_to_data(conversation)
        return options

    hass.config_entries.async_update_entry(
        entry,
        data={**connection_data, CONF_SELECTED_AGENT: binding_to_data(primary)},
        options=entry_options(primary),
        title=primary.title or entry.title or "LobeHub",
        unique_id=primary_unique_id,
        version=_CONFIG_ENTRY_VERSION,
    )
    existing_unique_ids.add(primary_unique_id)

    for binding in bindings[1:]:
        unique_id = f"{base_url}::{binding.agent_id}"
        if unique_id in existing_unique_ids:
            _LOGGER.warning("Skipping already configured LobeHub agent %s", binding.agent_id)
            continue
        await hass.config_entries.async_add(
            ConfigEntry(
                version=_CONFIG_ENTRY_VERSION,
                minor_version=entry.minor_version,
                domain=DOMAIN,
                title=binding.title or "LobeHub",
                data={**connection_data, CONF_SELECTED_AGENT: binding_to_data(binding)},
                options=entry_options(binding),
                source=entry.source,
                unique_id=unique_id,
                discovery_keys=entry.discovery_keys,
                subentries_data=(),
            )
        )
        existing_unique_ids.add(unique_id)

    return True


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
    """Refresh all bindings that share this entry's LobeHub connection."""

    connection_key = _connection_key(entry)
    groups = hass.data.setdefault(DOMAIN, {}).get(_SYNC_GROUPS, {})
    group = groups.get(connection_key)
    if group is None:
        return

    await _async_sync_connection(hass, connection_key, group)


def _connection_key(entry: ConfigEntry) -> str:
    """Return an opaque key for entries sharing remote credentials."""

    data = entry.data
    connection = f"{data[CONF_BASE_URL]}\0{data[CONF_API_KEY]}"
    return hashlib.sha256(connection.encode()).hexdigest()


async def _async_sync_connection(
    hass: HomeAssistant,
    connection_key: str,
    group: dict[str, Any],
) -> None:
    """Synchronize one remote Agent list for every entry on a connection."""

    entry_ids = set(group["entry_ids"])
    entries = {
        entry.entry_id: cast(LobeHubConfigEntry, entry)
        for entry in hass.config_entries.async_loaded_entries(DOMAIN)
        if entry.entry_id in entry_ids and entry.state is ConfigEntryState.LOADED
    }
    if not entries:
        return

    representative = next(iter(entries.values()))
    try:
        agents = await hass.async_add_executor_job(
            representative.runtime_data.integration.client.list_agents
        )
    except ApiError:
        _LOGGER.exception("LobeHub periodic sync failed for connection %s", connection_key)
        return
    except Exception:
        _LOGGER.exception("LobeHub periodic sync failed for connection %s", connection_key)
        return

    remote_agents = {agent.id: agent for agent in agents}
    for entry_id, entry in entries.items():
        runtime = entry.runtime_data
        binding = runtime.agent_binding
        if binding is None:
            continue
        summary = remote_agents.get(binding.agent_id)
        if summary is None:
            _LOGGER.warning(
                "Removing LobeHub entry %s because agent %s no longer exists remotely",
                entry_id,
                binding.agent_id,
            )
            await hass.config_entries.async_remove(entry_id)
            continue

        if summary.title and summary.title != binding.title:
            runtime.agent_binding = replace(binding, title=summary.title)
            runtime.integration._agent_binding = runtime.agent_binding
            hass.config_entries.async_update_entry(
                entry,
                options=_persist_runtime_state(entry),
            )


def _register_connection_sync(
    hass: HomeAssistant,
    entry: LobeHubConfigEntry,
) -> Any:
    """Register an entry with its connection's single periodic synchronizer."""

    domain_data = hass.data.setdefault(DOMAIN, {})
    groups = domain_data.setdefault(_SYNC_GROUPS, {})
    connection_key = _connection_key(entry)
    group = groups.get(connection_key)
    if group is None:
        entry_ids: set[str] = set()

        @callback
        def _schedule_connection_sync(now) -> None:
            del now
            hass.async_create_task(_async_sync_connection(hass, connection_key, group))

        group = {
            "entry_ids": entry_ids,
            "unsub": async_track_time_interval(hass, _schedule_connection_sync, SYNC_INTERVAL),
        }
        groups[connection_key] = group

    entry_ids = group["entry_ids"]
    entry_ids.add(entry.entry_id)

    @callback
    def _unregister() -> None:
        entry_ids.discard(entry.entry_id)
        if entry_ids:
            return
        group["unsub"]()
        groups.pop(connection_key, None)

    return _unregister


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

    entry.async_on_unload(_register_connection_sync(hass, entry))

    await hass.config_entries.async_forward_entry_setups(entry, _PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LobeHubConfigEntry) -> bool:
    """Unload a config entry."""

    return await hass.config_entries.async_unload_platforms(entry, _PLATFORMS)
