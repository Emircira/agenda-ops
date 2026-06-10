"""radar_user_daily table (Radar modülü)

Revision ID: b7c1d2e3f4a5
Revises: 3d9add235bd8
Create Date: 2026-06-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b7c1d2e3f4a5"
down_revision: Union[str, None] = "3d9add235bd8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "radar_user_daily",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("platform", sa.String(), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("posts_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("comments_received", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_engagement", sa.Float(), nullable=False, server_default="0"),
        sa.Column("reach_estimate", sa.Float(), nullable=False, server_default="0"),
        sa.Column("consecutive_days", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("radar_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("computed_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_radar_user_daily")),
        sa.UniqueConstraint("platform", "username", "day", name="uq_radar_user_daily"),
    )
    op.create_index(op.f("ix_radar_user_daily_platform"), "radar_user_daily", ["platform"], unique=False)
    op.create_index(op.f("ix_radar_user_daily_username"), "radar_user_daily", ["username"], unique=False)
    op.create_index(op.f("ix_radar_user_daily_day"), "radar_user_daily", ["day"], unique=False)
    op.create_index(op.f("ix_radar_user_daily_radar_score"), "radar_user_daily", ["radar_score"], unique=False)
    op.create_index("ix_radar_user_daily_day_score", "radar_user_daily", ["day", "radar_score"], unique=False)
    op.create_index("ix_radar_user_daily_platform_day", "radar_user_daily", ["platform", "day"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_radar_user_daily_platform_day", table_name="radar_user_daily")
    op.drop_index("ix_radar_user_daily_day_score", table_name="radar_user_daily")
    op.drop_index(op.f("ix_radar_user_daily_radar_score"), table_name="radar_user_daily")
    op.drop_index(op.f("ix_radar_user_daily_day"), table_name="radar_user_daily")
    op.drop_index(op.f("ix_radar_user_daily_username"), table_name="radar_user_daily")
    op.drop_index(op.f("ix_radar_user_daily_platform"), table_name="radar_user_daily")
    op.drop_table("radar_user_daily")
