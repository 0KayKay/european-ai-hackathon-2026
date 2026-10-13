from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
import structlog
from structlog import BoundLogger

from _old.challenge.challenge.data.purchase_offer_data import BENCHMARKS

logger: BoundLogger = structlog.get_logger("get_price_reference_tool")


@tool
async def get_price_reference(sku: str, runtime: ToolRuntime, config: RunnableConfig):
    """Fetch benchmark pricing for validation.

    Args:
        sku: SKU code

    Returns:
        Dictionary with benchmark_unit_price, source, and region
    """
    logger.debug("get_price_reference tool called.")

    # Try to find matching benchmark
    for key, value in BENCHMARKS.items():
        if key in sku:
            return value

    # Default fallback
    return {
        "error": "No information on provided product available.",
    }