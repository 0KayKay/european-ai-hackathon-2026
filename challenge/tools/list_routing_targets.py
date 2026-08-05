from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
import structlog
from structlog import BoundLogger

from challenge.data.purchase_offer_data import ROUTING_TARGETS

logger: BoundLogger = structlog.get_logger("list_routing_targets_tool")


@tool
async def list_routing_targets(runtime: ToolRuntime, config: RunnableConfig):
    """Get available reviewers with their competences and roles.

    Returns:
        List of reviewers with user_id, name, role, competences (max_amount, domains),
        and is_fallback flag
    """
    logger.debug("list_routing_targets tool called.")

    return ROUTING_TARGETS