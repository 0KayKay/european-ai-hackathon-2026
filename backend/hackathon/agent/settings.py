from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel


class ChatUISettings(BaseModel):
    """Client (user) chat settings."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
    model_id: str
    mode: Literal["PLAYGROUND", "CHALLENGE_1", "CHALLENGE_2", "CHALLENGE_3"] = (
        "CHALLENGE_1"
    )
    difficulty: Literal["EASY", "MEDIUM", "HARD"] = "EASY"


class EmailSubmission(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
    kind: Literal["email"]
    subject: str = Field(max_length=300)
    body: str = Field(max_length=10000)


class OfferSubmission(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
    kind: Literal["offer"]
    hidden_instructions: str = Field(max_length=10000)


ChallengeSubmission = Annotated[
    EmailSubmission | OfferSubmission, Field(discriminator="kind")
]


class ChatState(BaseModel):
    """Client chat state."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    settings: ChatUISettings
    submission: ChallengeSubmission | None = None

    @model_validator(mode="after")
    def validate_submission_mode(self) -> "ChatState":
        expected = {"CHALLENGE_2": "email", "CHALLENGE_3": "offer"}.get(
            self.settings.mode
        )
        if expected and (self.submission is None or self.submission.kind != expected):
            raise ValueError(f"{self.settings.mode} requires a {expected} submission")
        return self
