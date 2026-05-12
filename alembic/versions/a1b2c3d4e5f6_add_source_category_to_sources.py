"""Add source_category to sources (Karargah kaynak tipi)

Revision ID: a1b2c3d4e5f6
Revises: fe3aabf62742
Create Date: 2026-05-12

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "fe3aabf62742"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column(
            "source_category",
            sa.String(),
            nullable=False,
            server_default="general_agenda",
        ),
    )


def downgrade() -> None:
    op.drop_column("sources", "source_category")
