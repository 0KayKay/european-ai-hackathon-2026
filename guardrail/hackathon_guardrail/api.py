import structlog
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

router = APIRouter()
logger = structlog.get_logger("guardrail")


class Message(BaseModel):
    role: str
    content: str


class PromptMessages(BaseModel):
    messages: list[Message] = Field(default_factory=list)


class GuardrailsPromptRequest(BaseModel):
    body: PromptMessages


class ResponseChoice(BaseModel):
    message: Message


class ResponseChoices(BaseModel):
    choices: list[ResponseChoice] = Field(default_factory=list)


class GuardrailsResponseRequest(BaseModel):
    body: ResponseChoices


class PassAction(BaseModel):
    reason: str | None = None


class GuardrailsResponse(BaseModel):
    action: PassAction = Field(default_factory=PassAction)


def log_webhook(request: Request, messages: list[Message]) -> None:
    headers = {
        name: "[REDACTED]"
        if any(
            secret in name.lower()
            for secret in (
                "authorization",
                "cookie",
                "key",
                "token",
                "secret",
                "password",
            )
        )
        else value
        for name, value in request.headers.items()
    }
    logger.info(
        "guardrail_checked",
        phase=request.url.path.removeprefix("/"),
        action="pass",
        user_id=headers.get("x-user-id") or headers.get("x-auth-user"),
        model=headers.get("x-model"),
        request_id=headers.get("x-request-id"),
        client=request.client.host if request.client else None,
        headers=headers,
        message_count=len(messages),
        roles=[message.role for message in messages],
        content_chars=sum(len(message.content) for message in messages),
    )


@router.post("/request", response_model=GuardrailsResponse)
async def process_prompts(
    body: GuardrailsPromptRequest, request: Request
) -> GuardrailsResponse:
    log_webhook(request, body.body.messages)
    return GuardrailsResponse()


@router.post("/response", response_model=GuardrailsResponse)
async def process_responses(
    body: GuardrailsResponseRequest, request: Request
) -> GuardrailsResponse:
    log_webhook(request, [choice.message for choice in body.body.choices])
    return GuardrailsResponse()
