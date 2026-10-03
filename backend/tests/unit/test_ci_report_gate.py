"""Synthetic report fixtures exercise the release gate, never product answers."""
import importlib.util
import json
from pathlib import Path

import pytest


def gate():
    path = Path(__file__).resolve().parents[3] / "scripts/ci/check_reports.py"
    assert path.is_file(), "machine report gate is required before publishing successful CI summaries"
    spec = importlib.util.spec_from_file_location("ci_report_gate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def xml(tmp_path, body="", *, declared=1, classname="tests.production.test_ticket_progress", counters=True):
    attributes = f'tests="{declared}" failures="0" errors="0" skipped="0"' if counters else 'tests="1"'
    (tmp_path / "result.xml").write_text(f'<testsuites><testsuite name="suite" {attributes}><testcase classname="{classname}" name="synthetic">{body}</testcase></testsuite></testsuites>', encoding="utf-8")


def report(kind="pytest"):
    return {"id": "controlled", "kind": kind, "xml": "result.xml", "json": "result.json",
            "modules": ["tests.production.test_ticket_progress"] if kind == "pytest" else [],
            "projects": ["demo", "production-mock"] if kind == "playwright" else []}


def pw(projects=("demo", "production-mock")):
    return {"errors": [], "stats": {"expected": len(projects), "skipped": 0, "unexpected": 0, "flaky": 0},
        "suites": [{"suites": [{"specs": [{"tests": [{"projectName": project, "status": "expected", "expectedStatus": "passed",
            "results": [{"status": "passed", "retry": 0}]} for project in projects]}]}]}]}


def pw_xml(tmp_path, projects=("demo", "production-mock")):
    suites = ''.join(f'<testsuite name="test.spec.ts" hostname="{project}" tests="1" failures="0" errors="0" skipped="0"><testcase classname="test.spec.ts" name="{project}" /></testsuite>' for project in projects)
    (tmp_path / "result.xml").write_text(f'<testsuites tests="{len(projects)}" failures="0" errors="0" skipped="0">{suites}</testsuites>', encoding="utf-8")


def vitest():
    return {"success": True, "numTotalTests": 1, "numPassedTests": 1, "numFailedTests": 0, "numFailedTestSuites": 0,
        "numPendingTests": 0, "numPendingTestSuites": 0, "numTodoTests": 0,
        "testResults": [{"assertionResults": [{"status": "passed"}]}]}


def save_json(tmp_path, value):
    (tmp_path / "result.json").write_text(json.dumps(value), encoding="utf-8")


def test_valid_pytest_counts_actual_cases_without_double_counting_nested_suites(tmp_path):
    xml(tmp_path)
    result = gate().check_report(report(), tmp_path, "success")
    assert result["verdict"] == "pass" and result["counts"] == {"tests": 1, "passed": 1, "failures": 0, "errors": 0, "skipped": 0}


@pytest.mark.parametrize("kind", ["failure", "error", "skipped"])
def test_actual_bad_cases_are_rejected_even_if_report_declares_zero(tmp_path, kind):
    xml(tmp_path, f'<{kind}>PRIVATE_REPORT_BODY</{kind}>')
    result = gate().check_report(report(), tmp_path, "success")
    assert result["verdict"] == "fail" and "PRIVATE_REPORT_BODY" not in json.dumps(result)


@pytest.mark.parametrize("condition", ["empty", "missing", "malformed", "counter_missing", "counter_conflict", "negative", "missing_module", "process_failure"])
def test_missing_or_dishonest_evidence_never_passes(tmp_path, condition):
    xml(tmp_path, declared=2 if condition == "counter_conflict" else -1 if condition == "negative" else 1,
        classname="other" if condition == "missing_module" else "tests.production.test_ticket_progress", counters=condition != "counter_missing")
    if condition == "missing":
        (tmp_path / "result.xml").unlink()
    elif condition in {"empty", "malformed"}:
        (tmp_path / "result.xml").write_text('<testsuites/>' if condition == "empty" else '<broken PRIVATE_TEXT', encoding="utf-8")
    result = gate().check_report(report(), tmp_path, "failure" if condition == "process_failure" else "success")
    assert result["verdict"] == "fail" and "PRIVATE_TEXT" not in json.dumps(result)


def test_valid_playwright_requires_actual_projects_and_passed_results(tmp_path):
    pw_xml(tmp_path); save_json(tmp_path, pw())
    assert gate().check_report(report("playwright"), tmp_path, "success")["verdict"] == "pass"


@pytest.mark.parametrize("condition", ["config_only", "missing_project", "expected_failure", "flaky", "retry", "interrupted", "error", "xml_mismatch"])
def test_playwright_rejects_skips_expected_failures_retries_and_fake_coverage(tmp_path, condition):
    pw_xml(tmp_path); value = pw()
    tests = value["suites"][0]["suites"][0]["specs"][0]["tests"]
    if condition == "config_only":
        value["config"] = {"projects": [{"name": "demo"}, {"name": "production-mock"}]}; value["suites"] = []
    elif condition == "missing_project":
        value = pw(("demo",)); pw_xml(tmp_path, ("demo",))
    elif condition == "expected_failure":
        tests[0]["expectedStatus"] = "failed"; tests[0]["results"][0]["status"] = "failed"
    elif condition == "flaky":
        value["stats"]["flaky"] = 1; tests[0]["status"] = "flaky"
    elif condition == "retry":
        tests[0]["results"].append({"status": "passed", "retry": 1})
    elif condition == "interrupted":
        tests[0]["results"][0]["status"] = "interrupted"
    elif condition == "error":
        value["errors"] = [{"message": "PRIVATE_CONTEXT"}]
    elif condition == "xml_mismatch":
        pw_xml(tmp_path, ("demo",))
    save_json(tmp_path, value)
    result = gate().check_report(report("playwright"), tmp_path, "success")
    assert result["verdict"] == "fail" and "PRIVATE_CONTEXT" not in json.dumps(result)


def test_valid_vitest_requires_matching_assertions_and_xml(tmp_path):
    xml(tmp_path); save_json(tmp_path, vitest())
    assert gate().check_report(report("vitest"), tmp_path, "success")["verdict"] == "pass"


@pytest.mark.parametrize("condition", ["todo", "pending", "false_success", "missing_assertions", "bad_assertion", "count_mismatch"])
def test_vitest_rejects_incomplete_or_conflicting_results(tmp_path, condition):
    xml(tmp_path); value = vitest()
    if condition in {"todo", "pending"}:
        value["numTodoTests" if condition == "todo" else "numPendingTests"] = 1
    elif condition == "false_success":
        value["success"] = False
    elif condition == "missing_assertions":
        value["testResults"] = []
    elif condition == "bad_assertion":
        value["testResults"][0]["assertionResults"][0]["status"] = "skipped"
    else:
        value["numTotalTests"] = 2; value["numPassedTests"] = 2
    save_json(tmp_path, value)
    assert gate().check_report(report("vitest"), tmp_path, "success")["verdict"] == "fail"


def test_new_source_test_module_cannot_silently_escape_report_contract(tmp_path):
    xml(tmp_path)
    source = tmp_path / "backend/tests/production"
    source.mkdir(parents=True)
    (source / "test_ticket_progress.py").write_text("# Synthetic inventory", encoding="utf-8")
    spec = report() | {"source_globs": ["backend/tests/**/test_*.py"], "source_style": "pytest"}
    assert gate().check_report(spec, tmp_path, "success")["verdict"] == "pass"
    (source / "test_extra.py").write_text("# New tests require explicit contract inclusion", encoding="utf-8")
    result = gate().check_report(spec, tmp_path, "success")
    assert "source_test_inventory_changed" in result["reason_codes"] and result["verdict"] == "fail"


def test_cli_writes_safe_failure_summary_even_when_contract_is_missing(tmp_path, monkeypatch, capsys):
    import sys
    monkeypatch.setenv("GITHUB_SERVER_URL", "")
    output = tmp_path / "public/summary.json"
    monkeypatch.setattr(sys, "argv", ["check_reports.py", "--job", "backend", "--contract", str(tmp_path / "missing.json"),
                                    "--root", str(tmp_path), "--output", str(output)])
    assert gate().main() == 1
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["verdict"] == "fail" and result["reason_codes"] == ["contract_missing_or_invalid"]
    assert str(tmp_path) not in capsys.readouterr().out


@pytest.mark.parametrize("skipped", [False, True])
def test_vitest_wrapper_omits_skipped_but_children_must_count_every_case(tmp_path, skipped):
    xml(tmp_path, '<skipped/>' if skipped else '')
    path = tmp_path / "result.xml"
    text = path.read_text(encoding="utf-8").replace('<testsuites>', '<testsuites tests="1" failures="0" errors="0">')
    if skipped:
        text = text.replace('skipped="0"', 'skipped="1"')
    path.write_text(text, encoding="utf-8"); save_json(tmp_path, vitest())
    result = gate().check_report(report("vitest"), tmp_path, "success")
    assert result["verdict"] == ("fail" if skipped else "pass")


@pytest.mark.parametrize("marker", ["failure", "error", "skipped"])
def test_suite_level_bad_marker_is_rejected_even_when_cases_and_counters_pass(tmp_path, marker):
    xml(tmp_path)
    path = tmp_path / "result.xml"
    path.write_text(path.read_text(encoding="utf-8").replace('<testcase', f'<{marker}>PRIVATE_SUITE_BODY</{marker}><testcase'), encoding="utf-8")
    result = gate().check_report(report(), tmp_path, "success")
    assert result["verdict"] == "fail" and "PRIVATE_SUITE_BODY" not in json.dumps(result)


@pytest.mark.parametrize("structure", ["direct_case", "unknown_node"])
def test_invalid_junit_topology_cannot_bypass_required_suite_counters(tmp_path, structure):
    xml(tmp_path)
    path = tmp_path / "result.xml"
    value = path.read_text(encoding="utf-8")
    if structure == "direct_case":
        value = '<testsuites><testcase classname="tests.production.test_ticket_progress" name="synthetic"/></testsuites>'
    else:
        value = value.replace('<testcase', '<unknown/><testcase')
    path.write_text(value, encoding="utf-8")
    assert gate().check_report(report(), tmp_path, "success")["verdict"] == "fail"


def collected_pytest(tmp_path):
    module = "tests.production.test_ticket_progress"
    value = {"schema_version": 1, "runner": "pytest", "collection_errors": 0, "deselected": 0,
             "cases": [{"classname": module, "name": name} for name in ("one", "two")]}
    (tmp_path / "collected.json").write_text(json.dumps(value), encoding="utf-8")
    return report() | {"collected": "collected.json"}


def named_xml(tmp_path, names, classname="tests.production.test_ticket_progress"):
    cases = ''.join(f'<testcase classname="{classname}" name="{name}"/>' for name in names)
    (tmp_path / "result.xml").write_text(f'<testsuites><testsuite tests="{len(names)}" failures="0" errors="0" skipped="0">{cases}</testsuite></testsuites>', encoding="utf-8")


@pytest.mark.parametrize("condition", ["missing_case", "duplicate_replacement", "deselected", "collection_error"])
def test_all_required_modules_do_not_prove_all_collected_cases_executed(tmp_path, condition):
    spec = collected_pytest(tmp_path)
    named_xml(tmp_path, ["one"] if condition == "missing_case" else ["one", "one"] if condition == "duplicate_replacement" else ["one", "two"])
    if condition in {"deselected", "collection_error"}:
        path = tmp_path / "collected.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["deselected" if condition == "deselected" else "collection_errors"] = 1
        path.write_text(json.dumps(value), encoding="utf-8")
    result = gate().check_report(spec, tmp_path, "success")
    assert result["verdict"] == "fail" and result["missing_modules"] == []


def test_complete_collected_pytest_identities_are_accepted(tmp_path):
    spec = collected_pytest(tmp_path); named_xml(tmp_path, ["one", "two"])
    result = gate().check_report(spec, tmp_path, "success")
    assert result["verdict"] == "pass" and result["counts"]["tests"] == 2


@pytest.mark.parametrize("condition", ["missing_inventory", "collection_failed", "invalid_shape", "duplicate_inventory"])
def test_unreliable_collection_evidence_cannot_authorize_complete_execution(tmp_path, condition):
    spec = collected_pytest(tmp_path); named_xml(tmp_path, ["one", "two"])
    path = tmp_path / "collected.json"
    if condition == "missing_inventory":
        path.unlink()
    elif condition == "invalid_shape":
        path.write_text('[]', encoding="utf-8")
    elif condition == "duplicate_inventory":
        value = json.loads(path.read_text(encoding="utf-8")); value["cases"][1] = value["cases"][0]
        path.write_text(json.dumps(value), encoding="utf-8")
    result = gate().check_report(spec, tmp_path, "success", "failure" if condition == "collection_failed" else "success")
    assert result["verdict"] == "fail"


def test_vitest_same_file_partial_execution_cannot_pass_collected_identity_gate(tmp_path):
    path = tmp_path / "frontend/src/api.test.ts"
    collected = [{"file": str(path), "name": name} for name in ("one", "two")]
    (tmp_path / "collected.json").write_text(json.dumps(collected), encoding="utf-8")
    named_xml(tmp_path, ["one"], "src/api.test.ts")
    value = vitest()
    value["testResults"][0]["name"] = str(path)
    value["testResults"][0]["assertionResults"][0].update(ancestorTitles=[], title="one", fullName="one")
    save_json(tmp_path, value)
    assert gate().check_report(report("vitest") | {"collected": "collected.json"}, tmp_path, "success")["verdict"] == "fail"


def test_playwright_login_only_cannot_replace_collected_production_lifecycle(tmp_path):
    value = pw(("production-live",))
    suite = value["suites"][0]["suites"][0]
    suite.update(title="production-live.spec.ts", file="production-live.spec.ts")
    spec = suite["specs"][0]
    spec.update(id="login-id", title="login", file="production-live.spec.ts")
    collected = json.loads(json.dumps(value))
    login = collected["suites"][0]["suites"][0]["specs"][0]
    login["tests"][0]["results"] = []
    lifecycle = json.loads(json.dumps(login)); lifecycle.update(id="lifecycle-id", title="complete business lifecycle")
    collected["suites"][0]["suites"][0]["specs"].append(lifecycle)
    (tmp_path / "collected.json").write_text(json.dumps(collected), encoding="utf-8")
    (tmp_path / "result.xml").write_text('<testsuites><testsuite name="production-live.spec.ts" hostname="production-live" tests="1" failures="0" errors="0" skipped="0"><testcase classname="production-live.spec.ts" name="[production-live] login"/></testsuite></testsuites>', encoding="utf-8")
    save_json(tmp_path, value)
    result = gate().check_report(report("playwright") | {"projects": ["production-live"], "collected": "collected.json"}, tmp_path, "success")
    assert result["verdict"] == "fail"


def test_pytest_inventory_is_captured_before_execution_and_records_deselection(tmp_path):
    import os
    import subprocess
    import sys
    source = tmp_path / "test_synthetic.py"
    source.write_text('def test_one():\n    assert True\ndef test_two():\n    assert True\n', encoding="utf-8")
    output = tmp_path / "collected.json"
    root = Path(__file__).resolve().parents[3]
    env = os.environ | {"PYTHONPATH": str(root) + os.pathsep + str(root / "backend")}
    command = [sys.executable, "-m", "pytest", str(source), "-q", "-p", "scripts.ci.pytest_inventory",
               "--ci-inventory", str(output), "-k", "test_one"]
    result = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, timeout=30)
    assert result.returncode == 0, "CI collector must run without importing application or production settings"
    value = json.loads(output.read_text(encoding="utf-8"))
    assert value["deselected"] == 1 and len(value["cases"]) == 2


@pytest.mark.parametrize("dirty", [False, True])
def test_github_summary_requires_the_tested_sources_to_match_the_checkout(tmp_path, monkeypatch, dirty):
    import sys
    module = gate()
    spec = collected_pytest(tmp_path); named_xml(tmp_path, ["one", "two"])
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps({"schema_version": 1, "jobs": {"backend": [spec]}}), encoding="utf-8")
    commit = "a" * 40
    monkeypatch.setattr(module, "git_identity", lambda _root: (commit, dirty))
    for key, value in {"GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": "synthetic/fixture",
                       "GITHUB_RUN_ID": "123", "GITHUB_SHA": commit}.items():
        monkeypatch.setenv(key, value)
    output = tmp_path / "summary.json"
    monkeypatch.setattr(sys, "argv", ["check_reports.py", "--contract", str(contract), "--root", str(tmp_path),
        "--job", "backend", "--outcome", "controlled=success", "--outcome", "controlled_collection=success", "--output", str(output)])
    assert module.main() == (1 if dirty else 0)
