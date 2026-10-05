"""Exercise the real deploy script with Docker stubbed: failed backups block migration."""
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SHA = "a" * 40

DOCKER_STUB = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ["DOCKER_CALLS"], "a") as f:
    f.write(json.dumps(args) + "\n")
if "pg_dump" in args:
    if os.environ.get("BACKUP_FAILURE") == "dump":
        sys.exit(1)
    if os.environ.get("BACKUP_FAILURE") != "empty":
        sys.stdout.write("fake custom archive")
elif "pg_restore" in args:
    sys.stdin.read()
    if os.environ.get("BACKUP_FAILURE") == "list":
        sys.exit(1)
elif args[:2] == ["image", "inspect"]:
    pass
elif "ps" in args:
    print("api-container")
elif args[:1] == ["inspect"]:
    print("ghcr.io/mike2906/school-comparison-api:" + "a" * 40)
'''


def run_deploy(tmp_path, failure=""):
    app = tmp_path / "app"
    (app / "deploy").mkdir(parents=True)
    for name in ["docker-compose.prod.yml", "deploy/Caddyfile", "deploy/cloudflare-proxies.caddy", "deploy/deploy.sh"]:
        shutil.copyfile(REPO / name, app / name)
    names = ["docker-compose.prod.yml", "deploy/Caddyfile", "deploy/cloudflare-proxies.caddy", "deploy/deploy.sh"]
    config_hash = hashlib.sha256(b"".join((app / name).read_bytes() for name in names)).hexdigest()
    (app / "deploy/release.env").write_text("API_IMAGE=previous\nPREVIOUS_API_IMAGE=older\n")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    docker = binaries / "docker"
    docker.write_text(DOCKER_STUB)
    docker.chmod(0o755)
    calls = tmp_path / "calls.jsonl"
    env = dict(os.environ, PATH=f"{binaries}:/usr/local/bin:/usr/bin:/bin", SCHOOLDECIDER_DIR=str(app),
               DOCKER_CALLS=str(calls), BACKUP_FAILURE=failure)
    env.pop("SSH_ORIGINAL_COMMAND", None)
    result = subprocess.run(["bash", str(app / "deploy/deploy.sh"), "deploy", SHA, config_hash],
                            env=env, capture_output=True, text=True, timeout=10)
    commands = [json.loads(line) for line in calls.read_text().splitlines()]
    return result, commands, app


@pytest.mark.parametrize("failure", ["dump", "empty", "list"])
def test_failed_backup_never_runs_migrations(tmp_path, failure):
    result, commands, app = run_deploy(tmp_path, failure)
    assert result.returncode != 0
    assert "migration not started" in result.stderr
    assert not any("alembic" in command for command in commands)
    assert not any("up" in command for command in commands)
    assert (app / "deploy/release.env").read_text() == "API_IMAGE=previous\nPREVIOUS_API_IMAGE=older\n"
    assert not list((app / "deploy/backups").glob("*.dump"))


def test_backup_is_verified_before_migration_and_retained(tmp_path):
    result, commands, app = run_deploy(tmp_path)
    assert result.returncode == 0, result.stderr
    dump = next(i for i, command in enumerate(commands) if "pg_dump" in command)
    verify = next(i for i, command in enumerate(commands) if "pg_restore" in command)
    migrate = next(i for i, command in enumerate(commands) if "alembic" in command)
    assert dump < verify < migrate
    backups = list((app / "deploy/backups").glob("*.dump"))
    assert len(backups) == 1
    backup = backups[0]
    assert backup.stat().st_mode & 0o777 == 0o600
    assert Path(str(backup) + ".sha256").read_text().split()[0] == hashlib.sha256(backup.read_bytes()).hexdigest()
