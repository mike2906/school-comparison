"""add exam result pupil count

Revision ID: b9e00afbacf2
Revises: 3caad1dffde6
Create Date: 2026-10-08 11:06:21.944160

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b9e00afbacf2'
down_revision: Union[str, None] = '3caad1dffde6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Expand-only: nullable, so the previous image keeps working.
    op.add_column('exam_results', sa.Column('pupil_count', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('exam_results', 'pupil_count')
