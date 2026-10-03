import json
from types import SimpleNamespace

import pytest

from test_evaluation_cli import script


class DemoIngestor:
    def __init__(self):
        self.paths = []
        self.deleted = []
    async def ingest_paths(self, paths):
        self.paths = list(paths)
        return SimpleNamespace(document_count=len(paths), chunk_count=1, removed_document_count=0)
    async def delete_planned(self, deletions):
        self.deleted.append(deletions)
        return 1


def settings(environment="test"):
    return SimpleNamespace(environment=environment, demo_enabled=True, qdrant_collection="owned-demo")


def test_production_import_preserves_frontmatter_access_level_by_default():
    from pathlib import Path
    cli = script("ingest_knowledge")
    assert cli.build_parser().parse_args([]).access_level is None
    data = b"---\naccess_level: support\n---\n# Restricted\n\nSupport process."
    assert cli.file_access_level(Path("restricted.md"), data, None) == "support"
    assert cli.file_access_level(Path("restricted.md"), data, "admin") == "admin"
    with pytest.raises(ValueError, match="access level"):
        cli.file_access_level(Path("restricted.md"), data.replace(b"support", b"everyone"), None)


@pytest.mark.asyncio
async def test_default_demo_import_appends_without_removing_absent_sources(tmp_path):
    cli = script("ingest_knowledge")
    (tmp_path / "vpn.md").write_text("# VPN\n\nRestart VPN.")
    ingestor = DemoIngestor()
    args = cli.build_parser().parse_args(["--demo", "--directory", str(tmp_path)])
    await cli.run_demo(args, settings(), ingestor, existing_sources=[{"id": "old", "source_path": "old.md", "version": "1", "status": "published"}])
    assert len(ingestor.paths) == 1 and not ingestor.deleted


@pytest.mark.asyncio
async def test_sync_delete_requires_exact_dry_run_plan_and_rejects_drift(tmp_path):
    cli = script("ingest_knowledge")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    source = corpus / "vpn.md"
    source.write_text("# VPN\n\nRestart VPN.")
    plan_path = tmp_path / "plan.json"
    old = [{"id": "old", "source_path": "old.md", "version": "1", "status": "published"}]
    ingestor = DemoIngestor()
    args = cli.build_parser().parse_args(["--demo", "--directory", str(corpus), "--sync-delete", "--dry-run", "--plan-file", str(plan_path)])
    await cli.run_demo(args, settings(), ingestor, existing_sources=old)
    plan = json.loads(plan_path.read_text())
    assert plan["deletions"] == old and not ingestor.deleted and not ingestor.paths
    args = cli.build_parser().parse_args(["--demo", "--directory", str(corpus), "--sync-delete", "--plan-file", str(plan_path)])
    with pytest.raises(ValueError, match="confirm"):
        await cli.run_demo(args, settings(), ingestor, existing_sources=old)
    args.confirm_plan = plan["sha256"]
    source.write_text("# changed")
    with pytest.raises(ValueError, match="changed|match"):
        await cli.run_demo(args, settings(), ingestor, existing_sources=old)
    source.write_text("# VPN\n\nRestart VPN.")
    await cli.run_demo(args, settings(), ingestor, existing_sources=old)
    assert ingestor.deleted == [old]


@pytest.mark.asyncio
async def test_formal_environment_forbids_legacy_import_and_missing_mount_refuses_delete(tmp_path):
    cli = script("ingest_knowledge")
    args = cli.build_parser().parse_args(["--demo", "--directory", str(tmp_path), "--sync-delete", "--dry-run"])
    with pytest.raises(ValueError, match="production|formal"):
        await cli.run_demo(args, settings("production"), DemoIngestor(), existing_sources=[])
    with pytest.raises(ValueError, match="empty"):
        await cli.run_demo(args, settings(), DemoIngestor(), existing_sources=[])
    args.directory = tmp_path / "missing-mount"
    with pytest.raises(ValueError, match="exist"):
        await cli.run_demo(args, settings(), DemoIngestor(), existing_sources=[])
