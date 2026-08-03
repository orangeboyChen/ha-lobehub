"""HTTP client for the public LobeHub API."""

import json
import time
from collections.abc import Callable, Iterable, Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

from .const import EXECUTION_TARGETS
from .models import (
    AgentSummary,
    IntegrationConfig,
    RemoteOptions,
    TopicSummary,
    normalize_runtime,
)
from .exceptions import ApiError, ValidationError

TransportResponse = tuple[int, Mapping[str, str], bytes]
Transport = Callable[[str, str, Mapping[str, str], bytes | None], TransportResponse]
KNOWN_EXECUTION_TARGETS = list(EXECUTION_TARGETS)
_MESSAGE_PAGE_SIZE = 50
_APP_CONTEXT_KEY_ALIASES = {
    "default_task_assignee_agent_id": "defaultTaskAssigneeAgentId",
    "document_id": "documentId",
    "editing_agent_id": "editingAgentId",
    "group_id": "groupId",
    "groupId": "groupId",
    "initial_topic_metadata": "initialTopicMetadata",
    "initialTopicMetadata": "initialTopicMetadata",
    "orchestration_role": "orchestrationRole",
    "orchestrationRole": "orchestrationRole",
    "scope": "scope",
    "session_id": "sessionId",
    "sessionId": "sessionId",
    "task_id": "taskId",
    "taskId": "taskId",
    "thread_id": "threadId",
    "threadId": "threadId",
    "topic_id": "topicId",
    "topicId": "topicId",
}


def _normalize_workspace_id(value: Any) -> str | None:
    """Normalize one workspace id from an upstream payload."""

    if not isinstance(value, str):
        return None

    normalized = value.strip()
    return normalized or None


def _normalize_model_value(model: str | None, provider: str | None = None) -> str | None:
    """Return the upstream model value as-is when present."""

    normalized_model = str(model).strip() if model else ""
    if not normalized_model:
        return None
    return normalized_model


def _append_normalized_model_option(
    values: list[str],
    labels: dict[str, str],
    model: str | None,
    provider: str | None = None,
) -> None:
    """Append a normalized provider/model option if it exists."""

    normalized_value = _normalize_model_value(model, provider)
    if not normalized_value or normalized_value in values:
        return
    values.append(normalized_value)
    labels.setdefault(normalized_value, normalized_value)


def _append_model_option_from_value(
    values: list[str],
    labels: dict[str, str],
    value: Any,
) -> None:
    """Append one remote model option from either a string or a mapping."""

    if isinstance(value, Mapping):
        _append_normalized_model_option(
            values,
            labels,
            value.get("model"),
            value.get("provider"),
        )
        return
    if isinstance(value, str):
        _append_normalized_model_option(values, labels, value)


def _append_model_options_from_collection(
    values: list[str],
    labels: dict[str, str],
    collection: Any,
) -> None:
    """Append remote model options from a list or one direct value."""

    if isinstance(collection, list):
        for item in collection:
            _append_model_option_from_value(values, labels, item)
        return
    _append_model_option_from_value(values, labels, collection)


def _append_runtime_option(values: list[str], value: Any) -> None:
    """Append one runtime option if it can be normalized to a string."""

    normalized = normalize_runtime(value, None)
    if normalized and normalized not in values:
        values.append(normalized)


def _append_runtime_options_from_collection(values: list[str], collection: Any) -> None:
    """Append runtime options from direct strings, mappings or collections."""

    if isinstance(collection, Mapping):
        for candidate_key in (
            "id",
            "value",
            "name",
            "label",
            "runtime",
            "executionTarget",
        ):
            candidate = collection.get(candidate_key)
            if isinstance(candidate, str):
                _append_runtime_option(values, candidate)
        return

    if isinstance(collection, (list, tuple, set)):
        for item in collection:
            _append_runtime_options_from_collection(values, item)
        return

    _append_runtime_option(values, collection)


def _normalize_initial_topic_metadata(value: Any) -> dict[str, Any] | None:
    """Normalize initialTopicMetadata keys to the server-side schema."""

    if not isinstance(value, Mapping):
        return None

    metadata: dict[str, Any] = {}
    repos = value.get("repos")
    if isinstance(repos, list):
        metadata["repos"] = [str(repo) for repo in repos if repo is not None]

    working_directory = value.get("workingDirectory")
    if not isinstance(working_directory, str):
        working_directory = value.get("working_directory")
    if isinstance(working_directory, str) and working_directory:
        metadata["workingDirectory"] = working_directory

    return metadata or None


def _normalize_app_context(context: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalize supported appContext keys to LobeHub's camelCase schema."""

    normalized: dict[str, Any] = {}
    for key, value in dict(context or {}).items():
        if value is None:
            continue
        mapped_key = _APP_CONTEXT_KEY_ALIASES.get(key)
        if mapped_key is None:
            continue
        if mapped_key == "initialTopicMetadata":
            normalized_value = _normalize_initial_topic_metadata(value)
            if normalized_value is not None:
                normalized[mapped_key] = normalized_value
            continue
        normalized[mapped_key] = value

    return normalized


def _device_option_from_item(item: Any) -> dict[str, Any] | None:
    """Normalize one device entry returned by LobeHub."""

    if not isinstance(item, Mapping):
        return None

    device_id = item.get("deviceId")
    if not isinstance(device_id, str) or not device_id:
        return None

    online = item.get("online")
    # Current LobeHub responses provide ``online`` directly. Older gateway
    # responses expose only their live connection list, which carries the same
    # information: a device is online when it has at least one channel.
    if not isinstance(online, bool):
        channels = item.get("channels")
        online = isinstance(channels, list) and bool(channels)

    return {
        "device_id": device_id,
        "label": str(item.get("friendlyName") or item.get("hostname") or device_id),
        "online": online,
        "scope": item.get("scope"),
        "platform": item.get("platform"),
    }


def _default_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
) -> TransportResponse:
    request = Request(url, data=body, headers=dict(headers), method=method)
    try:
        with urlopen(request, timeout=30) as response:
            status = int(getattr(response, "status", response.getcode()))
            response_headers = dict(response.headers.items())
            payload = response.read()
            return status, response_headers, payload
    except HTTPError as exc:
        payload = exc.read() if hasattr(exc, "read") else b""
        headers_obj = getattr(exc, "headers", None)
        response_headers = dict(headers_obj.items()) if headers_obj else {}
        return int(exc.code), response_headers, payload


class LobeHubClient:
    """A tiny wrapper around the public LobeHub REST API."""

    def __init__(
        self,
        config: IntegrationConfig,
        transport: Transport | None = None,
    ) -> None:
        """Initialize the client with validated configuration."""

        if not config.api_key:
            raise ValidationError("api_key is required")
        if not config.base_url:
            raise ValidationError("base_url is required")

        self.config = config
        self._transport = transport or _default_transport

    @property
    def base_url(self) -> str:
        """Return the normalized base URL."""

        return self.config.base_url.rstrip("/") + "/"

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """Send one HTTP request to the configured LobeHub server."""

        url = urljoin(self.base_url, path.lstrip("/"))
        if params:
            query = urlencode(
                [(k, v) for k, v in params.items() if v is not None], doseq=True
            )
            if query:
                url = f"{url}?{query}"

        request_headers: dict[str, str] = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Accept": "application/json",
        }
        if headers:
            request_headers.update(headers)
        body: bytes | None = None
        if json_body is not None:
            request_headers["Content-Type"] = "application/json"
            body = json.dumps(
                json_body, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")

        try:
            status, _response_headers, payload = self._transport(
                method.upper(), url, request_headers, body
            )
        except URLError as exc:
            raise ApiError(status_code=0, message=str(exc), payload=None) from exc

        text = payload.decode("utf-8") if payload else ""
        parsed: Any = None
        if text:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = text

        if status < 200 or status >= 300:
            message = "HTTP error"
            if isinstance(parsed, dict):
                message = str(parsed.get("message") or parsed.get("error") or message)
            elif isinstance(parsed, str) and parsed.strip():
                message = parsed.strip()
            raise ApiError(status_code=status, message=message, payload=parsed)

        if parsed is None:
            return {}
        return parsed

    def _trpc_headers(self, workspace_id: str | None = None) -> dict[str, str]:
        headers = {
            "X-API-Key": self.config.api_key,
            "Authorization": "",
        }
        if workspace_id:
            headers["X-Workspace-Id"] = workspace_id
        return headers

    def _trpc_query(
        self,
        procedure: str,
        payload: Mapping[str, Any] | None = None,
        *,
        workspace_id: str | None = None,
    ) -> Any:
        query_payload = {
            key: value
            for key, value in dict(payload or {}).items()
            if value is not None
        }
        body = {"json": query_payload}
        data = self.request(
            "GET",
            f"/trpc/lambda/{procedure}",
            params={
                "input": json.dumps(body, separators=(",", ":"), ensure_ascii=False)
            },
            headers=self._trpc_headers(workspace_id),
        )
        return self._unwrap_trpc_json(data)

    def _trpc_mutation(
        self,
        procedure: str,
        payload: Mapping[str, Any] | None = None,
        *,
        workspace_id: str | None = None,
    ) -> Any:
        mutation_payload = {
            key: value
            for key, value in dict(payload or {}).items()
            if value is not None
        }
        data = self.request(
            "POST",
            f"/trpc/lambda/{procedure}",
            json_body={"json": mutation_payload},
            headers=self._trpc_headers(workspace_id),
        )
        return self._unwrap_trpc_json(data)

    @staticmethod
    def _unwrap_trpc_json(payload: Any) -> Any:
        if not isinstance(payload, dict):
            return payload
        result = payload.get("result")
        if not isinstance(result, dict):
            return payload
        data = result.get("data")
        if not isinstance(data, dict):
            return result
        return data.get("json", data)

    @staticmethod
    def _unwrap_data(payload: Any) -> Any:
        if isinstance(payload, dict) and "data" in payload:
            return payload["data"]
        return payload

    def validate_auth(self) -> Any:
        """Validate the configured API key against the user profile endpoint."""

        return self._unwrap_data(self.request("GET", "/api/v1/users/me"))

    def list_workspace_scopes(self) -> list[dict[str, Any]]:
        """Return workspace scopes visible to the configured API key."""

        data = self._trpc_query("messenger.listBindingScopes")
        if not isinstance(data, list):
            return []
        return [dict(item) for item in data if isinstance(item, Mapping)]

    def get_ai_provider_runtime_state(self) -> Any:
        """Return the upstream aiProvider runtime state used by LobeHub itself."""

        return self._trpc_query("aiProvider.getAiProviderRuntimeState")

    def list_devices(
        self,
        *,
        workspace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return the execution devices exposed by LobeHub."""

        data = self._trpc_query("device.listDevices", workspace_id=workspace_id)
        if not isinstance(data, list):
            return []
        return [device for item in data if (device := _device_option_from_item(item))]

    def discover_remote_options(
        self,
        *,
        workspace_id: str | None = None,
    ) -> RemoteOptions:
        """Collect remote model/provider/runtime options exposed by LobeHub."""

        profile = self.validate_auth()
        agents = list(self.list_agents())

        model_values: list[str] = []
        model_labels: dict[str, str] = {}
        provider_values: list[str] = []
        runtime_values: list[str] = list(KNOWN_EXECUTION_TARGETS)

        try:
            runtime_state = self.get_ai_provider_runtime_state()
        except ApiError:
            runtime_state = None
        else:
            self._append_runtime_state_options(
                runtime_state,
                model_values,
                model_labels,
                provider_values,
                runtime_values,
            )

        try:
            devices = self.list_devices(workspace_id=workspace_id)
        except ApiError:
            devices = []

        self._collect_remote_values(profile, "provider", provider_values)
        self._collect_remote_values(profile, "runtime", runtime_values)
        self._collect_remote_values(profile, "runtimes", runtime_values)
        self._collect_remote_values(profile, "executionTarget", runtime_values)
        self._collect_remote_values(profile, "providers", provider_values)
        _append_normalized_model_option(
            model_values,
            model_labels,
            profile.get("model") if isinstance(profile, dict) else None,
            profile.get("provider") if isinstance(profile, dict) else None,
        )

        for agent in agents:
            detail = self.get_agent(
                agent.id,
                workspace_id=agent.workspace_id,
            )

            _append_normalized_model_option(
                model_values,
                model_labels,
                detail.model,
                detail.provider,
            )
            if detail.provider and detail.provider not in provider_values:
                provider_values.append(detail.provider)

            self._collect_remote_values(detail.raw, "provider", provider_values)
            self._collect_remote_values(detail.raw, "runtime", runtime_values)
            self._collect_remote_values(detail.raw, "providers", provider_values)
            self._collect_remote_values(detail.raw, "runtimes", runtime_values)
            self._collect_remote_values(detail.raw, "executionTarget", runtime_values)
            self._collect_remote_values(detail.chat_config, "provider", provider_values)
            self._collect_remote_values(detail.chat_config, "runtime", runtime_values)
            self._collect_remote_values(detail.chat_config, "runtimes", runtime_values)
            self._collect_remote_values(detail.chat_config, "executionTarget", runtime_values)
            _append_normalized_model_option(
                model_values,
                model_labels,
                detail.raw.get("model") if isinstance(detail.raw, dict) else None,
                detail.raw.get("provider") if isinstance(detail.raw, dict) else None,
            )
            _append_normalized_model_option(
                model_values,
                model_labels,
                detail.chat_config.get("model") if isinstance(detail.chat_config, dict) else None,
                detail.chat_config.get("provider") if isinstance(detail.chat_config, dict) else None,
            )
            _append_normalized_model_option(
                model_values,
                model_labels,
                detail.chat_config.get("searchFCModel") if isinstance(detail.chat_config, dict) else None,
                detail.chat_config.get("searchFCProvider") if isinstance(detail.chat_config, dict) else None,
            )

        return RemoteOptions(
            models=model_values,
            model_labels=model_labels,
            providers=provider_values,
            runtimes=[
                runtime for runtime in KNOWN_EXECUTION_TARGETS if runtime in runtime_values
            ],
            devices=devices,
        )

    @staticmethod
    def _iter_api_items(payload: Any) -> Iterable[Any]:
        """Yield items from common paginated or grouped API payloads."""

        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            return []

        for key in ("items", "data", "list", "results"):
            items = payload.get(key)
            if isinstance(items, list):
                return items

        if isinstance(payload.get("providers"), list):
            return payload["providers"]
        if isinstance(payload.get("models"), list):
            return payload["models"]
        return []

    @classmethod
    def _append_models_from_api_payload(
        cls,
        payload: Any,
        values: list[str],
        labels: dict[str, str],
    ) -> None:
        """Append model options from a generic API payload."""

        for item in cls._iter_api_items(payload):
            _append_model_option_from_value(values, labels, item)

    @classmethod
    def _append_runtime_state_options(
        cls,
        payload: Any,
        model_values: list[str],
        model_labels: dict[str, str],
        provider_values: list[str],
        runtime_values: list[str],
    ) -> None:
        """Append model and provider options from aiProvider runtime state."""

        if not isinstance(payload, Mapping):
            return

        for item in cls._iter_api_items(payload.get("enabledAiModels")):
            if not isinstance(item, Mapping):
                _append_model_option_from_value(model_values, model_labels, item)
                continue

            provider = item.get("providerId") or item.get("provider")
            model = item.get("id") or item.get("model") or item.get("name")
            _append_normalized_model_option(model_values, model_labels, model, provider)

        for item in cls._iter_api_items(payload.get("enabledAiProviders")):
            if isinstance(item, Mapping):
                provider = item.get("id") or item.get("provider") or item.get("name")
            else:
                provider = item
            if isinstance(provider, str) and provider and provider not in provider_values:
                provider_values.append(provider)

        cls._collect_remote_values(payload, "provider", provider_values)
        cls._collect_remote_values(payload, "providers", provider_values)
        cls._collect_remote_values(payload, "runtime", runtime_values)
        cls._collect_remote_values(payload, "runtimes", runtime_values)
        cls._collect_remote_values(payload, "executionTarget", runtime_values)

        for key in (
            "enabledModels",
        ):
            cls._append_models_from_api_payload(payload.get(key), model_values, model_labels)

        runtime_config = payload.get("runtimeConfig")
        if isinstance(runtime_config, Mapping):
            for provider, config in runtime_config.items():
                if isinstance(provider, str) and provider and provider not in provider_values:
                    provider_values.append(provider)
                _append_runtime_options_from_collection(runtime_values, config)

        _append_runtime_options_from_collection(runtime_values, runtime_config)

    def list_agents(
        self,
        *,
        keyword: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> Iterable[AgentSummary]:
        """Return the visible agents from the LobeHub lambda API."""

        payload = {
            "keyword": keyword,
            "limit": page_size,
            "offset": ((page - 1) * page_size) if page and page_size else None,
        }
        discovered: dict[str, AgentSummary] = {}

        # The personal inbox is a virtual agent, so ``queryAgents`` excludes
        # it. Fetch the builtin Lobe AI agent without a workspace header, then
        # include it with ordinary agents. Do not use ``getAgentConfig`` with
        # ``sessionId='inbox'`` here: that legacy endpoint provisions a
        # session-backed inbox and can create a title-less ordinary agent.
        try:
            default_agent = self._trpc_query(
                "agent.getBuiltinAgent",
                {"slug": "inbox"},
                workspace_id=None,
            )
        except ApiError:
            default_agent = None
        if isinstance(default_agent, dict):
            agent = self._to_agent(default_agent)
            if agent.id:
                discovered[agent.id] = agent

        workspace_ids = [None]
        workspace_ids.extend(
            _normalize_workspace_id(scope.get("id"))
            for scope in self.list_workspace_scopes()
            if isinstance(scope, Mapping)
        )

        for workspace_id in workspace_ids:
            if workspace_id is not None:
                try:
                    workspace_agent = self._trpc_query(
                        "agent.getBuiltinAgent",
                        {"slug": "inbox"},
                        workspace_id=workspace_id,
                    )
                except ApiError:
                    workspace_agent = None
                if isinstance(workspace_agent, dict):
                    agent = self._to_agent(workspace_agent)
                    if agent.id and agent.id not in discovered:
                        agent.workspace_id = workspace_id
                        agent.raw.setdefault("workspaceId", workspace_id)
                        discovered[agent.id] = agent

            data = self._trpc_query(
                "agent.queryAgents",
                payload,
                workspace_id=workspace_id,
            )
            agents: list[Any] = []
            if isinstance(data, list):
                agents = data
            elif isinstance(data, dict):
                items = data.get("items")
                if isinstance(items, list):
                    agents = items

            for item in agents:
                if not isinstance(item, Mapping):
                    continue
                agent = self._to_agent(item)
                if not agent.id or agent.id in discovered:
                    continue
                if agent.workspace_id is None:
                    agent.workspace_id = workspace_id
                    if isinstance(agent.raw, dict) and workspace_id:
                        agent.raw.setdefault("workspaceId", workspace_id)
                discovered[agent.id] = agent

        return list(discovered.values())

    def get_agent(
        self,
        agent_id: str,
        *,
        workspace_id: str | None = None,
    ) -> AgentSummary:
        """Fetch one agent configuration by id."""

        candidate_workspaces = [workspace_id] if workspace_id else []
        candidate_workspaces.extend(
            _normalize_workspace_id(scope.get("id"))
            for scope in self.list_workspace_scopes()
            if isinstance(scope, Mapping)
        )
        candidate_workspaces.append(None)

        seen: set[str | None] = set()
        for candidate in candidate_workspaces:
            if candidate in seen:
                continue
            seen.add(candidate)
            data = self._trpc_query(
                "agent.getAgentConfigById",
                {"agentId": agent_id},
                workspace_id=candidate,
            )
            if not isinstance(data, dict):
                continue
            agent = self._to_agent(data)
            if not agent.id:
                continue
            if agent.workspace_id is None:
                agent.workspace_id = candidate
                if isinstance(agent.raw, dict) and candidate:
                    agent.raw.setdefault("workspaceId", candidate)
            return agent

        raise ApiError(
            status_code=404,
            message=f"Agent not found: {agent_id}",
            payload={"agentId": agent_id},
        )

    def update_agent(
        self,
        agent_id: str,
        *,
        workspace_id: str | None = None,
        **changes: Any,
    ) -> AgentSummary:
        """Apply explicit remote agent configuration changes."""

        data = self._trpc_mutation(
            "agent.updateAgentConfig",
            {"agentId": agent_id, "value": changes},
            workspace_id=workspace_id,
        )
        if isinstance(data, dict) and isinstance(data.get("agent"), dict):
            data = data["agent"]
        if not isinstance(data, dict):
            raise ApiError(
                status_code=404,
                message=f"Agent not found: {agent_id}",
                payload={"agentId": agent_id},
            )
        return self._to_agent(data)

    def list_topics(
        self,
        *,
        agent_id: str | None = None,
        group_id: str | None = None,
        keyword: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
        workspace_id: str | None = None,
    ) -> Iterable[TopicSummary]:
        """List topics visible to the configured API key."""

        data = self._trpc_query(
            "topic.getTopics",
            {
                "agentId": agent_id,
                "groupId": group_id,
                "current": page,
                "pageSize": page_size,
            },
            workspace_id=workspace_id,
        )
        topics: list[Any] = []
        if isinstance(data, dict):
            items = data.get("items")
            if isinstance(items, list):
                topics = items
        elif isinstance(data, list):
            topics = data
        return [self._to_topic(item) for item in topics]

    def create_topic(
        self,
        *,
        title: str,
        agent_id: str | None = None,
        group_id: str | None = None,
        client_id: str | None = None,
        favorite: bool | None = None,
        workspace_id: str | None = None,
    ) -> TopicSummary:
        """Create a new topic for one agent or group."""

        payload = {
            "agentId": agent_id,
            "favorite": favorite,
            "groupId": group_id,
            "title": title,
        }
        topic_id = self._trpc_mutation(
            "topic.createTopic",
            payload,
            workspace_id=workspace_id,
        )
        if not isinstance(topic_id, str):
            raise ApiError(
                status_code=0,
                message="Invalid topic id returned by LobeHub",
                payload=topic_id,
            )
        raw = {"id": topic_id, **payload}
        if workspace_id:
            raw["workspaceId"] = workspace_id
        return TopicSummary(
            id=topic_id,
            title=title,
            agent_id=agent_id,
            group_id=group_id,
            workspace_id=workspace_id,
            raw=raw,
        )

    def get_topic(
        self,
        topic_id: str,
        *,
        workspace_id: str | None = None,
    ) -> TopicSummary:
        """Fetch full topic metadata by id."""

        data = self._trpc_query(
            "topic.getTopicDetail",
            {"id": topic_id},
            workspace_id=workspace_id,
        )
        if not isinstance(data, dict):
            raise ApiError(
                status_code=404,
                message=f"Topic not found: {topic_id}",
                payload={"id": topic_id},
            )
        topic = self._to_topic(data)
        if topic.workspace_id is None:
            topic.workspace_id = workspace_id
            if isinstance(topic.raw, dict) and workspace_id:
                topic.raw.setdefault("workspaceId", workspace_id)
        return topic

    def get_topic_messages(
        self,
        topic_id: str,
        *,
        all_pages: bool = False,
        workspace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch one topic's messages, optionally paginating through all pages."""

        data = self._trpc_query(
            "message.getMessages",
            {
                "current": 0,
                "pageSize": _MESSAGE_PAGE_SIZE,
                "topicId": topic_id,
            },
            workspace_id=workspace_id,
        )
        if not isinstance(data, list):
            return []

        messages = [dict(item) for item in data if isinstance(item, dict)]
        if not all_pages or len(messages) < _MESSAGE_PAGE_SIZE:
            return messages

        current = 1
        while True:
            data = self._trpc_query(
                "message.getMessages",
                {
                    "current": current,
                    "pageSize": _MESSAGE_PAGE_SIZE,
                    "topicId": topic_id,
                },
                workspace_id=workspace_id,
            )
            if not isinstance(data, list):
                return messages

            batch = [dict(item) for item in data if isinstance(item, dict)]
            messages.extend(batch)
            if len(batch) < _MESSAGE_PAGE_SIZE:
                return messages
            current += 1

    def create_message_reply(
        self,
        *,
        content: str,
        topic_id: str,
        model: str | None = None,
        provider: str | None = None,
        client_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        thread_id: str | None = None,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a user message in the active topic."""

        payload = {
            "agentId": None,
            "content": content,
            "clientId": client_id,
            "metadata": dict(metadata or {}),
            "model": model,
            "provider": provider,
            "role": "user",
            "threadId": thread_id,
            "topicId": topic_id,
        }
        return self._trpc_mutation(
            "message.createMessage",
            payload,
            workspace_id=workspace_id,
        )

    def create_response(
        self,
        *,
        agent_id: str,
        instruction: str,
        topic_id: str | None = None,
        context: Mapping[str, Any] | None = None,
        previous_response_id: str | None = None,
        device_id: str | None = None,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute one task-oriented agent run through the lambda API."""

        app_context = _normalize_app_context(context)
        if topic_id:
            app_context["topicId"] = topic_id
        payload = {
            "agentId": agent_id,
            "appContext": app_context or None,
            "autoStart": True,
            "deviceId": device_id,
            "prompt": instruction,
            "trigger": "openapi",
            "userInterventionConfig": {"approvalMode": "headless"},
        }
        if previous_response_id:
            payload["parentMessageId"] = previous_response_id
        return self._trpc_mutation(
            "aiAgent.execAgent",
            payload,
            workspace_id=workspace_id,
        )

    def list_tasks(
        self,
        *,
        assignee_agent_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        parent_task: str | None = None,
        statuses: list[str] | None = None,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        """List saved LobeHub tasks visible to the configured API key."""

        data = self._trpc_query(
            "task.list",
            {
                "assigneeAgentId": assignee_agent_id,
                "limit": limit,
                "offset": offset,
                "parentIdentifier": parent_task,
                "parentTaskId": parent_task,
                "statuses": statuses,
            },
            workspace_id=workspace_id,
        )
        if not isinstance(data, dict):
            return {"tasks": [], "total": 0}

        items = data.get("data")
        tasks = (
            [dict(item) for item in items if isinstance(item, Mapping)]
            if isinstance(items, list)
            else []
        )
        total = data.get("total")
        return {
            "tasks": tasks,
            "total": total if isinstance(total, int) else len(tasks),
            "raw": dict(data),
        }

    def get_task_detail(
        self,
        task: str,
        *,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch one task by raw id or identifier."""

        data = self._unwrap_data(
            self._trpc_query("task.detail", {"id": task}, workspace_id=workspace_id)
        )
        return dict(data) if isinstance(data, Mapping) else {}

    def get_task_topics(
        self,
        task: str,
        *,
        workspace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch topics linked to one task by raw id or identifier."""

        data = self._unwrap_data(
            self._trpc_query("task.getTopics", {"id": task}, workspace_id=workspace_id)
        )
        if isinstance(data, list):
            return [dict(item) for item in data if isinstance(item, Mapping)]
        return []

    def run_saved_task(
        self,
        task: str,
        *,
        continue_topic_id: str | None = None,
        prompt: str | None = None,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        """Run one saved LobeHub task by raw id or identifier."""

        data = self._trpc_mutation(
            "task.run",
            {
                "continueTopicId": continue_topic_id,
                "id": task,
                "prompt": prompt,
            },
            workspace_id=workspace_id,
        )
        return dict(data) if isinstance(data, Mapping) else {}

    def get_operation_status(
        self,
        operation_id: str,
        *,
        include_history: bool = False,
        history_limit: int = 10,
        workspace_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Fetch the latest status for an agent operation."""

        data = self._trpc_query(
            "aiAgent.getOperationStatus",
            {
                "historyLimit": history_limit,
                "includeHistory": include_history,
                "operationId": operation_id,
            },
            workspace_id=workspace_id,
        )
        return data if isinstance(data, dict) else None

    def wait_for_operation(
        self,
        operation_id: str,
        *,
        timeout_seconds: float = 60.0,
        poll_interval: float = 0.5,
        workspace_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Poll the operation status endpoint until execution settles."""

        deadline = time.monotonic() + timeout_seconds
        latest_status: dict[str, Any] | None = None

        while time.monotonic() < deadline:
            latest_status = self.get_operation_status(
                operation_id,
                workspace_id=workspace_id,
            )
            if latest_status is None:
                time.sleep(poll_interval)
                continue

            if (
                latest_status.get("isCompleted")
                or latest_status.get("hasError")
                or latest_status.get("needsHumanInput")
            ):
                return latest_status

            time.sleep(poll_interval)

        return latest_status

    def get_agent_stream_events(
        self,
        operation_id: str,
        *,
        timeout_seconds: float = 30.0,
        workspace_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Read SSE stream events for one agent operation until completion."""

        deadline = time.monotonic() + timeout_seconds
        last_event_id = "0"
        collected: list[dict[str, Any]] = []

        while time.monotonic() < deadline:
            events_text = self.request(
                "GET",
                "/api/agent/stream",
                params={
                    "operationId": operation_id,
                    "includeHistory": "true",
                    "lastEventId": last_event_id,
                },
                headers={
                    "Accept": "text/event-stream",
                    **(
                        {"X-Workspace-Id": workspace_id}
                        if workspace_id
                        else {}
                    ),
                },
            )
            events = (
                self._parse_sse_events(events_text)
                if isinstance(events_text, str)
                else []
            )

            if not events:
                time.sleep(0.5)
                continue

            for event in events:
                event_id = event.get("id")
                if isinstance(event_id, str) and event_id:
                    last_event_id = event_id
                collected.append(event)
                if event.get("event") in {"stream_end", "agent_runtime_end", "error"}:
                    return collected

        return collected

    @staticmethod
    def collect_stream_text(events: Iterable[Mapping[str, Any]]) -> str:
        """Aggregate assistant text from stream chunk events."""

        chunks: list[str] = []
        for event in events:
            event_name = event.get("event")
            data = event.get("data")
            if event_name == "stream_chunk" and isinstance(data, Mapping):
                chunk_type = data.get("chunkType")
                if chunk_type == "text":
                    content = data.get("content")
                    if isinstance(content, str) and content:
                        chunks.append(content)
                    continue
                if chunk_type == "content_part":
                    for part in data.get("contentParts", []):
                        if not isinstance(part, Mapping):
                            continue
                        text = part.get("text")
                        if isinstance(text, str) and text:
                            chunks.append(text)
                    continue

            for content in LobeHubClient._extract_text_fragments(data):
                if content:
                    chunks.append(content)
        return "".join(chunks).strip()

    @staticmethod
    def _extract_text_fragments(payload: Any) -> list[str]:
        """Extract text recursively from known LobeHub event payload shapes."""

        fragments: list[str] = []

        def collect(value: Any) -> None:
            if isinstance(value, str):
                stripped = value.strip()
                if stripped and stripped != "Agent operation created successfully":
                    fragments.append(stripped)
                return
            if isinstance(value, Mapping):
                for key in (
                    "content",
                    "output_text",
                    "response",
                    "answer",
                ):
                    if key in value:
                        collect(value[key])
                content_parts = value.get("contentParts")
                if isinstance(content_parts, list):
                    for part in content_parts:
                        if isinstance(part, Mapping):
                            text = part.get("text")
                            if isinstance(text, str):
                                collect(text)
                for key in ("data", "delta", "chunk", "payload"):
                    if key in value:
                        collect(value[key])
                return
            if isinstance(value, list):
                for item in value:
                    collect(item)

        collect(payload)
        return fragments

    @staticmethod
    def _parse_sse_events(payload: str) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for block in payload.split("\n\n"):
            lines = [line for line in block.splitlines() if line.strip()]
            if not lines:
                continue

            event: dict[str, Any] = {}
            data_lines: list[str] = []
            for line in lines:
                if line.startswith("id:"):
                    event["id"] = line[3:].strip()
                elif line.startswith("event:"):
                    event["event"] = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].strip())

            if data_lines:
                data_text = "\n".join(data_lines)
                try:
                    event["data"] = json.loads(data_text)
                except json.JSONDecodeError:
                    event["data"] = data_text

            if event:
                events.append(event)

        return events

    @staticmethod
    def _format_context(context: Mapping[str, Any] | None) -> str | None:
        if not context:
            return None
        lines = []
        for key in sorted(context):
            value = context[key]
            lines.append(f"{key}: {value}")
        return "\n".join(lines)

    @classmethod
    def _collect_remote_values(
        cls,
        payload: Any,
        key: str,
        values: list[str],
    ) -> None:
        if isinstance(payload, dict):
            for current_key, current_value in payload.items():
                if current_key == key:
                    if current_key == "models":
                        continue
                    if current_key in {"runtime", "runtimes", "executionTarget"}:
                        _append_runtime_options_from_collection(values, current_value)
                    else:
                        cls._append_remote_values(current_value, values)
                elif isinstance(current_value, (dict, list, tuple, set)):
                    cls._collect_remote_values(current_value, key, values)
            return

        if isinstance(payload, (list, tuple, set)):
            for item in payload:
                cls._collect_remote_values(item, key, values)

    @staticmethod
    def _append_remote_values(payload: Any, values: list[str]) -> None:
        if isinstance(payload, str):
            value = payload.strip()
            if value and value not in values:
                values.append(value)
            return

        if isinstance(payload, dict):
            for candidate_key in ("id", "value", "name", "label", "key"):
                candidate = payload.get(candidate_key)
                if isinstance(candidate, str) and candidate.strip():
                    normalized = candidate.strip()
                    if normalized not in values:
                        values.append(normalized)
                    return
            return

        if isinstance(payload, (list, tuple, set)):
            for item in payload:
                LobeHubClient._append_remote_values(item, values)

    @staticmethod
    def _to_agent(item: Any) -> AgentSummary:
        if not isinstance(item, dict):
            return AgentSummary(id=str(item), title=str(item))
        meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
        chat_config = dict(item.get("chatConfig") or {})
        raw_title = (
            item.get("title")
            or meta.get("title")
            or item.get("name")
            or meta.get("name")
            or item.get("displayName")
            or meta.get("displayName")
            or item.get("agentName")
            or meta.get("agentName")
            or item.get("slug")
            or meta.get("slug")
            or item.get("identifier")
            or item.get("id")
            or ""
        )
        if item.get("slug") == "inbox" and raw_title in {"", "inbox"}:
            raw_title = "Lobe AI"
        provider = (
            item.get("provider")
            or meta.get("provider")
            or chat_config.get("provider")
        )
        model = item.get("model") or meta.get("model") or chat_config.get("model")
        return AgentSummary(
            id=str(item.get("id", "")),
            title=str(raw_title).strip() or str(item.get("id", "")),
            model=str(model).strip() if model else None,
            provider=provider,
            workspace_id=_normalize_workspace_id(item.get("workspaceId")),
            chat_config=chat_config,
            raw=dict(item),
        )

    @staticmethod
    def _to_topic(item: Any) -> TopicSummary:
        if not isinstance(item, dict):
            return TopicSummary(id=str(item), title=str(item))
        return TopicSummary(
            id=str(item.get("id", "")),
            title=str(item.get("title", "")),
            agent_id=item.get("agentId"),
            group_id=item.get("groupId"),
            session_id=item.get("sessionId"),
            workspace_id=_normalize_workspace_id(item.get("workspaceId")),
            metadata=dict(item.get("metadata") or {}),
            raw=dict(item),
        )
