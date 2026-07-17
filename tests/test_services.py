"""Service registration and response contract tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from custom_components.lobehub.const import DOMAIN
from custom_components.lobehub.models import AgentBinding, ConversationState, TaskResult
from custom_components.lobehub import services
from custom_components.lobehub.services import (
    _resolve_single_entry,
    _resolve_target_entries,
    async_register_services,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import ServiceCall
from homeassistant.exceptions import HomeAssistantError


class _Services:
    def __init__(self) -> None:
        self.handlers = {}

    def has_service(self, domain, service) -> bool:
        return (domain, service) in self.handlers

    def async_register(self, domain, service, handler, **kwargs) -> None:
        self.handlers[(domain, service)] = handler


class _Runtime:
    def __init__(self) -> None:
        self.agent_id = "agent-1"
        self.agent_binding = AgentBinding(
            agent_id="agent-1", title="LobeAI", model="gpt", provider="openai"
        )

    def dump_binding(self):
        return {"agent_id": self.agent_id}

    def dump_conversation(self):
        return {"id": "topic-1", "agent_id": self.agent_id}

    def send_conversation_message(self, message, **kwargs):
        return ConversationState(id="topic-1", agent_id=self.agent_id), {"final_output_text": message}

    def new_topic(self, title):
        return ConversationState(id="topic-new", agent_id=self.agent_id, title=title)

    def switch_topic(self, topic_id):
        return ConversationState(id=topic_id, agent_id=self.agent_id, title="Switched")

    def run_task(self, instruction, **kwargs):
        return TaskResult(response_id="response-1", output_text=instruction, raw={"ok": True})

    def list_agents(self):
        return [{"id": self.agent_id, "title": "LobeAI"}]

    def list_devices(self):
        return [{"device_id": "device-1", "online": True}]

    def update_agent_settings(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self.agent_binding, key, value)
        return self.agent_binding

    def list_tasks(self, **kwargs):
        return {"tasks": [{"id": "task-1"}], "total": 1, "raw": {"page": 1}}

    def get_task(self, task):
        return {"task": {"id": task}, "topics": [{"id": "topic-1"}]}

    def run_saved_task(self, task, **kwargs):
        return TaskResult(task_id=task, response_id="response-2", output_text="saved")


def test_services_register_and_return_normalized_responses() -> None:
    """Exercise every public service through its registered handler."""

    runtime = _Runtime()
    entry = SimpleNamespace(
        entry_id="entry-1",
        state=ConfigEntryState.LOADED,
        options={},
        runtime_data=runtime,
    )
    updates = []
    hass = SimpleNamespace(
        services=_Services(),
        config_entries=SimpleNamespace(
            async_loaded_entries=lambda domain: [entry] if domain == DOMAIN else [],
            async_update_entry=lambda updated, **kwargs: updates.append((updated, kwargs)),
        ),
    )

    async def executor(func, *args):
        return func(*args)

    hass.async_add_executor_job = executor
    async_register_services(hass)

    async def call(name, data):
        return await hass.services.handlers[(DOMAIN, name)](ServiceCall(data=data))

    async def exercise():
        assert (await call("send_message", {"message": "hello"}))["results"][0]["response"] == "hello"
        assert (await call("new_topic", {"topic_title": "New"}))["results"][0]["topic_id"] == "topic-new"
        assert (await call("switch_topic", {"topic_id": "topic-2"}))["results"][0]["title"] == "Switched"
        assert (await call("run_task", {"instruction": "do it"}))["results"][0]["response_id"] == "response-1"
        assert (await call("list_agents", {}))["results"][0]["agents"][0]["title"] == "LobeAI"
        assert (await call("list_devices", {}))["results"][0]["devices"][0]["online"] is True
        assert (await call("update_agent_settings", {"runtime": "gateway", "model": "next"}))["results"][0]["runtime"] == "auto"
        assert (await call("list_tasks", {"limit": 3, "offset": 0}))["results"][0]["total"] == 1
        assert (await call("get_task", {"task": "task-1"}))["results"][0]["task"]["id"] == "task-1"
        assert (await call("run_saved_task", {"task": "task-1"}))["results"][0]["response_id"] == "response-2"

    asyncio.run(exercise())
    assert len(updates) == 5


def test_target_resolution_filters_invalid_entities(monkeypatch) -> None:
    """Only loaded LobeHub conversation entities resolve from a service target."""

    valid = SimpleNamespace(entry_id="entry-1", domain=DOMAIN, state=ConfigEntryState.LOADED)
    invalid = SimpleNamespace(entry_id="entry-2", domain="other", state=ConfigEntryState.LOADED)
    registry_entries = {
        "conversation.valid": SimpleNamespace(config_entry_id="entry-1"),
        "conversation.invalid": SimpleNamespace(config_entry_id="entry-2"),
    }
    registry = SimpleNamespace(async_get=registry_entries.get)
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_loaded_entries=lambda domain: [valid, invalid],
            async_get_entry=lambda entry_id: {"entry-1": valid, "entry-2": invalid}.get(entry_id),
        )
    )
    monkeypatch.setattr(services.er, "async_get", lambda value: registry)
    monkeypatch.setattr(
        services,
        "TargetSelection",
        lambda data: SimpleNamespace(has_any_target=True),
    )
    monkeypatch.setattr(
        services,
        "async_extract_referenced_entity_ids",
        lambda hass, selection: SimpleNamespace(
            referenced={"conversation.valid", "conversation.invalid", "sensor.ignored"},
            indirectly_referenced={"conversation.valid"},
        ),
    )

    assert _resolve_target_entries(hass, ServiceCall(data={"entity_id": "conversation.valid"})) == [
        ("conversation.valid", valid)
    ]


def test_target_resolution_errors_for_missing_or_ambiguous_entries(monkeypatch) -> None:
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_loaded_entries=lambda domain: [])
    )
    with pytest.raises(HomeAssistantError, match="not initialized"):
        _resolve_target_entries(hass, ServiceCall(data={}))

    entries = [
        SimpleNamespace(entry_id="one", state=ConfigEntryState.LOADED),
        SimpleNamespace(entry_id="two", state=ConfigEntryState.LOADED),
    ]
    hass.config_entries.async_loaded_entries = lambda domain: entries
    with pytest.raises(HomeAssistantError, match="Target a LobeHub"):
        _resolve_target_entries(hass, ServiceCall(data={}))

    monkeypatch.setattr(services, "_resolve_target_entries", lambda *args: entries)
    with pytest.raises(HomeAssistantError, match="exactly one"):
        _resolve_single_entry(hass, ServiceCall(data={}), service_name="list_agents")
