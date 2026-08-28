"""Fail tests that attempt to use the network.

Scanner's trust contracts must be deterministic: tests model provider, storage and
HTTP behaviour locally, never through a live endpoint. The guard is installed
before test collection and records every forbidden attempt, including attempts a
production path catches and degrades instead of surfacing.
"""

from __future__ import annotations

import socket
import traceback

import pytest


class NetworkAccessBlocked(RuntimeError):
    """Raised whenever a test attempts DNS resolution or a socket connection."""


_ORIGINAL_SOCKET = socket.socket
_ORIGINAL_CREATE_CONNECTION = socket.create_connection
_ORIGINAL_GETADDRINFO = socket.getaddrinfo
_NETWORK_ATTEMPTS: list[str] = []


def _blocked(operation: str, target: object) -> None:
    message = f"Network access blocked during tests: {operation}({target!r})"
    caller = "".join(traceback.format_stack(limit=20)[:-2]).strip()
    _NETWORK_ATTEMPTS.append(f"{message}\n{caller}")
    raise NetworkAccessBlocked(message)


class _NoNetworkSocket(_ORIGINAL_SOCKET):
    def connect(self, address):  # type: ignore[no-untyped-def]
        _blocked("socket.connect", address)

    def connect_ex(self, address):  # type: ignore[no-untyped-def]
        _blocked("socket.connect_ex", address)


def _blocked_create_connection(address, *args, **kwargs):  # type: ignore[no-untyped-def]
    _blocked("socket.create_connection", address)


def _blocked_getaddrinfo(host, *args, **kwargs):  # type: ignore[no-untyped-def]
    _blocked("socket.getaddrinfo", host)


def pytest_sessionstart(session: pytest.Session) -> None:
    _NETWORK_ATTEMPTS.clear()
    socket.socket = _NoNetworkSocket
    socket.create_connection = _blocked_create_connection
    socket.getaddrinfo = _blocked_getaddrinfo


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    socket.socket = _ORIGINAL_SOCKET
    socket.create_connection = _ORIGINAL_CREATE_CONNECTION
    socket.getaddrinfo = _ORIGINAL_GETADDRINFO
    if _NETWORK_ATTEMPTS:
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        if reporter:
            reporter.write_line("Forbidden network attempts:")
            for attempt in _NETWORK_ATTEMPTS:
                reporter.write_line(f"  - {attempt}")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
