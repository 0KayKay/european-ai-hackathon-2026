import datetime
from enum import Enum
from typing import Any

from sqlalchemy import (
    JSON,
    ForeignKey,
    text, func, UniqueConstraint, Index,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from _old.challenge.migrations.models.base import Base

class ChallengeLevel(str, Enum):
    LEVEL_1 = "LEVEL_1"
    LEVEL_2 = "LEVEL_2"
    LEVEL_3 = "LEVEL_3"
    LEVEL_4 = "LEVEL_4"
    LEVEL_5 = "LEVEL_5"
    LEVEL_6 = "LEVEL_6"
    LEVEL_7 = "LEVEL_7"
    LEVEL_8 = "LEVEL_8"
    LEVEL_9 = "LEVEL_9"

class ChallengeSubmissions(Base):
    """
    Represents the solution submission for a challenge.
    The challenges done via chat interface (Level 1) are initialized with thread_id.
    """

    __tablename__ = "ChallengeSubmissions"
    id: Mapped[str] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    thread_id: Mapped[str] = mapped_column(
        nullable=False,
    )
    # user_id: Mapped[str] = mapped_column(
    #     ForeignKey("User.id", ondelete="CASCADE", onupdate="CASCADE"),
    #     nullable=False,
    # )
    user_identifier: Mapped[str] = mapped_column(
        ForeignKey("User.identifier", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    challenge_level: Mapped[ChallengeLevel]
    # Level 1: the submitted email
    # Level 2: the submitted email content
    # Level 3: the submitted document content
    # not necessary actually, data can also be found embedded in the checkpoint_blobs table.
    submission_data: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        default=dict,
        server_default=text("'{}'"),
        nullable=True,
    )
    # Level 1: Set to true when chat initialized successfully
    # Level 2: When agent for submission ran successfully
    # Level 3: When agent for document submission ran successfully
    submission_executed_successfully: Mapped[bool] = mapped_column(
        nullable=False, default=False
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        nullable=False, server_default=func.now(), onupdate=func.now()
    )
    solved: Mapped[bool] = mapped_column(
        nullable=False, default=False
    )
    solved_at: Mapped[datetime.datetime] = mapped_column(
        nullable=True
    )
    input_tokens_used: Mapped[int] = mapped_column(
        nullable=False, default=0
    )
    output_tokens_used: Mapped[int] = mapped_column(
        nullable=False, default=0
    )

    __table_args__ = (
        UniqueConstraint("user_identifier", "thread_id", name="uq_cs_user_thread"),
        Index("ix_csub_user_identifier", "user_identifier"),
        Index("ix_csub_user_level", "user_identifier", "challenge_level"),
        Index("ix_csub_user_level_thread", "user_identifier", "challenge_level", "thread_id"),
    )
