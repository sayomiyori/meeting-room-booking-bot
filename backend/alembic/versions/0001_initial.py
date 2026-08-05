"""initial schema with exclude constraint

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.create_table(
        "rooms",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("photo_url", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "bookings",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("room_id", sa.Integer(), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("user_display_name", sa.Text(), nullable=False),
        sa.Column("during", postgresql.TSTZRANGE(), nullable=False),
        sa.Column("canceled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("reminder_sent", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["room_id"], ["rooms.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bookings_telegram_id", "bookings", ["telegram_id"])

    op.execute(
        """
        ALTER TABLE bookings
        ADD CONSTRAINT bookings_room_during_excl
        EXCLUDE USING gist (room_id WITH =, during WITH &&)
        WHERE (NOT canceled)
        """
    )

    op.execute(
        """
        CREATE INDEX ix_bookings_reminder_due
        ON bookings (lower(during))
        WHERE NOT canceled AND NOT reminder_sent
        """
    )

    op.execute(
        """
        INSERT INTO rooms (name, capacity, photo_url, description) VALUES
        (
          'Север',
          6,
          'https://images.unsplash.com/photo-1497366216548-37526070297c?w=800&q=80',
          'Компактная переговорная у окна, доски и HDMI.'
        ),
        (
          'Юг',
          10,
          'https://images.unsplash.com/photo-1497366811353-6870744d04b2?w=800&q=80',
          'Просторная комната для командных синкапов.'
        ),
        (
          'Восток',
          4,
          'https://images.unsplash.com/photo-1556761175-5973dc0f32e7?w=800&q=80',
          'Тихая комната для 1:1 и собеседований.'
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_bookings_reminder_due")
    op.execute("ALTER TABLE bookings DROP CONSTRAINT IF EXISTS bookings_room_during_excl")
    op.drop_index("ix_bookings_telegram_id", table_name="bookings")
    op.drop_table("bookings")
    op.drop_table("rooms")
