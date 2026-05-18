"""
ITL Control Plane Dashboard - Session Management

Server-side session storage with secure session ID cookies.
Tokens are stored server-side, only a random session ID is in the cookie.

Supports:
- In-memory storage (development, single instance)
- Redis storage (production, horizontally scalable)
"""
import secrets
import time
import hmac
import hashlib
import json
from typing import Optional, Dict, Any, Protocol
from dataclasses import dataclass, asdict
from threading import Lock
from abc import ABC, abstractmethod

from loguru import logger

from src.config import get_settings


@dataclass
class UserSession:
    """User session data - stored server-side, never in cookie"""
    session_id: str
    user_id: str
    email: str
    name: str
    given_name: str = ""
    family_name: str = ""
    access_token: str = ""
    refresh_token: str = ""
    id_token: str = ""
    token_expires_at: float = 0.0
    created_at: float = 0.0
    last_accessed: float = 0.0
    
    @property
    def is_expired(self) -> bool:
        """Check if the session token is expired"""
        return time.time() > self.token_expires_at
    
    @property
    def display_name(self) -> str:
        """Get display name"""
        return self.name or self.given_name or self.email.split("@")[0]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary (for JSON serialization)"""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UserSession":
        """Create from dictionary"""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
    
    @classmethod
    def from_tokens(cls, session_id: str, tokens: Dict[str, Any], userinfo: Dict[str, Any]) -> "UserSession":
        """Create session from OIDC tokens and userinfo"""
        expires_in = tokens.get("expires_in", 3600)
        now = time.time()
        return cls(
            session_id=session_id,
            user_id=userinfo.get("sub", ""),
            email=userinfo.get("email", ""),
            name=userinfo.get("name", ""),
            given_name=userinfo.get("given_name", ""),
            family_name=userinfo.get("family_name", ""),
            access_token=tokens.get("access_token", ""),
            refresh_token=tokens.get("refresh_token", ""),
            id_token=tokens.get("id_token", ""),
            token_expires_at=now + expires_in - 60,
            created_at=now,
            last_accessed=now,
        )


class SessionStore(ABC):
    """Abstract base class for session stores"""
    
    @abstractmethod
    def create(self, tokens: Dict[str, Any], userinfo: Dict[str, Any]) -> UserSession:
        """Create a new session and return it"""
        pass
    
    @abstractmethod
    def get(self, session_id: str) -> Optional[UserSession]:
        """Get session by ID, returns None if not found or expired"""
        pass
    
    @abstractmethod
    def delete(self, session_id: str) -> Optional[UserSession]:
        """Delete session and return it (for logout)"""
        pass
    
    @abstractmethod
    def cleanup_expired(self):
        """Remove expired sessions"""
        pass


class InMemorySessionStore(SessionStore):
    """
    In-memory session store. 
    For development and single-instance deployments.
    Sessions are lost on restart.
    """
    
    def __init__(self, max_age: int = 3600 * 8):
        self._sessions: Dict[str, UserSession] = {}
        self._lock = Lock()
        self.max_age = max_age
    
    def create(self, tokens: Dict[str, Any], userinfo: Dict[str, Any]) -> UserSession:
        """Create a new session and return it"""
        session_id = secrets.token_urlsafe(32)
        session = UserSession.from_tokens(session_id, tokens, userinfo)
        
        with self._lock:
            self._sessions[session_id] = session
        
        logger.debug(f"Created in-memory session {session_id[:8]}... for {session.email}")
        return session
    
    def get(self, session_id: str) -> Optional[UserSession]:
        """Get session by ID, returns None if not found or expired"""
        with self._lock:
            session = self._sessions.get(session_id)
            
            if not session:
                return None
            
            # Check if session is too old
            if time.time() - session.created_at > self.max_age:
                del self._sessions[session_id]
                logger.debug(f"Session {session_id[:8]}... expired (age)")
                return None
            
            # Update last accessed
            session.last_accessed = time.time()
            return session
    
    def delete(self, session_id: str) -> Optional[UserSession]:
        """Delete session and return it (for logout)"""
        with self._lock:
            return self._sessions.pop(session_id, None)
    
    def cleanup_expired(self):
        """Remove expired sessions (call periodically)"""
        now = time.time()
        with self._lock:
            expired = [
                sid for sid, sess in self._sessions.items()
                if now - sess.created_at > self.max_age
            ]
            for sid in expired:
                del self._sessions[sid]
            if expired:
                logger.debug(f"Cleaned up {len(expired)} expired in-memory sessions")


class RedisSessionStore(SessionStore):
    """
    Redis-backed session store.
    For production: persistent across restarts, horizontally scalable.
    """
    
    def __init__(self, redis_url: str, max_age: int = 3600 * 8, key_prefix: str = "session:"):
        try:
            import redis
        except ImportError:
            raise ImportError("redis package required: pip install redis[hiredis]")
        
        self.redis = redis.from_url(redis_url, decode_responses=True)
        self.max_age = max_age
        self.key_prefix = key_prefix
        
        # Test connection
        self.redis.ping()
        logger.info(f"Connected to Redis for session storage")
    
    def _key(self, session_id: str) -> str:
        """Build Redis key for session"""
        return f"{self.key_prefix}{session_id}"
    
    def create(self, tokens: Dict[str, Any], userinfo: Dict[str, Any]) -> UserSession:
        """Create a new session and store in Redis"""
        session_id = secrets.token_urlsafe(32)
        session = UserSession.from_tokens(session_id, tokens, userinfo)
        
        # Store as JSON with TTL
        key = self._key(session_id)
        self.redis.setex(key, self.max_age, json.dumps(session.to_dict()))
        
        logger.debug(f"Created Redis session {session_id[:8]}... for {session.email}")
        return session
    
    def get(self, session_id: str) -> Optional[UserSession]:
        """Get session from Redis"""
        key = self._key(session_id)
        data = self.redis.get(key)
        
        if not data:
            return None
        
        try:
            session = UserSession.from_dict(json.loads(data))
            
            # Update last accessed (refresh TTL)
            session.last_accessed = time.time()
            self.redis.setex(key, self.max_age, json.dumps(session.to_dict()))
            
            return session
        except Exception as e:
            logger.error(f"Failed to deserialize session: {e}")
            return None
    
    def delete(self, session_id: str) -> Optional[UserSession]:
        """Delete session from Redis and return it"""
        key = self._key(session_id)
        
        # Get before delete (for logout with id_token)
        data = self.redis.get(key)
        self.redis.delete(key)
        
        if data:
            try:
                return UserSession.from_dict(json.loads(data))
            except Exception:
                pass
        return None
    
    def cleanup_expired(self):
        """Redis handles TTL automatically, nothing to do"""
        pass


def create_session_store(redis_url: Optional[str] = None, max_age: int = 3600 * 8) -> SessionStore:
    """
    Factory function to create the appropriate session store.
    Uses Redis if REDIS_URL is set, otherwise falls back to in-memory.
    """
    if redis_url:
        try:
            store = RedisSessionStore(redis_url, max_age)
            logger.success("Using Redis for session storage (production mode)")
            return store
        except Exception as e:
            logger.warning(f"Failed to connect to Redis: {e}")
            logger.warning("Falling back to in-memory session storage")
    
    logger.info("Using in-memory session storage (development mode)")
    return InMemorySessionStore(max_age)


class SessionManager:
    """
    Manages session cookies with server-side storage.
    Cookie only contains signed session ID, all data stored server-side.
    """
    
    def __init__(self, secret_key: str, max_age: int = 3600 * 8, redis_url: Optional[str] = None):
        self.secret_key = secret_key.encode("utf-8")
        self.max_age = max_age
        self.store = create_session_store(redis_url, max_age)
    
    def _sign(self, session_id: str) -> str:
        """Create HMAC signature for session ID"""
        return hmac.new(self.secret_key, session_id.encode(), hashlib.sha256).hexdigest()[:16]
    
    def _verify(self, session_id: str, signature: str) -> bool:
        """Verify HMAC signature"""
        expected = self._sign(session_id)
        return hmac.compare_digest(expected, signature)
    
    def create_session(self, tokens: Dict[str, Any], userinfo: Dict[str, Any]) -> tuple:
        """
        Create a new session and return (session, cookie_value).
        Cookie value is just: session_id.signature (small, ~60 chars)
        """
        session = self.store.create(tokens, userinfo)
        signature = self._sign(session.session_id)
        cookie_value = f"{session.session_id}.{signature}"
        return session, cookie_value
    
    def get_session(self, cookie_value: str) -> Optional[UserSession]:
        """Get session from cookie value"""
        if not cookie_value:
            return None
        
        try:
            parts = cookie_value.split(".")
            if len(parts) != 2:
                logger.warning("Invalid session cookie format")
                return None
            
            session_id, signature = parts
            
            if not self._verify(session_id, signature):
                logger.warning("Invalid session signature")
                return None
            
            return self.store.get(session_id)
            
        except Exception as e:
            logger.error(f"Failed to decode session: {e}")
            return None
    
    def delete_session(self, cookie_value: str) -> Optional[UserSession]:
        """Delete session and return it (for logout with id_token)"""
        if not cookie_value:
            return None
        
        try:
            parts = cookie_value.split(".")
            if len(parts) != 2:
                return None
            
            session_id, signature = parts
            if not self._verify(session_id, signature):
                return None
            
            return self.store.delete(session_id)
        except Exception:
            return None
    
    # Backwards compatibility
    def encode_session(self, session: UserSession) -> str:
        """Encode session to cookie value (just the signed session ID)"""
        signature = self._sign(session.session_id)
        return f"{session.session_id}.{signature}"
    
    def decode_session(self, cookie_value: str) -> Optional[UserSession]:
        """Decode session from cookie value"""
        return self.get_session(cookie_value)


# Global session manager
_session_manager: Optional[SessionManager] = None


def get_session_manager() -> SessionManager:
    """Get the global session manager instance"""
    global _session_manager
    if _session_manager is None:
        settings = get_settings()
        _session_manager = SessionManager(
            settings.secret_key, 
            settings.session_max_age,
            settings.redis_url,
        )
    return _session_manager
