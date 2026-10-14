"""Archive РУО София-град 8th-grade min/max score files listed in ruo_sofia_min_max_sources.json.

Downloads each listed file not yet captured, checks its sha256, parses it and writes
aggregate rows to the cut-off archive (docs/ADMISSION_CUTOFFS.md). Run by the capture
workflow after the ИСОДЗ capture; needs no database.

    uv run python -m scripts.capture_ruo_min_max --archive ../cutoff-archive
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import time
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

from app.scrapers.sources.bg.ruo_sofia_min_max import SOURCE, archive_rows, load_sources, parse_workbook
from app.services.admission_cutoffs import load_state, save_state, write_rows

logger = logging.getLogger("capture_ruo_min_max")


def capture(archive: Path, delay: float = 2.0) -> dict:
    state = load_state(archive)
    ruo_state = state.setdefault(SOURCE, {})
    captured: dict[str, str] = ruo_state.setdefault("captured", {})
    summary = {"files": 0, "rows": 0, "failed": [], "sha_changed": []}
    with httpx.Client(timeout=60.0, follow_redirects=True, headers={"User-Agent": "schooldecider.com cut-off archive"}) as http:
        for source in load_sources():
            url = source["url"]
            if url in captured:
                continue
            if summary["files"] or summary["failed"]:
                time.sleep(delay)
            try:
                response = http.get(url)
                response.raise_for_status()
                content = response.content
                if not content.startswith(b"PK"):
                    raise ValueError(f"not an XLSX ({response.headers.get('content-type')})")
                digest = hashlib.sha256(content).hexdigest()
                if source.get("sha256") and digest != source["sha256"]:
                    logger.warning("%s: sha256 %s differs from the listed %s; using the new file", url, digest, source["sha256"])
                    summary["sha_changed"].append(url)
                classified_on, rows = parse_workbook(content, int(source["year"]))
                if classified_on and classified_on != date.fromisoformat(source["date"]):
                    logger.warning("%s: file says %s, list says %s", url, classified_on, source["date"])
                if not rows:
                    raise ValueError("no rows parsed")
                retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                new_rows = archive_rows(source, classified_on, rows, retrieved_at)
                write_rows(archive, new_rows)
                captured[url] = digest
                ruo_state.get("failed", {}).pop(url, None)
                save_state(archive, state)
                summary["files"] += 1
                summary["rows"] += len(new_rows)
                logger.info(
                    "%s round %s: %d classes (%d quota, %d with nobody admitted)",
                    source["year"], source["round"], len(new_rows),
                    sum(r.quota for r in rows), len(rows) - len(new_rows),
                )
            except Exception as error:  # one bad file must not stop the others
                logger.error("%s: %s", url, error)
                summary["failed"].append(url)
                ruo_state.setdefault("failed", {})[url] = f"{datetime.now(timezone.utc).date()}: {error}"
                save_state(archive, state)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    summary = capture(args.archive)
    print(summary)


if __name__ == "__main__":
    main()
