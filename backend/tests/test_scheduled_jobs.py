"""Exercise the scheduled refresh scripts (backend/scripts/refresh_*.sh) with uv, docker, gh
and the publish stubbed: when they run, what they spend, how they carry on after a stop."""
import fcntl
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
if args[1:3] == ["playwright", "install"]:
    sys.exit(int(os.environ.get("BROWSER_FAIL", "0")))
if "find-websites" in args:
    if os.environ.get("FIND_FAIL"):
        sys.exit(1)
    json.dump({"found": int(os.environ.get("FIND_FOUND", "0")), "searched": 3}, open(args[args.index("--out") + 1], "w"))
    sys.exit(0)
if "--out" not in args or "--max-usd" not in args:
    print(os.environ.get("UV_STDOUT", ""))
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
if [ "$1" = "--lock-path" ]; then echo "$SCHEDULED_STATE_DIR/publish.lock"; exit 0; fi
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
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run([*git, "remote", "add", "origin", str(origin)], check=True)
    subprocess.run([*git, "push", "-q", "origin", "main"], check=True)
    subprocess.run([*git, "fetch", "-q", "origin"], check=True)
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
        OPEN_DAYS_STATE_DIR=str(state),
        SCHEDULED_BACKUPS=str(tmp_path / "backups"),
        CALLS=str(calls),
        UV_PLAN=str(plan),
    )

    class PC:
        def __init__(self):
            self.state, self.checkout = state, checkout

        def merge_to_main(self, path, text):
            """A PR merged on GitHub: a commit on origin's main that the checkout lacks."""
            clone = tmp_path / "clone"
            if not clone.exists():
                subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
            other = ["git", "-C", str(clone), "-c", "user.name=t", "-c", "user.email=t@t"]
            subprocess.run([*other, "pull", "-q", "origin", "main"], check=True)
            (clone / path).parent.mkdir(parents=True, exist_ok=True)
            (clone / path).write_text(text)
            subprocess.run([*other, "add", "."], check=True)
            subprocess.run([*other, "commit", "-qm", f"change {path}"], check=True)
            subprocess.run([*other, "push", "-q", "origin", "main"], check=True)

        def head(self):
            return subprocess.run(
                ["git", "-C", str(checkout), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
            ).stdout.strip()

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


def _find_websites_calls(pc):
    return [c["uv"] for c in pc.calls if "uv" in c and "find-websites" in c["uv"]]


def test_the_months_first_fee_run_first_searches_for_missing_websites(pc):
    pc.run("refresh_fees.sh", "2026-10-20T12:20")
    pc.run("refresh_fees.sh", "2026-11-01T02:20", [{"schools": [5, 8], "spent": 0.8, "stopped": "stopped before school 9: cap"}], FIND_FOUND="2")
    uv = [c["uv"] for c in pc.calls if "uv" in c]
    assert "find-websites" in uv[0] and "--apply" in uv[0] and "--max-usd" in uv[1]  # searched before the read
    assert "find-websites: 2 of 3 schools without a site found one" in pc.log("fees")
    # A carried-on run does not search again: the schools before it were read already.
    pc.run("refresh_fees.sh", "2026-11-02T02:20", [{"schools": [9], "spent": 0.3}])
    assert _find_websites_calls(pc) == [] and pc.option("--after-id") == "8"


def test_nothing_found_publishes_only_what_the_read_wrote(pc):
    pc.run("refresh_fees.sh", "2026-10-20T12:20")
    (pc.state / "fees-2026-11.schools.jsonl").unlink(missing_ok=True)
    pc.run("refresh_fees.sh", "2026-11-01T02:20", [{"schools": [], "spent": 0.0}], FIND_FOUND="0")
    assert pc.code == 0 and len(_find_websites_calls(pc)) == 1 and not pc.published


def test_sites_found_are_published_even_when_no_fee_was_written(pc):
    pc.run("refresh_fees.sh", "2026-10-20T12:20")
    pc.run("refresh_fees.sh", "2026-11-01T02:20", [{"schools": [], "spent": 0.0}], FIND_FOUND="1")
    assert pc.code == 0 and pc.published


def test_a_failed_search_does_not_hold_up_the_fee_read(pc):
    pc.run("refresh_fees.sh", "2026-10-20T12:20")
    pc.run("refresh_fees.sh", "2026-11-01T02:20", [{"schools": [3], "spent": 0.2}], FIND_FAIL="1")
    assert pc.code == 0 and pc.option("--max-usd") == "1.50"
    assert "find-websites failed; the fee read goes on without it" in pc.log("fees")


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
    assert commands == ["install", "run", "scripts/import_sofia_municipal_points.py", "register-pins", "site-address", "confirm-pins"]
    assert pc.calls[-1] == {"pins": "1 0.05"} and pc.code == 0
    assert (pc.state / "register_cycle").read_text().strip() == "2026-11"


def test_a_failed_register_import_stops_before_the_pins_and_tries_three_times_a_month(pc):
    pc.run("refresh_register.sh", "2026-10-20T12:50")
    pc.run("refresh_register.sh", "2026-11-05T12:50", UV_FAIL="1")
    assert pc.code == 1 and len(pc.calls) == 2 and "Scheduled register refresh stopped" in pc.issues
    for day in ("06", "07"):
        assert pc.run("refresh_register.sh", f"2026-11-{day}T12:50", UV_FAIL="1").calls != []
    pc.run("refresh_register.sh", "2026-11-08T12:50", UV_FAIL="1")
    assert pc.calls == [] and "wait for next month" in pc.log("register")
    assert (pc.state / "register_cycle").read_text().strip() == "2026-11"


def test_a_blocked_register_names_the_cause_in_its_issue(pc):
    pc.run("refresh_register.sh", "2026-10-20T12:50")
    pc.run(
        "refresh_register.sh",
        "2026-11-05T12:50",
        UV_FAIL="1",
        UV_STDOUT="Running moe_registry...\n✗ Error: https://ri-api.mon.bg answered with a Cloudflare challenge (HTTP 403)",
    )
    assert pc.code == 1 and "answered with a Cloudflare challenge (HTTP 403)" in pc.issues
    assert "Cloudflare challenge" in pc.log("register")


def test_a_register_browser_that_will_not_install_stops_before_the_import(pc):
    pc.run("refresh_register.sh", "2026-10-20T12:50")
    pc.run("refresh_register.sh", "2026-11-05T12:50", BROWSER_FAIL="1")
    assert pc.code == 1 and len(pc.calls) == 1 and "installing the register's headless browser failed" in pc.issues


def test_an_untracked_file_under_backend_runs_nothing(pc):
    pc.run("refresh_fees.sh", "2026-10-20T12:20")
    (pc.checkout / "backend/sitecustomize.py").write_text("print('not merged')\n")
    pc.run("refresh_fees.sh", "2026-11-01T02:20", [{"schools": [1], "spent": 0.5}])
    assert pc.code == 1 and pc.calls == [] and "untracked files" in pc.log("fees")


def test_a_run_first_brings_the_checkout_up_to_main(pc):
    pc.run("refresh_fees.sh", "2026-10-20T12:20")
    pc.merge_to_main("backend/app/fix.py", "FIXED = True\n")
    pc.run("refresh_fees.sh", "2026-11-01T02:20", [{"schools": [1], "spent": 0.5}])
    assert pc.code == 0 and pc.option("--max-usd") is not None
    assert (pc.checkout / "backend/app/fix.py").exists() and "checkout at" in pc.log("fees")


def test_a_change_to_a_scheduled_job_on_main_runs_the_old_code_and_says_so(pc):
    pc.run("refresh_fees.sh", "2026-10-20T12:20")
    before = pc.head()
    pc.merge_to_main("backend/scripts/refresh_fees.sh", "#!/usr/bin/env bash\n# a higher cap\n")
    pc.run("refresh_fees.sh", "2026-11-01T02:20", [{"schools": [1], "spent": 0.5}])
    assert pc.code == 0 and pc.option("--max-usd") is not None and pc.head() == before
    assert "backend/scripts/refresh_fees.sh" in pc.log("fees") and "checkout not updated" in pc.issues


def test_a_new_migration_on_main_is_not_pulled(pc):
    pc.run("refresh_fees.sh", "2026-10-20T12:20")
    before = pc.head()
    pc.merge_to_main("backend/alembic/versions/abc_new.py", "revision = 'abc'\n")
    pc.run("refresh_fees.sh", "2026-11-01T02:20", [{"schools": [1], "spent": 0.5}])
    assert pc.head() == before and "backend/alembic/versions/abc_new.py" in pc.log("fees")


def test_a_failed_fetch_runs_the_code_it_has_and_says_so(pc):
    pc.run("refresh_fees.sh", "2026-10-20T12:20")
    subprocess.run(["git", "-C", str(pc.checkout), "remote", "set-url", "origin", "/nowhere"], check=True)
    pc.run("refresh_fees.sh", "2026-11-01T02:20", [{"schools": [1], "spent": 0.5}])
    assert pc.code == 0 and "git fetch origin main failed" in pc.log("fees") and "checkout not updated" in pc.issues


def test_a_detached_checkout_moves_to_main(pc):
    pc.run("refresh_fees.sh", "2026-10-20T12:20")
    subprocess.run(["git", "-C", str(pc.checkout), "checkout", "-q", "--detach"], check=True)
    pc.merge_to_main("backend/app/fix.py", "FIXED = True\n")
    pc.run("refresh_fees.sh", "2026-11-01T02:20", [{"schools": [1], "spent": 0.5}])
    assert pc.code == 0 and (pc.checkout / "backend/app/fix.py").exists()


def test_no_update_while_a_publish_runs(pc):
    pc.run("refresh_fees.sh", "2026-10-20T12:20")
    pc.merge_to_main("backend/app/fix.py", "FIXED = True\n")
    with open(pc.state / "publish.lock", "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        pc.run("refresh_fees.sh", "2026-11-01T02:20", [{"schools": [1], "spent": 0.5}])
    assert pc.code == 0 and not (pc.checkout / "backend/app/fix.py").exists()
    assert "a publish is running" in pc.log("fees")
