"""HTTP client for the public LobeHub API."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

from .exceptions import ApiError, ValidationError
from .models import AgentSummary, IntegrationConfig, TaskResult, TopicSummary

TransportResponse = Tuple[int, Mapping[str, str], bytes]
Transport = Callable[[str, str, Mapping[str, str], Optional[bytes]], TransportResponse]


def _default_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: Optional[bytes],
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
        transport: Optional[Transport] = None,
    ) -> None:
        if not config.api_key:
            raise ValidationError("api_key is required")
        if not config.base_url:
            raise ValidationError("base_url is required")

        self.config = config
        self._transport = transport or _default_transport

    @property
    def base_url(self) -> str:
        return self.config.base_url.rstrip("/") + "/"

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Mapping[str, Any]] = None,
        params: Optional[Mapping[str, Any]] = None,
    ) -> Any:
        url = urljoin(self.base_url, path.lstrip("/"))
        if params:
            query = urlencode([(k, v) for k, v in params.items() if v is not None], doseq=True)
            if query:
                url = f"{url}?{query}"

        headers: Dict[str, str] = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Accept": "application/json",
        }
        body: Optional[bytes] = None
        if json_body is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(json_body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

        try:
            status, response_headers, payload = self._transport(method.upper(), url, headers, body)
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

    def validate_auth(self) -> Any:
        return self.request("GET", "/api/v1/users/me")

    def list_agents(self, *, keyword: Optional[str] = None, page: Optional[int] = None, page_size: Optional[int] = None) -> Iterable[AgentSummary]:
        data = self.request(
            "GET",
            "/api/v1/agents",
            params={"keyword": keyword, "page": page, "pageSize": page_size},
        )
        agents = data.get("agents", []) if isinstance(data, dict) else []
        return [self._to_agent(item) for item in agents]

    def get_agent(self, agent_id: str) -> AgentSummary:
        data = self.request("GET", f"/api/v1/agents/{agent_id}")
        return self._to_agent(data.get("agent", data) if isinstance(data, dict) else data)

    def update_agent(self, agent_id: str, **changes: Any) -> AgentSummary:
        data = self.request("PATCH", f"/api/v1/agents/{agent_id}", json_body=changes)
        return self._to_agent(data.get("agent", data) if isinstance(data, dict) else data)

    def list_topics(
        self,
        *,
        agent_id: Optional[str] = None,
        group_id: Optional[str] = None,
        keyword: Optional[str] = None,
        page: Optional[int] = None,
        page_size: Optional[int] = None,
    ) -> Iterable[TopicSummary]:
        data = self.request(
            "GET",
            "/api/v1/topics",
            params={
                "agentId": agent_id,
                "groupId": group_id,
                "keyword": keyword,
                "page": page,
                "pageSize": page_size,
            },
        )
        topics = data.get("topics", []) if isinstance(data, dict) else []
        return [self._to_topic(item) for item in topics]

    def create_topic(
        self,
        *,
        title: str,
        agent_id: Optional[str] = None,
        group_id: Optional[str] = None,
        client_id: Optional[str] = None,
        favorite: Optional[bool] = None,
    ) -> TopicSummary:
        payload = {
            "agentId": agent_id,
            "clientId": client_id,
            "favorite": favorite,
            "groupId": group_id,
            "title": title,
        }
        data = self.request("POST", "/api/v1/topics", json_body=payload)
        return self._to_topic(data.get("data", data) if isinstance(data, dict) else data)

    def get_topic(self, topic_id: str) -> TopicSummary:
        data = self.request("GET", f"/api/v1/topics/{topic_id}")
        return self._to_topic(data.get("data", data) if isinstance(data, dict) else data)

    def create_message_reply(
        self,
        *,
        content: str,
        topic_id: str,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        client_id: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        thread_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = {
            "content": content,
            "clientId": client_id,
            "metadata": dict(metadata or {}),
            "model": model,
            "provider": provider,
            "role": "user",
            "threadId": thread_id,
            "topicId": topic_id,
        }
        return self.request("POST", "/api/v1/messages/replies", json_body=payload)

    def create_response(
        self,
        *,
        agent_id: str,
        instruction: str,
        context: Optional[Mapping[str, Any]] = None,
        previous_response_id: Optional[str] = None,
        stream: bool = False,
        tools: Optional[Iterable[Mapping[str, Any]]] = None,
    ) -> Dict[str, Any]:
        instructions = self._format_context(context)
        payload = {
            "input": instruction,
            "instructions": instructions or None,
            "model": agent_id,
            "previous_response_id": previous_response_id,
            "stream": stream,
            "tools": list(tools or []),
        }
        return self.request("POST", "/api/v1/responses", json_body=payload)

    @staticmethod
    def _format_context(context: Optional[Mapping[str, Any]]) -> Optional[str]:
        if not context:
            return None
        lines = []
        for key in sorted(context):
            value = context[key]
            lines.append(f"{key}: {value}")
        return "\n".join(lines)

    @staticmethod
    def _to_agent(item: Any) -> AgentSummary:
        if not isinstance(item, dict):
            return AgentSummary(id=str(item), title=str(item))
        return AgentSummary(
            id=str(item.get("id", "")),
            title=str(item.get("title", "")),
            model=item.get("model"),
            provider=item.get("provider"),
            chat_config=dict(item.get("chatConfig") or {}),
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
            metadata=dict(item.get("metadata") or {}),
            raw=dict(item),
        )

