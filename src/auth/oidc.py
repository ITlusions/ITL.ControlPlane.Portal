"""
ITL Control Plane Dashboard - OIDC Client

Handles OAuth2/OIDC authentication with Keycloak using authlib.
"""
import secrets
from typing import Optional, Dict, Any
from urllib.parse import urlencode

import httpx
from loguru import logger

from src.config import get_settings, OIDCConfig


class OIDCClient:
    """OIDC client for Keycloak authentication"""
    
    def __init__(self, config: OIDCConfig):
        self.config = config
        self._http_client: Optional[httpx.AsyncClient] = None
    
    @property
    def enabled(self) -> bool:
        return self.config.enabled and self.config.is_configured
    
    async def get_http_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client"""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=30.0)
        return self._http_client
    
    async def close(self):
        """Close HTTP client"""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
    
    def generate_state(self) -> str:
        """Generate a random state parameter for CSRF protection"""
        return secrets.token_urlsafe(32)
    
    def get_authorization_url(self, redirect_uri: str, state: str) -> str:
        """Build the authorization URL for redirecting to Keycloak"""
        params = {
            "client_id": self.config.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.config.scopes),
            "state": state,
        }
        return f"{self.config.auth_uri}?{urlencode(params)}"
    
    async def exchange_code(self, code: str, redirect_uri: str) -> Optional[Dict[str, Any]]:
        """Exchange authorization code for tokens"""
        client = await self.get_http_client()
        
        data = {
            "grant_type": "authorization_code",
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        }
        
        try:
            response = await client.post(self.config.token_uri, data=data)
            if response.status_code == 200:
                tokens = response.json()
                logger.info("Successfully exchanged code for tokens")
                return tokens
            else:
                logger.error(f"Token exchange failed: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            logger.error(f"Token exchange error: {e}")
            return None
    
    async def refresh_token(self, refresh_token: str) -> Optional[Dict[str, Any]]:
        """Refresh access token using refresh token"""
        client = await self.get_http_client()
        
        data = {
            "grant_type": "refresh_token",
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
            "refresh_token": refresh_token,
        }
        
        try:
            response = await client.post(self.config.token_uri, data=data)
            if response.status_code == 200:
                return response.json()
            else:
                logger.warning(f"Token refresh failed: {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"Token refresh error: {e}")
            return None
    
    async def get_userinfo(self, access_token: str) -> Optional[Dict[str, Any]]:
        """Fetch user info from userinfo endpoint"""
        client = await self.get_http_client()
        
        try:
            response = await client.get(
                self.config.userinfo_uri,
                headers={"Authorization": f"Bearer {access_token}"}
            )
            if response.status_code == 200:
                return response.json()
            else:
                logger.warning(f"Userinfo request failed: {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"Userinfo error: {e}")
            return None
    
    def get_logout_url(self, redirect_uri: str, id_token_hint: Optional[str] = None) -> str:
        """Build the logout URL for Keycloak"""
        params = {
            "client_id": self.config.client_id,
            "post_logout_redirect_uri": redirect_uri,
        }
        if id_token_hint:
            params["id_token_hint"] = id_token_hint
        return f"{self.config.logout_uri}?{urlencode(params)}"


# Global OIDC client instance
_oidc_client: Optional[OIDCClient] = None


def get_oidc_client() -> OIDCClient:
    """Get the global OIDC client instance"""
    global _oidc_client
    if _oidc_client is None:
        settings = get_settings()
        _oidc_client = OIDCClient(settings.oidc)
    return _oidc_client
