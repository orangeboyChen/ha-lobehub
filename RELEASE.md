# Release Guide

This repository is a HACS custom integration. Releases are published from Git
tags; HACS installs the tagged repository contents under `custom_components`.

Before creating a release:

1. Update the version in `custom_components/lobehub/manifest.json` and
   `pyproject.toml` together.
2. Install [uv](https://docs.astral.sh/uv/) and run `uv sync --group dev
   --locked`.
3. Run `uv run python -m compileall -q custom_components tests`, `uv run ruff
   check custom_components tests`, and `uv run pytest`.
4. Confirm the HACS workflow is green and review the Home Assistant config flow
   on a supported Home Assistant version.
5. Create an annotated semantic-version tag such as `v0.2.0`, push it, and
   publish a GitHub release with user-facing changes and upgrade notes.

Do not put API keys, LobeHub URLs containing credentials, or Home Assistant
configuration files in a release artifact.
