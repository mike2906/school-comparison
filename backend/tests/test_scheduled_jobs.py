"""Exercise the scheduled refresh scripts (backend/scripts/refresh_*.sh) with uv, docker, gh
and the publish stubbed: when they run, what they spend, how they carry on after a stop."""
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = ["lib/scheduled_job.sh", "refresh_fees.sh", "refresh_facts.sh", "refresh_register.sh"]

# `uv run python -m app.scrapers.cli <command> ...`: records the call and plays the next planned
# run from $UV_PLAN: the schools it reads, what it spends, how it stops.
UV_STUB = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["CALLS"], "a") as f:
    f.write(json.dumps({"uv": args, "force": os.environ.get("PINS_FORCE")}) + "\n")
if "--out" not in args or "--max-usd" not in args:
    sys.exit(int(os.environ.get("UV_FAIL", "0")))
plan_path = os.environ["UV_PLAN"]
plan = json.load(open(plan_path))
run = plan.pop(0)
json.dump(plan, open(plan_path, "w"))
out = args[args.index("--out") + 1]
lines = out[: -len(".json")] + ".schools.jsonl"
results = [{"school_id": i, "outcome": "error" if i in run.get("errors", []) else "written"} for i in run["schools"]]
with open(lines, "a") as f:
    for result in results:
        f.write(json.dumps(result) + "\n")
if run.get("killed"):
    sys.exit(1)
json.dump({"schools": len(results), "spent_usd": run["spent"], "stopped": run.get("stopped"), "results": results},
          open(out, "w"))
'''
DOCKER_STUB = "#!/bin/sh\necho 'fake dump'\n"
GH_STUB = """#!/bin/sh
echo "gh $*" >> "$CALLS.gh"
"""
PUBLISH_STUB = """#!/bin/sh
echo '{"publish": true}' >> "$CALLS"
"""
PINS_STUB = """#!/bin/sh
echo "{\\"pins\\": \\"$PINS_FORCE $PINS_MAX_USD\\"}" >> "$CALLS"
date -u +%s > "$SCHEDULED_STATE_DIR/pins_last_ok"
rm -f "$SCHEDULED_STATE_DIR/pins_unpublished"
"""


def _exe(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


@pytest.fixture
def pc(tmp_path):
    """A checkout on main with the real scripts and stubbed tools; returns a runner."""
    checkout = tmp_path / "checkout"
    scripts = checkout / "backend/scripts"
    for name in SCRIPTS:
        (scripts / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / "backend/scripts" / name, scripts / name)
    _exe(scripts / "publish_snapshot.sh", PUBLISH_STUB)
    _exe(scripts / "refresh_pins.sh", PINS_STUB)
    git = ["git", "-C", str(checkout), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "scripts"], check=True)
    subprocess.run([*git, "update-ref", "refs/remotes/origin/main", "HEAD"], check=True)
    binaries = tmp_path / "bin"
    _exe(binaries / "uv", UV_STUB)
    _exe(binaries / "docker", DOCKER_STUB)
    _exe(binaries / "gh", GH_STUB)
    state = tmp_path / "state"
    calls = tmp_path / "calls.jsonl"
    plan = tmp_path / "plan.json"
    env = dict(
        os.environ,
        PATH=f"{binaries}:{os.environ['PATH']}",
        SCHEDULED_STATE_DIR=str(state),
        PINS_STATE_DIR=str(state),
        SCHEDULED_BACKUPS=str(tmp_path / "backups"),
        CALLS=str(calls),
        UV_PLAN=str(plan),
    )

    class PC:
        def __init__(self):
            self.state, self.checkout = state, checkout

        def run(self, script, day, runs=(), **extra):
            plan.write_text(json.dumps(list(runs)))
            calls.unlink(missing_ok=True)
            now = int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp())
            result = subprocess.run(
                ["bash", str(scripts / script)], env={**env, "SCHEDULED_NOW": str(now), **extra}, capture_output=True
            )
            self.code = result.returncode
            self.calls = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
            self.issues = Path(f"{calls}.gh").read_text() if Path(f"{calls}.gh").exists() else ""
            Path(f"{calls}.gh").unlink(missing_ok=True)
            return self

        def option(self, name):
            uv = next(c["uv"] for c in self.calls if "uv" in c and "--max-usd" in c["uv"])
            return uv[uv.index(name) + 1] if name in uv else None

        @property
        def published(self):
            return any(c.get("publish") for c in self.calls)

        def log(self, job):
            return (state / f"{job}.log").read_text()

    return PC()


def test_installing_starts_nothing_and_the_next_month_runs_once(pc):
    assert pc.run("refresh_fees.sh", "2026-10-20T12:20").calls == []
    assert (pc.state / "fees_cycle").read_text().strip() == "2026-10"
    assert pc.run("refresh_fees.sh", "2026-10-31T23:20").calls == []

    pc.run("refresh_fees.sh", "2026-11-01T02:20", [{"schools": [1, 2, 3], "spent": 0.9}])
    assert pc.code == 0 and pc.option("--max-usd") == "1.50" and pc.option("--after-id") is None
    assert pc.option("--workers") == "4" and pc.published
    assert (pc.state / "fees_cycle").read_text().strip() == "2026-11"
    assert pc.run("refresh_fees.sh", "2026-11-02T02:20").calls == []


def test_a_run_the_cap_stopped_carries_on_tomorrow_with_what_is_left(pc):
    pc.run("refresh_fees.sh", "2026-10-20T12:20")
    pc.run("refresh_fees.sh", "2026-11-01T02:20", [{"schools": [5, 8], "spent": 0.8, "stopped": "stopped before school 9: cap"}])
    assert pc.published and (pc.state / "fees_cycle").read_text().strip() == "2026-10"
    assert pc.run("refresh_fees.sh", "2026-11-01T03:20").calls == []  # one start a day

    pc.run("refresh_fees.sh", "2026-11-02T02:20", [{"schools": [9], "spent": 0.3}])
    assert pc.option("--after-id") == "8" and pc.option("--max-usd") == "0.70"
    assert (pc.state / "fees_cycle").read_text().strip() == "2026-11"


def test_a_killed_run_counts_its_whole_cap_and_publishes_what_it_wrote(pc):
    pc.run("refresh_fees.sh", "2026-10-20T12:20")
    pc.run("refresh_fees.sh", "2026-11-01T02:20", [{"schools": [5], "spent": 0, "killed": True}])
    assert pc.code == 1 and pc.published
    assert "Scheduled fees re-read stopped" in pc.issues and "after school 5" in pc.log("fees")

    pc.run("refresh_fees.sh", "2026-11-02T02:20")
    assert pc.code == 1 and not any("uv" in c for c in pc.calls)
    assert "$1.50 is spent" in pc.log("fees")
    assert (pc.state / "fees_cycle").read_text().strip() == "2026-11"  # no more tries this month


def test_a_run_where_most_schools_failed_reads_them_again(pc):
    pc.run("refresh_fees.sh", "2026-10-20T12:20")
    failed = list(range(1, 11))
    pc.run("refresh_fees.sh", "2026-11-01T02:20", [{"schools": failed, "spent": 0.01, "errors": failed[1:]}])
    assert pc.code == 1 and "9 of 10 schools failed" in pc.log("fees")
    pc.run("refresh_fees.sh", "2026-11-02T02:20", [{"schools": failed, "spent": 0.5}])
    assert pc.option("--after-id") is None and pc.code == 0


def test_one_failed_school_at_the_end_does_not_hold_the_cycle_open(pc):
    pc.run("refresh_fees.sh", "2026-10-20T12:20")
    pc.run("refresh_fees.sh", "2026-11-01T02:20", [{"schools": [7], "spent": 0.01, "errors": [7]}])
    assert pc.code == 0 and (pc.state / "fees_cycle").read_text().strip() == "2026-11"


def test_facts_read_a_night_at_a_time_until_the_last_school(pc):
    pc.run("refresh_facts.sh", "2026-10-20T12:30")
    assert (pc.state / "facts_cycle").read_text().strip() == "2026-08"
    assert pc.run("refresh_facts.sh", "2027-01-31T12:30").calls == []

    night = {"FACTS_PER_NIGHT": "2"}
    pc.run("refresh_facts.sh", "2027-02-01T01:30", [{"schools": [1, 2], "spent": 0.9}], **night)
    assert pc.option("--limit") == "2" and pc.option("--max-usd") == "1.70" and pc.published
    pc.run("refresh_facts.sh", "2027-02-02T01:30", [{"schools": [3, 4], "spent": 0.9}], **night)
    assert pc.option("--after-id") == "2"
    pc.run("refresh_facts.sh", "2027-02-03T01:30", [{"schools": [5], "spent": 0.5}], **night)
    assert pc.option("--after-id") == "4"
    assert (pc.state / "facts_cycle").read_text().strip() == "2027-02"
    assert pc.run("refresh_facts.sh", "2027-02-04T01:30").calls == []
    assert pc.run("refresh_facts.sh", "2027-08-25T01:30", [{"schools": [1], "spent": 0.1}]).option("--after-id") is None


def test_one_start_a_day_across_the_jobs(pc):
    pc.run("refresh_fees.sh", "2026-10-20T12:20")
    pc.run("refresh_facts.sh", "2026-10-20T12:30")
    pc.run("refresh_fees.sh", "2027-02-01T00:20", [{"schools": [1], "spent": 0.5}])
    assert pc.run("refresh_facts.sh", "2027-02-01T00:30").calls == []
    assert pc.run("refresh_facts.sh", "2027-02-02T00:30", [{"schools": [1], "spent": 0.5}]).calls != []


def test_a_checkout_with_local_changes_runs_nothing(pc):
    pc.run("refresh_fees.sh", "2026-10-20T12:20")
    script = pc.checkout / "backend/scripts/refresh_fees.sh"
    script.write_text(script.read_text() + "\n# edited\n")
    pc.run("refresh_fees.sh", "2026-11-01T02:20", [{"schools": [1], "spent": 0.5}])
    assert pc.code == 1 and pc.calls == [] and "local changes" in pc.log("fees")


def test_the_register_chain_runs_from_the_fifth_and_ends_with_a_forced_pin_run(pc):
    pc.run("refresh_register.sh", "2026-10-20T12:50")
    assert pc.run("refresh_register.sh", "2026-11-04T12:50").calls == []

    pc.run("refresh_register.sh", "2026-11-05T12:50", UV_FAIL="0")
    commands = [c["uv"][4] if c["uv"][1] == "python" and c["uv"][2] == "-m" else c["uv"][2] for c in pc.calls if "uv" in c]
    assert commands == ["run", "scripts/import_sofia_municipal_points.py", "register-pins", "site-address", "confirm-pins"]
    assert pc.calls[-1] == {"pins": "1 0.15"} and pc.code == 0
    assert (pc.state / "register_cycle").read_text().strip() == "2026-11"


def test_a_failed_register_import_stops_before_the_pins(pc):
    pc.run("refresh_register.sh", "2026-10-20T12:50")
    pc.run("refresh_register.sh", "2026-11-05T12:50", UV_FAIL="1")
    assert pc.code == 1 and len(pc.calls) == 1 and "Scheduled register refresh stopped" in pc.issues
