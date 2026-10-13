from pydantic_ai.messages import ModelMessage
from sqlalchemy import (
    JSON,
    ForeignKey,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from _old.challenge.migrations.models.base import Base


class ChatHistory(Base):
    """Represents the full chat history associated with a thread including thinking tokens, tool calls and results."""

    __tablename__ = "ChatHistory"
    thread_id: Mapped[str | None] = mapped_column(
        ForeignKey("Thread.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
        primary_key=True,
    )
    messages: Mapped[list[ModelMessage]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        server_default=text("'[]'"),
        nullable=False,
    )
