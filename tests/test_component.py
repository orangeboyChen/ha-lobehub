"""Tests for config-entry state persistence and the per-agent runtime."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from custom_components.lobehub import config_flow
from custom_components.lobehub.const import (
    CONF_AGENT_IDS,
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_SELECTED_AGENT,
    CONF_TOPIC_POLICY,
    DOMAIN,
    TOPIC_POLICY_REUSE,
)
from custom_components.lobehub.entry_state import (
    build_options_with_runtime_state,
    get_persisted_binding,
    get_persisted_conversation,
)
from custom_components.lobehub.models import AgentBinding, ConversationState
from custom_components.lobehub.models import AgentSummary
from custom_components.lobehub.runtime import LobeHubRuntime
import pytest
import voluptuous as vol

from custom_components.lobehub.services import (
    GET_TASK_SCHEMA,
    LIST_AGENTS_SCHEMA,
    LIST_DEVICES_SCHEMA,
    LIST_TASKS_SCHEMA,
    NEW_TOPIC_SCHEMA,
    RUN_SAVED_TASK_SCHEMA,
    RUN_TASK_SCHEMA,
    SEND_MESSAGE_SCHEMA,
    SWITCH_TOPIC_SCHEMA,
    UPDATE_AGENT_SETTINGS_SCHEMA,
    _resolve_target_entries,
)
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall


class FakeIntegration:
    """Small integration double that records the runtime-facing contract."""

    def __init__(self) -> None:
        self._conversation: ConversationState | None = None
        self.configured_binding: AgentBinding | None = None
        self.sent: list[tuple[str, str | None, dict[str, object] | None]] = []

    def configure_agent(self, binding: AgentBinding) -> AgentBinding:
        self.configured_binding = binding
        return binding

    def restore_conversation(
        self,
        topic_id: str,
        binding: AgentBinding,
        *,
        fallback: ConversationState | None = None,
    ) -> ConversationState | None:
        return fallback if fallback and fallback.id == topic_id else None

    def new_topic(self, title: str) -> ConversationState:
        assert self.configured_binding is not None
        self._conversation = ConversationState(
            id="topic-new",
            agent_id=self.configured_binding.agent_id,
            title=title,
            model=self.configured_binding.model,
            provider=self.configured_binding.provider,
            runtime=self.configured_binding.runtime,
            topic_policy=self.configured_binding.topic_policy,
        )
        return self._conversation

    def switch_topic(self, topic_id: str) -> ConversationState:
        assert self.configured_binding is not None
        self._conversation = ConversationState(
            id=topic_id,
            agent_id=self.configured_binding.agent_id,
            title="Restored topic",
        )
        return self._conversation

    def send_message(
        self,
        message: str,
        *,
        conversation_id: str | None,
        context: dict[str, object] | None,
    ) -> tuple[ConversationState, dict[str, object]]:
        assert self._conversation is not None
        self.sent.append((message, conversation_id, context))
        return self._conversation, {"final_output_text": "Done"}

    def discover_agents(self) -> dict[str, AgentBinding]:
        return {}


def test_runtime_persists_one_agent_and_active_topic() -> None:
    integration = FakeIntegration()
    runtime = LobeHubRuntime(integration)  # type: ignore[arg-type]
    binding = AgentBinding(
        agent_id="agent-coffee",
        title="Coffee",
        model="gpt-4o-mini",
        provider="openai",
        runtime="device",
        bound_device_id="device-1",
        topic_policy="reuse",
    )

    runtime.configure(binding)
    created = runtime.new_topic("Morning coffee")
    conversation, payload = runtime.send_conversation_message(
        "Order a latte",
        conversation_id=created.id,
        context={"location": "office"},
    )

    assert conversation.id == "topic-new"
    assert payload["final_output_text"] == "Done"
    assert integration.sent == [
        ("Order a latte", "topic-new", {"location": "office"})
    ]
    assert runtime.snapshot() == {
        "agent_id": "agent-coffee",
        "agent_title": "Coffee",
        "task_lookup_supported": True,
        "bound_device_id": "device-1",
        "active_topic_id": "topic-new",
        "conversation_id": "topic-new",
        "model": "gpt-4o-mini",
        "provider": "openai",
        "runtime": "device",
        "title": "Morning coffee",
        "topic_policy": "reuse",
        "workspace_id": None,
    }

    entry = SimpleNamespace(data={}, options={"unrelated": "kept"})
    options = build_options_with_runtime_state(entry.options, runtime)
    entry.options = options

    persisted_binding, binding_item = get_persisted_binding(entry)
    persisted_conversation, conversation_item = get_persisted_conversation(entry)
    assert binding_item == runtime.dump_binding()
    assert conversation_item == runtime.dump_conversation()
    assert persisted_binding == binding
    assert persisted_conversation == created
    assert options["unrelated"] == "kept"


def test_runtime_switch_topic_replaces_active_conversation() -> None:
    integration = FakeIntegration()
    runtime = LobeHubRuntime(integration)  # type: ignore[arg-type]
    runtime.configure(AgentBinding(agent_id="agent-1", title="One"))
    runtime.new_topic("First")

    switched = runtime.switch_topic("topic-existing")

    assert switched.id == "topic-existing"
    assert runtime.conversation is switched


def test_list_agents_includes_configured_default_agent() -> None:
    integration = FakeIntegration()
    runtime = LobeHubRuntime(integration)  # type: ignore[arg-type]
    runtime.configure(AgentBinding(agent_id="agent-default", title="Default"))

    assert runtime.list_agents() == [
        {
            "id": "agent-default",
            "agent_id": "agent-default",
            "title": "Default",
            "model": None,
            "provider": None,
            "runtime": "auto",
            "bound_device_id": None,
            "workspace_id": None,
        }
    ]


@pytest.mark.parametrize(
    ("schema", "data"),
    [
        (SEND_MESSAGE_SCHEMA, {"message": "Hello"}),
        (NEW_TOPIC_SCHEMA, {"topic_title": "New topic"}),
        (SWITCH_TOPIC_SCHEMA, {"topic_id": "topic-1"}),
        (RUN_TASK_SCHEMA, {"instruction": "Summarize this"}),
        (LIST_TASKS_SCHEMA, {}),
        (GET_TASK_SCHEMA, {"task": "TASK-1"}),
        (RUN_SAVED_TASK_SCHEMA, {"task": "TASK-1"}),
        (LIST_AGENTS_SCHEMA, {}),
        (LIST_DEVICES_SCHEMA, {}),
        (UPDATE_AGENT_SETTINGS_SCHEMA, {"runtime": "auto"}),
    ],
)
def test_all_service_schemas_accept_home_assistant_entity_targets(schema, data) -> None:
    assert schema({**data, "entity_id": "conversation.lobehub_default"})[
        "entity_id"
    ] == "conversation.lobehub_default"


def test_service_schemas_reject_unknown_fields() -> None:
    with pytest.raises(vol.Invalid):
        LIST_AGENTS_SCHEMA({"unknown_field": "value"})


def test_resolve_target_entries_handles_one_loaded_entry_without_a_target() -> None:
    entry = SimpleNamespace(entry_id="entry-1", state=ConfigEntryState.LOADED)
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_loaded_entries=lambda domain: [entry] if domain == DOMAIN else []
        )
    )

    assert _resolve_target_entries(hass, ServiceCall(data={})) == [("entry-1", entry)]


def test_config_flow_creates_one_entry_per_selected_agent(monkeypatch) -> None:
    """Batch setup persists the first agent and creates entries for the rest."""

    class FakeClient:
        def __init__(self, config) -> None:
            self.config = config

        def validate_auth(self) -> dict[str, str]:
            return {"id": "user-1"}

        def list_agents(self) -> list[AgentSummary]:
            return [
                AgentSummary(id="agent-1", title="Coffee"),
                AgentSummary(id="agent-2", title="Driver"),
            ]

    monkeypatch.setattr(config_flow, "LobeHubClient", FakeClient)
    hass = HomeAssistant()
    flow = config_flow.LobeHubConfigFlow()
    flow.hass = hass

    result = asyncio.run(flow.async_step_user(
        {
            CONF_BASE_URL: "https://lobehub.example",
            CONF_API_KEY: "test-key",
        }
    ))
    assert result["type"] == "form"
    assert result["step_id"] == "select_agent"

    result = asyncio.run(flow.async_step_select_agent(
        {
            CONF_AGENT_IDS: ["agent-1", "agent-2"],
            CONF_TOPIC_POLICY: TOPIC_POLICY_REUSE,
        }
    ))
    assert result["type"] == "create_entry"
    assert result["title"] == "Coffee"
    assert result["data"][CONF_SELECTED_AGENT]["agent_id"] == "agent-1"

    primary = ConfigEntry(
        title="Coffee",
        data=result["data"],
        domain=DOMAIN,
        unique_id="https://lobehub.example::agent-1",
    )
    completed = asyncio.run(flow.async_on_create_entry({"result": primary}))

    assert completed["result"] is primary
    assert len(hass.config_entries.entries) == 1
    additional = hass.config_entries.entries[0]
    assert additional.title == "Driver"
    assert additional.unique_id == "https://lobehub.example::agent-2"
    assert additional.data[CONF_SELECTED_AGENT]["agent_id"] == "agent-2"
