"""Demo seeding must refuse live targets before opening a database connection."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import seed_data


@pytest.mark.asyncio
@pytest.mark.parametrize("database_url, reset", [
    ("postgresql+asyncpg://postgres:postgres@localhost/sofia_schools_demo", False),
    ("postgresql+asyncpg://postgres:postgres@localhost/sofia_schools", True),
    ("postgresql+asyncpg://schools:password@postgres/schools", True),
    ("postgresql+asyncpg://postgres:postgres@example.com/sofia_schools_demo", True),
    ("postgresql+asyncpg://postgres:postgres@localhost/sofia_schools_demo?host=example.com", True),
    ("postgresql+asyncpg://postgres:postgres@localhost/sofia_schools_demo?database=schools", True),
    ("sqlite+aiosqlite:///schools.db", True),
    ("not a database URL", True),
])
async def test_unsafe_seed_never_opens_a_connection(monkeypatch, database_url, reset):
    engine = Mock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(seed_data, "get_settings", lambda: SimpleNamespace(DATABASE_URL=database_url))
    monkeypatch.setattr(seed_data, "create_async_engine", engine)
    with pytest.raises(ValueError):
        await seed_data.seed_database(reset_demo_data=reset)
    engine.assert_not_called()


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_explicit_local_demo_reset_is_allowed(host):
    seed_data.validate_seed_target(
        f"postgresql+asyncpg://postgres:postgres@{host}:5432/sofia_schools_demo",
        reset_demo_data=True,
    )
