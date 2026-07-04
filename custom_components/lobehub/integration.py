"""High-level integration façade for Home Assistant-style workflows."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, Iterable, Mapping, Optional
from uuid import uuid4

from .client import LobeHubClient
from .exceptions import ValidationError
from .models import AgentBinding, ConversationState, IntegrationConfig, TaskResult


class LobeHubIntegration:
    """Stateful integration layer used by HA actions and entities."""

    def __init__(self, client: LobeHubClient, config: IntegrationConfig) -> None:
        self.client = client
        self.config = config
        self._agent_bindings: Dict[str, AgentBinding] = {}
        self._conversations: Dict[str, ConversationState] = {}

    @property
    def agent_bindings(self) -> Dict[str, AgentBinding]:
        return dict(self._agent_bindings)

    @property
    def conversations(self) -> Dict[str, ConversationState]:
        return dict(self._conversations)

    def validate(self) -> Mapping[str, Any]:
        return self.client.validate_auth()

    def discover_agents(self, *, keyword: Optional[str] = None) -> Dict[str, AgentBinding]:
        agents = list(self.client.list_agents(keyword=keyword))
        return {
            agent.id: AgentBinding(
                agent_id=agent.id,
                enabled=True,
                allow_task=True,
                model=agent.model or self.config.default_model,
                provider=agent.provider or self.config.default_provider,
                runtime=self.config.default_runtime,
                title=agent.title,
                raw=agent.raw,
            )
            for agent in agents
        }

    def select_agents(self, agent_ids: Iterable[str]) -> Dict[str, AgentBinding]:
        discovered = self.discover_agents()
        selected: Dict[str, AgentBinding] = {}
        for agent_id in agent_ids:
            if agent_id not in discovered:
                raise ValidationError(f"Agent not found: {agent_id}")
            selected[agent_id] = discovered[agent_id]
        self._agent_bindings = selected
        return self.agent_bindings

    def configure_agent(
        self,
        agent_id: str,
        *,
        enabled: bool = True,
        allow_task: bool = True,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        runtime: Optional[str] = None,
        title: Optional[str] = None,
    ) -> AgentBinding:
        current = self._agent_bindings.get(agent_id)
        if current is None:
            current = AgentBinding(agent_id=agent_id)

        updated = replace(
            current,
            enabled=enabled,
            allow_task=allow_task,
            model=model if model is not None else current.model,
            provider=provider if provider is not None else current.provider,
            runtime=runtime if runtime is not None else current.runtime,
            title=title if title is not None else current.title,
        )
        self._agent_bindings[agent_id] = updated

        changes: Dict[str, Any] = {}
        if model is not None:
            changes["model"] = model
        if provider is not None:
            changes["provider"] = provider
        if title is not None:
            changes["title"] = title
        if changes:
            self.client.update_agent(agent_id, **changes)

        return updated

    def create_conversation(
        self,
        *,
        agent_id: str,
        title: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        runtime: Optional[str] = None,
        topic_title: Optional[str] = None,
    ) -> ConversationState:
        if agent_id not in self._agent_bindings:
            self._agent_bindings[agent_id] = AgentBinding(
                agent_id=agent_id,
                model=self.config.default_model,
                provider=self.config.default_provider,
                runtime=self.config.default_runtime,
            )

        topic = self.client.create_topic(
            title=topic_title or title or "Conversation",
            agent_id=agent_id,
        )
        conversation_id = str(uuid4())
        state = ConversationState(
            id=conversation_id,
            agent_id=agent_id,
            active_topic_id=topic.id,
            model=model if model is not None else self._agent_bindings[agent_id].model,
            provider=provider if provider is not None else self._agent_bindings[agent_id].provider,
            runtime=runtime if runtime is not None else self._agent_bindings[agent_id].runtime,
            title=title or topic.title,
        )
        self._conversations[conversation_id] = state
        return state

    def switch_topic(self, conversation_id: str, topic_id: str) -> ConversationState:
        conversation = self._get_conversation(conversation_id)
        updated = replace(conversation, active_topic_id=topic_id)
        self._conversations[conversation_id] = updated
        return updated

    def new_topic(self, conversation_id: str, title: str) -> ConversationState:
        conversation = self._get_conversation(conversation_id)
        topic = self.client.create_topic(title=title, agent_id=conversation.agent_id)
        updated = replace(conversation, active_topic_id=topic.id)
        self._conversations[conversation_id] = updated
        return updated

    def send_message(
        self,
        conversation_id: str,
        content: str,
        *,
        context: Optional[Mapping[str, Any]] = None,
        client_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        conversation = self._get_conversation(conversation_id)
        topic_id = conversation.active_topic_id
        if not topic_id:
            raise ValidationError("Conversation has no active topic")

        return self.client.create_message_reply(
            content=content,
            topic_id=topic_id,
            model=conversation.model or self.config.default_model,
            provider=conversation.provider or self.config.default_provider,
            client_id=client_id,
            metadata=context,
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
    ) -> TaskResult:
        binding = self._agent_bindings.get(agent_id)
        if binding is None:
            binding = AgentBinding(
                agent_id=agent_id,
                model=self.config.default_model,
                provider=self.config.default_provider,
                runtime=self.config.default_runtime,
            )
            self._agent_bindings[agent_id] = binding

        if not binding.allow_task:
            raise ValidationError(f"Task execution is disabled for agent: {agent_id}")

        selected_model = model if model is not None else binding.model
        selected_provider = provider if provider is not None else binding.provider
        selected_runtime = runtime if runtime is not None else binding.runtime

        if model is not None or provider is not None:
            self.client.update_agent(
                agent_id,
                **{k: v for k, v in {"model": model, "provider": provider}.items() if v is not None},
            )
            self._agent_bindings[agent_id] = replace(
                binding,
                model=selected_model,
                provider=selected_provider,
            )

        response = self.client.create_response(
            agent_id=agent_id,
            instruction=instruction,
            context={
                **dict(context or {}),
                "runtime": selected_runtime,
                "provider": selected_provider,
                "task_mode": "true",
            },
            previous_response_id=previous_response_id,
            stream=stream,
            tools=tools,
        )

        response_id = str(response.get("id", ""))
        output = response.get("output", []) if isinstance(response, dict) else []
        output_text = response.get("output_text", "") if isinstance(response, dict) else ""
        status = response.get("status", "completed") if isinstance(response, dict) else "completed"
        return TaskResult(
            response_id=response_id,
            output_text=output_text,
            output=list(output) if isinstance(output, list) else [],
            status=str(status),
            raw=dict(response) if isinstance(response, dict) else {"response": response},
        )

    def _get_conversation(self, conversation_id: str) -> ConversationState:
        try:
            return self._conversations[conversation_id]
        except KeyError as exc:
            raise ValidationError(f"Conversation not found: {conversation_id}") from exc

