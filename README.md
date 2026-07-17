# LobeHub Home Assistant Integration

Home Assistant custom integration for LobeHub. Every configured LobeHub agent
is exposed as an independent Home Assistant conversation entity.

## Requirements

- Home Assistant 2026.7 or newer
- A reachable LobeHub server
- A LobeHub API key that can list and use the agents you intend to configure

## Install With HACS

1. In HACS, open **Integrations** and add this repository as a custom
   repository with the **Integration** category.
2. Download **LobeHub** from HACS and restart Home Assistant.
3. Open **Settings > Devices & services > Add integration**, select
   **LobeHub**, then enter the server base URL and API key.
4. Select one or more agents. Home Assistant creates one config entry and one
   conversation entity for every selected agent.

For a manual installation, copy `custom_components/lobehub` into the same path
inside your Home Assistant configuration directory and restart Home Assistant.

## Setup And Behavior

Create an API key in your LobeHub server, then enter it during the config flow.
The base URL is the root URL of that server, for example
`https://lobehub.example`; do not append `/api/v1`.

Each entry keeps its own agent binding and active LobeHub topic. In the entry's
options, choose whether new conversations reuse that topic or create a new one,
and optionally override the agent's model, provider, execution target, and
bound device. Removing an entry removes only its Home Assistant configuration;
it does not delete the LobeHub agent or its topics.

When more than one LobeHub agent is configured, target the intended LobeHub
conversation entity in automations and service calls. Calls without a target
are only valid when exactly one LobeHub entry is loaded.

## Services

The integration provides these targetable services:

- `lobehub.send_message`: send a message to the active topic.
- `lobehub.new_topic`: create and activate a topic.
- `lobehub.switch_topic`: activate an existing topic by ID.
- `lobehub.run_task`: run an ad-hoc task through the configured agent.
- `lobehub.list_tasks`, `lobehub.get_task`, and `lobehub.run_saved_task`:
  inspect and run saved LobeHub tasks.
- `lobehub.list_agents` and `lobehub.list_devices`: return IDs for automation
  configuration.
- `lobehub.update_agent_settings`: update model, provider, execution target,
  bound device, and topic policy for the configured agent.

Service field definitions and supported target selectors are available in the
Home Assistant automation editor and in
[`services.yaml`](custom_components/lobehub/services.yaml).

## Troubleshooting

- **Cannot connect or validate API key:** confirm the base URL is reachable
  from Home Assistant and the key is valid for that LobeHub server.
- **API key validates but no agents appear:** the API key must have permission
  to list agents in the selected LobeHub workspace.
- **A service reports that a target is required:** select one LobeHub
  conversation entity, especially when multiple agents are configured.
- **A configured agent disappears:** the integration removes the matching Home
  Assistant entry when LobeHub reports that the remote agent no longer exists.

## Development And Releases

Install [uv](https://docs.astral.sh/uv/), then run `uv sync --group dev
--locked`, `uv run python -m compileall -q custom_components tests`, `uv run
ruff check custom_components tests`, and `uv run pytest`. See
[RELEASE.md](RELEASE.md) for the versioning and release checklist.
