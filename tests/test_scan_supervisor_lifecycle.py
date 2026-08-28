"""Phase 2 lifecycle contracts for the disabled Scan Supervisor."""

from __future__ import annotations

import ast
import os
import sys

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from scan_supervisor import (  # noqa: E402
    ScanSupervisorLifecycle,
    ScanSupervisorSettings,
    StageExecutionResult,
    SupervisorLifecycleStatus,
)


class NeverCalledExecutor:
    def __init__(self):
        self.calls = 0

    def execute(self, _lease):
        self.calls += 1
        return StageExecutionResult(
            output_hash="a" * 64,
            checkpoint_ref="checkpoint://never",
            gate_result={"status": "passed"},
            next_stage=None,
        )


def _settings(*, enabled: bool, schema_ready: bool) -> ScanSupervisorSettings:
    return ScanSupervisorSettings(
        database_url="postgresql://scanner:test@localhost/scanner",
        enabled=enabled,
        schema_ready=schema_ready,
    )


def test_disabled_lifecycle_is_inert_without_database_or_executor_work():
    executor = NeverCalledExecutor()

    report = ScanSupervisorLifecycle(
        _settings(enabled=False, schema_ready=False),
        executor=executor,
    ).start()

    assert report.status is SupervisorLifecycleStatus.DISABLED
    assert executor.calls == 0


def test_enabled_lifecycle_refuses_work_until_schema_is_explicitly_armed():
    executor = NeverCalledExecutor()

    report = ScanSupervisorLifecycle(
        _settings(enabled=True, schema_ready=False),
        executor=executor,
    ).start()

    assert report.status is SupervisorLifecycleStatus.BLOCKED_SCHEMA_NOT_READY
    assert "SCAN_SUPERVISOR_SCHEMA_READY" in report.reason
    assert executor.calls == 0


def test_schema_ready_lifecycle_requires_phase_three_executor_registration():
    report = ScanSupervisorLifecycle(
        _settings(enabled=True, schema_ready=True),
    ).start()

    assert report.status is SupervisorLifecycleStatus.BLOCKED_STAGE_EXECUTOR_NOT_REGISTERED


def test_phase_two_does_not_start_work_even_when_an_executor_is_supplied():
    executor = NeverCalledExecutor()

    report = ScanSupervisorLifecycle(
        _settings(enabled=True, schema_ready=True),
        executor=executor,
    ).start()

    assert report.status is SupervisorLifecycleStatus.BLOCKED_PHASE_3_CHECKPOINTING_REQUIRED
    assert executor.calls == 0


def test_fastapi_registers_disabled_supervisor_lifecycle_hooks():
    main_path = os.path.join(_BACKEND, "main.py")
    with open(main_path, encoding="utf-8") as main_file:
        tree = ast.parse(main_file.read(), filename=main_path)

    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    for name in ("start_scan_supervisor_lifecycle", "stop_scan_supervisor_lifecycle"):
        decorators = functions[name].decorator_list
        assert any(
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr == "on_event"
            and decorator.args
            and isinstance(decorator.args[0], ast.Constant)
            and decorator.args[0].value in {"startup", "shutdown"}
            for decorator in decorators
        )
