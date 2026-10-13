from typing import Any

from sqlalchemy import text, ForeignKey, UniqueConstraint, JSON, Index
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from challenge.migrations.models import Base
from _old.challenge.challenge.migrations.models.challenge_submissions import ChallengeLevel


class ChallengeSolutions(Base):
    """
    Represents the solution targeted to each user.
    """

    __tablename__ = "ChallengeSolutions"
    id: Mapped[str] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_identifier: Mapped[str] = mapped_column(
        ForeignKey("User.identifier", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    challenge_level: Mapped[ChallengeLevel] = mapped_column(
        postgresql.ENUM(
            *[e.name for e in ChallengeLevel],
            name='challengelevel',
            create_type=False
        )
    )

    # Level 1: the submitted email
    # Level 2: the secret password
    # Level 3: None at the moment
    # not necessary actually, data can also be found embedded in the checkpoint_blobs table.
    solution_data: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        default=dict,
        server_default=text("'{}'"),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("user_identifier", "challenge_level", name="uq_cs_user_level"),
        Index("ix_cs_user_identifier", "user_identifier"),
        Index("ix_cs_challenge_level", "challenge_level"),
        Index("ix_cs_user_level", "user_identifier", "challenge_level"),
    )


