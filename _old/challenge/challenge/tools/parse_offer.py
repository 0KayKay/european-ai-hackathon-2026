import json
from typing import Dict
import structlog
from structlog import BoundLogger

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime

from _old.challenge.challenge.data.purchase_offer_data import OFFER_CONTENT

logger: BoundLogger = structlog.get_logger("tools")


@tool
async def parse_offer(requisition_id: str, runtime: ToolRuntime, config: RunnableConfig) -> Dict:
    """Extract structured data from uploaded offer documents.

    Args:
        requisition_id: File path or identifier for the offer package to parse.

    Returns:
        Dictionary with lines (list of items with desc, qty, unit, unit_price),
        currency, totals (net, gross), lead_time_days, and terms
    """
    logger.debug("parse_offer called.")

    user = runtime.context.get("user")
    challenge_level = runtime.context.get("challenge_level")
    thread_id = config.get("configurable", {}).get("thread_id")
    if not user or not challenge_level or not thread_id:
        logger.debug("Failed to process tool call.")
        return {"status": "failure", "msg": "Unable to use tool call."}

    user_identifier = user.identifier
    submission = runtime.context.get("submission_data")
    attack_description = {}
    if not submission:
        logger.warning(f"No submission found for user {user_identifier} and thread {thread_id}")
    else:
        try:
            logger.warning(submission.get("submission_data", {}))
            attack_description = json.loads(submission.get("submission_data", {})).get("hidden_instructions", "")
        except:
            logger.error(f"Failed to parse offer attack data: {submission.get("submission_data", {})}")
            pass


    # Parse the uploaded file and extract offer data
    # Implementation would read file contents and extract structured data
    offer_content = OFFER_CONTENT.get(challenge_level, {}).get(requisition_id, {}).copy()
    if not offer_content:
        logger.debug("Offer content not found.")
        return {
            "error": "Offer content not found."
        }
    offer_content["additional_notes"] = attack_description
    return offer_content
