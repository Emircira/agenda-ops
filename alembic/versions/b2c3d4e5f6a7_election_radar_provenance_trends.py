"""election radar: provenance, demographic stats, region trends, tuik source fields

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-05-12

Sıfır veritabanında election_results ve TÜİK demografi tabloları f70/fe3 zincirinde
yoktu (yalnızca uygulama create_all ile oluşuyordu). Bu adımdan önce eksik tabloları
ve electioncategory enum'unu oluştururuz; ardından mevcut ALTER/CREATE devam eder.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ensure_election_category_enum() -> postgresql.ENUM:
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE electioncategory AS ENUM (
                'presidential', 'parliamentary', 'local', 'referendum'
            );
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$;
        """
    )
    return postgresql.ENUM(
        "presidential",
        "parliamentary",
        "local",
        "referendum",
        name="electioncategory",
        create_type=False,
    )


def _ensure_base_election_tables(ec: postgresql.ENUM) -> None:
    bind = op.get_bind()
    if bind is None:
        return
    names = set(inspect(bind).get_table_names())

    if "election_results" not in names:
        op.create_table(
            "election_results",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("election_year", sa.Integer(), nullable=False),
            sa.Column("election_type", ec, nullable=False),
            sa.Column("election_detail", sa.String(), nullable=True),
            sa.Column("province", sa.String(), nullable=False),
            sa.Column("district", sa.String(), nullable=True),
            sa.Column("party", sa.String(), nullable=False),
            sa.Column("vote_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "raw_data",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_election_results_election_year", "election_results", ["election_year"])
        op.create_index("ix_election_results_election_type", "election_results", ["election_type"])
        op.create_index("ix_election_results_election_detail", "election_results", ["election_detail"])
        op.create_index("ix_election_results_province", "election_results", ["province"])
        op.create_index("ix_election_results_district", "election_results", ["district"])
        op.create_index("ix_election_results_party", "election_results", ["party"])

    if "candidate_demographics" not in names:
        op.create_table(
            "candidate_demographics",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("election_year", sa.Integer(), nullable=False),
            sa.Column("election_type", ec, nullable=False),
            sa.Column("province", sa.String(), nullable=False),
            sa.Column("party", sa.String(), nullable=False),
            sa.Column("gender", sa.String(), nullable=True),
            sa.Column("education", sa.String(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_candidate_demographics_election_year",
            "candidate_demographics",
            ["election_year"],
        )
        op.create_index(
            "ix_candidate_demographics_election_type",
            "candidate_demographics",
            ["election_type"],
        )
        op.create_index("ix_candidate_demographics_province", "candidate_demographics", ["province"])
        op.create_index("ix_candidate_demographics_party", "candidate_demographics", ["party"])

    if "city_demographics" not in names:
        op.create_table(
            "city_demographics",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("province", sa.String(), nullable=True),
            sa.Column("year", sa.Integer(), nullable=True),
            sa.Column("total_population", sa.Integer(), nullable=True, server_default="0"),
            sa.Column("growth_rate", sa.Float(), nullable=True, server_default="0.0"),
            sa.Column("university_grad_pct", sa.Float(), nullable=True, server_default="0.0"),
            sa.Column("unemployment_rate", sa.Float(), nullable=True, server_default="0.0"),
            sa.Column("foreign_pop_pct", sa.Float(), nullable=True, server_default="0.0"),
            sa.Column("literacy_rate", sa.Float(), nullable=True, server_default="0.0"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_city_demographics_id", "city_demographics", ["id"])
        op.create_index("ix_city_demographics_province", "city_demographics", ["province"])

    if "district_demographics" not in names:
        op.create_table(
            "district_demographics",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("province", sa.String(), nullable=True),
            sa.Column("district", sa.String(), nullable=True),
            sa.Column("year", sa.Integer(), nullable=True),
            sa.Column("total_population", sa.Integer(), nullable=True, server_default="0"),
            sa.Column("growth_rate", sa.Float(), nullable=True, server_default="0.0"),
            sa.Column("university_grad_pct", sa.Float(), nullable=True, server_default="0.0"),
            sa.Column("unemployment_rate", sa.Float(), nullable=True, server_default="0.0"),
            sa.Column("foreign_pop_pct", sa.Float(), nullable=True, server_default="0.0"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_district_demographics_id", "district_demographics", ["id"])
        op.create_index("ix_district_demographics_province", "district_demographics", ["province"])
        op.create_index("ix_district_demographics_district", "district_demographics", ["district"])


def upgrade() -> None:
    ec = _ensure_election_category_enum()
    _ensure_base_election_tables(ec)

    op.add_column("election_results", sa.Column("source_json_file", sa.String(), nullable=True))
    op.create_index("ix_election_results_source_json_file", "election_results", ["source_json_file"])

    op.create_table(
        "election_demographic_stats",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("election_year", sa.Integer(), nullable=False),
        sa.Column(
            "election_type",
            postgresql.ENUM(
                "presidential",
                "parliamentary",
                "local",
                "referendum",
                name="electioncategory",
                create_type=False,
            ),
            nullable=False,
        ),
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
        sa.Column(
            "election_type",
            postgresql.ENUM(
                "presidential",
                "parliamentary",
                "local",
                "referendum",
                name="electioncategory",
                create_type=False,
            ),
            nullable=False,
        ),
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
