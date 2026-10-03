#!/usr/bin/env python3
"""Single-server operations. Commands are argument arrays; credentials never appear in logs."""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path
import secrets
import shutil
import subprocess
import tarfile
import tempfile
import time
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]


def run(command, *, output=None, input_file=None, capture=False):
    result = subprocess.run(command, cwd=ROOT, stdout=output or (subprocess.PIPE if capture else None),
                            stdin=input_file, stderr=subprocess.PIPE)
    if result.returncode:
        # Subprocess diagnostics can contain DSNs, provider keys, or database rows.
        raise RuntimeError(f"{Path(command[0]).name} failed (exit {result.returncode}); inspect restricted service logs")
    return result.stdout.decode() if capture else None


def compose(args, *command, **kwargs):
    files = args.compose_file or [str(ROOT / "compose.prod.yml")]
    base = ["docker", "compose", "--project-name", args.project]
    if args.env_file:
        base += ["--env-file", str(Path(args.env_file).resolve())]
    for file in files:
        base += ["-f", str(Path(file).resolve())]
    return run(base + list(command), **kwargs)


def bootstrap(args):
    target = Path(args.secret_dir).resolve()
    if target.exists() and any(target.iterdir()):
        raise ValueError("Secret directory must be empty; existing credentials are never overwritten")
    model_key = Path(args.model_key_file).resolve()
    cert, key = Path(args.tls_cert).resolve(), Path(args.tls_key).resolve()
    for source in (model_key, cert, key):
        if not source.is_file():
            raise ValueError("All supplied key/certificate files must exist")
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.chmod(0o700)
    values = {name:secrets.token_urlsafe(48) for name in (
        "postgres_password", "app_db_password", "migration_db_password", "keycloak_db_password",
        "keycloak_admin_password", "oidc_client_secret", "session_secret", "redis_password",
        "qdrant_api_key", "qdrant_read_api_key")}
    values["app_database_url"] = f"postgresql+asyncpg://itops_app:{quote(values['app_db_password'], safe='')}@postgres:5432/itops"
    values["migration_database_url"] = f"postgresql+asyncpg://itops_migrator:{quote(values['migration_db_password'], safe='')}@postgres:5432/itops"
    values["redis_url"] = f"redis://:{quote(values['redis_password'], safe='')}@redis:6379/0"
    values["redis_config"] = f"bind 0.0.0.0\nprotected-mode yes\nrequirepass {values['redis_password']}\nappendonly yes\nmaxmemory 192mb\nmaxmemory-policy noeviction\n"
    for name, value in values.items():
        with (target/name).open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(value + ("" if value.endswith("\n") else "\n"))
        # File-backed Compose secrets preserve host modes. Containers use different UIDs;
        # the 0700 parent protects host access, and only selected containers receive each file.
        (target/name).chmod(0o444)
    for source, name in ((model_key,"openai_api_key"),(cert,"tls_cert"),(key,"tls_key")):
        with (target/name).open("xb") as handle:
            handle.write(source.read_bytes())
        (target/name).chmod(0o444)
    print(json.dumps({"secret_directory":str(target),"files":len(list(target.iterdir()))}))


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda:handle.read(1024*1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def backup(args):
    if args.test_database and (not disposable_database(args.test_database) or "test" not in args.project):
        raise ValueError("Unencrypted backup is restricted to an explicitly named test database/project")
    if not args.test_database and not args.age_recipient:
        raise ValueError("Production backup requires an age encryption recipient")
    backup_dir = Path(args.output_dir).resolve()
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup_dir.chmod(0o700)
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    started = time.monotonic()
    running = set(compose(args,"ps","--status","running","--services",capture=True).split())
    paused = sorted(running & {"gateway","api","worker","keycloak"})
    with tempfile.TemporaryDirectory(prefix="itops-staging-",dir=backup_dir) as temporary:
        stage = Path(temporary).resolve()
        if stage.parent != backup_dir:
            raise ValueError("Invalid backup staging path")
        try:
            if paused:
                compose(args,"stop",*paused)
            databases = [args.test_database] if args.test_database else ["itops", "keycloak"]
            for database in databases:
                with (stage/f"{database}.dump").open("wb") as output:
                    compose(args,"exec","-T","postgres","pg_dump","-U",args.db_user,"-Fc","--no-owner",database,output=output)
            if not args.test_database:
                with (stage/"knowledge.tar.gz").open("wb") as output:
                    compose(args,"run","--rm","--no-deps","--entrypoint","tar","api","-czf","-","-C","/app","knowledge",output=output)
                secret_dir = Path(args.secret_dir).resolve()
                with tarfile.open(stage/"secrets.tar.gz","w:gz") as archive:
                    archive.add(secret_dir,arcname="secrets",recursive=True)
                # Immutable collections are authoritative in PG and can also be rebuilt.
                collection_code = """import json,urllib.request,pathlib
key=pathlib.Path('/run/secrets/qdrant_api_key').read_text().strip()
request=urllib.request.Request('http://qdrant:6333/collections',headers={'api-key':key})
print(json.dumps(json.load(urllib.request.urlopen(request,timeout=120))['result']['collections']))"""
                collections = json.loads(compose(args,"run","--rm","--no-deps","--entrypoint","python","worker","-c",collection_code,capture=True))
                snapshots = []
                snapshot_request = "import json,pathlib,urllib.request,urllib.parse,sys; key=pathlib.Path('/run/secrets/qdrant_api_key').read_text().strip(); path='/collections/'+urllib.parse.quote(sys.argv[1],safe='')+'/snapshots'+('/'+urllib.parse.quote(sys.argv[3],safe='') if len(sys.argv)>3 else ''); request=urllib.request.Request('http://qdrant:6333'+path,method=sys.argv[2],headers={'api-key':key}); payload=urllib.request.urlopen(request,timeout=120).read(); print(json.dumps(json.loads(payload)['result']['name'])) if sys.argv[2]=='POST' else None"
                for index, collection in enumerate(collections):
                    name = collection["name"]
                    snapshot = json.loads(compose(args,"run","--rm","--no-deps","--entrypoint","python","worker","-c",snapshot_request,name,"POST",capture=True))
                    item = {"collection":name,"snapshot":snapshot}
                    try:
                        code = "import pathlib,urllib.request,urllib.parse,sys; key=pathlib.Path('/run/secrets/qdrant_api_key').read_text().strip(); request=urllib.request.Request('http://qdrant:6333/collections/'+urllib.parse.quote(sys.argv[1],safe='')+'/snapshots/'+urllib.parse.quote(sys.argv[2],safe=''),headers={'api-key':key}); sys.stdout.buffer.write(urllib.request.urlopen(request,timeout=120).read())"
                        with (stage/f"qdrant-{index}.snapshot").open("wb") as output:
                            compose(args,"run","--rm","--no-deps","--entrypoint","python","worker","-c",code,name,snapshot,output=output)
                        snapshots.append(item)
                    finally:
                        # Downloaded backup bytes are retained in the encrypted bundle; server
                        # snapshots are temporary so nightly backups cannot fill Qdrant's disk.
                        try:
                            compose(args,"run","--rm","--no-deps","--entrypoint","python","worker","-c",snapshot_request,name,"DELETE",snapshot)
                        except RuntimeError:
                            print(json.dumps({"event":"backup_snapshot_cleanup_failed","collection":name}))
                (stage/"qdrant.json").write_text(json.dumps(snapshots),encoding="utf-8")
                shutil.copyfile(ROOT/"compose.prod.yml",stage/"compose.prod.yml")
                shutil.copyfile(ROOT/"infra/keycloak/itops-realm.json",stage/"realm-template.json")
                if args.env_file:
                    shutil.copyfile(Path(args.env_file).resolve(),stage/"runtime.env")
            manifest = {"version":1,"timestamp":timestamp,"project":args.project,"databases":databases,"test_only":bool(args.test_database),
                "image_manifest":compose(args,"images","--format","json",capture=True),
                "sha256":{p.name:file_hash(p) for p in stage.iterdir() if p.is_file()}}
            (stage/"manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
            plain = stage/"bundle.tar.gz"
            with tarfile.open(plain,"w:gz") as archive:
                for path in sorted(stage.iterdir()):
                    if path != plain:
                        archive.add(path,arcname=path.name)
            artifact = backup_dir/f"itops-{timestamp}.tar.gz{'.age' if not args.test_database else ''}"
            if args.test_database:
                shutil.copyfile(plain,artifact)
            else:
                with plain.open("rb") as input_file:
                    run(["age","-r",args.age_recipient,"-o",str(artifact)],input_file=input_file)
            artifact.chmod(0o600)
            if args.offsite:
                run(["rclone","copyto",str(artifact),args.offsite.rstrip("/")+"/"+artifact.name])
            print(json.dumps({"artifact":str(artifact),"sha256":file_hash(artifact),"offsite_copied":bool(args.offsite),"seconds":round(time.monotonic()-started,2)}))
        finally:
            if paused:
                compose(args,"start",*paused)


def safe_extract(archive, destination):
    names = set()
    for member in archive.getmembers():
        target = (destination/member.name).resolve()
        if member.name in names or "\\" in member.name or not target.is_relative_to(destination) or member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
            raise ValueError("Unsafe backup archive member")
        names.add(member.name)
    archive.extractall(destination,filter="data")


def disposable_database(name):
    return bool(re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,62}", name)) and any(part in name.lower() for part in ("test", "e2e"))


def validate_manifest(stage, manifest):
    """Validate names and the complete inventory before filesystem reads or service writes."""
    if not isinstance(manifest, dict) or manifest.get("version") != 1 or type(manifest.get("test_only")) is not bool:
        raise ValueError("Unsupported backup manifest")
    databases = manifest.get("databases")
    if not isinstance(databases, list) or (manifest["test_only"] and (len(databases) != 1 or not isinstance(databases[0], str) or not disposable_database(databases[0]))) or (not manifest["test_only"] and databases != ["itops", "keycloak"]):
        raise ValueError("Invalid backup database inventory")
    hashes = manifest.get("sha256")
    if not isinstance(hashes, dict) or not hashes:
        raise ValueError("Invalid backup checksum inventory")
    for name, expected in hashes.items():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name) or ".." in name or not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError("Invalid backup checksum entry")
    required = {f"{name}.dump" for name in databases}
    if not manifest["test_only"]:
        required |= {"knowledge.tar.gz", "secrets.tar.gz", "qdrant.json", "compose.prod.yml", "realm-template.json"}
    if not required <= set(hashes) or {path.name for path in stage.iterdir()} != set(hashes) | {"manifest.json"}:
        raise ValueError("Incomplete backup inventory")
    for name, expected in hashes.items():
        path = stage/name
        if not path.is_file() or path.is_symlink() or file_hash(path) != expected:
            raise ValueError("Backup checksum mismatch")
    if not manifest["test_only"]:
        snapshots = json.loads((stage/"qdrant.json").read_text(encoding="utf-8"))
        if not isinstance(snapshots, list):
            raise ValueError("Invalid snapshot inventory")
        collections = set()
        for index, item in enumerate(snapshots):
            if not isinstance(item, dict) or not isinstance(item.get("collection"), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,254}", item["collection"]) or item["collection"] in collections or f"qdrant-{index}.snapshot" not in hashes:
                raise ValueError("Invalid snapshot inventory")
            collections.add(item["collection"])
        if {name for name in hashes if name.endswith(".snapshot")} != {f"qdrant-{index}.snapshot" for index in range(len(snapshots))}:
            raise ValueError("Incomplete snapshot inventory")


def restore(args):
    if "restore" not in args.project and "test" not in args.project:
        raise ValueError("Restore must target an isolated project named *restore* or *test*; switch DNS after validation")
    if args.test_database and (not disposable_database(args.test_database) or "test" not in args.project):
        raise ValueError("Test restore requires an explicitly named disposable test database/project")
    artifact = Path(args.artifact).resolve()
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="itops-restore-") as temporary:
        stage = Path(temporary).resolve()
        plain = artifact
        if artifact.suffix == ".age":
            if not args.age_identity:
                raise ValueError("Encrypted restore requires the protected age identity file")
            plain = stage/"bundle.tar.gz"
            run(["age","-d","-i",str(Path(args.age_identity).resolve()),"-o",str(plain),str(artifact)])
        extracted = stage/"extracted"
        extracted.mkdir(mode=0o700)
        with tarfile.open(plain,"r:gz") as archive:
            safe_extract(archive,extracted)
        stage = extracted
        manifest = json.loads((stage/"manifest.json").read_text(encoding="utf-8"))
        validate_manifest(stage, manifest)
        targets = [args.test_database] if args.test_database else manifest["databases"]
        if bool(args.test_database) != manifest["test_only"]:
            raise ValueError("Test and production backups cannot be mixed")
        if not args.test_database:
            secret_target = Path(args.secret_dir).resolve()
            with tarfile.open(stage/"secrets.tar.gz","r:gz") as archive:
                safe_extract(archive,stage/"restored-secrets")
            saved_secrets = stage/"restored-secrets/secrets"
            if secret_target.exists() and any(secret_target.iterdir()):
                if any(not (secret_target/p.name).is_file() or file_hash(secret_target/p.name) != file_hash(p) for p in saved_secrets.iterdir()):
                    raise ValueError("Restore refused: existing secrets differ from protected backup")
            else:
                secret_target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copytree(saved_secrets,secret_target,dirs_exist_ok=True)
            secret_target.chmod(0o700)
            for path in secret_target.iterdir():
                path.chmod(0o444)
            if args.prepare_only:
                config_dir = secret_target.parent/"restore-config"
                config_dir.mkdir(exist_ok=True,mode=0o700)
                for name in ("runtime.env","compose.prod.yml","realm-template.json","manifest.json"):
                    if (stage/name).is_file():
                        destination=config_dir/name
                        if destination.exists():
                            raise ValueError("Restore configuration destination already exists")
                        shutil.copyfile(stage/name,destination)
                        destination.chmod(0o600)
                print(json.dumps({"secret_directory":str(secret_target),"configuration_directory":str(config_dir),"next":"configure SECRET_DIR; start only postgres/qdrant/redis/storage-init; then repeat restore without --prepare-only"}))
                return
            running=set(compose(args,"ps","--status","running","--services",capture=True).split())
            if running & {"gateway","api","worker","keycloak"}:
                raise ValueError("Restore refused: stop all application and identity writers before recovery")
            # Before this phase start only postgres/qdrant/redis/storage-init with the saved secrets.
            occupied = compose(args,"run","--rm","--no-deps","--entrypoint","python","api","-c",
                "import pathlib; print(int(any(pathlib.Path('/app/knowledge').iterdir())))",capture=True).strip()
            if occupied != "0":
                raise ValueError("Restore refused: knowledge volume is not empty")
            collection_check="import json,pathlib,urllib.request; key=pathlib.Path('/run/secrets/qdrant_api_key').read_text().strip(); request=urllib.request.Request('http://qdrant:6333/collections',headers={'api-key':key}); print(len(json.load(urllib.request.urlopen(request,timeout=15))['result']['collections']))"
            occupied=compose(args,"run","--rm","--no-deps","--entrypoint","python","worker","-c",collection_check,capture=True).strip()
            if occupied != "0":
                raise ValueError("Restore refused: Qdrant target is not empty")
        # Preflight every database before restoring the first byte into any one of them.
        for source, target in zip(manifest["databases"], targets,strict=True):
            count = compose(args,"exec","-T","postgres","psql","-U",args.db_user,"-d",target,"-Atc",
                "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%' AND c.relkind IN ('r','p','v','m','S','f')",capture=True).strip()
            if count != "0":
                raise ValueError("Restore refused: target database is not empty")
        for source, target in zip(manifest["databases"], targets,strict=True):
            with (stage/f"{source}.dump").open("rb") as input_file:
                owner = args.db_user if args.test_database else ("itops_migrator" if target == "itops" else "keycloak")
                compose(args,"exec","-T","postgres","pg_restore","-U",owner,"-d",target,"--no-owner","--exit-on-error",input_file=input_file)
        if not args.test_database:
            with (stage/"knowledge.tar.gz").open("rb") as input_file:
                compose(args,"run","--rm","--no-deps","--entrypoint","tar","api","-xzf","-","-C","/app",input_file=input_file)
            snapshots = json.loads((stage/"qdrant.json").read_text())
            for index,item in enumerate(snapshots):
                # Qdrant receives snapshot bytes via its authenticated upload API.
                code = """import pathlib,urllib.request,sys,uuid
boundary=uuid.uuid4().hex; blob=sys.stdin.buffer.read()
body=('--'+boundary+'\\r\\nContent-Disposition: form-data; name="snapshot"; filename="restore.snapshot"\\r\\nContent-Type: application/octet-stream\\r\\n\\r\\n').encode()+blob+('\\r\\n--'+boundary+'--\\r\\n').encode()
key=pathlib.Path('/run/secrets/qdrant_api_key').read_text().strip()
r=urllib.request.Request('http://qdrant:6333/collections/'+sys.argv[1]+'/snapshots/upload?priority=snapshot',data=body,headers={'api-key':key,'Content-Type':'multipart/form-data; boundary='+boundary})
urllib.request.urlopen(r,timeout=600).read()"""
                with (stage/f"qdrant-{index}.snapshot").open("rb") as input_file:
                    compose(args,"run","--rm","-T","--no-deps","--entrypoint","python","worker","-c",code,item["collection"],input_file=input_file)
        print(json.dumps({"restored_databases":targets,"seconds":round(time.monotonic()-started,2),"next":"start services; run login, knowledge, confirmation probes before switching traffic"}))


def release(args):
    evidence = json.loads(Path(args.evidence).read_text(encoding="utf-8"))
    required = ("tests_passed","real_model_evaluation_passed","capacity_passed","restore_passed","rollback_passed","alerts_passed")
    if not all(evidence.get(name) is True for name in required):
        raise ValueError("Release evidence must pass tests, real-model evaluation, capacity, restore, and rollback")
    expected = args.image_tag
    if evidence.get("image_tag") != expected or not re.fullmatch(r"[0-9a-f]{40}", expected):
        raise ValueError("Evidence must identify the exact immutable image tag")
    os.environ["IMAGE_TAG"] = expected
    compose(args,"config","--quiet")
    compose(args,"pull")
    compose(args,"stop","gateway","api","worker")
    try:
        compose(args,"run","--rm","migrate")
        compose(args,"up","-d","--wait","--wait-timeout","180","postgres","redis","qdrant","keycloak","storage-init","api","worker","web","otel-collector","prometheus","alertmanager")
        compose(args,"up","-d","--no-deps","--wait","--wait-timeout","120","gateway")
    except Exception:
        try:
            compose(args,"stop","gateway","api","worker")
        except Exception:
            pass
        raise RuntimeError("Release failed; keep the maintenance window and follow the documented compatible-image rollback") from None
    print(json.dumps({"deployed_image_tag":expected,"evidence":str(Path(args.evidence).resolve())}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project",default="itops-production")
    parser.add_argument("--compose-file",action="append")
    parser.add_argument("--env-file",default=None)
    sub = parser.add_subparsers(dest="command",required=True)
    boot = sub.add_parser("bootstrap")
    boot.add_argument("--secret-dir",default=str(ROOT/"secrets")); boot.add_argument("--model-key-file",required=True)
    boot.add_argument("--tls-cert",required=True); boot.add_argument("--tls-key",required=True)
    back = sub.add_parser("backup")
    back.add_argument("--output-dir",required=True); back.add_argument("--age-recipient")
    back.add_argument("--offsite"); back.add_argument("--secret-dir",default=str(ROOT/"secrets"))
    back.add_argument("--test-database"); back.add_argument("--db-user",default="postgres")
    rest = sub.add_parser("restore")
    rest.add_argument("--artifact",required=True); rest.add_argument("--age-identity")
    rest.add_argument("--test-database"); rest.add_argument("--db-user",default="postgres")
    rest.add_argument("--secret-dir",default=str(ROOT/"secrets-restored"))
    rest.add_argument("--prepare-only",action="store_true")
    rel = sub.add_parser("release"); rel.add_argument("--evidence",required=True); rel.add_argument("--image-tag",required=True)
    args = parser.parse_args()
    try:
        {"bootstrap":bootstrap,"backup":backup,"restore":restore,"release":release}[args.command](args)
    except (ValueError, RuntimeError, OSError, tarfile.TarError) as error:
        parser.exit(1,f"Operations command failed: {error}\n")


if __name__ == "__main__":
    main()
