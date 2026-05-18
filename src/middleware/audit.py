"""
ITL Control Plane Dashboard - Audit Log Middleware

Logs all requests with correlation IDs and timing information.
"""
import os
import time
import uuid
import socket
from datetime import datetime, timezone

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from loguru import logger


class AuditLogMiddleware(BaseHTTPMiddleware):
    """Middleware for audit logging of all requests"""
    
    def __init__(self, app, exempt_paths: list = None, exempt_user_agents: list = None):
        super().__init__(app)
        self.node_name = os.getenv("NODE_NAME", "unknown")
        self.hostname = os.getenv("HOSTNAME", socket.gethostname())
        self.app_version = os.getenv("APP_VERSION", "1.0.0")
        self.environment = os.getenv("ENVIRONMENT", "development")
        
        # Paths to skip logging (health checks, static assets)
        self.exempt_paths = exempt_paths or ["/health", "/assets/", "/favicon.ico"]
        self.exempt_user_agents = exempt_user_agents or ["kube-probe", "Prometheus"]
    
    def _should_log(self, request: Request) -> bool:
        """Check if this request should be logged"""
        # Skip exempt paths
        path = request.url.path
        if any(path.startswith(p) for p in self.exempt_paths):
            return False
        
        # Skip exempt user agents (health probes)
        user_agent = request.headers.get("user-agent", "")
        if any(agent in user_agent for agent in self.exempt_user_agents):
            return False
        
        return True
    
    async def dispatch(self, request: Request, call_next) -> Response:
        # Generate request/correlation IDs
        request_id = str(uuid.uuid4())
        correlation_id = request.headers.get("x-correlation-id", str(uuid.uuid4()))
        
        # Store in request state for access in routes
        request.state.request_id = request_id
        request.state.correlation_id = correlation_id
        
        # Start timing
        start_time = time.perf_counter()
        
        # Log request (if not exempt)
        if self._should_log(request):
            client_ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "unknown")
            logger.info(
                f"[{request_id[:8]}] {request.method} {request.url.path} "
                f"from {client_ip}"
            )
        
        # Process request
        try:
            response = await call_next(request)
        except Exception as e:
            duration = time.perf_counter() - start_time
            logger.error(
                f"[{request_id[:8]}] Request failed after {duration:.3f}s: {e}"
            )
            raise
        
        # Calculate duration
        duration = time.perf_counter() - start_time
        
        # Add response headers
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Correlation-ID"] = correlation_id
        response.headers["X-Response-Time"] = f"{duration:.3f}s"
        response.headers["X-Server-Hostname"] = self.hostname
        response.headers["X-App-Version"] = self.app_version
        
        # Log response (if not exempt)
        if self._should_log(request):
            logger.info(
                f"[{request_id[:8]}] {response.status_code} in {duration:.3f}s"
            )
        
        return response
