from urllib.error import URLError

import pytest

from custom_components.lobehub import client as client_module
from custom_components.lobehub.client import LobeHubClient
from custom_components.lobehub.exceptions import ApiError
from custom_components.lobehub.models import IntegrationConfig


def make_transport(responses):
    calls = []

    def transport(method, url, headers, body):
        calls.append((method, url, headers, body))
        response = responses.pop(0)
        return response

    return transport, calls


def test_validate_auth_uses_bearer_api_key():
    transport, calls = make_transport(
        [(200, {}, b'{"id":"user-1","email":"a@example.com"}')]
    )
    client = LobeHubClient(
        IntegrationConfig(api_key="sk-lh-1234567890abcd", base_url="https://lobehub.example"),
        transport=transport,
    )

    data = client.validate_auth()

    assert data["id"] == "user-1"
    assert calls[0][0] == "GET"
    assert calls[0][1] == "https://lobehub.example/api/v1/users/me"
    assert calls[0][2]["Authorization"] == "Bearer sk-lh-1234567890abcd"


def test_list_agents_parses_agent_items():
    transport, calls = make_transport(
        [
            (
                200,
                {},
                b'{"result":{"data":{"json":{"id":"agent-inbox","slug":"inbox"}}}}',
            ),
            (
                200,
                {},
                b'{"result":{"data":{"json":[]}}}',
            ),
            (
                200,
                {},
                b'{"result":{"data":{"json":[{"id":"agent-1","title":"Coffee","model":"gpt-4o-mini","provider":"openai"}]}}}',
            )
        ]
    )
    client = LobeHubClient(
        IntegrationConfig(api_key="sk-lh-1234567890abcd", base_url="https://lobehub.example"),
        transport=transport,
    )

    agents = list(client.list_agents())

    assert [(agent.id, agent.title) for agent in agents] == [
        ("agent-inbox", "Lobe AI"),
        ("agent-1", "Coffee"),
    ]
    assert agents[1].model == "gpt-4o-mini"
    assert "X-Workspace-Id" not in calls[0][2]


def test_list_devices_derives_online_status_from_legacy_channels():
    transport, _ = make_transport(
        [
            (
                200,
                {},
                b'{"result":{"data":{"json":[{"deviceId":"device-online","hostname":"MacBook","channels":[{"channel":"gateway"}]},{"deviceId":"device-offline","friendlyName":"Desktop","channels":[]},{"deviceId":"explicit-offline","friendlyName":"Stopped device","online":false,"channels":[{"channel":"stale"}]}]}}}',
            )
        ]
    )
    client = LobeHubClient(
        IntegrationConfig(api_key="sk-lh-1234567890abcd", base_url="https://lobehub.example"),
        transport=transport,
    )

    devices = client.list_devices()

    assert devices == [
        {
            "device_id": "device-online",
            "label": "MacBook",
            "online": True,
            "scope": None,
            "platform": None,
        },
        {
            "device_id": "device-offline",
            "label": "Desktop",
            "online": False,
            "scope": None,
            "platform": None,
        },
        {
            "device_id": "explicit-offline",
            "label": "Stopped device",
            "online": False,
            "scope": None,
            "platform": None,
        },
    ]


def test_create_topic_and_message_reply():
    transport, calls = make_transport(
        [
            (200, {}, b'{"result":{"data":{"json":"topic-1"}}}'),
            (200, {}, b'{"result":{"data":{"json":{"id":"msg-1"}}}}'),
        ]
    )
    client = LobeHubClient(
        IntegrationConfig(api_key="sk-lh-1234567890abcd", base_url="https://lobehub.example"),
        transport=transport,
    )

    topic = client.create_topic(title="Morning run", agent_id="agent-1")
    reply = client.create_message_reply(content="Hello", topic_id=topic.id, model="gpt-4o-mini")

    assert topic.id == "topic-1"
    assert reply["id"] == "msg-1"
    assert calls[0][0] == "POST"
    assert calls[0][1].endswith("/trpc/lambda/topic.createTopic")
    assert calls[1][1].endswith("/trpc/lambda/message.createMessage")


def test_request_serializes_json_parameters_and_custom_headers() -> None:
    transport, calls = make_transport([(200, {}, b'{"data":{"ok":true}}')])
    client = LobeHubClient(
        IntegrationConfig(api_key="key", base_url="https://lobehub.example/base/"),
        transport=transport,
    )

    assert client.request(
        "post",
        "/api/test",
        json_body={"message": "hello"},
        params={"scope": "one", "ignored": None},
        headers={"X-Test": "present"},
    ) == {"data": {"ok": True}}

    method, url, headers, body = calls[0]
    assert method == "POST"
    assert url == "https://lobehub.example/base/api/test?scope=one"
    assert headers["Authorization"] == "Bearer key"
    assert headers["Content-Type"] == "application/json"
    assert headers["X-Test"] == "present"
    assert body == b'{"message":"hello"}'


@pytest.mark.parametrize(
    ("response", "status_code", "message"),
    [
        ((401, {}, b'{"message":"invalid key"}'), 401, "invalid key"),
        ((500, {}, b"plain failure"), 500, "plain failure"),
    ],
)
def test_request_raises_api_error_for_unsuccessful_responses(
    response: tuple[int, dict[str, str], bytes], status_code: int, message: str
) -> None:
    transport, _ = make_transport([response])
    client = LobeHubClient(
        IntegrationConfig(api_key="key", base_url="https://lobehub.example"),
        transport=transport,
    )

    with pytest.raises(ApiError, match=message) as error:
        client.request("GET", "/unavailable")

    assert error.value.status_code == status_code


def test_request_wraps_transport_errors() -> None:
    def failing_transport(*_args: object) -> tuple[int, dict[str, str], bytes]:
        raise URLError("unreachable")

    client = LobeHubClient(
        IntegrationConfig(api_key="key", base_url="https://lobehub.example"),
        transport=failing_transport,  # type: ignore[arg-type]
    )

    with pytest.raises(ApiError, match="unreachable") as error:
        client.request("GET", "/api/v1/users/me")

    assert error.value.status_code == 0


def test_list_agents_includes_workspaces_and_preserves_workspace_ids() -> None:
    transport, calls = make_transport(
        [
            (200, {}, b'{"result":{"data":{"json":{"id":"inbox","slug":"inbox"}}}}'),
            (200, {}, b'{"result":{"data":{"json":[{"id":"workspace-1"}]}}}'),
            (200, {}, b'{"result":{"data":{"json":[{"id":"personal","title":"Personal"}]}}}'),
            (200, {}, b'{"result":{"data":{"json":[{"id":"team","title":"Team"}]}}}'),
        ]
    )
    client = LobeHubClient(
        IntegrationConfig(api_key="key", base_url="https://lobehub.example"),
        transport=transport,
    )

    agents = list(client.list_agents(page=2, page_size=10))

    assert [(agent.id, agent.title, agent.workspace_id) for agent in agents] == [
        ("inbox", "Lobe AI", None),
        ("personal", "Personal", None),
        ("team", "Team", "workspace-1"),
    ]
    assert "offset%22%3A10" in calls[2][1]
    assert calls[3][2]["X-Workspace-Id"] == "workspace-1"


def test_get_agent_tries_workspace_scopes_before_personal() -> None:
    transport, calls = make_transport(
        [
            (200, {}, b'{"result":{"data":{"json":[{"id":"workspace-1"}]}}}'),
            (200, {}, b'{"result":{"data":{"json":{"id":"agent-1","title":"Team"}}}}'),
        ]
    )
    client = LobeHubClient(
        IntegrationConfig(api_key="key", base_url="https://lobehub.example"),
        transport=transport,
    )

    agent = client.get_agent("agent-1")

    assert agent.workspace_id == "workspace-1"
    assert calls[1][2]["X-Workspace-Id"] == "workspace-1"


def test_stream_parsing_and_text_collection_cover_lobehub_event_shapes() -> None:
    events = LobeHubClient._parse_sse_events(
        'id: 1\nevent: stream_chunk\ndata: {"chunkType":"text","content":"Hello "}\n\n'
        'id: 2\nevent: stream_chunk\ndata: {"chunkType":"content_part","contentParts":[{"text":"world"}]}\n\n'
        'event: agent_runtime_end\ndata: {"response":{"answer":"!"}}\n\n'
    )

    assert [event["id"] for event in events[:2]] == ["1", "2"]
    assert LobeHubClient.collect_stream_text(events) == "Hello world!"


def test_discover_remote_options_combines_runtime_state_profile_and_devices() -> None:
    transport, _ = make_transport(
        [
            (200, {}, b'{"id":"me","provider":"openai","runtimes":["gateway"]}'),
            (200, {}, b'{"result":{"data":{"json":{"id":"inbox","slug":"inbox"}}}}'),
            (200, {}, b'{"result":{"data":{"json":[]}}}'),
            (200, {}, b'{"result":{"data":{"json":[]}}}'),
            (200, {}, b'{"result":{"data":{"json":{"enabledAiModels":[{"id":"gpt-4o","providerId":"openai"}],"enabledAiProviders":[{"id":"anthropic"}]}}}}'),
            (200, {}, b'{"result":{"data":{"json":[{"deviceId":"mac","online":true}]}}}'),
            (200, {}, b'{"result":{"data":{"json":[]}}}'),
            (200, {}, b'{"result":{"data":{"json":{"id":"inbox","slug":"inbox","model":"gpt-4o","provider":"openai"}}}}'),
        ]
    )
    client = LobeHubClient(
        IntegrationConfig(api_key="key", base_url="https://lobehub.example"),
        transport=transport,
    )

    options = client.discover_remote_options()

    assert options.models == ["gpt-4o"]
    assert options.providers == ["anthropic", "openai"]
    assert "auto" in options.runtimes
    assert options.devices[0]["device_id"] == "mac"


def test_client_topic_and_message_operations_handle_invalid_and_workspace_data() -> None:
    client = LobeHubClient(
        IntegrationConfig(api_key="key", base_url="https://lobehub.example")
    )
    mutations: list[tuple[str, dict[str, object], str | None]] = []
    queries: list[tuple[str, dict[str, object], str | None]] = []

    def mutation(
        procedure: str, payload: dict[str, object] | None = None, *, workspace_id: str | None = None
    ) -> object:
        mutations.append((procedure, payload or {}, workspace_id))
        return "topic-1" if procedure == "topic.createTopic" else {"id": "message-1"}

    def query(
        procedure: str, payload: dict[str, object] | None = None, *, workspace_id: str | None = None
    ) -> object:
        queries.append((procedure, payload or {}, workspace_id))
        if procedure == "topic.getTopicDetail":
            return {"id": "topic-1", "title": "One"}
        return {"items": [{"id": "topic-1", "title": "One"}]}

    client._trpc_mutation = mutation  # type: ignore[method-assign]
    client._trpc_query = query  # type: ignore[method-assign]

    topic = client.create_topic(title="One", agent_id="agent-1", workspace_id="team")
    detail = client.get_topic("topic-1", workspace_id="team")
    topics = list(client.list_topics(agent_id="agent-1", workspace_id="team"))
    message = client.create_message_reply(
        content="Hello", topic_id="topic-1", metadata={"source": "ha"}, workspace_id="team"
    )

    assert topic.raw["workspaceId"] == "team"
    assert detail.workspace_id == "team"
    assert topics[0].id == "topic-1"
    assert message == {"id": "message-1"}
    assert mutations[0] == (
        "topic.createTopic",
        {"agentId": "agent-1", "favorite": None, "groupId": None, "title": "One"},
        "team",
    )
    assert mutations[1][1]["metadata"] == {"source": "ha"}
    assert queries[0][0] == "topic.getTopicDetail"


def test_client_rejects_invalid_topic_and_missing_topic_data() -> None:
    client = LobeHubClient(IntegrationConfig(api_key="key", base_url="https://lobehub.example"))
    client._trpc_mutation = lambda *_args, **_kwargs: {}  # type: ignore[method-assign]
    client._trpc_query = lambda *_args, **_kwargs: None  # type: ignore[method-assign]

    with pytest.raises(ApiError, match="Invalid topic id"):
        client.create_topic(title="One")
    with pytest.raises(ApiError, match="Topic not found"):
        client.get_topic("missing")
    assert list(client.list_topics()) == []


def test_client_tasks_response_and_operation_helpers() -> None:
    client = LobeHubClient(IntegrationConfig(api_key="key", base_url="https://lobehub.example"))
    calls: list[tuple[str, dict[str, object], str | None]] = []

    def query(
        procedure: str, payload: dict[str, object] | None = None, *, workspace_id: str | None = None
    ) -> object:
        calls.append((procedure, payload or {}, workspace_id))
        return {
            "task.list": {"data": [{"id": "task-1"}, "bad"], "total": "unknown"},
            "task.detail": {"data": {"id": "task-1"}},
            "task.getTopics": {"data": [{"id": "topic-1"}, "bad"]},
            "aiAgent.getOperationStatus": {"isCompleted": True},
        }[procedure]

    client._trpc_query = query  # type: ignore[method-assign]
    client._trpc_mutation = lambda *_args, **_kwargs: {"operationId": "run-1"}  # type: ignore[method-assign]

    assert client.list_tasks(limit=10, statuses=["pending"], workspace_id="team") == {
        "tasks": [{"id": "task-1"}],
        "total": 1,
        "raw": {"data": [{"id": "task-1"}, "bad"], "total": "unknown"},
    }
    assert client.get_task_detail("task-1") == {"id": "task-1"}
    assert client.get_task_topics("task-1") == [{"id": "topic-1"}]
    assert client.run_saved_task("task-1", prompt="go") == {"operationId": "run-1"}
    assert client.get_operation_status("run-1") == {"isCompleted": True}
    assert client.wait_for_operation("run-1", timeout_seconds=1, poll_interval=0) == {"isCompleted": True}
    assert calls[0][1]["statuses"] == ["pending"]


def test_create_response_normalizes_app_context_and_parent_message() -> None:
    client = LobeHubClient(IntegrationConfig(api_key="key", base_url="https://lobehub.example"))
    captured: dict[str, object] = {}

    def mutation(procedure: str, payload: dict[str, object] | None = None, **_kwargs: object) -> object:
        captured["procedure"] = procedure
        captured["payload"] = payload or {}
        return {"operationId": "op-1"}

    client._trpc_mutation = mutation  # type: ignore[method-assign]
    result = client.create_response(
        agent_id="agent-1",
        instruction="Do work",
        topic_id="topic-1",
        previous_response_id="message-1",
        device_id="mac",
        context={
            "session_id": "session-1",
            "working_directory": "/repo",
            "initial_topic_metadata": {"repos": ["one", None]},
            "unsupported": "discard",
        },
    )

    assert result == {"operationId": "op-1"}
    assert captured["procedure"] == "aiAgent.execAgent"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["parentMessageId"] == "message-1"
    assert payload["appContext"] == {
        "sessionId": "session-1",
        "initialTopicMetadata": {"repos": ["one"]},
        "topicId": "topic-1",
    }


def test_stream_polling_tracks_event_id_and_stops_at_terminal_event(monkeypatch: pytest.MonkeyPatch) -> None:
    client = LobeHubClient(IntegrationConfig(api_key="key", base_url="https://lobehub.example"))
    calls: list[dict[str, object]] = []
    payloads = [
        'id: 4\nevent: stream_chunk\ndata: {"chunkType":"text","content":"Hi"}\n\n',
        "id: 5\nevent: stream_end\ndata: {}\n\n",
    ]

    def request(_method: str, _path: str, **kwargs: object) -> str:
        calls.append(kwargs)
        return payloads.pop(0)

    client.request = request  # type: ignore[method-assign]
    monkeypatch.setattr("custom_components.lobehub.client.time.monotonic", lambda: 0)
    events = client.get_agent_stream_events("operation-1", timeout_seconds=1, workspace_id="team")

    assert [event["id"] for event in events] == ["4", "5"]
    assert calls[0]["headers"] == {"Accept": "text/event-stream", "X-Workspace-Id": "team"}
    assert calls[1]["params"] == {
        "operationId": "operation-1", "includeHistory": "true", "lastEventId": "4"
    }


def test_agent_lookup_update_and_message_pagination_paths() -> None:
    client = LobeHubClient(IntegrationConfig(api_key="key", base_url="https://lobehub.example"))
    pages = [
        [{"id": str(index)} for index in range(50)],
        [{"id": "50"}, "invalid"],
    ]

    def query(
        procedure: str, _payload: dict[str, object] | None = None, *, workspace_id: str | None = None
    ) -> object:
        if procedure == "messenger.listBindingScopes":
            return [{"id": "team"}, {"id": "team"}, {"id": "  "}]
        if procedure == "agent.getAgentConfigById":
            return {} if workspace_id == "team" else {"id": "agent-1", "name": "Personal"}
        if procedure == "message.getMessages":
            return pages.pop(0)
        return None

    client._trpc_query = query  # type: ignore[method-assign]
    client._trpc_mutation = lambda *_args, **_kwargs: {"agent": {"id": "agent-1", "title": "Updated"}}  # type: ignore[method-assign]

    agent = client.get_agent("agent-1")
    updated = client.update_agent("agent-1", title="Updated")
    messages = client.get_topic_messages("topic-1", all_pages=True)

    assert agent.workspace_id is None
    assert updated.title == "Updated"
    assert [message["id"] for message in messages] == [str(index) for index in range(51)]


def test_client_normalization_helpers_cover_upstream_payload_variants() -> None:
    assert client_module._normalize_workspace_id(" team ") == "team"
    assert client_module._normalize_workspace_id(1) is None
    assert client_module._normalize_model_value(" gpt-4o ") == "gpt-4o"
    assert client_module._normalize_model_value(None) is None
    assert client_module._normalize_initial_topic_metadata({"repos": ["one", None], "working_directory": "/repo"}) == {
        "repos": ["one"],
        "workingDirectory": "/repo",
    }
    assert client_module._normalize_initial_topic_metadata("bad") is None
    assert client_module._normalize_app_context({"group_id": "group", "unknown": "skip"}) == {"groupId": "group"}
    assert client_module._device_option_from_item({"deviceId": "mac", "friendlyName": "Mac", "online": "yes"}) == {
        "device_id": "mac", "label": "Mac", "online": False, "scope": None, "platform": None,
    }
    assert client_module._device_option_from_item({"hostname": "missing"}) is None

    models: list[str] = []
    labels: dict[str, str] = {}
    client_module._append_model_options_from_collection(models, labels, [{"model": "gpt-4o"}, "gpt-4o", "claude"])
    assert models == ["gpt-4o", "claude"]
    runtimes: list[str] = []
    client_module._append_runtime_options_from_collection(
        runtimes, {"id": "gateway", "executionTarget": "local", "ignored": 1}
    )
    assert runtimes == ["auto", "local"]


def test_remote_value_and_stream_helpers_cover_nested_shapes() -> None:
    values: list[str] = []
    LobeHubClient._collect_remote_values(
        {"items": [{"providers": [{"id": "openai"}, {"name": "anthropic"}]}]}, "providers", values
    )
    LobeHubClient._collect_remote_values({"nested": {"runtimes": ["gateway", {"value": "local"}]}}, "runtimes", values)
    assert values == ["openai", "anthropic", "auto", "local"]
    assert LobeHubClient._iter_api_items({"results": [1]}) == [1]
    assert LobeHubClient._iter_api_items({"models": ["gpt-4o"]}) == ["gpt-4o"]
    assert LobeHubClient._format_context({"b": 2, "a": 1}) == "a: 1\nb: 2"
    assert LobeHubClient._format_context(None) is None
    assert LobeHubClient._extract_text_fragments(
        {"content": " Agent operation created successfully ", "delta": {"output_text": " done "}, "payload": [" more "]}
    ) == ["done", "more"]
    assert LobeHubClient._parse_sse_events("event: note\ndata: raw\ndata: text\n\nignored") == [
        {"event": "note", "data": "raw\ntext"}
    ]


def test_client_validates_config_and_unwraps_common_response_shapes() -> None:
    with pytest.raises(Exception, match="api_key is required"):
        LobeHubClient(IntegrationConfig(api_key="", base_url="https://lobehub.example"))
    with pytest.raises(Exception, match="base_url is required"):
        LobeHubClient(IntegrationConfig(api_key="key", base_url=""))

    assert LobeHubClient._unwrap_trpc_json("value") == "value"
    assert LobeHubClient._unwrap_trpc_json({"result": "not-a-mapping"}) == {"result": "not-a-mapping"}
    assert LobeHubClient._unwrap_trpc_json({"result": {"data": "not-a-mapping"}}) == {"data": "not-a-mapping"}
    assert LobeHubClient._unwrap_trpc_json({"result": {"data": {"value": 1}}}) == {"value": 1}
    assert LobeHubClient._unwrap_data({"data": 3}) == 3
    assert LobeHubClient._unwrap_data("value") == "value"


def test_client_handles_invalid_upstream_collections_and_agent_not_found() -> None:
    client = LobeHubClient(IntegrationConfig(api_key="key", base_url="https://lobehub.example"))
    client._trpc_query = lambda procedure, *_args, **_kwargs: {
        "messenger.listBindingScopes": {"unexpected": True},
        "device.listDevices": {"unexpected": True},
        "agent.getAgentConfig": "invalid-default",
        "agent.queryAgents": {"items": [None, {}, {"id": "agent-1", "slug": "one"}, {"id": "agent-1"}]},
        "agent.getAgentConfigById": None,
    }[procedure]  # type: ignore[method-assign]

    assert client.list_workspace_scopes() == []
    assert client.list_devices() == []
    agents = list(client.list_agents())
    assert [(agent.id, agent.title) for agent in agents] == [("agent-1", "one")]
    with pytest.raises(ApiError, match="Agent not found: missing"):
        client.get_agent("missing")


def test_update_messages_and_task_helpers_tolerate_invalid_responses() -> None:
    client = LobeHubClient(IntegrationConfig(api_key="key", base_url="https://lobehub.example"))
    client._trpc_mutation = lambda *_args, **_kwargs: "invalid"  # type: ignore[method-assign]
    client._trpc_query = lambda procedure, *_args, **_kwargs: {
        "message.getMessages": {"not": "a list"},
        "task.list": [],
        "task.detail": "invalid",
        "task.getTopics": {"invalid": True},
        "aiAgent.getOperationStatus": [],
    }[procedure]  # type: ignore[method-assign]

    with pytest.raises(ApiError, match="Agent not found"):
        client.update_agent("missing")
    assert client.get_topic_messages("topic") == []
    assert client.list_tasks() == {"tasks": [], "total": 0}
    assert client.get_task_detail("task") == {}
    assert client.get_task_topics("task") == []
    assert client.run_saved_task("task") == {}
    assert client.get_operation_status("operation") is None


def test_wait_and_stream_timeout_paths_return_latest_values(monkeypatch: pytest.MonkeyPatch) -> None:
    client = LobeHubClient(IntegrationConfig(api_key="key", base_url="https://lobehub.example"))
    statuses = iter([None, {"state": "running"}])
    client.get_operation_status = lambda *_args, **_kwargs: next(statuses)  # type: ignore[method-assign]
    clock = iter([0.0, 0.2, 0.3, 2.0])
    monkeypatch.setattr("custom_components.lobehub.client.time.monotonic", lambda: next(clock))
    monkeypatch.setattr("custom_components.lobehub.client.time.sleep", lambda _seconds: None)
    assert client.wait_for_operation("operation", timeout_seconds=1, poll_interval=0) == {"state": "running"}

    stream_clock = iter([0.0, 2.0])
    monkeypatch.setattr("custom_components.lobehub.client.time.monotonic", lambda: next(stream_clock))
    client.request = lambda *_args, **_kwargs: ""  # type: ignore[method-assign]
    assert client.get_agent_stream_events("operation", timeout_seconds=1) == []
