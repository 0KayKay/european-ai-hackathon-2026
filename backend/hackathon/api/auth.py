import httpx
from urllib.parse import unquote
from fastapi import HTTPException, Request

from hackathon.config import get_settings


async def require_user(request: Request) -> str:
    settings = get_settings()
    cookie = request.headers.get("cookie")
    if settings.is_local and not cookie:
        request.state.user_email = "local@codify.ch"
        request.state.user_name = "Local user"
        return "local-user"

    if not cookie:
        raise HTTPException(status_code=401, detail="Sign in required")

    try:
        async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
            response = await client.get(
                f"{get_settings().AUTH_SERVICE_URL}/api/auth/authorize",
                headers={"cookie": cookie},
            )
        if response.status_code in (401, 403):
            raise HTTPException(status_code=401, detail="Sign in required")
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503, detail="Authentication unavailable"
        ) from exc

    user_id = response.headers.get("x-auth-user")
    if not user_id:
        raise HTTPException(status_code=503, detail="Authentication unavailable")
    request.state.user_email = response.headers.get("x-auth-email", "")
    request.state.user_name = unquote(response.headers.get("x-auth-name", "User"))
    return "local-user" if settings.is_local else user_id


def is_internal(request: Request) -> bool:
    email = getattr(request.state, "user_email", "").lower()
    return email.endswith("@lapiscode.com") or email.endswith("@codify.ch")
