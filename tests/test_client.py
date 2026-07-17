from custom_components.lobehub.client import LobeHubClient
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
    transport, _ = make_transport(
        [
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

    assert len(agents) == 1
    assert agents[0].id == "agent-1"
    assert agents[0].title == "Coffee"
    assert agents[0].model == "gpt-4o-mini"


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
