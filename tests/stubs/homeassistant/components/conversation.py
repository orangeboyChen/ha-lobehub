"""Small conversation API surface used by the integration tests."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.helpers.entity import Entity


class ConversationEntity(Entity):
    """Base conversation entity."""


class AbstractConversationAgent:
    """Marker base class for conversation agents."""


@dataclass
class ConversationInput:
    """Input passed to a conversation agent."""

    text: str
    language: str = "en"
    conversation_id: str | None = None
    device_id: str | None = None
    satellite_id: str | None = None


class ChatLog:
    """Conversation history placeholder."""


@dataclass
class ConversationResult:
    """Result returned by a conversation agent."""

    response: object
    conversation_id: str | None
    continue_conversation: bool


def async_set_agent(hass, entry, agent) -> None:
    hass.active_agent = (entry, agent)


def async_unset_agent(hass, entry) -> None:
    hass.removed_agent = entry
