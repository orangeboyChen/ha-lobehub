"""Constants for the LobeHub Home Assistant integration."""

from __future__ import annotations

DOMAIN = "lobehub"
NAME = "LobeHub"

DEFAULT_BASE_URL = "https://app.lobehub.com"
DEFAULT_RUNTIME = "gateway"

CONF_BASE_URL = "base_url"
CONF_API_KEY = "api_key"
CONF_DEFAULT_RUNTIME = "default_runtime"
CONF_SELECTED_AGENTS = "selected_agents"
CONF_AGENT_CONFIGS = "agent_configs"
CONF_AGENT_ID = "agent_id"
CONF_AGENT_TITLE = "agent_title"
CONF_AGENT_MODEL = "agent_model"
CONF_AGENT_PROVIDER = "agent_provider"
CONF_AGENT_RUNTIME = "agent_runtime"
CONF_AGENT_ALLOW_TASK = "agent_allow_task"
CONF_AGENT_ENABLED = "agent_enabled"
CONF_CONVERSATION_ID = "conversation_id"
CONF_ACTIVE_TOPIC_ID = "active_topic_id"
CONF_TOPIC_TITLE = "topic_title"
CONF_TOPIC_ID = "topic_id"
CONF_MESSAGE = "message"
CONF_INSTRUCTION = "instruction"
CONF_CONTEXT = "context"
CONF_CLIENT_ID = "client_id"
CONF_MODEL = "model"
CONF_PROVIDER = "provider"
CONF_RUNTIME = "runtime"
CONF_PREVIOUS_RESPONSE_ID = "previous_response_id"
CONF_STREAM = "stream"
CONF_TOOLS = "tools"
CONF_TITLE = "title"
CONF_ALLOW_TASK = "allow_task"
CONF_ENABLED = "enabled"

SERVICE_SEND_MESSAGE = "send_message"
SERVICE_NEW_TOPIC = "new_topic"
SERVICE_SWITCH_TOPIC = "switch_topic"
SERVICE_RUN_TASK = "run_task"

PLATFORMS = ("sensor",)

