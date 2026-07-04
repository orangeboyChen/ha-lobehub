"""Data models used by the integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

RuntimeMode = str


@dataclass
class IntegrationConfig:
    """Top-level integration configuration."""

    api_key: str
    base_url: str
    default_runtime: RuntimeMode = "gateway"
    default_model: Optional[str] = None
    default_provider: Optional[str] = None


@dataclass
class AgentSummary:
    """Lightweight representation of a LobeHub agent."""

    id: str
    title: str
    model: Optional[str] = None
    provider: Optional[str] = None
    chat_config: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TopicSummary:
    """Lightweight representation of a topic."""

    id: str
    title: str
    agent_id: Optional[str] = None
    group_id: Optional[str] = None
    session_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentBinding:
    """HA-side selection and overrides for a single agent."""

    agent_id: str
    enabled: bool = True
    allow_task: bool = True
    model: Optional[str] = None
    provider: Optional[str] = None
    runtime: RuntimeMode = "gateway"
    title: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ConversationState:
    """HA-side conversation state tied to one agent and one active topic."""

    id: str
    agent_id: str
    active_topic_id: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    runtime: RuntimeMode = "gateway"
    topic_policy: str = "single"
    title: Optional[str] = None


@dataclass
class TaskResult:
    """Normalized result returned from a task-trigger call."""

    response_id: str
    output_text: str
    output: List[Dict[str, Any]] = field(default_factory=list)
    status: str = "completed"
    raw: Dict[str, Any] = field(default_factory=dict)

