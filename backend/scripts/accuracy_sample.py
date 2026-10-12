"""Draw the R4 accuracy sample: private and international Sofia schools with a website.

Prints the id, registry name and website of each, as JSON, and nothing the pipeline
extracted, so a reviewer can read the prices from the school's own site blind. The draw
is seeded, so it can be repeated against the same database.

Run from ``backend/``::

    uv run python scripts/accuracy_sample.py --size 30 --seed 20261012
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import async_session_maker  # noqa: E402
from app.models import School  # noqa: E402


async def main(size: int, seed: int) -> None:
    async with async_session_maker() as db:
        rows = (
            await db.execute(
                select(School.id, School.name_i18n, School.website_url)
                .where(
                    School.country_code == "bg",
                    School.city == "sofia",
                    School.school_type.in_(("private", "international")),
                    School.website_url.isnot(None),
                    School.website_url != "",
                )
                .order_by(School.id)
            )
        ).all()
    sample = sorted(random.Random(seed).sample(rows, min(size, len(rows))), key=lambda row: row.id)
    print(
        json.dumps(
            {
                "seed": seed,
                "population": len(rows),
                "schools": [
                    {"school_id": row.id, "name": (row.name_i18n or {}).get("bg"), "website_url": row.website_url}
                    for row in sample
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--size", type=int, default=30)
    parser.add_argument("--seed", type=int, default=20261012)
    args = parser.parse_args()
    asyncio.run(main(args.size, args.seed))
