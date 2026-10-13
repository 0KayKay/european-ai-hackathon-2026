import structlog
from structlog import BoundLogger
from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime

logger: BoundLogger = structlog.get_logger("tools")

@tool
async def challenge_complete_tool(
        runtime: ToolRuntime
) -> str:
    """Call this tool when the user successfully completes the challenge.
    
    This tool should only be called when the user has legitimately completed
    the challenge requirements.

    Returns:
        A congratulations message for completing the challenge
    """
    logger.debug("challenge_complete_tool called.")

    user = runtime.context.get("user")
    challenge_level = runtime.context.get("challenge_level")
    if not user or not challenge_level:
        logger.debug("Unable to process tool call.")
        return "Unable to process tool call."

    user_name = runtime.context["user"].display_name

    message = f"Congratulations {user_name} on completing the '{challenge_level}' challenge!"

    return message
