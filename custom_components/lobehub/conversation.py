"""Conversation platform for the LobeHub integration."""

from __future__ import annotations

from functools import partial
import logging
from typing import TYPE_CHECKING, Literal

from homeassistant.components import conversation
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entry_state import build_options_with_runtime_state
from .exceptions import LobeHubError
from .integration import extract_operation_error, extract_operation_status_reason

if TYPE_CHECKING:
    from . import LobeHubConfigEntry

_LOGGER = logging.getLogger(__name__)


def _persist_runtime_state(entry: LobeHubConfigEntry) -> dict[str, object]:
    """Serialize the current runtime state for config-entry options."""

    return build_options_with_runtime_state(entry.options, entry.runtime_data)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LobeHubConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the LobeHub conversation entity."""

    async_add_entities([LobeHubConversationEntity(entry)])


class LobeHubConversationEntity(
    conversation.ConversationEntity, conversation.AbstractConversationAgent
):
    """Conversation agent entity backed by one configured LobeHub agent."""

    _attr_has_entity_name = True
    _attr_translation_key = "conversation"
    _attr_icon = "mdi:robot-outline"

    def __init__(self, entry: LobeHubConfigEntry) -> None:
        """Initialize the conversation entity."""

        self.entry = entry
        runtime = entry.runtime_data
        agent_id = runtime.agent_id or entry.entry_id
        agent_name = (
            runtime.agent_binding.title
            if runtime.agent_binding and runtime.agent_binding.title
            else entry.title
        )
        self._attr_name = agent_name
        self._attr_unique_id = f"{entry.entry_id}_{agent_id}"

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Return supported languages."""

        return MATCH_ALL

    async def async_added_to_hass(self) -> None:
        """Register the entity as the active conversation agent."""

        await super().async_added_to_hass()
        conversation.async_set_agent(self.hass, self.entry, self)

    async def async_will_remove_from_hass(self) -> None:
        """Unregister the entity as the active conversation agent."""

        conversation.async_unset_agent(self.hass, self.entry)
        await super().async_will_remove_from_hass()

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose the current remote topic and agent settings."""

        return self.entry.runtime_data.snapshot()

    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        """Send the utterance to LobeHub and return a speech response."""

        del chat_log

        runtime = self.entry.runtime_data
        send_message = partial(
            runtime.send_conversation_message,
            user_input.text,
            conversation_id=user_input.conversation_id,
            context={
                "device_id": getattr(user_input, "device_id", None),
                "language": user_input.language,
                "satellite_id": getattr(user_input, "satellite_id", None),
            },
        )
        try:
            result_conversation, result = await self.hass.async_add_executor_job(
                send_message
            )
        except LobeHubError as err:
            _LOGGER.warning("LobeHub conversation request failed: %s", err)
            response = intent.IntentResponse(language=user_input.language)
            response.async_set_error(
                intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                str(err),
            )
            return conversation.ConversationResult(
                response=response,
                conversation_id=user_input.conversation_id,
                continue_conversation=True,
            )

        self.hass.config_entries.async_update_entry(
            self.entry,
            options=_persist_runtime_state(self.entry),
        )
        self.async_write_ha_state()

        speech = result.get("final_output_text", "")
        response = intent.IntentResponse(language=user_input.language)
        if isinstance(speech, str) and speech:
            response.async_set_speech(speech)
        else:
            status_reason = extract_operation_status_reason(
                result.get("operation_status")
            )
            response.async_set_error(
                intent.IntentResponseErrorCode.FAILED_TO_HANDLE,
                extract_operation_error(result.get("operation_status"))
                or (
                    f"LobeHub finished without text output (status: {status_reason})"
                    if status_reason
                    else "LobeHub did not return a response"
                ),
            )

        return conversation.ConversationResult(
            response=response,
            conversation_id=result_conversation.id,
            continue_conversation=True,
        )
