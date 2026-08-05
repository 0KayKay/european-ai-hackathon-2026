import time

import httpx
import structlog
from chainlit import User
from chainlit.auth import create_jwt, set_auth_cookie, clear_auth_cookie
from fastapi import APIRouter, Request, Response, HTTPException, Form
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature
from itsdangerous import URLSafeSerializer
from jwt import decode as jwt_decode
from starlette.responses import JSONResponse

from _old.challenge.config import get_settings

router = APIRouter()
settings = get_settings()
logger = structlog.get_logger(__name__)


@router.post("/auth/send-link")
async def send_link(req: Request, res: Response, email: str = Form(...)):
    s = URLSafeSerializer(settings.SIGNING_SECRET, salt="email_cookie")
    payload = s.dumps({"email": email, "ts": int(time.time())})

    logger.info(f"MAIL_DEBUG: Request to Sign-In from {email}.")
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"https://identitytoolkit.googleapis.com/v1/accounts:sendOobCode?key={settings.IDENTITY_PLATFORM_API_KEY}",
            json={
                "requestType": "EMAIL_SIGNIN",
                "email": email,
                "continueUrl": f"{str(req.base_url)}auth/callback",
            },
            timeout=15,
        )
    if r.is_success:
        response =  RedirectResponse(url="/check-email", status_code=302)
        response.set_cookie(
            "email_for_signin",
            payload,
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=7200,
        )
        logger.info(f"MAIL_DEBUG: email_for_signin cookie set for {email}.")
        return response

    raise HTTPException(400, r.text)


@router.get("/auth/callback")
async def callback(req: Request, oobCode: str):
    email_cookie = req.cookies.get("email_for_signin")
    logger.info(f"MAIL_DEBUG: Callback received with email_cookie of value {email_cookie}")
    email = None
    if email_cookie:
        try:
            data = URLSafeSerializer(
                settings.SIGNING_SECRET, salt="email_cookie"
            ).loads(email_cookie)
            email = data.get("email")
            logger.info(f"MAIL_DEBUG: Email {email} found in cookie during sign-in with email link.")
        except BadSignature:
            pass
    if not email:
        logger.warn("Email not found in cookie during sign-in with email link")
        raise HTTPException(
            status_code=400, detail="Sign-in session expired or was invalid."
        )

    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithEmailLink?key={settings.IDENTITY_PLATFORM_API_KEY}",
            json={"email": email, "oobCode": oobCode},
            timeout=15,
        )
    if not r.is_success:
        logger.error("signInWithEmailLink failed", response=r.text)
        raise HTTPException(400, r.text)

    body = r.json()
    id_token = body["idToken"]
    decoded_user = jwt_decode(id_token, options={"verify_signature": False})
    user = User(identifier=decoded_user["email"])
    resp = RedirectResponse(url="/challenge/info-page", status_code=302)
    resp.delete_cookie("email_for_signin")
    jwt_token = create_jwt(user)
    set_auth_cookie(req, resp, jwt_token)
    return resp



@router.post("/logout")
async def logout(request: Request, _: Response) -> RedirectResponse:
    logger.debug("User logged out")
    response = RedirectResponse(url="/", status_code=302)
    clear_auth_cookie(request, response)
    return response
