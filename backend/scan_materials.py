"""Deterministic preparation of durable Scanner input material.

Material records describe what was submitted and extracted.  They are not
company evidence, do not establish source trust, and are never sent to a model
from this module.  Raw file bytes are intentionally discarded after extraction.
"""

from __future__ import annotations

import hashlib
import io
import mimetypes
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from bs4 import BeautifulSoup
from pypdf import PdfReader

from scan_supervisor import ScanMaterial


MATERIAL_PARSER_VERSION = "scanner-material-extractor/v1"


@dataclass(frozen=True)
class MaterialPreparationResult:
    materials: tuple[ScanMaterial, ...]


def prepare_scan_materials(
    *,
    run_id: str,
    report_text: str,
    uploads: Sequence[Mapping[str, Any]] | None,
) -> MaterialPreparationResult:
    """Extract and hash submitted scan material without retaining raw bytes."""

    if not run_id:
        raise ValueError("run_id is required.")
    materials: list[ScanMaterial] = []

    if report_text:
        raw_text = report_text.encode("utf-8")
        materials.append(
            _material(
                run_id=run_id,
                origin="submitted_report_text",
                ordinal=0,
                filename="submitted_report_text.txt",
                media_type="text/plain; charset=utf-8",
                raw_content=raw_text,
                extracted_text=report_text,
                extraction_status="extracted",
                extraction_warnings=(),
                allowed_use_classification="company_provided_context",
                parser_version="submitted-text/v1",
            )
        )

    for ordinal, upload in enumerate(uploads or (), start=1):
        filename = str(upload.get("filename") or "uploaded_document")
        raw_content = upload.get("content", b"")
        if isinstance(raw_content, bytearray):
            raw_content = bytes(raw_content)
        if not isinstance(raw_content, bytes):
            raise TypeError("uploaded material content must be bytes.")
        media_type = _media_type(filename, upload.get("content_type"))
        extracted_text, extraction_status, warnings = _extract_uploaded_text(
            filename=filename,
            raw_content=raw_content,
        )
        materials.append(
            _material(
                run_id=run_id,
                origin="uploaded_file",
                ordinal=ordinal,
                filename=filename,
                media_type=media_type,
                raw_content=raw_content,
                extracted_text=extracted_text,
                extraction_status=extraction_status,
                extraction_warnings=warnings,
                allowed_use_classification="company_provided_context",
                parser_version=MATERIAL_PARSER_VERSION,
            )
        )

    return MaterialPreparationResult(materials=tuple(materials))


def _material(
    *,
    run_id: str,
    origin: str,
    ordinal: int,
    filename: str,
    media_type: str,
    raw_content: bytes,
    extracted_text: str,
    extraction_status: str,
    extraction_warnings: tuple[str, ...],
    allowed_use_classification: str,
    parser_version: str,
) -> ScanMaterial:
    content_sha256 = _sha256(raw_content)
    extracted_text_sha256 = _sha256(extracted_text.encode("utf-8"))
    material_id = _material_id(
        run_id=run_id,
        origin=origin,
        ordinal=ordinal,
        filename=filename,
        content_sha256=content_sha256,
    )
    return ScanMaterial(
        material_id=material_id,
        origin=origin,
        filename=filename,
        media_type=media_type,
        byte_size=len(raw_content),
        content_sha256=content_sha256,
        extracted_text_sha256=extracted_text_sha256,
        parser_version=parser_version,
        extraction_status=extraction_status,
        extraction_warnings=extraction_warnings,
        allowed_use_classification=allowed_use_classification,
        extracted_text=extracted_text,
    )


def _extract_uploaded_text(
    *,
    filename: str,
    raw_content: bytes,
) -> tuple[str, str, tuple[str, ...]]:
    lower_filename = filename.lower()
    try:
        if lower_filename.endswith(".pdf"):
            reader = PdfReader(io.BytesIO(raw_content))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        elif lower_filename.endswith((".html", ".htm")):
            soup = BeautifulSoup(raw_content, "html.parser")
            for element in soup(["script", "style"]):
                element.decompose()
            text = soup.get_text(separator=" ")
        else:
            try:
                text = raw_content.decode("utf-8")
            except UnicodeDecodeError:
                text = raw_content.decode("utf-8", errors="ignore")
                return text, _extraction_status(text), ("utf8_decode_lossy",)
    except Exception as error:
        return "", "failed", (f"parser_failed:{type(error).__name__}",)

    return text, _extraction_status(text), ()


def _extraction_status(text: str) -> str:
    return "extracted" if text.strip() else "empty"


def _media_type(filename: str, supplied: Any) -> str:
    if isinstance(supplied, str) and supplied.strip():
        return supplied.strip().lower()
    guessed, _encoding = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def _material_id(
    *,
    run_id: str,
    origin: str,
    ordinal: int,
    filename: str,
    content_sha256: str,
) -> str:
    identity = "\0".join((run_id, origin, str(ordinal), filename, content_sha256))
    return "MAT-" + _sha256(identity.encode("utf-8"))[:32].upper()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
