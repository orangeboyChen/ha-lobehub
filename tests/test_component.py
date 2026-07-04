from __future__ import annotations

import asyncio
import json

import pytest

import custom_components.lobehub as lobehub_init
from custom_components.lobehub import async_setup_entry
from custom_components.lobehub import config_flow as lobehub_config_flow
from custom_components.lobehub.const import (
    CONF_AGENT_ALLOW_TASK,
    CONF_AGENT_MODEL,
    CONF_AGENT_PROVIDER,
    CONF_AGENT_RUNTIME,
    CONF_AGENT_TITLE,
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_DEFAULT_RUNTIME,
    CONF_MESSAGE,
    CONF_SELECTED_AGENTS,
    CONF_TOPIC_TITLE,
    DEFAULT_RUNTIME,
    DOMAIN,
    SERVICE_NEW_TOPIC,
    SERVICE_RUN_TASK,
    SERVICE_SEND_MESSAGE,
)
from custom_components.lobehub.client import LobeHubClient
from custom_components.lobehub.integration import LobeHubIntegration
from custom_components.lobehub.models import AgentBinding, IntegrationConfig
from custom_components.lobehub.runtime import build_runtime, binding_to_data
from custom_components.lobehub.sensor import LobeHubConversationSensor
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant


def make_transport(responses):
    calls = []

    def transport(method, url, headers, body):
        calls.append((method, url, headers, body))
        return responses.pop(0)

    return transport, calls


def run(coro):
    return asyncio.run(coro)


def test_config_flow_supports_multi_agent_setup(monkeypatch):
    transport, _ = make_transport(
        [
            (200, {}, b'{"id":"user-1"}'),
            (
                200,
                {},
                b'{"agents":['
                b'{"id":"agent-1","title":"Coffee","model":"gpt-4o-mini","provider":"openai"},'
                b'{"id":"agent-2","title":"Driver","model":"gpt-4o","provider":"openai"}'
                b']}',
            ),
        ]
    )
    client = LobeHubClient(
        IntegrationConfig(api_key="sk-lh-1234567890abcd", base_url="https://lobehub.example"),
        transport=transport,
    )
    monkeypatch.setattr(lobehub_config_flow, "LobeHubClient", lambda config: client)

    flow = lobehub_config_flow.LobeHubConfigFlow()
    result = run(
        flow.async_step_user(
            {
                CONF_BASE_URL: "https://lobehub.example",
                CONF_API_KEY: "sk-lh-1234567890abcd",
                CONF_DEFAULT_RUNTIME: DEFAULT_RUNTIME,
            }
        )
    )
    assert result["type"] == "form"
    assert result["step_id"] == "select_agents"

    result = run(flow.async_step_select_agents({CONF_SELECTED_AGENTS: ["agent-1", "agent-2"]}))
    assert result["type"] == "form"
    assert result["step_id"] == "agent_config"

    result = run(
        flow.async_step_agent_config(
            {
                CONF_AGENT_TITLE: "Coffee",
                CONF_AGENT_MODEL: "gpt-4o-mini",
                CONF_AGENT_PROVIDER: "openai",
                CONF_AGENT_RUNTIME: "gateway",
                CONF_AGENT_ALLOW_TASK: True,
            }
        )
    )
    assert result["type"] == "form"
    assert result["step_id"] == "agent_config"

    result = run(
        flow.async_step_agent_config(
            {
                CONF_AGENT_TITLE: "Driver",
                CONF_AGENT_MODEL: "gpt-4o",
                CONF_AGENT_PROVIDER: "openai",
                CONF_AGENT_RUNTIME: "device",
                CONF_AGENT_ALLOW_TASK: False,
            }
        )
    )
    assert result["type"] == "create_entry"
    assert result["title"] == "LobeHub"
    assert len(result["data"][CONF_SELECTED_AGENTS]) == 2
    assert result["data"][CONF_SELECTED_AGENTS][0]["title"] == "Coffee"
    assert result["data"][CONF_SELECTED_AGENTS][1]["runtime"] == "device"


def test_options_flow_updates_selected_agents(monkeypatch):
    transport, _ = make_transport(
        [
            (
                200,
                {},
                b'{"agents":[{"id":"agent-1","title":"Coffee","model":"gpt-4o-mini","provider":"openai"}]}',
            )
        ]
    )
    client = LobeHubClient(
        IntegrationConfig(api_key="sk-lh-1234567890abcd", base_url="https://lobehub.example"),
        transport=transport,
    )
    monkeypatch.setattr(lobehub_config_flow, "LobeHubClient", lambda config: client)

    entry = ConfigEntry(
        entry_id="entry-1",
        title="LobeHub",
        data={
            CONF_BASE_URL: "https://lobehub.example",
            CONF_API_KEY: "sk-lh-1234567890abcd",
            CONF_DEFAULT_RUNTIME: DEFAULT_RUNTIME,
        },
        options={},
    )
    flow = lobehub_config_flow.LobeHubOptionsFlow(entry)
    result = run(flow.async_step_init())
    assert result["type"] == "form"
    assert result["step_id"] == "select_agents"

    result = run(flow.async_step_init({CONF_SELECTED_AGENTS: ["agent-1"]}))
    assert result["type"] == "form"
    assert result["step_id"] == "agent_config"

    result = run(
        flow.async_step_agent_config(
            {
                CONF_AGENT_TITLE: "Morning coffee",
                CONF_AGENT_MODEL: "gpt-4o-mini",
                CONF_AGENT_PROVIDER: "openai",
                CONF_AGENT_RUNTIME: "gateway",
                CONF_AGENT_ALLOW_TASK: True,
            }
        )
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_SELECTED_AGENTS][0]["title"] == "Morning coffee"


def test_services_drive_conversation_state_and_task(monkeypatch):
    transport, calls = make_transport(
        [
            (200, {}, b'{"id":"user-1"}'),
            (
                200,
                {},
                b'{"agents":[{"id":"agent-1","title":"Coffee","model":"gpt-4o-mini","provider":"openai"}]}',
            ),
            (200, {}, b'{"data":{"id":"topic-1","title":"Coffee chat","agentId":"agent-1"}}'),
            (200, {}, b'{"data":{"id":"topic-2","title":"Buy coffee","agentId":"agent-1"}}'),
            (200, {}, b'{"data":{"id":"message-1"}}'),
            (200, {}, b'{"id":"resp-1","output_text":"done","output":[],"status":"completed"}'),
        ]
    )
    config = IntegrationConfig(
        api_key="sk-lh-1234567890abcd",
        base_url="https://lobehub.example",
    )
    runtime = build_runtime(config, transport=transport)
    assert runtime.integration.validate()["id"] == "user-1"

    monkeypatch.setattr(lobehub_init, "build_runtime", lambda integration_config: runtime)

    hass = HomeAssistant()
    entry = ConfigEntry(
        entry_id="entry-1",
        title="LobeHub",
        data={
            CONF_BASE_URL: "https://lobehub.example",
            CONF_API_KEY: "sk-lh-1234567890abcd",
            CONF_DEFAULT_RUNTIME: DEFAULT_RUNTIME,
            CONF_SELECTED_AGENTS: [
                binding_to_data(
                    AgentBinding(
                        agent_id="agent-1",
                        runtime=DEFAULT_RUNTIME,
                        allow_task=True,
                    )
                )
            ],
        },
        options={},
    )

    run(async_setup_entry(hass, entry))

    loaded_runtime = hass.data[DOMAIN]["runtime"]
    assert loaded_runtime.selected_agent_ids == ["agent-1"]

    sensor = LobeHubConversationSensor(loaded_runtime, "agent-1")
    assert sensor.native_value == "topic-1"

    new_topic = run(
        hass.services.async_call(
            DOMAIN,
            SERVICE_NEW_TOPIC,
            {
                "agent_id": "agent-1",
                CONF_TOPIC_TITLE: "Buy coffee",
            },
        )
    )
    assert new_topic["active_topic_id"] == "topic-2"
    assert sensor.native_value == "topic-2"

    message = run(
        hass.services.async_call(
            DOMAIN,
            SERVICE_SEND_MESSAGE,
            {
                "agent_id": "agent-1",
                CONF_MESSAGE: "Order a latte",
                "context": {"location": "near office"},
            },
        )
    )
    assert message["data"]["id"] == "message-1"

    task = run(
        hass.services.async_call(
            DOMAIN,
            SERVICE_RUN_TASK,
            {
                "agent_id": "agent-1",
                "instruction": "Buy coffee at the nearest Luckin",
                "context": {"location": "near office"},
            },
        )
    )
    assert task["response_id"] == "resp-1"
    assert task["output_text"] == "done"

    assert len(calls) == 6
    assert calls[0][1].endswith("/api/v1/users/me")
    assert calls[2][1].endswith("/api/v1/topics")
    assert calls[3][1].endswith("/api/v1/topics")
    send_message_body = json.loads(calls[4][3].decode("utf-8"))
    assert send_message_body["topicId"] == "topic-2"
    assert calls[5][1].endswith("/api/v1/responses")
    assert json.loads(calls[5][3].decode("utf-8"))["model"] == "agent-1"
