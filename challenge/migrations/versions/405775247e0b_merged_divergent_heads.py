"""Merged divergent heads

Revision ID: 405775247e0b
Revises: af750f93f3c8, ce69db24f321
Create Date: 2025-11-13 15:41:30.107362

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '405775247e0b'
down_revision: Union[str, Sequence[str], None] = ('af750f93f3c8', 'ce69db24f321')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
