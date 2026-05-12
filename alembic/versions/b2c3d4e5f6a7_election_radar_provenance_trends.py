"""election radar: provenance, demographic stats, region trends, tuik source fields

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-05-12

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("election_results", sa.Column("source_json_file", sa.String(), nullable=True))
    op.create_index("ix_election_results_source_json_file", "election_results", ["source_json_file"])

    op.create_table(
        "election_demographic_stats",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("election_year", sa.Integer(), nullable=False),
        sa.Column("election_type", postgresql.ENUM("presidential", "parliamentary", "local", "referendum", name="electioncategory", create_type=False), nullable=False),
        sa.Column("election_detail", sa.String(), nullable=True),
        sa.Column("province", sa.String(), nullable=True),
        sa.Column("district", sa.String(), nullable=True),
        sa.Column("party", sa.String(), nullable=False),
        sa.Column("dimension", sa.String(), nullable=False),
        sa.Column("bucket", sa.String(), nullable=False),
        sa.Column("count_value", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_json_file", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_election_demographic_stats_election_year", "election_demographic_stats", ["election_year"])
    op.create_index("ix_election_demographic_stats_election_type", "election_demographic_stats", ["election_type"])
    op.create_index("ix_election_demographic_stats_province", "election_demographic_stats", ["province"])
    op.create_index("ix_election_demographic_stats_source_json_file", "election_demographic_stats", ["source_json_file"])

    op.create_table(
        "election_region_trends",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("province", sa.String(), nullable=False),
        sa.Column("district", sa.String(), nullable=True),
        sa.Column("election_type", postgresql.ENUM("presidential", "parliamentary", "local", "referendum", name="electioncategory", create_type=False), nullable=False),
        sa.Column("party", sa.String(), nullable=False),
        sa.Column("ref_year", sa.Integer(), nullable=False),
        sa.Column("vote_share_pct", sa.Float(), nullable=False),
        sa.Column("vote_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("yoy_delta_pct", sa.Float(), nullable=True),
        sa.Column("source_json_file", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_election_region_trends_province", "election_region_trends", ["province"])
    op.create_index("ix_election_region_trends_election_type", "election_region_trends", ["election_type"])
    op.create_index("ix_election_region_trends_ref_year", "election_region_trends", ["ref_year"])

    op.add_column("candidate_demographics", sa.Column("source_json_file", sa.String(), nullable=True))
    op.create_index("ix_candidate_demographics_source_json_file", "candidate_demographics", ["source_json_file"])

    op.add_column("city_demographics", sa.Column("source_json_file", sa.String(), nullable=False, server_default="city_stats.json"))
    op.add_column("city_demographics", sa.Column("source_category", sa.String(), nullable=False, server_default="tuik_city_aggregate"))
    op.add_column("district_demographics", sa.Column("source_json_file", sa.String(), nullable=False, server_default="district_stats.json"))
    op.add_column("district_demographics", sa.Column("source_category", sa.String(), nullable=False, server_default="tuik_district_aggregate"))


def downgrade() -> None:
    op.drop_column("district_demographics", "source_category")
    op.drop_column("district_demographics", "source_json_file")
    op.drop_column("city_demographics", "source_category")
    op.drop_column("city_demographics", "source_json_file")

    op.drop_index("ix_candidate_demographics_source_json_file", table_name="candidate_demographics")
    op.drop_column("candidate_demographics", "source_json_file")

    op.drop_table("election_region_trends")
    op.drop_table("election_demographic_stats")

    op.drop_index("ix_election_results_source_json_file", table_name="election_results")
    op.drop_column("election_results", "source_json_file")
