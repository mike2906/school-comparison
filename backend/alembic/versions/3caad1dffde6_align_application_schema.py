"""Align migrations with the existing application schema

Revision ID: 3caad1dffde6
Revises: 9926b4854ed3
Create Date: 2026-10-05 21:55:11.303909

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '3caad1dffde6'
down_revision: Union[str, None] = '9926b4854ed3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Derived from autogeneration, then made conditional for create_all installations.
    # PostgreSQL performs the existence checks so offline SQL needs no live connection.
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE discrepancytype AS ENUM (
                'NUMERIC_DIFF', 'VALUE_MISMATCH', 'MISSING_FIELD', 'EXTRA_FIELD'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS spot_check_results (
            id SERIAL PRIMARY KEY,
            run_id VARCHAR(36) NOT NULL REFERENCES pipeline_runs(id),
            school_id INTEGER NOT NULL REFERENCES schools(id),
            extractor_name VARCHAR(100) NOT NULL,
            field_name VARCHAR(100) NOT NULL,
            cheap_value TEXT,
            capable_value TEXT,
            discrepancy_type discrepancytype NOT NULL,
            discrepancy_magnitude DOUBLE PRECISION NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL
        )
    """)
    for statement in (
        "ALTER TABLE school_locations ADD COLUMN IF NOT EXISTS district VARCHAR(100)",
        "ALTER TABLE schools ADD COLUMN IF NOT EXISTS institutional_id VARCHAR(20)",
        "ALTER TABLE schools ADD COLUMN IF NOT EXISTS scrape_status VARCHAR(30) DEFAULT 'pending' NOT NULL",
        "ALTER TABLE schools ADD COLUMN IF NOT EXISTS last_full_scrape_at TIMESTAMP WITH TIME ZONE",
        "ALTER TABLE schools ADD COLUMN IF NOT EXISTS city VARCHAR(100)",
        "ALTER TABLE scrape_log ADD COLUMN IF NOT EXISTS run_id VARCHAR(36)",
        "ALTER TABLE scrape_log ADD COLUMN IF NOT EXISTS llm_input_tokens INTEGER",
        "ALTER TABLE scrape_log ADD COLUMN IF NOT EXISTS llm_output_tokens INTEGER",
        "ALTER TABLE scrape_log ADD COLUMN IF NOT EXISTS duration_ms INTEGER",
        "ALTER TABLE source_pages ADD COLUMN IF NOT EXISTS page_category VARCHAR(50)",
        "ALTER TABLE source_pages ADD COLUMN IF NOT EXISTS raw_markdown TEXT",
        "ALTER TABLE source_pages ADD COLUMN IF NOT EXISTS is_valid BOOLEAN",
    ):
        op.execute(statement)


def downgrade() -> None:
    # These fields may predate this revision and contain production data.
    # Keep the additive schema when rolling back the application/revision.
    pass
