"""extend priceperiod enum

Revision ID: ebdc1c57cdac
Revises: 8ba45757e632
Create Date: 2026-02-09 16:23:38.328071

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'ebdc1c57cdac'
down_revision: Union[str, None] = '8ba45757e632'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE priceperiod ADD VALUE IF NOT EXISTS 'TERM'")
    op.execute("ALTER TYPE priceperiod ADD VALUE IF NOT EXISTS 'SEMESTER'")


def downgrade() -> None:
    # NOTE: Postgres enums cannot easily drop values; leaving added values in place.
    pass
