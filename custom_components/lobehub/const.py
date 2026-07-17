"""Constants for the LobeHub Home Assistant integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "lobehub"
NAME = "LobeHub"

DEFAULT_BASE_URL = "https://app.lobehub.com"
DEFAULT_RUNTIME = "auto"
SYNC_INTERVAL = timedelta(minutes=2)
EXECUTION_TARGETS = ("none", "auto", "local", "device", "sandbox")

CONF_BASE_URL = "base_url"
CONF_API_KEY = "api_key"
CONF_DEFAULT_RUNTIME = "default_runtime"
CONF_SELECTED_AGENT = "selected_agent"
CONF_CONVERSATION = "conversation"
CONF_AGENT_ID = "agent_id"
CONF_AGENT_IDS = "agent_ids"
CONF_MODEL = "model"
CONF_PROVIDER = "provider"
CONF_RUNTIME = "runtime"
CONF_BOUND_DEVICE_ID = "bound_device_id"
CONF_TOPIC_POLICY = "topic_policy"
CONF_TOPIC_TITLE = "topic_title"
CONF_TOPIC_ID = "topic_id"
CONF_MESSAGE = "message"
CONF_INSTRUCTION = "instruction"
CONF_CONTEXT = "context"
CONF_PREVIOUS_RESPONSE_ID = "previous_response_id"
CONF_TASK = "task"
CONF_PROMPT = "prompt"
CONF_CONTINUE_TOPIC_ID = "continue_topic_id"
CONF_LIMIT = "limit"
CONF_OFFSET = "offset"
CONF_STATUSES = "statuses"
CONF_ASSIGNEE_AGENT_ID = "assignee_agent_id"
CONF_PARENT_TASK = "parent_task"

TOPIC_POLICY_REUSE = "reuse"
TOPIC_POLICY_NEW = "new"

SERVICE_SEND_MESSAGE = "send_message"
SERVICE_NEW_TOPIC = "new_topic"
SERVICE_SWITCH_TOPIC = "switch_topic"
SERVICE_RUN_TASK = "run_task"
SERVICE_LIST_TASKS = "list_tasks"
SERVICE_GET_TASK = "get_task"
SERVICE_RUN_SAVED_TASK = "run_saved_task"
SERVICE_LIST_AGENTS = "list_agents"
SERVICE_LIST_DEVICES = "list_devices"
SERVICE_UPDATE_AGENT_SETTINGS = "update_agent_settings"

PLATFORMS = ("conversation",)
