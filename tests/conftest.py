"""Fail tests that attempt to use the network.

Scanner's trust contracts must be deterministic: tests model provider, storage and
HTTP behaviour locally, never through a live endpoint. The guard is installed
before test collection and records every forbidden attempt, including attempts a
production path catches and degrades instead of surfacing.
"""

from __future__ import annotations

import os
import socket
from urllib.parse import urlparse

import pytest


class NetworkAccessBlocked(RuntimeError):
    """Raised whenever a test attempts DNS resolution or a socket connection."""


_ORIGINAL_SOCKET = socket.socket
_ORIGINAL_CREATE_CONNECTION = socket.create_connection
_ORIGINAL_GETADDRINFO = socket.getaddrinfo
_NETWORK_ATTEMPTS: list[str] = []


def _blocked(operation: str, target: object) -> None:
    message = f"Network access blocked during tests: {operation}({target!r})"
    _NETWORK_ATTEMPTS.append(message)
    raise NetworkAccessBlocked(message)


def _loopback_database_endpoint() -> tuple[str, int] | None:
    """Return the explicitly configured disposable CI database endpoint only."""
    if os.environ.get("SCANNER_ALLOW_LOOPBACK_TEST_DATABASE") != "true":
        return None
    raw_url = os.environ.get("SCANNER_TEST_DATABASE_URL") or ""
    parsed = urlparse(raw_url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        return None
    host = (parsed.hostname or "").lower()
    port = parsed.port or 5432
    if host != "127.0.0.1" or port != 5432:
        return None
    return host, port


def _is_allowed_loopback_database(address: object) -> bool:
    endpoint = _loopback_database_endpoint()
    if endpoint is None:
        return False
    host, port = endpoint
    if isinstance(address, str):
        return address == host
    if isinstance(address, tuple) and len(address) >= 2:
        return address[0] == host and address[1] == port
    return False


class _NoNetworkSocket(_ORIGINAL_SOCKET):
    def connect(self, address):  # type: ignore[no-untyped-def]
        if _is_allowed_loopback_database(address):
            return super().connect(address)
        _blocked("socket.connect", address)

    def connect_ex(self, address):  # type: ignore[no-untyped-def]
        if _is_allowed_loopback_database(address):
            return super().connect_ex(address)
        _blocked("socket.connect_ex", address)


def _blocked_create_connection(address, *args, **kwargs):  # type: ignore[no-untyped-def]
    if _is_allowed_loopback_database(address):
        return _ORIGINAL_CREATE_CONNECTION(address, *args, **kwargs)
    _blocked("socket.create_connection", address)


def _blocked_getaddrinfo(host, *args, **kwargs):  # type: ignore[no-untyped-def]
    if _is_allowed_loopback_database(host):
        return _ORIGINAL_GETADDRINFO(host, *args, **kwargs)
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
