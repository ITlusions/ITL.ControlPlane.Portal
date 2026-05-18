"""
ITL Control Plane Dashboard - Pages Router

Contains all HTML page routes.
"""
from typing import Optional
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse

from src.dependencies import get_current_user_optional
from src.auth.session import UserSession

router = APIRouter(tags=["pages"])

# Note: The page routes are currently in main.py
# They will be migrated here in a future refactor.
# This router is included for the authentication structure.

# Example of how protected page routes will look:
#
# @router.get("/protected-page", response_class=HTMLResponse)
# async def protected_page(
#     request: Request,
#     user: UserSession = Depends(get_current_user_optional),
# ):
#     """Example protected page"""
#     if user is None:
#         return RedirectResponse(url="/login", status_code=302)
#     return templates.TemplateResponse("protected.html", {"request": request, "user": user})
