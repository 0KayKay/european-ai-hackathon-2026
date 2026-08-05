from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    TIMESTAMP,
    Boolean,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from challenge.migrations.models.base import Base

"""
Important: these are defined to create a baseline migration for the Chainlit DB schema.
"""

StepTypeEnum = Enum(
    "assistant_message",
    "embedding",
    "llm",
    "retrieval",
    "rerank",
    "run",
    "system_message",
    "tool",
    "undefined",
    "user_message",
    name="StepType",
)


class TimestampMixin:
    """A mixin to add id, created_at, and updated_at columns to a table."""

    id: Mapped[str] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    created_at: Mapped[datetime] = mapped_column(
        "createdAt",
        TIMESTAMP(timezone=False),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        "updatedAt",
        TIMESTAMP(timezone=False),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class Element(TimestampMixin, Base):
    """Represents an element in the Chainlit DB."""

    __tablename__ = "Element"

    thread_id: Mapped[str | None] = mapped_column(
        "threadId", ForeignKey("Thread.id", ondelete="CASCADE", onupdate="CASCADE")
    )
    step_id: Mapped[str] = mapped_column(
        "stepId", ForeignKey("Step.id", ondelete="CASCADE", onupdate="CASCADE")
    )
    metadata_: Mapped[dict | None] = mapped_column(
        "metadata", JSON().with_variant(JSONB(), "postgresql")
    )
    mime: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    object_key: Mapped[str | None] = mapped_column("objectKey", Text)
    url: Mapped[str | None] = mapped_column(Text)
    chainlit_key: Mapped[str | None] = mapped_column("chainlitKey", Text)
    display: Mapped[str | None] = mapped_column(Text)
    size: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(Text)
    page: Mapped[int | None] = mapped_column(Integer)
    props: Mapped[dict | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )

    step: Mapped["Step"] = relationship(back_populates="elements")
    thread: Mapped["Thread"] = relationship(back_populates="elements")


class User(TimestampMixin, Base):
    """Represents a user in the Chainlit DB."""

    __tablename__ = "User"

    metadata_: Mapped[dict | None] = mapped_column(
        "metadata", JSON().with_variant(JSONB(), "postgresql")
    )
    identifier: Mapped[str] = mapped_column(Text, unique=True)

    threads: Mapped[list["Thread"]] = relationship(back_populates="user")


class Feedback(TimestampMixin, Base):
    """Represents feedback in the Chainlit DB."""

    __tablename__ = "Feedback"

    step_id: Mapped[str | None] = mapped_column(
        "stepId", ForeignKey("Step.id", ondelete="SET NULL", onupdate="CASCADE")
    )
    name: Mapped[str] = mapped_column(Text)
    value: Mapped[float] = mapped_column(Float)
    comment: Mapped[str | None] = mapped_column(Text)

    step: Mapped[Optional["Step"]] = relationship(back_populates="feedbacks")


class Step(TimestampMixin, Base):
    """Represents a step in the Chainlit DB."""

    __tablename__ = "Step"
    id: Mapped[str] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    parent_id: Mapped[str | None] = mapped_column(
        "parentId", ForeignKey("Step.id", ondelete="CASCADE", onupdate="CASCADE")
    )
    thread_id: Mapped[str | None] = mapped_column(
        "threadId", ForeignKey("Thread.id", ondelete="CASCADE", onupdate="CASCADE")
    )
    input: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict | None] = mapped_column(
        "metadata", JSON().with_variant(JSONB(), "postgresql")
    )
    name: Mapped[str | None] = mapped_column(Text)
    output: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str] = mapped_column(StepTypeEnum)
    show_input: Mapped[str | None] = mapped_column(
        "showInput", Text, server_default=text("'json'")
    )
    is_error: Mapped[bool | None] = mapped_column(
        "isError", Boolean, server_default=text("false")
    )
    start_time: Mapped[datetime] = mapped_column(
        "startTime",
        TIMESTAMP(timezone=False),
        server_default=text("CURRENT_TIMESTAMP"),
    )
    end_time: Mapped[datetime | None] = mapped_column(
        "endTime", TIMESTAMP(timezone=False)
    )
    parent: Mapped[Optional["Step"]] = relationship(
        remote_side=[id], back_populates="children"
    )
    children: Mapped[list["Step"]] = relationship(
        back_populates="parent", cascade="all, delete"
    )
    thread: Mapped["Thread"] = relationship(back_populates="steps")
    elements: Mapped[list[Element]] = relationship(back_populates="step")
    feedbacks: Mapped[list[Feedback]] = relationship(back_populates="step")


class Thread(TimestampMixin, Base):
    """Represents a thread in the Chainlit DB."""

    __tablename__ = "Thread"

    deleted_at: Mapped[datetime | None] = mapped_column(
        "deletedAt", TIMESTAMP(timezone=False)
    )
    name: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict | None] = mapped_column(
        "metadata", JSON().with_variant(JSONB(), "postgresql")
    )
    user_id: Mapped[str | None] = mapped_column(
        "userId", ForeignKey("User.id", ondelete="SET NULL", onupdate="CASCADE")
    )
    tags: Mapped[list[str] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), server_default=text("'[]'")
    )
    user: Mapped[User | None] = relationship(back_populates="threads")
    steps: Mapped[list[Step]] = relationship(back_populates="thread")
    elements: Mapped[list[Element]] = relationship(back_populates="thread")


# Indexes are defined outside the class definitions
Index("Element_stepId_idx", Element.step_id)
Index("Element_threadId_idx", Element.thread_id)
Index("User_identifier_idx", User.identifier)
Index("Feedback_createdAt_idx", Feedback.created_at)
Index("Feedback_name_idx", Feedback.name)
Index("Feedback_stepId_idx", Feedback.step_id)
Index("Feedback_value_idx", Feedback.value)
Index("Feedback_name_value_idx", Feedback.name, Feedback.value)
Index("Step_createdAt_idx", Step.created_at)
Index("Step_endTime_idx", Step.end_time)
Index("Step_parentId_idx", Step.parent_id)
Index("Step_startTime_idx", Step.start_time)
Index("Step_threadId_idx", Step.thread_id)
Index("Step_type_idx", Step.type)
Index("Step_name_idx", Step.name)
Index(
    "Step_threadId_startTime_endTime_idx",
    Step.thread_id,
    Step.start_time,
    Step.end_time,
)
Index("Thread_createdAt_idx", Thread.created_at)
Index("Thread_name_idx", Thread.name)
