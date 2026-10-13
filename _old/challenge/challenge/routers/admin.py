from datetime import datetime

import pytz
import structlog
from chainlit.auth import get_current_user
from fastapi.templating import Jinja2Templates
from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from starlette.requests import Request
from starlette.responses import JSONResponse
from structlog import BoundLogger

from _old.challenge.challenge.chat.datalayer import get_data_layer

from challenge.config import get_settings

settings = get_settings()
router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="challenge/templates")

logger: BoundLogger = structlog.get_logger("pages_router")


async def is_admin_user(user=Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not user.identifier.endswith("@lapiscode.com") and not user.identifier.endswith("@codify.ch"):
        raise HTTPException(status_code=403, detail="Unauthorized")

    return user

# @router.get("/get_agent_log")
# async def get_agent_log(request: Request, user_identifier: str, thread_id: str, user=Depends(is_admin_user)):
#     data_layer = get_data_layer()
#
#     submission_and_execution_sequence = await data_layer.get_complete_agent_execution_sequence(
#         user_identifier=user_identifier, thread_id=thread_id)
#
#     submission_and_execution_sequence = jsonable_encoder(submission_and_execution_sequence)
#     return JSONResponse(content=submission_and_execution_sequence)

# @router.get("/data/users_stats")
# async def get_agent_log(request: Request, user_identifier: str, user=Depends(is_admin_user)):
#     data_layer = get_data_layer()
#
#     submission_stats_user = await data_layer.get_all_submission_and_solved_count_grouped_by_challenge_level_for_user(
#         user_identifier=user_identifier)
#
#     submission_stats_user = jsonable_encoder(submission_stats_user)
#     return JSONResponse(content=submission_stats_user)


@router.get("/render_agent_log")
async def get_agent_log(request: Request, user_identifier: str, thread_id: str, user=Depends(is_admin_user)):
    data_layer = get_data_layer()

    submission_data_and_execution_sequence = await data_layer.get_complete_agent_execution_sequence(
        user_identifier=user_identifier, thread_id=thread_id)
    agent_log = submission_data_and_execution_sequence.get("execution_sequence", {})
    # submission_and_execution_sequence = jsonable_encoder(submission_and_execution_sequence)
    return templates.TemplateResponse("components/agent_log.html", {"request": request,
                                                                          "agent_log": agent_log})


@router.get("/dashboard")
async def admin_submissions(request: Request, user=Depends(is_admin_user)):
    try:
        cet = pytz.timezone('CET')
        since = cet.localize(datetime(2025, 11, 24, 13, 30))
        until = cet.localize(datetime(2025, 11, 28, 22, 0, 0))
        data_layer = get_data_layer()
        submission_stats = await data_layer.get_all_submission_and_solved_count(since=since, until=until)
        submission_stats_per_challenge = await data_layer.get_all_submission_and_solved_count_grouped_by_challenge_level(since=since, until=until)
        all_submissions = await data_layer.get_all_submissions(since=since, until=until)
        all_submissions = jsonable_encoder(all_submissions)

        # successful_submissions = await data_layer.get_all_successful_submissions()
        # successful_submissions = jsonable_encoder(successful_submissions)
        all_users = await data_layer.get_all_users()
        n_internal_users = [u for u in all_users if u.get("identifier", "").endswith("@lapiscode.com") or u.get("identifier", "").endswith("@codify.ch")]
        current_winners = await data_layer.get_winners_sorted_by_points(n = 3, since=since, until=until)

        """Render Admin Dashboard"""
        return templates.TemplateResponse(
            "pages/admin_dashboard.html", {"request": request,
                                           "n_users": len(all_users),
                                           "n_internal_users": len(n_internal_users),
                                           "all_submissions": all_submissions,
                                           "submission_stats": submission_stats,
                                           "submission_stats_per_challenge": submission_stats_per_challenge,
                                           "current_winners": current_winners
                                           }
        )
    except Exception as e:
        logger.exception(f"Failed to get Admin Dashboard")
        return JSONResponse(status_code=500, content={"error": "Failed to get Admin Dashboard"})
