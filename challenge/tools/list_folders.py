from typing import List

import structlog
from structlog import BoundLogger
from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime

from challenge.data.email_data import DEFAULT_SECRET_EMAIL
from challenge.data.level1_drive import get_level1_folder_structure

logger: BoundLogger = structlog.get_logger("tools")


@tool
async def list_folders(runtime: ToolRuntime) -> List:
    """List all folders available in the company shared drive.

    Returns:
        List of dictionaries, each containing 'folder_name' field with the folder path
    """
    logger.debug("list_folders_tool called.")

    user = runtime.context.get("user")
    thread_id = runtime.config.get("configurable", {}).get("thread_id")

    if not user or not thread_id:
        logger.debug("Unable to process tool call.")
        return []

    folder_structure_level = get_level1_folder_structure("")

    folders = [
        {
            "folder_name": folder.get("folder_name", ""),
        }
        for folder in folder_structure_level
    ]

    return folders
