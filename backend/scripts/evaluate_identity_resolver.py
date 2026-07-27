"""Run the fixed English-identity resolver benchmark without writes or provider calls."""

from __future__ import annotations

import asyncio
import json

from app.database import async_session_maker
from app.services.identity_resolver_benchmark import evaluate_identity_resolver_benchmark


async def main() -> None:
    async with async_session_maker() as db:
        result = await evaluate_identity_resolver_benchmark(db)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
