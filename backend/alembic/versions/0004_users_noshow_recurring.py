"""users whitelist, room active, no-show + recurring fields

Revision ID: 0004_users_noshow_recurring
Revises: 0003_room_names
Create Date: 2026-08-07
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_users_noshow_recurring"
down_revision: Union[str, None] = "0003_room_names"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("display_name", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("role", sa.Text(), server_default=sa.text("'member'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('member', 'admin')", name="ck_users_role"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_id", name="uq_users_telegram_id"),
    )
    op.create_index("ix_users_telegram_id", "users", ["telegram_id"])

    op.add_column(
        "rooms",
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )

    op.add_column(
        "bookings",
        sa.Column("checked_in", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "bookings",
        sa.Column(
            "checkin_prompt_sent",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "bookings",
        sa.Column(
            "auto_canceled_notified",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "bookings",
        sa.Column("recurring_group_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_bookings_recurring_group_id", "bookings", ["recurring_group_id"])

    op.execute(
        """
        CREATE INDEX ix_bookings_checkin_due
        ON bookings (lower(during))
        WHERE NOT canceled AND NOT checkin_prompt_sent
        """
    )
    op.execute(
        """
        CREATE INDEX ix_bookings_noshow_due
        ON bookings (lower(during))
        WHERE NOT canceled AND NOT checked_in AND NOT auto_canceled_notified
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_bookings_noshow_due")
    op.execute("DROP INDEX IF EXISTS ix_bookings_checkin_due")
    op.drop_index("ix_bookings_recurring_group_id", table_name="bookings")
    op.drop_column("bookings", "recurring_group_id")
    op.drop_column("bookings", "auto_canceled_notified")
    op.drop_column("bookings", "checkin_prompt_sent")
    op.drop_column("bookings", "checked_in")
    op.drop_column("rooms", "active")
    op.drop_index("ix_users_telegram_id", table_name="users")
    op.drop_table("users")
