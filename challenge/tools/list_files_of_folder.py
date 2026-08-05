from typing import List

import structlog
from structlog import BoundLogger
from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime

from challenge.data.email_data import DEFAULT_SECRET_EMAIL
from challenge.data.level1_drive import get_level1_folder_structure


logger: BoundLogger = structlog.get_logger("tools")

@tool
async def list_files_of_folder(folder_name: str, runtime: ToolRuntime) -> List:
    """List all files available in a specific folder of the company shared drive.
    
    Args:
        folder_name: The full path of the folder to list files from (e.g., '/Finance/Procurement/')

    Returns:
        List of dictionaries containing 'folder_name' and 'files' (list of file names)
    """
    logger.debug("list_files_tool called.")

    user = runtime.context.get("user")
    thread_id = runtime.config.get("configurable", {}).get("thread_id")
    
    if not user or not thread_id:
        logger.debug("Unable to process tool call.")
        return []

    secret_email = runtime.context.get("submission_data", {}).get("solution_data", {}).get("solution", DEFAULT_SECRET_EMAIL)
    folder_structure_level = get_level1_folder_structure(secret_email)

    folder_and_filenames = [
        {
            "folder_name": folder.get("folder_name", ""),
            "files": [
                file.get("file_name", "") for file in folder.get("files", [])
            ]
        }
        for folder in folder_structure_level if folder.get("folder_name", "") == folder_name
    ]

    return folder_and_filenames
