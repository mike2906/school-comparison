"""Dump each school's website price rows, marking which ones the API publishes."""
import asyncio, json, sys
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from app.database import async_session_maker
from app.main import app
from app.models import Pricing
from app.models.pricing import PriceSource

def v(x):
    return getattr(x, "value", x)

def num(x):
    return float(x) if x is not None else None

async def main(out, ids):
    result = {}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://export") as client:
        for sid in ids:
            async with async_session_maker() as db:
                rows = (await db.execute(select(Pricing).where(
                    Pricing.school_id == sid, Pricing.source == PriceSource.SCRAPED_WEBSITE))).scalars().all()
            response = await client.get(f"/schools/{sid}")
            published = set() if response.status_code == 404 else {p["id"] for p in response.json().get("pricing") or []}
            result[sid] = [{
                "id": r.id, "category": v(r.category), "amount": num(r.amount), "amount_min": num(r.amount_min),
                "amount_max": num(r.amount_max), "currency": r.currency, "period": v(r.period),
                "academic_year": r.academic_year, "age_group": r.age_group, "plan_name": r.plan_name,
                "source_url": r.source_url, "extractor": (r.pricing_context or {}).get("extractor"),
                "evidence": (r.pricing_context or {}).get("evidence"), "published": r.id in published,
            } for r in rows]
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

asyncio.run(main(sys.argv[1], [int(x) for x in sys.argv[2:]]))
