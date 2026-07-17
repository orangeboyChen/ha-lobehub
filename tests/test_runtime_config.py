"""Focused behavior coverage for runtime state and configuration forms."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest
import voluptuous as vol

from custom_components.lobehub import config_flow
from custom_components.lobehub.config_flow import LobeHubOptionsFlow
from custom_components.lobehub.const import (
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_BOUND_DEVICE_ID,
    CONF_CONNECTION,
    CONF_CONVERSATION,
    CONF_DEFAULT_RUNTIME,
    CONF_MODEL,
    CONF_PROVIDER,
    CONF_RUNTIME,
    CONF_SELECTED_AGENT,
    CONF_TOPIC_POLICY,
)
from custom_components.lobehub.exceptions import ApiError
from custom_components.lobehub.models import (
    AgentBinding,
    AgentSummary,
    ConversationState,
    IntegrationConfig,
    RemoteOptions,
)
from custom_components.lobehub.runtime import (
    LobeHubRuntime,
    build_runtime,
    load_binding_item,
    load_conversation_item,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant


class RuntimeIntegration:
    """Integration double exposing every runtime delegation point."""

    def __init__(self) -> None:
        self._conversation: ConversationState | None = None
        self._agent_binding: AgentBinding | None = None
        self.discover_error: ApiError | None = None
        self.updated: dict[str, object] | None = None

    @property
    def conversation(self):
        return self._conversation

    def configure_agent(self, binding: AgentBinding) -> AgentBinding:
        self._agent_binding = replace(binding, title="Remote title")
        return self._agent_binding

    def restore_conversation(self, topic_id, binding, *, fallback):
        assert binding.agent_id == "agent-1"
        return replace(fallback, id=topic_id, title="Restored")

    def new_topic(self, title):
        self._conversation = ConversationState(id="new", agent_id="agent-1", title=title)
        return self._conversation

    def switch_topic(self, topic_id):
        self._conversation = ConversationState(id=topic_id, agent_id="agent-1")
        return self._conversation

    def send_message(self, message, *, conversation_id, context):
        self._conversation = ConversationState(id=conversation_id or "sent", agent_id="agent-1")
        return self._conversation, {"message": message, "context": context}

    def run_task(self, instruction, *, context, previous_response_id):
        self._conversation = ConversationState(id="task-topic", agent_id="agent-1")
        return {"instruction": instruction, "previous": previous_response_id, "context": context}

    def list_tasks(self, **kwargs):
        return kwargs

    def discover_agents(self):
        if self.discover_error:
            raise self.discover_error
        return {"other": AgentBinding(agent_id="other", title="Other")}

    def list_devices(self):
        return [{"device_id": "device-1"}]

    def discover_remote_options(self):
        return RemoteOptions(models=["model-a"])

    def update_agent_settings(self, **kwargs):
        self.updated = kwargs
        return replace(self._agent_binding, **kwargs)  # type: ignore[arg-type]

    def get_task(self, task):
        return {"task": task}

    def run_saved_task(self, task, *, continue_topic_id, prompt):
        return {"task": task, "topic": continue_topic_id, "prompt": prompt}

    def fetch_agent_binding(self, agent_id, *, workspace_id):
        return AgentBinding(agent_id=agent_id, title="Fresh", model="remote")


def test_runtime_delegates_operations_and_serializes_refreshed_state() -> None:
    integration = RuntimeIntegration()
    runtime = LobeHubRuntime(integration)  # type: ignore[arg-type]
    binding = AgentBinding(
        agent_id="agent-1", title="Local", model="local", provider="provider",
        runtime="device", bound_device_id="device-1", topic_policy="new",
        workspace_id="workspace-1",
    )

    runtime.configure(binding, ConversationState(id="old", agent_id="agent-1", title="Old"))
    assert runtime.agent_id == "agent-1"
    assert runtime.conversation and runtime.conversation.title == "Restored"
    assert runtime.new_topic("New").id == "new"
    assert runtime.switch_topic("switched").id == "switched"
    assert runtime.send_conversation_message("Hello", conversation_id=None)[1]["message"] == "Hello"
    assert runtime.run_task("Do it", previous_response_id="previous")["previous"] == "previous"
    assert runtime.list_tasks(limit=3)["limit"] == 3
    assert runtime.list_devices() == [{"device_id": "device-1"}]
    assert runtime.discover_remote_options().models == ["model-a"]
    assert runtime.get_task("TASK-1") == {"task": "TASK-1"}
    assert runtime.run_saved_task("TASK-1", continue_topic_id="topic", prompt="go") == {
        "task": "TASK-1", "topic": "topic", "prompt": "go"
    }

    updated = runtime.update_agent_settings(
        model="new-model", provider=None, runtime="auto", bound_device_id=None,
        topic_policy="reuse",
    )
    assert updated.model == "new-model"
    assert runtime.dump_binding() and runtime.dump_binding()["agent_id"] == "agent-1"
    assert runtime.dump_conversation() and runtime.dump_conversation()["id"] == "task-topic"
    assert runtime.refresh_remote_state() is True
    assert runtime.agent_binding and runtime.agent_binding.title == "Fresh"


def test_runtime_lists_default_on_api_error_and_handles_empty_state() -> None:
    integration = RuntimeIntegration()
    runtime = LobeHubRuntime(integration)  # type: ignore[arg-type]
    assert [item["agent_id"] for item in runtime.list_agents()] == ["other"]
    assert runtime.refresh_remote_state() is False
    assert runtime.dump_binding() is None
    assert runtime.dump_conversation() is None

    runtime.configure(AgentBinding(agent_id="agent-1", title="Configured"))
    integration.discover_error = ApiError(503, "Unavailable")
    assert [item["agent_id"] for item in runtime.list_agents()] == ["agent-1"]
    assert runtime.is_remote_agent_missing(ApiError(404, "anything")) is True
    assert runtime.is_remote_agent_missing(ApiError(400, "Agent not found")) is True
    assert runtime.is_remote_agent_missing(ApiError(500, "other")) is False


def test_runtime_loaders_and_builder_validate_persisted_state() -> None:
    assert load_binding_item(None) is None
    assert load_binding_item({"agent_id": ""}) is None
    assert load_binding_item({"agent_id": "agent-1"}).agent_id == "agent-1"  # type: ignore[union-attr]
    assert load_conversation_item({"id": "topic"}) is None
    assert load_conversation_item({"id": "topic", "agent_id": "agent-1"}).id == "topic"  # type: ignore[union-attr]
    runtime = build_runtime(IntegrationConfig(api_key="key", base_url="https://lobehub.example"))
    assert isinstance(runtime, LobeHubRuntime)


def test_config_flow_helpers_and_connection_choices(monkeypatch) -> None:
    assert config_flow._agent_list_error_key(ApiError(403, "Permission denied for agent list")) == "agent_list_forbidden"
    assert config_flow._agent_list_error_key(ApiError(500, "bad gateway")) == "cannot_connect"

    class Client:
        def validate_auth(self):
            return {"id": "user"}

        def list_agents(self):
            return [AgentSummary(id="agent", title="Agent")]

    assert config_flow._validate_and_list_agents(Client()) == [AgentSummary(id="agent", title="Agent")]
    entry = ConfigEntry(
        entry_id="existing", domain="lobehub", title="Agent",
        data={CONF_BASE_URL: "https://lobehub.example", CONF_API_KEY: "key"},
    )
    hass = HomeAssistant()
    hass.config_entries.entries.append(entry)
    flow = config_flow.LobeHubConfigFlow()
    flow.hass = hass
    form = asyncio.run(flow.async_step_user())
    assert form["step_id"] == "user"
    missing = asyncio.run(flow.async_step_user({"connection": "missing"}))
    assert missing["errors"] == {"connection": "required"}


def test_config_flow_loads_and_selects_agents_with_duplicate_handling(monkeypatch) -> None:
    class Client:
        def __init__(self, config):
            self.config = config

        def validate_auth(self):
            return {"id": "user"}

        def list_agents(self):
            return [
                AgentSummary(id="existing", title="Existing"),
                AgentSummary(id="new", title="New"),
            ]

    monkeypatch.setattr(config_flow, "LobeHubClient", Client)
    hass = HomeAssistant()
    hass.config_entries.entries.append(ConfigEntry(
        domain="lobehub", unique_id="https://lobehub.example::existing",
        data={CONF_BASE_URL: "https://lobehub.example", CONF_API_KEY: "key"},
    ))
    flow = config_flow.LobeHubConfigFlow()
    flow.hass = hass
    result = asyncio.run(flow.async_step_user({
        CONF_BASE_URL: "https://lobehub.example", CONF_API_KEY: "key",
    }))
    assert result["step_id"] == "select_agent"
    empty = asyncio.run(flow.async_step_select_agent({
        "agent_ids": ["missing"], CONF_TOPIC_POLICY: "reuse",
    }))
    assert empty["errors"] == {"agent_ids": "required"}
    created = asyncio.run(flow.async_step_select_agent({
        "agent_ids": ["existing", "new"], CONF_TOPIC_POLICY: "new",
    }))
    assert created["type"] == "create_entry"
    assert created["data"][CONF_SELECTED_AGENT]["agent_id"] == "new"


def test_config_flow_maps_api_and_unexpected_connection_failures(monkeypatch) -> None:
    class FailingClient:
        def __init__(self, config):
            pass

        def validate_auth(self):
            raise ApiError(403, "Permission denied for agent list")

    monkeypatch.setattr(config_flow, "LobeHubClient", FailingClient)
    flow = config_flow.LobeHubConfigFlow()
    flow.hass = HomeAssistant()
    result = asyncio.run(flow.async_step_connection({
        CONF_BASE_URL: "https://lobehub.example", CONF_API_KEY: "key",
    }))
    assert result["errors"] == {"base": "agent_list_forbidden"}

    class BrokenClient(FailingClient):
        def validate_auth(self):
            raise RuntimeError("network broke")

    monkeypatch.setattr(config_flow, "LobeHubClient", BrokenClient)
    result = asyncio.run(flow.async_step_connection({
        CONF_BASE_URL: "https://lobehub.example", CONF_API_KEY: "key",
    }))
    assert result["errors"] == {"base": "cannot_connect"}


def test_options_flow_validates_device_and_persists_local_binding() -> None:
    entry = ConfigEntry(
        domain="lobehub",
        data={CONF_BASE_URL: "https://lobehub.example", CONF_API_KEY: "key"},
        options={CONF_SELECTED_AGENT: {"agent_id": "agent-1", "title": "Agent"}},
    )
    flow = LobeHubOptionsFlow(entry)
    flow.hass = HomeAssistant()
    flow._remote_options = RemoteOptions(runtimes=["auto", "device"])
    result = asyncio.run(flow.async_step_init({
        CONF_TOPIC_POLICY: "reuse", CONF_MODEL: "", CONF_PROVIDER: "",
        CONF_RUNTIME: "device", CONF_BOUND_DEVICE_ID: "",
    }))
    assert result["errors"] == {CONF_BOUND_DEVICE_ID: "required"}

    result = asyncio.run(flow.async_step_init({
        CONF_TOPIC_POLICY: "new", CONF_MODEL: "custom", CONF_PROVIDER: "provider",
        CONF_RUNTIME: "auto", CONF_BOUND_DEVICE_ID: "",
    }))
    assert result["type"] == "create_entry"
    assert result["data"][CONF_SELECTED_AGENT]["model"] == "custom"

    schema = flow._options_schema(AgentBinding(agent_id="agent-1"), RemoteOptions(
        models=["m"], providers=["p"], runtimes=[],
        devices=[{"device_id": "d", "label": "Device", "online": False}],
    ))
    with pytest.raises(vol.Invalid):
        schema({CONF_TOPIC_POLICY: "reuse", CONF_MODEL: "m", CONF_PROVIDER: "p", CONF_RUNTIME: "unknown", CONF_BOUND_DEVICE_ID: "d"})


def test_options_flow_updates_runtime_and_handles_remote_api_error(monkeypatch) -> None:
    binding = AgentBinding(agent_id="agent-1", title="Agent", model="old")

    class Runtime:
        def __init__(self):
            self.conversation = ConversationState(id="topic", agent_id="agent-1")
            self.integration = SimpleNamespace(_conversation=None)

        def update_agent_settings(self, **kwargs):
            return replace(binding, **kwargs)

        def dump_conversation(self):
            return {"id": self.conversation.id}

    entry = ConfigEntry(
        domain="lobehub", entry_id="entry", data={
            CONF_BASE_URL: "https://lobehub.example", CONF_API_KEY: "key",
        }, options={CONF_SELECTED_AGENT: {"agent_id": "agent-1", "title": "Agent"}},
    )
    entry.runtime_data = Runtime()
    flow = LobeHubOptionsFlow(entry)
    flow.hass = HomeAssistant()
    flow._binding = binding
    flow._remote_options = RemoteOptions(runtimes=["auto"])
    result = asyncio.run(flow.async_step_init({
        CONF_TOPIC_POLICY: "new", CONF_MODEL: "updated", CONF_PROVIDER: "",
        CONF_RUNTIME: "auto", CONF_BOUND_DEVICE_ID: "",
    }))
    assert result["data"][CONF_SELECTED_AGENT]["model"] == "updated"
    assert entry.runtime_data.conversation.topic_policy == "new"

    class FailingRuntime(Runtime):
        def update_agent_settings(self, **kwargs):
            raise ApiError(500, "nope")

    entry.runtime_data = FailingRuntime()
    flow = LobeHubOptionsFlow(entry)
    flow.hass = HomeAssistant()
    flow._binding = binding
    flow._remote_options = RemoteOptions(runtimes=["auto"])
    error = asyncio.run(flow.async_step_init({
        CONF_TOPIC_POLICY: "reuse", CONF_MODEL: "", CONF_PROVIDER: "",
        CONF_RUNTIME: "auto", CONF_BOUND_DEVICE_ID: "",
    }))
    assert error["errors"] == {"base": "cannot_connect"}


def test_remote_options_refresh_keeps_local_agent_overrides() -> None:
    class Client:
        def discover_remote_options(self, *, workspace_id: str | None = None) -> RemoteOptions:
            assert workspace_id == "workspace-1"
            return RemoteOptions(models=["remote-model"])

        def get_agent(self, agent_id: str, *, workspace_id: str | None = None) -> AgentSummary:
            assert (agent_id, workspace_id) == ("agent-1", "workspace-1")
            return AgentSummary(
                id=agent_id,
                title="Remote",
                raw={"agencyConfig": {"executionTarget": "device", "boundDeviceId": "remote"}},
            )

    binding = AgentBinding(
        agent_id="agent-1",
        enabled=False,
        model="local-model",
        provider="local-provider",
        runtime="auto",
        bound_device_id="local-device",
        topic_policy="new",
        workspace_id="workspace-1",
    )
    options, refreshed = config_flow._remote_options_for_binding(Client(), binding)

    assert options.models == ["remote-model"]
    assert refreshed.title == "Remote"
    assert refreshed.enabled is False
    assert refreshed.model == "local-model"
    assert refreshed.provider == "local-provider"
    assert refreshed.bound_device_id == "local-device"
    assert refreshed.topic_policy == "new"


def test_config_flow_reuses_existing_connection_and_handles_all_duplicates(monkeypatch) -> None:
    class Client:
        def __init__(self, config) -> None:
            self.config = config

        def validate_auth(self):
            return {"id": "user"}

        def list_agents(self):
            return [AgentSummary(id="agent-1", title="Agent")]

    monkeypatch.setattr(config_flow, "LobeHubClient", Client)
    entry = ConfigEntry(
        entry_id="existing",
        domain="lobehub",
        title="Existing",
        unique_id="https://lobehub.example::agent-1",
        data={
            CONF_BASE_URL: "https://lobehub.example",
            CONF_API_KEY: "key",
            CONF_DEFAULT_RUNTIME: "device",
        },
    )
    hass = HomeAssistant()
    hass.config_entries.entries.append(entry)
    flow = config_flow.LobeHubConfigFlow()
    flow.hass = hass

    assert asyncio.run(flow.async_step_user({CONF_CONNECTION: config_flow._NEW_CONNECTION}))["step_id"] == "connection"
    loaded = asyncio.run(flow.async_step_user({CONF_CONNECTION: "existing"}))
    assert loaded["step_id"] == "select_agent"
    assert flow._integration_config is not None
    assert flow._integration_config.default_runtime == "device"
    aborted = asyncio.run(flow.async_step_select_agent({
        "agent_ids": ["agent-1"], CONF_TOPIC_POLICY: "reuse",
    }))
    assert aborted == {"type": "abort", "reason": "already_configured"}


def test_config_flow_additional_entries_skip_racing_duplicate_and_clear_pending() -> None:
    hass = HomeAssistant()
    flow = config_flow.LobeHubConfigFlow()
    flow.hass = hass
    flow._integration_config = IntegrationConfig(api_key="key", base_url="https://lobehub.example")
    flow._pending_additional_bindings = [
        AgentBinding(agent_id="already", title="Already"),
        AgentBinding(agent_id="added", title="Added"),
    ]
    hass.config_entries.entries.append(ConfigEntry(
        entry_id="already-entry",
        domain="lobehub",
        unique_id="https://lobehub.example::already",
    ))
    primary = ConfigEntry(entry_id="primary", domain="lobehub", source="user")

    result = asyncio.run(flow.async_on_create_entry({"result": primary}))

    assert result == {"result": primary}
    assert [entry.unique_id for entry in hass.config_entries.entries] == [
        "https://lobehub.example::already",
        "https://lobehub.example::added",
    ]
    assert flow._pending_additional_bindings == []
    assert asyncio.run(flow.async_on_create_entry(result)) is result


def test_options_flow_loads_remote_form_and_degrades_when_remote_options_fail(monkeypatch) -> None:
    entry = ConfigEntry(
        domain="lobehub",
        entry_id="entry",
        data={CONF_BASE_URL: "https://lobehub.example", CONF_API_KEY: "key"},
        options={
            CONF_SELECTED_AGENT: {"agent_id": "agent-1", "title": "Agent"},
            CONF_CONVERSATION: {"id": "saved"},
        },
    )

    class Client:
        def __init__(self, config) -> None:
            assert config.base_url == "https://lobehub.example"

        def discover_remote_options(self, *, workspace_id: str | None = None) -> RemoteOptions:
            return RemoteOptions(models=["remote"], runtimes=["auto"])

        def get_agent(self, agent_id: str, *, workspace_id: str | None = None) -> AgentSummary:
            return AgentSummary(id=agent_id, title="Remote agent")

    monkeypatch.setattr(config_flow, "LobeHubClient", Client)
    flow = LobeHubOptionsFlow(entry)
    flow.hass = HomeAssistant()
    form = asyncio.run(flow.async_step_init())
    assert form["type"] == "form"
    assert flow._binding is not None and flow._binding.title == "Remote agent"
    assert flow._remote_options is not None and flow._remote_options.models == ["remote"]

    class BrokenClient(Client):
        def discover_remote_options(self, *, workspace_id: str | None = None) -> RemoteOptions:
            raise RuntimeError("offline")

    monkeypatch.setattr(config_flow, "LobeHubClient", BrokenClient)
    broken_flow = LobeHubOptionsFlow(entry)
    broken_flow.hass = HomeAssistant()
    broken_form = asyncio.run(broken_flow.async_step_init())
    assert broken_form["type"] == "form"
    assert broken_flow._remote_options == RemoteOptions()
    assert broken_flow._binding is not None


def test_options_flow_uses_data_binding_and_preserves_saved_conversation() -> None:
    entry = ConfigEntry(
        domain="lobehub",
        data={
            CONF_BASE_URL: "https://lobehub.example",
            CONF_API_KEY: "key",
            CONF_SELECTED_AGENT: {"agent_id": "agent-1", "title": "From data"},
        },
        options={CONF_CONVERSATION: {"id": "saved"}},
    )
    flow = LobeHubOptionsFlow(entry)
    flow.hass = HomeAssistant()
    flow._remote_options = RemoteOptions(runtimes=["auto"])
    result = asyncio.run(flow.async_step_init({
        CONF_TOPIC_POLICY: "reuse", CONF_MODEL: "", CONF_PROVIDER: "",
        CONF_RUNTIME: "auto", CONF_BOUND_DEVICE_ID: "",
    }))
    assert result["data"][CONF_CONVERSATION] == {"id": "saved"}

    missing = LobeHubOptionsFlow(ConfigEntry(domain="lobehub"))
    with pytest.raises(ValueError, match="No LobeHub agent"):
        missing._current_binding()
