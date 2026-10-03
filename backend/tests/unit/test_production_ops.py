"""Recovery preconditions must fail before any database bytes are written."""
import importlib.util
import io
import json
from pathlib import Path
import tarfile
from types import SimpleNamespace

import pytest


spec = importlib.util.spec_from_file_location("production_ops", Path(__file__).resolve().parents[3] / "scripts/production/ops.py")
ops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops)


def inventory(directory, databases=("itops_test",), test_only=True):
    files = {f"{name}.dump": b"synthetic dump" for name in databases}
    if not test_only:
        files.update({name: b"synthetic" for name in ("knowledge.tar.gz", "secrets.tar.gz", "compose.prod.yml", "realm-template.json")})
        files["qdrant.json"] = b"[]"
    for name, contents in files.items():
        (directory / name).write_bytes(contents)
    manifest = {"version": 1, "test_only": test_only, "databases": list(databases), "sha256": {name: ops.file_hash(directory / name) for name in files}}
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def test_complete_inventory_and_hashes_are_required(tmp_path):
    manifest = inventory(tmp_path)
    ops.validate_manifest(tmp_path, manifest)
    (tmp_path / "itops_test.dump").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="checksum"):
        ops.validate_manifest(tmp_path, manifest)


@pytest.mark.parametrize("invalid_name", ["../outside", "/outside", "x\\outside", "x..dump"])
def test_manifest_never_reads_a_path_outside_inventory(tmp_path, invalid_name):
    manifest = inventory(tmp_path)
    manifest["sha256"][invalid_name] = "0" * 64
    with pytest.raises(ValueError, match="checksum entry"):
        ops.validate_manifest(tmp_path, manifest)


def test_production_database_targets_cannot_be_redefined_by_archive(tmp_path):
    manifest = inventory(tmp_path, ("other_database",), test_only=False)
    with pytest.raises(ValueError, match="database inventory"):
        ops.validate_manifest(tmp_path, manifest)


def test_duplicate_archive_members_fail_before_extraction(tmp_path):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for _ in range(2):
            info = tarfile.TarInfo("duplicate")
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
    stream.seek(0)
    with tarfile.open(fileobj=stream, mode="r:gz") as archive:
        with pytest.raises(ValueError, match="Unsafe"):
            ops.safe_extract(archive, tmp_path)
    assert not (tmp_path / "duplicate").exists()


def test_non_disposable_restore_target_is_rejected_before_compose(monkeypatch, tmp_path):
    monkeypatch.setattr(ops, "compose", lambda *args, **kwargs: pytest.fail("must not contact services"))
    args = SimpleNamespace(project="itops-test", test_database="company", artifact=str(tmp_path / "none"))
    with pytest.raises(ValueError, match="disposable"):
        ops.restore(args)


def test_second_nonempty_database_prevents_first_restore(monkeypatch, tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir()
    inventory(stage, ("itops", "keycloak"), test_only=False)
    # Nested secret archive uses real extraction, then service responses are controlled.
    with tarfile.open(stage / "secrets.tar.gz", "w:gz") as archive:
        info = tarfile.TarInfo("secrets/session_secret")
        info.size = 1
        archive.addfile(info, io.BytesIO(b"x"))
    manifest = json.loads((stage / "manifest.json").read_text())
    manifest["sha256"]["secrets.tar.gz"] = ops.file_hash(stage / "secrets.tar.gz")
    (stage / "manifest.json").write_text(json.dumps(manifest))
    artifact = tmp_path / "backup.tar.gz"
    with tarfile.open(artifact, "w:gz") as archive:
        for path in stage.iterdir():
            archive.add(path, arcname=path.name)
    commands = []
    def compose(args, *command, **kwargs):
        commands.append(command)
        if command[0] == "ps":
            return "postgres qdrant redis"
        if "pg_restore" in command:
            pytest.fail("no writes before all preflight checks pass")
        if "psql" in command:
            return "1" if "keycloak" in command else "0"
        return "0"
    monkeypatch.setattr(ops, "compose", compose)
    args = SimpleNamespace(project="itops-restore", test_database=None, artifact=str(artifact), age_identity=None,
                           secret_dir=str(tmp_path / "secrets"), prepare_only=False, db_user="postgres")
    with pytest.raises(ValueError, match="not empty"):
        ops.restore(args)
    assert sum("psql" in command for command in commands) == 2


def test_failed_backend_release_never_opens_gateway(monkeypatch, tmp_path):
    evidence = tmp_path / "evidence.json"
    required = ("tests_passed", "real_model_evaluation_passed", "capacity_passed", "restore_passed", "rollback_passed", "alerts_passed")
    evidence.write_text(json.dumps({**dict.fromkeys(required, True), "image_tag": "a" * 40}))
    commands = []
    def compose(args, *command, **kwargs):
        commands.append(command)
        if command[0] == "up" and "--wait" in command:
            raise RuntimeError("health failure")
    monkeypatch.setattr(ops, "compose", compose)
    monkeypatch.setenv("IMAGE_TAG", "test-initial")
    with pytest.raises(RuntimeError, match="maintenance window"):
        ops.release(SimpleNamespace(evidence=str(evidence), image_tag="a" * 40))
    assert commands[-1] == ("stop", "gateway", "api", "worker")
    assert not any(command[0] == "up" and "gateway" in command for command in commands)


@pytest.mark.parametrize("download_fails", [False, True])
def test_server_snapshot_is_temporary_even_when_download_fails(monkeypatch, tmp_path, download_fails):
    secret_dir = tmp_path / "secrets"
    secret_dir.mkdir()
    calls = []
    def compose(args, *command, **kwargs):
        calls.append(command)
        if command[0] == "ps":
            return ""
        if command[0] == "images":
            return "[]"
        if "-c" in command:
            code = command[command.index("-c") + 1]
            if "['result']['collections']" in code:
                return '[{"name":"owned-index"}]'
            if command[-1] == "POST":
                return '"temporary.snapshot"'
            if "DELETE" in command:
                return None
            if download_fails:
                raise RuntimeError("download failed")
        if kwargs.get("output"):
            kwargs["output"].write(b"synthetic payload")
    def encrypt(command, **kwargs):
        assert command[0] == "age"
        Path(command[command.index("-o") + 1]).write_bytes(kwargs["input_file"].read())
    monkeypatch.setattr(ops, "compose", compose)
    monkeypatch.setattr(ops, "run", encrypt)
    args = SimpleNamespace(test_database=None, age_recipient="test-only", project="itops-test", db_user="postgres",
                           output_dir=str(tmp_path / "backups"), secret_dir=str(secret_dir), env_file=None, offsite=None)
    if download_fails:
        with pytest.raises(RuntimeError, match="download failed"):
            ops.backup(args)
    else:
        ops.backup(args)
    assert any("DELETE" in command and command[-1] == "temporary.snapshot" for command in calls)
