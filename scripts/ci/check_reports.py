"""Fail closed on incomplete test evidence; publish counts and hashes only."""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]


def integer(value):
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        return int(value)
    raise ValueError("invalid counter")


def counts(cases):
    result = {"tests": len(cases), **{key: sum(case.find(tag) is not None for case in cases)
              for key, tag in (("failures", "failure"), ("errors", "error"), ("skipped", "skipped"))}}
    return result | {"passed": result["tests"] - result["failures"] - result["errors"] - result["skipped"]}


def read_bytes(path):
    if path.stat().st_size > 50_000_000:
        raise ValueError("report too large")
    return path.read_bytes()


def junit(path):
    root = ET.fromstring(read_bytes(path))
    if root.tag not in {"testsuite", "testsuites"}:
        raise ValueError("invalid root")
    children = {"testsuites": {"testsuite"}, "testsuite": {"testcase", "properties", "system-out", "system-err"},
                "testcase": {"properties", "failure", "error", "skipped", "system-out", "system-err"},
                "properties": {"property"}, "property": set(), "failure": set(), "error": set(),
                "skipped": set(), "system-out": set(), "system-err": set()}
    for node in root.iter():
        if node.tag not in children or any(child.tag not in children[node.tag] for child in node):
            raise ValueError("invalid JUnit topology")
        if node.tag == "testcase" and (not node.get("classname") or not node.get("name")):
            raise ValueError("testcase identity missing")
    cases = list(root.iter("testcase"))
    errors = []
    for node in root.iter():
        if node.tag not in {"testsuite", "testsuites"}:
            continue
        # pytest has a counterless wrapper; Vitest's wrapper omits skipped. Verify
        # every declared aggregate while requiring all four counters on actual suites.
        keys = ("tests", "failures", "errors", "skipped")
        if node.tag == "testsuites":
            keys = tuple(key for key in keys if key in node.attrib)
        actual = counts(list(node.iter("testcase")))
        if any(integer(node.attrib[key]) != actual[key] for key in keys):
            errors.append("xml_counter_conflict")
    projects = Counter()
    for suite in root.iter("testsuite"):
        if suite.get("hostname"):
            projects[suite.get("hostname")] += len(suite.findall("testcase"))
    identities = Counter((case.get("classname", "").replace("\\", "/"), case.get("name")) for case in cases)
    return counts(cases), {item[0] for item in identities}, projects, errors, identities


def playwright_tests(suites):
    for suite in suites:
        for spec in suite.get("specs", []):
            yield from spec.get("tests", [])
        yield from playwright_tests(suite.get("suites", []))


def playwright_identities(suites, file_title=None, ancestors=()):
    for suite in suites:
        current_file, current_ancestors = file_title, ancestors
        if current_file is None and suite.get("file"):
            current_file, current_ancestors = suite["title"], ()
        elif current_file is not None:
            current_ancestors = ancestors + (suite["title"],)
        for spec in suite.get("specs", []):
            for test in spec["tests"]:
                identity = spec["id"], test["projectName"]
                xml_identity = current_file, f'[{test["projectName"]}] ' + " › ".join(current_ancestors + (spec["title"],))
                yield identity, xml_identity, test
        yield from playwright_identities(suite.get("suites", []), current_file, current_ancestors)


def frontend_file(root, filename):
    return Path(filename).resolve().relative_to((root / "frontend").resolve()).as_posix()


def vitest_identities(root, body):
    return Counter((frontend_file(root, file["name"]), " > ".join(item["ancestorTitles"] + [item["title"]]))
                   for file in body["testResults"] for item in file["assertionResults"])


def complete_inventory(spec, root, xml_identities, body, hashes):
    path = root / spec["collected"]
    data = read_bytes(path)
    hashes["collected"] = hashlib.sha256(data).hexdigest()
    collected = json.loads(data)
    if spec["kind"] == "pytest":
        if (collected.get("schema_version") != 1 or collected.get("runner") != "pytest"
                or integer(collected["collection_errors"]) != 0 or integer(collected["deselected"]) != 0):
            raise ValueError("incomplete collection")
        expected = Counter((case["classname"], case["name"]) for case in collected["cases"])
        actual = xml_identities
    elif spec["kind"] == "vitest":
        expected = Counter((frontend_file(root, case["file"]), case["name"]) for case in collected)
        actual = vitest_identities(root, body)
        if actual != xml_identities:
            raise ValueError("JSON/XML identities conflict")
    else:
        entries = list(playwright_identities(collected["suites"]))
        if collected["errors"] or any(test.get("expectedStatus") != "passed" or test.get("results") != []
                                     for _, _, test in entries):
            raise ValueError("incomplete collection")
        expected = Counter(identity for identity, _, _ in entries)
        executed = list(playwright_identities(body["suites"]))
        actual = Counter(identity for identity, _, _ in executed)
        if Counter(xml_identity for _, xml_identity, _ in executed) != xml_identities:
            raise ValueError("JSON/XML identities conflict")
    if (not expected or any(count != 1 for count in expected.values())
            or any(not all(isinstance(part, str) and part for part in identity) for identity in expected)
            or actual != expected):
        raise ValueError("collected testcase identities missing or duplicate")
    return sum(expected.values())


def check_report(spec, root, outcome, collection_outcome=None):
    outcome = outcome if outcome in {"success", "failure", "cancelled", "skipped", "missing"} else "invalid"
    result = {"id": spec["id"], "kind": spec["kind"], "outcome": outcome,
              "classification": spec.get("classification", "controlled test evidence"),
              "counts": None, "hashes": {}, "required_modules": spec.get("modules", []),
              "missing_modules": [], "projects": {}, "reason_codes": []}
    errors = result["reason_codes"]
    if outcome != "success":
        errors.append("test_process_not_successful")
    try:
        path = root / spec["xml"]
        result["hashes"]["xml"] = hashlib.sha256(read_bytes(path)).hexdigest()
        actual, modules, xml_projects, xml_errors, xml_identities = junit(path)
        result["counts"] = actual
        errors.extend(xml_errors)
        if actual["tests"] == 0 or actual["passed"] != actual["tests"]:
            errors.append("xml_not_all_passed")
        result["missing_modules"] = sorted(set(spec.get("modules", [])) - modules)
        if result["missing_modules"]:
            errors.append("required_modules_missing")
        if spec.get("source_globs"):
            source_modules = set()
            for pattern in spec["source_globs"]:
                for path in root.glob(pattern):
                    if spec.get("source_style") == "pytest":
                        source_modules.add(path.relative_to(root / "backend").with_suffix("").as_posix().replace("/", "."))
                    elif spec.get("source_style") == "vitest":
                        source_modules.add(path.relative_to(root / "frontend").as_posix())
            if source_modules != set(spec.get("modules", [])):
                errors.append("source_test_inventory_changed")
        body = None
        if spec["kind"] in {"vitest", "playwright"}:
            data = read_bytes(root / spec["json"])
            result["hashes"]["json"] = hashlib.sha256(data).hexdigest()
            body = json.loads(data)
            if not isinstance(body, dict):
                raise ValueError("JSON object required")
            if spec["kind"] == "vitest":
                assertions = [item for file in body["testResults"] for item in file["assertionResults"]]
                if (body["success"] is not True or any(integer(body[key]) != 0 for key in (
                    "numFailedTests", "numFailedTestSuites", "numPendingTests", "numPendingTestSuites", "numTodoTests"))
                    or integer(body["numTotalTests"]) != actual["tests"]
                    or integer(body["numPassedTests"]) != actual["tests"]
                    or len(assertions) != actual["tests"] or any(item.get("status") != "passed" for item in assertions)):
                    errors.append("vitest_incomplete_or_conflicting")
            else:
                tests = list(playwright_tests(body["suites"]))
                projects = Counter(test["projectName"] for test in tests)
                expected_projects = set(spec["projects"])
                result["projects"] = {name: projects[name] for name in sorted(expected_projects)}
                if set(projects) != expected_projects or set(xml_projects) != expected_projects or projects != xml_projects:
                    errors.append("playwright_projects_missing_or_conflicting")
                if (body["errors"] != [] or integer(body["stats"]["expected"]) != actual["tests"]
                    or any(integer(body["stats"][key]) != 0 for key in ("skipped", "unexpected", "flaky"))
                    or len(tests) != actual["tests"] or any(
                        test.get("status") != "expected" or test.get("expectedStatus") != "passed"
                        or len(test.get("results", [])) != 1 or any(
                            item.get("status") != "passed" or integer(item.get("retry", 0)) != 0
                            for item in test.get("results", [])) for test in tests)):
                    errors.append("playwright_incomplete_or_conflicting")
        elif spec["kind"] != "pytest":
            errors.append("unsupported_report_kind")
        if spec.get("collected"):
            if (collection_outcome if collection_outcome is not None else outcome) != "success":
                errors.append("collection_process_not_successful")
            result["collected_tests"] = complete_inventory(spec, root, xml_identities, body, result["hashes"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ET.ParseError):
        # Never include raw report/parser exception text, stdout, attachments or secrets.
        errors.append("report_missing_or_invalid")
    result["reason_codes"] = sorted(set(errors))
    result["verdict"] = "fail" if errors else "pass"
    return result


def git_identity(root):
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, check=True).stdout)
        return commit if re.fullmatch(r"[a-f0-9]{40}", commit) else None, dirty
    except (OSError, subprocess.SubprocessError):
        return None, None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=ROOT / "scripts/ci/report-contract.json")
    parser.add_argument("--job", required=True)
    parser.add_argument("--outcome", action="append", default=[])
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    commit, dirty = git_identity(root)
    summary = {"schema_version": 1, "job": args.job, "checkout_commit": commit, "worktree_dirty": dirty,
               "github_run_url": None, "reports": [], "reason_codes": [], "verdict": "fail"}
    server, repository, run_id = (os.getenv(key, "") for key in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    if server == "https://github.com" and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) and run_id.isdigit():
        summary["github_run_url"] = f"{server}/{repository}/actions/runs/{run_id}"
        if commit != os.getenv("GITHUB_SHA"):
            summary["reason_codes"].append("checkout_commit_mismatch")
        if dirty is not False:
            summary["reason_codes"].append("checkout_sources_changed_or_unverifiable")
    try:
        contract = json.loads(args.contract.read_text(encoding="utf-8"))
        if contract.get("schema_version") != 1:
            raise ValueError("invalid contract")
        specs = contract["jobs"][args.job]
        outcomes = dict(value.split("=", 1) for value in args.outcome)
        if not specs or any(not spec.get("collected") for spec in specs):
            raise ValueError("complete collection required")
        required_outcomes = {name for spec in specs for name in (spec["id"], spec["id"] + "_collection")}
        if set(outcomes) != required_outcomes:
            summary["reason_codes"].append("command_outcomes_missing_or_unknown")
        summary["reports"] = [check_report(spec, root, outcomes.get(spec["id"], "missing"),
            outcomes.get(spec["id"] + "_collection", "missing")) for spec in specs]
        if any(report["verdict"] != "pass" for report in summary["reports"]):
            summary["reason_codes"].append("test_evidence_rejected")
    except (OSError, ValueError, KeyError, TypeError):
        summary["reason_codes"].append("contract_missing_or_invalid")
    summary["verdict"] = "fail" if summary["reason_codes"] else "pass"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"job": args.job, "verdict": summary["verdict"], "reason_codes": summary["reason_codes"]}))
    return 0 if summary["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
