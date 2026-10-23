from datetime import datetime
from typing import NotRequired, TypedDict
from collections.abc import Callable


class AgentDependencies(TypedDict):
    """Dependencies available in the agent's RunContext"""

    current_date: datetime
    user_id: str
    mailbox: str
    difficulty: str
    mode: str
    submission: dict
    secret: str
    user_name: NotRequired[str]
    user_email: NotRequired[str]
    input_checked: NotRequired[bool]
    solved: NotRequired[bool]
    guardrails: NotRequired[list[dict]]
    sent_emails: NotRequired[list[dict]]
    model_calls: NotRequired[int]
    tool_calls: NotRequired[int]
    thread_model_calls: NotRequired[int]
    thread_tool_calls: NotRequired[int]

    on_guardrail: NotRequired[Callable[[dict], None]]
    on_activity: NotRequired[Callable[[str], None]]
