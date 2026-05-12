"""Add source_category to sources

Revision ID: c8e4aa9b2f31
Revises: fe3aabf62742

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8e4aa9b2f31"
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
            server_default="genel_gundem",
        ),
    )


def downgrade() -> None:
    op.drop_column("sources", "source_category")
