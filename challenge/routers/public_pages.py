import os

from fastapi import APIRouter, HTTPException, status
from starlette.requests import Request
from starlette.responses import StreamingResponse
from starlette.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="challenge/templates")

@router.get("/")
async def landing_page(request: Request):
    """Render Home Page"""

    return templates.TemplateResponse("pages/home.html", {"request": request})

@router.get("/signup")
async def signup_page(request: Request):
    """Render Challenge Page"""
    return templates.TemplateResponse("pages/signup.html", {"request": request})


@router.get("/signin")
async def signin_page(request: Request):
    """Render Challenge Page"""
    return templates.TemplateResponse("pages/signin.html", {"request": request})


@router.get("/check-email")
async def check_email_page(request: Request, email: str = ""):
    """Render Check Email Page"""
    return templates.TemplateResponse(
        "pages/check-email.html", {"request": request, "email": email}
    )


@router.get("/countdown")
async def countdown_page(request: Request):
    """Render Countdown Page"""

    return templates.TemplateResponse("pages/countdown.html", {"request": request})


@router.get("/challenge-concluded")
async def challenge_concluded_page(request: Request):
    """Render Challenge Concluded Page"""

    return templates.TemplateResponse("pages/challenge-concluded.html", {"request": request})
  
  
@router.get("/info-page")
async def info_page(request: Request):
    """Render Info Page - publicly accessible"""
    return templates.TemplateResponse("pages/info_page.html", {"request": request})


@router.get("/public/kick-off-video")
async def stream_kick_off_video(request: Request):
    file_path = "public/Kickoff_Prompt_Injection_Challenge.mp4"

    # 1. Verify file exists
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="Video not found")

    file_size = os.path.getsize(file_path)

    # 2. Parse the "Range" header from the browser
    range_header = request.headers.get("range")

    # Defaults if no range is requested (send usually the start)
    start = 0
    end = file_size - 1

    if range_header:
        # format is usually "bytes=0-" or "bytes=0-1024"
        headers = range_header.replace("bytes=", "").split("-")
        start = int(headers[0]) if headers[0] else 0
        end = int(headers[1]) if headers[1] else file_size - 1

    # 3. IMPORTANT: Enforce a chunk limit (e.g., 1MB) to satisfy Cloud Run limits
    # If the browser asks for the WHOLE file, we only give them 1MB and tell them to ask again.
    chunk_size = 1024 * 1024  # 1MB
    if (end - start) >= chunk_size:
        end = start + chunk_size - 1

    content_length = end - start + 1

    # 4. Create the generator to read just that slice
    def iterfile():
        with open(file_path, mode="rb") as f:
            f.seek(start)
            yield f.read(content_length)

    # 5. Return 206 Partial Content (Standard for streaming)
    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Content-Type": "video/mp4",
    }

    return StreamingResponse(
        iterfile(),
        status_code=status.HTTP_206_PARTIAL_CONTENT,
        headers=headers,
        media_type="video/mp4",
    )