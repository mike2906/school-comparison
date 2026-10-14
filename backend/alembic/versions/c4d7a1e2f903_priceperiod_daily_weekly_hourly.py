"""priceperiod: daily, weekly, hourly

A daily lunch, a weekly summer camp or an hourly class was withheld as a period the
site could not show; the fees agent now stores and the site shows them.

Revision ID: c4d7a1e2f903
Revises: b9e00afbacf2
Create Date: 2026-10-14 20:10:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c4d7a1e2f903'
down_revision: Union[str, None] = 'b9e00afbacf2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Autogenerate does not see new enum values. Postgres stores the member names.
    op.execute("ALTER TYPE priceperiod ADD VALUE IF NOT EXISTS 'DAILY'")
    op.execute("ALTER TYPE priceperiod ADD VALUE IF NOT EXISTS 'WEEKLY'")
    op.execute("ALTER TYPE priceperiod ADD VALUE IF NOT EXISTS 'HOURLY'")


def downgrade() -> None:
    # Postgres enums cannot easily drop values; leaving added values in place.
    pass
