"""Helpers for config entry state persistence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from homeassistant.config_entries import ConfigEntry

from .const import CONF_CONVERSATION, CONF_SELECTED_AGENT
from .models import AgentBinding, ConversationState
from .runtime import LobeHubRuntime, load_binding_item, load_conversation_item


def get_persisted_binding(
    entry: ConfigEntry,
) -> tuple[AgentBinding | None, dict[str, object] | None]:
    """Return the persisted binding plus its raw stored payload."""

    raw_binding = cast(
        dict[str, object] | None,
        entry.options.get(CONF_SELECTED_AGENT) or entry.data.get(CONF_SELECTED_AGENT),
    )
    return load_binding_item(raw_binding), raw_binding


def get_persisted_conversation(
    entry: ConfigEntry,
) -> tuple[ConversationState | None, dict[str, object] | None]:
    """Return the persisted conversation plus its raw stored payload."""

    raw_conversation = cast(
        dict[str, object] | None,
        entry.options.get(CONF_CONVERSATION) or entry.data.get(CONF_CONVERSATION),
    )
    return load_conversation_item(raw_conversation), raw_conversation


def build_options_with_runtime_state(
    current_options: Mapping[str, Any],
    runtime: LobeHubRuntime,
) -> dict[str, object]:
    """Merge the runtime state into config entry options."""

    options = dict(current_options)
    options[CONF_SELECTED_AGENT] = runtime.dump_binding()
    options[CONF_CONVERSATION] = runtime.dump_conversation()
    return options
