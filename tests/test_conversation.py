"""Conversation entity behavior tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from custom_components.lobehub.conversation import LobeHubConversationEntity, async_setup_entry
from custom_components.lobehub.exceptions import LobeHubError
from custom_components.lobehub.models import AgentBinding, ConversationState
from homeassistant.components.conversation import ChatLog, ConversationInput


class _Runtime:
    agent_id = "agent-1"
    agent_binding = AgentBinding(agent_id="agent-1", title="LobeAI")

    def __init__(self, result=None, error=None) -> None:
        self.result = result or {"final_output_text": "Hello from LobeAI"}
        self.error = error

    def snapshot(self):
        return {"agent_id": self.agent_id}

    def dump_binding(self):
        return {"agent_id": self.agent_id}

    def dump_conversation(self):
        return {"id": "topic-1"}

    def send_conversation_message(self, *args, **kwargs):
        if self.error:
            raise self.error
        return ConversationState(id="topic-1", agent_id=self.agent_id), self.result


def _entry(runtime):
    return SimpleNamespace(entry_id="entry-1", title="Fallback", options={}, runtime_data=runtime)


def _hass(entry):
    updates = []
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_update_entry=lambda *args, **kwargs: updates.append(kwargs))
    )
    hass.async_add_executor_job = lambda func: _async_result(func())
    return hass, updates


async def _async_result(value):
    return value


def test_conversation_entity_success_and_lifecycle() -> None:
    runtime = _Runtime()
    entry = _entry(runtime)
    entity = LobeHubConversationEntity(entry)
    hass, updates = _hass(entry)
    entity.hass = hass
    writes = []
    entity.async_write_ha_state = lambda: writes.append(True)

    async def exercise():
        await entity.async_added_to_hass()
        result = await entity._async_handle_message(ConversationInput(text="Hi"), ChatLog())
        await entity.async_will_remove_from_hass()
        return result

    result = asyncio.run(exercise())
    assert entity.unique_id == "entry-1_agent-1"
    assert entity.supported_languages == "*"
    assert entity.extra_state_attributes == {"agent_id": "agent-1"}
    assert result.response.speech == "Hello from LobeAI"
    assert result.conversation_id == "topic-1"
    assert hass.active_agent[1] is entity
    assert hass.removed_agent is entry
    assert updates and writes


def test_conversation_entity_returns_errors_from_remote_and_empty_output() -> None:
    entry = _entry(_Runtime(error=LobeHubError("offline")))
    entity = LobeHubConversationEntity(entry)
    hass, _ = _hass(entry)
    entity.hass = hass

    error_result = asyncio.run(entity._async_handle_message(ConversationInput(text="Hi"), ChatLog()))
    assert error_result.response.error[1] == "offline"

    entry.runtime_data = _Runtime(result={"operation_status": {"currentState": {"error": "failed"}}})
    empty_result = asyncio.run(entity._async_handle_message(ConversationInput(text="Hi"), ChatLog()))
    assert empty_result.response.error[1] == "failed"


def test_async_setup_entry_adds_entity() -> None:
    added = []
    asyncio.run(async_setup_entry(SimpleNamespace(), _entry(_Runtime()), added.extend))
    assert isinstance(added[0], LobeHubConversationEntity)
