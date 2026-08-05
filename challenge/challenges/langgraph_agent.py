import structlog
from langgraph.types import Checkpointer

from challenge.agents.level1_agent import create_level1_agent
from challenge.agents.level2_agent import create_level2_agent
from challenge.agents.level3_agent import create_level3_agent

logger = structlog.get_logger(__name__)

async def create_langgraph_agent(
        challenge_level: str,
        difficulty: str,
        user_name: str,
        checkpointer: Checkpointer = None,
        is_playground: bool = False
):
    """Creates a LangGraph agent for the given challenge level.

    Args:
        challenge_level: The challenge level (e.g., "LEVEL_1", "LEVEL_1", etc.)
        difficulty: The challenge difficulty
        user_name: The name of the user
        checkpointer: The checkpointer to use for persisting the agent state

    Returns:
        A compiled LangGraph agent with guardrails
    """

    # Extract the numeric level
    level_number = int(challenge_level.replace("LEVEL_", ""))
    
    logger.info(f"Creating Level {level_number} agent with difficulty {difficulty} for profile: {challenge_level}")
    
    # Agent 1 (Level 1 challenges): Levels 1, 4, 7
    if level_number in [1, 4, 7]:
        return await create_level1_agent(user_name, difficulty, checkpointer, thinking_budget=500)
    # Agent 2 (Level 2 challenges): Levels 2, 5, 8
    elif level_number in [2, 5, 8]:
        return await create_level2_agent(user_name, difficulty, checkpointer,
                                         thinking_budget=500, is_playground=is_playground)
    # Agent 3 (Level 3 challenges): Levels 3, 6, 9
    elif level_number in [3, 6, 9]:
        return await create_level3_agent(user_name, difficulty, checkpointer,
                                         thinking_budget=500, is_playground=is_playground)

    return None
