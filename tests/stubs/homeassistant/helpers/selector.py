"""Minimal selector stubs for tests."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, List

import voluptuous as vol


@dataclass
class SelectSelectorConfig:
    """Selector configuration stub."""

    options: List[Any]
    multiple: bool = False
    mode: object | None = None
    sort: bool = False
    custom_value: bool = False


class SelectSelectorMode(Enum):
    """Selector display mode accepted by config-flow schemas."""

    DROPDOWN = "dropdown"


class SelectOptionDict(dict):
    """Dict-shaped selector option."""

    def __init__(self, *, value: str, label: str) -> None:
        super().__init__(value=value, label=label)


@dataclass
class SelectSelector:
    """Selector stub."""

    config: SelectSelectorConfig

    def __voluptuous_compile__(self, schema):
        allowed = []
        for option in self.config.options:
            if isinstance(option, dict) and "value" in option:
                allowed.append(option["value"])
            else:
                allowed.append(option)

        def validate(path, value):
            if self.config.multiple:
                if value is None:
                    result = []
                elif isinstance(value, str):
                    result = [item.strip() for item in value.split(",") if item.strip()]
                elif isinstance(value, (list, tuple, set)):
                    result = list(value)
                else:
                    raise vol.Invalid("Expected a list of selected values")
                values = [str(item) for item in result]
            else:
                if isinstance(value, (list, tuple, set)):
                    if len(value) != 1:
                        raise vol.Invalid("Expected a single selected value")
                    value = next(iter(value))
                values = str(value)
            if allowed:
                if self.config.multiple:
                    missing = [item for item in values if item not in allowed]
                    if missing:
                        raise vol.Invalid(f"Invalid selector values: {missing}")
                elif values not in allowed:
                    raise vol.Invalid(f"Invalid selector value: {values}")
            return values

        return validate
