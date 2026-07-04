"""Service helpers for the LobeHub Home Assistant integration."""

from __future__ import annotations

from typing import Any, Iterable, Mapping, MutableMapping, Optional

import voluptuous as vol

from .const import (
    CONF_AGENT_ID,
    CONF_CLIENT_ID,
    CONF_CONTEXT,
    CONF_INSTRUCTION,
    CONF_MESSAGE,
    CONF_MODEL,
    CONF_PREVIOUS_RESPONSE_ID,
    CONF_PROVIDER,
    CONF_RUNTIME,
    CONF_STREAM,
    CONF_TOPIC_ID,
    CONF_TOPIC_TITLE,
    CONF_TOOLS,
    DOMAIN,
    SERVICE_NEW_TOPIC,
    SERVICE_RUN_TASK,
    SERVICE_SEND_MESSAGE,
    SERVICE_SWITCH_TOPIC,
)
from .runtime import LobeHubRuntime


SEND_MESSAGE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_AGENT_ID): str,
        vol.Required(CONF_MESSAGE): str,
        vol.Optional(CONF_CLIENT_ID): str,
        vol.Optional(CONF_CONTEXT): dict,
    }
)

NEW_TOPIC_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_AGENT_ID): str,
        vol.Required(CONF_TOPIC_TITLE): str,
    }
)

SWITCH_TOPIC_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_AGENT_ID): str,
        vol.Required(CONF_TOPIC_ID): str,
    }
)

RUN_TASK_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_AGENT_ID): str,
        vol.Required(CONF_INSTRUCTION): str,
        vol.Optional(CONF_CONTEXT): dict,
        vol.Optional(CONF_MODEL): str,
        vol.Optional(CONF_PROVIDER): str,
        vol.Optional(CONF_RUNTIME): str,
        vol.Optional(CONF_PREVIOUS_RESPONSE_ID): str,
        vol.Optional(CONF_STREAM, default=False): bool,
        vol.Optional(CONF_TOOLS): list,
    }
)


def _get_runtime(hass: Any) -> LobeHubRuntime:
    domain_data = hass.data.get(DOMAIN, {})
    runtime = domain_data.get("runtime")
    if runtime is None:
        raise RuntimeError("LobeHub runtime is not initialized")
    return runtime


def _register_service(hass: Any, service_name: str, schema: vol.Schema, handler) -> None:
    services = getattr(hass, "services", None)
    if services is None:
        return
    services.async_register(DOMAIN, service_name, handler, schema=schema)


def async_register_services(hass: Any) -> None:
    """Register domain services on the Home Assistant instance."""

    async def handle_send_message(call: Any) -> dict[str, Any]:
        runtime = _get_runtime(hass)
        data = getattr(call, "data", call)
        return runtime.send_message(
            data[CONF_AGENT_ID],
            data[CONF_MESSAGE],
            context=data.get(CONF_CONTEXT),
            client_id=data.get(CONF_CLIENT_ID),
        )

    async def handle_new_topic(call: Any) -> dict[str, Any]:
        runtime = _get_runtime(hass)
        data = getattr(call, "data", call)
        conversation = runtime.new_topic(data[CONF_AGENT_ID], data[CONF_TOPIC_TITLE])
        return conversation.__dict__

    async def handle_switch_topic(call: Any) -> dict[str, Any]:
        runtime = _get_runtime(hass)
        data = getattr(call, "data", call)
        conversation = runtime.switch_topic(data[CONF_AGENT_ID], data[CONF_TOPIC_ID])
        return conversation.__dict__

    async def handle_run_task(call: Any) -> dict[str, Any]:
        runtime = _get_runtime(hass)
        data = getattr(call, "data", call)
        result = runtime.run_task(
            data[CONF_AGENT_ID],
            data[CONF_INSTRUCTION],
            context=data.get(CONF_CONTEXT),
            model=data.get(CONF_MODEL),
            provider=data.get(CONF_PROVIDER),
            runtime=data.get(CONF_RUNTIME),
            tools=data.get(CONF_TOOLS),
            previous_response_id=data.get(CONF_PREVIOUS_RESPONSE_ID),
            stream=data.get(CONF_STREAM, False),
        )
        return result.__dict__ if hasattr(result, "__dict__") else dict(result)

    _register_service(hass, SERVICE_SEND_MESSAGE, SEND_MESSAGE_SCHEMA, handle_send_message)
    _register_service(hass, SERVICE_NEW_TOPIC, NEW_TOPIC_SCHEMA, handle_new_topic)
    _register_service(hass, SERVICE_SWITCH_TOPIC, SWITCH_TOPIC_SCHEMA, handle_switch_topic)
    _register_service(hass, SERVICE_RUN_TASK, RUN_TASK_SCHEMA, handle_run_task)
