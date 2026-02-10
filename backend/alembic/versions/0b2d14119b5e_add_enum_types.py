"""add_enum_types

Revision ID: 0b2d14119b5e
Revises: 0001
Create Date: 2026-02-04 11:08:10.122071

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0b2d14119b5e'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create enum types
    op.execute("CREATE TYPE examtype AS ENUM ('NVO_4', 'NVO_7', 'NVO_10')")
    op.execute("CREATE TYPE agegroup AS ENUM ('NURSERY', 'FIRST', 'SECOND', 'THIRD', 'PRESCHOOL', 'GRADE_1_4', 'GRADE_5_7', 'GRADE_8_12')")
    op.execute("CREATE TYPE pricecategory AS ENUM ('TUITION', 'FOOD', 'TRANSPORT', 'ACTIVITIES')")
    op.execute("CREATE TYPE priceperiod AS ENUM ('MONTHLY', 'YEARLY', 'ONE_TIME')")
    op.execute("CREATE TYPE pricesource AS ENUM ('OFFICIAL', 'SCRAPED_WEBSITE', 'FORUM', 'NOT_FOUND')")
    op.execute("CREATE TYPE shift AS ENUM ('MORNING', 'AFTERNOON', 'FULL_DAY')")
    op.execute("CREATE TYPE schooltype AS ENUM ('STATE', 'PRIVATE', 'INTERNATIONAL')")
    op.execute("CREATE TYPE educationlevel AS ENUM ('NURSERY', 'KINDERGARTEN', 'PRIMARY', 'LOWER_SECONDARY', 'UPPER_SECONDARY')")
    op.execute("CREATE TYPE scrapetype AS ENUM ('DISCOVERY', 'WEBSITE', 'PRICES', 'NVO', 'SOCIAL')")
    op.execute("CREATE TYPE scrapestatus AS ENUM ('SUCCESS', 'FAILED', 'SKIPPED')")
    op.execute("CREATE TYPE errortype AS ENUM ('TRANSIENT', 'PERMANENT', 'BLOCKED')")

    # Convert columns to use enum types
    op.execute("ALTER TABLE exam_results ALTER COLUMN exam_type TYPE examtype USING exam_type::examtype")
    op.execute("ALTER TABLE pricing ALTER COLUMN age_group TYPE agegroup USING age_group::agegroup")
    op.execute("ALTER TABLE pricing ALTER COLUMN category TYPE pricecategory USING category::pricecategory")
    op.execute("ALTER TABLE pricing ALTER COLUMN period TYPE priceperiod USING period::priceperiod")
    op.execute("ALTER TABLE pricing ALTER COLUMN source TYPE pricesource USING source::pricesource")
    op.execute("ALTER TABLE school_locations ALTER COLUMN age_group TYPE agegroup USING age_group::agegroup")
    op.execute("ALTER TABLE school_locations ALTER COLUMN shift TYPE shift USING shift::shift")
    op.execute("ALTER TABLE schools ALTER COLUMN school_type TYPE schooltype USING school_type::schooltype")
    op.execute("ALTER TABLE schools ALTER COLUMN education_level TYPE educationlevel USING education_level::educationlevel")
    op.execute("ALTER TABLE scrape_log ALTER COLUMN scrape_type TYPE scrapetype USING scrape_type::scrapetype")
    op.execute("ALTER TABLE scrape_log ALTER COLUMN status TYPE scrapestatus USING status::scrapestatus")
    op.execute("ALTER TABLE scrape_log ALTER COLUMN error_type TYPE errortype USING error_type::errortype")


def downgrade() -> None:
    # Convert columns back to varchar
    op.execute("ALTER TABLE scrape_log ALTER COLUMN error_type TYPE VARCHAR(20)")
    op.execute("ALTER TABLE scrape_log ALTER COLUMN status TYPE VARCHAR(20)")
    op.execute("ALTER TABLE scrape_log ALTER COLUMN scrape_type TYPE VARCHAR(20)")
    op.execute("ALTER TABLE schools ALTER COLUMN education_level TYPE VARCHAR(20)")
    op.execute("ALTER TABLE schools ALTER COLUMN school_type TYPE VARCHAR(20)")
    op.execute("ALTER TABLE school_locations ALTER COLUMN shift TYPE VARCHAR(20)")
    op.execute("ALTER TABLE school_locations ALTER COLUMN age_group TYPE VARCHAR(20)")
    op.execute("ALTER TABLE pricing ALTER COLUMN source TYPE VARCHAR(20)")
    op.execute("ALTER TABLE pricing ALTER COLUMN period TYPE VARCHAR(20)")
    op.execute("ALTER TABLE pricing ALTER COLUMN category TYPE VARCHAR(20)")
    op.execute("ALTER TABLE pricing ALTER COLUMN age_group TYPE VARCHAR(20)")
    op.execute("ALTER TABLE exam_results ALTER COLUMN exam_type TYPE VARCHAR(20)")

    # Drop enum types
    op.execute("DROP TYPE IF EXISTS errortype")
    op.execute("DROP TYPE IF EXISTS scrapestatus")
    op.execute("DROP TYPE IF EXISTS scrapetype")
    op.execute("DROP TYPE IF EXISTS educationlevel")
    op.execute("DROP TYPE IF EXISTS schooltype")
    op.execute("DROP TYPE IF EXISTS shift")
    op.execute("DROP TYPE IF EXISTS pricesource")
    op.execute("DROP TYPE IF EXISTS priceperiod")
    op.execute("DROP TYPE IF EXISTS pricecategory")
    op.execute("DROP TYPE IF EXISTS agegroup")
    op.execute("DROP TYPE IF EXISTS examtype")
