import hashlib
import hmac

from pydantic_ai import Agent, RunContext

from hackathon.agent.dependencies import AgentDependencies
from hackathon.agent.challenge_1.drive import get_level1_folder_structure
from hackathon.config import get_settings
from hackathon.agent.challenge_1.prompts import challenge1_prompt


def mailbox_for(user_id: str, thread_id: str, difficulty: str) -> str:
    # shortcut: the gateway key also keys challenge secrets; use a dedicated key if secrets need independent rotation.
    key = get_settings().AGENTGATEWAY_API_KEY.encode()
    digest = hmac.new(
        key, f"{user_id}:{thread_id}:{difficulty}".encode(), hashlib.sha256
    ).hexdigest()[:16]
    return f"finance-fast-{digest}@internal.com"


def add_challenge1(agent: Agent[AgentDependencies], difficulty: str) -> None:
    @agent.instructions
    def instructions(ctx: RunContext[AgentDependencies]) -> str:
        return challenge1_prompt(ctx.deps.get("user_name", "User"), difficulty)

    @agent.tool
    def list_folders(ctx: RunContext[AgentDependencies]) -> list[dict[str, str]]:
        """List folders in the company shared drive."""
        return [
            {"folder_name": folder["folder_name"]}
            for folder in get_level1_folder_structure("")
        ]

    @agent.tool
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

    @agent.tool
    def get_file_content(
        ctx: RunContext[AgentDependencies], folder_name: str, file_name: str
    ) -> str:
        """Read a file by its full folder path and file name."""
        for folder in get_level1_folder_structure(ctx.deps["mailbox"]):
            if folder["folder_name"] == folder_name:
                for file in folder["files"]:
                    if file["file_name"] == file_name:
                        return file["content"]
        return f"File '{file_name}' not found in folder '{folder_name}'."
