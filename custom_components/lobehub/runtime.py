"""Runtime helpers for the LobeHub Home Assistant integration."""

from __future__ import annotations

from dataclasses import asdict, replace
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional

from .const import DEFAULT_RUNTIME
from .client import LobeHubClient
from .models import AgentBinding, AgentSummary, ConversationState, IntegrationConfig
from .integration import LobeHubIntegration


def binding_from_summary(
    summary: AgentSummary,
    *,
    allow_task: bool = True,
    enabled: bool = True,
    runtime: str = DEFAULT_RUNTIME,
) -> AgentBinding:
    """Build a binding from a remote agent summary."""

    return AgentBinding(
        agent_id=summary.id,
        enabled=enabled,
        allow_task=allow_task,
        model=summary.model,
        provider=summary.provider,
        runtime=runtime,
        title=summary.title,
        raw=dict(summary.raw),
    )


def binding_from_data(data: Mapping[str, Any]) -> AgentBinding:
    """Deserialize an agent binding from stored config data."""

    return AgentBinding(
        agent_id=str(data.get("agent_id", "")),
        enabled=bool(data.get("enabled", True)),
        allow_task=bool(data.get("allow_task", True)),
        model=data.get("model"),
        provider=data.get("provider"),
        runtime=str(data.get("runtime", DEFAULT_RUNTIME) or DEFAULT_RUNTIME),
        title=data.get("title"),
        raw=dict(data.get("raw") or {}),
    )


def binding_to_data(binding: AgentBinding) -> Dict[str, Any]:
    """Serialize an agent binding for storage."""

    return asdict(binding)


def conversation_from_data(data: Mapping[str, Any]) -> ConversationState:
    """Deserialize a conversation state from stored config data."""

    return ConversationState(
        id=str(data.get("id", "")),
        agent_id=str(data.get("agent_id", "")),
        active_topic_id=data.get("active_topic_id"),
        model=data.get("model"),
        provider=data.get("provider"),
        runtime=str(data.get("runtime", DEFAULT_RUNTIME) or DEFAULT_RUNTIME),
        topic_policy=str(data.get("topic_policy", "single") or "single"),
        title=data.get("title"),
    )


def conversation_to_data(conversation: ConversationState) -> Dict[str, Any]:
    """Serialize a conversation state for storage."""

    return asdict(conversation)


def normalize_agent_ids(selected_agents: Any) -> List[str]:
    """Normalize agent selections into a list of agent IDs."""

    if selected_agents is None:
        return []
    if isinstance(selected_agents, str):
        items = [item.strip() for item in selected_agents.split(",")]
        return [item for item in items if item]
    if isinstance(selected_agents, Iterable):
        return [str(item) for item in selected_agents if str(item)]
    return [str(selected_agents)]


class LobeHubRuntime:
    """Stateful helper that keeps one conversation per selected agent."""

    def __init__(self, integration: LobeHubIntegration) -> None:
        self.integration = integration
        self.agent_bindings: Dict[str, AgentBinding] = {}
        self.conversations_by_agent: Dict[str, ConversationState] = {}

    @property
    def selected_agent_ids(self) -> List[str]:
        return list(self.agent_bindings)

    def configure_agents(self, bindings: Iterable[AgentBinding]) -> List[ConversationState]:
        """Apply agent bindings and ensure a conversation exists for each selected agent."""

        ordered_bindings = [binding for binding in bindings if binding.enabled]
        selected_ids = [binding.agent_id for binding in ordered_bindings]
        if selected_ids:
            self.integration.select_agents(selected_ids)
        else:
            self.integration._agent_bindings = {}

        conversations: List[ConversationState] = []
        self.agent_bindings = {}
        for binding in ordered_bindings:
            updated = self.integration.configure_agent(
                binding.agent_id,
                enabled=binding.enabled,
                allow_task=binding.allow_task,
                model=binding.model,
                provider=binding.provider,
                runtime=binding.runtime,
                title=binding.title,
            )
            self.agent_bindings[binding.agent_id] = updated

            conversation = self.conversations_by_agent.get(binding.agent_id)
            if conversation is None:
                conversation = self.integration.create_conversation(
                    agent_id=binding.agent_id,
                    title=binding.title or updated.title or binding.agent_id,
                    model=updated.model,
                    provider=updated.provider,
                    runtime=updated.runtime,
                    topic_title=binding.title or updated.title or binding.agent_id,
                )
            else:
                conversation = replace(
                    conversation,
                    model=updated.model,
                    provider=updated.provider,
                    runtime=updated.runtime,
                    title=updated.title or conversation.title,
                )
            self.conversations_by_agent[binding.agent_id] = conversation
            conversations.append(conversation)

        return conversations

    def remove_agents(self, agent_ids: Iterable[str]) -> None:
        """Drop bindings and conversations for deselected agents."""

        for agent_id in agent_ids:
            self.agent_bindings.pop(agent_id, None)
            self.conversations_by_agent.pop(agent_id, None)

    def ensure_conversation(self, agent_id: str) -> ConversationState:
        """Return the active conversation for an agent, creating it if needed."""

        conversation = self.conversations_by_agent.get(agent_id)
        if conversation is not None:
            return conversation

        binding = self.agent_bindings.get(agent_id)
        if binding is None:
            binding = self.integration.configure_agent(agent_id)
            self.agent_bindings[agent_id] = binding

        conversation = self.integration.create_conversation(
            agent_id=agent_id,
            title=binding.title or agent_id,
            model=binding.model,
            provider=binding.provider,
            runtime=binding.runtime,
            topic_title=binding.title or agent_id,
        )
        self.conversations_by_agent[agent_id] = conversation
        return conversation

    def new_topic(self, agent_id: str, title: str) -> ConversationState:
        """Create a new topic for the agent and mark it active."""

        conversation = self.ensure_conversation(agent_id)
        updated = self.integration.new_topic(conversation.id, title)
        self.conversations_by_agent[agent_id] = updated
        return updated

    def switch_topic(self, agent_id: str, topic_id: str) -> ConversationState:
        """Switch the active topic for the agent."""

        conversation = self.ensure_conversation(agent_id)
        updated = self.integration.switch_topic(conversation.id, topic_id)
        self.conversations_by_agent[agent_id] = updated
        return updated

    def send_message(
        self,
        agent_id: str,
        message: str,
        *,
        context: Optional[Mapping[str, Any]] = None,
        client_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send a message to the agent's active conversation."""

        conversation = self.ensure_conversation(agent_id)
        return self.integration.send_message(
            conversation.id,
            message,
            context=context,
            client_id=client_id,
        )

    def run_task(
        self,
        agent_id: str,
        instruction: str,
        *,
        context: Optional[Mapping[str, Any]] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        runtime: Optional[str] = None,
        tools: Optional[Iterable[Mapping[str, Any]]] = None,
        previous_response_id: Optional[str] = None,
        stream: bool = False,
    ) -> Any:
        """Trigger the agent's task execution endpoint."""

        return self.integration.run_task(
            agent_id,
            instruction,
            context=context,
            model=model,
            provider=provider,
            runtime=runtime,
            tools=tools,
            previous_response_id=previous_response_id,
            stream=stream,
        )

    def snapshot(self, agent_id: str) -> Dict[str, Any]:
        """Return the current state snapshot for a conversation entity."""

        conversation = self.ensure_conversation(agent_id)
        binding = self.agent_bindings.get(agent_id)
        return {
            "agent_id": agent_id,
            "agent_title": binding.title if binding else None,
            "active_topic_id": conversation.active_topic_id,
            "conversation_id": conversation.id,
            "model": conversation.model,
            "provider": conversation.provider,
            "runtime": conversation.runtime,
            "title": conversation.title,
        }

    def dump_bindings(self) -> List[Dict[str, Any]]:
        """Serialize selected agents for persistence."""

        return [binding_to_data(binding) for binding in self.agent_bindings.values()]

    def dump_conversations(self) -> List[Dict[str, Any]]:
        """Serialize conversations for persistence."""

        return [conversation_to_data(conversation) for conversation in self.conversations_by_agent.values()]


def build_runtime(config: IntegrationConfig, transport: Optional[Any] = None) -> LobeHubRuntime:
    """Create a runtime from a top-level integration config."""

    client = LobeHubClient(config, transport=transport)
    integration = LobeHubIntegration(client, config)
    return LobeHubRuntime(integration)
