"""Runtime helpers for the LobeHub Home Assistant integration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from .client import LobeHubClient
from .exceptions import ApiError
from .integration import LobeHubIntegration
from .models import (
    AgentBinding,
    ConversationState,
    IntegrationConfig,
    RemoteOptions,
    binding_from_data,
    binding_to_data,
    conversation_from_data,
    conversation_to_data,
)


class LobeHubRuntime:
    """Stateful helper that manages one LobeHub agent per config entry."""

    def __init__(self, integration: LobeHubIntegration) -> None:
        self.integration = integration
        self.agent_binding: AgentBinding | None = None
        self.conversation: ConversationState | None = None

    @property
    def agent_id(self) -> str | None:
        """Return the configured agent id."""

        return self.agent_binding.agent_id if self.agent_binding else None

    def configure(
        self,
        binding: AgentBinding,
        conversation: ConversationState | None = None,
    ) -> None:
        """Load the configured remote agent and optionally restore its topic."""

        self.agent_binding = self.integration.configure_agent(binding)
        if conversation is None or not conversation.id:
            self.conversation = None
            self.integration._conversation = None
            return

        restored = self.integration.restore_conversation(
            conversation.id,
            self.agent_binding,
            fallback=replace(
                conversation,
                model=self.agent_binding.model,
                provider=self.agent_binding.provider,
                runtime=self.agent_binding.runtime,
                topic_policy=self.agent_binding.topic_policy,
            ),
        )
        self.conversation = restored

    def new_topic(self, title: str) -> ConversationState:
        """Create and activate a new topic."""

        self.conversation = self.integration.new_topic(title)
        return self.conversation

    def switch_topic(self, topic_id: str) -> ConversationState:
        """Switch the active topic."""

        self.conversation = self.integration.switch_topic(topic_id)
        return self.conversation

    def send_conversation_message(
        self,
        message: str,
        *,
        conversation_id: str | None,
        context: Mapping[str, Any] | None = None,
    ) -> tuple[ConversationState, dict[str, Any]]:
        """Send a conversational message and persist the active topic."""

        conversation, payload = self.integration.send_message(
            message,
            conversation_id=conversation_id,
            context=context,
        )
        self.conversation = conversation
        return conversation, payload

    def run_task(
        self,
        instruction: str,
        *,
        context: Mapping[str, Any] | None = None,
        previous_response_id: str | None = None,
    ) -> Any:
        """Trigger the agent's task execution endpoint."""

        result = self.integration.run_task(
            instruction,
            context=context,
            previous_response_id=previous_response_id,
        )
        self.conversation = self.integration.conversation
        return result

    def list_tasks(
        self,
        *,
        assignee_agent_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
        parent_task: str | None = None,
        statuses: list[str] | None = None,
    ) -> dict[str, Any]:
        """List saved LobeHub tasks for the configured account."""

        return self.integration.list_tasks(
            assignee_agent_id=assignee_agent_id,
            limit=limit,
            offset=offset,
            parent_task=parent_task,
            statuses=statuses,
        )

    def list_agents(self) -> list[dict[str, Any]]:
        """List visible agents, including the configured default agent."""

        try:
            bindings = self.integration.discover_agents()
        except ApiError:
            if self.agent_binding is None:
                raise
            bindings = {}

        if self.agent_binding is not None:
            bindings.setdefault(self.agent_binding.agent_id, self.agent_binding)

        return [
            {
                "id": binding.agent_id,
                "agent_id": binding.agent_id,
                "title": binding.title,
                "model": binding.model,
                "provider": binding.provider,
                "runtime": binding.runtime,
                "bound_device_id": binding.bound_device_id,
                "workspace_id": binding.workspace_id,
            }
            for binding in bindings.values()
        ]

    def list_devices(self) -> list[dict[str, Any]]:
        """List execution devices visible to the configured account."""

        return self.integration.list_devices()

    def discover_remote_options(self) -> RemoteOptions:
        """Return remote model/provider/runtime/device options."""

        return self.integration.discover_remote_options()

    def update_agent_settings(
        self,
        *,
        model: str | None,
        provider: str | None,
        runtime: str,
        bound_device_id: str | None,
        topic_policy: str,
    ) -> AgentBinding:
        """Update remote agent settings and keep the runtime in sync."""

        binding = self.integration.update_agent_settings(
            model=model,
            provider=provider,
            runtime=runtime,
            bound_device_id=bound_device_id,
            topic_policy=topic_policy,
        )
        self.agent_binding = binding
        return binding

    def get_task(self, task: str) -> dict[str, Any]:
        """Fetch one saved LobeHub task plus its topic history."""

        return self.integration.get_task(task)

    def run_saved_task(
        self,
        task: str,
        *,
        continue_topic_id: str | None = None,
        prompt: str | None = None,
    ) -> Any:
        """Run one saved LobeHub task without changing the active conversation."""

        return self.integration.run_saved_task(
            task,
            continue_topic_id=continue_topic_id,
            prompt=prompt,
        )

    def snapshot(self) -> dict[str, Any]:
        """Return the current state snapshot for the conversation entity."""

        return {
            "agent_id": self.agent_id,
            "agent_title": self.agent_binding.title if self.agent_binding else None,
            "task_lookup_supported": True,
            "bound_device_id": (
                self.agent_binding.bound_device_id if self.agent_binding else None
            ),
            "active_topic_id": self.conversation.id if self.conversation else None,
            "conversation_id": self.conversation.id if self.conversation else None,
            "model": (
                self.conversation.model
                if self.conversation
                else (self.agent_binding.model if self.agent_binding else None)
            ),
            "provider": (
                self.conversation.provider
                if self.conversation
                else (self.agent_binding.provider if self.agent_binding else None)
            ),
            "runtime": (
                self.conversation.runtime
                if self.conversation
                else (self.agent_binding.runtime if self.agent_binding else None)
            ),
            "title": (
                self.conversation.title
                if self.conversation
                else (self.agent_binding.title if self.agent_binding else None)
            ),
            "topic_policy": self.agent_binding.topic_policy
            if self.agent_binding
            else None,
            "workspace_id": self.agent_binding.workspace_id
            if self.agent_binding
            else None,
        }

    def dump_binding(self) -> dict[str, Any] | None:
        """Serialize the configured agent binding for persistence."""

        if self.agent_binding is None:
            return None
        return binding_to_data(self.agent_binding)

    def dump_conversation(self) -> dict[str, Any] | None:
        """Serialize the active topic for persistence."""

        if self.conversation is None:
            return None
        return conversation_to_data(self.conversation)

    def refresh_remote_state(self) -> bool:
        """Refresh the current remote agent metadata and active topic."""

        if self.agent_binding is None:
            return False

        previous_binding = self.dump_binding()
        previous_conversation = self.dump_conversation()

        self.agent_binding = replace(
            self.integration.fetch_agent_binding(
                self.agent_binding.agent_id,
                workspace_id=self.agent_binding.workspace_id,
            ),
            enabled=self.agent_binding.enabled,
            topic_policy=self.agent_binding.topic_policy,
        )
        self.integration._agent_binding = self.agent_binding

        if self.conversation is not None:
            refreshed = self.integration.restore_conversation(
                self.conversation.id,
                self.agent_binding,
                fallback=self.conversation,
            )
            self.conversation = refreshed
        self.integration._conversation = self.conversation

        return (
            previous_binding != self.dump_binding()
            or previous_conversation != self.dump_conversation()
        )

    @staticmethod
    def is_remote_agent_missing(err: ApiError) -> bool:
        """Return whether the remote error means the configured agent was deleted."""

        if err.status_code == 404:
            return True
        return "agent not found" in err.message.lower()


def load_binding_item(data: Mapping[str, Any] | None) -> AgentBinding | None:
    """Load the persisted binding for one config entry."""

    if not isinstance(data, Mapping):
        return None

    binding = binding_from_data(data)
    return binding if binding.agent_id else None


def load_conversation_item(data: Mapping[str, Any] | None) -> ConversationState | None:
    """Load the persisted conversation for one config entry."""

    if not isinstance(data, Mapping):
        return None

    conversation = conversation_from_data(data)
    return conversation if conversation.id and conversation.agent_id else None


def build_runtime(
    config: IntegrationConfig, transport: Any | None = None
) -> LobeHubRuntime:
    """Create a runtime from a top-level integration config."""

    client = LobeHubClient(config, transport=transport)
    integration = LobeHubIntegration(client, config)
    return LobeHubRuntime(integration)
