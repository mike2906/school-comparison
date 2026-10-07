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
if "up" in args and "postgres" in args:
    if os.environ.get("BACKUP_FAILURE") == "startup":
        sys.exit(1)
    Path(os.environ["POSTGRES_READY"]).touch()
elif "pg_dump" in args:
    if not Path(os.environ["POSTGRES_READY"]).exists():
        sys.exit(1)
    if os.environ.get("BACKUP_FAILURE") == "dump":
        sys.exit(1)
    if os.environ.get("BACKUP_FAILURE") != "empty":
        sys.stdout.write("fake custom archive")
elif "pg_restore" in args:
    data = sys.stdin.read()
    if os.environ.get("BACKUP_FAILURE") == "list":
        sys.exit(1)
    if "--single-transaction" in args:
        # A load: record what was loaded; "load" fails the snapshot, never the backup.
        with open(os.environ["DOCKER_CALLS"] + ".loads", "a") as f:
            f.write(data + "\n")
        if os.environ.get("PUBLISH_FAILURE") == "load" and data == "new snapshot":
            sys.exit(1)
elif "alembic" in args:
    if os.environ.get("PUBLISH_FAILURE") == "migrate":
        sys.exit(1)
elif "psql" in args:
    print("714")
elif args[:2] == ["image", "inspect"]:
    pass
elif "ps" in args:
    print("api-container")
elif args[:1] == ["inspect"]:
    print("ghcr.io/mike2906/school-comparison-api:" + "a" * 40)
'''


def run_deploy(tmp_path, failure="", old_backups=0):
    app = tmp_path / "app"
    (app / "deploy").mkdir(parents=True)
    for name in ["docker-compose.prod.yml", "deploy/Caddyfile", "deploy/cloudflare-proxies.caddy", "deploy/deploy.sh"]:
        shutil.copyfile(REPO / name, app / name)
    names = ["docker-compose.prod.yml", "deploy/Caddyfile", "deploy/cloudflare-proxies.caddy", "deploy/deploy.sh"]
    config_hash = hashlib.sha256(b"".join((app / name).read_bytes() for name in names)).hexdigest()
    (app / "deploy/release.env").write_text("API_IMAGE=previous\nPREVIOUS_API_IMAGE=older\n")
    if old_backups:
        backup_dir = app / "deploy/backups"
        backup_dir.mkdir()
        for i in range(old_backups):
            archive = backup_dir / f"pre-migrate-bbbbbbbbbbbb-{i:08d}.dump"
            archive.write_text(f"old archive {i}")
            os.utime(archive, (2000000000 + i, 2000000000 + i))
            Path(str(archive) + ".sha256").write_text("old checksum")
        (backup_dir / "manual.dump").write_text("keep unrelated backups")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    docker = binaries / "docker"
    docker.write_text(DOCKER_STUB)
    docker.chmod(0o755)
    calls = tmp_path / "calls.jsonl"
    env = dict(os.environ, PATH=f"{binaries}:/usr/local/bin:/usr/bin:/bin", SCHOOLDECIDER_DIR=str(app),
               DOCKER_CALLS=str(calls), BACKUP_FAILURE=failure, POSTGRES_READY=str(tmp_path / "postgres-ready"))
    env.pop("SSH_ORIGINAL_COMMAND", None)
    result = subprocess.run(["bash", str(app / "deploy/deploy.sh"), "deploy", SHA, config_hash],
                            env=env, capture_output=True, text=True, timeout=10)
    commands = [json.loads(line) for line in calls.read_text().splitlines()]
    return result, commands, app


@pytest.mark.parametrize("failure", ["startup", "dump", "empty", "list"])
def test_failed_backup_never_runs_migrations(tmp_path, failure):
    result, commands, app = run_deploy(tmp_path, failure)
    assert result.returncode != 0
    assert "migration not started" in result.stderr
    assert not any("alembic" in command for command in commands)
    assert not any("up" in command and "postgres" not in command for command in commands)
    assert (app / "deploy/release.env").read_text() == "API_IMAGE=previous\nPREVIOUS_API_IMAGE=older\n"
    assert not list((app / "deploy/backups").glob("*.dump"))


def test_backup_is_verified_before_migration_and_retained(tmp_path):
    result, commands, app = run_deploy(tmp_path)
    assert result.returncode == 0, result.stderr
    dump = next(i for i, command in enumerate(commands) if "pg_dump" in command)
    verify = next(i for i, command in enumerate(commands) if "pg_restore" in command)
    migrate = next(i for i, command in enumerate(commands) if "alembic" in command)
    start = next(i for i, command in enumerate(commands) if "up" in command and "postgres" in command)
    assert "--wait" in commands[start]
    assert start < dump < verify < migrate
    backups = list((app / "deploy/backups").glob("*.dump"))
    assert len(backups) == 1
    backup = backups[0]
    assert backup.stat().st_mode & 0o777 == 0o600
    assert Path(str(backup) + ".sha256").read_text().split()[0] == hashlib.sha256(backup.read_bytes()).hexdigest()


def test_retention_keeps_new_backup_and_six_recent_archives(tmp_path):
    result, _, app = run_deploy(tmp_path / "path with spaces", old_backups=9)
    assert result.returncode == 0, result.stderr
    backup_dir = app / "deploy/backups"
    assert len(list(backup_dir.glob("pre-migrate-*.dump"))) == 7
    for i in range(9):
        archive = backup_dir / f"pre-migrate-bbbbbbbbbbbb-{i:08d}.dump"
        assert archive.exists() == (i >= 3)
        assert Path(str(archive) + ".sha256").exists() == (i >= 3)
    assert (backup_dir / "manual.dump").read_text() == "keep unrelated backups"


@pytest.mark.parametrize("failure", ["startup", "dump", "empty", "list"])
def test_failed_backup_preserves_all_previous_archives(tmp_path, failure):
    result, _, app = run_deploy(tmp_path, failure=failure, old_backups=9)
    assert result.returncode != 0
    backup_dir = app / "deploy/backups"
    assert len(list(backup_dir.glob("pre-migrate-*.dump"))) == 9
    assert len(list(backup_dir.glob("pre-migrate-*.sha256"))) == 9


IMAGE = "ghcr.io/mike2906/school-comparison-api:" + "a" * 40


def run_publish(tmp_path, failure="", checksum=None):
    app = tmp_path / "app"
    (app / "deploy").mkdir(parents=True)
    for name in ["docker-compose.prod.yml", "deploy/deploy.sh"]:
        shutil.copyfile(REPO / name, app / name)
    (app / "deploy/release.env").write_text(f"API_IMAGE={IMAGE}\nPREVIOUS_API_IMAGE=older\n")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    docker = binaries / "docker"
    docker.write_text(DOCKER_STUB)
    docker.chmod(0o755)
    calls = tmp_path / "calls.jsonl"
    calls.touch()
    snapshot = b"new snapshot"
    env = dict(os.environ, PATH=f"{binaries}:/usr/local/bin:/usr/bin:/bin", SCHOOLDECIDER_DIR=str(app),
               DOCKER_CALLS=str(calls), BACKUP_FAILURE="", PUBLISH_FAILURE=failure,
               POSTGRES_READY=str(tmp_path / "postgres-ready"), READY_POLL_SECONDS="0")
    env.pop("SSH_ORIGINAL_COMMAND", None)
    result = subprocess.run(
        ["bash", str(app / "deploy/deploy.sh"), "publish", checksum or hashlib.sha256(snapshot).hexdigest()],
        env=env, input=snapshot, capture_output=True, timeout=20)
    commands = [json.loads(line) for line in calls.read_text().splitlines()]
    loads_file = Path(str(calls) + ".loads")
    loads = loads_file.read_text().splitlines() if loads_file.exists() else []
    return result, commands, loads, app


def test_publish_backs_up_then_loads_the_snapshot_and_migrates(tmp_path):
    result, commands, loads, app = run_publish(tmp_path)
    assert result.returncode == 0, result.stderr
    step = lambda match: next(i for i, command in enumerate(commands) if match(command))
    dump = step(lambda c: "pg_dump" in c)
    stop = step(lambda c: c[-2:] == ["stop", "api"])
    load = step(lambda c: "--single-transaction" in c)
    migrate = step(lambda c: "alembic" in c)
    start = step(lambda c: c[-2:] == ["up", "-d"])
    assert dump < stop < load < migrate < start
    assert loads == ["new snapshot"]
    assert "--clean" in commands[load] and "--exit-on-error" in commands[load]
    assert len(list((app / "deploy/backups").glob("pre-publish-*.dump"))) == 1
    assert not list((app / "deploy/backups").glob("incoming-*"))
    assert b"schools: 714 -> 714" in result.stdout
    assert (app / "deploy/release.env").read_text() == f"API_IMAGE={IMAGE}\nPREVIOUS_API_IMAGE=older\n"


def test_publish_rejects_a_snapshot_that_does_not_match_its_checksum(tmp_path):
    result, commands, loads, app = run_publish(tmp_path, checksum="0" * 64)
    assert result.returncode != 0
    assert b"nothing changed" in result.stderr
    assert commands == [] and loads == []
    assert not list((app / "deploy/backups").glob("incoming-*"))


def test_publish_keeps_the_previous_data_when_the_snapshot_does_not_load(tmp_path):
    result, commands, loads, _ = run_publish(tmp_path, failure="load")
    assert result.returncode != 0
    assert b"previous data is unchanged" in result.stderr
    assert loads == ["new snapshot"]
    assert not any("alembic" in command for command in commands)
    assert commands[-1][-2:] == ["up", "-d"]


def test_publish_restores_the_backup_when_migration_fails(tmp_path):
    result, commands, loads, app = run_publish(tmp_path, failure="migrate")
    assert result.returncode != 0
    assert b"previous data is restored" in result.stderr
    # The snapshot was loaded, then the pre-publish backup over it.
    assert loads == ["new snapshot", "fake custom archive"]
    assert any(command[-2:] == ["up", "-d"] for command in commands)
    assert len(list((app / "deploy/backups").glob("pre-publish-*.dump"))) == 1
