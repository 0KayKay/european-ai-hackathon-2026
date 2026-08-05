from datetime import datetime
from typing import TypedDict


class AgentDependencies(TypedDict):
    """Dependencies available in the agent's RunContext"""
    current_date: datetime
    user_id: str
    mailbox: str
    difficulty: str
    mode: str
