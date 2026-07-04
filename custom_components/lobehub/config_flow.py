"""Config flow for the LobeHub Home Assistant integration."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers import selector

from .const import (
    CONF_AGENT_ALLOW_TASK,
    CONF_AGENT_MODEL,
    CONF_AGENT_PROVIDER,
    CONF_AGENT_RUNTIME,
    CONF_AGENT_TITLE,
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_DEFAULT_RUNTIME,
    CONF_SELECTED_AGENTS,
    DEFAULT_RUNTIME,
    DOMAIN,
)
from .client import LobeHubClient
from .exceptions import ApiError, ValidationError
from .models import AgentBinding, AgentSummary, IntegrationConfig
from .runtime import binding_from_summary, binding_to_data, normalize_agent_ids


def _agent_selector(agent_summaries: Iterable[AgentSummary]) -> selector.SelectSelector:
    options = [
        {"value": summary.id, "label": summary.title or summary.id}
        for summary in agent_summaries
    ]
    return selector.SelectSelector(
        selector.SelectSelectorConfig(options=options, multiple=True)
    )


def _agent_config_schema(
    summary: Optional[AgentSummary],
    defaults: Optional[AgentBinding] = None,
) -> vol.Schema:
    defaults = defaults or AgentBinding(agent_id=summary.id if summary else "")
    title = defaults.title or (summary.title if summary else None) or ""
    model = defaults.model or (summary.model if summary else None) or ""
    provider = defaults.provider or (summary.provider if summary else None) or ""
    runtime = defaults.runtime or DEFAULT_RUNTIME
    return vol.Schema(
        {
            vol.Optional(CONF_AGENT_TITLE, default=title): str,
            vol.Optional(CONF_AGENT_MODEL, default=model): str,
            vol.Optional(CONF_AGENT_PROVIDER, default=provider): str,
            vol.Optional(CONF_AGENT_RUNTIME, default=runtime): str,
            vol.Optional(CONF_AGENT_ALLOW_TASK, default=defaults.allow_task): bool,
        }
    )


class _AgentConfigMixin:
    """Shared flow implementation for config and options flows."""

    _integration_config: Optional[IntegrationConfig]
    _discovered_agents: Dict[str, AgentSummary]
    _selected_agent_ids: List[str]
    _selected_bindings: Dict[str, AgentBinding]
    _current_index: int

    def _reset_agent_state(self) -> None:
        self._selected_agent_ids = []
        self._selected_bindings = {}
        self._current_index = 0

    def _selected_summary(self, agent_id: str) -> Optional[AgentSummary]:
        return self._discovered_agents.get(agent_id)

    def _current_agent_id(self) -> Optional[str]:
        if self._current_index >= len(self._selected_agent_ids):
            return None
        return self._selected_agent_ids[self._current_index]

    def _next_agent_step(self, agent_id: str, user_input: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        summary = self._selected_summary(agent_id)
        defaults = self._selected_bindings.get(agent_id)
        if user_input is None:
            return self.async_show_form(
                step_id="agent_config",
                data_schema=_agent_config_schema(summary, defaults),
                description_placeholders={"agent_id": agent_id},
            )

        summary = summary or AgentSummary(id=agent_id, title=agent_id)
        binding = binding_from_summary(summary)
        binding = AgentBinding(
            agent_id=agent_id,
            enabled=True,
            allow_task=bool(
                user_input.get(
                    CONF_AGENT_ALLOW_TASK,
                    defaults.allow_task if defaults is not None else binding.allow_task,
                )
            ),
            model=(user_input.get(CONF_AGENT_MODEL) or binding.model or None),
            provider=(user_input.get(CONF_AGENT_PROVIDER) or binding.provider or None),
            runtime=str(
                user_input.get(
                    CONF_AGENT_RUNTIME,
                    defaults.runtime if defaults is not None else binding.runtime,
                )
                or DEFAULT_RUNTIME
            ),
            title=(user_input.get(CONF_AGENT_TITLE) or summary.title or agent_id),
            raw=dict(summary.raw),
        )
        self._selected_bindings[agent_id] = binding
        self._current_index += 1
        return self._advance_agent_flow()

    def _advance_agent_flow(self) -> Dict[str, Any]:
        next_agent = self._current_agent_id()
        if next_agent is not None:
            return self._next_agent_step(next_agent, None)
        return self._finish_agent_flow()

    def _finish_agent_flow(self) -> Dict[str, Any]:
        return self._create_entry()

    def _create_entry(self) -> Dict[str, Any]:
        raise NotImplementedError

    async def async_step_agent_config(self, user_input: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        current_agent_id = self._current_agent_id()
        if current_agent_id is None:
            return self._finish_agent_flow()
        return self._next_agent_step(current_agent_id, user_input)

    def _begin_agent_selection(self) -> Dict[str, Any]:
        options = self._agent_selection_schema()
        return self.async_show_form(step_id="select_agents", data_schema=options)

    def _agent_selection_schema(self) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(CONF_SELECTED_AGENTS, default=self._selected_agent_ids): _agent_selector(
                    self._discovered_agents.values()
                ),
            }
        )

    def _handle_agent_selection(self, user_input: Dict[str, Any]) -> Dict[str, Any]:
        previous_bindings = dict(self._selected_bindings)
        selected = normalize_agent_ids(user_input.get(CONF_SELECTED_AGENTS))
        self._selected_agent_ids = selected
        self._selected_bindings = {agent_id: previous_bindings[agent_id] for agent_id in selected if agent_id in previous_bindings}
        self._current_index = 0
        if not selected:
            return self._create_entry()
        return self._advance_agent_flow()


class LobeHubConfigFlow(_AgentConfigMixin, config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for first-time setup."""

    VERSION = 1

    def __init__(self) -> None:
        self._integration_config = None
        self._discovered_agents = {}
        self._selected_agent_ids = []
        self._selected_bindings = {}
        self._current_index = 0

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_BASE_URL): str,
                        vol.Required(CONF_API_KEY): str,
                        vol.Optional(CONF_DEFAULT_RUNTIME, default=DEFAULT_RUNTIME): str,
                    }
                ),
            )

        try:
            config = IntegrationConfig(
                api_key=user_input[CONF_API_KEY],
                base_url=user_input[CONF_BASE_URL],
                default_runtime=user_input.get(CONF_DEFAULT_RUNTIME, DEFAULT_RUNTIME),
            )
            client = LobeHubClient(config)
            client.validate_auth()
            discovered_agents = list(client.list_agents())
        except (ApiError, ValidationError, KeyError) as exc:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema(
                    {
                        vol.Required(CONF_BASE_URL): str,
                        vol.Required(CONF_API_KEY): str,
                        vol.Optional(CONF_DEFAULT_RUNTIME, default=DEFAULT_RUNTIME): str,
                    }
                ),
                errors={"base": "auth"},
            )

        self._integration_config = config
        self._discovered_agents = {agent.id: agent for agent in discovered_agents}
        return self._begin_agent_selection()

    async def async_step_select_agents(self, user_input: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if user_input is None:
            return self._begin_agent_selection()
        return self._handle_agent_selection(user_input)

    def _create_entry(self) -> Dict[str, Any]:
        assert self._integration_config is not None
        data = {
            CONF_BASE_URL: self._integration_config.base_url,
            CONF_API_KEY: self._integration_config.api_key,
            CONF_DEFAULT_RUNTIME: self._integration_config.default_runtime,
            CONF_SELECTED_AGENTS: [binding_to_data(binding) for binding in self._selected_bindings.values()],
        }
        return self.async_create_entry(title="LobeHub", data=data)


class LobeHubOptionsFlow(_AgentConfigMixin, config_entries.OptionsFlow):
    """Options flow that can reconfigure selected agents after setup."""

    def __init__(self, config_entry: Any) -> None:
        self.config_entry = config_entry
        self._integration_config = None
        self._discovered_agents = {}
        existing_data = config_entry.options.get(CONF_SELECTED_AGENTS) or config_entry.data.get(CONF_SELECTED_AGENTS) or []
        self._selected_bindings = {
            binding.agent_id: binding_from_data(binding) for binding in existing_data if binding.get("agent_id")
        }
        self._selected_agent_ids = list(self._selected_bindings)
        self._current_index = 0

    async def async_step_init(self, user_input: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if self._integration_config is None:
            self._integration_config = IntegrationConfig(
                api_key=self.config_entry.data[CONF_API_KEY],
                base_url=self.config_entry.data[CONF_BASE_URL],
                default_runtime=self.config_entry.data.get(CONF_DEFAULT_RUNTIME, DEFAULT_RUNTIME),
            )
            client = LobeHubClient(self._integration_config)
            self._discovered_agents = {agent.id: agent for agent in client.list_agents()}

        if user_input is None:
            return self._begin_agent_selection()
        return self._handle_agent_selection(user_input)

    def _create_entry(self) -> Dict[str, Any]:
        data = dict(self.config_entry.options)
        data[CONF_SELECTED_AGENTS] = [binding_to_data(binding) for binding in self._selected_bindings.values()]
        return self.async_create_entry(title="", data=data)


async def async_get_options_flow(config_entry: Any) -> LobeHubOptionsFlow:
    """Return the options flow handler."""

    return LobeHubOptionsFlow(config_entry)
