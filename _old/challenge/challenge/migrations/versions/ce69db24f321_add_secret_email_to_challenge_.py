"""add secret_email to challenge_submissions

Revision ID: ce69db24f321
Revises: 397d2f96b2ad
Create Date: 2025-11-13 12:36:51.649495

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ce69db24f321'
down_revision: Union[str, Sequence[str], None] = '397d2f96b2ad'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add secret_email column to ChallengeSubmissions table
    op.add_column('ChallengeSubmissions', sa.Column('secret_email', sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    # Remove secret_email column from ChallengeSubmissions table
    op.drop_column('ChallengeSubmissions', 'secret_email')
