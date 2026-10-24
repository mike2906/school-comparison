"""Cache headers for Cloudflare and the list's gzip, compressed once."""
import gzip
import json

import pytest

from app.main import CDN_CACHE_CONTROL, EDGE_CACHE_CONTROL


@pytest.mark.asyncio
async def test_public_get_is_cacheable_for_every_origin(seeded_client):
    response = await seeded_client.get("/schools/filters", headers={"Origin": "https://schooldecider.com"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == EDGE_CACHE_CONTROL
    # The edge serves a stale copy while it refreshes or the API is down; s-maxage would forbid that.
    assert response.headers["cdn-cache-control"] == CDN_CACHE_CONTROL
    assert "stale-while-revalidate" in CDN_CACHE_CONTROL and "s-maxage" not in CDN_CACHE_CONTROL + EDGE_CACHE_CONTROL
    assert response.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in response.headers
    assert "origin" not in response.headers.get("vary", "").lower()

    # A request without Origin (a crawler, the site build) gets the same headers, so a
    # copy the edge keeps from it still works for the site.
    plain = await seeded_client.get("/schools/filters")
    assert plain.headers["access-control-allow-origin"] == "*"


@pytest.mark.asyncio
async def test_errors_and_health_are_not_cached(seeded_client):
    missing = await seeded_client.get("/schools/999999")
    assert missing.status_code == 404
    assert missing.headers["cache-control"] == "no-store"
    assert "cdn-cache-control" not in missing.headers
    health = await seeded_client.get("/health")
    assert health.headers["cache-control"] == "no-store"
    assert "access-control-allow-origin" not in health.headers


@pytest.mark.asyncio
async def test_post_keeps_the_origin_allowlist(seeded_client):
    response = await seeded_client.post("/search-misses", json={"q": "zz"}, headers={"Origin": "https://evil.example"})
    assert response.headers.get("access-control-allow-origin") != "*"
    assert "cache-control" not in response.headers


@pytest.mark.asyncio
async def test_list_is_served_from_its_stored_gzip(seeded_client):
    from app.routers import schools as schools_router

    plain = await seeded_client.get("/schools", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in plain.headers
    (_, gzipped), = schools_router._list_cache.values()
    body = gzip.decompress(gzipped)
    assert "Accept-Encoding" in plain.headers["vary"]

    compressed = await seeded_client.get("/schools", headers={"Accept-Encoding": "gzip"})
    assert compressed.headers["content-encoding"] == "gzip"
    assert "Accept-Encoding" in compressed.headers["vary"]
    assert compressed.json() == plain.json() == json.loads(body)


@pytest.mark.asyncio
async def test_other_gets_are_not_public(seeded_client):
    response = await seeded_client.get("/openapi.json", headers={"Origin": "https://schooldecider.com"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers.get("access-control-allow-origin") != "*"
