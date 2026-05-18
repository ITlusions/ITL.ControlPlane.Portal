"""
ITL Control Plane Dashboard - Configuration Management

Loads configuration from environment variables or mounted secret files (Kubernetes).
"""
import os
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field
from loguru import logger


def read_secret_file(file_path: str) -> Optional[str]:
    """Safely read a secret from a mounted file"""
    try:
        path = Path(file_path)
        if path.exists():
            return path.read_text().strip()
    except Exception as e:
        logger.error(f"Failed to read secret file {file_path}: {e}")
    return None


@dataclass
class OIDCConfig:
    """OIDC/Keycloak configuration"""
    enabled: bool = False
    issuer: str = ""
    client_id: str = ""
    client_secret: str = ""
    auth_uri: str = ""
    token_uri: str = ""
    userinfo_uri: str = ""
    logout_uri: str = ""
    redirect_uri: str = ""  # Optional override for callback URL
    scopes: list = field(default_factory=lambda: ["openid", "email", "profile"])
    
    @property
    def is_configured(self) -> bool:
        return bool(self.issuer and self.client_id and self.client_secret)


@dataclass
class Settings:
    """Application settings"""
    # Server
    host: str = "0.0.0.0"
    port: int = 8090
    debug: bool = False
    
    # API Gateway
    api_gateway_url: str = "http://localhost:8081"
    
    # Session
    secret_key: str = "dev-secret-key-change-in-production"
    session_cookie_name: str = "itl_session"
    session_max_age: int = 3600 * 8  # 8 hours
    
    # Redis (session storage)
    redis_url: Optional[str] = None  # e.g., redis://redis:6379/0
    
    # OIDC
    oidc: OIDCConfig = field(default_factory=OIDCConfig)
    
    # Paths
    templates_dir: str = "templates"
    static_dir: str = "static"
    config_dir: str = "config"
    data_dir: str = "data"


def _detect_oidc_source() -> Optional[str]:
    """Detect where OIDC configuration is available"""
    secret_path = Path("/etc/secrets/keycloak")
    
    # Check mounted secret files (Kubernetes)
    if secret_path.exists():
        required_files = ["issuer", "client-id", "client-secret"]
        if all((secret_path / f).exists() for f in required_files):
            logger.info("OIDC configuration found in mounted secret files")
            return "files"
    
    # Check environment variables
    # Support both KEYCLOAK_ISSUER or KEYCLOAK_SERVER_URL + KEYCLOAK_REALM
    if "KEYCLOAK_ISSUER" in os.environ:
        has_issuer = True
    elif "KEYCLOAK_SERVER_URL" in os.environ and "KEYCLOAK_REALM" in os.environ:
        has_issuer = True
    else:
        has_issuer = False
    
    required_vars = ["KEYCLOAK_CLIENT_ID", "KEYCLOAK_CLIENT_SECRET"]
    if has_issuer and all(var in os.environ for var in required_vars):
        logger.info("OIDC configuration found in environment variables")
        return "env"
    
    logger.warning("No OIDC configuration found - authentication will be disabled")
    return None


def _load_oidc_from_files() -> OIDCConfig:
    """Load OIDC config from mounted secret files"""
    secret_path = Path("/etc/secrets/keycloak")
    
    issuer = read_secret_file(secret_path / "issuer")
    client_id = read_secret_file(secret_path / "client-id")
    client_secret = read_secret_file(secret_path / "client-secret")
    
    # Build URIs from issuer
    auth_uri = f"{issuer}/protocol/openid-connect/auth"
    token_uri = read_secret_file(secret_path / "token-uri") or f"{issuer}/protocol/openid-connect/token"
    userinfo_uri = read_secret_file(secret_path / "userinfo-uri") or f"{issuer}/protocol/openid-connect/userinfo"
    logout_uri = f"{issuer}/protocol/openid-connect/logout"
    
    logger.info(f"Loaded OIDC issuer: {issuer}")
    logger.info(f"Loaded OIDC client_id: {client_id}")
    logger.debug(f"Loaded OIDC client_secret: {client_secret[:8]}..." if client_secret else "No secret")
    
    return OIDCConfig(
        enabled=True,
        issuer=issuer,
        client_id=client_id,
        client_secret=client_secret,
        auth_uri=auth_uri,
        token_uri=token_uri,
        userinfo_uri=userinfo_uri,
        logout_uri=logout_uri,
    )


def _load_oidc_from_env() -> OIDCConfig:
    """Load OIDC config from environment variables"""
    # Support both KEYCLOAK_ISSUER or KEYCLOAK_SERVER_URL + KEYCLOAK_REALM
    if "KEYCLOAK_ISSUER" in os.environ:
        issuer = os.environ["KEYCLOAK_ISSUER"]
    else:
        server_url = os.environ["KEYCLOAK_SERVER_URL"].rstrip("/")
        realm = os.environ["KEYCLOAK_REALM"]
        issuer = f"{server_url}/realms/{realm}"
    
    client_id = os.environ["KEYCLOAK_CLIENT_ID"]
    client_secret = os.environ["KEYCLOAK_CLIENT_SECRET"]
    
    auth_uri = os.environ.get("KEYCLOAK_AUTH_URI", f"{issuer}/protocol/openid-connect/auth")
    token_uri = os.environ.get("KEYCLOAK_TOKEN_URI", f"{issuer}/protocol/openid-connect/token")
    userinfo_uri = os.environ.get("KEYCLOAK_USERINFO_URI", f"{issuer}/protocol/openid-connect/userinfo")
    logout_uri = os.environ.get("KEYCLOAK_LOGOUT_URI", f"{issuer}/protocol/openid-connect/logout")
    redirect_uri = os.environ.get("KEYCLOAK_REDIRECT_URI", "")
    
    logger.info(f"Loaded OIDC issuer: {issuer}")
    logger.info(f"Loaded OIDC client_id: {client_id}")
    if redirect_uri:
        logger.info(f"Loaded OIDC redirect_uri: {redirect_uri}")
    logger.debug(f"Loaded OIDC client_secret: {client_secret[:8]}..." if client_secret else "No secret")
    
    return OIDCConfig(
        enabled=True,
        issuer=issuer,
        client_id=client_id,
        client_secret=client_secret,
        auth_uri=auth_uri,
        token_uri=token_uri,
        userinfo_uri=userinfo_uri,
        logout_uri=logout_uri,
        redirect_uri=redirect_uri,
    )


def load_settings() -> Settings:
    """Load all application settings"""
    logger.info("Loading application settings...")
    
    # Load OIDC configuration
    oidc_source = _detect_oidc_source()
    if oidc_source == "files":
        oidc = _load_oidc_from_files()
    elif oidc_source == "env":
        oidc = _load_oidc_from_env()
    else:
        oidc = OIDCConfig(enabled=False)
    
    # Load secret key
    secret_path = Path("/etc/secrets/keycloak/flask-secret-key")
    if secret_path.exists():
        secret_key = read_secret_file(secret_path) or "dev-secret-key"
    else:
        secret_key = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")
    
    settings = Settings(
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8090")),
        debug=os.getenv("DEBUG", "false").lower() == "true",
        api_gateway_url=os.getenv("API_GATEWAY_URL", "http://localhost:8081"),
        secret_key=secret_key,
        session_cookie_name=os.getenv("SESSION_COOKIE_NAME", "itl_session"),
        session_max_age=int(os.getenv("SESSION_MAX_AGE", str(3600 * 8))),
        redis_url=os.getenv("REDIS_URL"),  # e.g., redis://redis:6379/0
        oidc=oidc,
    )
    
    if oidc.enabled:
        logger.success(f"Authentication enabled (OIDC from {oidc_source})")
    else:
        logger.warning("Authentication disabled - running in anonymous mode")
    
    return settings


# Global settings instance (lazy loaded)
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Get the global settings instance"""
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings
