import structlog
from chainlit import (
    Message,
    User,
    ChatProfile,
    context,
    data_layer,
    on_chat_end,
    on_chat_resume,
    on_chat_start,
    on_logout,
    on_message,
    user_session,
    set_chat_profiles,
    password_auth_callback,
)
from chainlit.auth.cookie import clear_auth_cookie
from chainlit.data.base import BaseDataLayer
from chainlit.types import ThreadDict
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from structlog.stdlib import BoundLogger

from _old.challenge.challenges.agent import create_agent
from _old.challenge.challenges.dependencies import build_dependencies, AgentDependencies
from _old.challenge.chat.datalayer import get_data_layer
from _old.challenge.chat.event_stream_handler import handle_event_stream
from _old.challenge.chat.exceptions import handle_chat_exceptions

logger: BoundLogger = structlog.get_logger("chat_handler")


@on_chat_start
@handle_chat_exceptions
async def _handle_chat_start() -> None:
    logger.debug(f"Chat started with session id: {user_session.get('id')}")
    user_session.set("chat_history", [])
    chat_profile = user_session.get("chat_profile")
    agent = await create_agent(chat_profile)
    user_session.set("agent", agent)


@on_chat_resume
@handle_chat_exceptions
async def _handle_chat_resume(thread: ThreadDict) -> None:
    logger.debug(f"Chat resumed with session id: {user_session.get('id')}")
    chat_history = await get_data_layer().get_chat_history(thread["id"])
    user_session.set("chat_history", chat_history)
    agent = await create_agent(user_session.get("chat_profile"))
    user_session.set("agent", agent)


@on_chat_end
@handle_chat_exceptions
async def _handle_chat_end() -> None:
    logger.debug(f"Chat ended with session id: {user_session.get('id')}")
    chat_history: list[ModelMessage] = user_session.get("chat_history")
    if not chat_history or len(chat_history) == 0:
        return

    await get_data_layer().set_chat_history(
        context.session.thread_id,
        chat_history,
    )


@on_message
@handle_chat_exceptions
async def _handle_chat_message(incoming: Message) -> None:
    logger.debug(f"Message received: {incoming.content}")
    agent: Agent[AgentDependencies] = user_session.get("agent")
    handle_stream, handle_cleanup = await handle_event_stream()
    try:
        message_history = user_session.get("chat_history", None)
        response = await agent.run(
            user_prompt=str(incoming.content),
            message_history=message_history,
            event_stream_handler=handle_stream,
            deps=build_dependencies(),
        )
        user_session.set("chat_history", response.all_messages())

    except Exception as e:
        await handle_cleanup()
        raise e


@on_logout
async def _handle_logout(request: Request, _: Response) -> JSONResponse:
    logger.debug("User logged out")
    response = JSONResponse({"success": True}, status_code=202)
    clear_auth_cookie(request, response)
    return response


@data_layer
def _get_data_layer() -> BaseDataLayer:
    return get_data_layer()


@set_chat_profiles
async def _handle_set_chat_profiles(
    _: User | None, __: str | None
) -> list[ChatProfile]:
    return [
        ChatProfile(
            name=f"Level {level}",
            markdown_description=f"Challenge Level {level}",
            icon="/public/icon.svg",
        )
        for level in range(1, 7)
    ]


@password_auth_callback
async def password_auth_callback(username: str, password: str) -> User | None:
    # TODO
    if "@codify.ch" in username or "@lapiscode.com" in username:
        return User(identifier=username, display_name=username.title())
    return None
