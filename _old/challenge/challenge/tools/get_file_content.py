import structlog
from structlog import BoundLogger

from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime

from _old.challenge.challenge.data.email_data import DEFAULT_SECRET_EMAIL
from _old.challenge.challenge.data.level1_drive import get_level1_folder_structure


logger: BoundLogger = structlog.get_logger("tools")

@tool
async def get_file_content(folder_name: str, file_name: str, runtime: ToolRuntime) -> str:
    """Get the content of a specific file from the company shared drive.
    
    Args:
        folder_name: The full path of the folder (e.g., '/Finance/Procurement/')
        file_name: The name of the file (e.g., 'approval_matrix.md')
    
    Returns:
        The content of the file, or an error message if not found.
    """
    logger.debug("get_file_content called.")

    user = runtime.context.get("user")
    thread_id = runtime.config.get("configurable", {}).get("thread_id", "")
    
    if not user or not thread_id:
        return "Error: Unable to access file system."

    secret_email = runtime.context.get("submission_data", {}).get("solution_data", {}).get("solution", DEFAULT_SECRET_EMAIL)

    folder_structure_level = get_level1_folder_structure(secret_email)

    for folder in folder_structure_level:
        if folder.get("folder_name", "") == folder_name:
            for file in folder.get("files", []):
                if file.get("file_name", "") == file_name:
                    return file.get("content", "")

    return f"File '{file_name}' not found in folder '{folder_name}'."
