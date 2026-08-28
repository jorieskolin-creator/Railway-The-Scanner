"""Phase 3 contracts for deterministic scan-material preparation and persistence."""

from __future__ import annotations

import os
import sys

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from scan_materials import MATERIAL_PARSER_VERSION, prepare_scan_materials  # noqa: E402
from scan_supervisor import ScanMaterial, ScanRepository  # noqa: E402


class RecordingCursor:
    def __init__(self, rows):
        self.rows = list(rows)
        self.executed = []
        self.rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def execute(self, query, parameters=None):
        self.executed.append((query, parameters))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None


class RecordingConnection:
    def __init__(self, cursor):
        self.cursor_value = cursor
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def _material() -> ScanMaterial:
    return ScanMaterial(
        material_id="MAT-001",
        origin="uploaded_file",
        filename="brief.txt",
        media_type="text/plain",
        byte_size=12,
        content_sha256="a" * 64,
        extracted_text_sha256="b" * 64,
        parser_version=MATERIAL_PARSER_VERSION,
        extraction_status="extracted",
        extraction_warnings=(),
        allowed_use_classification="company_provided_context",
        extracted_text="hello world!",
    )


def test_preparation_is_deterministic_and_does_not_retain_raw_upload_bytes():
    first = prepare_scan_materials(
        run_id="RUN-001",
        report_text="Submitted context",
        uploads=[{"filename": "brief.txt", "content": b"hello world!"}],
    )
    second = prepare_scan_materials(
        run_id="RUN-001",
        report_text="Submitted context",
        uploads=[{"filename": "brief.txt", "content": b"hello world!"}],
    )

    assert first == second
    assert len(first.materials) == 2
    report, upload = first.materials
    assert report.origin == "submitted_report_text"
    assert upload.origin == "uploaded_file"
    assert upload.media_type == "text/plain"
    assert upload.byte_size == 12
    assert upload.parser_version == MATERIAL_PARSER_VERSION
    assert upload.extracted_text == "hello world!"
    assert upload.content_sha256 != upload.extracted_text_sha256
    assert not hasattr(upload, "raw_content")


def test_failed_parser_is_explicit_and_does_not_fabricate_extracted_text():
    result = prepare_scan_materials(
        run_id="RUN-001",
        report_text="",
        uploads=[{"filename": "broken.pdf", "content": b"not a PDF"}],
    )

    material = result.materials[0]
    assert material.extraction_status == "failed"
    assert material.extracted_text == ""
    assert material.extraction_warnings == ("parser_failed:PdfStreamError",)


def test_repository_persists_hashes_and_text_but_never_raw_bytes():
    material = _material()
    row = {
        "material_id": material.material_id,
        "run_id": "RUN-001",
        "content_sha256": material.content_sha256,
        "extracted_text_sha256": material.extracted_text_sha256,
        "parser_version": material.parser_version,
        "extraction_status": material.extraction_status,
        "allowed_use_classification": material.allowed_use_classification,
    }
    cursor = RecordingCursor([row])
    connection = RecordingConnection(cursor)
    repository = ScanRepository(
        "postgresql://scanner:test@localhost/scanner",
        connection_factory=lambda _dsn: connection,
    )

    persisted = repository.persist_materials(run_id="RUN-001", materials=[material])

    assert persisted == [row]
    assert connection.committed is True
    sql, parameters = cursor.executed[0]
    assert "insert into scan_materials" in " ".join(sql.lower().split())
    assert material.extracted_text in parameters
    assert all(not isinstance(value, bytes) for value in parameters)
    assert connection.closed is True


def test_repository_rejects_reuse_of_a_material_id_for_different_immutable_content():
    material = _material()
    conflicting = {
        "material_id": material.material_id,
        "run_id": "RUN-001",
        "content_sha256": "c" * 64,
        "extracted_text_sha256": material.extracted_text_sha256,
        "parser_version": material.parser_version,
        "extraction_status": material.extraction_status,
        "allowed_use_classification": material.allowed_use_classification,
    }
    cursor = RecordingCursor([None, conflicting])
    connection = RecordingConnection(cursor)
    repository = ScanRepository(
        "postgresql://scanner:test@localhost/scanner",
        connection_factory=lambda _dsn: connection,
    )

    with pytest.raises(ValueError, match="different immutable material content"):
        repository.persist_materials(run_id="RUN-001", materials=[material])

    assert connection.rolled_back is True
