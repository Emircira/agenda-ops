"""election_region_archives + election_detail on trends

Revision ID: c3d4e5f6b7a8
Revises: b2c3d4e5f6a7
Create Date: 2026-05-12

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "c3d4e5f6b7a8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "election_region_archives",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("election_year", sa.Integer(), nullable=False),
        sa.Column(
            "election_type",
            postgresql.ENUM("presidential", "parliamentary", "local", "referendum", name="electioncategory", create_type=False),
            nullable=False,
        ),
        sa.Column("election_detail", sa.String(length=180), nullable=False, server_default=""),
        sa.Column("province", sa.String(length=120), nullable=False),
        sa.Column("district_key", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("total_valid_votes", sa.Integer(), nullable=True),
        sa.Column("winner_party", sa.String(length=200), nullable=True),
        sa.Column("winner_candidate", sa.String(length=300), nullable=True),
        sa.Column("results_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("demographics_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("source_files_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "election_year",
            "election_type",
            "election_detail",
            "province",
            "district_key",
            name="uq_election_region_archive",
        ),
    )
    op.create_index("ix_election_region_archives_election_year", "election_region_archives", ["election_year"])
    op.create_index("ix_election_region_archives_election_type", "election_region_archives", ["election_type"])
    op.create_index("ix_election_region_archives_election_detail", "election_region_archives", ["election_detail"])
    op.create_index("ix_election_region_archives_province", "election_region_archives", ["province"])

    op.add_column(
        "election_region_trends",
        sa.Column("election_detail", sa.String(length=180), nullable=True),
    )
    op.create_index("ix_election_region_trends_election_detail", "election_region_trends", ["election_detail"])


def downgrade() -> None:
    op.drop_index("ix_election_region_trends_election_detail", table_name="election_region_trends")
    op.drop_column("election_region_trends", "election_detail")
    op.drop_table("election_region_archives")
