# LobeHub Home Assistant Integration

This repository provides a Home Assistant custom integration for LobeHub.

## Installation

Install the repository through HACS as a custom integration, then add the
`LobeHub` integration from Home Assistant.

## Features

- Configure the LobeHub API key in Home Assistant setup
- Select one or more agents
- Configure per-agent model, provider, runtime, and task availability
- Create and switch topics per agent
- Trigger agent tasks through Home Assistant services

## Services

- `lobehub.send_message`
- `lobehub.new_topic`
- `lobehub.switch_topic`
- `lobehub.run_task`

## Notes

- API keys are entered by the user during setup
- The default task runtime is `gateway`
- Conversation state is tracked per agent, with one active topic per agent

