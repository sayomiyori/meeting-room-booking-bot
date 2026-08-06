"""local room photos and drop Test Room

Revision ID: 0002_local_media
Revises: 0001_initial
Create Date: 2026-08-06
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0002_local_media"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DELETE FROM bookings WHERE room_id IN (SELECT id FROM rooms WHERE name = 'Test Room')")
    op.execute("DELETE FROM rooms WHERE name = 'Test Room'")
    op.execute("UPDATE rooms SET photo_url = '/media/rooms/north.jpg' WHERE name = 'Север'")
    op.execute("UPDATE rooms SET photo_url = '/media/rooms/south.jpg' WHERE name = 'Юг'")
    op.execute("UPDATE rooms SET photo_url = '/media/rooms/east.jpg' WHERE name = 'Восток'")


def downgrade() -> None:
    # Keep local media paths; do not restore external Unsplash URLs
    pass
