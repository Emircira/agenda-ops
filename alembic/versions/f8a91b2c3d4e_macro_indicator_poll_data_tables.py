"""macro_indicators and poll_data (Faz 4.1)

Revision ID: f8a91b2c3d4e
Revises: e643666efc98
Create Date: 2026-05-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f8a91b2c3d4e"
down_revision: Union[str, None] = "e643666efc98"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "macro_indicators",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("indicator_type", sa.String(), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("recorded_date", sa.DateTime(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_macro_indicators")),
    )
    op.create_index(
        op.f("ix_macro_indicators_indicator_type"),
        "macro_indicators",
        ["indicator_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_macro_indicators_recorded_date"),
        "macro_indicators",
        ["recorded_date"],
        unique=False,
    )
    op.create_index(
        "ix_macro_indicators_type_recorded",
        "macro_indicators",
        ["indicator_type", "recorded_date"],
        unique=False,
    )

    op.create_table(
        "poll_data",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("company", sa.String(), nullable=False),
        sa.Column("topic", sa.String(), nullable=False),
        sa.Column("results", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("published_date", sa.Date(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_poll_data")),
    )
    op.create_index(
        op.f("ix_poll_data_company"),
        "poll_data",
        ["company"],
        unique=False,
    )
    op.create_index(
        "ix_poll_data_published",
        "poll_data",
        ["published_date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_poll_data_published", table_name="poll_data")
    op.drop_index(op.f("ix_poll_data_company"), table_name="poll_data")
    op.drop_table("poll_data")

    op.drop_index("ix_macro_indicators_type_recorded", table_name="macro_indicators")
    op.drop_index(op.f("ix_macro_indicators_recorded_date"), table_name="macro_indicators")
    op.drop_index(op.f("ix_macro_indicators_indicator_type"), table_name="macro_indicators")
    op.drop_table("macro_indicators")
