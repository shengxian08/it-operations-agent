"""Capture every collected pytest identity before test execution, including deselection."""
import json
from pathlib import Path


def pytest_addoption(parser):
    parser.addoption("--ci-inventory", help="Runner-local collected testcase inventory")


def pytest_sessionstart(session):
    session._ci_collected = []
    session._ci_deselected = 0


def pytest_itemcollected(item):
    # Match pytest's pinned JUnit node-address convention, retaining parameter IDs.
    path, bracket, parameters = item.nodeid.partition("[")
    parts = path.split("::")
    parts[0] = parts[0].replace("\\", "/").replace("/", ".").removesuffix(".py")
    parts[-1] += bracket + parameters
    item.session._ci_collected.append({"classname": ".".join(parts[:-1]), "name": parts[-1]})


def pytest_deselected(items):
    if items:
        items[0].session._ci_deselected += len(items)


def pytest_collection_finish(session):
    target = session.config.getoption("--ci-inventory")
    if target:
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema_version": 1, "runner": "pytest", "cases": session._ci_collected,
            "deselected": session._ci_deselected, "collection_errors": session.testsfailed}, ensure_ascii=False,
            indent=2) + "\n", encoding="utf-8")
