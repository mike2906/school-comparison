"""Align migrations with the existing application schema

Revision ID: 3caad1dffde6
Revises: 9926b4854ed3
Create Date: 2026-10-05 21:55:11.303909

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '3caad1dffde6'
down_revision: Union[str, None] = '9926b4854ed3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing installations used create_all for these fields. Preserve them.
    inspector = sa.inspect(op.get_bind())
    columns = {table: {c['name'] for c in inspector.get_columns(table)}
               for table in ('school_locations', 'schools', 'scrape_log', 'source_pages')}
    if not inspector.has_table('spot_check_results'):
        op.create_table('spot_check_results',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('run_id', sa.String(length=36), nullable=False),
        sa.Column('school_id', sa.Integer(), nullable=False),
        sa.Column('extractor_name', sa.String(length=100), nullable=False),
        sa.Column('field_name', sa.String(length=100), nullable=False),
        sa.Column('cheap_value', sa.Text(), nullable=True),
        sa.Column('capable_value', sa.Text(), nullable=True),
        sa.Column('discrepancy_type', sa.Enum('NUMERIC_DIFF', 'VALUE_MISMATCH', 'MISSING_FIELD', 'EXTRA_FIELD', name='discrepancytype'), nullable=False),
        sa.Column('discrepancy_magnitude', sa.Float(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['run_id'], ['pipeline_runs.id'], ),
        sa.ForeignKeyConstraint(['school_id'], ['schools.id'], ),
        sa.PrimaryKeyConstraint('id')
        )

    if 'district' not in columns['school_locations']:
        op.add_column('school_locations', sa.Column('district', sa.String(length=100), nullable=True))
    if 'institutional_id' not in columns['schools']:
        op.add_column('schools', sa.Column('institutional_id', sa.String(length=20), nullable=True))
    if 'scrape_status' not in columns['schools']:
        op.add_column('schools', sa.Column('scrape_status', sa.String(length=30), server_default='pending', nullable=False))
    if 'last_full_scrape_at' not in columns['schools']:
        op.add_column('schools', sa.Column('last_full_scrape_at', sa.DateTime(timezone=True), nullable=True))
    if 'city' not in columns['schools']:
        op.add_column('schools', sa.Column('city', sa.String(length=100), nullable=True))
    if 'run_id' not in columns['scrape_log']:
        op.add_column('scrape_log', sa.Column('run_id', sa.String(length=36), nullable=True))
    if 'llm_input_tokens' not in columns['scrape_log']:
        op.add_column('scrape_log', sa.Column('llm_input_tokens', sa.Integer(), nullable=True))
    if 'llm_output_tokens' not in columns['scrape_log']:
        op.add_column('scrape_log', sa.Column('llm_output_tokens', sa.Integer(), nullable=True))
    if 'duration_ms' not in columns['scrape_log']:
        op.add_column('scrape_log', sa.Column('duration_ms', sa.Integer(), nullable=True))
    if 'page_category' not in columns['source_pages']:
        op.add_column('source_pages', sa.Column('page_category', sa.String(length=50), nullable=True))
    if 'raw_markdown' not in columns['source_pages']:
        op.add_column('source_pages', sa.Column('raw_markdown', sa.Text(), nullable=True))
    if 'is_valid' not in columns['source_pages']:
        op.add_column('source_pages', sa.Column('is_valid', sa.Boolean(), nullable=True))


def downgrade() -> None:
    # These fields may predate this revision and contain production data.
    # Keep the additive schema when rolling back the application/revision.
    pass
