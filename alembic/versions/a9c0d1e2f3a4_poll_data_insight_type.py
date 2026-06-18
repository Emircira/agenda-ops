"""poll_data: poll_type (grievances | demands)

Revision ID: a9c0d1e2f3a4
Revises: f8a91b2c3d4e
Create Date: 2026-05-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a9c0d1e2f3a4"
down_revision: Union[str, None] = "f8a91b2c3d4e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "poll_data",
        sa.Column(
            "poll_type",
            sa.String(),
            nullable=False,
            server_default=sa.text("'grievances'"),
        ),
    )
    op.create_index(
        "ix_poll_data_poll_type",
        "poll_data",
        ["poll_type"],
        unique=False,
    )
    op.create_index(
        "ix_poll_data_company_type",
        "poll_data",
        ["company", "poll_type"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_poll_data_company_type", table_name="poll_data")
    op.drop_index("ix_poll_data_poll_type", table_name="poll_data")
    op.drop_column("poll_data", "poll_type")
