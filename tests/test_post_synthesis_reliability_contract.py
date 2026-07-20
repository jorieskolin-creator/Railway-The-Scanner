"""Static regression checks for the governed post-synthesis recovery contract."""

from __future__ import annotations

import ast
import re
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


def _pitch_contract_functions() -> dict:
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    wanted_functions = {"_pitch_contract_issues", "_pitch_contract_severity"}
    wanted_assignments = {"_PITCH_INTERNAL_LANGUAGE"}
    namespace = {"re": re}
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = {target.id for target in node.targets if isinstance(target, ast.Name)}
            if names.intersection(wanted_assignments):
                nodes.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in wanted_functions:
            nodes.append(node)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(MAIN), "exec"), namespace, namespace)
    return namespace


def _claim_correction_functions() -> dict:
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    wanted_functions = {
        "_publishable_fact_check_correction",
        "_remove_sentence_containing_claim",
        "_final_public_language_qa",
        "_replace_flagged_claim",
    }
    wanted_assignments = {
        "_EDITORIAL_CORRECTION_RE",
        "_EDITORIAL_CORRECTION_FRAGMENT_RE",
    }
    namespace = {"re": re}
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = {target.id for target in node.targets if isinstance(target, ast.Name)}
            if names.intersection(wanted_assignments):
                nodes.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in wanted_functions:
            nodes.append(node)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(MAIN), "exec"), namespace, namespace)
    return namespace


def test_anthropic_activation_schema_avoids_unsupported_array_limits():
    schemas = _activation_schemas()
    pair_array = schemas["_ACTIVATION_OUTPUT_SCHEMA"]["properties"]["activation_pairs"]

    assert "minItems" not in pair_array
    assert "maxItems" not in pair_array

    def visit(value):
        if isinstance(value, dict):
            assert "minItems" not in value
            assert "maxItems" not in value
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(schemas["_ACTIVATION_OUTPUT_SCHEMA"])


def test_diagnosis_sonnet_fallback_is_not_conditioned_on_gpt_availability():
    source = MAIN.read_text(encoding="utf-8")

    assert "if not response_text and _use_sonnet" in source
    assert "Provider availability must not suppress it" in source


def test_detailed_report_requires_final_recheck_after_repair():
    source = MAIN.read_text(encoding="utf-8")

    assert "Detailed Forensic Report (final repaired text)" in source
    assert "verification_only=True" in source
    assert '"verified_after_removals"' in source
    assert '"generated_with_removals"' not in source


def test_synthesis_only_activation_has_distinct_failure_status():
    source = MAIN.read_text(encoding="utf-8")

    assert '"synthesis_complete_activation_failed"' in source
    assert '2 <= len(activation_pairs) <= 3' in source
    assert 'pitch != "Pitch Unavailable"' in source


def test_pitch_has_post_parse_contract_and_pitch_only_recovery():
    source = MAIN.read_text(encoding="utf-8")

    assert "def _pitch_contract_issues" in source
    assert "def _pitch_contract_severity" in source
    assert "if not 90 <= word_count <= 110" in source
    assert 'if len(paragraphs) != 3' in source
    assert "scanner_pitch_only_recovery" in source
    assert "Do not regenerate, alter," in source
    assert '"completed_with_pitch_warning"' in source
    assert '"retained_with_warning"' in source
    assert 'reasoning_effort="low"' in source


def test_safe_113_word_pitch_is_soft_warning_not_blocking():
    namespace = _pitch_contract_functions()
    paragraph_a = " ".join(["value"] * 38) + "."
    paragraph_b = " ".join(["progress"] * 38) + "."
    paragraph_c = " ".join(["outcome"] * 37) + "."
    pitch = "\n\n".join([paragraph_a, paragraph_b, paragraph_c])

    severity = namespace["_pitch_contract_severity"](pitch)

    assert severity["hard"] == []
    assert severity["soft"] == ["word_count:113"]


def test_internal_pitch_language_remains_blocking():
    namespace = _pitch_contract_functions()
    pitch = "The Scanner found a proof gap.\n\nCustomer value remains possible.\n\nValidate the outcome carefully."

    severity = namespace["_pitch_contract_severity"](pitch)

    assert any(issue.startswith("internal_evidence_language:") for issue in severity["hard"])


def test_detailed_report_has_bounded_multi_cycle_recovery_and_specific_statuses():
    source = MAIN.read_text(encoding="utf-8")

    assert "for repair_cycle in range(1, 4)" in source
    assert source.index("correction = str(issue.get") < source.index("remaining_before_removal =")
    assert '"unresolved_blocking_claims"' in source
    assert '"verification_not_approved"' in source
    assert '"repair_cycles": verification_history' in source


def test_pitch_scoped_audit_findings_are_customer_safe_blocking_actions():
    reality_check = (ROOT / "backend" / "reality_check.py").read_text(encoding="utf-8")

    assert 'pitch_scoped = any(str(path).startswith("pitch_text")' in reality_check
    assert 'issue_type = "pitch_customer_safe_overstatement"' in reality_check


def test_foresight_misattribution_uses_citation_only_correction():
    source = MAIN.read_text(encoding="utf-8")

    assert "def _apply_foresight_citation_correction" in source
    assert 'data["strategic_synthesis"] = synthesis[:match.start()] + replacement' in source


def test_reader_does_not_render_unavailable_placeholders_as_analysis():
    source = COMPANY_SUMMARY.read_text(encoding="utf-8")

    assert "data?.summary_text !== 'AI Summary Unavailable'" in source
    assert "data?.pitch_text !== 'Pitch Unavailable'" in source


def test_general_summary_badge_matches_final_verification_state():
    source = COMPANY_SUMMARY.read_text(encoding="utf-8")

    assert "unverified wording remains" in source
    assert "verification unavailable" in source
    assert "unverified claims removed" not in source


def test_editorial_verifier_command_is_removed_instead_of_published():
    namespace = _claim_correction_functions()
    original = (
        "The company has a record order backlog. "
        "It is pursuing active capacity expansion. "
        "Its product portfolio is modular."
    )

    corrected = namespace["_replace_flagged_claim"](
        original,
        "active capacity expansion",
        "Remove or qualify as unverified; the evidence does not establish this initiative.",
    )

    assert "Remove or qualify" not in corrected
    assert "active capacity expansion" not in corrected
    assert "record order backlog" in corrected
    assert "product portfolio is modular" in corrected


def test_final_public_language_qa_preserves_paragraphs():
    namespace = _claim_correction_functions()
    text = (
        "First analytical paragraph remains complete.\n\n"
        "Remove or qualify as unverified; this is an editorial instruction.\n\n"
        "Third analytical paragraph remains complete."
    )

    cleaned = namespace["_final_public_language_qa"](text)

    assert "Remove or qualify" not in cleaned
    assert cleaned.count("\n\n") == 1
    assert cleaned.startswith("First analytical paragraph")
    assert cleaned.endswith("Third analytical paragraph remains complete.")

    synthesis = "\n\n".join(f"Paragraph {index} remains complete." for index in range(1, 6))
    assert namespace["_final_public_language_qa"](synthesis).count("\n\n") == 4
    assert namespace["_final_public_language_qa"]("Remove delivery bottlenecks safely.") == "Remove delivery bottlenecks safely."


def test_company_summary_always_renders_excluded_claims_section():
    source = COMPANY_SUMMARY.read_text(encoding="utf-8")

    assert "Claims Excluded From Analysis" in source
    assert "N/A - no excluded claims." in source
    assert "Blocking Issues" not in source
