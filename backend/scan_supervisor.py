"""PostgreSQL-backed execution primitives for the in-process Scan Supervisor.

This module supplies only opt-in lifecycle wiring.  It cannot claim or execute
work until Phase 3 supplies durable material persistence, checkpoints, and a
concrete stage executor.  The existing in-memory /scan path remains unchanged.

Operational rows are never evidence.  They contain only identity, leases,
fencing tokens, hashes, transition diagnostics, and checkpoint references.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Iterator, Mapping, Protocol


class ScanSupervisorConfigurationError(RuntimeError):
    """Raised when an explicitly enabled Supervisor lacks its database DSN."""


class StaleLeaseError(RuntimeError):
    """Raised when a reclaimed or expired attempt tries to mutate a step."""


class MaterialIntegrityError(RuntimeError):
    """Raised when persisted material no longer matches its immutable text hash."""


class SourceRetrievalIntegrityError(RuntimeError):
    """Raised when durable source-retrieval text or metadata is malformed."""


class JurySourceCandidateIntegrityError(RuntimeError):
    """Raised when durable Jury candidate text or metadata is malformed."""


class IntentInputSnapshotIntegrityError(RuntimeError):
    """Raised when an immutable Intent input manifest is malformed or mismatched."""


class JuryInputSnapshotIntegrityError(RuntimeError):
    """Raised when an immutable Jury input manifest is malformed or mismatched."""


class DiagnosisInputSnapshotIntegrityError(RuntimeError):
    """Raised when an immutable Diagnosis input manifest is malformed or mismatched."""


class ForesightInputSnapshotIntegrityError(RuntimeError):
    """Raised when an immutable Foresight input manifest is malformed or mismatched."""


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class StepStatus(str, Enum):
    PENDING = "pending"
    LEASED = "leased"
    RUNNING = "running"
    COMPLETED = "completed"
    RETRYABLE_FAILED = "retryable_failed"
    TERMINAL_FAILED = "terminal_failed"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ScanSupervisorSettings:
    """Configuration for the future in-process Supervisor.

    The default is deliberately inert.  Enabling it requires a PostgreSQL DSN
    and a separately applied schema migration; this module never falls back to
    process-local state.
    """

    database_url: str
    enabled: bool
    schema_ready: bool = False
    lease_seconds: int = 90
    poll_seconds: float = 2.0

    @classmethod
    def from_env(cls) -> "ScanSupervisorSettings":
        enabled = _env_bool("SCAN_SUPERVISOR_ENABLED", default=False)
        schema_ready = _env_bool("SCAN_SUPERVISOR_SCHEMA_READY", default=False)
        database_url = (
            os.environ.get("SCANNER_DATABASE_URL")
            or os.environ.get("DATABASE_URL")
            or ""
        ).strip()
        settings = cls(
            database_url=database_url,
            enabled=enabled,
            schema_ready=schema_ready,
            lease_seconds=max(15, int(os.environ.get("SCAN_SUPERVISOR_LEASE_SECONDS", "90"))),
            poll_seconds=max(0.25, float(os.environ.get("SCAN_SUPERVISOR_POLL_SECONDS", "2"))),
        )
        if settings.enabled and not settings.database_url:
            raise ScanSupervisorConfigurationError(
                "SCAN_SUPERVISOR_ENABLED requires SCANNER_DATABASE_URL or DATABASE_URL."
            )
        return settings


@dataclass(frozen=True)
class StepLease:
    run_id: str
    stage_name: str
    attempt: int
    lease_owner: str
    fencing_token: int
    lease_expires_at: datetime


@dataclass(frozen=True)
class ScanMaterial:
    """Immutable extracted scan input; never a claim, source verdict, or evidence."""

    material_id: str
    origin: str
    filename: str
    media_type: str
    byte_size: int
    content_sha256: str
    extracted_text_sha256: str
    parser_version: str
    extraction_status: str
    extraction_warnings: tuple[str, ...]
    allowed_use_classification: str
    extracted_text: str


@dataclass(frozen=True)
class SourceRetrieval:
    """One retrieval outcome; operational input only, never claim-support evidence."""

    retrieval_id: str
    run_id: str
    stage_name: str
    attempt: int
    retrieval_origin: str
    source_url: str
    canonical_url: str
    retrieved_at: datetime
    http_status: int | None
    content_type: str
    response_sha256: str
    extracted_text_sha256: str
    parser_route: str
    readability_status: str
    retrieval_outcome: str
    extraction_warnings: tuple[str, ...]
    response_metadata: Mapping[str, Any]
    extracted_text: str


@dataclass(frozen=True)
class JurySourceCandidate:
    """One Jury-stage discovery candidate; never claim support or evidence."""

    candidate_id: str
    run_id: str
    stage_name: str
    attempt: int
    candidate_origin: str
    source_url: str
    canonical_url: str
    recorded_at: datetime
    parser_route: str
    cached_extract_sha256: str
    cached_extract_text: str
    extraction_warnings: tuple[str, ...]
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class IntentInputSnapshot:
    """Immutable manifest binding one Intent attempt to verified durable input.

    It contains only material and retrieval references with hashes and status
    metadata. It has no Company Evidence, Foresight, claim-support, or
    publication meaning.
    """

    snapshot_id: str
    run_id: str
    stage_name: str
    attempt: int
    contract_version: str
    input_hash: str
    manifest: Mapping[str, Any]

    @property
    def snapshot_ref(self) -> str:
        return f"intent-input://scan/{self.snapshot_id}"


@dataclass(frozen=True)
class JuryInputSnapshot:
    """Immutable manifest binding one Jury attempt to verified durable input.

    It contains only the upstream Intent checkpoint identity plus Jury-stage
    candidate and retrieval references. It has no Company Evidence, Foresight,
    claim-support, or publication meaning.
    """

    snapshot_id: str
    run_id: str
    stage_name: str
    attempt: int
    contract_version: str
    input_hash: str
    manifest: Mapping[str, Any]

    @property
    def snapshot_ref(self) -> str:
        return f"jury-input://scan/{self.snapshot_id}"


@dataclass(frozen=True)
class DiagnosisInputSnapshot:
    """Immutable manifest binding one Diagnosis attempt to a passed Jury checkpoint.

    It contains only the upstream Jury checkpoint identity. It has no Company
    Evidence, Foresight, claim-support, or publication meaning.
    """

    snapshot_id: str
    run_id: str
    stage_name: str
    attempt: int
    contract_version: str
    input_hash: str
    manifest: Mapping[str, Any]

    @property
    def snapshot_ref(self) -> str:
        return f"diagnosis-input://scan/{self.snapshot_id}"


@dataclass(frozen=True)
class ForesightInputSnapshot:
    """Immutable manifest binding one Foresight attempt to a passed Diagnosis checkpoint.

    It contains only the upstream Diagnosis checkpoint identity. It has no
    Company Evidence, Foresight Knowledge Base content, claim-support, or
    publication meaning.
    """

    snapshot_id: str
    run_id: str
    stage_name: str
    attempt: int
    contract_version: str
    input_hash: str
    manifest: Mapping[str, Any]

    @property
    def snapshot_ref(self) -> str:
        return f"foresight-input://scan/{self.snapshot_id}"


@dataclass(frozen=True)
class ScanCheckpoint:
    """Immutable persisted stage output with only references to input material."""

    checkpoint_id: str
    run_id: str
    stage_name: str
    attempt: int
    contract_version: str
    input_hash: str
    output_hash: str
    gate_result: Mapping[str, Any]
    snapshot: Mapping[str, Any]

    @property
    def checkpoint_ref(self) -> str:
        return f"checkpoint://scan/{self.checkpoint_id}"


@dataclass(frozen=True)
class StageExecutionResult:
    """Metadata a future stage adapter may commit after durable validation.

    Payloads themselves remain outside this object: Phase 3 must first write
    immutable material/checkpoint records and calculate their hashes.
    """

    output_hash: str
    checkpoint_ref: str | None
    gate_result: Mapping[str, Any]
    next_stage: str | None
    checkpoint: ScanCheckpoint | None = None


class StageExecutor(Protocol):
    """Adapter boundary for invoking an existing cognitive stage.

    The adapter is intentionally not called in Phase 2.  Its later
    implementation must read verified durable input for the lease and return
    only checkpoint metadata after the existing stage function has completed.
    """

    def execute(self, lease: StepLease) -> StageExecutionResult: ...


class SupervisorLifecycleStatus(str, Enum):
    DISABLED = "disabled"
    BLOCKED_SCHEMA_NOT_READY = "blocked_schema_not_ready"
    BLOCKED_STAGE_EXECUTOR_NOT_REGISTERED = "blocked_stage_executor_not_registered"
    BLOCKED_PHASE_3_CHECKPOINTING_REQUIRED = "blocked_phase_3_checkpointing_required"


@dataclass(frozen=True)
class SupervisorLifecycleReport:
    status: SupervisorLifecycleStatus
    reason: str


class ScanSupervisorLifecycle:
    """Own FastAPI lifecycle intent without allowing premature execution.

    The lifecycle performs no database I/O while disabled or blocked.  That is
    intentional: applying the migration is a release operation, and a concrete
    executor must not exist before durable material/checkpoint support lands.
    """

    def __init__(
        self,
        settings: ScanSupervisorSettings,
        *,
        executor: StageExecutor | None = None,
    ) -> None:
        self.settings = settings
        self.executor = executor
        self._report: SupervisorLifecycleReport | None = None

    @property
    def report(self) -> SupervisorLifecycleReport | None:
        return self._report

    def start(self) -> SupervisorLifecycleReport:
        if not self.settings.enabled:
            self._report = SupervisorLifecycleReport(
                SupervisorLifecycleStatus.DISABLED,
                "SCAN_SUPERVISOR_ENABLED is false.",
            )
        elif not self.settings.schema_ready:
            self._report = SupervisorLifecycleReport(
                SupervisorLifecycleStatus.BLOCKED_SCHEMA_NOT_READY,
                "Apply the verified Supervisor migration before setting SCAN_SUPERVISOR_SCHEMA_READY=true.",
            )
        elif self.executor is None:
            self._report = SupervisorLifecycleReport(
                SupervisorLifecycleStatus.BLOCKED_STAGE_EXECUTOR_NOT_REGISTERED,
                "Phase 3 must register a durable material/checkpoint stage executor before work can be claimed.",
            )
        else:
            self._report = SupervisorLifecycleReport(
                SupervisorLifecycleStatus.BLOCKED_PHASE_3_CHECKPOINTING_REQUIRED,
                "Phase 2 never starts a worker loop; Phase 3 must supply durable checkpoint commits first.",
            )
        return self._report

    def stop(self) -> SupervisorLifecycleReport | None:
        """Return the last state; Phase 2 has no worker thread to stop."""

        return self._report


class CursorLike(Protocol):
    rowcount: int

    def execute(self, query: str, parameters: tuple[Any, ...] | None = None) -> Any: ...
    def fetchone(self) -> Mapping[str, Any] | None: ...
    def fetchall(self) -> list[Mapping[str, Any]]: ...
    def __enter__(self) -> "CursorLike": ...
    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None: ...


class ConnectionLike(Protocol):
    def cursor(self) -> CursorLike: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...


ConnectionFactory = Callable[[str], ConnectionLike]


def _env_bool(name: str, *, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _now() -> datetime:
    return datetime.now(timezone.utc)

def _is_sha256_digest(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdefABCDEF" for character in value)


def _sha256_text(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _material_warnings(value: Any) -> tuple[str, ...]:
    raw_value = value
    if isinstance(raw_value, str):
        try:
            raw_value = json.loads(raw_value)
        except json.JSONDecodeError as error:
            raise MaterialIntegrityError("Material warnings are not valid JSON.") from error
    if isinstance(raw_value, Mapping):
        raw_value = raw_value.get("warnings", [])
    if not isinstance(raw_value, list) or not all(isinstance(item, str) for item in raw_value):
        raise MaterialIntegrityError("Material warnings must be a string list.")
    return tuple(raw_value)



def _intent_manifest(value: Any) -> Mapping[str, Any]:
    raw_value = value
    if isinstance(raw_value, str):
        try:
            raw_value = json.loads(raw_value)
        except json.JSONDecodeError as error:
            raise IntentInputSnapshotIntegrityError("Intent input manifest is not valid JSON.") from error
    if not isinstance(raw_value, Mapping) or not _json_safe(raw_value):
        raise IntentInputSnapshotIntegrityError("Intent input manifest must be a JSON-safe mapping.")
    return dict(raw_value)


def _jury_manifest(value: Any) -> Mapping[str, Any]:
    raw_value = value
    if isinstance(raw_value, str):
        try:
            raw_value = json.loads(raw_value)
        except json.JSONDecodeError as error:
            raise JuryInputSnapshotIntegrityError("Jury input manifest is not valid JSON.") from error
    if not isinstance(raw_value, Mapping) or not _json_safe(raw_value):
        raise JuryInputSnapshotIntegrityError("Jury input manifest must be a JSON-safe mapping.")
    return dict(raw_value)


def _diagnosis_manifest(value: Any) -> Mapping[str, Any]:
    raw_value = value
    if isinstance(raw_value, str):
        try:
            raw_value = json.loads(raw_value)
        except json.JSONDecodeError as error:
            raise DiagnosisInputSnapshotIntegrityError(
                "Diagnosis input manifest is not valid JSON."
            ) from error
    if not isinstance(raw_value, Mapping) or not _json_safe(raw_value):
        raise DiagnosisInputSnapshotIntegrityError(
            "Diagnosis input manifest must be a JSON-safe mapping."
        )
    return dict(raw_value)


def _foresight_manifest(value: Any) -> Mapping[str, Any]:
    raw_value = value
    if isinstance(raw_value, str):
        try:
            raw_value = json.loads(raw_value)
        except json.JSONDecodeError as error:
            raise ForesightInputSnapshotIntegrityError(
                "Foresight input manifest is not valid JSON."
            ) from error
    if not isinstance(raw_value, Mapping) or not _json_safe(raw_value):
        raise ForesightInputSnapshotIntegrityError(
            "Foresight input manifest must be a JSON-safe mapping."
        )
    return dict(raw_value)


def _source_metadata(value: Any) -> Mapping[str, Any]:
    raw_value = value
    if isinstance(raw_value, str):
        try:
            raw_value = json.loads(raw_value)
        except json.JSONDecodeError as error:
            raise SourceRetrievalIntegrityError("Source retrieval metadata is not valid JSON.") from error
    if not isinstance(raw_value, Mapping) or not _json_safe(raw_value):
        raise SourceRetrievalIntegrityError("Source retrieval metadata must be a JSON-safe mapping.")
    return dict(raw_value)


_FORBIDDEN_JURY_CANDIDATE_METADATA_KEYS = frozenset(
    {
        "claim_support",
        "generation_eligible",
        "analysis_verified",
        "allowed_as_fact",
        "promotion_status",
        "foresight_eligible",
        "entity_verified",
        "entity_match_status",
    }
)


def _jury_candidate_metadata(value: Any) -> Mapping[str, Any]:
    raw_value = value
    if isinstance(raw_value, str):
        try:
            raw_value = json.loads(raw_value)
        except json.JSONDecodeError as error:
            raise JurySourceCandidateIntegrityError("Jury source candidate metadata is not valid JSON.") from error
    if not isinstance(raw_value, Mapping) or not _json_safe(raw_value):
        raise JurySourceCandidateIntegrityError("Jury source candidate metadata must be a JSON-safe mapping.")
    mapping = dict(raw_value)
    blocked = sorted(key for key in mapping if key in _FORBIDDEN_JURY_CANDIDATE_METADATA_KEYS)
    if blocked:
        raise JurySourceCandidateIntegrityError(
            "Jury source candidate metadata cannot include evidence fields: "
            + ", ".join(blocked)
        )
    return mapping


def _json_safe(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool, int, float)):
        return True
    if isinstance(value, Mapping):
        return all(isinstance(key, str) and _json_safe(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return all(_json_safe(item) for item in value)
    return False



def _json(value: Mapping[str, Any] | None) -> str:
    return json.dumps(value or {}, sort_keys=True, separators=(",", ":"))


def _mapping(row: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if row is None:
        return None
    if not isinstance(row, Mapping):
        raise TypeError("Scan repository requires mapping-style PostgreSQL rows.")
    return row


def _default_connection_factory(database_url: str) -> ConnectionLike:
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as error:  # pragma: no cover - exercised by deploy setup
        raise ScanSupervisorConfigurationError(
            "PostgreSQL Supervisor requires the psycopg package."
        ) from error
    return psycopg.connect(database_url, row_factory=dict_row)


class ScanRepository:
    """Transactional repository for Scan Supervisor operational state.

    Every conditional mutation includes the lease owner and fencing token.  A
    process that lost its lease may finish external work, but cannot heartbeat,
    checkpoint, complete, or fail the reclaimed attempt.
    """

    def __init__(
        self,
        database_url: str,
        *,
        connection_factory: ConnectionFactory | None = None,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        if not database_url:
            raise ScanSupervisorConfigurationError("A PostgreSQL database URL is required.")
        self.database_url = database_url
        self._connection_factory = connection_factory or _default_connection_factory
        self._clock = clock

    @contextmanager
    def _transaction(self) -> Iterator[ConnectionLike]:
        connection = self._connection_factory(self.database_url)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create_run(
        self,
        *,
        run_id: str,
        company_name: str,
        company_url: str,
        idempotency_key: str,
        request_hash: str,
        cognitive_contract_version: str,
        initial_stage: str,
    ) -> Mapping[str, Any]:
        """Create one queued run and its first pending step idempotently."""

        with self._transaction() as connection, connection.cursor() as cursor:
            return self._create_run_on_cursor(
                cursor,
                run_id=run_id,
                company_name=company_name,
                company_url=company_url,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                cognitive_contract_version=cognitive_contract_version,
                initial_stage=initial_stage,
            )

    def create_run_with_materials(
        self,
        *,
        run_id: str,
        company_name: str,
        company_url: str,
        idempotency_key: str,
        request_hash: str,
        cognitive_contract_version: str,
        initial_stage: str,
        materials: list[ScanMaterial],
    ) -> Mapping[str, Any]:
        """Atomically create (or replay) a run with immutable prepared material.

        This is an ingress transaction only.  It does not claim a lease, start
        a worker, or route the legacy in-memory /scan path through PostgreSQL.
        """

        self._validate_materials(materials)
        with self._transaction() as connection, connection.cursor() as cursor:
            created = self._create_run_on_cursor(
                cursor,
                run_id=run_id,
                company_name=company_name,
                company_url=company_url,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                cognitive_contract_version=cognitive_contract_version,
                initial_stage=initial_stage,
            )
            if str(created["run_id"]) != run_id:
                raise ValueError(
                    "idempotency_key already belongs to a different deterministic run_id."
                )
            self._persist_materials_on_cursor(cursor, run_id=run_id, materials=materials)
            return created

    def _create_run_on_cursor(
        self,
        cursor: CursorLike,
        *,
        run_id: str,
        company_name: str,
        company_url: str,
        idempotency_key: str,
        request_hash: str,
        cognitive_contract_version: str,
        initial_stage: str,
    ) -> Mapping[str, Any]:
        if not all((run_id, company_name, idempotency_key, request_hash, initial_stage)):
            raise ValueError("run_id, company_name, idempotency_key, request_hash, and initial_stage are required.")

        cursor.execute(
            """
            INSERT INTO scan_runs (
                run_id, company_name, company_url, cognitive_contract_version,
                status, current_stage, idempotency_key, request_hash
            ) VALUES (%s, %s, %s, %s, 'queued', %s, %s, %s)
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING run_id, status, current_stage, request_hash
            """,
            (
                run_id,
                company_name,
                company_url or None,
                cognitive_contract_version,
                initial_stage,
                idempotency_key,
                request_hash,
            ),
        )
        created = _mapping(cursor.fetchone())
        if created is None:
            cursor.execute(
                """
                SELECT run_id, status, current_stage, request_hash
                FROM scan_runs
                WHERE idempotency_key = %s
                """,
                (idempotency_key,),
            )
            created = _mapping(cursor.fetchone())
            if created is None:
                raise RuntimeError("Idempotent scan run disappeared before it could be read.")
            if created.get("request_hash") != request_hash:
                raise ValueError("Idempotency key was already used for different scan input.")
            return created

        cursor.execute(
            """
            INSERT INTO scan_steps (run_id, stage_name, attempt, status)
            VALUES (%s, %s, 1, 'pending')
            """,
            (run_id, initial_stage),
        )
        cursor.execute(
            """
            INSERT INTO scan_events (run_id, stage_name, attempt, event_type, details)
            VALUES (%s, %s, 1, 'run_queued', %s::jsonb)
            """,
            (run_id, initial_stage, _json({"idempotency_key": idempotency_key})),
        )
        return created

    def persist_materials(
        self,
        *,
        run_id: str,
        materials: list[ScanMaterial],
    ) -> list[Mapping[str, Any]]:
        """Persist immutable extracted material without retaining raw file bytes."""

        self._validate_materials(materials)
        with self._transaction() as connection, connection.cursor() as cursor:
            return self._persist_materials_on_cursor(cursor, run_id=run_id, materials=materials)

    def _validate_materials(self, materials: list[ScanMaterial]) -> None:
        seen_material_ids: set[str] = set()
        allowed_statuses = {"extracted", "empty", "failed"}
        for material in materials:
            if material.material_id in seen_material_ids:
                raise ValueError("material_id must be unique within one persistence request.")
            seen_material_ids.add(material.material_id)
            if not material.material_id or not material.origin or not material.filename:
                raise ValueError("material identity fields are required.")
            if material.byte_size < 0:
                raise ValueError("material byte_size cannot be negative.")
            if not _is_sha256_digest(material.content_sha256) or not _is_sha256_digest(
                material.extracted_text_sha256
            ):
                raise ValueError("material hashes must be SHA-256 hex digests.")
            if material.extraction_status not in allowed_statuses:
                raise ValueError("material extraction_status is invalid.")
            if not material.parser_version or not material.allowed_use_classification:
                raise ValueError("material parser and allowed-use classifications are required.")

    def _persist_materials_on_cursor(
        self,
        cursor: CursorLike,
        *,
        run_id: str,
        materials: list[ScanMaterial],
    ) -> list[Mapping[str, Any]]:
        if not run_id:
            raise ValueError("run_id is required.")

        persisted: list[Mapping[str, Any]] = []
        for material in materials:
            cursor.execute(
                """
                INSERT INTO scan_materials (
                    material_id, run_id, origin, filename, media_type, byte_size,
                    content_sha256, extracted_text_sha256, parser_version,
                    extraction_status, extraction_warnings,
                    allowed_use_classification, extracted_text
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s
                )
                ON CONFLICT (material_id) DO NOTHING
                RETURNING material_id, run_id, content_sha256,
                          extracted_text_sha256, parser_version,
                          extraction_status, allowed_use_classification
                """,
                (
                    material.material_id,
                    run_id,
                    material.origin,
                    material.filename,
                    material.media_type,
                    material.byte_size,
                    material.content_sha256,
                    material.extracted_text_sha256,
                    material.parser_version,
                    material.extraction_status,
                    _json({"warnings": list(material.extraction_warnings)}),
                    material.allowed_use_classification,
                    material.extracted_text,
                ),
            )
            stored = _mapping(cursor.fetchone())
            if stored is None:
                cursor.execute(
                    """
                    SELECT material_id, run_id, content_sha256,
                           extracted_text_sha256, parser_version,
                           extraction_status, allowed_use_classification
                    FROM scan_materials
                    WHERE material_id = %s
                    """,
                    (material.material_id,),
                )
                stored = _mapping(cursor.fetchone())
                if stored is None:
                    raise RuntimeError("Idempotent material disappeared before it could be read.")
                expected = {
                    "material_id": material.material_id,
                    "run_id": run_id,
                    "content_sha256": material.content_sha256,
                    "extracted_text_sha256": material.extracted_text_sha256,
                    "parser_version": material.parser_version,
                    "extraction_status": material.extraction_status,
                    "allowed_use_classification": material.allowed_use_classification,
                }
                if any(stored.get(key) != value for key, value in expected.items()):
                    raise ValueError(
                        "material_id was already used for different immutable material content."
                    )
            persisted.append(stored)
        return persisted

    def load_materials(self, *, run_id: str) -> list[ScanMaterial]:
        """Load extracted material and reject any text-hash mismatch."""

        if not run_id:
            raise ValueError("run_id is required.")
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM scan_runs WHERE run_id = %s", (run_id,))
            if _mapping(cursor.fetchone()) is None:
                raise KeyError(f"Unknown scan run: {run_id}")
            cursor.execute(
                """
                SELECT material_id, origin, filename, media_type, byte_size,
                       content_sha256, extracted_text_sha256, parser_version,
                       extraction_status, extraction_warnings,
                       allowed_use_classification, extracted_text
                FROM scan_materials
                WHERE run_id = %s
                ORDER BY created_at, material_id
                """,
                (run_id,),
            )
            rows = [_mapping(row) for row in cursor.fetchall()]

        materials: list[ScanMaterial] = []
        for row in rows:
            if row is None:
                raise MaterialIntegrityError("Material query returned an empty row.")
            warnings = _material_warnings(row.get("extraction_warnings"))
            material = ScanMaterial(
                material_id=str(row["material_id"]),
                origin=str(row["origin"]),
                filename=str(row["filename"]),
                media_type=str(row["media_type"]),
                byte_size=int(row["byte_size"]),
                content_sha256=str(row["content_sha256"]),
                extracted_text_sha256=str(row["extracted_text_sha256"]),
                parser_version=str(row["parser_version"]),
                extraction_status=str(row["extraction_status"]),
                extraction_warnings=warnings,
                allowed_use_classification=str(row["allowed_use_classification"]),
                extracted_text=str(row["extracted_text"]),
            )
            self._validate_materials([material])
            if _sha256_text(material.extracted_text) != material.extracted_text_sha256:
                raise MaterialIntegrityError(
                    f"Extracted text hash mismatch for material {material.material_id}."
                )
            materials.append(material)
        return materials


    def persist_source_retrievals(
        self,
        *,
        retrievals: list[SourceRetrieval],
    ) -> list[Mapping[str, Any]]:
        """Persist retrieval provenance without converting it into evidence."""

        self._validate_source_retrievals(retrievals)
        with self._transaction() as connection, connection.cursor() as cursor:
            return self._persist_source_retrievals_on_cursor(cursor, retrievals=retrievals)

    def load_source_retrievals(
        self,
        *,
        run_id: str,
        stage_name: str,
        attempt: int,
    ) -> list[SourceRetrieval]:
        """Load retrievals only when their extracted text remains hash-verified."""

        if not run_id or not stage_name or attempt < 1:
            raise ValueError("run_id, stage_name, and a positive attempt are required.")
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM scan_steps
                WHERE run_id = %s AND stage_name = %s AND attempt = %s
                """,
                (run_id, stage_name, attempt),
            )
            if _mapping(cursor.fetchone()) is None:
                raise KeyError(f"Unknown scan step: {run_id}/{stage_name}/{attempt}")
            cursor.execute(
                """
                SELECT retrieval_id, run_id, stage_name, attempt, retrieval_origin,
                       source_url, canonical_url, retrieved_at, http_status,
                       content_type, response_sha256, extracted_text_sha256,
                       parser_route, readability_status, retrieval_outcome,
                       extraction_warnings, response_metadata, extracted_text
                FROM source_retrievals
                WHERE run_id = %s AND stage_name = %s AND attempt = %s
                ORDER BY retrieved_at, retrieval_id
                """,
                (run_id, stage_name, attempt),
            )
            rows = [_mapping(row) for row in cursor.fetchall()]

        retrievals: list[SourceRetrieval] = []
        for row in rows:
            if row is None:
                raise SourceRetrievalIntegrityError("Source retrieval query returned an empty row.")
            metadata = _source_metadata(row.get("response_metadata"))
            warnings = _material_warnings(row.get("extraction_warnings"))
            retrieved_at = row.get("retrieved_at")
            if not isinstance(retrieved_at, datetime):
                raise SourceRetrievalIntegrityError("Source retrieval timestamp is invalid.")
            retrieval = SourceRetrieval(
                retrieval_id=str(row["retrieval_id"]),
                run_id=str(row["run_id"]),
                stage_name=str(row["stage_name"]),
                attempt=int(row["attempt"]),
                retrieval_origin=str(row["retrieval_origin"]),
                source_url=str(row["source_url"]),
                canonical_url=str(row["canonical_url"]),
                retrieved_at=retrieved_at,
                http_status=int(row["http_status"]) if row.get("http_status") is not None else None,
                content_type=str(row["content_type"]),
                response_sha256=str(row["response_sha256"]),
                extracted_text_sha256=str(row["extracted_text_sha256"]),
                parser_route=str(row["parser_route"]),
                readability_status=str(row["readability_status"]),
                retrieval_outcome=str(row["retrieval_outcome"]),
                extraction_warnings=warnings,
                response_metadata=metadata,
                extracted_text=str(row["extracted_text"]),
            )
            self._validate_source_retrievals([retrieval])
            if _sha256_text(retrieval.extracted_text) != retrieval.extracted_text_sha256:
                raise SourceRetrievalIntegrityError(
                    f"Extracted text hash mismatch for retrieval {retrieval.retrieval_id}."
                )
            retrievals.append(retrieval)
        return retrievals

    def _validate_source_retrievals(self, retrievals: list[SourceRetrieval]) -> None:
        seen_retrieval_ids: set[str] = set()
        allowed_readability = {"readable", "unreadable", "unknown"}
        allowed_outcomes = {"succeeded", "not_found", "blocked", "failed"}
        for retrieval in retrievals:
            if retrieval.retrieval_id in seen_retrieval_ids:
                raise ValueError("retrieval_id must be unique within one persistence request.")
            seen_retrieval_ids.add(retrieval.retrieval_id)
            if not all(
                (
                    retrieval.retrieval_id,
                    retrieval.run_id,
                    retrieval.stage_name,
                    retrieval.retrieval_origin,
                    retrieval.source_url,
                    retrieval.canonical_url,
                    retrieval.content_type,
                    retrieval.parser_route,
                )
            ):
                raise ValueError("source retrieval identity fields are required.")
            if retrieval.attempt < 1:
                raise ValueError("source retrieval attempt must be positive.")
            if retrieval.retrieved_at.tzinfo is None:
                raise ValueError("source retrieval timestamp must be timezone-aware.")
            if not retrieval.source_url.startswith(("http://", "https://")):
                raise ValueError("source retrieval URL must use HTTP or HTTPS.")
            if not retrieval.canonical_url.startswith(("http://", "https://")):
                raise ValueError("source retrieval canonical URL must use HTTP or HTTPS.")
            if not _is_sha256_digest(retrieval.response_sha256) or not _is_sha256_digest(
                retrieval.extracted_text_sha256
            ):
                raise ValueError("source retrieval hashes must be SHA-256 hex digests.")
            if retrieval.readability_status not in allowed_readability:
                raise ValueError("source retrieval readability_status is invalid.")
            if retrieval.retrieval_outcome not in allowed_outcomes:
                raise ValueError("source retrieval outcome is invalid.")
            if retrieval.http_status is not None and not (100 <= retrieval.http_status <= 599):
                raise ValueError("source retrieval HTTP status is invalid.")
            if not _json_safe(retrieval.response_metadata):
                raise ValueError("source retrieval metadata must be JSON-safe and contain no raw bytes.")

    def _persist_source_retrievals_on_cursor(
        self,
        cursor: CursorLike,
        *,
        retrievals: list[SourceRetrieval],
    ) -> list[Mapping[str, Any]]:
        persisted: list[Mapping[str, Any]] = []
        for retrieval in retrievals:
            cursor.execute(
                """
                INSERT INTO source_retrievals (
                    retrieval_id, run_id, stage_name, attempt, retrieval_origin,
                    source_url, canonical_url, retrieved_at, http_status,
                    content_type, response_sha256, extracted_text_sha256,
                    parser_route, readability_status, retrieval_outcome,
                    extraction_warnings, response_metadata, extracted_text
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s::jsonb, %s::jsonb, %s
                )
                ON CONFLICT (retrieval_id) DO NOTHING
                RETURNING retrieval_id, run_id, stage_name, attempt, canonical_url,
                          response_sha256, extracted_text_sha256, parser_route,
                          readability_status, retrieval_outcome
                """,
                (
                    retrieval.retrieval_id,
                    retrieval.run_id,
                    retrieval.stage_name,
                    retrieval.attempt,
                    retrieval.retrieval_origin,
                    retrieval.source_url,
                    retrieval.canonical_url,
                    retrieval.retrieved_at,
                    retrieval.http_status,
                    retrieval.content_type,
                    retrieval.response_sha256,
                    retrieval.extracted_text_sha256,
                    retrieval.parser_route,
                    retrieval.readability_status,
                    retrieval.retrieval_outcome,
                    _json({"warnings": list(retrieval.extraction_warnings)}),
                    _json(retrieval.response_metadata),
                    retrieval.extracted_text,
                ),
            )
            stored = _mapping(cursor.fetchone())
            if stored is None:
                cursor.execute(
                    """
                    SELECT retrieval_id, run_id, stage_name, attempt, canonical_url,
                           response_sha256, extracted_text_sha256, parser_route,
                           readability_status, retrieval_outcome
                    FROM source_retrievals
                    WHERE retrieval_id = %s
                    """,
                    (retrieval.retrieval_id,),
                )
                stored = _mapping(cursor.fetchone())
                if stored is None:
                    raise RuntimeError("Idempotent source retrieval disappeared before it could be read.")
                expected = {
                    "retrieval_id": retrieval.retrieval_id,
                    "run_id": retrieval.run_id,
                    "stage_name": retrieval.stage_name,
                    "attempt": retrieval.attempt,
                    "canonical_url": retrieval.canonical_url,
                    "response_sha256": retrieval.response_sha256,
                    "extracted_text_sha256": retrieval.extracted_text_sha256,
                    "parser_route": retrieval.parser_route,
                    "readability_status": retrieval.readability_status,
                    "retrieval_outcome": retrieval.retrieval_outcome,
                }
                if any(stored.get(key) != value for key, value in expected.items()):
                    raise ValueError(
                        "retrieval_id was already used for different immutable retrieval content."
                    )
            persisted.append(stored)
        return persisted

    def persist_jury_source_candidates(
        self,
        *,
        candidates: list[JurySourceCandidate],
    ) -> list[Mapping[str, Any]]:
        """Persist Jury-stage candidates without converting them into evidence."""

        self._validate_jury_source_candidates(candidates)
        with self._transaction() as connection, connection.cursor() as cursor:
            return self._persist_jury_source_candidates_on_cursor(cursor, candidates=candidates)

    def load_jury_source_candidates(
        self,
        *,
        run_id: str,
        stage_name: str,
        attempt: int,
    ) -> list[JurySourceCandidate]:
        """Load Jury candidates only when cached extract text remains hash-verified."""

        if not run_id or stage_name != "jury_analysis" or attempt < 1:
            raise ValueError("run_id, stage_name='jury_analysis', and a positive attempt are required.")
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM scan_steps
                WHERE run_id = %s AND stage_name = %s AND attempt = %s
                """,
                (run_id, stage_name, attempt),
            )
            if _mapping(cursor.fetchone()) is None:
                raise KeyError(f"Unknown scan step: {run_id}/{stage_name}/{attempt}")
            cursor.execute(
                """
                SELECT candidate_id, run_id, stage_name, attempt, candidate_origin,
                       source_url, canonical_url, recorded_at, parser_route,
                       cached_extract_sha256, cached_extract_text,
                       extraction_warnings, metadata
                FROM jury_source_candidates
                WHERE run_id = %s AND stage_name = %s AND attempt = %s
                ORDER BY recorded_at, candidate_id
                """,
                (run_id, stage_name, attempt),
            )
            rows = [_mapping(row) for row in cursor.fetchall()]

        candidates: list[JurySourceCandidate] = []
        for row in rows:
            if row is None:
                raise JurySourceCandidateIntegrityError("Jury source candidate query returned an empty row.")
            recorded_at = row.get("recorded_at")
            if not isinstance(recorded_at, datetime):
                raise JurySourceCandidateIntegrityError("Jury source candidate timestamp is invalid.")
            candidate = JurySourceCandidate(
                candidate_id=str(row["candidate_id"]),
                run_id=str(row["run_id"]),
                stage_name=str(row["stage_name"]),
                attempt=int(row["attempt"]),
                candidate_origin=str(row["candidate_origin"]),
                source_url=str(row["source_url"]),
                canonical_url=str(row["canonical_url"]),
                recorded_at=recorded_at,
                parser_route=str(row["parser_route"]),
                cached_extract_sha256=str(row["cached_extract_sha256"]),
                cached_extract_text=str(row["cached_extract_text"]),
                extraction_warnings=_material_warnings(row.get("extraction_warnings")),
                metadata=_jury_candidate_metadata(row.get("metadata")),
            )
            self._validate_jury_source_candidates([candidate])
            if _sha256_text(candidate.cached_extract_text) != candidate.cached_extract_sha256:
                raise JurySourceCandidateIntegrityError(
                    f"Cached extract hash mismatch for candidate {candidate.candidate_id}."
                )
            candidates.append(candidate)
        return candidates

    def _validate_jury_source_candidates(self, candidates: list[JurySourceCandidate]) -> None:
        seen_ids: set[str] = set()
        allowed_origins = {
            "company_evidence_db",
            "previous_scan_source_cache",
            "jury_live_acquisition_seed",
        }
        for candidate in candidates:
            if candidate.candidate_id in seen_ids:
                raise ValueError("candidate_id must be unique within one persistence request.")
            seen_ids.add(candidate.candidate_id)
            if not all(
                (
                    candidate.candidate_id,
                    candidate.run_id,
                    candidate.stage_name,
                    candidate.candidate_origin,
                    candidate.source_url,
                    candidate.canonical_url,
                    candidate.parser_route,
                )
            ):
                raise ValueError("Jury source candidate identity fields are required.")
            if candidate.stage_name != "jury_analysis" or candidate.attempt < 1:
                raise ValueError("Jury source candidate must bind one positive jury_analysis attempt.")
            if candidate.candidate_origin not in allowed_origins:
                raise ValueError("Jury source candidate origin is invalid.")
            if candidate.recorded_at.tzinfo is None:
                raise ValueError("Jury source candidate timestamp must be timezone-aware.")
            if not candidate.source_url.startswith(("http://", "https://")):
                raise ValueError("Jury source candidate URL must use HTTP or HTTPS.")
            if not candidate.canonical_url.startswith(("http://", "https://")):
                raise ValueError("Jury source candidate canonical URL must use HTTP or HTTPS.")
            if not _is_sha256_digest(candidate.cached_extract_sha256):
                raise ValueError("Jury source candidate extract hash must be a SHA-256 digest.")
            if not _json_safe(candidate.metadata):
                raise ValueError("Jury source candidate metadata must be JSON-safe and contain no raw bytes.")
            blocked = sorted(
                key for key in candidate.metadata if key in _FORBIDDEN_JURY_CANDIDATE_METADATA_KEYS
            )
            if blocked:
                raise ValueError(
                    "Jury source candidate metadata cannot include evidence fields: "
                    + ", ".join(blocked)
                )

    def _persist_jury_source_candidates_on_cursor(
        self,
        cursor: CursorLike,
        *,
        candidates: list[JurySourceCandidate],
    ) -> list[Mapping[str, Any]]:
        persisted: list[Mapping[str, Any]] = []
        for candidate in candidates:
            cursor.execute(
                """
                INSERT INTO jury_source_candidates (
                    candidate_id, run_id, stage_name, attempt, candidate_origin,
                    source_url, canonical_url, recorded_at, parser_route,
                    cached_extract_sha256, cached_extract_text,
                    extraction_warnings, metadata
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb
                )
                ON CONFLICT (candidate_id) DO NOTHING
                RETURNING candidate_id, run_id, stage_name, attempt, candidate_origin,
                          canonical_url, cached_extract_sha256, parser_route
                """,
                (
                    candidate.candidate_id,
                    candidate.run_id,
                    candidate.stage_name,
                    candidate.attempt,
                    candidate.candidate_origin,
                    candidate.source_url,
                    candidate.canonical_url,
                    candidate.recorded_at,
                    candidate.parser_route,
                    candidate.cached_extract_sha256,
                    candidate.cached_extract_text,
                    _json({"warnings": list(candidate.extraction_warnings)}),
                    _json(candidate.metadata),
                ),
            )
            stored = _mapping(cursor.fetchone())
            if stored is None:
                cursor.execute(
                    """
                    SELECT candidate_id, run_id, stage_name, attempt, candidate_origin,
                           canonical_url, cached_extract_sha256, parser_route
                    FROM jury_source_candidates
                    WHERE candidate_id = %s
                    """,
                    (candidate.candidate_id,),
                )
                stored = _mapping(cursor.fetchone())
                if stored is None:
                    raise RuntimeError("Idempotent Jury source candidate disappeared before it could be read.")
                expected = {
                    "candidate_id": candidate.candidate_id,
                    "run_id": candidate.run_id,
                    "stage_name": candidate.stage_name,
                    "attempt": candidate.attempt,
                    "candidate_origin": candidate.candidate_origin,
                    "canonical_url": candidate.canonical_url,
                    "cached_extract_sha256": candidate.cached_extract_sha256,
                    "parser_route": candidate.parser_route,
                }
                if any(stored.get(key) != value for key, value in expected.items()):
                    raise ValueError(
                        "candidate_id was already used for different immutable candidate content."
                    )
            persisted.append(stored)
        return persisted


    def persist_intent_input_snapshot(
        self,
        *,
        snapshot: IntentInputSnapshot,
    ) -> Mapping[str, Any]:
        """Persist one immutable Intent input manifest without stage execution."""

        self._validate_intent_input_snapshot(snapshot)
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO intent_input_snapshots (
                    snapshot_id, run_id, stage_name, attempt, contract_version,
                    input_hash, manifest
                ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT DO NOTHING
                RETURNING snapshot_id, run_id, stage_name, attempt,
                          contract_version, input_hash, manifest
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.run_id,
                    snapshot.stage_name,
                    snapshot.attempt,
                    snapshot.contract_version,
                    snapshot.input_hash,
                    _json(snapshot.manifest),
                ),
            )
            stored = _mapping(cursor.fetchone())
            if stored is None:
                cursor.execute(
                    """
                    SELECT snapshot_id, run_id, stage_name, attempt,
                           contract_version, input_hash, manifest
                    FROM intent_input_snapshots
                    WHERE snapshot_id = %s
                       OR (run_id = %s AND stage_name = %s AND attempt = %s)
                    ORDER BY snapshot_id = %s DESC
                    LIMIT 1
                    """,
                    (
                        snapshot.snapshot_id,
                        snapshot.run_id,
                        snapshot.stage_name,
                        snapshot.attempt,
                        snapshot.snapshot_id,
                    ),
                )
                stored = _mapping(cursor.fetchone())
                if stored is None:
                    raise RuntimeError("Idempotent Intent input snapshot disappeared before it could be read.")
                expected = {
                    "snapshot_id": snapshot.snapshot_id,
                    "run_id": snapshot.run_id,
                    "stage_name": snapshot.stage_name,
                    "attempt": snapshot.attempt,
                    "contract_version": snapshot.contract_version,
                    "input_hash": snapshot.input_hash,
                    "manifest": dict(snapshot.manifest),
                }
                if any(stored.get(key) != value for key, value in expected.items()):
                    raise ValueError(
                        "Intent input snapshot already exists for different immutable input."
                    )
            return stored

    def load_intent_input_snapshot(
        self,
        *,
        run_id: str,
        stage_name: str,
        attempt: int,
    ) -> IntentInputSnapshot:
        """Load and validate a persisted Intent input manifest."""

        if not run_id or stage_name != "intent" or attempt < 1:
            raise ValueError("run_id, stage_name='intent', and a positive attempt are required.")
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT snapshot_id, run_id, stage_name, attempt,
                       contract_version, input_hash, manifest
                FROM intent_input_snapshots
                WHERE run_id = %s AND stage_name = %s AND attempt = %s
                """,
                (run_id, stage_name, attempt),
            )
            row = _mapping(cursor.fetchone())
        if row is None:
            raise KeyError(f"Unknown Intent input snapshot: {run_id}/{stage_name}/{attempt}")
        snapshot = IntentInputSnapshot(
            snapshot_id=str(row["snapshot_id"]),
            run_id=str(row["run_id"]),
            stage_name=str(row["stage_name"]),
            attempt=int(row["attempt"]),
            contract_version=str(row["contract_version"]),
            input_hash=str(row["input_hash"]),
            manifest=_intent_manifest(row.get("manifest")),
        )
        self._validate_intent_input_snapshot(snapshot)
        return snapshot

    def load_verified_intent_inputs(
        self,
        *,
        run_id: str,
        attempt: int,
    ) -> tuple[IntentInputSnapshot, list[ScanMaterial], list[SourceRetrieval]]:
        """Hydrate only input records that exactly match the immutable manifest."""

        snapshot = self.load_intent_input_snapshot(
            run_id=run_id,
            stage_name="intent",
            attempt=attempt,
        )
        materials = self.load_materials(run_id=run_id)
        retrievals = self.load_source_retrievals(
            run_id=run_id,
            stage_name="intent",
            attempt=attempt,
        )
        from scan_intent_snapshot import assert_intent_input_snapshot_matches

        assert_intent_input_snapshot_matches(
            snapshot,
            materials=materials,
            retrievals=retrievals,
        )
        return snapshot, materials, retrievals

    def _validate_intent_input_snapshot(self, snapshot: IntentInputSnapshot) -> None:
        if not all(
            (
                snapshot.snapshot_id,
                snapshot.run_id,
                snapshot.contract_version,
                snapshot.input_hash,
            )
        ):
            raise IntentInputSnapshotIntegrityError("Intent input snapshot identity fields are required.")
        if snapshot.stage_name != "intent" or snapshot.attempt < 1:
            raise IntentInputSnapshotIntegrityError("Intent input snapshot must bind one positive Intent attempt.")
        if not _is_sha256_digest(snapshot.input_hash):
            raise IntentInputSnapshotIntegrityError("Intent input snapshot hash must be a SHA-256 digest.")
        if not isinstance(snapshot.manifest, Mapping) or not _json_safe(snapshot.manifest):
            raise IntentInputSnapshotIntegrityError("Intent input manifest must be a JSON-safe mapping.")
        from scan_intent_snapshot import validate_intent_input_snapshot_manifest

        validate_intent_input_snapshot_manifest(snapshot)

    def persist_jury_input_snapshot(
        self,
        *,
        snapshot: JuryInputSnapshot,
    ) -> Mapping[str, Any]:
        """Persist one immutable Jury input manifest without stage execution."""

        self._validate_jury_input_snapshot(snapshot)
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO jury_input_snapshots (
                    snapshot_id, run_id, stage_name, attempt, contract_version,
                    input_hash, manifest
                ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT DO NOTHING
                RETURNING snapshot_id, run_id, stage_name, attempt,
                          contract_version, input_hash, manifest
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.run_id,
                    snapshot.stage_name,
                    snapshot.attempt,
                    snapshot.contract_version,
                    snapshot.input_hash,
                    _json(snapshot.manifest),
                ),
            )
            stored = _mapping(cursor.fetchone())
            if stored is None:
                cursor.execute(
                    """
                    SELECT snapshot_id, run_id, stage_name, attempt,
                           contract_version, input_hash, manifest
                    FROM jury_input_snapshots
                    WHERE snapshot_id = %s
                       OR (run_id = %s AND stage_name = %s AND attempt = %s)
                    ORDER BY snapshot_id = %s DESC
                    LIMIT 1
                    """,
                    (
                        snapshot.snapshot_id,
                        snapshot.run_id,
                        snapshot.stage_name,
                        snapshot.attempt,
                        snapshot.snapshot_id,
                    ),
                )
                stored = _mapping(cursor.fetchone())
                if stored is None:
                    raise RuntimeError("Idempotent Jury input snapshot disappeared before it could be read.")
                expected = {
                    "snapshot_id": snapshot.snapshot_id,
                    "run_id": snapshot.run_id,
                    "stage_name": snapshot.stage_name,
                    "attempt": snapshot.attempt,
                    "contract_version": snapshot.contract_version,
                    "input_hash": snapshot.input_hash,
                    "manifest": dict(snapshot.manifest),
                }
                if any(stored.get(key) != value for key, value in expected.items()):
                    raise ValueError(
                        "Jury input snapshot already exists for different immutable input."
                    )
            return stored

    def load_jury_input_snapshot(
        self,
        *,
        run_id: str,
        stage_name: str,
        attempt: int,
    ) -> JuryInputSnapshot:
        """Load and validate a persisted Jury input manifest."""

        if not run_id or stage_name != "jury_analysis" or attempt < 1:
            raise ValueError("run_id, stage_name='jury_analysis', and a positive attempt are required.")
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT snapshot_id, run_id, stage_name, attempt,
                       contract_version, input_hash, manifest
                FROM jury_input_snapshots
                WHERE run_id = %s AND stage_name = %s AND attempt = %s
                """,
                (run_id, stage_name, attempt),
            )
            row = _mapping(cursor.fetchone())
        if row is None:
            raise KeyError(f"Unknown Jury input snapshot: {run_id}/{stage_name}/{attempt}")
        snapshot = JuryInputSnapshot(
            snapshot_id=str(row["snapshot_id"]),
            run_id=str(row["run_id"]),
            stage_name=str(row["stage_name"]),
            attempt=int(row["attempt"]),
            contract_version=str(row["contract_version"]),
            input_hash=str(row["input_hash"]),
            manifest=_jury_manifest(row.get("manifest")),
        )
        self._validate_jury_input_snapshot(snapshot)
        return snapshot

    def load_verified_jury_inputs(
        self,
        *,
        run_id: str,
        attempt: int,
    ) -> tuple[JuryInputSnapshot, ScanCheckpoint, list[JurySourceCandidate], list[SourceRetrieval]]:
        """Hydrate only Jury input records that exactly match the immutable manifest."""

        snapshot = self.load_jury_input_snapshot(
            run_id=run_id,
            stage_name="jury_analysis",
            attempt=attempt,
        )
        intent_ref = snapshot.manifest.get("intent_checkpoint_ref")
        if not isinstance(intent_ref, Mapping):
            raise JuryInputSnapshotIntegrityError(
                "Jury input snapshot is missing its Intent checkpoint ref."
            )
        try:
            intent_attempt = int(intent_ref.get("attempt"))
        except (TypeError, ValueError) as error:
            raise JuryInputSnapshotIntegrityError(
                "Jury input snapshot Intent checkpoint attempt is invalid."
            ) from error
        checkpoint = self.load_checkpoint(
            run_id=run_id,
            stage_name="intent",
            attempt=intent_attempt,
        )
        if checkpoint is None:
            raise JuryInputSnapshotIntegrityError(
                f"Unknown Intent checkpoint for Jury input: {run_id}/intent/{intent_attempt}"
            )
        candidates = self.load_jury_source_candidates(
            run_id=run_id,
            stage_name="jury_analysis",
            attempt=attempt,
        )
        retrievals = self.load_source_retrievals(
            run_id=run_id,
            stage_name="jury_analysis",
            attempt=attempt,
        )
        from scan_jury_snapshot import assert_jury_input_snapshot_matches

        assert_jury_input_snapshot_matches(
            snapshot,
            intent_checkpoint=checkpoint,
            candidates=candidates,
            retrievals=retrievals,
        )
        return snapshot, checkpoint, candidates, retrievals

    def _validate_jury_input_snapshot(self, snapshot: JuryInputSnapshot) -> None:
        if not all(
            (
                snapshot.snapshot_id,
                snapshot.run_id,
                snapshot.contract_version,
                snapshot.input_hash,
            )
        ):
            raise JuryInputSnapshotIntegrityError("Jury input snapshot identity fields are required.")
        if snapshot.stage_name != "jury_analysis" or snapshot.attempt < 1:
            raise JuryInputSnapshotIntegrityError("Jury input snapshot must bind one positive jury_analysis attempt.")
        if not _is_sha256_digest(snapshot.input_hash):
            raise JuryInputSnapshotIntegrityError("Jury input snapshot hash must be a SHA-256 digest.")
        if not isinstance(snapshot.manifest, Mapping) or not _json_safe(snapshot.manifest):
            raise JuryInputSnapshotIntegrityError("Jury input manifest must be a JSON-safe mapping.")
        from scan_jury_snapshot import validate_jury_input_snapshot_manifest

        validate_jury_input_snapshot_manifest(snapshot)

    def persist_diagnosis_input_snapshot(
        self,
        *,
        snapshot: DiagnosisInputSnapshot,
    ) -> Mapping[str, Any]:
        """Persist one immutable Diagnosis input manifest without stage execution."""

        self._validate_diagnosis_input_snapshot(snapshot)
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO diagnosis_input_snapshots (
                    snapshot_id, run_id, stage_name, attempt, contract_version,
                    input_hash, manifest
                ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT DO NOTHING
                RETURNING snapshot_id, run_id, stage_name, attempt,
                          contract_version, input_hash, manifest
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.run_id,
                    snapshot.stage_name,
                    snapshot.attempt,
                    snapshot.contract_version,
                    snapshot.input_hash,
                    _json(snapshot.manifest),
                ),
            )
            stored = _mapping(cursor.fetchone())
            if stored is None:
                cursor.execute(
                    """
                    SELECT snapshot_id, run_id, stage_name, attempt,
                           contract_version, input_hash, manifest
                    FROM diagnosis_input_snapshots
                    WHERE snapshot_id = %s
                       OR (run_id = %s AND stage_name = %s AND attempt = %s)
                    ORDER BY snapshot_id = %s DESC
                    LIMIT 1
                    """,
                    (
                        snapshot.snapshot_id,
                        snapshot.run_id,
                        snapshot.stage_name,
                        snapshot.attempt,
                        snapshot.snapshot_id,
                    ),
                )
                stored = _mapping(cursor.fetchone())
                if stored is None:
                    raise RuntimeError(
                        "Idempotent Diagnosis input snapshot disappeared before it could be read."
                    )
                expected = {
                    "snapshot_id": snapshot.snapshot_id,
                    "run_id": snapshot.run_id,
                    "stage_name": snapshot.stage_name,
                    "attempt": snapshot.attempt,
                    "contract_version": snapshot.contract_version,
                    "input_hash": snapshot.input_hash,
                    "manifest": dict(snapshot.manifest),
                }
                if any(stored.get(key) != value for key, value in expected.items()):
                    raise ValueError(
                        "Diagnosis input snapshot already exists for different immutable input."
                    )
            return stored

    def load_diagnosis_input_snapshot(
        self,
        *,
        run_id: str,
        stage_name: str,
        attempt: int,
    ) -> DiagnosisInputSnapshot:
        """Load and validate a persisted Diagnosis input manifest."""

        if not run_id or stage_name != "diagnosis" or attempt < 1:
            raise ValueError("run_id, stage_name='diagnosis', and a positive attempt are required.")
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT snapshot_id, run_id, stage_name, attempt,
                       contract_version, input_hash, manifest
                FROM diagnosis_input_snapshots
                WHERE run_id = %s AND stage_name = %s AND attempt = %s
                """,
                (run_id, stage_name, attempt),
            )
            row = _mapping(cursor.fetchone())
        if row is None:
            raise KeyError(f"Unknown Diagnosis input snapshot: {run_id}/{stage_name}/{attempt}")
        snapshot = DiagnosisInputSnapshot(
            snapshot_id=str(row["snapshot_id"]),
            run_id=str(row["run_id"]),
            stage_name=str(row["stage_name"]),
            attempt=int(row["attempt"]),
            contract_version=str(row["contract_version"]),
            input_hash=str(row["input_hash"]),
            manifest=_diagnosis_manifest(row.get("manifest")),
        )
        self._validate_diagnosis_input_snapshot(snapshot)
        return snapshot

    def load_verified_diagnosis_inputs(
        self,
        *,
        run_id: str,
        attempt: int,
    ) -> tuple[DiagnosisInputSnapshot, ScanCheckpoint]:
        """Hydrate only the Jury checkpoint that exactly matches the manifest."""

        snapshot = self.load_diagnosis_input_snapshot(
            run_id=run_id,
            stage_name="diagnosis",
            attempt=attempt,
        )
        jury_ref = snapshot.manifest.get("jury_checkpoint_ref")
        if not isinstance(jury_ref, Mapping):
            raise DiagnosisInputSnapshotIntegrityError(
                "Diagnosis input snapshot is missing its Jury checkpoint ref."
            )
        try:
            jury_attempt = int(jury_ref.get("attempt"))
        except (TypeError, ValueError) as error:
            raise DiagnosisInputSnapshotIntegrityError(
                "Diagnosis input snapshot Jury checkpoint attempt is invalid."
            ) from error
        checkpoint = self.load_checkpoint(
            run_id=run_id,
            stage_name="jury_analysis",
            attempt=jury_attempt,
        )
        if checkpoint is None:
            raise DiagnosisInputSnapshotIntegrityError(
                f"Unknown Jury checkpoint for Diagnosis input: {run_id}/jury_analysis/{jury_attempt}"
            )
        from scan_diagnosis_snapshot import assert_diagnosis_input_snapshot_matches

        assert_diagnosis_input_snapshot_matches(
            snapshot,
            jury_checkpoint=checkpoint,
        )
        return snapshot, checkpoint

    def _validate_diagnosis_input_snapshot(self, snapshot: DiagnosisInputSnapshot) -> None:
        if not all(
            (
                snapshot.snapshot_id,
                snapshot.run_id,
                snapshot.contract_version,
                snapshot.input_hash,
            )
        ):
            raise DiagnosisInputSnapshotIntegrityError(
                "Diagnosis input snapshot identity fields are required."
            )
        if snapshot.stage_name != "diagnosis" or snapshot.attempt < 1:
            raise DiagnosisInputSnapshotIntegrityError(
                "Diagnosis input snapshot must bind one positive diagnosis attempt."
            )
        if not _is_sha256_digest(snapshot.input_hash):
            raise DiagnosisInputSnapshotIntegrityError(
                "Diagnosis input snapshot hash must be a SHA-256 digest."
            )
        if not isinstance(snapshot.manifest, Mapping) or not _json_safe(snapshot.manifest):
            raise DiagnosisInputSnapshotIntegrityError(
                "Diagnosis input manifest must be a JSON-safe mapping."
            )
        from scan_diagnosis_snapshot import validate_diagnosis_input_snapshot_manifest

        validate_diagnosis_input_snapshot_manifest(snapshot)

    def persist_foresight_input_snapshot(
        self,
        *,
        snapshot: ForesightInputSnapshot,
    ) -> Mapping[str, Any]:
        """Persist one immutable Foresight input manifest without stage execution."""

        self._validate_foresight_input_snapshot(snapshot)
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO foresight_input_snapshots (
                    snapshot_id, run_id, stage_name, attempt, contract_version,
                    input_hash, manifest
                ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT DO NOTHING
                RETURNING snapshot_id, run_id, stage_name, attempt,
                          contract_version, input_hash, manifest
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.run_id,
                    snapshot.stage_name,
                    snapshot.attempt,
                    snapshot.contract_version,
                    snapshot.input_hash,
                    _json(snapshot.manifest),
                ),
            )
            stored = _mapping(cursor.fetchone())
            if stored is None:
                cursor.execute(
                    """
                    SELECT snapshot_id, run_id, stage_name, attempt,
                           contract_version, input_hash, manifest
                    FROM foresight_input_snapshots
                    WHERE snapshot_id = %s
                       OR (run_id = %s AND stage_name = %s AND attempt = %s)
                    ORDER BY snapshot_id = %s DESC
                    LIMIT 1
                    """,
                    (
                        snapshot.snapshot_id,
                        snapshot.run_id,
                        snapshot.stage_name,
                        snapshot.attempt,
                        snapshot.snapshot_id,
                    ),
                )
                stored = _mapping(cursor.fetchone())
                if stored is None:
                    raise RuntimeError(
                        "Idempotent Foresight input snapshot disappeared before it could be read."
                    )
                expected = {
                    "snapshot_id": snapshot.snapshot_id,
                    "run_id": snapshot.run_id,
                    "stage_name": snapshot.stage_name,
                    "attempt": snapshot.attempt,
                    "contract_version": snapshot.contract_version,
                    "input_hash": snapshot.input_hash,
                    "manifest": dict(snapshot.manifest),
                }
                if any(stored.get(key) != value for key, value in expected.items()):
                    raise ValueError(
                        "Foresight input snapshot already exists for different immutable input."
                    )
            return stored

    def load_foresight_input_snapshot(
        self,
        *,
        run_id: str,
        stage_name: str,
        attempt: int,
    ) -> ForesightInputSnapshot:
        """Load and validate a persisted Foresight input manifest."""

        if not run_id or stage_name != "foresight" or attempt < 1:
            raise ValueError("run_id, stage_name='foresight', and a positive attempt are required.")
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT snapshot_id, run_id, stage_name, attempt,
                       contract_version, input_hash, manifest
                FROM foresight_input_snapshots
                WHERE run_id = %s AND stage_name = %s AND attempt = %s
                """,
                (run_id, stage_name, attempt),
            )
            row = _mapping(cursor.fetchone())
        if row is None:
            raise KeyError(f"Unknown Foresight input snapshot: {run_id}/{stage_name}/{attempt}")
        snapshot = ForesightInputSnapshot(
            snapshot_id=str(row["snapshot_id"]),
            run_id=str(row["run_id"]),
            stage_name=str(row["stage_name"]),
            attempt=int(row["attempt"]),
            contract_version=str(row["contract_version"]),
            input_hash=str(row["input_hash"]),
            manifest=_foresight_manifest(row.get("manifest")),
        )
        self._validate_foresight_input_snapshot(snapshot)
        return snapshot

    def load_verified_foresight_inputs(
        self,
        *,
        run_id: str,
        attempt: int,
    ) -> tuple[ForesightInputSnapshot, ScanCheckpoint]:
        """Hydrate only the Diagnosis checkpoint that exactly matches the manifest."""

        snapshot = self.load_foresight_input_snapshot(
            run_id=run_id,
            stage_name="foresight",
            attempt=attempt,
        )
        diagnosis_ref = snapshot.manifest.get("diagnosis_checkpoint_ref")
        if not isinstance(diagnosis_ref, Mapping):
            raise ForesightInputSnapshotIntegrityError(
                "Foresight input snapshot is missing its Diagnosis checkpoint ref."
            )
        try:
            diagnosis_attempt = int(diagnosis_ref.get("attempt"))
        except (TypeError, ValueError) as error:
            raise ForesightInputSnapshotIntegrityError(
                "Foresight input snapshot Diagnosis checkpoint attempt is invalid."
            ) from error
        checkpoint = self.load_checkpoint(
            run_id=run_id,
            stage_name="diagnosis",
            attempt=diagnosis_attempt,
        )
        if checkpoint is None:
            raise ForesightInputSnapshotIntegrityError(
                f"Unknown Diagnosis checkpoint for Foresight input: {run_id}/diagnosis/{diagnosis_attempt}"
            )
        from scan_foresight_snapshot import assert_foresight_input_snapshot_matches

        assert_foresight_input_snapshot_matches(
            snapshot,
            diagnosis_checkpoint=checkpoint,
        )
        return snapshot, checkpoint

    def _validate_foresight_input_snapshot(self, snapshot: ForesightInputSnapshot) -> None:
        if not all(
            (
                snapshot.snapshot_id,
                snapshot.run_id,
                snapshot.contract_version,
                snapshot.input_hash,
            )
        ):
            raise ForesightInputSnapshotIntegrityError(
                "Foresight input snapshot identity fields are required."
            )
        if snapshot.stage_name != "foresight" or snapshot.attempt < 1:
            raise ForesightInputSnapshotIntegrityError(
                "Foresight input snapshot must bind one positive foresight attempt."
            )
        if not _is_sha256_digest(snapshot.input_hash):
            raise ForesightInputSnapshotIntegrityError(
                "Foresight input snapshot hash must be a SHA-256 digest."
            )
        if not isinstance(snapshot.manifest, Mapping) or not _json_safe(snapshot.manifest):
            raise ForesightInputSnapshotIntegrityError(
                "Foresight input manifest must be a JSON-safe mapping."
            )
        from scan_foresight_snapshot import validate_foresight_input_snapshot_manifest

        validate_foresight_input_snapshot_manifest(snapshot)

    def claim_next_step(
        self,
        *,
        lease_owner: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> StepLease | None:
        """Atomically lease one eligible step with a new fencing token.

        A PostgreSQL transaction advisory lock serializes claims.  The query
        keeps the single running run active until it has no valid active lease;
        only then may an expired step be reclaimed or a queued run begin.
        """

        if not lease_owner:
            raise ValueError("lease_owner is required.")
        current_time = now or self._clock()
        expires_at = current_time + timedelta(seconds=max(1, lease_seconds))
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                WITH supervisor_lock AS (
                    SELECT pg_try_advisory_xact_lock(7419001) AS acquired
                ),
                active_lease AS (
                    SELECT s.run_id
                    FROM scan_steps s
                    JOIN scan_runs r ON r.run_id = s.run_id
                    WHERE r.status = 'running'
                      AND s.status IN ('leased', 'running')
                      AND s.lease_expires_at > %s
                    LIMIT 1
                ),
                active_run AS (
                    SELECT r.run_id
                    FROM scan_runs r
                    WHERE r.status = 'running'
                    ORDER BY r.created_at, r.run_id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                ),
                candidate AS (
                    SELECT s.run_id, s.stage_name, s.attempt
                    FROM scan_steps s
                    JOIN scan_runs r ON r.run_id = s.run_id
                    CROSS JOIN supervisor_lock lock_state
                    WHERE lock_state.acquired
                      AND NOT EXISTS (SELECT 1 FROM active_lease)
                      AND (
                        r.run_id = (SELECT run_id FROM active_run)
                        OR (
                            NOT EXISTS (SELECT 1 FROM active_run)
                            AND r.status = 'queued'
                        )
                      )
                      AND (
                        s.status IN ('pending', 'retryable_failed')
                        OR (
                            s.status IN ('leased', 'running')
                            AND s.lease_expires_at <= %s
                        )
                      )
                    ORDER BY r.created_at, r.run_id, s.stage_order, s.attempt
                    FOR UPDATE OF s SKIP LOCKED
                    LIMIT 1
                ),
                claimed_step AS (
                    UPDATE scan_steps s
                    SET status = 'leased',
                        lease_owner = %s,
                        lease_expires_at = %s,
                        heartbeat_at = %s,
                        fencing_token = s.fencing_token + 1,
                        updated_at = %s
                    FROM candidate c
                    WHERE s.run_id = c.run_id
                      AND s.stage_name = c.stage_name
                      AND s.attempt = c.attempt
                    RETURNING s.run_id, s.stage_name, s.attempt,
                              s.lease_owner, s.fencing_token, s.lease_expires_at
                )
                UPDATE scan_runs r
                SET status = 'running',
                    current_stage = c.stage_name,
                    updated_at = %s
                FROM claimed_step c
                WHERE r.run_id = c.run_id
                RETURNING c.run_id, c.stage_name, c.attempt,
                          c.lease_owner, c.fencing_token, c.lease_expires_at
                """,
                (
                    current_time,
                    current_time,
                    lease_owner,
                    expires_at,
                    current_time,
                    current_time,
                    current_time,
                ),
            )
            row = _mapping(cursor.fetchone())
            if row is None:
                return None
            cursor.execute(
                """
                INSERT INTO scan_events (run_id, stage_name, attempt, event_type, details)
                VALUES (%s, %s, %s, 'step_leased', %s::jsonb)
                """,
                (
                    row["run_id"],
                    row["stage_name"],
                    row["attempt"],
                    _json({"lease_owner": lease_owner, "fencing_token": row["fencing_token"]}),
                ),
            )
            return StepLease(
                run_id=str(row["run_id"]),
                stage_name=str(row["stage_name"]),
                attempt=int(row["attempt"]),
                lease_owner=str(row["lease_owner"]),
                fencing_token=int(row["fencing_token"]),
                lease_expires_at=row["lease_expires_at"],
            )

    def heartbeat(self, lease: StepLease, *, lease_seconds: int, now: datetime | None = None) -> bool:
        """Extend only the current owner's still-valid lease."""

        current_time = now or self._clock()
        expires_at = current_time + timedelta(seconds=max(1, lease_seconds))
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE scan_steps
                SET heartbeat_at = %s, lease_expires_at = %s, updated_at = %s
                WHERE run_id = %s
                  AND stage_name = %s
                  AND attempt = %s
                  AND status IN ('leased', 'running')
                  AND lease_owner = %s
                  AND fencing_token = %s
                  AND lease_expires_at > %s
                """,
                (
                    current_time,
                    expires_at,
                    current_time,
                    lease.run_id,
                    lease.stage_name,
                    lease.attempt,
                    lease.lease_owner,
                    lease.fencing_token,
                    current_time,
                ),
            )
            return cursor.rowcount == 1

    def complete_step(
        self,
        lease: StepLease,
        *,
        output_hash: str,
        checkpoint_ref: str | None,
        gate_result: Mapping[str, Any],
        next_stage: str | None,
        now: datetime | None = None,
    ) -> None:
        """Complete a step with a pre-existing checkpoint reference."""

        current_time = now or self._clock()
        with self._transaction() as connection, connection.cursor() as cursor:
            self._complete_step_on_cursor(
                cursor,
                lease,
                output_hash=output_hash,
                checkpoint_ref=checkpoint_ref,
                gate_result=gate_result,
                next_stage=next_stage,
                current_time=current_time,
            )

    def complete_step_with_checkpoint(
        self,
        lease: StepLease,
        *,
        checkpoint: ScanCheckpoint,
        next_stage: str | None,
        now: datetime | None = None,
    ) -> None:
        """Atomically persist one immutable checkpoint and complete its lease."""

        self._validate_checkpoint(lease, checkpoint)
        current_time = now or self._clock()
        with self._transaction() as connection, connection.cursor() as cursor:
            self._insert_checkpoint_on_cursor(cursor, checkpoint)
            self._complete_step_on_cursor(
                cursor,
                lease,
                output_hash=checkpoint.output_hash,
                checkpoint_ref=checkpoint.checkpoint_ref,
                gate_result=checkpoint.gate_result,
                next_stage=next_stage,
                current_time=current_time,
            )

    def load_checkpoint(
        self,
        *,
        run_id: str,
        stage_name: str,
        attempt: int,
    ) -> ScanCheckpoint | None:
        """Load one persisted checkpoint, or None if that attempt has no row."""

        if not run_id or not stage_name or attempt < 1:
            raise ValueError("run_id, stage_name, and a positive attempt are required.")
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT checkpoint_id, run_id, stage_name, attempt, contract_version,
                       input_hash, output_hash, gate_result, snapshot
                FROM scan_checkpoints
                WHERE run_id = %s AND stage_name = %s AND attempt = %s
                """,
                (run_id, stage_name, attempt),
            )
            row = _mapping(cursor.fetchone())
        if row is None:
            return None
        gate_result = row.get("gate_result")
        snapshot = row.get("snapshot")
        if not isinstance(gate_result, Mapping) or not isinstance(snapshot, Mapping):
            raise ValueError("persisted checkpoint gate result and snapshot must be mappings.")
        return ScanCheckpoint(
            checkpoint_id=str(row["checkpoint_id"]),
            run_id=str(row["run_id"]),
            stage_name=str(row["stage_name"]),
            attempt=int(row["attempt"]),
            contract_version=str(row["contract_version"]),
            input_hash=str(row["input_hash"]),
            output_hash=str(row["output_hash"]),
            gate_result=dict(gate_result),
            snapshot=dict(snapshot),
        )

    def _validate_checkpoint(self, lease: StepLease, checkpoint: ScanCheckpoint) -> None:
        if (
            checkpoint.run_id != lease.run_id
            or checkpoint.stage_name != lease.stage_name
            or checkpoint.attempt != lease.attempt
        ):
            raise ValueError("checkpoint identity does not match the leased step.")
        if not checkpoint.checkpoint_id or not checkpoint.contract_version:
            raise ValueError("checkpoint identity and contract version are required.")
        if not _is_sha256_digest(checkpoint.input_hash) or not _is_sha256_digest(
            checkpoint.output_hash
        ):
            raise ValueError("checkpoint hashes must be SHA-256 hex digests.")
        if checkpoint.gate_result.get("status") != "passed":
            raise ValueError("only a passed gate may complete a step checkpoint.")
        if not isinstance(checkpoint.snapshot, Mapping):
            raise ValueError("checkpoint snapshot must be a mapping.")

    def _insert_checkpoint_on_cursor(
        self,
        cursor: CursorLike,
        checkpoint: ScanCheckpoint,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO scan_checkpoints (
                checkpoint_id, run_id, stage_name, attempt, contract_version,
                input_hash, output_hash, gate_result, snapshot
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
            """,
            (
                checkpoint.checkpoint_id,
                checkpoint.run_id,
                checkpoint.stage_name,
                checkpoint.attempt,
                checkpoint.contract_version,
                checkpoint.input_hash,
                checkpoint.output_hash,
                _json(checkpoint.gate_result),
                _json(checkpoint.snapshot),
            ),
        )

    def _complete_step_on_cursor(
        self,
        cursor: CursorLike,
        lease: StepLease,
        *,
        output_hash: str,
        checkpoint_ref: str | None,
        gate_result: Mapping[str, Any],
        next_stage: str | None,
        current_time: datetime,
    ) -> None:
        cursor.execute(
            """
            UPDATE scan_steps
            SET status = 'completed',
                output_hash = %s,
                checkpoint_ref = %s,
                gate_result = %s::jsonb,
                completed_at = %s,
                updated_at = %s
            WHERE run_id = %s
              AND stage_name = %s
              AND attempt = %s
              AND status IN ('leased', 'running')
              AND lease_owner = %s
              AND fencing_token = %s
              AND lease_expires_at > %s
            """,
            (
                output_hash,
                checkpoint_ref,
                _json(gate_result),
                current_time,
                current_time,
                lease.run_id,
                lease.stage_name,
                lease.attempt,
                lease.lease_owner,
                lease.fencing_token,
                current_time,
            ),
        )
        if cursor.rowcount != 1:
            raise StaleLeaseError(
                f"Lease lost before completing {lease.run_id}/{lease.stage_name} attempt {lease.attempt}."
            )

        if next_stage:
            cursor.execute(
                """
                INSERT INTO scan_steps (run_id, stage_name, attempt, status)
                VALUES (%s, %s, 1, 'pending')
                ON CONFLICT (run_id, stage_name, attempt) DO NOTHING
                """,
                (lease.run_id, next_stage),
            )
            cursor.execute(
                """
                UPDATE scan_runs
                SET status = 'running', current_stage = %s, updated_at = %s
                WHERE run_id = %s
                """,
                (next_stage, current_time, lease.run_id),
            )
        else:
            cursor.execute(
                """
                UPDATE scan_runs
                SET status = 'completed', current_stage = NULL, completed_at = %s, updated_at = %s
                WHERE run_id = %s
                """,
                (current_time, current_time, lease.run_id),
            )

        cursor.execute(
            """
            INSERT INTO scan_events (run_id, stage_name, attempt, event_type, details)
            VALUES (%s, %s, %s, 'step_completed', %s::jsonb)
            """,
            (
                lease.run_id,
                lease.stage_name,
                lease.attempt,
                _json({"output_hash": output_hash, "next_stage": next_stage}),
            ),
        )

    def fail_step(
        self,
        lease: StepLease,
        *,
        retryable: bool,
        reason: str,
        now: datetime | None = None,
    ) -> None:
        """Record a failure without letting a stale attempt alter operational state."""

        current_time = now or self._clock()
        step_status = "retryable_failed" if retryable else "terminal_failed"
        run_status = "running" if retryable else "failed"
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE scan_steps
                SET status = %s,
                    failure_reason = %s,
                    completed_at = %s,
                    updated_at = %s
                WHERE run_id = %s
                  AND stage_name = %s
                  AND attempt = %s
                  AND status IN ('leased', 'running')
                  AND lease_owner = %s
                  AND fencing_token = %s
                  AND lease_expires_at > %s
                """,
                (
                    step_status,
                    reason[:2000],
                    current_time,
                    current_time,
                    lease.run_id,
                    lease.stage_name,
                    lease.attempt,
                    lease.lease_owner,
                    lease.fencing_token,
                    current_time,
                ),
            )
            if cursor.rowcount != 1:
                raise StaleLeaseError(
                    f"Lease lost before failing {lease.run_id}/{lease.stage_name} attempt {lease.attempt}."
                )
            cursor.execute(
                """
                UPDATE scan_runs
                SET status = %s,
                    current_stage = CASE WHEN %s = 'failed' THEN NULL ELSE current_stage END,
                    completed_at = CASE WHEN %s = 'failed' THEN %s ELSE completed_at END,
                    updated_at = %s
                WHERE run_id = %s
                """,
                (run_status, run_status, run_status, current_time, current_time, lease.run_id),
            )
            cursor.execute(
                """
                INSERT INTO scan_events (run_id, stage_name, attempt, event_type, details)
                VALUES (%s, %s, %s, 'step_failed', %s::jsonb)
                """,
                (
                    lease.run_id,
                    lease.stage_name,
                    lease.attempt,
                    _json({"retryable": retryable, "reason": reason[:2000]}),
                ),
            )
