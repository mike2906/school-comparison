"""Initial schema

Revision ID: 0001
Revises:
Create Date: 2026-02-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Schools table
    op.create_table(
        'schools',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(500), nullable=False),
        sa.Column('school_type', sa.String(20), nullable=False),
        sa.Column('education_level', sa.String(20), nullable=False),
        sa.Column('source_url', sa.String(1000)),
        sa.Column('website_url', sa.String(1000)),
        sa.Column('summary_bg', sa.Text()),
        sa.Column('summary_en', sa.Text()),
        sa.Column('num_pupils', sa.Integer()),
        sa.Column('admission_info', sa.JSON()),
        sa.Column('attributes', sa.JSON()),
        sa.Column('page_hash', sa.String(64)),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
    )

    # School locations table
    op.create_table(
        'school_locations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('school_id', sa.Integer(), sa.ForeignKey('schools.id', ondelete='CASCADE'), nullable=False),
        sa.Column('age_group', sa.String(20), nullable=False),
        sa.Column('address', sa.String(500), nullable=False),
        sa.Column('lat', sa.Float()),
        sa.Column('lng', sa.Float()),
        sa.Column('phone', sa.String(100)),
        sa.Column('shift', sa.String(20)),
        sa.Column('has_organised_groups', sa.Boolean()),
        sa.Column('is_primary', sa.Boolean(), default=True),
    )
    op.create_index('ix_school_locations_school_id', 'school_locations', ['school_id'])
    op.create_index('ix_school_locations_age_group', 'school_locations', ['age_group'])

    # Pricing table
    op.create_table(
        'pricing',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('school_id', sa.Integer(), sa.ForeignKey('schools.id', ondelete='CASCADE'), nullable=False),
        sa.Column('age_group', sa.String(20)),
        sa.Column('category', sa.String(20), nullable=False),
        sa.Column('amount', sa.Numeric(10, 2), nullable=False),
        sa.Column('currency', sa.String(3), default='BGN'),
        sa.Column('period', sa.String(20), nullable=False),
        sa.Column('source', sa.String(20), nullable=False),
        sa.Column('source_url', sa.String(1000)),
        sa.Column('scraped_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index('ix_pricing_school_id', 'pricing', ['school_id'])

    # Exam results table
    op.create_table(
        'exam_results',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('school_id', sa.Integer(), sa.ForeignKey('schools.id', ondelete='CASCADE'), nullable=False),
        sa.Column('year', sa.Integer(), nullable=False),
        sa.Column('exam_type', sa.String(20), nullable=False),
        sa.Column('subject', sa.String(100), nullable=False),
        sa.Column('metric', sa.String(100), nullable=False),
        sa.Column('value', sa.Numeric(10, 2), nullable=False),
        sa.Column('source_url', sa.String(1000)),
        sa.Column('scraped_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index('ix_exam_results_school_id', 'exam_results', ['school_id'])

    # Scrape log table
    op.create_table(
        'scrape_log',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('school_id', sa.Integer(), sa.ForeignKey('schools.id', ondelete='SET NULL')),
        sa.Column('scrape_type', sa.String(20), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('page_hash', sa.String(64)),
        sa.Column('raw_html', sa.Text()),
        sa.Column('error_message', sa.Text()),
        sa.Column('error_type', sa.String(20)),
        sa.Column('retry_count', sa.Integer(), default=0),
        sa.Column('next_retry_at', sa.DateTime()),
        sa.Column('model_used', sa.String(100)),
        sa.Column('scraped_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index('ix_scrape_log_school_id', 'scrape_log', ['school_id'])
    op.create_index('ix_scrape_log_scraped_at', 'scrape_log', ['scraped_at'])


def downgrade() -> None:
    op.drop_table('scrape_log')
    op.drop_table('exam_results')
    op.drop_table('pricing')
    op.drop_table('school_locations')
    op.drop_table('schools')
