import pytest

from custom_components.lobehub.client import LobeHubClient
from custom_components.lobehub.integration import LobeHubIntegration
from custom_components.lobehub.models import AgentBinding
from custom_components.lobehub.models import IntegrationConfig


def make_transport(responses):
    calls = []

    def transport(method, url, headers, body):
        calls.append((method, url, headers, body))
        return responses.pop(0)

    return transport, calls


def test_end_to_end_agent_selection_and_task_flow():
    transport, calls = make_transport(
        [
            (200, {}, b'{"id":"user-1"}'),
            (
                200,
                {},
                b'{"agents":[{"id":"agent-1","title":"Coffee","model":"gpt-4o-mini","provider":"openai"}],"total":1}',
            ),
            (200, {}, b'{"data":{"id":"topic-1","title":"Coffee chat","agentId":"agent-1"}}'),
            (200, {}, b'{"data":{"id":"topic-2","title":"Buy coffee","agentId":"agent-1"}}'),
            (200, {}, b'{"data":{"id":"message-1"}}'),
            (200, {}, b'{"id":"resp-1","output_text":"done","output":[],"status":"completed"}'),
        ]
    )
    client = LobeHubClient(
        IntegrationConfig(api_key="sk-lh-1234567890abcd", base_url="https://lobehub.example"),
        transport=transport,
    )
    integration = LobeHubIntegration(
        client,
        IntegrationConfig(api_key="sk-lh-1234567890abcd", base_url="https://lobehub.example"),
    )

    assert integration.validate()["id"] == "user-1"
    bindings = integration.select_agents(["agent-1"])
    assert "agent-1" in bindings

    conversation = integration.create_conversation(agent_id="agent-1", title="Coffee chat")
    assert conversation.active_topic_id == "topic-1"

    conversation = integration.new_topic(conversation.id, "Buy coffee")
    assert conversation.active_topic_id == "topic-2"

    message = integration.send_message(conversation.id, "Order a latte")
    assert message["data"]["id"] == "message-1"

    task = integration.run_task(
        "agent-1",
        "Buy coffee at the nearest Luckin",
        context={"location": "near office"},
    )
    assert task.response_id == "resp-1"
    assert task.output_text == "done"
    assert task.status == "completed"

    assert len(calls) == 6
    assert calls[0][1].endswith("/api/v1/users/me")
    assert calls[2][1].endswith("/api/v1/topics")
    assert calls[5][1].endswith("/api/v1/responses")


def test_run_task_respects_agent_task_toggle():
    transport, _ = make_transport([(200, {}, b'{"id":"user-1"}')])
    client = LobeHubClient(
        IntegrationConfig(api_key="sk-lh-1234567890abcd", base_url="https://lobehub.example"),
        transport=transport,
    )
    integration = LobeHubIntegration(
        client,
        IntegrationConfig(api_key="sk-lh-1234567890abcd", base_url="https://lobehub.example"),
    )
    integration._agent_bindings["agent-1"] = AgentBinding(agent_id="agent-1", allow_task=False)

    with pytest.raises(Exception) as exc:
        integration.run_task("agent-1", "Buy coffee")
    assert "disabled" in str(exc.value)
