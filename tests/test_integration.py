"""Behavior tests for the single-agent LobeHub integration facade."""

from __future__ import annotations

import pytest

from custom_components.lobehub.exceptions import ApiError, ValidationError
from custom_components.lobehub.integration import (
    LobeHubIntegration,
    extract_assistant_text,
    extract_latest_assistant_text,
    extract_operation_error,
    extract_operation_status_reason,
    extract_operation_text,
)
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


def make_integration(client: FakeClient | None = None) -> tuple[LobeHubIntegration, FakeClient]:
    fake_client = client or FakeClient()
    integration = LobeHubIntegration(
        fake_client,  # type: ignore[arg-type]
        IntegrationConfig(api_key="test-key", base_url="https://lobehub.example"),
    )
    integration.configure_agent(
        AgentBinding(
            agent_id="agent-1",
            title="Coffee",
            model="gpt-4o-mini",
            provider="openai",
        )
    )
    return integration, fake_client


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


def test_message_and_operation_extractors_handle_lobehub_payload_shapes() -> None:
    messages = [
        {"id": "old", "role": "assistant", "content": "Old"},
        {"id": "loading", "sender": "model", "content": "..."},
        {
            "messageId": "target",
            "role": "assistant",
            "content": [{"text": "Fresh"}, {"content": " answer"}],
        },
    ]
    status = {
        "currentState": {
            "status": "waiting_approval",
            "error": {"body": {"detail": "Need confirmation"}},
            "response": {"content": "Completed from state"},
        }
    }

    assert extract_assistant_text(messages, "target") == "Fresh\nanswer"
    assert extract_latest_assistant_text(messages) == "Fresh\nanswer"
    assert extract_operation_text(status) == "Completed from state"
    assert extract_operation_error(status) == "Need confirmation"
    assert extract_operation_status_reason(status) == "waiting_approval"


def test_configure_agent_preserves_local_settings_over_remote_defaults() -> None:
    integration, _ = make_integration()

    configured = integration.configure_agent(
        AgentBinding(
            agent_id="agent-1",
            enabled=False,
            model="custom-model",
            provider="custom-provider",
            runtime="device",
            bound_device_id="mac",
            topic_policy="new",
        )
    )

    assert configured.enabled is False
    assert configured.model == "custom-model"
    assert configured.provider == "custom-provider"
    assert configured.runtime == "device"
    assert configured.bound_device_id == "mac"
    assert configured.topic_policy == "new"
    assert configured.title == "Coffee"


def test_send_message_uses_explicit_context_topic_and_stream_fallback() -> None:
    class StreamingClient(FakeClient):
        def get_topic(self, topic_id: str, *, workspace_id: str | None = None) -> TopicSummary:
            if topic_id == "explicit-topic":
                return TopicSummary(id=topic_id, title="Explicit", agent_id="agent-1")
            raise ApiError(404, "not found")

        def create_response(self, **kwargs: object) -> dict[str, object]:
            self.responses.append(kwargs)
            return {
                "topicId": "remote-topic",
                "assistantMessageId": "message-2",
                "operationId": "operation-1",
            }

        def wait_for_operation(self, operation_id: str, *, workspace_id: str | None = None) -> dict[str, object]:
            assert operation_id == "operation-1"
            return {"currentState": {"status": "completed"}}

        def get_agent_stream_events(self, operation_id: str, *, workspace_id: str | None = None) -> list[dict[str, object]]:
            return [{"event": "stream_chunk", "data": {"content": "From stream"}}]

        def collect_stream_text(self, events: object) -> str:
            return "From stream"

    integration, client = make_integration(StreamingClient())

    conversation, result = integration.send_message(
        "Use the supplied topic", context={"topic_id": "explicit-topic"}
    )

    assert client.responses[0]["topic_id"] == "explicit-topic"
    assert conversation.id == "remote-topic"
    assert result["final_output_text"] == "From stream"
    assert result["operation_status"] == {"currentState": {"status": "completed"}}


def test_switch_topic_rejects_deleted_and_other_agent_topics() -> None:
    class MissingTopicClient(FakeClient):
        def get_topic(
            self, topic_id: str, *, workspace_id: str | None = None
        ) -> TopicSummary:
            if topic_id == "deleted":
                raise ApiError(404, "Topic not found")
            return super().get_topic(topic_id, workspace_id=workspace_id)

    integration, client = make_integration(MissingTopicClient())

    client.topics["wrong-agent"] = TopicSummary(
        id="wrong-agent", title="Wrong", agent_id="other-agent"
    )
    client.messages["wrong-agent"] = []
    with pytest.raises(ValidationError, match="does not belong"):
        integration.switch_topic("wrong-agent")

    with pytest.raises(ValidationError, match="Topic not found"):
        integration.switch_topic("deleted")


def test_run_task_validates_parent_message_before_continuing() -> None:
    integration, client = make_integration()
    integration.switch_topic("topic-1")

    with pytest.raises(ValidationError, match="does not belong"):
        integration.run_task("Continue", previous_response_id="missing-message")

    assert client.responses == []


def test_update_agent_settings_requires_bound_device_for_device_runtime() -> None:
    integration, _ = make_integration()

    with pytest.raises(ValidationError, match="requires a bound device"):
        integration.update_agent_settings(
            model="gpt-4o-mini",
            provider="openai",
            runtime="device",
            bound_device_id=None,
            topic_policy="reuse",
        )


def test_discovery_and_agent_settings_update_refresh_the_active_conversation() -> None:
    class SettingsClient(FakeClient):
        def __init__(self) -> None:
            super().__init__()
            self.updated: list[dict[str, object]] = []

        def list_agents(self) -> list[AgentSummary]:
            return [
                AgentSummary(id="agent-1", title="Coffee"),
                AgentSummary(id="agent-2", title="Tea", workspace_id="workspace-2"),
            ]

        def get_agent(self, agent_id: str, *, workspace_id: str | None = None) -> AgentSummary:
            return AgentSummary(
                id=agent_id,
                title="Coffee" if agent_id == "agent-1" else "Tea",
                workspace_id=workspace_id,
            )

        def discover_remote_options(self, *, workspace_id: str | None = None) -> object:
            return {"workspace_id": workspace_id}

        def list_devices(self, *, workspace_id: str | None = None) -> list[dict[str, object]]:
            return [{"id": "mac", "workspace_id": workspace_id}]

        def update_agent(self, agent_id: str, **changes: object) -> None:
            self.updated.append({"agent_id": agent_id, **changes})

    integration, client = make_integration(SettingsClient())
    integration.switch_topic("topic-1")

    discovered = integration.discover_agents()
    assert set(discovered) == {"agent-1", "agent-2"}
    assert integration.discover_remote_options() == {"workspace_id": None}
    assert integration.list_devices() == [{"id": "mac", "workspace_id": None}]

    updated = integration.update_agent_settings(
        model="new-model",
        provider="new-provider",
        runtime="gateway",
        bound_device_id=None,
        topic_policy="new",
    )

    assert updated.runtime == "auto"
    assert client.updated[0]["agencyConfig"] == {
        "executionTarget": "auto",
        "boundDeviceId": None,
    }
    assert integration.conversation is not None
    assert integration.conversation.model == "new-model"
    assert integration.conversation.topic_policy == "new"


def test_restore_conversation_handles_missing_errors_workspace_and_persistence() -> None:
    class RestoreClient(FakeClient):
        def __init__(self) -> None:
            super().__init__()
            self.messages["other"] = []

        def get_topic(self, topic_id: str, *, workspace_id: str | None = None) -> TopicSummary:
            if topic_id == "missing":
                raise ApiError(404, "missing")
            if topic_id == "unavailable":
                raise ApiError(500, "temporary")
            if topic_id == "other":
                return TopicSummary(id="other", title="Other", agent_id="agent-2")
            return TopicSummary(
                id=topic_id,
                title="Restored",
                agent_id="agent-1",
                workspace_id="workspace-1",
            )

    integration, _ = make_integration(RestoreClient())
    binding = integration.agent_binding
    assert binding is not None
    fallback = integration.switch_topic("topic-1")

    assert integration.restore_conversation("missing", binding, fallback=fallback) is None
    assert integration.restore_conversation("unavailable", binding, fallback=fallback) is fallback
    assert integration.restore_conversation("other", binding) is None

    restored = integration.restore_conversation("topic-1", binding, persist=False)
    assert restored is not None
    assert restored.workspace_id == "workspace-1"
    assert restored.title == "Restored"
    assert integration.conversation is fallback


def test_new_topic_and_send_message_handle_missing_remote_conversation() -> None:
    class NewTopicClient(FakeClient):
        def create_topic(self, *, title: str, agent_id: str, workspace_id: str | None = None) -> TopicSummary:
            assert (title, agent_id, workspace_id) == ("Fresh", "agent-1", None)
            self.messages["fresh"] = []
            return TopicSummary(id="fresh", title="", workspace_id=workspace_id)

        def create_response(self, **kwargs: object) -> dict[str, object]:
            self.responses.append(kwargs)
            return {"topicId": "gone"}

        def get_topic(self, topic_id: str, *, workspace_id: str | None = None) -> TopicSummary:
            if topic_id == "gone":
                raise ApiError(404, "not found")
            return super().get_topic(topic_id, workspace_id=workspace_id)

    integration, _ = make_integration(NewTopicClient())
    created = integration.new_topic("Fresh")
    assert created.id == "fresh"
    assert created.title == "Fresh"

    conversation, result = integration.send_message("Hello", conversation_id="unknown")
    assert conversation.id == "gone"
    assert conversation.messages == []
    assert result["final_output_text"] == ""


def test_run_task_handles_operation_status_fallback_and_explicit_topic() -> None:
    class TaskClient(FakeClient):
        def create_response(self, **kwargs: object) -> dict[str, object]:
            self.responses.append(kwargs)
            return {
                "topicId": "topic-1",
                "messageId": "response-1",
                "operationId": "operation-1",
            }

        def wait_for_operation(self, operation_id: str, *, workspace_id: str | None = None) -> dict[str, object]:
            return {"currentState": {"status": "waiting", "result": "Operation output"}}

    integration, client = make_integration(TaskClient())
    result = integration.run_task("Do work", context={"topicId": "topic-1"})

    assert client.responses[0]["topic_id"] == "topic-1"
    assert result.response_id == "response-1"
    assert result.output_text == "Ready"
    assert result.status == "waiting"
    assert result.topic_id == "topic-1"


def test_run_task_falls_back_to_stream_and_rejects_invalid_continuation() -> None:
    class StreamTaskClient(FakeClient):
        def create_response(self, **kwargs: object) -> dict[str, object]:
            self.responses.append(kwargs)
            return {"topicId": "new-topic", "operationId": "operation-1", "status": "queued"}

        def get_topic(self, topic_id: str, *, workspace_id: str | None = None) -> TopicSummary:
            if topic_id == "new-topic":
                raise ApiError(404, "not found")
            return super().get_topic(topic_id, workspace_id=workspace_id)

        def wait_for_operation(self, operation_id: str, *, workspace_id: str | None = None) -> dict[str, object]:
            return {"currentState": {"status": ""}}

        def get_agent_stream_events(self, operation_id: str, *, workspace_id: str | None = None) -> list[dict[str, object]]:
            return [{"data": {"content": "stream"}}]

        def collect_stream_text(self, events: object) -> str:
            return "Stream output"

    integration, _ = make_integration(StreamTaskClient())
    result = integration.run_task("Do work")
    assert result.output_text == "Stream output"
    assert result.status == "completed"

    integration = LobeHubIntegration(
        FakeClient(),  # type: ignore[arg-type]
        IntegrationConfig(api_key="test-key", base_url="https://lobehub.example"),
    )
    integration.configure_agent(AgentBinding(agent_id="agent-1"))
    with pytest.raises(ValidationError, match="active topic"):
        integration.run_task("Continue", previous_response_id="assistant-1")


def test_saved_task_listing_detail_and_execution_paths() -> None:
    class SavedTaskClient(FakeClient):
        def list_tasks(self, **kwargs: object) -> dict[str, object]:
            assert kwargs["limit"] == 2
            return {"tasks": [{"id": "task-1", "assigneeAgentId": "agent-1"}], "total": "bad"}

        def get_task_detail(self, task: str, *, workspace_id: str | None = None) -> dict[str, object]:
            return {"id": task, "name": "Coffee task"}

        def get_task_topics(self, task: str, *, workspace_id: str | None = None) -> list[dict[str, object]]:
            return [{"topicId": "topic-1", "title": "Coffee"}]

        def run_saved_task(self, task: str, **kwargs: object) -> dict[str, object]:
            return {"taskId": task, "taskIdentifier": "COFFEE-1", "topicId": "topic-1", "assistantMessageId": "assistant-1"}

    integration, _ = make_integration(SavedTaskClient())
    listed = integration.list_tasks(limit=2, statuses=["open"])
    assert listed["total"] == 1
    assert listed["tasks"][0]["assignee_agent_id"] == "agent-1"
    detail = integration.get_task("task-1")
    assert detail["task"]["name"] == "Coffee task"
    assert detail["topics"][0]["topic_id"] == "topic-1"
    result = integration.run_saved_task("task-1", continue_topic_id="topic-1", prompt="Go")
    assert result.task_identifier == "COFFEE-1"
    assert result.output_text == "Ready"

    with pytest.raises(ValidationError, match="does not belong"):
        integration.run_saved_task("task-1", continue_topic_id="other-topic")


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ApiError(404, "anything"), True),
        (ApiError(500, "resource not found"), True),
        (ApiError(500, "bad", payload={"error": "Not Found"}), True),
        (ApiError(500, "bad"), False),
    ],
)
def test_not_found_error_detection(error: ApiError, expected: bool) -> None:
    assert LobeHubIntegration._is_not_found_error(error) is expected


def test_extractors_cover_empty_payloads_and_all_supported_operation_shapes() -> None:
    assert extract_assistant_text(
        [
            {"id": "target", "role": "user", "content": "ignored"},
            {"role": "assistant", "content": "fallback"},
        ],
        "target",
    ) == "fallback"
    assert extract_assistant_text([{"role": "assistant", "content": "..."}]) == ""
    assert extract_latest_assistant_text([{"role": "user", "content": "hello"}]) == ""

    assert extract_operation_error(None) == ""
    assert extract_operation_error({}) == ""
    assert extract_operation_error({"currentState": {"error": "Oops"}}) == "Oops"
    assert extract_operation_error({"currentState": {"error": {"type": "blocked"}}}) == "blocked"
    assert extract_operation_error({"currentState": {"error": {}}}) == ""
    assert extract_operation_text(None) == ""
    assert extract_operation_text({"result": "...", "resultContent": {"text": "Object"}}) == "Object"
    assert extract_operation_text({"currentState": {"content": "State content"}}) == "State content"
    assert extract_operation_text({"currentState": {"response": {"text": "State object"}}}) == "State object"
    assert extract_operation_text({"response": {"content": []}}) == ""
    assert extract_operation_status_reason(None) == ""
    assert extract_operation_status_reason({}) == ""
    assert extract_operation_status_reason({"currentState": {"reason": "finished"}}) == "finished"
    assert extract_operation_status_reason({"currentState": {}}) == ""


def test_unconfigured_integration_requires_an_agent_and_validate_delegates() -> None:
    class ValidationClient(FakeClient):
        def validate_auth(self) -> dict[str, str]:
            return {"user": "orangeboy"}

    integration = LobeHubIntegration(
        ValidationClient(),  # type: ignore[arg-type]
        IntegrationConfig(api_key="test-key", base_url="https://lobehub.example"),
    )
    assert integration.validate() == {"user": "orangeboy"}
    assert integration.agent_binding is None
    assert integration.conversation is None
    with pytest.raises(ValidationError, match="No LobeHub agent"):
        integration.discover_remote_options()
    with pytest.raises(ValidationError, match="No LobeHub agent"):
        integration.list_devices()


def test_topic_validation_and_context_resolution_error_paths() -> None:
    class TopicClient(FakeClient):
        def get_topic(self, topic_id: str, *, workspace_id: str | None = None) -> TopicSummary:
            if topic_id == "missing":
                raise ApiError(404, "missing")
            if topic_id == "broken":
                raise ApiError(500, "broken")
            if topic_id == "wrong-workspace":
                return TopicSummary(id=topic_id, title="Wrong", agent_id="agent-1", workspace_id="elsewhere")
            return super().get_topic(topic_id, workspace_id=workspace_id)

    integration, _ = make_integration(TopicClient())
    binding = integration.agent_binding
    assert binding is not None
    binding.workspace_id = "workspace-1"
    assert integration._validate_topic_for_binding(None, binding) is None
    assert integration._validate_topic_for_binding("missing", binding, allow_missing=True) is None
    with pytest.raises(ValidationError, match="Topic not found"):
        integration._validate_topic_for_binding("missing", binding)
    with pytest.raises(ApiError, match="broken"):
        integration._validate_topic_for_binding("broken", binding)
    with pytest.raises(ValidationError, match="workspace"):
        integration._validate_topic_for_binding("wrong-workspace", binding)
    assert integration._context_topic_id("not-a-map") is None
    assert integration._context_topic_id({"topic_id": 1}) is None
    assert integration._context_topic_id({"topic_id": "fallback"}) == "fallback"
    assert integration._resolve_target_topic_id("reuse") is None
    assert integration._resolve_target_topic_id("new") is None
    with pytest.raises(ValidationError, match="requires a topic"):
        integration._validate_message_for_topic("anything", None)


def test_continue_existing_task_and_saved_task_operation_fallbacks() -> None:
    class ContinuationClient(FakeClient):
        def __init__(self) -> None:
            super().__init__()
            self.fail_history = False

        def create_response(self, **kwargs: object) -> dict[str, object]:
            self.responses.append(kwargs)
            return {"topicId": "topic-1", "assistantMessageId": "assistant-1", "status": "queued"}

        def get_task_topics(self, task: str, *, workspace_id: str | None = None) -> list[dict[str, object]]:
            return [{"id": "topic-1"}]

        def run_saved_task(self, task: str, **kwargs: object) -> dict[str, object]:
            return {"topicId": "topic-1", "operationId": "saved-op", "status": "queued"}

        def get_topic_messages(self, topic_id: str, *, workspace_id: str | None = None, all_pages: bool = False) -> list[dict[str, str]]:
            if self.fail_history and topic_id == "topic-1" and not all_pages:
                raise ApiError(500, "history unavailable")
            return super().get_topic_messages(topic_id, workspace_id=workspace_id, all_pages=all_pages)

        def wait_for_operation(self, operation_id: str, *, workspace_id: str | None = None) -> dict[str, object]:
            return {"currentState": {"status": "finished", "result": "Saved output"}}

    integration, client = make_integration(ContinuationClient())
    integration.switch_topic("topic-1")
    result = integration.run_task("Continue", previous_response_id="assistant-1")
    assert client.responses[0]["previous_response_id"] == "assistant-1"
    assert result.status == "queued"

    client.fail_history = True
    saved = integration.run_saved_task("task-1", continue_topic_id="topic-1")
    assert saved.output_text == "Saved output"
    assert saved.status == "finished"
