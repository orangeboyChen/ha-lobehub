"""Data models used by the integration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .const import DEFAULT_RUNTIME, EXECUTION_TARGETS
from .exceptions import ValidationError

RuntimeMode = str


def normalize_runtime(
    value: Any,
    fallback: RuntimeMode | None = DEFAULT_RUNTIME,
) -> RuntimeMode | None:
    """Normalize one runtime value to the upstream execution-target set."""

    if not isinstance(value, str):
        return fallback

    normalized = value.strip().lower()
    if not normalized:
        return fallback

    if normalized == "gateway":
        normalized = "auto"

    if normalized in EXECUTION_TARGETS:
        return normalized

    return fallback


def normalize_base_url(value: str) -> str:
    """Normalize the configured LobeHub base URL."""

    base_url = value.strip()
    if not base_url:
        raise ValidationError("base_url is required")

    parts = urlsplit(base_url)
    if not parts.scheme or not parts.netloc:
        raise ValidationError("base_url must include scheme and host")

    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path.rstrip("/"),
            "",
            "",
        )
    )


@dataclass
class IntegrationConfig:
    """Top-level integration configuration."""

    api_key: str
    base_url: str
    default_runtime: RuntimeMode = DEFAULT_RUNTIME
    default_model: str | None = None
    default_provider: str | None = None

    def __post_init__(self) -> None:
        """Normalize the base URL before it reaches the HTTP client."""

        self.base_url = normalize_base_url(self.base_url)
        self.default_runtime = (
            normalize_runtime(self.default_runtime) or DEFAULT_RUNTIME
        )


@dataclass
class AgentSummary:
    """Lightweight representation of a LobeHub agent."""

    id: str
    title: str
    model: str | None = None
    provider: str | None = None
    workspace_id: str | None = None
    chat_config: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class RemoteOptions:
    """Remote model/provider/runtime options discovered from LobeHub."""

    models: list[str] = field(default_factory=list)
    model_labels: dict[str, str] = field(default_factory=dict)
    providers: list[str] = field(default_factory=list)
    runtimes: list[str] = field(default_factory=list)
    devices: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class TopicSummary:
    """Lightweight representation of a topic."""

    id: str
    title: str
    agent_id: str | None = None
    group_id: str | None = None
    session_id: str | None = None
    workspace_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentBinding:
    """HA-side configuration for a single LobeHub agent."""

    agent_id: str
    enabled: bool = True
    model: str | None = None
    provider: str | None = None
    runtime: RuntimeMode = DEFAULT_RUNTIME
    bound_device_id: str | None = None
    title: str | None = None
    topic_policy: str = "reuse"
    workspace_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConversationState:
    """HA-side conversation state tied to one remote LobeHub topic."""

    id: str
    agent_id: str
    model: str | None = None
    provider: str | None = None
    runtime: RuntimeMode = DEFAULT_RUNTIME
    topic_policy: str = "reuse"
    title: str | None = None
    workspace_id: str | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class TaskResult:
    """Normalized result returned from a task-trigger call."""

    response_id: str
    output_text: str
    task_id: str = ""
    task_identifier: str = ""
    assistant_message_id: str = ""
    operation_id: str = ""
    topic_id: str = ""
    output: list[dict[str, Any]] = field(default_factory=list)
    status: str = "completed"
    raw: dict[str, Any] = field(default_factory=dict)


def binding_from_summary(
    summary: AgentSummary,
    *,
    runtime: str = DEFAULT_RUNTIME,
) -> AgentBinding:
    """Build an HA binding from a remote agent summary."""

    return AgentBinding(
        agent_id=summary.id,
        model=summary.model,
        provider=summary.provider,
        runtime=normalize_runtime(runtime) or DEFAULT_RUNTIME,
        bound_device_id=(
            ((summary.raw.get("agencyConfig", {}) or {}).get("boundDeviceId"))
            or summary.raw.get("boundDeviceId")
        )
        if isinstance(summary.raw, dict)
        else None,
        title=summary.title,
        workspace_id=summary.workspace_id,
        raw=dict(summary.raw),
    )


def binding_from_data(data: Mapping[str, Any]) -> AgentBinding:
    """Deserialize an agent binding from stored config data."""

    return AgentBinding(
        agent_id=str(data.get("agent_id", "")),
        enabled=bool(data.get("enabled", True)),
        model=data.get("model"),
        provider=data.get("provider"),
        runtime=normalize_runtime(data.get("runtime")) or DEFAULT_RUNTIME,
        bound_device_id=data.get("bound_device_id"),
        title=data.get("title"),
        topic_policy=str(data.get("topic_policy", "reuse") or "reuse"),
        workspace_id=data.get("workspace_id"),
        raw=dict(data.get("raw") or {}),
    )


def binding_to_data(binding: AgentBinding) -> dict[str, Any]:
    """Serialize an agent binding for storage."""

    return {
        "agent_id": binding.agent_id,
        "enabled": binding.enabled,
        "model": binding.model,
        "provider": binding.provider,
        "runtime": binding.runtime,
        "bound_device_id": binding.bound_device_id,
        "title": binding.title,
        "topic_policy": binding.topic_policy,
        "workspace_id": binding.workspace_id,
    }


def conversation_from_data(data: Mapping[str, Any]) -> ConversationState:
    """Deserialize a conversation state from stored config data."""

    topic_id = (
        data.get("active_topic_id")
        or data.get("topic_id")
        or data.get("id")
        or ""
    )
    return ConversationState(
        id=str(topic_id),
        agent_id=str(data.get("agent_id", "")),
        model=data.get("model"),
        provider=data.get("provider"),
        runtime=normalize_runtime(data.get("runtime")) or DEFAULT_RUNTIME,
        topic_policy=str(data.get("topic_policy", "reuse") or "reuse"),
        title=data.get("title"),
        workspace_id=data.get("workspace_id"),
    )


def conversation_to_data(conversation: ConversationState) -> dict[str, Any]:
    """Serialize a conversation state for storage."""

    return {
        "id": conversation.id,
        "agent_id": conversation.agent_id,
        "model": conversation.model,
        "provider": conversation.provider,
        "runtime": conversation.runtime,
        "topic_policy": conversation.topic_policy,
        "title": conversation.title,
        "workspace_id": conversation.workspace_id,
    }
