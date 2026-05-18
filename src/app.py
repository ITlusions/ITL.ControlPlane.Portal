"""
ITL Control Plane Dashboard - Application Factory

Creates and configures the FastAPI application.
"""
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse, JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from loguru import logger

from src.config import get_settings
from src.middleware.audit import AuditLogMiddleware
from src.routers.auth import router as auth_router
from src.routers.api import router as api_router
from src.routers.pages import router as pages_router
from src.auth.session import UserSession, get_session_manager
from src.auth.oidc import get_oidc_client


def create_app() -> FastAPI:
    """Create and configure the FastAPI application"""
    settings = get_settings()
    
    app = FastAPI(
        title="ITL Control Plane Portal",
        version="1.0.0",
        description="Customer self-service portal for ITL Control Plane resource management",
    )
    
    # Mount static files
    app.mount("/assets", StaticFiles(directory=settings.static_dir), name="assets")
    
    # Add middleware
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie=settings.session_cookie_name,
        max_age=settings.session_max_age,
    )
    app.add_middleware(AuditLogMiddleware)
    
    # Register routers
    app.include_router(auth_router)
    app.include_router(api_router)
    app.include_router(pages_router)
    
    # Exception handler for 401 -> redirect to login
    @app.exception_handler(401)
    async def auth_exception_handler(request: Request, exc):
        # Check if this is an API request
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                status_code=401,
                content={"error": "Not authenticated", "login_url": "/login"}
            )
        # For page requests, redirect to login
        return RedirectResponse(url=f"/login?next={request.url.path}", status_code=302)
    
    # Startup event
    @app.on_event("startup")
    async def startup():
        oidc = get_oidc_client()
        if oidc.enabled:
            logger.success(f"Authentication enabled via Keycloak: {oidc.config.issuer}")
        else:
            logger.warning("Running without authentication")
    
    # Shutdown event
    @app.on_event("shutdown")
    async def shutdown():
        oidc = get_oidc_client()
        await oidc.close()
        logger.info("Application shutdown complete")
    
    return app


def get_templates() -> Jinja2Templates:
    """Get Jinja2 templates instance"""
    settings = get_settings()
    return Jinja2Templates(directory=settings.templates_dir)


def get_current_user_from_request(request: Request) -> UserSession | None:
    """Helper to get current user from request (for use in existing routes)"""
    settings = get_settings()
    session_manager = get_session_manager()
    
    cookie_value = request.cookies.get(settings.session_cookie_name)
    if not cookie_value:
        return None
    
    return session_manager.decode_session(cookie_value)
