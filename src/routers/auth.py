"""
ITL Control Plane Dashboard - Authentication Router

Handles login, logout, OIDC callback, and user info endpoints.
"""
from typing import Optional
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse

from loguru import logger

from src.config import get_settings, Settings
from src.auth.oidc import get_oidc_client, OIDCClient
from src.auth.session import (
    UserSession,
    get_session_manager,
    SessionManager,
)
from src.dependencies import get_current_user_optional

router = APIRouter(tags=["auth"])


def _is_secure_request(request: Request) -> bool:
    """
    Determine if cookies should be set with secure=True.
    True for HTTPS requests, False for HTTP (localhost dev).
    """
    # Check X-Forwarded-Proto header (behind reverse proxy)
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    if forwarded_proto == "https":
        return True
    
    # Check request URL scheme
    return request.url.scheme == "https"


@router.get("/login")
async def login(
    request: Request,
    next: Optional[str] = None,
    settings: Settings = Depends(lambda: get_settings()),
):
    """Initiate OIDC login flow"""
    oidc = get_oidc_client()
    
    if not oidc.enabled:
        logger.warning("Login attempted but OIDC not configured")
        return JSONResponse(
            {"error": "Authentication not configured"},
            status_code=501
        )
    
    # Check if already logged in
    session_manager = get_session_manager()
    cookie_value = request.cookies.get(settings.session_cookie_name)
    if cookie_value:
        session = session_manager.decode_session(cookie_value)
        if session and not session.is_expired:
            logger.info(f"User already logged in: {session.email}")
            return RedirectResponse(url=next or "/", status_code=302)
    
    # Generate state for CSRF protection
    state = oidc.generate_state()
    
    # Build callback URL (use configured redirect_uri if available, otherwise auto-detect)
    if settings.oidc.redirect_uri:
        callback_url = settings.oidc.redirect_uri
    else:
        callback_url = str(request.url_for("oidc_callback"))
    
    # Store state and next URL in a temporary cookie
    auth_url = oidc.get_authorization_url(callback_url, state)
    
    response = RedirectResponse(url=auth_url, status_code=302)
    
    # Store state in cookie (short-lived)
    response.set_cookie(
        key="oidc_state",
        value=f"{state}|{next or '/'}",
        max_age=300,  # 5 minutes
        path="/",
        httponly=True,
        secure=_is_secure_request(request),
        samesite="lax",
    )
    
    logger.info(f"Initiating OIDC login, redirecting to Keycloak")
    return response


@router.get("/oidc/callback")
async def oidc_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
    settings: Settings = Depends(lambda: get_settings()),
):
    """Handle OIDC callback after authentication"""
    oidc = get_oidc_client()
    session_manager = get_session_manager()
    
    if not oidc.enabled:
        return RedirectResponse(url="/", status_code=302)
    
    # Handle errors from IdP
    if error:
        logger.error(f"OIDC error: {error} - {error_description}")
        return JSONResponse(
            {"error": error, "description": error_description},
            status_code=400
        )
    
    if not code or not state:
        logger.error("Missing code or state in callback")
        return RedirectResponse(url="/login", status_code=302)
    
    # Verify state
    state_cookie = request.cookies.get("oidc_state", "")
    if not state_cookie:
        logger.error("Missing state cookie")
        return RedirectResponse(url="/login", status_code=302)
    
    stored_state, next_url = state_cookie.split("|", 1) if "|" in state_cookie else (state_cookie, "/")
    
    if state != stored_state:
        logger.error("State mismatch - possible CSRF attack")
        return RedirectResponse(url="/login", status_code=302)
    
    # Exchange code for tokens (use same redirect_uri as login)
    if settings.oidc.redirect_uri:
        callback_url = settings.oidc.redirect_uri
    else:
        callback_url = str(request.url_for("oidc_callback"))
    tokens = await oidc.exchange_code(code, callback_url)
    
    if not tokens:
        logger.error("Failed to exchange code for tokens")
        return RedirectResponse(url="/login", status_code=302)
    
    # Get user info
    userinfo = await oidc.get_userinfo(tokens.get("access_token", ""))
    if not userinfo:
        logger.error("Failed to get user info")
        return RedirectResponse(url="/login", status_code=302)
    
    # Create session (stored server-side, cookie only has session ID)
    session, cookie_value = session_manager.create_session(tokens, userinfo)
    
    logger.info(f"User authenticated: {session.email}")
    logger.debug(f"Session cookie length: {len(cookie_value)} chars")
    
    # Redirect to original destination
    response = RedirectResponse(url=next_url or "/", status_code=302)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=cookie_value,
        max_age=settings.session_max_age,
        path="/",
        httponly=True,
        secure=_is_secure_request(request),
        samesite="lax",
    )
    logger.debug(f"Set-Cookie header added: {settings.session_cookie_name}")
    # Clear state cookie
    response.delete_cookie(key="oidc_state", path="/")
    
    return response


@router.get("/logout")
async def logout(
    request: Request,
    settings: Settings = Depends(lambda: get_settings()),
):
    """Logout and clear session"""
    oidc = get_oidc_client()
    session_manager = get_session_manager()
    
    # Delete session from server-side store (returns session with id_token for Keycloak logout)
    cookie_value = request.cookies.get(settings.session_cookie_name)
    user = session_manager.delete_session(cookie_value) if cookie_value else None
    
    if user:
        logger.info(f"User logging out: {user.email}")
    
    # If OIDC is enabled, also logout from Keycloak
    if oidc.enabled and user and user.id_token:
        # Build post-logout redirect URL
        post_logout_url = str(request.base_url)
        logout_url = oidc.get_logout_url(post_logout_url, user.id_token)
        response = RedirectResponse(url=logout_url, status_code=302)
    else:
        response = RedirectResponse(url="/", status_code=302)
    
    # Always delete session cookie
    response.delete_cookie(key=settings.session_cookie_name, path="/")
    
    return response


@router.get("/user-info")
async def user_info(
    user: Optional[UserSession] = Depends(get_current_user_optional),
):
    """Get current user information"""
    oidc = get_oidc_client()
    
    if not oidc.enabled:
        return JSONResponse({
            "logged_in": False,
            "auth_enabled": False,
            "error": "Authentication not configured"
        })
    
    if user:
        return JSONResponse({
            "logged_in": True,
            "auth_enabled": True,
            "user_id": user.user_id,
            "email": user.email,
            "name": user.display_name,
            "given_name": user.given_name,
            "family_name": user.family_name,
        })
    
    return JSONResponse({
        "logged_in": False,
        "auth_enabled": True,
    })


@router.get("/auth-status")
async def auth_status():
    """Check authentication system status"""
    oidc = get_oidc_client()
    settings = get_settings()
    
    return JSONResponse({
        "auth_enabled": oidc.enabled,
        "provider": "keycloak" if oidc.enabled else None,
        "issuer": oidc.config.issuer if oidc.enabled else None,
        "client_id": oidc.config.client_id if oidc.enabled else None,
    })
