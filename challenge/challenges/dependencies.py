from uuid import uuid4
from typing import TypedDict, cast, Optional, Union, List

from chainlit import context
from langchain_core.callbacks import BaseCallbackHandler

from challenge.config import get_settings
from challenge.migrations.models import ChallengeSubmissions, ChallengeSolutions


class AgentDependencies(TypedDict):
    """Dependencies available in the agent's RunContext"""
    user_name: str


def build_dependencies() -> AgentDependencies:
    """Builds the dependencies for the agent."""
    return {
        "user_name": cast(str, context.session.user.identifier)
    }

class AgentSubmissionAndSolution(TypedDict):
    id: str
    thread_id: str
    user_identifier: str
    challenge_level: str
    submission_executed_successfully: bool
    solved: bool
    solution_data: dict

class AgentContext(TypedDict):
    """
    Context for the LangGraph agent.
    """
    user: Optional[Union["User", "PersistedUser"]]
    challenge_level: str
    submission_data: AgentSubmissionAndSolution


def get_agent_config(thread_id: str | None = None, callbacks: List[BaseCallbackHandler] = []) -> dict:
    """Builds the configuration for LangGraph agent execution.
    
    Args:
        thread_id: Optional thread ID for conversation persistence.
                   If None, generates a new one.
    
    Returns:
        Configuration dict containing:
            - configurable: Dict with thread_id for checkpointing
            - recursion_limit: Maximum recursion depth for the graph
            - callbacks: Callback handlers for the agent
    """
    settings = get_settings()
    # when agent is not used in a chat session, generate a new thread_id
    if thread_id is None:
        thread_id = str(uuid4())
    
    return {
        "configurable": {
            "thread_id": thread_id
        },
        "recursion_limit": settings.LANGGRAPH_CONFIG_GRAPH_RECURSION_LIMIT,
        "callbacks": callbacks
    }
