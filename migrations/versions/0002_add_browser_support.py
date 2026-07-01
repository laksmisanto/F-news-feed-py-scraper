"""add browser support

Revision ID: 0002_add_browser
Revises: 0001_add_crawler
Create Date: 2026-06-30 00:00:00.000000

Adds:
  - sources.requires_browser  (BOOLEAN, default FALSE, not null)
  - fetcherusedenum value     'headless'
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0002_add_browser"
down_revision: Union[str, None] = "0001_add_crawler"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. Add 'headless' to the existing fetcherusedenum type ─────────────
    op.execute("ALTER TYPE fetcherusedenum ADD VALUE IF NOT EXISTS 'headless'")

    # ── 2. Add requires_browser BOOLEAN NOT NULL DEFAULT FALSE ────────────
    op.add_column(
        "sources",
        sa.Column(
            "requires_browser",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("sources", "requires_browser")
    # PostgreSQL does not support removing enum values cleanly.
    # The 'headless' value remains on the type after downgrade.
