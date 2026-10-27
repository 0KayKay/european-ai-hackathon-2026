"""Secret generators: how the protected value of a case is produced."""

import hashlib
import hmac
import secrets
import string
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from hackathon.config import get_settings


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class SecretContext:
    """Identity of one case. `thread_id` is the conversation (legacy thread, arena case id).

    `key` is the HMAC key; it defaults to AGENTGATEWAY_API_KEY like the legacy level 1.
    """

    user_id: str
    thread_id: str
    difficulty: str
    key: str | None = None


class HmacMailboxParams(_Params):
    template: str = "finance-fast-{digest}@internal.com"
    digest_length: int = Field(default=16, ge=8, le=64)

    @field_validator("template")
    @classmethod
    def has_digest(cls, value: str) -> str:
        if "{digest}" not in value:
            raise ValueError("template must contain '{digest}'")
        return value


def _hmac_mailbox(params: HmacMailboxParams, context: SecretContext) -> str:
    # Same derivation as hackathon.agent.challenge_1.agent.mailbox_for (legacy difficulties are upper case).
    key = (
        context.key if context.key is not None else get_settings().AGENTGATEWAY_API_KEY
    ).encode()
    message = (
        f"{context.user_id}:{context.thread_id}:{context.difficulty.upper()}".encode()
    )
    digest = hmac.new(key, message, hashlib.sha256).hexdigest()[: params.digest_length]
    return params.template.replace("{digest}", digest)


ALPHABETS = {"letters_digits": string.ascii_letters + string.digits}


class RandomPasswordParams(_Params):
    length: int = Field(default=32, ge=8, le=256)
    alphabet: Literal["letters_digits"] = "letters_digits"


def _random_password(params: RandomPasswordParams, context: SecretContext) -> str:
    alphabet = ALPHABETS[params.alphabet]
    return "".join(secrets.choice(alphabet) for _ in range(params.length))


@dataclass(frozen=True)
class SecretKind:
    name: str
    title: str
    params_model: type[BaseModel]
    generator: Callable[[Any, SecretContext], str]

    def generate(self, params: Any, context: SecretContext) -> str:
        if not isinstance(params, self.params_model):
            params = self.params_model.model_validate(params or {})
        return self.generator(params, context)


SECRET_KINDS: dict[str, SecretKind] = {
    kind.name: kind
    for kind in [
        SecretKind(
            "hmac_mailbox",
            "Mailbox derived per user, case and difficulty",
            HmacMailboxParams,
            _hmac_mailbox,
        ),
        SecretKind(
            "random_password",
            "Random password per case",
            RandomPasswordParams,
            _random_password,
        ),
        SecretKind("none", "No secret", _Params, lambda params, context: ""),
    ]
}
