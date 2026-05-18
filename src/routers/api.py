"""
ITL Control Plane Dashboard - API Router

Contains all /api/* endpoints for data operations.
These are JSON API endpoints used by the frontend JavaScript.
"""
from typing import Optional
from fastapi import APIRouter, Depends, Query

from src.dependencies import get_current_user_optional
from src.auth.session import UserSession

router = APIRouter(prefix="/api", tags=["api"])

# Note: The API routes are currently in main.py
# They will be migrated here in a future refactor.
# This router is included for the authentication structure.

# Example of how protected API routes will look:
#
# @router.get("/protected-data")
# async def protected_data(
#     user: UserSession = Depends(get_current_user_optional),
# ):
#     """Example protected API endpoint"""
#     return {"message": "This is protected data", "user": user.email if user else None}
