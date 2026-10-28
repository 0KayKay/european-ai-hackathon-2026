"""Example tool implementation: the level's simulated shared drive."""

from pydantic_ai import RunContext

from hackathon.agent.dependencies import AgentDependencies
from hackathon.agent.level1_drive import get_level1_folder_structure


def secret_for(user_id: str, thread_id: str, level_id: str) -> str:
    from hackathon.agent.challenge1 import mailbox_for

    return mailbox_for(user_id, thread_id, level_id)


def list_folders(ctx: RunContext[AgentDependencies]) -> list[dict[str, str]]:
    """List folders in the company shared drive."""
    return [
        {"folder_name": folder["folder_name"]}
        for folder in get_level1_folder_structure("")
    ]


def list_files_of_folder(
    ctx: RunContext[AgentDependencies], folder_name: str
) -> list[dict]:
    """List file names in a folder by its full path."""
    return [
        {
            "folder_name": folder_name,
            "files": [file["file_name"] for file in folder["files"]],
        }
        for folder in get_level1_folder_structure("")
        if folder["folder_name"] == folder_name
    ]


def get_file_content(
    ctx: RunContext[AgentDependencies], folder_name: str, file_name: str
) -> str:
    """Read a file by its full folder path and file name."""
    for folder in get_level1_folder_structure(ctx.deps["mailbox"]):
        if folder["folder_name"] == folder_name:
            for file in folder["files"]:
                if file["file_name"] == file_name:
                    return file["content"]
    return "File not found."
