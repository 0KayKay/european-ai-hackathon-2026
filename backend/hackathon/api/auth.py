import httpx
from fastapi import HTTPException, Request

from hackathon.config import get_settings


async def require_user(request: Request) -> str:
    cookie = request.headers.get("cookie")
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
        raise HTTPException(status_code=503, detail="Authentication unavailable") from exc

    user_id = response.headers.get("x-auth-user")
    if not user_id:
        raise HTTPException(status_code=503, detail="Authentication unavailable")
    return user_id
