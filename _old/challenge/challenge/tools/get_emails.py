from typing import List
import copy

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
import structlog
from structlog import BoundLogger

from _old.challenge.challenge.data.email_data import EMAIL_INBOXES, SECRET_PLAYGROUND_CREDENTIALS

logger: BoundLogger = structlog.get_logger("tools")

@tool
async def get_emails(runtime: ToolRuntime, config: RunnableConfig) -> List:
    """Fetch the user's email inbox.

    Returns:
        List of email dictionaries, each containing sender, subject, and body fields
    """
    logger.debug("get_emails called.")

    challenge_level = runtime.context.get("challenge_level")
    user = runtime.context.get("user")
    thread_id = config.get("configurable", {}).get("thread_id")
    if not user or not challenge_level or not thread_id:
        logger.debug("Failed to process tool call.")
        return []

    user_identifier = user.identifier
    submission = runtime.context.get("submission_data")

    # Extract base level without difficulty (e.g., "Level 5 - MEDIUM" -> "Level 5")
    base_level = challenge_level.split(" - ")[0] if " - " in challenge_level else challenge_level
    
    logger.info(f"get_emails called with challenge_level='{challenge_level}', base_level='{base_level}'")

    # add dummy secret for playground
    secret_password = SECRET_PLAYGROUND_CREDENTIALS
    context_is_playground = "playground" in config.get("tags", [])
    if not context_is_playground:
        # use actual secret for challenge submissions
        secret_password = runtime.context.get("submission_data", {}).get("solution_data", {}).get("solution", SECRET_PLAYGROUND_CREDENTIALS)
    emails = copy.deepcopy(EMAIL_INBOXES.get(base_level, []))
    for email in emails:
        if "{secret_password}" in email["body"]:
            email["body"] = email["body"].format(secret_password=secret_password)
    if not emails:
        logger.warning(f"No emails found in EMAIL_INBOXES for level '{base_level}'. Available keys: {list(EMAIL_INBOXES.keys())}")
    
    if submission is None:
        logger.warning(f"No submission found for user {user_identifier} and thread {thread_id}")
    else:
        attack_emails = submission.get("submission_data", {})
        if len(attack_emails) > 0:
            emails.append(attack_emails)
    
    logger.info(f"Returning {len(emails)} emails")
    return emails
