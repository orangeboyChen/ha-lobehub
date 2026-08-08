"""Tests for config-entry state persistence and the per-agent runtime."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from custom_components.lobehub import config_flow
import custom_components.lobehub as lobehub
from custom_components.lobehub import (
    _async_remove_stale_devices,
    _async_sync_connection,
    _async_sync_runtime,
    _connection_key,
    _load_runtime_from_entry,
    _register_connection_sync,
    async_setup,
    async_setup_entry,
    async_unload_entry,
    async_migrate_entry,
)
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
from custom_components.lobehub.exceptions import ApiError
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
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
        (RUN_SAVED_TASK_SCHEMA, {"task_id": "TASK-1"}),
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


def test_migrate_legacy_selected_agents_preserves_each_agent_conversation() -> None:
    primary = ConfigEntry(
        entry_id="legacy-entry",
        title="Legacy",
        domain=DOMAIN,
        version=1,
        data={
            CONF_BASE_URL: "https://lobehub.example",
            CONF_API_KEY: "test-key",
        },
        options={
            "selected_agents": [
                {"agent_id": "agent-1", "title": "Coffee"},
                {"agent_id": "agent-2", "title": "Driver"},
            ],
            "conversations": [
                {"id": "topic-1", "agent_id": "agent-1", "title": "Morning"},
                {"id": "topic-2", "agent_id": "agent-2", "title": "Errands"},
            ],
        },
    )
    hass = HomeAssistant()
    hass.config_entries.entries.append(primary)

    assert asyncio.run(async_migrate_entry(hass, primary)) is True
    assert primary.version == 2
    assert primary.unique_id == "https://lobehub.example::agent-1"
    assert primary.options[CONF_SELECTED_AGENT]["agent_id"] == "agent-1"
    assert primary.options["conversation"]["id"] == "topic-1"
    assert len(hass.config_entries.entries) == 2

    secondary = hass.config_entries.entries[1]
    assert secondary.unique_id == "https://lobehub.example::agent-2"
    assert secondary.data[CONF_SELECTED_AGENT]["agent_id"] == "agent-2"
    assert secondary.options["conversation"]["id"] == "topic-2"


def test_migrate_legacy_selected_agents_rejects_empty_bindings() -> None:
    entry = ConfigEntry(
        entry_id="legacy-entry",
        domain=DOMAIN,
        version=1,
        options={"selected_agents": []},
    )
    hass = HomeAssistant()
    hass.config_entries.entries.append(entry)

    assert asyncio.run(async_migrate_entry(hass, entry)) is False


def test_migrate_v1_singular_binding_preserves_its_conversation() -> None:
    entry = ConfigEntry(
        entry_id="v1-entry",
        title="Coffee",
        domain=DOMAIN,
        version=1,
        data={
            CONF_BASE_URL: "https://lobehub.example",
            CONF_API_KEY: "test-key",
            CONF_SELECTED_AGENT: {"agent_id": "agent-1", "title": "Coffee"},
        },
        options={
            CONF_SELECTED_AGENT: {"agent_id": "agent-1", "title": "Coffee"},
            "conversation": {
                "id": "topic-1",
                "agent_id": "agent-1",
                "title": "Morning",
            },
        },
    )
    hass = HomeAssistant()
    hass.config_entries.entries.append(entry)

    assert asyncio.run(async_migrate_entry(hass, entry)) is True
    assert entry.version == 2
    assert entry.unique_id == "https://lobehub.example::agent-1"
    assert entry.options[CONF_SELECTED_AGENT]["agent_id"] == "agent-1"
    assert entry.options["conversation"] == {
        "id": "topic-1",
        "agent_id": "agent-1",
        "title": "Morning",
        "model": None,
        "provider": None,
        "runtime": "auto",
        "topic_policy": "reuse",
        "workspace_id": None,
    }


def test_migrate_legacy_selected_agents_rejects_invalid_binding_container() -> None:
    entry = ConfigEntry(
        entry_id="legacy-entry",
        domain=DOMAIN,
        version=1,
        options={"selected_agents": {"agent_id": "agent-1"}},
    )

    assert asyncio.run(async_migrate_entry(HomeAssistant(), entry)) is False
    assert entry.version == 1


def test_migrate_filters_invalid_legacy_items_and_rejects_primary_collision() -> None:
    existing = ConfigEntry(
        entry_id="existing",
        domain=DOMAIN,
        unique_id="https://lobehub.example::agent-1",
    )
    entry = ConfigEntry(
        entry_id="legacy",
        domain=DOMAIN,
        version=1,
        data={CONF_BASE_URL: "https://lobehub.example", CONF_API_KEY: "key"},
        options={
            "selected_agents": [None, {}, {"agent_id": "agent-1", "title": "One"}],
            "conversations": {"not": "a list"},
        },
    )
    hass = HomeAssistant()
    hass.config_entries.entries.extend([existing, entry])

    assert asyncio.run(async_migrate_entry(hass, entry)) is False
    assert entry.version == 1


def test_migrate_current_entry_is_a_noop() -> None:
    entry = ConfigEntry(domain=DOMAIN, version=2)

    assert asyncio.run(async_migrate_entry(HomeAssistant(), entry)) is True
    assert entry.data == {}


def test_migrate_skips_duplicate_agent_ids_and_existing_entries() -> None:
    existing = ConfigEntry(
        entry_id="existing",
        domain=DOMAIN,
        unique_id="https://lobehub.example::agent-2",
    )
    legacy = ConfigEntry(
        entry_id="legacy",
        domain=DOMAIN,
        version=1,
        data={CONF_BASE_URL: "https://lobehub.example", CONF_API_KEY: "key"},
        options={
            "selected_agents": [
                {"agent_id": "agent-1", "title": "One"},
                {"agent_id": "agent-1", "title": "Duplicate"},
                {"agent_id": "agent-2", "title": "Already present"},
            ]
        },
    )
    hass = HomeAssistant()
    hass.config_entries.entries.extend([legacy, existing])

    assert asyncio.run(async_migrate_entry(hass, legacy)) is True
    assert legacy.unique_id == "https://lobehub.example::agent-1"
    assert len(hass.config_entries.entries) == 2


def test_connection_sync_fetches_agents_once_for_all_entries() -> None:
    calls = []

    class Client:
        def list_agents(self):
            calls.append(True)
            return [AgentSummary(id="agent-1", title="Renamed")]

    runtimes = [
        SimpleNamespace(
            agent_binding=AgentBinding(agent_id="agent-1", title="Old"),
            integration=SimpleNamespace(client=Client(), _agent_binding=None),
            dump_binding=lambda: {"agent_id": "agent-1"},
            dump_conversation=lambda: None,
        ),
        SimpleNamespace(
            agent_binding=AgentBinding(agent_id="agent-2", title="Deleted"),
            integration=SimpleNamespace(client=Client(), _agent_binding=None),
            dump_binding=lambda: {"agent_id": "agent-2"},
            dump_conversation=lambda: None,
        ),
    ]
    entries = [
        SimpleNamespace(
            entry_id=f"entry-{index}",
            state=ConfigEntryState.LOADED,
            options={},
            data={CONF_BASE_URL: "https://lobehub.example", CONF_API_KEY: "key"},
            runtime_data=runtime,
        )
        for index, runtime in enumerate(runtimes, 1)
    ]
    updated = []
    removed = []
    async def async_remove(entry_id):
        removed.append(entry_id)

    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_loaded_entries=lambda domain: entries,
            async_update_entry=lambda entry, **kwargs: updated.append((entry, kwargs)),
            async_remove=async_remove,
        ),
        async_add_executor_job=lambda func: _async_value(func()),
    )

    asyncio.run(
        _async_sync_connection(
            hass,
            "connection",
            {"entry_ids": {"entry-1", "entry-2"}},
        )
    )

    assert calls == [True]
    assert runtimes[0].agent_binding.title == "Renamed"
    assert removed == ["entry-2"]
    assert len(updated) == 1


async def _async_value(value):
    return value


def test_connection_sync_registration_is_shared_and_unloads_last_entry(monkeypatch) -> None:
    scheduled = []
    monkeypatch.setattr(
        "custom_components.lobehub.async_track_time_interval",
        lambda hass, callback, interval: scheduled.append(callback) or (lambda: scheduled.append("off")),
    )
    hass = SimpleNamespace(data={DOMAIN: {}})
    entries = [
        SimpleNamespace(
            entry_id=f"entry-{index}",
            data={CONF_BASE_URL: "https://lobehub.example", CONF_API_KEY: "key"},
        )
        for index in (1, 2)
    ]

    unregister_one = _register_connection_sync(hass, entries[0])
    unregister_two = _register_connection_sync(hass, entries[1])

    assert len(scheduled) == 1
    assert _connection_key(entries[0]) in hass.data[DOMAIN]["sync_groups"]
    unregister_one()
    assert "off" not in scheduled
    unregister_two()
    assert scheduled[-1] == "off"


def test_connection_sync_leaves_entries_unchanged_when_remote_request_fails() -> None:
    class Client:
        def list_agents(self):
            raise ApiError(503, "unavailable")

    runtime = SimpleNamespace(
        agent_binding=AgentBinding(agent_id="agent-1", title="One"),
        integration=SimpleNamespace(client=Client()),
    )
    entry = SimpleNamespace(
        entry_id="entry-1", state=ConfigEntryState.LOADED, runtime_data=runtime
    )
    removed = []
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_loaded_entries=lambda domain: [entry],
            async_remove=lambda entry_id: removed.append(entry_id),
        ),
        async_add_executor_job=lambda func: _async_value(func()),
    )

    asyncio.run(_async_sync_connection(hass, "connection", {"entry_ids": {"entry-1"}}))

    assert removed == []
    assert runtime.agent_binding.title == "One"


def test_connection_sync_ignores_unexpected_errors_and_entries_without_bindings() -> None:
    class Client:
        def list_agents(self):
            raise RuntimeError("unexpected")

    runtime = SimpleNamespace(agent_binding=None, integration=SimpleNamespace(client=Client()))
    entry = SimpleNamespace(entry_id="entry-1", state=ConfigEntryState.LOADED, runtime_data=runtime)
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_loaded_entries=lambda domain: [entry],
            async_remove=lambda entry_id: None,
        ),
        async_add_executor_job=lambda func: _async_value(func()),
    )

    asyncio.run(_async_sync_connection(hass, "connection", {"entry_ids": {"entry-1"}}))

    class HealthyClient:
        def list_agents(self):
            return []

    runtime.integration.client = HealthyClient()
    asyncio.run(_async_sync_connection(hass, "connection", {"entry_ids": {"entry-1"}}))


def test_runtime_sync_only_runs_for_registered_connection(monkeypatch) -> None:
    entry = SimpleNamespace(data={CONF_BASE_URL: "https://example", CONF_API_KEY: "key"})
    hass = SimpleNamespace(data={DOMAIN: {}})
    called = []
    monkeypatch.setattr(
        lobehub,
        "_async_sync_connection",
        lambda *args: called.append(args) or _async_value(None),
    )

    asyncio.run(_async_sync_runtime(hass, entry))

    assert called == []


def test_runtime_sync_uses_its_shared_connection_group(monkeypatch) -> None:
    entry = SimpleNamespace(data={CONF_BASE_URL: "https://example", CONF_API_KEY: "key"})
    connection_key = _connection_key(entry)
    group = {"entry_ids": {"entry-1"}}
    hass = SimpleNamespace(data={DOMAIN: {"sync_groups": {connection_key: group}}})
    called = []

    async def sync_connection(*args):
        called.append(args)

    monkeypatch.setattr(lobehub, "_async_sync_connection", sync_connection)
    asyncio.run(_async_sync_runtime(hass, entry))

    assert called == [(hass, connection_key, group)]


def test_connection_sync_skips_when_no_registered_loaded_entries() -> None:
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_loaded_entries=lambda domain: [])
    )

    asyncio.run(_async_sync_connection(hass, "connection", {"entry_ids": {"missing"}}))


def test_load_runtime_discards_conversation_for_another_agent(monkeypatch) -> None:
    runtime = SimpleNamespace()
    entry = ConfigEntry(
        domain=DOMAIN,
        data={CONF_BASE_URL: "https://lobehub.example", CONF_API_KEY: "key"},
        options={
            CONF_SELECTED_AGENT: {"agent_id": "agent-1", "title": "One"},
            "conversation": {"id": "topic-2", "agent_id": "agent-2"},
        },
    )
    monkeypatch.setattr(lobehub, "build_runtime", lambda config: runtime)

    loaded_runtime, binding, conversation, binding_item, conversation_item = _load_runtime_from_entry(entry)

    assert loaded_runtime is runtime
    assert binding.agent_id == "agent-1"
    assert conversation is None
    assert binding_item == entry.options[CONF_SELECTED_AGENT]
    assert conversation_item == entry.options["conversation"]


def test_setup_entry_persists_normalized_runtime_state_and_removes_stale_devices(monkeypatch) -> None:
    binding = AgentBinding(agent_id="agent-1", title="One")
    conversation = ConversationState(id="topic-1", agent_id="agent-1", title="Topic")
    runtime = SimpleNamespace(
        agent_binding=binding,
        integration=SimpleNamespace(
            client=SimpleNamespace(validate_auth=lambda: None),
            _agent_binding=binding,
        ),
        configure=lambda configured_binding, configured_conversation: None,
        dump_binding=lambda: {"agent_id": "agent-1", "title": "One"},
        dump_conversation=lambda: {"id": "topic-1", "agent_id": "agent-1", "title": "Topic"},
    )
    entry = ConfigEntry(
        entry_id="entry-1",
        domain=DOMAIN,
        data={CONF_BASE_URL: "https://lobehub.example", CONF_API_KEY: "key"},
        options={},
    )
    hass = HomeAssistant()
    stale_removed = []
    registered = []
    monkeypatch.setattr(
        lobehub,
        "_load_runtime_from_entry",
        lambda value: (runtime, binding, conversation, None, None),
    )
    monkeypatch.setattr(lobehub, "_async_remove_stale_devices", lambda *args: stale_removed.append(True))
    monkeypatch.setattr(lobehub, "_register_connection_sync", lambda *args: registered.append(True) or (lambda: None))

    assert asyncio.run(async_setup_entry(hass, entry)) is True
    assert stale_removed == [True]
    assert registered == [True]
    assert entry.runtime_data is runtime
    assert entry.options[CONF_SELECTED_AGENT]["agent_id"] == "agent-1"
    assert entry.options["conversation"]["id"] == "topic-1"


def test_setup_entry_defers_when_validation_fails(monkeypatch) -> None:
    binding = AgentBinding(agent_id="agent-1", title="One")
    runtime = SimpleNamespace(
        configure=lambda configured_binding, configured_conversation: None,
        integration=SimpleNamespace(client=SimpleNamespace(validate_auth=lambda: (_ for _ in ()).throw(ApiError(503, "down")))),
        is_remote_agent_missing=lambda error: False,
    )
    entry = ConfigEntry(
        domain=DOMAIN,
        data={CONF_BASE_URL: "https://lobehub.example", CONF_API_KEY: "key"},
    )
    monkeypatch.setattr(
        lobehub,
        "_load_runtime_from_entry",
        lambda value: (runtime, binding, None, None, None),
    )

    with pytest.raises(ConfigEntryNotReady, match="Unable to connect"):
        asyncio.run(async_setup_entry(HomeAssistant(), entry))


def test_setup_entry_defers_on_unexpected_validation_error(monkeypatch) -> None:
    binding = AgentBinding(agent_id="agent-1", title="One")
    runtime = SimpleNamespace(
        configure=lambda configured_binding, configured_conversation: None,
        integration=SimpleNamespace(
            client=SimpleNamespace(
                validate_auth=lambda: (_ for _ in ()).throw(RuntimeError("bad response"))
            )
        ),
    )
    entry = ConfigEntry(
        domain=DOMAIN,
        data={CONF_BASE_URL: "https://lobehub.example", CONF_API_KEY: "key"},
    )
    monkeypatch.setattr(
        lobehub,
        "_load_runtime_from_entry",
        lambda value: (runtime, binding, None, None, None),
    )

    with pytest.raises(ConfigEntryNotReady, match="Unable to connect"):
        asyncio.run(async_setup_entry(HomeAssistant(), entry))


def test_setup_entry_removes_entry_when_its_agent_is_missing(monkeypatch) -> None:
    binding = AgentBinding(agent_id="deleted-agent", title="Deleted")
    runtime = SimpleNamespace(
        configure=lambda configured_binding, configured_conversation: None,
        integration=SimpleNamespace(
            client=SimpleNamespace(
                validate_auth=lambda: (_ for _ in ()).throw(ApiError(404, "missing"))
            )
        ),
        is_remote_agent_missing=lambda error: True,
    )
    entry = ConfigEntry(
        entry_id="entry-1",
        domain=DOMAIN,
        data={CONF_BASE_URL: "https://lobehub.example", CONF_API_KEY: "key"},
    )
    hass = HomeAssistant()
    removed = []

    async def async_remove(entry_id):
        removed.append(entry_id)

    hass.config_entries.async_remove = async_remove
    monkeypatch.setattr(
        lobehub,
        "_load_runtime_from_entry",
        lambda value: (runtime, binding, None, None, None),
    )

    async def exercise():
        assert await async_setup_entry(hass, entry) is False
        await asyncio.sleep(0)

    asyncio.run(exercise())
    assert removed == ["entry-1"]


def test_setup_entry_rejects_missing_connection_data(monkeypatch) -> None:
    entry = ConfigEntry(domain=DOMAIN, data={})
    monkeypatch.setattr(lobehub, "_load_runtime_from_entry", lambda value: (_ for _ in ()).throw(KeyError(CONF_API_KEY)))

    with pytest.raises(ConfigEntryError, match="Missing required"):
        asyncio.run(async_setup_entry(HomeAssistant(), entry))


def test_async_setup_registers_domain_services(monkeypatch) -> None:
    registered = []
    monkeypatch.setattr(lobehub, "async_register_services", lambda hass: registered.append(hass))
    hass = HomeAssistant()

    assert asyncio.run(async_setup(hass, {})) is True
    assert registered == [hass]


def test_remove_stale_devices_and_unload_platform(monkeypatch) -> None:
    removed = []
    registry = SimpleNamespace(async_remove_device=lambda device_id: removed.append(device_id))
    monkeypatch.setattr(lobehub.dr, "async_get", lambda hass: registry)
    monkeypatch.setattr(
        lobehub.dr,
        "async_entries_for_config_entry",
        lambda registry, entry_id: [SimpleNamespace(id="old-device-1"), SimpleNamespace(id="old-device-2")],
    )
    entry = ConfigEntry(entry_id="entry-1", domain=DOMAIN)
    hass = HomeAssistant()

    _async_remove_stale_devices(hass, entry)
    assert removed == ["old-device-1", "old-device-2"]
    assert asyncio.run(async_unload_entry(hass, entry)) is True


def test_config_flow_rejects_invalid_base_url() -> None:
    hass = HomeAssistant()
    flow = config_flow.LobeHubConfigFlow()
    flow.hass = hass

    result = asyncio.run(
        flow.async_step_user({CONF_BASE_URL: "not a url", CONF_API_KEY: "test-key"})
    )

    assert result["type"] == "form"
    assert result["errors"] == {CONF_BASE_URL: "invalid_base_url"}


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
