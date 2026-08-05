import asyncio
from typing import Dict

_overrides: Dict[str, str] = {}
_lock = asyncio.Lock()


async def set_chat_profile_override(user_identifier: str, chat_profile: str) -> None:
    """Store the desired chat profile for a user until the next chat session starts."""
    async with _lock:
        _overrides[user_identifier] = chat_profile


async def pop_chat_profile_override(user_identifier: str) -> str | None:
    """Return and remove the pending chat profile override for the user."""
    async with _lock:
        return _overrides.pop(user_identifier, None)


async def get_chat_profile_override(user_identifier: str) -> str | None:
    """Return the pending chat profile override without removing it."""
    async with _lock:
        return _overrides.get(user_identifier)
