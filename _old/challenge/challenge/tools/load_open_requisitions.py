from typing import List

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
import structlog
from structlog import BoundLogger

from _old.challenge.challenge.data.purchase_offer_data import REQUISITIONS

logger: BoundLogger = structlog.get_logger("tools")


@tool
async def load_open_offers(runtime: ToolRuntime, config: RunnableConfig) -> List:
    """Retrieve active offers with thresholds and assigned reviewers.

    Returns:
        List of requisitions with requisition_id, project_or_cost_center,
        fallback_threshold, and primary_reviewer
    """
    logger.debug("load_open_requisitions tool called.")

    return REQUISITIONS
