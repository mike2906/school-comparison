"""Capture Sofia ИСОДЗ round results (kindergarten, nursery, ПГУ, first grade) into the archive.

Run by .github/workflows/capture-cutoffs.yml on a schedule; needs no database. Each run
fetches the institution lists and their groups, then every (round, group) list not yet in
the archive, and writes aggregate rows only (app.scrapers.sources.bg.kg_sofia_ratings).
Resumable: rows are saved after each institution, and ``--max-requests`` stops a run
cleanly so the next one carries on.

    uv run python -m scripts.capture_isodz_cutoffs --archive ../cutoff-archive
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

from app.scrapers.sources.bg.kg_sofia_ratings import (
    INSTITUTION_TYPES,
    SOFIA_TZ,
    IsodzClient,
    aggregate_round,
    group_rounds,
    rows_for_round,
)
from app.services.admission_cutoffs import iter_rows, load_state, save_state, write_rows

logger = logging.getLogger("capture_isodz_cutoffs")

FULL_SCAN_EVERY_DAYS = 7
ROUND_EVENT_MARKER = "класиране"


class Budget(Exception):
    pass


def _captured_keys(archive: Path) -> set[tuple[int, int]]:
    return {
        (int(row["round_id"]), int(row["group_id"]))
        for row in iter_rows(archive)
        if row.get("source") == "kg.sofia.bg" and row.get("group_id") is not None
    }


def round_published_since(events: list[dict], since: date, today: date) -> bool:
    for event in events:
        title = (event.get("title") or "").lower()
        try:
            start = date.fromisoformat(event.get("start") or "")
        except ValueError:
            continue
        if ROUND_EVENT_MARKER in title and since <= start <= today:
            return True
    return False


async def _capture_institution(client, dz, family, today, captured, empty, rows, summary, spend) -> None:
    for round_ in group_rounds(dz, family):
        key = f"{round_.rating_id}:{round_.group_id}"
        if (round_.rating_id, round_.group_id) in captured or key in empty:
            continue
        if round_.classified_on > today:
            continue
        spend(3)
        rated = await client.round_list("rated", round_.rating_id, round_.group_id)
        if rated is None:
            if round_.classified_on < today:
                empty.add(key)
                summary["empty_lists"] += 1
            continue
        not_rated = await client.round_list("not-rated", round_.rating_id, round_.group_id)
        entry = await client.round_list("entry", round_.rating_id, round_.group_id)
        retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        new_rows = rows_for_round(round_, aggregate_round(rated, not_rated, entry), retrieved_at)
        if not new_rows:
            empty.add(key)
            summary["empty_lists"] += 1
            continue
        rows.extend(new_rows)
        captured.add((round_.rating_id, round_.group_id))
        summary["rounds_captured"] += 1


async def capture(archive: Path, *, delay: float, max_requests: int, force: bool, only: list[str]) -> dict:
    today = datetime.now(SOFIA_TZ).date()
    state = load_state(archive)
    isodz_state = state.setdefault("kg.sofia.bg", {})
    empty: set[str] = set(isodz_state.get("empty_lists", []))
    captured = _captured_keys(archive)
    summary = {"rounds_captured": 0, "rows": 0, "empty_lists": 0, "requests": 0, "stopped_early": False}

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, headers={"User-Agent": "schooldecider.com cut-off archive"}) as http:
        client = IsodzClient(http, delay=delay)

        last_scan = isodz_state.get("last_full_scan")
        if not force and last_scan and not isodz_state.get("incomplete"):
            last = date.fromisoformat(last_scan)
            events = await client.calendar()
            if today - last < timedelta(days=FULL_SCAN_EVERY_DAYS) and not round_published_since(events, last, today):
                logger.info("No round published since %s; nothing to do.", last)
                summary["requests"] = client.requests
                return summary

        def spend(n: int = 1) -> None:
            if client.requests + n > max_requests:
                raise Budget()

        try:
            for list_type, (family, endpoint_type) in INSTITUTION_TYPES.items():
                if only and family not in only:
                    continue
                spend()
                institutions = await client.institutions(list_type)
                logger.info("%s: %d institutions", list_type, len(institutions))
                for institution in institutions:
                    if institution.get("id") is None:
                        continue
                    spend()
                    dz = await client.dz(endpoint_type, int(institution["id"]))
                    rows = []
                    try:
                        await _capture_institution(client, dz, family, today, captured, empty, rows, summary, spend)
                    finally:
                        if rows:
                            write_rows(archive, rows)
                            summary["rows"] += len(rows)
                        isodz_state["empty_lists"] = sorted(empty)
                        save_state(archive, state)
            isodz_state["last_full_scan"] = today.isoformat()
            isodz_state.pop("incomplete", None)
        except Budget:
            summary["stopped_early"] = True
            isodz_state["incomplete"] = True
            logger.warning("Request budget of %d reached; the next run continues.", max_requests)
        finally:
            isodz_state["empty_lists"] = sorted(empty)
            isodz_state["last_run"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            save_state(archive, state)
            summary["requests"] = client.requests
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--archive", type=Path, required=True, help="cut-off archive checkout (the cutoff-data branch)")
    parser.add_argument("--delay", type=float, default=0.5, help="seconds between requests")
    parser.add_argument("--max-requests", type=int, default=7000)
    parser.add_argument("--force", action="store_true", help="scan even if no round was published")
    parser.add_argument("--only", nargs="*", default=[], choices=sorted({family for family, _ in INSTITUTION_TYPES.values()}))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    summary = asyncio.run(
        capture(args.archive, delay=args.delay, max_requests=args.max_requests, force=args.force, only=args.only)
    )
    print(summary)


if __name__ == "__main__":
    main()
