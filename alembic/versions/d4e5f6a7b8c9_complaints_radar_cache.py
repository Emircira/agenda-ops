"""complaints_radar_cache table

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6b7a8
Create Date: 2026-05-12

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6b7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "complaints_radar_cache",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("province_key", sa.String(length=120), nullable=False),
        sa.Column("province_label", sa.String(length=120), nullable=False),
        sa.Column("cached_at", sa.DateTime(), nullable=False),
        sa.Column(
            "payload_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("province_key", name="uq_complaints_radar_cache_province_key"),
    )
    op.create_index(
        "ix_complaints_radar_cache_cached_at",
        "complaints_radar_cache",
        ["cached_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_complaints_radar_cache_cached_at", table_name="complaints_radar_cache")
    op.drop_table("complaints_radar_cache")
