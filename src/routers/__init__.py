"""ITL Control Plane Dashboard - Routers Package"""
from .auth import router as auth_router
from .api import router as api_router
from .pages import router as pages_router

__all__ = ["auth_router", "api_router", "pages_router"]
