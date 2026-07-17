"""Behavior tests for the single-agent LobeHub integration facade."""

from __future__ import annotations

from custom_components.lobehub.integration import LobeHubIntegration
from custom_components.lobehub.models import AgentBinding, AgentSummary, IntegrationConfig, TopicSummary


class FakeClient:
    """Client double that models one remote agent and its topic history."""

    def __init__(self) -> None:
        self.responses: list[dict[str, object]] = []
        self.topics = {
            "topic-1": TopicSummary(id="topic-1", title="Coffee", agent_id="agent-1")
        }
        self.messages = {
            "topic-1": [{"id": "assistant-1", "role": "assistant", "content": "Ready"}]
        }

    def get_agent(self, agent_id: str, *, workspace_id: str | None = None) -> AgentSummary:
        assert agent_id == "agent-1"
        return AgentSummary(id=agent_id, title="Coffee", model="gpt-4o-mini", provider="openai")

    def get_topic(self, topic_id: str, *, workspace_id: str | None = None) -> TopicSummary:
        return self.topics[topic_id]

    def get_topic_messages(self, topic_id: str, *, workspace_id: str | None = None, all_pages: bool = False) -> list[dict[str, str]]:
        return self.messages[topic_id]

    def create_response(self, **kwargs: object) -> dict[str, object]:
        self.responses.append(kwargs)
        return {"topicId": "topic-1", "assistantMessageId": "assistant-1"}


def test_send_message_reuses_active_topic_and_returns_assistant_text() -> None:
    client = FakeClient()
    integration = LobeHubIntegration(
        client,  # type: ignore[arg-type]
        IntegrationConfig(api_key="test-key", base_url="https://lobehub.example"),
    )
    integration.configure_agent(
        AgentBinding(agent_id="agent-1", topic_policy="reuse", title="Coffee")
    )
    integration.switch_topic("topic-1")

    conversation, result = integration.send_message("Make coffee")

    assert conversation.id == "topic-1"
    assert result["final_output_text"] == "Ready"
    assert client.responses == [
        {
            "agent_id": "agent-1",
            "instruction": "Make coffee",
            "topic_id": "topic-1",
            "context": None,
            "device_id": None,
            "workspace_id": None,
        }
    ]


def test_new_topic_policy_does_not_reuse_the_active_topic() -> None:
    client = FakeClient()
    integration = LobeHubIntegration(
        client,  # type: ignore[arg-type]
        IntegrationConfig(api_key="test-key", base_url="https://lobehub.example"),
    )
    integration.configure_agent(AgentBinding(agent_id="agent-1", topic_policy="new"))
    integration.switch_topic("topic-1")

    integration.send_message("Start fresh")

    assert client.responses[0]["topic_id"] is None
