"""Deterministic, inactive Scan ingress preparation for Phase 3.

The caller supplies an idempotency key.  That key deterministically identifies
the future run and its material records, allowing safe retry before any worker
is allowed to execute cognitive stages.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from scan_materials import prepare_scan_materials
from scan_supervisor import ScanMaterial


SCAN_INGRESS_CONTRACT_VERSION = "scanner-ingress/v1"
COGNITIVE_CONTRACT_VERSION = "scanner-cognitive/v1"
MATERIAL_INGESTION_STAGE = "material_ingestion"


@dataclass(frozen=True)
class PreparedScanIngress:
    run_id: str
    company_name: str
    company_url: str
    idempotency_key: str
    request_hash: str
    cognitive_contract_version: str
    initial_stage: str
    save_to_database: bool
    materials: tuple[ScanMaterial, ...]


def prepare_scan_ingress(
    *,
    company_name: str,
    company_url: str | None,
    report_text: str | None,
    save_to_database: bool,
    uploads: Sequence[Mapping[str, Any]] | None,
    idempotency_key: str,
) -> PreparedScanIngress:
    """Validate and deterministically prepare a non-executing scan ingress."""

    normalized_company_name = str(company_name or "").strip()
    normalized_company_url = str(company_url or "").strip()
    normalized_report_text = str(report_text or "")
    normalized_idempotency_key = str(idempotency_key or "").strip()
    if not normalized_company_name:
        raise ValueError("company_name is required.")
    if not normalized_idempotency_key:
        raise ValueError("idempotency_key is required.")
    if type(save_to_database) is not bool:
        raise ValueError("save_to_database must be a boolean.")

    run_id = "RUN-" + _sha256(
        f"{SCAN_INGRESS_CONTRACT_VERSION}\0{normalized_idempotency_key}".encode("utf-8")
    )[:32].upper()
    prepared_materials = prepare_scan_materials(
        run_id=run_id,
        report_text=normalized_report_text,
        uploads=uploads,
    ).materials
    request_hash = _request_hash(
        company_name=normalized_company_name,
        company_url=normalized_company_url,
        report_text=normalized_report_text,
        save_to_database=save_to_database,
        materials=prepared_materials,
    )
    return PreparedScanIngress(
        run_id=run_id,
        company_name=normalized_company_name,
        company_url=normalized_company_url,
        idempotency_key=normalized_idempotency_key,
        request_hash=request_hash,
        cognitive_contract_version=COGNITIVE_CONTRACT_VERSION,
        initial_stage=MATERIAL_INGESTION_STAGE,
        save_to_database=save_to_database,
        materials=prepared_materials,
    )


def _request_hash(
    *,
    company_name: str,
    company_url: str,
    report_text: str,
    save_to_database: bool,
    materials: Sequence[ScanMaterial],
) -> str:
    payload = {
        "contract": SCAN_INGRESS_CONTRACT_VERSION,
        "company_name": company_name,
        "company_url": company_url,
        "report_text_sha256": _sha256(report_text.encode("utf-8")),
        "save_to_database": save_to_database,
        "materials": [
            {
                "origin": material.origin,
                "filename": material.filename,
                "media_type": material.media_type,
                "byte_size": material.byte_size,
                "content_sha256": material.content_sha256,
                "extracted_text_sha256": material.extracted_text_sha256,
                "parser_version": material.parser_version,
                "extraction_status": material.extraction_status,
                "extraction_warnings": list(material.extraction_warnings),
                "allowed_use_classification": material.allowed_use_classification,
            }
            for material in materials
        ],
    }
    return _sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    )


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
