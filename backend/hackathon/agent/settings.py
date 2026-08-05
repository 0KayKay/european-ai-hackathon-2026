from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class ChatUISettings(BaseModel):
    """Client (user) chat settings."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
    model_id: str
    mode: Literal["PLAYGROUND", "CHALLENGE_1"] = "CHALLENGE_1"
    difficulty: Literal["EASY", "MEDIUM", "HARD"] = "EASY"


class ChatState(BaseModel):
    """Client chat state."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    settings: ChatUISettings
