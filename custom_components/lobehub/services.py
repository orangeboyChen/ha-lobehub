"""Service helpers for the LobeHub Home Assistant integration."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING, Any, cast

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.target import (
    TargetSelection,
    async_extract_referenced_entity_ids,
)

from .const import (
    CONF_ASSIGNEE_AGENT_ID,
    CONF_BOUND_DEVICE_ID,
    CONF_CONTEXT,
    CONF_CONTINUE_TOPIC_ID,
    CONF_INSTRUCTION,
    CONF_LIMIT,
    CONF_MESSAGE,
    CONF_MODEL,
    CONF_OFFSET,
    CONF_PARENT_TASK,
    CONF_PREVIOUS_RESPONSE_ID,
    CONF_PROMPT,
    CONF_PROVIDER,
    CONF_RUNTIME,
    CONF_STATUSES,
    CONF_TASK,
    CONF_TOPIC_ID,
    CONF_TOPIC_POLICY,
    CONF_TOPIC_TITLE,
    DOMAIN,
    SERVICE_GET_TASK,
    SERVICE_LIST_AGENTS,
    SERVICE_LIST_DEVICES,
    SERVICE_LIST_TASKS,
    SERVICE_NEW_TOPIC,
    SERVICE_RUN_SAVED_TASK,
    SERVICE_RUN_TASK,
    SERVICE_SEND_MESSAGE,
    SERVICE_SWITCH_TOPIC,
    SERVICE_UPDATE_AGENT_SETTINGS,
)
from .entry_state import build_options_with_runtime_state
from .models import normalize_runtime

if TYPE_CHECKING:
    from . import LobeHubConfigEntry

_TASK_STATUSES = (
    "backlog",
    "running",
    "scheduled",
    "paused",
    "completed",
    "failed",
    "canceled",
)


_TARGET_SELECTOR_SCHEMA = {
    # Home Assistant injects target selectors into ServiceCall.data before
    # validating the service schema. Keep these explicit so misspelled service
    # fields are still rejected instead of being silently ignored.
    vol.Optional("entity_id"): vol.Any(str, [str]),
    vol.Optional("device_id"): vol.Any(str, [str]),
    vol.Optional("area_id"): vol.Any(str, [str]),
    vol.Optional("floor_id"): vol.Any(str, [str]),
    vol.Optional("label_id"): vol.Any(str, [str]),
}


def _service_schema(fields: dict[object, object]) -> vol.Schema:
    """Allow Home Assistant target selectors alongside service fields."""

    return vol.Schema({**fields, **_TARGET_SELECTOR_SCHEMA})


SEND_MESSAGE_SCHEMA = _service_schema(
    {
        vol.Required(CONF_MESSAGE): str,
        vol.Optional(CONF_CONTEXT): dict,
    }
)

NEW_TOPIC_SCHEMA = _service_schema(
    {
        vol.Required(CONF_TOPIC_TITLE): str,
    }
)

SWITCH_TOPIC_SCHEMA = _service_schema(
    {
        vol.Required(CONF_TOPIC_ID): str,
    }
)

RUN_TASK_SCHEMA = _service_schema(
    {
        vol.Required(CONF_INSTRUCTION): str,
        vol.Optional(CONF_CONTEXT): dict,
        vol.Optional(CONF_PREVIOUS_RESPONSE_ID): str,
    }
)

LIST_TASKS_SCHEMA = _service_schema(
    {
        vol.Optional(CONF_ASSIGNEE_AGENT_ID): str,
        vol.Optional(CONF_LIMIT, default=50): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=100)
        ),
        vol.Optional(CONF_OFFSET, default=0): vol.All(
            vol.Coerce(int), vol.Range(min=0)
        ),
        vol.Optional(CONF_PARENT_TASK): str,
        vol.Optional(CONF_STATUSES): [vol.In(_TASK_STATUSES)],
    }
)

GET_TASK_SCHEMA = _service_schema(
    {
        vol.Required(CONF_TASK): str,
    }
)

RUN_SAVED_TASK_SCHEMA = _service_schema(
    {
        vol.Required(CONF_TASK): str,
        vol.Optional(CONF_CONTINUE_TOPIC_ID): str,
        vol.Optional(CONF_PROMPT): str,
    }
)

LIST_AGENTS_SCHEMA = _service_schema({})

LIST_DEVICES_SCHEMA = _service_schema({})

UPDATE_AGENT_SETTINGS_SCHEMA = _service_schema(
    {
        vol.Optional(CONF_MODEL): str,
        vol.Optional(CONF_PROVIDER): str,
        vol.Optional(CONF_RUNTIME): str,
        vol.Optional(CONF_BOUND_DEVICE_ID): str,
        vol.Optional(CONF_TOPIC_POLICY): vol.In(("reuse", "new")),
    }
)


def _register_service(
    hass: HomeAssistant,
    service_name: str,
    schema: vol.Schema,
    handler: Callable[[ServiceCall], Any],
) -> None:
    """Register one service unless it is already available."""

    if hass.services.has_service(DOMAIN, service_name):
        return

    hass.services.async_register(
        DOMAIN,
        service_name,
        handler,
        schema=schema,
        # Responses are useful to dashboards and scripts, but these actions
        # must remain callable from automations that do not request one.
        supports_response=SupportsResponse.OPTIONAL,
    )


def _resolve_target_entries(
    hass: HomeAssistant,
    call: ServiceCall,
) -> list[tuple[str, LobeHubConfigEntry]]:
    entries = [
        cast(LobeHubConfigEntry, entry)
        for entry in hass.config_entries.async_loaded_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]
    if not entries:
        raise HomeAssistantError("LobeHub runtime is not initialized")

    target_selection = TargetSelection(call.data)
    if not target_selection.has_any_target:
        if len(entries) != 1:
            raise HomeAssistantError(
                "Target a LobeHub conversation entity when multiple entries are configured"
            )
        return [(entries[0].entry_id, entries[0])]

    entity_registry = er.async_get(hass)
    referenced = async_extract_referenced_entity_ids(hass, target_selection)
    entity_ids = sorted(referenced.referenced | referenced.indirectly_referenced)
    resolved: list[tuple[str, LobeHubConfigEntry]] = []
    resolved_entry_ids: set[str] = set()

    for entity_id in entity_ids:
        # Service metadata constrains targets in the UI, but service calls can
        # also be assembled directly. Only conversation entities are valid.
        if not entity_id.startswith("conversation."):
            continue
        entity_entry = entity_registry.async_get(entity_id)
        if entity_entry is None or entity_entry.config_entry_id is None:
            continue
        config_entry = hass.config_entries.async_get_entry(entity_entry.config_entry_id)
        if (
            config_entry is None
            or config_entry.domain != DOMAIN
            or config_entry.state is not ConfigEntryState.LOADED
        ):
            continue
        if config_entry.entry_id in resolved_entry_ids:
            continue
        resolved_entry_ids.add(config_entry.entry_id)
        resolved.append((entity_id, cast(LobeHubConfigEntry, config_entry)))

    if not resolved:
        raise HomeAssistantError("No targeted LobeHub conversation entity was found")

    return resolved


def _format_response(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {"results": results}


def _resolve_single_entry(
    hass: HomeAssistant,
    call: ServiceCall,
    *,
    service_name: str,
) -> tuple[str, LobeHubConfigEntry]:
    """Resolve exactly one LobeHub config entry for workspace-scoped services."""

    resolved = _resolve_target_entries(hass, call)
    if len(resolved) != 1:
        raise HomeAssistantError(
            f"Target exactly one LobeHub conversation entity when calling {service_name}"
        )
    return resolved[0]


def _binding_state(entry: LobeHubConfigEntry) -> dict[str, Any]:
    binding = entry.runtime_data.agent_binding
    return {
        "agent_id": entry.runtime_data.agent_id,
        "model": binding.model if binding else None,
        "provider": binding.provider if binding else None,
        "runtime": binding.runtime if binding else None,
        "bound_device_id": binding.bound_device_id if binding else None,
        "workspace_id": binding.workspace_id if binding else None,
    }


def async_register_services(hass: HomeAssistant) -> None:
    """Register domain services on the Home Assistant instance."""

    async def handle_send_message(call: ServiceCall) -> dict[str, Any]:
        responses: list[dict[str, Any]] = []
        for target_id, entry in _resolve_target_entries(hass, call):
            conversation, payload = await hass.async_add_executor_job(
                partial(
                    entry.runtime_data.send_conversation_message,
                    call.data[CONF_MESSAGE],
                    conversation_id=None,
                    context=call.data.get(CONF_CONTEXT),
                )
            )
            hass.config_entries.async_update_entry(
                entry,
                options=build_options_with_runtime_state(
                    entry.options, entry.runtime_data
                ),
            )
            responses.append(
                {
                    "target": target_id,
                    "conversation_id": conversation.id,
                    "topic_id": conversation.id,
                    "agent_id": entry.runtime_data.agent_id,
                    "response": payload.get("final_output_text", ""),
                    "raw": payload,
                }
            )
        return _format_response(responses)

    async def handle_new_topic(call: ServiceCall) -> dict[str, Any]:
        responses: list[dict[str, Any]] = []
        for target_id, entry in _resolve_target_entries(hass, call):
            conversation = await hass.async_add_executor_job(
                entry.runtime_data.new_topic,
                call.data[CONF_TOPIC_TITLE],
            )
            hass.config_entries.async_update_entry(
                entry,
                options=build_options_with_runtime_state(
                    entry.options, entry.runtime_data
                ),
            )
            responses.append(
                {
                    "target": target_id,
                    "conversation_id": conversation.id,
                    "topic_id": conversation.id,
                    "agent_id": entry.runtime_data.agent_id,
                    "title": conversation.title,
                }
            )
        return _format_response(responses)

    async def handle_switch_topic(call: ServiceCall) -> dict[str, Any]:
        responses: list[dict[str, Any]] = []
        for target_id, entry in _resolve_target_entries(hass, call):
            conversation = await hass.async_add_executor_job(
                entry.runtime_data.switch_topic,
                call.data[CONF_TOPIC_ID],
            )
            hass.config_entries.async_update_entry(
                entry,
                options=build_options_with_runtime_state(
                    entry.options, entry.runtime_data
                ),
            )
            responses.append(
                {
                    "target": target_id,
                    "conversation_id": conversation.id,
                    "topic_id": conversation.id,
                    "agent_id": entry.runtime_data.agent_id,
                    "title": conversation.title,
                }
            )
        return _format_response(responses)

    async def handle_run_task(call: ServiceCall) -> dict[str, Any]:
        responses: list[dict[str, Any]] = []
        for target_id, entry in _resolve_target_entries(hass, call):
            result = await hass.async_add_executor_job(
                partial(
                    entry.runtime_data.run_task,
                    call.data[CONF_INSTRUCTION],
                    context=call.data.get(CONF_CONTEXT),
                    previous_response_id=call.data.get(CONF_PREVIOUS_RESPONSE_ID),
                )
            )
            hass.config_entries.async_update_entry(
                entry,
                options=build_options_with_runtime_state(
                    entry.options, entry.runtime_data
                ),
            )
            responses.append(
                {
                    "target": target_id,
                    **_binding_state(entry),
                    "response_id": result.response_id,
                    "assistant_message_id": result.assistant_message_id,
                    "operation_id": result.operation_id,
                    "topic_id": result.topic_id,
                    "output_text": result.output_text,
                    "status": result.status,
                    "raw": result.raw,
                }
            )
        return _format_response(responses)

    async def handle_update_agent_settings(call: ServiceCall) -> dict[str, Any]:
        target_id, entry = _resolve_single_entry(
            hass,
            call,
            service_name=SERVICE_UPDATE_AGENT_SETTINGS,
        )
        binding = entry.runtime_data.agent_binding
        if binding is None:
            raise HomeAssistantError("No LobeHub agent is configured")

        model = (
            str(call.data[CONF_MODEL]).strip() or None
            if CONF_MODEL in call.data
            else binding.model
        )
        provider = (
            str(call.data[CONF_PROVIDER]).strip() or None
            if CONF_PROVIDER in call.data
            else binding.provider
        )
        runtime = normalize_runtime(call.data.get(CONF_RUNTIME), binding.runtime)
        if runtime is None:
            raise HomeAssistantError("Unsupported execution target")
        bound_device_id = (
            str(call.data[CONF_BOUND_DEVICE_ID]).strip() or None
            if CONF_BOUND_DEVICE_ID in call.data
            else binding.bound_device_id
        )
        topic_policy = str(call.data.get(CONF_TOPIC_POLICY) or binding.topic_policy)

        updated_binding = await hass.async_add_executor_job(
            partial(
                entry.runtime_data.update_agent_settings,
                model=model,
                provider=provider,
                runtime=runtime,
                bound_device_id=bound_device_id,
                topic_policy=topic_policy,
            )
        )
        hass.config_entries.async_update_entry(
            entry,
            options=build_options_with_runtime_state(entry.options, entry.runtime_data),
        )
        return _format_response(
            [
                {
                    "target": target_id,
                    "agent_id": updated_binding.agent_id,
                    "model": updated_binding.model,
                    "provider": updated_binding.provider,
                    "runtime": updated_binding.runtime,
                    "bound_device_id": updated_binding.bound_device_id,
                    "topic_policy": updated_binding.topic_policy,
                    "workspace_id": updated_binding.workspace_id,
                }
            ]
        )

    async def handle_list_tasks(call: ServiceCall) -> dict[str, Any]:
        target_id, entry = _resolve_single_entry(
            hass,
            call,
            service_name=SERVICE_LIST_TASKS,
        )
        payload = await hass.async_add_executor_job(
            partial(
                entry.runtime_data.list_tasks,
                assignee_agent_id=call.data.get(CONF_ASSIGNEE_AGENT_ID),
                limit=call.data[CONF_LIMIT],
                offset=call.data[CONF_OFFSET],
                parent_task=call.data.get(CONF_PARENT_TASK),
                statuses=call.data.get(CONF_STATUSES),
            )
        )
        return _format_response(
            [
                {
                    "target": target_id,
                    **_binding_state(entry),
                    "tasks": payload.get("tasks", []),
                    "total": payload.get("total", 0),
                    "raw": payload.get("raw", payload),
                }
            ]
        )

    async def handle_list_agents(call: ServiceCall) -> dict[str, Any]:
        target_id, entry = _resolve_single_entry(
            hass,
            call,
            service_name=SERVICE_LIST_AGENTS,
        )
        payload = await hass.async_add_executor_job(entry.runtime_data.list_agents)
        return _format_response(
            [
                {
                    "target": target_id,
                    "agents": payload,
                    "current_agent_id": entry.runtime_data.agent_id,
                    "current_model": entry.runtime_data.agent_binding.model
                    if entry.runtime_data.agent_binding
                    else None,
                    "current_provider": entry.runtime_data.agent_binding.provider
                    if entry.runtime_data.agent_binding
                    else None,
                    "current_runtime": entry.runtime_data.agent_binding.runtime
                    if entry.runtime_data.agent_binding
                    else None,
                }
            ]
        )

    async def handle_list_devices(call: ServiceCall) -> dict[str, Any]:
        target_id, entry = _resolve_single_entry(
            hass,
            call,
            service_name=SERVICE_LIST_DEVICES,
        )
        payload = await hass.async_add_executor_job(entry.runtime_data.list_devices)
        return _format_response(
            [
                {
                    "target": target_id,
                    "devices": payload,
                    "current_agent_id": entry.runtime_data.agent_id,
                    "current_runtime": entry.runtime_data.agent_binding.runtime
                    if entry.runtime_data.agent_binding
                    else None,
                    "bound_device_id": entry.runtime_data.agent_binding.bound_device_id
                    if entry.runtime_data.agent_binding
                    else None,
                }
            ]
        )

    async def handle_get_task(call: ServiceCall) -> dict[str, Any]:
        target_id, entry = _resolve_single_entry(
            hass,
            call,
            service_name=SERVICE_GET_TASK,
        )
        payload = await hass.async_add_executor_job(
            entry.runtime_data.get_task,
            call.data[CONF_TASK],
        )
        return _format_response(
            [
                {
                    "target": target_id,
                    **_binding_state(entry),
                    "task": payload.get("task", {}),
                    "topics": payload.get("topics", []),
                    "raw": payload.get("raw", payload),
                }
            ]
        )

    async def handle_run_saved_task(call: ServiceCall) -> dict[str, Any]:
        target_id, entry = _resolve_single_entry(
            hass,
            call,
            service_name=SERVICE_RUN_SAVED_TASK,
        )
        result = await hass.async_add_executor_job(
            partial(
                entry.runtime_data.run_saved_task,
                call.data[CONF_TASK],
                continue_topic_id=call.data.get(CONF_CONTINUE_TOPIC_ID),
                prompt=call.data.get(CONF_PROMPT),
            )
        )
        return _format_response(
            [
                {
                    "target": target_id,
                    **_binding_state(entry),
                    "task_id": result.task_id,
                    "task_identifier": result.task_identifier,
                    "response_id": result.response_id,
                    "assistant_message_id": result.assistant_message_id,
                    "operation_id": result.operation_id,
                    "topic_id": result.topic_id,
                    "output_text": result.output_text,
                    "status": result.status,
                    "raw": result.raw,
                }
            ]
        )

    _register_service(
        hass, SERVICE_SEND_MESSAGE, SEND_MESSAGE_SCHEMA, handle_send_message
    )
    _register_service(hass, SERVICE_NEW_TOPIC, NEW_TOPIC_SCHEMA, handle_new_topic)
    _register_service(
        hass, SERVICE_SWITCH_TOPIC, SWITCH_TOPIC_SCHEMA, handle_switch_topic
    )
    _register_service(hass, SERVICE_RUN_TASK, RUN_TASK_SCHEMA, handle_run_task)
    _register_service(hass, SERVICE_LIST_AGENTS, LIST_AGENTS_SCHEMA, handle_list_agents)
    _register_service(
        hass, SERVICE_LIST_DEVICES, LIST_DEVICES_SCHEMA, handle_list_devices
    )
    _register_service(
        hass,
        SERVICE_UPDATE_AGENT_SETTINGS,
        UPDATE_AGENT_SETTINGS_SCHEMA,
        handle_update_agent_settings,
    )
    _register_service(hass, SERVICE_LIST_TASKS, LIST_TASKS_SCHEMA, handle_list_tasks)
    _register_service(hass, SERVICE_GET_TASK, GET_TASK_SCHEMA, handle_get_task)
    _register_service(
        hass,
        SERVICE_RUN_SAVED_TASK,
        RUN_SAVED_TASK_SCHEMA,
        handle_run_saved_task,
    )
