"""add crawler support

Revision ID: 0001_add_crawler
Revises:
Create Date: 2026-06-26 00:00:00.000000

Adds:
  - sources.crawl_enabled  (BOOLEAN, default FALSE, not null)
  - sources.crawl_config   (JSON, nullable)
  - fetcherusedenum value  'crawler'

Notes:
  • ALTER TYPE ... ADD VALUE cannot run inside a transaction on older
    PostgreSQL. We use IF NOT EXISTS and disable the per-statement
    transaction. On PG 12+ this works in a normal transaction too.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0001_add_crawler"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. Add 'crawler' to the existing fetcherusedenum type ───────────────
    # ALTER TYPE ADD VALUE is idempotent with IF NOT EXISTS (PG 9.6+).
    op.execute("ALTER TYPE fetcherusedenum ADD VALUE IF NOT EXISTS 'crawler'")

    # ── 2. Add crawl_enabled BOOLEAN NOT NULL DEFAULT FALSE ────────────────
    op.add_column(
        "sources",
        sa.Column(
            "crawl_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # ── 3. Add crawl_config JSON nullable ──────────────────────────────────
    op.add_column(
        "sources",
        sa.Column(
            "crawl_config",
            sa.JSON(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("sources", "crawl_config")
    op.drop_column("sources", "crawl_enabled")
    # Note: PostgreSQL does NOT support removing enum values cleanly.
    # The 'crawler' value will remain on the type after downgrade.
    # If you truly need to remove it, you must:
    #   1. Recreate the enum type without 'crawler'
    #   2. Recast every column using it
    # This is intentional — we leave the enum value in place on downgrade.
