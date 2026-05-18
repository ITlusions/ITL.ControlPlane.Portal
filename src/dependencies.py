"""
ITL Control Plane Dashboard - FastAPI Dependencies

Provides dependency injection for authentication and other services.
"""
from typing import Optional
from fastapi import Request, HTTPException, Depends
from fastapi.responses import RedirectResponse

from src.config import get_settings, Settings
from src.auth.session import UserSession, get_session_manager, SessionManager
from src.auth.oidc import get_oidc_client, OIDCClient


def get_settings_dep() -> Settings:
    """Dependency to get application settings"""
    return get_settings()


def get_session_manager_dep() -> SessionManager:
    """Dependency to get session manager"""
    return get_session_manager()


def get_oidc_client_dep() -> OIDCClient:
    """Dependency to get OIDC client"""
    return get_oidc_client()


async def get_current_user_optional(
    request: Request,
    session_manager: SessionManager = Depends(get_session_manager_dep),
    settings: Settings = Depends(get_settings_dep),
) -> Optional[UserSession]:
    """
    Get current user from session cookie (optional).
    Returns None if not authenticated.
    """
    cookie_value = request.cookies.get(settings.session_cookie_name)
    if not cookie_value:
        return None
    
    session = session_manager.decode_session(cookie_value)
    return session


async def get_current_user(
    request: Request,
    user: Optional[UserSession] = Depends(get_current_user_optional),
    settings: Settings = Depends(get_settings_dep),
    oidc: OIDCClient = Depends(get_oidc_client_dep),
) -> UserSession:
    """
    Get current user from session cookie (required).
    Redirects to login if not authenticated.
    """
    if user is None:
        if not oidc.enabled:
            raise HTTPException(
                status_code=401,
                detail="Authentication not configured"
            )
        # Store the original URL to redirect back after login
        request.session["next_url"] = str(request.url)
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
            headers={"X-Redirect": "/login"}
        )
    return user


async def require_auth(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
) -> Optional[UserSession]:
    """
    Require authentication - redirect to login if not authenticated.
    Use this as a dependency for protected page routes.
    """
    session_manager = get_session_manager()
    cookie_value = request.cookies.get(settings.session_cookie_name)
    
    if cookie_value:
        session = session_manager.decode_session(cookie_value)
        if session and not session.is_expired:
            return session
    
    # Check if OIDC is enabled
    oidc = get_oidc_client()
    if not oidc.enabled:
        # No auth configured - allow anonymous access
        return None
    
    # Need to login - will be handled by exception handler
    raise HTTPException(
        status_code=401,
        detail="Authentication required"
    )


def login_required(allow_anonymous: bool = False):
    """
    Decorator factory for protecting routes.
    
    Args:
        allow_anonymous: If True, allow unauthenticated access when OIDC is disabled
    """
    async def dependency(
        request: Request,
        settings: Settings = Depends(get_settings_dep),
    ) -> Optional[UserSession]:
        session_manager = get_session_manager()
        oidc = get_oidc_client()
        
        cookie_value = request.cookies.get(settings.session_cookie_name)
        if cookie_value:
            session = session_manager.decode_session(cookie_value)
            if session and not session.is_expired:
                return session
        
        # Not authenticated
        if not oidc.enabled and allow_anonymous:
            return None
        
        if not oidc.enabled:
            raise HTTPException(status_code=501, detail="Authentication not configured")
        
        raise HTTPException(status_code=401, detail="Login required")
    
    return Depends(dependency)
