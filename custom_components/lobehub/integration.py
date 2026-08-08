"""High-level integration facade for Home Assistant workflows."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from .client import LobeHubClient
from .exceptions import ApiError, ValidationError
from .models import (
    AgentBinding,
    ConversationState,
    IntegrationConfig,
    RemoteOptions,
    TaskResult,
    binding_from_summary,
    normalize_runtime,
)

_LOBEHUB_LOADING_PLACEHOLDER = "..."


def _extract_message_text(message: Mapping[str, Any] | None) -> str:
    """Extract readable text from one stored LobeHub message."""

    if not isinstance(message, Mapping):
        return ""

    for key in ("content", "text"):
        value = message.get(key)
        if isinstance(value, str):
            stripped = value.strip()
            if stripped and stripped != _LOBEHUB_LOADING_PLACEHOLDER:
                return stripped
        if isinstance(value, list):
            fragments = [
                str(fragment.get("text") or fragment.get("content")).strip()
                for fragment in value
                if isinstance(fragment, Mapping)
                and isinstance(
                    fragment.get("text") or fragment.get("content"),
                    str,
                )
                and str(fragment.get("text") or fragment.get("content")).strip()
            ]
            if fragments:
                return "\n".join(fragments)

    return ""


def _extract_message_id(message: Mapping[str, Any] | None) -> str:
    """Extract the stable message id from one stored LobeHub message."""

    if not isinstance(message, Mapping):
        return ""

    for key in ("id", "messageId"):
        value = message.get(key)
        if isinstance(value, str) and value:
            return value

    return ""


def extract_assistant_text(
    messages: list[dict[str, Any]],
    assistant_message_id: str | None = None,
) -> str:
    """Return assistant text, preferring the exact assistant message when known."""

    if assistant_message_id:
        for message in messages:
            if _extract_message_id(message) != assistant_message_id:
                continue
            role = str(message.get("role") or message.get("sender") or "")
            if role in {"assistant", "model"}:
                text = _extract_message_text(message)
                if text:
                    return text
            break

    return extract_latest_assistant_text(messages)


def extract_latest_assistant_text(messages: list[dict[str, Any]]) -> str:
    """Return the most recent assistant message text from a topic history."""

    for message in reversed(messages):
        role = str(message.get("role") or message.get("sender") or "")
        if role not in {"assistant", "model"}:
            continue
        text = _extract_message_text(message)
        if text:
            return text
    return ""


def extract_operation_error(status: Mapping[str, Any] | None) -> str:
    """Extract a readable operation error from a status payload."""

    if not isinstance(status, Mapping):
        return ""

    current_state = status.get("currentState")
    if not isinstance(current_state, Mapping):
        return ""

    error = current_state.get("error")
    if isinstance(error, Mapping):
        for key in ("message", "type"):
            value = error.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        body = error.get("body")
        if isinstance(body, Mapping):
            for key in ("message", "detail"):
                value = body.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

    if isinstance(error, str) and error.strip():
        return error.strip()

    return ""


def extract_operation_text(status: Mapping[str, Any] | None) -> str:
    """Extract final assistant text from an operation status payload."""

    if not isinstance(status, Mapping):
        return ""

    for key in ("result", "resultContent", "output_text", "response"):
        value = status.get(key)
        if isinstance(value, str):
            stripped = value.strip()
            if stripped and stripped != _LOBEHUB_LOADING_PLACEHOLDER:
                return stripped
        if isinstance(value, Mapping):
            extracted = _extract_message_text(value)
            if extracted:
                return extracted

    current_state = status.get("currentState")
    if isinstance(current_state, Mapping):
        for key in ("result", "resultContent", "output_text", "response", "content"):
            value = current_state.get(key)
            if isinstance(value, str):
                stripped = value.strip()
                if stripped and stripped != _LOBEHUB_LOADING_PLACEHOLDER:
                    return stripped
            if isinstance(value, Mapping):
                extracted = _extract_message_text(value)
                if extracted:
                    return extracted

    return ""


def extract_operation_status_reason(status: Mapping[str, Any] | None) -> str:
    """Extract a non-error terminal state reason from an operation status payload."""

    if not isinstance(status, Mapping):
        return ""

    current_state = status.get("currentState")
    if not isinstance(current_state, Mapping):
        return ""

    for key in ("status", "reason"):
        value = current_state.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    return ""


def _normalize_task_item(item: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one LobeHub task record for Home Assistant service responses."""

    return {
        "id": str(item.get("id") or ""),
        "identifier": str(item.get("identifier") or ""),
        "name": item.get("name"),
        "description": item.get("description"),
        "status": item.get("status"),
        "priority": item.get("priority"),
        "assignee_agent_id": item.get("assigneeAgentId") or item.get("assignee_agent_id"),
        "assignee_user_id": item.get("assigneeUserId") or item.get("assignee_user_id"),
        "current_topic_id": item.get("currentTopicId") or item.get("current_topic_id"),
        "parent_task_id": item.get("parentTaskId") or item.get("parent_task_id"),
        "total_topics": item.get("totalTopics") or item.get("total_topics"),
        "error": item.get("error"),
        "raw": dict(item),
    }


def _normalize_task_topic(item: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one task-topic record for Home Assistant service responses."""

    topic_id = item.get("topicId") or item.get("topic_id") or item.get("id") or ""
    return {
        "topic_id": str(topic_id),
        "title": item.get("title") or item.get("topicTitle"),
        "status": item.get("status"),
        "operation_id": item.get("operationId") or item.get("operation_id"),
        "sequence": item.get("seq") or item.get("sequence"),
        "review_iteration": item.get("reviewIteration") or item.get("review_iteration"),
        "started_at": item.get("startedAt") or item.get("createdAt"),
        "completed_at": item.get("completedAt") or item.get("updatedAt"),
        "raw": dict(item),
    }


class LobeHubIntegration:
    """Stateful integration layer used by HA entities and services."""

    def __init__(self, client: LobeHubClient, config: IntegrationConfig) -> None:
        self.client = client
        self.config = config
        self._agent_binding: AgentBinding | None = None
        self._conversation: ConversationState | None = None

    @property
    def agent_binding(self) -> AgentBinding | None:
        """Return the current configured agent."""

        return self._agent_binding

    @property
    def conversation(self) -> ConversationState | None:
        """Return the current active topic state."""

        return self._conversation

    def validate(self) -> Mapping[str, Any]:
        """Validate credentials against the remote LobeHub instance."""

        return self.client.validate_auth()

    def discover_agents(self) -> dict[str, AgentBinding]:
        """Return all visible remote agents keyed by agent id."""

        return {
            agent.id: self.fetch_agent_binding(
                agent.id,
                workspace_id=agent.workspace_id,
            )
            for agent in self.client.list_agents()
        }

    def fetch_agent_binding(
        self,
        agent_id: str,
        *,
        workspace_id: str | None = None,
    ) -> AgentBinding:
        """Fetch the latest remote configuration for one agent."""

        summary = self.client.get_agent(agent_id, workspace_id=workspace_id)
        binding = binding_from_summary(
            summary,
            runtime=str(
                summary.raw.get("agencyConfig", {}).get("executionTarget")
                or summary.raw.get("executionTarget")
                or self.config.default_runtime
            ),
        )
        if not binding.model:
            binding.model = self.config.default_model
        if not binding.provider:
            binding.provider = self.config.default_provider
        return binding

    def configure_agent(self, binding: AgentBinding) -> AgentBinding:
        """Load the chosen agent and merge HA-local policy settings."""

        remote_binding = self.fetch_agent_binding(
            binding.agent_id,
            workspace_id=binding.workspace_id,
        )
        configured = replace(
            remote_binding,
            enabled=binding.enabled,
            model=binding.model,
            provider=binding.provider,
            runtime=binding.runtime,
            bound_device_id=binding.bound_device_id,
            topic_policy=binding.topic_policy,
        )
        self._agent_binding = configured
        return configured

    def discover_remote_options(self) -> RemoteOptions:
        """Return remote model/provider/runtime/device options for this account."""

        binding = self._require_agent_binding()
        return self.client.discover_remote_options(workspace_id=binding.workspace_id)

    def list_devices(self) -> list[dict[str, Any]]:
        """Return execution devices visible to the configured account."""

        binding = self._require_agent_binding()
        return self.client.list_devices(workspace_id=binding.workspace_id)

    def update_agent_settings(
        self,
        *,
        model: str | None,
        provider: str | None,
        runtime: str,
        bound_device_id: str | None,
        topic_policy: str,
    ) -> AgentBinding:
        """Update remote agent settings and refresh the active binding."""

        binding = self._require_agent_binding()
        normalized_runtime = normalize_runtime(runtime, self.config.default_runtime)
        if normalized_runtime is None:
            raise ValidationError(f"Unsupported execution target: {runtime}")

        agency_config = dict(binding.raw.get("agencyConfig") or {})
        agency_config["executionTarget"] = normalized_runtime
        if normalized_runtime == "device":
            if not bound_device_id:
                raise ValidationError(
                    "Selecting execution target 'device' requires a bound device"
                )
        agency_config["boundDeviceId"] = bound_device_id

        changes: dict[str, Any] = {
            "agencyConfig": agency_config,
            "model": model,
            "provider": provider,
        }
        self.client.update_agent(
            binding.agent_id,
            workspace_id=binding.workspace_id,
            **changes,
        )
        updated_binding = self.configure_agent(
            replace(
                binding,
                model=model,
                provider=provider,
                runtime=normalized_runtime,
                bound_device_id=bound_device_id,
                topic_policy=topic_policy,
            )
        )
        if self._conversation is not None:
            self._conversation = replace(
                self._conversation,
                model=updated_binding.model,
                provider=updated_binding.provider,
                runtime=updated_binding.runtime,
                topic_policy=updated_binding.topic_policy,
            )
        return updated_binding

    def restore_conversation(
        self,
        topic_id: str,
        binding: AgentBinding,
        *,
        fallback: ConversationState | None = None,
        persist: bool = True,
    ) -> ConversationState | None:
        """Restore a persisted topic from LobeHub."""

        topic_workspace_id = (
            fallback.workspace_id
            if fallback is not None and fallback.workspace_id
            else binding.workspace_id
        )
        try:
            topic = self.client.get_topic(topic_id, workspace_id=topic_workspace_id)
            messages = self.client.get_topic_messages(
                topic_id,
                workspace_id=topic.workspace_id or topic_workspace_id,
            )
        except ApiError as err:
            if self._is_not_found_error(err):
                return None
            return fallback

        if topic.agent_id and topic.agent_id != binding.agent_id:
            return None

        conversation = ConversationState(
            id=topic.id,
            agent_id=binding.agent_id,
            model=binding.model,
            provider=binding.provider,
            runtime=binding.runtime,
            topic_policy=binding.topic_policy,
            title=topic.title or (fallback.title if fallback else binding.title),
            workspace_id=topic.workspace_id or topic_workspace_id,
            messages=messages,
        )
        if persist:
            self._conversation = conversation
        return conversation

    def new_topic(self, title: str) -> ConversationState:
        """Create a new remote topic and make it active."""

        binding = self._require_agent_binding()
        topic = self.client.create_topic(
            title=title,
            agent_id=binding.agent_id,
            workspace_id=binding.workspace_id,
        )
        conversation = ConversationState(
            id=topic.id,
            agent_id=binding.agent_id,
            model=binding.model,
            provider=binding.provider,
            runtime=binding.runtime,
            topic_policy=binding.topic_policy,
            title=topic.title or title,
            workspace_id=topic.workspace_id or binding.workspace_id,
            messages=self.client.get_topic_messages(
                topic.id,
                workspace_id=topic.workspace_id or binding.workspace_id,
            ),
        )
        self._conversation = conversation
        return conversation

    def switch_topic(self, topic_id: str) -> ConversationState:
        """Switch the active topic to an existing remote topic."""

        binding = self._require_agent_binding()
        conversation = self.restore_conversation(topic_id, binding)
        if conversation is None:
            raise ValidationError(
                "Topic not found or does not belong to "
                f"agent {binding.agent_id}: {topic_id}"
            )
        self._conversation = conversation
        return conversation

    def send_message(
        self,
        content: str,
        *,
        conversation_id: str | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> tuple[ConversationState, dict[str, Any]]:
        """Send a message by delegating turn creation to execAgent."""

        binding = self._require_agent_binding()
        explicit_topic_id = self._context_topic_id(context)
        active_topic_id = self._validate_topic_for_binding(
            self._resolve_conversation_topic_id(
                binding.topic_policy,
                conversation_id=conversation_id,
                context=context,
            ),
            binding,
            allow_missing=explicit_topic_id is None,
        )

        response = self.client.create_response(
            agent_id=binding.agent_id,
            instruction=content,
            topic_id=active_topic_id,
            context=context,
            device_id=binding.bound_device_id,
            workspace_id=binding.workspace_id,
        )

        topic_id = response.get("topicId") or active_topic_id
        if not isinstance(topic_id, str) or not topic_id:
            raise ValidationError("LobeHub did not return a topic id")

        operation_status = None
        operation_id = response.get("operationId")
        if isinstance(operation_id, str) and operation_id:
            operation_status = self.client.wait_for_operation(
                operation_id,
                workspace_id=binding.workspace_id,
            )

        conversation = self.restore_conversation(topic_id, binding)
        if conversation is None:
            conversation = ConversationState(
                id=topic_id,
                agent_id=binding.agent_id,
                model=binding.model,
                provider=binding.provider,
                runtime=binding.runtime,
                topic_policy=binding.topic_policy,
                title=self._conversation.title if self._conversation else binding.title,
                workspace_id=binding.workspace_id,
                messages=[],
            )
            self._conversation = conversation

        final_output_text = extract_assistant_text(
            conversation.messages,
            str(response.get("assistantMessageId") or ""),
        )
        if not final_output_text:
            final_output_text = extract_operation_text(operation_status)
        if not final_output_text and isinstance(operation_id, str) and operation_id:
            final_output_text = self.client.collect_stream_text(
                self.client.get_agent_stream_events(
                    operation_id,
                    workspace_id=binding.workspace_id,
                )
            )
        payload = dict(response)
        payload["operation_status"] = operation_status
        payload["final_output_text"] = final_output_text
        payload["output_text"] = final_output_text
        return conversation, payload

    def run_task(
        self,
        instruction: str,
        *,
        context: Mapping[str, Any] | None = None,
        previous_response_id: str | None = None,
    ) -> TaskResult:
        """Trigger the agent execution endpoint and track the resulting topic."""

        binding = self._require_agent_binding()
        explicit_topic_id = self._context_topic_id(context)
        topic_id = self._validate_topic_for_binding(
            self._resolve_task_topic_id(
                binding.topic_policy,
                context=context,
                previous_response_id=previous_response_id,
            ),
            binding,
            allow_missing=explicit_topic_id is None and previous_response_id is None,
        )
        if previous_response_id:
            if not topic_id:
                raise ValidationError(
                    "Continuing a previous task requires an existing topic; "
                    "the active topic was not found, so switch to the correct topic "
                    "first or pass context.topicId"
                )
            self._validate_message_for_topic(
                previous_response_id,
                topic_id,
            )
        response = self.client.create_response(
            agent_id=binding.agent_id,
            instruction=instruction,
            topic_id=topic_id,
            context=context,
            previous_response_id=previous_response_id,
            device_id=binding.bound_device_id,
            workspace_id=binding.workspace_id,
        )

        operation_status = None
        operation_id = response.get("operationId")
        if isinstance(operation_id, str) and operation_id:
            operation_status = self.client.wait_for_operation(
                operation_id,
                workspace_id=binding.workspace_id,
            )

        output_text = ""
        response_topic_id = response.get("topicId")
        if isinstance(response_topic_id, str) and response_topic_id:
            conversation = self.restore_conversation(
                response_topic_id,
                binding,
                persist=False,
            )
            if conversation is not None:
                output_text = extract_assistant_text(
                    conversation.messages,
                    str(response.get("assistantMessageId") or ""),
                )
                if not output_text:
                    output_text = extract_operation_text(operation_status)
            else:
                conversation = ConversationState(
                    id=response_topic_id,
                    agent_id=binding.agent_id,
                    model=binding.model,
                    provider=binding.provider,
                    runtime=binding.runtime,
                    topic_policy=binding.topic_policy,
                    title=(
                        self._conversation.title
                        if self._conversation
                        and self._conversation.id == response_topic_id
                        else binding.title
                    ),
                    workspace_id=binding.workspace_id,
                    messages=[],
                )
            self._conversation = conversation
        if not output_text and isinstance(operation_id, str) and operation_id:
            output_text = self.client.collect_stream_text(
                self.client.get_agent_stream_events(
                    operation_id,
                    workspace_id=binding.workspace_id,
                )
            )

        status = "completed"
        if isinstance(operation_status, Mapping):
            current_state = operation_status.get("currentState")
            if isinstance(current_state, Mapping):
                current_status = current_state.get("status")
                if isinstance(current_status, str) and current_status:
                    status = current_status
        elif isinstance(response.get("status"), str):
            status = response["status"]

        assistant_message_id = str(response.get("assistantMessageId") or "")
        return TaskResult(
            response_id=assistant_message_id or str(response.get("messageId") or ""),
            output_text=output_text,
            task_id="",
            task_identifier="",
            assistant_message_id=assistant_message_id,
            operation_id=str(response.get("operationId") or ""),
            topic_id=str(response_topic_id or ""),
            status=status,
            raw={
                **dict(response),
                "operation_status": operation_status,
            },
        )

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

        payload = self.client.list_tasks(
            assignee_agent_id=assignee_agent_id,
            limit=limit,
            offset=offset,
            parent_task=parent_task,
            statuses=statuses,
            workspace_id=self._require_agent_binding().workspace_id,
        )
        raw_tasks = payload.get("tasks")
        tasks = (
            [
                _normalize_task_item(item)
                for item in raw_tasks
                if isinstance(item, Mapping)
            ]
            if isinstance(raw_tasks, list)
            else []
        )
        total = payload.get("total")
        return {
            "tasks": tasks,
            "total": total if isinstance(total, int) else len(tasks),
            "raw": payload.get("raw") if isinstance(payload.get("raw"), Mapping) else payload,
        }

    def get_task(self, task: str) -> dict[str, Any]:
        """Return one task plus its linked topic history."""

        workspace_id = self._require_agent_binding().workspace_id
        detail = self.client.get_task_detail(task, workspace_id=workspace_id)
        topics = self.client.get_task_topics(task, workspace_id=workspace_id)
        normalized_detail = (
            _normalize_task_item(detail) if isinstance(detail, Mapping) else {"id": "", "raw": {}}
        )
        return {
            "task": normalized_detail,
            "topics": [
                _normalize_task_topic(topic)
                for topic in topics
                if isinstance(topic, Mapping)
            ],
            "raw": {
                "detail": detail,
                "topics": topics,
            },
        }

    def run_saved_task(
        self,
        task: str,
        *,
        continue_topic_id: str | None = None,
        prompt: str | None = None,
    ) -> TaskResult:
        """Run one saved LobeHub task without rebinding the active HA conversation."""

        workspace_id = self._require_agent_binding().workspace_id
        response = self.client.run_saved_task(task, workspace_id=workspace_id)

        operation_status = None
        operation_id = response.get("operationId")
        if isinstance(operation_id, str) and operation_id:
            operation_status = self.client.wait_for_operation(
                operation_id,
                workspace_id=workspace_id,
            )

        output_text = ""
        response_topic_id = response.get("topicId")
        if isinstance(response_topic_id, str) and response_topic_id:
            try:
                output_text = extract_assistant_text(
                    self.client.get_topic_messages(
                        response_topic_id,
                        workspace_id=workspace_id,
                    ),
                    str(response.get("assistantMessageId") or ""),
                )
            except ApiError:
                output_text = ""
        if not output_text:
            output_text = extract_operation_text(operation_status)
        if not output_text and isinstance(operation_id, str) and operation_id:
            output_text = self.client.collect_stream_text(
                self.client.get_agent_stream_events(
                    operation_id,
                    workspace_id=workspace_id,
                )
            )

        status = "completed"
        if isinstance(operation_status, Mapping):
            current_state = operation_status.get("currentState")
            if isinstance(current_state, Mapping):
                current_status = current_state.get("status")
                if isinstance(current_status, str) and current_status:
                    status = current_status
        elif isinstance(response.get("status"), str):
            status = response["status"]

        assistant_message_id = str(response.get("assistantMessageId") or "")
        return TaskResult(
            response_id=assistant_message_id or str(response.get("messageId") or ""),
            output_text=output_text,
            task_id=str(response.get("taskId") or ""),
            task_identifier=str(response.get("taskIdentifier") or ""),
            assistant_message_id=assistant_message_id,
            operation_id=str(response.get("operationId") or ""),
            topic_id=str(response_topic_id or ""),
            status=status,
            raw={
                **dict(response),
                "operation_status": operation_status,
            },
        )

    def _require_agent_binding(self) -> AgentBinding:
        if self._agent_binding is None:
            raise ValidationError("No LobeHub agent is configured")
        return self._agent_binding

    def _validate_topic_for_binding(
        self,
        topic_id: str | None,
        binding: AgentBinding,
        *,
        allow_missing: bool = False,
    ) -> str | None:
        """Ensure the topic belongs to the configured agent before reuse."""

        if not topic_id:
            return None

        if (
            self._conversation is not None
            and self._conversation.id == topic_id
            and self._conversation.agent_id == binding.agent_id
        ):
            return topic_id

        try:
            topic = self.client.get_topic(topic_id, workspace_id=binding.workspace_id)
        except ApiError as err:
            if self._is_not_found_error(err):
                if self._conversation is not None and self._conversation.id == topic_id:
                    self._conversation = None
                if allow_missing:
                    return None
                raise ValidationError(f"Topic not found: {topic_id}") from err
            raise
        if (
            topic.workspace_id
            and binding.workspace_id
            and topic.workspace_id != binding.workspace_id
        ):
            raise ValidationError(
                f"Topic {topic_id} does not belong to workspace {binding.workspace_id}"
            )
        if topic.agent_id and topic.agent_id != binding.agent_id:
            raise ValidationError(
                f"Topic {topic_id} does not belong to agent {binding.agent_id}"
            )
        return topic.id

    def _resolve_target_topic_id(self, topic_policy: str) -> str | None:
        if self._conversation is None:
            return None
        if topic_policy == "new":
            return None
        return self._conversation.id

    @staticmethod
    def _context_topic_id(context: Mapping[str, Any] | None) -> str | None:
        """Return one explicit topic id from the provided execution context."""

        if not isinstance(context, Mapping):
            return None

        context_topic_id = context.get("topicId") or context.get("topic_id")
        if isinstance(context_topic_id, str) and context_topic_id:
            return context_topic_id
        return None

    def _resolve_task_topic_id(
        self,
        topic_policy: str,
        *,
        context: Mapping[str, Any] | None,
        previous_response_id: str | None,
    ) -> str | None:
        """Resolve the topic id for task execution and chained task resumes."""

        if context_topic_id := self._context_topic_id(context):
            return context_topic_id

        if previous_response_id:
            if self._conversation is None or not self._conversation.id:
                raise ValidationError(
                    "Continuing a previous task requires an active topic; "
                    "switch to that topic first or pass context.topicId"
                )
            return self._conversation.id

        return self._resolve_target_topic_id(topic_policy)

    def _resolve_conversation_topic_id(
        self,
        topic_policy: str,
        *,
        conversation_id: str | None,
        context: Mapping[str, Any] | None,
    ) -> str | None:
        """Resolve the topic id for one conversational user turn."""
        if conversation_id and self._conversation is not None:
            if conversation_id == self._conversation.id:
                return conversation_id

        if context_topic_id := self._context_topic_id(context):
            return context_topic_id

        return self._resolve_target_topic_id(topic_policy)

    def _validate_message_for_topic(
        self,
        message_id: str,
        topic_id: str | None,
    ) -> None:
        """Ensure the requested parent message belongs to the selected topic."""

        if not topic_id:
            raise ValidationError(
                "Continuing a previous task requires a topic; "
                "switch to that topic first or pass context.topicId"
            )

        messages = self.client.get_topic_messages(
            topic_id,
            all_pages=True,
            workspace_id=self._require_agent_binding().workspace_id,
        )
        if any(_extract_message_id(message) == message_id for message in messages):
            return

        raise ValidationError(
            f"Message {message_id} does not belong to topic {topic_id}"
        )

    @staticmethod
    def _is_not_found_error(err: ApiError) -> bool:
        """Return whether the remote API rejected the request as not found."""

        if err.status_code == 404:
            return True

        message = err.message.lower()
        if "not found" in message:
            return True

        if isinstance(err.payload, Mapping):
            payload_message = err.payload.get("message") or err.payload.get("error")
            if (
                isinstance(payload_message, str)
                and "not found" in payload_message.lower()
            ):
                return True

        return False
