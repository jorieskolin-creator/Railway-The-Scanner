"""Static regression checks for the governed post-synthesis recovery contract."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "backend" / "main.py"
COMPANY_SUMMARY = ROOT / "components" / "CompanySummary.tsx"


def _activation_schemas() -> dict:
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    wanted = {"_ACTIVATION_PAIR_SCHEMA", "_ACTIVATION_OUTPUT_SCHEMA"}
    namespace: dict = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        names = {target.id for target in node.targets if isinstance(target, ast.Name)}
        if not names.intersection(wanted):
            continue
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(MAIN), "exec"), {}, namespace)
    return namespace


def test_anthropic_activation_schema_uses_supported_array_minimum():
    schemas = _activation_schemas()
    pair_array = schemas["_ACTIVATION_OUTPUT_SCHEMA"]["properties"]["activation_pairs"]

    assert pair_array["minItems"] == 1
    assert pair_array["maxItems"] == 3


def test_diagnosis_sonnet_fallback_is_not_conditioned_on_gpt_availability():
    source = MAIN.read_text(encoding="utf-8")

    assert "if not response_text and _use_sonnet" in source
    assert "Provider availability must not suppress it" in source


def test_detailed_report_requires_final_recheck_after_repair():
    source = MAIN.read_text(encoding="utf-8")

    assert "Detailed Forensic Report (final repaired text)" in source
    assert '"verified_after_removals"' in source
    assert '"generated_with_removals"' not in source


def test_reader_does_not_render_unavailable_placeholders_as_analysis():
    source = COMPANY_SUMMARY.read_text(encoding="utf-8")

    assert "data?.summary_text !== 'AI Summary Unavailable'" in source
    assert "data?.pitch_text !== 'Pitch Unavailable'" in source
