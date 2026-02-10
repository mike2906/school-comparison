import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

from app.config import get_settings
from app.database import Base
from app.models import *  # noqa: F401, F403 - Import all models for metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)


def include_object(object, name, type_, reflected, compare_to):
    """Exclude PostGIS and other extension tables from autogenerate."""
    if type_ == "table":
        # Exclude PostGIS TIGER geocoding tables and topology tables
        excluded_tables = {
            'spatial_ref_sys', 'geometry_columns', 'geography_columns',
            'raster_columns', 'raster_overviews',
            # TIGER tables
            'state', 'county', 'cousub', 'place', 'addr', 'addrfeat',
            'edges', 'faces', 'featnames', 'tract', 'tabblock', 'tabblock20',
            'bg', 'zcta5',
            # TIGER lookup tables
            'county_lookup', 'countysub_lookup', 'place_lookup', 'state_lookup',
            'zip_lookup', 'zip_lookup_all', 'zip_lookup_base', 'zip_state',
            'zip_state_loc', 'direction_lookup', 'secondary_unit_lookup',
            'street_type_lookup',
            # TIGER config tables
            'loader_platform', 'loader_lookuptables', 'loader_variables',
            'geocode_settings', 'geocode_settings_default',
            # PAGC tables
            'pagc_gaz', 'pagc_lex', 'pagc_rules',
            # Topology tables
            'topology', 'layer'
        }
        if name in excluded_tables:
            return False
    return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
