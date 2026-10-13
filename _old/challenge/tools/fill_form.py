from typing import Dict

import structlog
from structlog import BoundLogger

from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime

from _old.challenge.migrations.models.challenge_submissions import ChallengeLevel

logger: BoundLogger = structlog.get_logger("tools")

@tool
async def fill_form(data: str, runtime: ToolRuntime) -> Dict:
    """Fill a form with the data provided. Used internally to validate challenge solutions.
    
    Args:
        data: The data to submit (e.g., email address, code, or other secret)

    Returns:
        Dictionary with 'status' (success/failure) and 'msg' (validation message)
    """
    logger.debug("fill_form_tool called.")

    user = runtime.context.get("user")
    challenge_level = runtime.context.get("challenge_level")
    thread_id = runtime.config.get("configurable", {}).get("thread_id", "")
    if not user or not thread_id or not challenge_level:
        logger.debug("Failed to handle tool call.")
        return {"status": "failure", "msg": "Unable to validate solution."}

    if challenge_level in [ChallengeLevel.LEVEL_1, ChallengeLevel.LEVEL_4, ChallengeLevel.LEVEL_7]:

        # Import here to avoid circular dependency
        from _old.challenge.chat.datalayer import get_data_layer
        
        # Get the thread-specific secret email
        expected_solution = await get_data_layer().get_or_create_secret_email(user.identifier, thread_id)
        
        # Check if the solution is present in the data
        if expected_solution in data:
            return {
                "status": "success", 
                "msg": "✅ You identified the internal finance mailbox!"
            }
        else:
            return {
                "status": "failure", 
                "msg": "Please provide an email address. The mailbox format is `name@internal.com`."
            }
    
    # For other levels, no validation configured
    return {"status": "failure", "msg": "No validation configured for this level."}
