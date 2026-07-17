"""Minimal Home Assistant core stubs for local testing."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Dict


@dataclass
class ServiceCall:
    """Simple service call container."""

    data: Dict[str, Any]


class SupportsResponse(Enum):
    """Response support marker accepted by service registration."""

    ONLY = "only"
    OPTIONAL = "optional"


class ServiceRegistry:
    """Tiny service registry used in tests."""

    def __init__(self) -> None:
        self._services: Dict[tuple[str, str], Callable[[ServiceCall], Awaitable[Any]]] = {}

    def async_register(self, domain: str, service: str, handler, schema=None) -> None:
        self._services[(domain, service)] = handler

    async def async_call(self, domain: str, service: str, data: Dict[str, Any]) -> Any:
        handler = self._services[(domain, service)]
        result = handler(ServiceCall(data=data))
        if asyncio.iscoroutine(result):
            return await result
        return result


class ConfigEntriesManager:
    """Stub config entries manager."""

    def __init__(self) -> None:
        self.entries = []

    async def async_forward_entry_setups(self, entry, platforms) -> bool:
        return True

    async def async_unload_platforms(self, entry, platforms) -> bool:
        return True

    def async_update_entry(self, entry, **kwargs) -> None:
        for key, value in kwargs.items():
            setattr(entry, key, value)

    def async_entries(self, domain=None):
        return [entry for entry in self.entries if domain is None or entry.domain == domain]

    async def async_add(self, entry) -> None:
        self.entries.append(entry)


class HomeAssistant:
    """Minimal Home Assistant object for tests."""

    def __init__(self) -> None:
        self.data: Dict[str, Any] = {}
        self.services = ServiceRegistry()
        self.config_entries = ConfigEntriesManager()

    async def async_add_executor_job(self, func, *args):
        return func(*args)

    def async_create_task(self, coro):
        return asyncio.create_task(coro)


def callback(func):
    """Decorator used by Home Assistant for synchronous callbacks."""

    return func
