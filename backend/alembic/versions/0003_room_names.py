"""rename rooms to Bolshaya / Malaya / Coworking

Revision ID: 0003_room_names
Revises: 0002_local_media
Create Date: 2026-08-06
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0003_room_names"
down_revision: Union[str, None] = "0002_local_media"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop legacy directional rooms and any leftover Test Room (cascades bookings)
    op.execute(
        """
        DELETE FROM rooms
        WHERE name IN ('Север', 'Юг', 'Восток', 'Test Room', 'Большая', 'Малая', 'Коворкинг')
        """
    )
    op.execute(
        """
        INSERT INTO rooms (name, capacity, photo_url, description) VALUES
        (
          'Большая',
          10,
          '/media/rooms/big.jpg',
          'Живой торец стола, вид на парковку'
        ),
        (
          'Малая',
          8,
          '/media/rooms/small.jpg',
          'Стекло, хром, для быстрых созвонов и встреч 1:1'
        ),
        (
          'Коворкинг',
          4,
          '/media/rooms/coworking.jpg',
          'Лаунж-зона для неформальных встреч'
        )
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM rooms WHERE name IN ('Большая', 'Малая', 'Коворкинг')")
    op.execute(
        """
        INSERT INTO rooms (name, capacity, photo_url, description) VALUES
        ('Север', 6, '/media/rooms/north.jpg', 'Компактная переговорная у окна, доски и HDMI.'),
        ('Юг', 10, '/media/rooms/south.jpg', 'Просторная комната для командных синкапов.'),
        ('Восток', 4, '/media/rooms/east.jpg', 'Тихая комната для 1:1 и собеседований.')
        """
    )
