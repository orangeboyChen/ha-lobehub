"""Config flow for the LobeHub Home Assistant integration."""

from __future__ import annotations

from dataclasses import replace
from functools import partial
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
from homeassistant.core import callback
from homeassistant.helpers import selector

from .client import LobeHubClient
from .const import (
    CONF_AGENT_IDS,
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_BOUND_DEVICE_ID,
    CONF_CONVERSATION,
    CONF_DEFAULT_RUNTIME,
    CONF_MODEL,
    CONF_PROVIDER,
    CONF_RUNTIME,
    CONF_SELECTED_AGENT,
    CONF_TOPIC_POLICY,
    DEFAULT_RUNTIME,
    DOMAIN,
    EXECUTION_TARGETS,
    TOPIC_POLICY_NEW,
    TOPIC_POLICY_REUSE,
)
from .exceptions import ApiError
from .models import (
    AgentBinding,
    AgentSummary,
    IntegrationConfig,
    RemoteOptions,
    binding_from_data,
    binding_from_summary,
    binding_to_data,
)

_LOGGER = logging.getLogger(__name__)

USER_STEP_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_BASE_URL): str,
        vol.Required(CONF_API_KEY): str,
    }
)


def _single_select(options: list[selector.SelectOptionDict]) -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options,
            mode=selector.SelectSelectorMode.DROPDOWN,
            sort=True,
        )
    )


def _agent_list_error_key(err: ApiError) -> str:
    """Map agent list API failures to a config-flow error key."""

    message = err.message.lower()
    if "permission" in message and "agent list" in message:
        return "agent_list_forbidden"
    return "cannot_connect"


def _validate_and_list_agents(client: LobeHubClient) -> list[AgentSummary]:
    """Validate credentials and fetch visible agents."""

    client.validate_auth()
    return list(client.list_agents())


def _topic_policy_selector() -> selector.SelectSelector:
    return _single_select(
        [
            selector.SelectOptionDict(
                value=TOPIC_POLICY_REUSE,
                label="Reuse current topic",
            ),
            selector.SelectOptionDict(
                value=TOPIC_POLICY_NEW,
                label="Create a new topic each time",
            ),
        ]
    )


def _optional_select_selector(
    options: list[selector.SelectOptionDict],
    *,
    allow_custom: bool = False,
) -> selector.SelectSelector:
    """Return a selector for optional values."""

    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options,
            mode=selector.SelectSelectorMode.DROPDOWN,
            sort=True,
            multiple=False,
            custom_value=allow_custom,
        )
    )


def _remote_options_for_binding(
    client: LobeHubClient, binding: AgentBinding
) -> tuple[RemoteOptions, AgentBinding]:
    """Load remote options plus a fresh binding for the configured agent."""

    options = client.discover_remote_options(workspace_id=binding.workspace_id)
    summary = client.get_agent(binding.agent_id, workspace_id=binding.workspace_id)
    refreshed = binding_from_summary(
        summary,
        runtime=str(
            summary.raw.get("agencyConfig", {}).get("executionTarget")
            or summary.raw.get("executionTarget")
            or binding.runtime
        ),
    )
    refreshed.enabled = binding.enabled
    refreshed.topic_policy = binding.topic_policy
    if binding.model:
        refreshed.model = binding.model
    if binding.provider:
        refreshed.provider = binding.provider
    if binding.bound_device_id:
        refreshed.bound_device_id = binding.bound_device_id
    return options, refreshed


class LobeHubConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for first-time setup."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> LobeHubOptionsFlow:
        """Return the options flow handler."""

        return LobeHubOptionsFlow(config_entry)

    def __init__(self) -> None:
        self._integration_config: IntegrationConfig | None = None
        self._discovered_agents: dict[str, AgentSummary] = {}
        self._pending_additional_bindings: list[AgentBinding] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate credentials before the agent selection step."""

        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=USER_STEP_SCHEMA)

        config = IntegrationConfig(
            api_key=user_input[CONF_API_KEY],
            base_url=user_input[CONF_BASE_URL],
            default_runtime=DEFAULT_RUNTIME,
        )
        client = LobeHubClient(config)

        try:
            discovered_agents = await self.hass.async_add_executor_job(
                _validate_and_list_agents, client
            )
        except ApiError as err:
            _LOGGER.error(
                "LobeHub config flow validation failed for base_url=%s with status=%s payload=%r",
                config.base_url,
                err.status_code,
                err.payload,
            )
            return self.async_show_form(
                step_id="user",
                data_schema=USER_STEP_SCHEMA,
                errors={"base": _agent_list_error_key(err)},
            )
        except Exception:
            _LOGGER.exception(
                "Unexpected LobeHub config flow failure for base_url=%s",
                config.base_url,
            )
            return self.async_show_form(
                step_id="user",
                data_schema=USER_STEP_SCHEMA,
                errors={"base": "cannot_connect"},
            )

        self._integration_config = config
        self._discovered_agents = {agent.id: agent for agent in discovered_agents}
        return await self.async_step_select_agent()

    async def async_step_select_agent(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose one or more LobeHub agents for config entries."""

        if user_input is None:
            return self.async_show_form(
                step_id="select_agent",
                data_schema=self._select_agent_schema(),
            )

        assert self._integration_config is not None

        selected_agent_ids = [
            agent_id
            for agent_id in user_input[CONF_AGENT_IDS]
            if agent_id in self._discovered_agents
        ]
        if not selected_agent_ids:
            return self.async_show_form(
                step_id="select_agent",
                data_schema=self._select_agent_schema(),
                errors={CONF_AGENT_IDS: "required"},
            )

        topic_policy = user_input[CONF_TOPIC_POLICY]
        selected_bindings: list[AgentBinding] = []
        primary_binding: AgentBinding | None = None
        skipped_agent_ids: set[str] = set()
        for agent_id in selected_agent_ids:
            summary = self._discovered_agents[agent_id]
            binding = binding_from_summary(summary)
            binding.topic_policy = topic_policy
            unique_id = f"{self._integration_config.base_url}::{binding.agent_id}"
            existing_entries = self.hass.config_entries.async_entries(DOMAIN)
            if any(entry.unique_id == unique_id for entry in existing_entries):
                skipped_agent_ids.add(agent_id)
                continue
            if primary_binding is None:
                primary_binding = binding
            else:
                selected_bindings.append(binding)

        if primary_binding is None:
            return self.async_abort(reason="already_configured")

        await self.async_set_unique_id(
            f"{self._integration_config.base_url}::{primary_binding.agent_id}"
        )
        self._abort_if_unique_id_configured()
        self._pending_additional_bindings = selected_bindings
        if skipped_agent_ids:
            _LOGGER.info(
                "Skipping already configured LobeHub agents during batch add: %s",
                ", ".join(sorted(skipped_agent_ids)),
            )

        return self.async_create_entry(
            title=primary_binding.title or "LobeHub",
            data={
                CONF_BASE_URL: self._integration_config.base_url,
                CONF_API_KEY: self._integration_config.api_key,
                CONF_DEFAULT_RUNTIME: self._integration_config.default_runtime,
                CONF_SELECTED_AGENT: binding_to_data(primary_binding),
            },
        )

    async def async_on_create_entry(self, result: ConfigFlowResult) -> ConfigFlowResult:
        """Create additional selected agent entries after the primary entry."""

        if not self._pending_additional_bindings:
            return result

        created_entry = result["result"]
        assert isinstance(created_entry, ConfigEntry)
        assert self._integration_config is not None

        for binding in self._pending_additional_bindings:
            unique_id = f"{self._integration_config.base_url}::{binding.agent_id}"
            if any(
                entry.unique_id == unique_id
                for entry in self.hass.config_entries.async_entries(DOMAIN)
            ):
                continue
            await self.hass.config_entries.async_add(
                ConfigEntry(
                    version=self.VERSION,
                    minor_version=self.MINOR_VERSION,
                    domain=DOMAIN,
                    title=binding.title or "LobeHub",
                    data={
                        CONF_BASE_URL: self._integration_config.base_url,
                        CONF_API_KEY: self._integration_config.api_key,
                        CONF_DEFAULT_RUNTIME: self._integration_config.default_runtime,
                        CONF_SELECTED_AGENT: binding_to_data(binding),
                    },
                    options={},
                    source=created_entry.source,
                    unique_id=unique_id,
                    discovery_keys=created_entry.discovery_keys,
                    subentries_data=(),
                )
            )

        self._pending_additional_bindings = []
        return result

    def _select_agent_schema(self) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(CONF_AGENT_IDS): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(
                                value=agent_id,
                                label=summary.title or agent_id,
                            )
                            for agent_id, summary in sorted(
                                self._discovered_agents.items(),
                                key=lambda item: item[1].title or item[0],
                            )
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        multiple=True,
                        sort=True,
                    )
                ),
                vol.Required(
                    CONF_TOPIC_POLICY,
                    default=TOPIC_POLICY_REUSE,
                ): _topic_policy_selector(),
            }
        )


class LobeHubOptionsFlow(config_entries.OptionsFlow):
    """Options flow for per-agent conversation behavior."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry
        self._remote_options: RemoteOptions | None = None
        self._binding: AgentBinding | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure the topic policy, model and execution target."""

        binding = self._binding or self._current_binding()
        if self._remote_options is None:
            client = LobeHubClient(self._integration_config())
            try:
                remote_options, binding = await self.hass.async_add_executor_job(
                    _remote_options_for_binding,
                    client,
                    binding,
                )
            except Exception:
                _LOGGER.exception(
                    "Failed to load remote LobeHub options for entry %s",
                    self._config_entry.entry_id,
                )
                remote_options = RemoteOptions()
            self._remote_options = remote_options
            self._binding = binding

        if user_input is None:
            return self.async_show_form(
                step_id="init",
                data_schema=self._options_schema(binding, self._remote_options),
            )

        runtime = str(user_input[CONF_RUNTIME] or binding.runtime)
        bound_device_id = user_input.get(CONF_BOUND_DEVICE_ID) or None
        if runtime == "device" and not bound_device_id:
            return self.async_show_form(
                step_id="init",
                data_schema=self._options_schema(binding, self._remote_options),
                errors={CONF_BOUND_DEVICE_ID: "required"},
            )

        updated_binding = AgentBinding(
            agent_id=binding.agent_id,
            enabled=binding.enabled,
            model=str(user_input.get(CONF_MODEL) or "").strip() or None,
            provider=str(user_input.get(CONF_PROVIDER) or "").strip() or None,
            runtime=runtime,
            bound_device_id=bound_device_id,
            title=binding.title,
            topic_policy=str(user_input[CONF_TOPIC_POLICY] or binding.topic_policy),
            workspace_id=binding.workspace_id,
            raw=dict(binding.raw),
        )
        if hasattr(self._config_entry, "runtime_data"):
            runtime_data = self._config_entry.runtime_data
            try:
                updated_binding = await self.hass.async_add_executor_job(
                    partial(
                        runtime_data.update_agent_settings,
                        model=updated_binding.model,
                        provider=updated_binding.provider,
                        runtime=updated_binding.runtime,
                        bound_device_id=updated_binding.bound_device_id,
                        topic_policy=updated_binding.topic_policy,
                    )
                )
            except ApiError:
                _LOGGER.exception(
                    "Failed to update remote LobeHub agent settings for entry %s",
                    self._config_entry.entry_id,
                )
                return self.async_show_form(
                    step_id="init",
                    data_schema=self._options_schema(binding, self._remote_options),
                    errors={"base": "cannot_connect"},
                )
            if runtime_data.conversation is not None:
                runtime_data.conversation = replace(
                    runtime_data.conversation,
                    topic_policy=updated_binding.topic_policy,
                    model=updated_binding.model,
                    provider=updated_binding.provider,
                    runtime=updated_binding.runtime,
                )
                runtime_data.integration._conversation = runtime_data.conversation
            self._binding = updated_binding
        updated_options = dict(self._config_entry.options)
        updated_options[CONF_SELECTED_AGENT] = binding_to_data(updated_binding)
        if hasattr(self._config_entry, "runtime_data"):
            updated_options[CONF_CONVERSATION] = (
                self._config_entry.runtime_data.dump_conversation()
            )
        else:
            updated_options[CONF_CONVERSATION] = self._config_entry.options.get(
                CONF_CONVERSATION
            )
        return self.async_create_entry(title="", data=updated_options)

    def _current_binding(self) -> AgentBinding:
        binding_data = self._config_entry.options.get(
            CONF_SELECTED_AGENT
        ) or self._config_entry.data.get(CONF_SELECTED_AGENT)
        if isinstance(binding_data, dict):
            return binding_from_data(binding_data)
        raise ValueError("No LobeHub agent is configured")

    def _integration_config(self) -> IntegrationConfig:
        data = self._config_entry.data
        return IntegrationConfig(
            api_key=data[CONF_API_KEY],
            base_url=data[CONF_BASE_URL],
            default_runtime=data.get(CONF_DEFAULT_RUNTIME, DEFAULT_RUNTIME),
        )

    def _options_schema(
        self,
        binding: AgentBinding,
        remote_options: RemoteOptions | None,
    ) -> vol.Schema:
        remote_options = remote_options or RemoteOptions()
        model_options = [
            selector.SelectOptionDict(
                value=model, label=remote_options.model_labels.get(model, model)
            )
            for model in remote_options.models
        ]
        provider_options = [
            selector.SelectOptionDict(value=provider, label=provider)
            for provider in remote_options.providers
        ]
        runtime_options = [
            selector.SelectOptionDict(value=runtime, label=runtime)
            for runtime in remote_options.runtimes
        ] or [
            selector.SelectOptionDict(value=runtime, label=runtime)
            for runtime in EXECUTION_TARGETS
        ]
        device_options = [
            selector.SelectOptionDict(
                value=device["device_id"],
                label=(
                    f"{device['label']} ({device['device_id']})"
                    + ("" if device.get("online") else " [offline]")
                ),
            )
            for device in remote_options.devices
            if isinstance(device.get("device_id"), str)
        ]

        return vol.Schema(
            {
                vol.Required(
                    CONF_TOPIC_POLICY,
                    default=binding.topic_policy,
                ): _topic_policy_selector(),
                vol.Required(
                    CONF_MODEL,
                    default=binding.model or "",
                ): _optional_select_selector(model_options, allow_custom=True),
                vol.Required(
                    CONF_PROVIDER,
                    default=binding.provider or "",
                ): _optional_select_selector(provider_options, allow_custom=True),
                vol.Required(
                    CONF_RUNTIME,
                    default=binding.runtime or DEFAULT_RUNTIME,
                ): _optional_select_selector(runtime_options),
                vol.Required(
                    CONF_BOUND_DEVICE_ID,
                    default=binding.bound_device_id or "",
                ): _optional_select_selector(device_options, allow_custom=True),
            }
        )
