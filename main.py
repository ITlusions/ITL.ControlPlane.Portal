"""ITL Control Plane Portal - Customer Self-Service Portal

Customer-facing portal for resource management and self-service:
- Resource browsing and management
- Subscription and resource group management  
- Activity logs (scoped to user's resources)
- Resource visualization

NOTE: This portal intentionally has NO access to platform infrastructure.
For infrastructure management, use the Admin Portal.
"""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
import httpx
from loguru import logger
import os
import yaml

# Authentication imports
from src.config import get_settings
from src.middleware.audit import AuditLogMiddleware
from src.routers.auth import router as auth_router
from src.auth.session import UserSession, get_session_manager
from src.auth.oidc import get_oidc_client
from src.widgets import get_widgets_for_user, get_user_roles

# ════════════════════════════════════════════════════════════════════
# Sample Queries Configuration
# ════════════════════════════════════════════════════════════════════

CONFIG_DIR = Path(__file__).parent / "config"
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

def load_builtin_sample_queries() -> list:
    """Load built-in sample queries from config/sample_queries.yaml"""
    config_file = CONFIG_DIR / "sample_queries.yaml"
    if not config_file.exists():
        logger.warning(f"Sample queries config not found: {config_file}")
        return []
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            samples = data.get("samples", [])
            # Add source marker
            for s in samples:
                s["source"] = "builtin"
            return samples
    except Exception as e:
        logger.error(f"Error loading sample queries: {e}")
        return []

def load_user_sample_queries() -> list:
    """Load user-defined sample queries from data/user_queries.json"""
    user_file = DATA_DIR / "user_queries.json"
    if not user_file.exists():
        return []
    try:
        with open(user_file, "r", encoding="utf-8") as f:
            queries = json.load(f)
            for q in queries:
                q["source"] = "user"
            return queries
    except Exception as e:
        logger.error(f"Error loading user queries: {e}")
        return []

def save_user_sample_queries(queries: list) -> bool:
    """Save user-defined sample queries to data/user_queries.json"""
    user_file = DATA_DIR / "user_queries.json"
    try:
        # Remove source markers before saving
        clean_queries = [{k: v for k, v in q.items() if k != "source"} for q in queries]
        with open(user_file, "w", encoding="utf-8") as f:
            json.dump(clean_queries, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Error saving user queries: {e}")
        return False

def get_all_sample_queries() -> dict:
    """Get all sample queries grouped by category"""
    builtin = load_builtin_sample_queries()
    user = load_user_sample_queries()
    
    # Group by category
    categories = {}
    for q in builtin + user:
        cat = q.get("category", "Other")
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(q)
    
    return {
        "categories": categories,
        "builtin_count": len(builtin),
        "user_count": len(user),
    }

# ══════════════════════════════════════════════════════════════════
# PORTAL NOTE: No Docker/Kubernetes access for security
# Infrastructure management is only available in Admin Portal
# ══════════════════════════════════════════════════════════════════
DOCKER_AVAILABLE = False
KUBERNETES_AVAILABLE = False
RUNNING_IN_KUBERNETES = False
logger.info("Portal mode: Infrastructure features disabled by design")

# Load settings
settings = get_settings()

app = FastAPI(
    title="ITL Control Plane Portal",
    description="Customer self-service portal for ITL Control Plane resource management",
    version="1.0.0",
)

# Add middleware
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie=settings.session_cookie_name,
    max_age=settings.session_max_age,
)
app.add_middleware(AuditLogMiddleware)

# Include auth routes
app.include_router(auth_router)

# Mount static files
app.mount("/assets", StaticFiles(directory="static/assets"), name="assets")


def get_current_user(request: Request) -> Optional[UserSession]:
    """Get current user from session cookie"""
    cookie_value = request.cookies.get(settings.session_cookie_name)
    if not cookie_value:
        return None
    session_manager = get_session_manager()
    return session_manager.decode_session(cookie_value)


def get_auth_context(request: Request) -> dict:
    """Get authentication context for templates"""
    user = get_current_user(request)
    oidc = get_oidc_client()
    return {
        "auth_enabled": oidc.enabled,
        "user": user,
        "is_authenticated": user is not None,
    }


def require_auth(request: Request) -> Optional[RedirectResponse]:
    """
    Check if authentication is required and user is logged in.
    Returns RedirectResponse to login if auth required but not logged in.
    Returns None if access should be granted.
    """
    oidc = get_oidc_client()
    if not oidc.enabled:
        # Auth not configured - allow anonymous access
        return None
    
    user = get_current_user(request)
    if user:
        # User is logged in
        return None
    
    # Auth required but not logged in - redirect to login
    next_url = str(request.url)
    return RedirectResponse(url=f"/login?next={next_url}", status_code=302)


def is_authenticated(request: Request) -> bool:
    """
    Check if user is authenticated (for API endpoints).
    Returns True if auth is disabled OR user is logged in.
    """
    oidc = get_oidc_client()
    if not oidc.enabled:
        # Auth not configured - allow anonymous access
        return True
    return get_current_user(request) is not None


# Templates
templates = Jinja2Templates(directory="templates")

# Dashboard connects ONLY via API Gateway - never directly to providers
# NOTE: Docker Compose maps 8081->8080, so use 8081 for local dev
API_GATEWAY_URL = os.getenv("API_GATEWAY_URL", "http://localhost:8081")


async def _fetch_landing_stats() -> dict:
    """Fetch counts for the landing page stats"""
    stats = {
        "tenants": 0,
        "management_groups": 0,
        "subscriptions": 0,
        "resource_groups": 0,
        "locations": 0,
        "providers": 1,
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            # Fetch counts in parallel
            # Note: /tenants is directly on gateway, others use /resources/ prefix
            endpoints = [
                ("tenants", f"{API_GATEWAY_URL}/tenants"),
                ("management_groups", f"{API_GATEWAY_URL}/resources/managementgroups"),
                ("subscriptions", f"{API_GATEWAY_URL}/resources/subscriptions"),
                ("resource_groups", f"{API_GATEWAY_URL}/resources/resourcegroups"),
                ("locations", f"{API_GATEWAY_URL}/resources/locations"),
            ]
            for key, url in endpoints:
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        data = resp.json()
                        if isinstance(data, list):
                            stats[key] = len(data)
                        elif isinstance(data, dict):
                            stats[key] = data.get("count", len(data.get("resources", [])))
                except Exception:
                    pass
    except Exception as e:
        logger.warning(f"Failed to fetch landing stats: {e}")
    return stats


@app.get("/", response_class=HTMLResponse)
async def landing_page(request: Request):
    """Public landing page with role-based widgets"""
    # Get auth context and user
    auth_ctx = get_auth_context(request)
    user = auth_ctx.get("user")
    
    # Get user roles and visible widgets
    user_roles = get_user_roles(user)
    widgets = get_widgets_for_user(user_roles)
    
    # Fetch stats
    stats = await _fetch_landing_stats()
    
    # Build widget data context
    widget_data = {
        "stats": stats,
    }
    
    # Portal: No infra data - that's Admin Portal only
    # Fetch recent activity for authenticated users
    if "authenticated" in user_roles:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{API_GATEWAY_URL}/activity-logs", params={"limit": 5})
                if resp.status_code == 200:
                    data = resp.json()
                    widget_data["recent_activity"] = data.get("value", [])
        except Exception:
            widget_data["recent_activity"] = []
    
    return templates.TemplateResponse("landing.html", {
        "request": request,
        **auth_ctx,
        "stats": stats,
        "widgets": widgets,
        "widget_data": widget_data,
        "user_roles": user_roles,
        "is_admin": False,  # Portal users are never platform admins
    })


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy", "service": "ITL Control Plane Portal"}


@app.get("/api/graph")
async def graph_data(
    rg_limit: int = 1000,
    depth: int = 4,  # 1=tenant, 2=+mg, 3=+sub, 4=+rg, 5=+location
):
    """Return graph nodes and links for D3.js visualization from the resource hierarchy.
    
    Args:
        rg_limit: Maximum number of resource groups to include (default 1000)
        depth: Hierarchy depth to show (1=tenant, 2=+mg, 3=+sub, 4=+rg, 5=+location)
    """
    nodes = []
    links = []
    node_ids = set()

    def _extract_name(resource_path: str) -> str:
        """Extract the short name from a full resource path like /providers/ITL.Core/tenants/ITL -> ITL"""
        if not resource_path:
            return ""
        return resource_path.rstrip("/").split("/")[-1]

    def _safe_list(data):
        """Normalize API response to a list of resources"""
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("resources", data.get("items", []))
        return []

    location_names = set()
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Always fetch tenants (depth >= 1)
            t_resp = await client.get(f"{API_GATEWAY_URL}/tenants")
            tenants = _safe_list(t_resp.json())
            
            # Fetch based on depth
            mgs, subs, rgs, locations = [], [], [], []
            if depth >= 2:
                mg_resp = await client.get(f"{API_GATEWAY_URL}/resources/managementgroups")
                mgs = _safe_list(mg_resp.json())
            if depth >= 3:
                s_resp = await client.get(f"{API_GATEWAY_URL}/resources/subscriptions")
                subs = _safe_list(s_resp.json())
            if depth >= 4:
                rg_resp = await client.get(f"{API_GATEWAY_URL}/resources/resourcegroups")
                rgs = _safe_list(rg_resp.json())
            if depth >= 5:
                loc_resp = await client.get(f"{API_GATEWAY_URL}/resources/locations")
                locations = _safe_list(loc_resp.json())

        # Build location nodes first (they will be linked from RGs)
        for loc in locations:
            loc_name = loc.get("name", "")
            if loc_name and loc_name not in node_ids:
                props = loc.get("properties", {})
                nodes.append({"id": loc_name, "label": props.get("display_name", loc_name), "type": "location"})
                node_ids.add(loc_name)
                location_names.add(loc_name)

        # Build tenant nodes
        for t in tenants:
            tid = t.get("name", "")
            if tid and tid not in node_ids:
                nodes.append({"id": tid, "label": t.get("properties", {}).get("display_name", tid), "type": "tenant"})
                node_ids.add(tid)

        # Build management group nodes + links to parent MG or tenant
        for mg in mgs:
            mg_name = mg.get("name", "")
            props = mg.get("properties", {})
            tenant = _extract_name(props.get("tenant_id", ""))
            parent_mg = _extract_name(props.get("parent_id", ""))
            if mg_name and mg_name not in node_ids:
                nodes.append({"id": mg_name, "label": props.get("display_name", mg_name), "type": "managementgroup"})
                node_ids.add(mg_name)
            if mg_name:
                # Link to parent MG if exists, otherwise link to tenant
                if parent_mg and parent_mg in node_ids:
                    links.append({"source": parent_mg, "target": mg_name})
                elif tenant and tenant in node_ids:
                    links.append({"source": tenant, "target": mg_name})

        # Build subscription nodes + links to management group
        for s in subs:
            s_name = s.get("name", "")
            props = s.get("properties", {})
            mg_parent = _extract_name(props.get("management_group_id", ""))
            if s_name and s_name not in node_ids:
                nodes.append({"id": s_name, "label": props.get("display_name", s_name), "type": "subscription"})
                node_ids.add(s_name)
            if s_name and mg_parent and mg_parent in node_ids:
                links.append({"source": mg_parent, "target": s_name})

        # Build resource group nodes + links to subscription
        rg_count = 0
        for rg in rgs:
            if rg_count >= rg_limit:
                break
            rg_name = rg.get("name", "")
            props = rg.get("properties", {})
            sub_parent = _extract_name(props.get("subscription_id", ""))
            rg_location = rg.get("location", "")
            if rg_name and rg_name not in node_ids:
                nodes.append({"id": rg_name, "label": rg_name, "type": "resourcegroup"})
                node_ids.add(rg_name)
                rg_count += 1
            if rg_name and sub_parent and sub_parent in node_ids:
                links.append({"source": sub_parent, "target": rg_name})
            # Link RG to its location (only if depth includes locations)
            if depth >= 5 and rg_name and rg_location and rg_location in location_names:
                links.append({"source": rg_name, "target": rg_location})

    except Exception as e:
        logger.error(f"Error building graph data: {e}", exc_info=True)

    return {"nodes": nodes, "links": links}


@app.get("/api/activity-logs")
async def get_activity_logs(
    scope: str = "/",
    action: str = None,
    resource_type: str = None,
    limit: int = 50,
    offset: int = 0,
):
    """
    Proxy activity logs from Core Provider with scope-based filtering.
    
    Scope follows ARM hierarchy:
    - "/" or empty: All events (tenant level)
    - "/subscriptions/{name}": Subscription + children
    - "/subscriptions/{sub}/resourceGroups/{rg}": Resource group + children
    - Full resource ID: Single resource
    """
    try:
        params = {"scope": scope, "limit": limit, "offset": offset}
        if action:
            params["action"] = action
        if resource_type:
            params["resource_type"] = resource_type
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{API_GATEWAY_URL}/activity-logs", params=params)
            if resp.status_code == 200:
                return resp.json()
            else:
                logger.error(f"Activity logs request failed: {resp.status_code} {resp.text}")
                return {"value": [], "count": 0, "total_count": 0, "scope": scope, "error": resp.text}
    except Exception as e:
        logger.error(f"Error fetching activity logs: {e}", exc_info=True)
        return {"value": [], "count": 0, "total_count": 0, "scope": scope, "error": str(e)}


# ══════════════════════════════════════════════════════════════════════════════
# Sample Queries API
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/sample-queries")
async def get_sample_queries():
    """Get all sample queries (built-in + user-defined) grouped by category"""
    return get_all_sample_queries()


@app.post("/api/sample-queries")
async def save_sample_query(request: Request):
    """Save a new user-defined sample query"""
    try:
        body = await request.json()
        name = body.get("name", "").strip()
        query = body.get("query", "").strip()
        category = body.get("category", "My Queries").strip()
        description = body.get("description", "").strip()
        
        if not name or not query:
            return JSONResponse({"error": "Name and query are required"}, status_code=400)
        
        # Load existing user queries
        user_queries = load_user_sample_queries()
        
        # Check for duplicate name
        existing_names = [q.get("name", "").lower() for q in user_queries]
        if name.lower() in existing_names:
            return JSONResponse({"error": f"Query with name '{name}' already exists"}, status_code=409)
        
        # Add new query
        new_query = {
            "name": name,
            "query": query,
            "category": category,
            "description": description,
            "created": datetime.now(timezone.utc).isoformat(),
        }
        user_queries.append(new_query)
        
        if save_user_sample_queries(user_queries):
            new_query["source"] = "user"
            return {"success": True, "query": new_query}
        else:
            return JSONResponse({"error": "Failed to save query"}, status_code=500)
    except Exception as e:
        logger.error(f"Error saving sample query: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.delete("/api/sample-queries/{name}")
async def delete_sample_query(name: str):
    """Delete a user-defined sample query by name"""
    try:
        user_queries = load_user_sample_queries()
        original_count = len(user_queries)
        
        # Filter out the query with matching name (case-insensitive)
        user_queries = [q for q in user_queries if q.get("name", "").lower() != name.lower()]
        
        if len(user_queries) == original_count:
            return JSONResponse({"error": f"Query '{name}' not found"}, status_code=404)
        
        if save_user_sample_queries(user_queries):
            return {"success": True, "deleted": name}
        else:
            return JSONResponse({"error": "Failed to delete query"}, status_code=500)
    except Exception as e:
        logger.error(f"Error deleting sample query: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/activity-logs", response_class=HTMLResponse)
async def activity_logs_page(
    request: Request,
    scope: str = "/",
    action: str = None,
    resource_type: str = None,
    limit: int = 50,
    offset: int = 0,
):
    """Render the Activity Logs page under Security/Monitoring"""
    # Require authentication
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    
    # Fetch activity logs
    logs = []
    total_count = 0
    error = None
    
    try:
        params = {"scope": scope, "limit": limit, "offset": offset}
        if action:
            params["action"] = action
        if resource_type:
            params["resource_type"] = resource_type
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{API_GATEWAY_URL}/activity-logs", params=params)
            if resp.status_code == 200:
                data = resp.json()
                logs = data.get("value", [])
                total_count = data.get("total_count", len(logs))
            else:
                error = f"API returned {resp.status_code}"
    except Exception as e:
        logger.error(f"Error fetching activity logs: {e}", exc_info=True)
        error = str(e)
    
    # Available filters
    actions = ["CREATE", "UPDATE", "DELETE", "READ"]
    resource_types = [
        "ITL.Core/tenants", "ITL.Core/managementgroups", 
        "ITL.Core/subscriptions", "ITL.Core/resourcegroups", "ITL.Core/locations"
    ]
    
    return templates.TemplateResponse("activity_logs.html", {
        "request": request,
        **get_auth_context(request),
        "title": "Activity Logs",
        "icon": "bi-clock-history",
        "logs": logs,
        "total_count": total_count,
        "error": error,
        "sidebar_items": _build_sidebar("activity-logs"),
        "filters": {
            "scope": scope,
            "action": action,
            "resource_type": resource_type,
            "limit": limit,
            "offset": offset,
        },
        "actions": actions,
        "resource_types": resource_types,
    })


@app.get("/talos-clusters", response_class=HTMLResponse)
async def talos_clusters_page(request: Request):
    """Render the Talos Clusters page"""
    # Require authentication
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    
    # Mock data for Talos clusters
    clusters = [
        {
            "name": "talos-prod-eu-west",
            "status": "healthy",
            "nodes": 5,
            "pods": 127,
            "cpu_usage": 42,
            "k8s_version": "1.29.2",
            "talos_version": "1.6.4",
            "region": "eu-west-1",
            "created": "2024-01-15",
            "node_list": [
                {"name": "cp-1", "role": "control-plane", "status": "ready"},
                {"name": "cp-2", "role": "control-plane", "status": "ready"},
                {"name": "cp-3", "role": "control-plane", "status": "ready"},
                {"name": "worker-1", "role": "worker", "status": "ready"},
                {"name": "worker-2", "role": "worker", "status": "ready"},
            ]
        },
        {
            "name": "talos-staging-eu-west",
            "status": "healthy",
            "nodes": 3,
            "pods": 45,
            "cpu_usage": 28,
            "k8s_version": "1.29.2",
            "talos_version": "1.6.4",
            "region": "eu-west-1",
            "created": "2024-02-01",
            "node_list": [
                {"name": "cp-1", "role": "control-plane", "status": "ready"},
                {"name": "worker-1", "role": "worker", "status": "ready"},
                {"name": "worker-2", "role": "worker", "status": "ready"},
            ]
        },
        {
            "name": "talos-dev-local",
            "status": "warning",
            "nodes": 3,
            "pods": 23,
            "cpu_usage": 65,
            "k8s_version": "1.28.5",
            "talos_version": "1.6.2",
            "region": "local",
            "created": "2024-03-10",
            "node_list": [
                {"name": "dev-cp", "role": "control-plane", "status": "ready"},
                {"name": "dev-worker-1", "role": "worker", "status": "ready"},
                {"name": "dev-worker-2", "role": "worker", "status": "not-ready"},
            ]
        },
    ]
    
    return templates.TemplateResponse("talos_clusters.html", {
        "request": request,
        **get_auth_context(request),
        "clusters": clusters,
        "sidebar_items": _build_sidebar("talos-clusters"),
    })


@app.get("/virtual-machines", response_class=HTMLResponse)
async def virtual_machines_page(request: Request):
    """Render the Virtual Machines page"""
    # Require authentication
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    
    # Mock data for VMs
    vms = [
        {
            "name": "vm-app-server-prod-01",
            "status": "running",
            "os_type": "linux",
            "os_name": "Ubuntu 22.04",
            "size": "Standard_D4s_v3",
            "ip_address": "10.0.1.10",
            "resource_group": "rg-production",
            "location": "westeurope",
        },
        {
            "name": "vm-app-server-prod-02",
            "status": "running",
            "os_type": "linux",
            "os_name": "Ubuntu 22.04",
            "size": "Standard_D4s_v3",
            "ip_address": "10.0.1.11",
            "resource_group": "rg-production",
            "location": "westeurope",
        },
        {
            "name": "vm-db-server-prod",
            "status": "running",
            "os_type": "linux",
            "os_name": "Debian 12",
            "size": "Standard_E8s_v3",
            "ip_address": "10.0.2.10",
            "resource_group": "rg-production",
            "location": "westeurope",
        },
        {
            "name": "vm-jumpbox-mgmt",
            "status": "stopped",
            "os_type": "windows",
            "os_name": "Windows Server 2022",
            "size": "Standard_B2s",
            "ip_address": "10.0.3.10",
            "resource_group": "rg-management",
            "location": "westeurope",
        },
        {
            "name": "vm-dev-workstation-01",
            "status": "deallocated",
            "os_type": "linux",
            "os_name": "Ubuntu 24.04",
            "size": "Standard_D8s_v3",
            "ip_address": "10.0.4.10",
            "resource_group": "rg-development",
            "location": "northeurope",
        },
        {
            "name": "vm-ci-runner-01",
            "status": "running",
            "os_type": "linux",
            "os_name": "Ubuntu 22.04",
            "size": "Standard_D2s_v3",
            "ip_address": "10.0.5.10",
            "resource_group": "rg-development",
            "location": "northeurope",
        },
    ]
    
    # Calculate summary
    running = sum(1 for vm in vms if vm["status"] == "running")
    stopped = sum(1 for vm in vms if vm["status"] in ("stopped", "deallocated"))
    
    summary = {
        "total": len(vms),
        "running": running,
        "stopped": stopped,
        "vcpus": 32,  # Mock value
        "memory_gb": 128,  # Mock value
    }
    
    return templates.TemplateResponse("virtual_machines.html", {
        "request": request,
        **get_auth_context(request),
        "vms": vms,
        "summary": summary,
        "sidebar_items": _build_sidebar("virtual-machines"),
    })


@app.get("/resources/{resource_type}", response_class=HTMLResponse)
async def resource_list(request: Request, resource_type: str):
    """Render a resource list page for a given resource type"""
    # Require authentication
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    
    type_map = {
        "all": {
            "title": "All Resources",
            "singular": "Resource",
            "endpoint": None,  # special: fetches all types
            "icon": "bi-collection",
            "color": "primary",
            "columns": [
                {"key": "name", "label": "Name", "link": True},
                {"key": "display_name", "label": "Display Name"},
                {"key": "_resource_type", "label": "Type", "badge": True, "badge_color": "primary"},
                {"key": "_resolved_tenant", "label": "Tenant", "badge": True, "badge_color": "primary"},
                {"key": "location", "label": "Location"},
                {"key": "state", "label": "State", "badge": True},
            ],
        },
        "tenants": {
            "title": "Tenants",
            "singular": "Tenant",
            "endpoint": "/tenants",
            "icon": "bi-building",
            "color": "primary",
            "columns": [
                {"key": "name", "label": "Name", "link": True},
                {"key": "display_name", "label": "Display Name"},
                {"key": "state", "label": "State", "badge": True},
                {"key": "id", "label": "Resource ID", "code": True},
            ],
        },
        "managementgroups": {
            "title": "Management Groups",
            "singular": "Management Group",
            "endpoint": "/resources/managementgroups",
            "icon": "bi-diagram-3",
            "color": "success",
            "columns": [
                {"key": "name", "label": "Name", "link": True},
                {"key": "display_name", "label": "Display Name"},
                {"key": "_resolved_tenant", "label": "Tenant", "badge": True, "badge_color": "primary"},
                {"key": "_resolved_parent", "label": "Parent"},
                {"key": "state", "label": "State", "badge": True},
            ],
        },
        "subscriptions": {
            "title": "Subscriptions",
            "singular": "Subscription",
            "endpoint": "/resources/subscriptions",
            "icon": "bi-credit-card",
            "color": "info",
            "columns": [
                {"key": "name", "label": "Name", "link": True},
                {"key": "display_name", "label": "Display Name"},
                {"key": "_resolved_mg", "label": "Management Group"},
                {"key": "_resolved_tenant", "label": "Tenant", "badge": True, "badge_color": "primary"},
                {"key": "state", "label": "State", "badge": True},
            ],
        },
        "resourcegroups": {
            "title": "Resource Groups",
            "singular": "Resource Group",
            "endpoint": "/resources/resourcegroups",
            "icon": "bi-folder",
            "color": "warning",
            "columns": [
                {"key": "name", "label": "Name", "link": True},
                {"key": "_resolved_subscription", "label": "Subscription"},
                {"key": "_resolved_tenant", "label": "Tenant", "badge": True, "badge_color": "primary"},
                {"key": "location", "label": "Location"},
                {"key": "state", "label": "State", "badge": True},
            ],
        },
        "locations": {
            "title": "Locations",
            "singular": "Location",
            "endpoint": "/resources/locations",
            "icon": "bi-geo-alt",
            "color": "secondary",
            "columns": [
                {"key": "name", "label": "Name", "link": True},
                {"key": "display_name", "label": "Display Name"},
                {"key": "type", "label": "Type"},
                {"key": "state", "label": "State", "badge": True},
            ],
        },
    }

    meta = type_map.get(resource_type)
    if not meta:
        return templates.TemplateResponse("resources.html", {
            "request": request,
            **get_auth_context(request),
            "title": resource_type.title(),
            "singular": resource_type.title(),
            "icon": "bi-question-circle",
            "color": "dark",
            "resources": [],
            "count": 0,
            "resource_type": resource_type,
            "columns": [{"key": "name", "label": "Name", "link": True}],
            "sidebar_items": _build_sidebar(resource_type),
            "filters": {},
        })

    def _extract_name(path: str) -> str:
        if not path:
            return ""
        return path.rstrip("/").split("/")[-1]

    def _safe_list(data):
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("resources", data.get("items", []))
        return []

    # Fetch dashboard stats + graph for the 'all' view
    dashboard_stats = None
    if resource_type == "all":
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                stats_resp = await client.get(f"{API_GATEWAY_URL}/admin/stats")
                dashboard_stats = stats_resp.json()
        except Exception as e:
            logger.error(f"Error fetching stats: {e}")
            dashboard_stats = {
                "postgresql": {"total": 0, "tenants": 0, "management_groups": 0, "subscriptions": 0, "resource_groups": 0, "locations": 0},
                "neo4j": {"connected": False, "total_nodes": 0, "total_relationships": 0}
            }

    # Type label mapping for the 'all' view
    _type_labels = {
        "tenants": "Tenant",
        "managementgroups": "Management Group",
        "subscriptions": "Subscription",
        "resourcegroups": "Resource Group",
        "locations": "Location",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            if resource_type == "all":
                # Fetch all resource types in parallel
                t_resp, mg_resp, s_resp, rg_resp, l_resp = await asyncio.gather(
                    client.get(f"{API_GATEWAY_URL}/tenants"),
                    client.get(f"{API_GATEWAY_URL}/resources/managementgroups"),
                    client.get(f"{API_GATEWAY_URL}/resources/subscriptions"),
                    client.get(f"{API_GATEWAY_URL}/resources/resourcegroups"),
                    client.get(f"{API_GATEWAY_URL}/resources/locations"),
                )
                all_tenants = _safe_list(t_resp.json())
                all_mgs = _safe_list(mg_resp.json())
                all_subs = _safe_list(s_resp.json())
                all_rgs = _safe_list(rg_resp.json())
                all_locs = _safe_list(l_resp.json())

                # Tag each resource with its type for link routing
                for r in all_tenants: r["_src_type"] = "tenants"
                for r in all_mgs: r["_src_type"] = "managementgroups"
                for r in all_subs: r["_src_type"] = "subscriptions"
                for r in all_rgs: r["_src_type"] = "resourcegroups"
                for r in all_locs: r["_src_type"] = "locations"

                resources = all_tenants + all_mgs + all_subs + all_rgs + all_locs
            else:
                response = await client.get(f"{API_GATEWAY_URL}{meta['endpoint']}")
                data = response.json()
                resources = _safe_list(data)

            # Resolve lookups for MGs (needed by subscriptions and resource groups)
            mg_tenant_map = {}
            mg_display_map = {}
            if resource_type in ("all", "resourcegroups", "subscriptions", "managementgroups"):
                if resource_type == "all":
                    mgs = all_mgs  # Already fetched above
                else:
                    mg_resp = await client.get(f"{API_GATEWAY_URL}/resources/managementgroups")
                    mgs = _safe_list(mg_resp.json())
                for mg in mgs:
                    mg_name = mg.get("name", "")
                    tenant = _extract_name(mg.get("properties", {}).get("tenant_id", ""))
                    parent = _extract_name(mg.get("properties", {}).get("parent_id", ""))
                    display = mg.get("properties", {}).get("display_name", mg_name)
                    if mg_name:
                        mg_tenant_map[mg_name] = tenant
                        mg_display_map[mg_name] = display

            # Resolve subscription lookup (needed by resource groups)
            sub_tenant_map = {}
            sub_display_map = {}
            if resource_type in ("all", "resourcegroups"):
                if resource_type == "all":
                    subs = all_subs  # Already fetched above
                else:
                    sub_resp = await client.get(f"{API_GATEWAY_URL}/resources/subscriptions")
                    subs = _safe_list(sub_resp.json())
                for s in subs:
                    s_name = s.get("name", "")
                    mg_name = _extract_name(s.get("properties", {}).get("management_group_id", ""))
                    tenant = mg_tenant_map.get(mg_name, "")
                    display = s.get("properties", {}).get("display_name", s_name)
                    if s_name:
                        sub_tenant_map[s_name] = tenant
                        sub_display_map[s_name] = display

            # Enrich each resource with resolved fields
            for res in resources:
                props = res.get("properties", {}) or {}
                flat = {"name": res.get("name", ""), "type": res.get("type", ""), "location": res.get("location", "")}

                # Display name
                flat["display_name"] = props.get("display_name", "")

                # State
                flat["state"] = props.get("state", "") or props.get("provisioning_state", "")

                # Resource-type-specific resolved fields
                src_type = res.get("_src_type", resource_type)
                flat["_src_type"] = src_type

                if resource_type == "all":
                    flat["_resource_type"] = _type_labels.get(src_type, src_type)

                if src_type == "tenants":
                    flat["_resolved_tenant"] = res.get("name", "")

                elif src_type == "managementgroups":
                    flat["_resolved_tenant"] = _extract_name(props.get("tenant_id", ""))
                    parent_name = _extract_name(props.get("parent_id", ""))
                    flat["_resolved_parent"] = mg_display_map.get(parent_name, parent_name)

                elif src_type == "subscriptions":
                    mg_name = _extract_name(props.get("management_group_id", ""))
                    flat["_resolved_mg"] = mg_display_map.get(mg_name, mg_name)
                    flat["_resolved_tenant"] = mg_tenant_map.get(mg_name, "")

                elif src_type == "resourcegroups":
                    sub_name = _extract_name(props.get("subscription_id", ""))
                    flat["_resolved_subscription"] = sub_display_map.get(sub_name, sub_name)
                    flat["_resolved_tenant"] = sub_tenant_map.get(sub_name, "")

                res["_flat"] = flat

    except Exception as e:
        logger.error(f"Error fetching {resource_type}: {e}", exc_info=True)
        resources = []

    # Collect unique values for filters
    tenants = sorted(set(r.get("_flat", {}).get("_resolved_tenant", "") for r in resources if r.get("_flat", {}).get("_resolved_tenant")))
    locations = sorted(set(r.get("_flat", {}).get("location", "") for r in resources if r.get("_flat", {}).get("location")))

    return templates.TemplateResponse("resources.html", {
        "request": request,
        **get_auth_context(request),
        "title": meta["title"],
        "singular": meta["singular"],
        "icon": meta["icon"],
        "color": meta["color"],
        "resources": resources,
        "count": len(resources),
        "resource_type": resource_type,
        "columns": meta["columns"],
        "sidebar_items": _build_sidebar(resource_type),
        "filters": {"tenants": tenants, "locations": locations},
        "dashboard_stats": dashboard_stats,
    })


def _build_sidebar(active_type: str) -> list:
    """Build sidebar navigation items - Portal Edition (no Infrastructure)"""
    items = [
        # ── Resource Manager ──────────────────────────────────────
        {"type": "", "label": "Resource Manager", "icon": "bi-grid", "is_header": True},
        {"type": "all", "label": "All Resources", "icon": "bi-collection", "href": "/resources/all"},
        {"type": "tenants", "label": "Tenants", "icon": "bi-building", "href": "/resources/tenants"},
        {"type": "managementgroups", "label": "Management Groups", "icon": "bi-diagram-3", "href": "/resources/managementgroups"},
        {"type": "subscriptions", "label": "Subscriptions", "icon": "bi-credit-card", "href": "/resources/subscriptions"},
        {"type": "resourcegroups", "label": "Resource Groups", "icon": "bi-folder", "href": "/resources/resourcegroups"},
        {"type": "locations", "label": "Locations", "icon": "bi-geo-alt", "href": "/resources/locations"},
        {"type": "", "label": "", "icon": "", "is_divider": True},
        {"type": "tags", "label": "Tags", "icon": "bi-tags", "href": "#"},
        {"type": "deployments", "label": "Deployments", "icon": "bi-rocket", "href": "#"},
        {"type": "", "label": "", "icon": "", "is_divider": True},
        # ── Management ────────────────────────────────────────────
        {"type": "", "label": "Management", "icon": "bi-sliders", "is_header": True},
        {"type": "subscription-mgmt", "label": "Subscription Settings", "icon": "bi-credit-card-2-front", "href": "#"},
        {"type": "resource-providers", "label": "Resource Providers", "icon": "bi-plugin", "href": "#"},
        {"type": "resource-locks", "label": "Resource Locks", "icon": "bi-lock", "href": "#"},
        {"type": "governance", "label": "Governance", "icon": "bi-clipboard2-data", "href": "#"},
        {"type": "compliance", "label": "Compliance", "icon": "bi-patch-check", "href": "#"},
        {"type": "quotas", "label": "Quotas & Limits", "icon": "bi-speedometer", "href": "#"},
        {"type": "", "label": "", "icon": "", "is_divider": True},
        # ── Identity ──────────────────────────────────────────────
        {"type": "", "label": "Identity", "icon": "bi-person-badge", "is_header": True},
        {"type": "users", "label": "Users", "icon": "bi-people", "href": "#"},
        {"type": "groups", "label": "Groups", "icon": "bi-people-fill", "href": "#"},
        {"type": "roles", "label": "Roles", "icon": "bi-person-lock", "href": "#"},
        {"type": "service-principals", "label": "Service Principals", "icon": "bi-robot", "href": "#"},
        {"type": "", "label": "", "icon": "", "is_divider": True},
        # ── Compute ───────────────────────────────────────────────
        {"type": "", "label": "Compute", "icon": "bi-cpu", "is_header": True},
        {"type": "talos-clusters", "label": "Secure Talos Clusters", "icon": "bi-shield-lock", "href": "/talos-clusters"},
        {"type": "proxmox-clusters", "label": "Proxmox Clusters", "icon": "bi-hdd-stack", "href": "#"},
        {"type": "virtual-machines", "label": "Virtual Machines", "icon": "bi-pc-display", "href": "/virtual-machines"},
        {"type": "", "label": "", "icon": "", "is_divider": True},
        # ── Networking ────────────────────────────────────────────
        {"type": "", "label": "Networking", "icon": "bi-diagram-2", "is_header": True},
        {"type": "virtual-networks", "label": "Virtual Networks", "icon": "bi-hdd-network", "href": "#"},
        {"type": "subnets", "label": "Subnets", "icon": "bi-bezier2", "href": "#"},
        {"type": "nsgs", "label": "Network Security Groups", "icon": "bi-shield-exclamation", "href": "#"},
        {"type": "load-balancers", "label": "Load Balancers", "icon": "bi-arrows-expand", "href": "#"},
        {"type": "dns-zones", "label": "DNS Zones", "icon": "bi-globe", "href": "#"},
        {"type": "", "label": "", "icon": "", "is_divider": True},
        # ── Storage ───────────────────────────────────────────────
        {"type": "", "label": "Storage", "icon": "bi-database", "is_header": True},
        {"type": "storage-accounts", "label": "Storage Accounts", "icon": "bi-archive", "href": "#"},
        {"type": "blob-containers", "label": "Blob Containers", "icon": "bi-box", "href": "#"},
        {"type": "file-shares", "label": "File Shares", "icon": "bi-folder2-open", "href": "#"},
        {"type": "disks", "label": "Managed Disks", "icon": "bi-disc", "href": "#"},
        {"type": "", "label": "", "icon": "", "is_divider": True},
        # ── Databases ─────────────────────────────────────────────
        {"type": "", "label": "Databases", "icon": "bi-server", "is_header": True},
        {"type": "postgresql", "label": "PostgreSQL", "icon": "bi-database-fill", "href": "#"},
        {"type": "mysql", "label": "MySQL", "icon": "bi-database", "href": "#"},
        {"type": "redis", "label": "Redis Cache", "icon": "bi-lightning", "href": "#"},
        {"type": "", "label": "", "icon": "", "is_divider": True},
        # ── Security ──────────────────────────────────────────────
        {"type": "", "label": "Security", "icon": "bi-shield-lock", "is_header": True},
        {"type": "key-vaults", "label": "Key Vaults", "icon": "bi-key", "href": "#"},
        {"type": "secrets", "label": "Secrets", "icon": "bi-file-lock", "href": "#"},
        {"type": "certificates", "label": "Certificates", "icon": "bi-award", "href": "#"},
        {"type": "policies", "label": "Policies", "icon": "bi-clipboard-check", "href": "#"},
        {"type": "", "label": "", "icon": "", "is_divider": True},
        # ── Monitoring ────────────────────────────────────────────
        {"type": "", "label": "Monitoring", "icon": "bi-graph-up", "is_header": True},
        {"type": "activity-logs", "label": "Activity Logs", "icon": "bi-clock-history", "href": "/activity-logs"},
        {"type": "alerts", "label": "Alerts", "icon": "bi-bell", "href": "#"},
        {"type": "metrics", "label": "Metrics", "icon": "bi-speedometer2", "href": "#"},
        {"type": "log-analytics", "label": "Log Analytics", "icon": "bi-journal-text", "href": "#"},
        {"type": "", "label": "", "icon": "", "is_divider": True},
        # ── Cost Management ───────────────────────────────────────
        {"type": "", "label": "Cost Management", "icon": "bi-currency-dollar", "is_header": True},
        {"type": "budgets", "label": "Budgets", "icon": "bi-piggy-bank", "href": "#"},
        {"type": "cost-analysis", "label": "Cost Analysis", "icon": "bi-pie-chart", "href": "#"},
        {"type": "usage-reports", "label": "Usage Reports", "icon": "bi-bar-chart-line", "href": "#"},
        {"type": "", "label": "", "icon": "", "is_divider": True},
        # ── Automation ────────────────────────────────────────────
        {"type": "", "label": "Automation", "icon": "bi-gear-wide-connected", "is_header": True},
        {"type": "runbooks", "label": "Runbooks", "icon": "bi-journal-code", "href": "#"},
        {"type": "schedules", "label": "Schedules", "icon": "bi-calendar-event", "href": "#"},
        {"type": "pipelines", "label": "Pipelines", "icon": "bi-arrow-left-right", "href": "#"},
        {"type": "jobs", "label": "Jobs", "icon": "bi-play-circle", "href": "#"},
        # NOTE: Infrastructure and Kubernetes sections removed - Admin Portal only
    ]
    for item in items:
        item["active"] = item.get("type") == active_type
    return items


@app.get("/resources/{resource_type}/{resource_name}", response_class=HTMLResponse)
async def resource_detail(request: Request, resource_type: str, resource_name: str):
    """Render a resource detail page with Azure-style split-pane layout"""
    # Require authentication
    auth_redirect = require_auth(request)
    if auth_redirect:
        return auth_redirect
    
    type_map = {
        "tenants": {"title": "Tenant", "plural": "Tenants", "endpoint": "/tenants", "icon": "bi-building", "color": "primary"},
        "managementgroups": {"title": "Management Group", "plural": "Management Groups", "endpoint": "/resources/managementgroups", "icon": "bi-diagram-3", "color": "success"},
        "subscriptions": {"title": "Subscription", "plural": "Subscriptions", "endpoint": "/resources/subscriptions", "icon": "bi-credit-card", "color": "info"},
        "resourcegroups": {"title": "Resource Group", "plural": "Resource Groups", "endpoint": "/resources/resourcegroups", "icon": "bi-folder", "color": "warning"},
        "locations": {"title": "Location", "plural": "Locations", "endpoint": "/resources/locations", "icon": "bi-geo-alt", "color": "secondary"},
    }

    meta = type_map.get(resource_type, {"title": resource_type, "plural": resource_type, "endpoint": f"/resources/{resource_type}", "icon": "bi-question-circle", "color": "dark"})

    def _extract_name(path: str) -> str:
        if not path:
            return ""
        return path.rstrip("/").split("/")[-1]

    def _safe_list(data):
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("resources", data.get("items", []))
        return []

    resource = None
    siblings = []
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Always fetch the full list (we need siblings anyway)
            list_resp = await client.get(f"{API_GATEWAY_URL}{meta['endpoint']}")
            all_items = _safe_list(list_resp.json())
            siblings = all_items

            # Find the selected resource
            for item in all_items:
                if item.get("name") == resource_name:
                    resource = item
                    break

            # If not found in list, try direct endpoint
            if not resource:
                detail_url = f"{API_GATEWAY_URL}{meta['endpoint']}/{resource_name}"
                resp = await client.get(detail_url)
                if resp.status_code == 200:
                    resource = resp.json()

    except Exception as e:
        logger.error(f"Error fetching {resource_type}/{resource_name}: {e}", exc_info=True)

    # Build the detail sidebar menu (Azure-style)
    detail_sidebar = [
        {"label": "Overview", "icon": "bi-speedometer2", "section": "overview", "active": True},
        {"label": "Activity log", "icon": "bi-clock-history", "section": "activity"},
        {"label": "Access control (IAM)", "icon": "bi-shield-lock", "section": "iam"},
        {"label": "Tags", "icon": "bi-tags", "section": "tags"},
        {"label": "Resource visualizer", "icon": "bi-diagram-3", "section": "visualizer"},
        {"is_divider": True},
        {"label": "Settings", "icon": "bi-gear", "section": "settings"},
        {"label": "Properties", "icon": "bi-list-ul", "section": "properties"},
    ]

    # Build essentials — key-value pairs shown at the top of the detail
    essentials = []
    if resource:
        props = resource.get("properties", {}) or {}

        if props.get("subscription_id"):
            sub_name = _extract_name(props["subscription_id"])
            essentials.append({"label": "Subscription", "value": sub_name, "link": f"/resources/subscriptions/{sub_name}"})

        if props.get("management_group_id"):
            mg_name = _extract_name(props["management_group_id"])
            essentials.append({"label": "Management Group", "value": mg_name, "link": f"/resources/managementgroups/{mg_name}"})

        if props.get("tenant_id"):
            t_name = _extract_name(props["tenant_id"])
            essentials.append({"label": "Tenant", "value": t_name, "link": f"/resources/tenants/{t_name}"})

        if props.get("parent_id"):
            p_name = _extract_name(props["parent_id"])
            essentials.append({"label": "Parent", "value": p_name, "link": f"/resources/{resource_type}/{p_name}"})

        if props.get("provisioning_state"):
            essentials.append({"label": "Status", "value": props["provisioning_state"]})

        if props.get("state"):
            essentials.append({"label": "State", "value": props["state"]})

        if resource.get("location"):
            essentials.append({"label": "Location", "value": resource["location"]})

        if props.get("display_name"):
            essentials.append({"label": "Display Name", "value": props["display_name"]})

        if props.get("created_at"):
            essentials.append({"label": "Created", "value": props["created_at"][:19].replace("T", " ")})

    return templates.TemplateResponse("resource_detail.html", {
        "request": request,
        **get_auth_context(request),
        "title": meta["title"],
        "plural": meta["plural"],
        "icon": meta["icon"],
        "color": meta["color"],
        "resource": resource,
        "resource_name": resource_name,
        "resource_type": resource_type,
        "siblings": siblings,
        "detail_sidebar": detail_sidebar,
        "essentials": essentials,
    })


# ── Service Status Detection ──────────────────────────────────────────────

def _get_service_status() -> dict:
    """Get health status of key platform services from Docker containers."""
    services = []
    
    if not DOCKER_AVAILABLE:
        return {"services": services}
    
    try:
        client = docker.from_env()
        containers = client.containers.list(all=True)
        
        # Map container names/services to friendly names
        service_map = {
            "api-gateway": "API Gateway",
            "itl-api-gateway": "API Gateway",
            "postgres": "PostgreSQL",
            "itl-postgres": "PostgreSQL",
            "redis": "Redis",
            "itl-redis": "Redis",
            "neo4j": "Neo4j",
            "itl-neo4j": "Neo4j",
            "rabbitmq": "RabbitMQ",
            "itl-rabbitmq": "RabbitMQ",
        }
        
        for c in containers:
            svc = c.labels.get("com.docker.compose.service", "")
            name = c.name
            friendly_name = service_map.get(svc) or service_map.get(name)
            
            if friendly_name:
                # Get health status
                health_data = c.attrs.get("State", {}).get("Health", {})
                health_status = health_data.get("Status", "none") if health_data else "none"
                
                if c.status == "running":
                    if health_status == "healthy":
                        status = "healthy"
                        detail = "Healthy"
                    elif health_status == "unhealthy":
                        status = "error"
                        detail = "Unhealthy"
                    elif health_status == "starting":
                        status = "warning"
                        detail = "Starting"
                    else:
                        status = "healthy"
                        detail = "Running"
                else:
                    status = "error"
                    detail = c.status.capitalize()
                
                services.append({
                    "name": friendly_name,
                    "status": status,
                    "detail": detail,
                })
        
        # Sort services for consistent display
        order = ["API Gateway", "PostgreSQL", "Redis", "Neo4j", "RabbitMQ"]
        services.sort(key=lambda s: order.index(s["name"]) if s["name"] in order else 999)
        
    except Exception as e:
        logger.error(f"Error getting service status: {e}")
    
    return {"services": services}


# ── Infrastructure Auto-Detection ─────────────────────────────────────────

def _get_infra_data() -> dict:
    """Auto-detect runtime environment and return infrastructure data."""
    if RUNNING_IN_KUBERNETES and KUBERNETES_AVAILABLE:
        return _get_k8s_infra_data()
    elif DOCKER_AVAILABLE:
        return _get_docker_infra_data()
    else:
        return {
            "error": "No infrastructure backend available",
            "runtime": "unknown",
            "containers": [],
            "networks": [],
            "volumes": [],
        }


def _get_k8s_infra_data() -> dict:
    """Get infrastructure data from Kubernetes API."""
    try:
        # Load in-cluster config (works when running as a pod)
        k8s_config.load_incluster_config()
    except Exception:
        try:
            # Fallback to kubeconfig (for local development)
            k8s_config.load_kube_config()
        except Exception as e:
            logger.error(f"Cannot load Kubernetes config: {e}")
            return {"error": str(e), "runtime": "kubernetes", "pods": [], "services": [], "deployments": []}

    v1 = k8s_client.CoreV1Api()
    apps_v1 = k8s_client.AppsV1Api()

    # Get current namespace (from mounted serviceaccount or default)
    namespace = os.environ.get("POD_NAMESPACE", "default")
    try:
        with open("/var/run/secrets/kubernetes.io/serviceaccount/namespace", "r") as f:
            namespace = f.read().strip()
    except Exception:
        pass

    pods = []
    services = []
    deployments = []

    try:
        # List pods in namespace
        pod_list = v1.list_namespaced_pod(namespace)
        for pod in pod_list.items:
            # Parse container statuses
            container_statuses = []
            for cs in (pod.status.container_statuses or []):
                container_statuses.append({
                    "name": cs.name,
                    "ready": cs.ready,
                    "restart_count": cs.restart_count,
                    "image": cs.image,
                    "state": "running" if cs.state.running else ("waiting" if cs.state.waiting else "terminated"),
                })

            # Calculate uptime
            uptime = ""
            if pod.status.start_time:
                delta = datetime.now(timezone.utc) - pod.status.start_time
                hours, remainder = divmod(int(delta.total_seconds()), 3600)
                minutes, _ = divmod(remainder, 60)
                if hours > 24:
                    uptime = f"{hours // 24}d {hours % 24}h"
                else:
                    uptime = f"{hours}h {minutes}m"

            pods.append({
                "name": pod.metadata.name,
                "namespace": pod.metadata.namespace,
                "status": pod.status.phase,
                "ip": pod.status.pod_ip or "",
                "node": pod.spec.node_name or "",
                "containers": container_statuses,
                "uptime": uptime,
                "labels": dict(pod.metadata.labels or {}),
            })

        # List services in namespace
        svc_list = v1.list_namespaced_service(namespace)
        for svc in svc_list.items:
            ports = []
            for port in (svc.spec.ports or []):
                ports.append({
                    "name": port.name or "",
                    "port": port.port,
                    "target_port": str(port.target_port),
                    "protocol": port.protocol,
                })
            services.append({
                "name": svc.metadata.name,
                "namespace": svc.metadata.namespace,
                "type": svc.spec.type,
                "cluster_ip": svc.spec.cluster_ip or "",
                "external_ip": (svc.status.load_balancer.ingress[0].ip if svc.status.load_balancer and svc.status.load_balancer.ingress else ""),
                "ports": ports,
            })

        # List deployments in namespace
        deploy_list = apps_v1.list_namespaced_deployment(namespace)
        for deploy in deploy_list.items:
            deployments.append({
                "name": deploy.metadata.name,
                "namespace": deploy.metadata.namespace,
                "replicas": deploy.spec.replicas or 0,
                "ready_replicas": deploy.status.ready_replicas or 0,
                "available_replicas": deploy.status.available_replicas or 0,
                "image": deploy.spec.template.spec.containers[0].image if deploy.spec.template.spec.containers else "",
            })

    except Exception as e:
        logger.error(f"Error fetching Kubernetes resources: {e}")
        return {"error": str(e), "runtime": "kubernetes", "pods": [], "services": [], "deployments": []}

    return {
        "runtime": "kubernetes",
        "namespace": namespace,
        "pods": pods,
        "services": services,
        "deployments": deployments,
    }


def _get_docker_infra_data() -> dict:
    """Auto-detect the Docker Compose stack by querying the Docker daemon."""
    if not DOCKER_AVAILABLE:
        return {"error": "Docker SDK not installed", "runtime": "docker", "containers": [], "networks": [], "volumes": []}

    try:
        client = docker.from_env()
    except Exception as e:
        logger.error(f"Cannot connect to Docker daemon: {e}")
        return {"error": str(e), "runtime": "docker", "containers": [], "networks": [], "volumes": []}

    # Auto-detect compose project from this container's labels
    my_hostname = os.environ.get("HOSTNAME", "")
    compose_project = None
    try:
        me = client.containers.get(my_hostname)
        compose_project = me.labels.get("com.docker.compose.project", None)
    except Exception:
        # Fallback: find project from any itl- container
        for c in client.containers.list(all=True):
            proj = c.labels.get("com.docker.compose.project")
            if proj:
                compose_project = proj
                break

    # List all containers in this compose project
    all_containers = client.containers.list(all=True)
    compose_containers = []
    other_containers = []

    for c in all_containers:
        proj = c.labels.get("com.docker.compose.project", "")
        svc = c.labels.get("com.docker.compose.service", "")

        # Parse port bindings
        ports = []
        port_bindings = c.attrs.get("NetworkSettings", {}).get("Ports", {}) or {}
        for container_port, bindings in port_bindings.items():
            if bindings:
                for b in bindings:
                    host_port = b.get("HostPort", "")
                    if host_port:
                        ports.append({"host": host_port, "container": container_port})

        # Parse health status
        health_status = "none"
        health_data = c.attrs.get("State", {}).get("Health", {})
        if health_data:
            health_status = health_data.get("Status", "none")

        # Started at
        started_at = c.attrs.get("State", {}).get("StartedAt", "")
        uptime = ""
        if started_at and c.status == "running":
            try:
                start_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                delta = datetime.now(timezone.utc) - start_dt
                hours, remainder = divmod(int(delta.total_seconds()), 3600)
                minutes, _ = divmod(remainder, 60)
                if hours > 24:
                    uptime = f"{hours // 24}d {hours % 24}h"
                elif hours > 0:
                    uptime = f"{hours}h {minutes}m"
                else:
                    uptime = f"{minutes}m"
            except Exception:
                pass

        # Safely resolve image tag — image may no longer exist after rebuild
        try:
            img = c.image
            image_name = img.tags[0] if img.tags else c.attrs.get("Config", {}).get("Image", "unknown")
        except Exception:
            image_name = c.attrs.get("Config", {}).get("Image", "unknown")

        # Get environment variables (filter sensitive)
        env_vars = []
        for env in c.attrs.get("Config", {}).get("Env", []) or []:
            key = env.split("=")[0] if "=" in env else env
            # Hide sensitive values
            if any(s in key.lower() for s in ["password", "secret", "key", "token", "credential"]):
                env_vars.append(f"{key}=***")
            else:
                env_vars.append(env)

        # Get mounts
        mounts = []
        for m in c.attrs.get("Mounts", []) or []:
            mounts.append({
                "type": m.get("Type", ""),
                "source": m.get("Source", ""),
                "destination": m.get("Destination", ""),
                "mode": m.get("Mode", "rw"),
            })

        # Get resource limits
        host_config = c.attrs.get("HostConfig", {})
        resources = {
            "memory_limit": host_config.get("Memory", 0),
            "cpu_shares": host_config.get("CpuShares", 0),
            "cpu_quota": host_config.get("CpuQuota", 0),
        }

        # Get network info
        network_settings = c.attrs.get("NetworkSettings", {})
        container_networks = []
        for net_name, net_info in (network_settings.get("Networks", {}) or {}).items():
            container_networks.append({
                "name": net_name,
                "ip": net_info.get("IPAddress", ""),
                "gateway": net_info.get("Gateway", ""),
            })

        # Get command
        cmd = c.attrs.get("Config", {}).get("Cmd", []) or []
        entrypoint = c.attrs.get("Config", {}).get("Entrypoint", []) or []

        info = {
            "id": c.short_id,
            "full_id": c.id,
            "name": c.name,
            "service": svc or c.name,
            "image": image_name,
            "status": c.status,
            "health": health_status,
            "ports": ports,
            "uptime": uptime,
            "started_at": started_at,
            "compose_project": proj,
            "labels": {k: v for k, v in c.labels.items() if k.startswith("com.docker.compose.")},
            "env_vars": env_vars,
            "mounts": mounts,
            "resources": resources,
            "networks": container_networks,
            "command": cmd,
            "entrypoint": entrypoint,
        }

        if proj == compose_project:
            compose_containers.append(info)
        else:
            other_containers.append(info)

    # Sort: compose containers by service name
    compose_containers.sort(key=lambda x: x["service"])
    other_containers.sort(key=lambda x: x["name"])

    # List compose networks
    networks = []
    for n in client.networks.list():
        proj = (n.attrs.get("Labels") or {}).get("com.docker.compose.project", "")
        if proj == compose_project:
            try:
                net_detail = client.networks.get(n.id)
                connected = list(net_detail.attrs.get("Containers", {}).values())
                connected = [c.get("Name", "") for c in connected]
            except Exception:
                connected = []
            networks.append({
                "name": n.name,
                "driver": n.attrs.get("Driver", "unknown"),
                "scope": n.attrs.get("Scope", ""),
                "containers": connected,
            })

    # List compose volumes
    volumes = []
    for v in client.volumes.list():
        proj = (v.attrs.get("Labels") or {}).get("com.docker.compose.project", "")
        if proj == compose_project:
            volumes.append({
                "name": v.name,
                "driver": v.attrs.get("Driver", "local"),
                "mountpoint": v.attrs.get("Mountpoint", ""),
            })

    # Docker host info
    host_info = {}
    try:
        info = client.info()
        host_info = {
            "docker_version": client.version().get("Version", "unknown"),
            "os": info.get("OperatingSystem", "unknown"),
            "architecture": info.get("Architecture", "unknown"),
            "cpus": info.get("NCPU", 0),
            "memory_gb": round(info.get("MemTotal", 0) / (1024 ** 3), 1),
            "containers_running": info.get("ContainersRunning", 0),
            "containers_total": info.get("Containers", 0),
            "images": info.get("Images", 0),
        }
    except Exception as e:
        logger.error(f"Error fetching Docker host info: {e}")

    return {
        "runtime": "docker",
        "compose_project": compose_project,
        "host": host_info,
        "containers": compose_containers,
        "other_containers": other_containers,
        "networks": networks,
        "volumes": volumes,
    }


@app.get("/api/infra")
async def infra_api(request: Request):
    """Return infrastructure data as JSON (requires authentication)"""
    # Require authentication for infrastructure API
    if not is_authenticated(request):
        return JSONResponse({"error": "Authentication required"}, status_code=401)
    # Portal: Infrastructure features disabled
    return JSONResponse({
        "error": "Infrastructure management is not available in the Customer Portal",
        "hint": "Use the Admin Portal for infrastructure management"
    }, status_code=403)


@app.get("/infra", response_class=HTMLResponse)
async def infra_page(request: Request):
    """Infrastructure page - disabled in Portal"""
    # Redirect to resources page with a message
    return RedirectResponse("/resources/all?message=Infrastructure+management+requires+Admin+Portal", status_code=302)


def _get_container_detail(container_id: str) -> dict:
    """Get full detail for a single container by short ID or name."""
    if not DOCKER_AVAILABLE:
        return {"error": "Docker SDK not installed"}

    try:
        client = docker.from_env()
    except Exception as e:
        return {"error": f"Cannot connect to Docker daemon: {e}"}

    try:
        c = client.containers.get(container_id)
    except Exception as e:
        return {"error": f"Container not found: {e}"}

    attrs = c.attrs
    config = attrs.get("Config", {})
    state = attrs.get("State", {})
    host_config = attrs.get("HostConfig", {})
    network_settings = attrs.get("NetworkSettings", {})

    # ── Basics ──
    svc = c.labels.get("com.docker.compose.service", "")
    proj = c.labels.get("com.docker.compose.project", "")

    try:
        img = c.image
        image_name = img.tags[0] if img.tags else config.get("Image", "unknown")
    except Exception:
        image_name = config.get("Image", "unknown")

    # ── Ports ──
    ports = []
    port_bindings = network_settings.get("Ports", {}) or {}
    for container_port, bindings in port_bindings.items():
        if bindings:
            for b in bindings:
                hp = b.get("HostPort", "")
                if hp:
                    ports.append({"host": hp, "container": container_port})

    # ── Health ──
    health_status = "none"
    health_log = []
    health_data = state.get("Health", {})
    if health_data:
        health_status = health_data.get("Status", "none")
        for entry in (health_data.get("Log") or [])[-5:]:
            health_log.append({
                "start": entry.get("Start", ""),
                "end": entry.get("End", ""),
                "exit_code": entry.get("ExitCode", -1),
                "output": (entry.get("Output") or "")[:500],
            })

    # ── Uptime ──
    started_at = state.get("StartedAt", "")
    uptime = ""
    if started_at and c.status == "running":
        try:
            start_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            delta = datetime.now(timezone.utc) - start_dt
            hours, remainder = divmod(int(delta.total_seconds()), 3600)
            minutes, _ = divmod(remainder, 60)
            if hours > 24:
                uptime = f"{hours // 24}d {hours % 24}h {minutes}m"
            elif hours > 0:
                uptime = f"{hours}h {minutes}m"
            else:
                uptime = f"{minutes}m"
        except Exception:
            pass

    # ── Environment variables (mask sensitive values) ──
    env_vars = []
    sensitive_keys = {"password", "secret", "key", "token", "credentials", "auth"}
    for e in (config.get("Env") or []):
        parts = e.split("=", 1)
        k = parts[0]
        v = parts[1] if len(parts) > 1 else ""
        masked = any(s in k.lower() for s in sensitive_keys)
        env_vars.append({"key": k, "value": "••••••••" if masked else v})

    # ── Mounts / Volumes ──
    mounts = []
    for m in (attrs.get("Mounts") or []):
        mounts.append({
            "type": m.get("Type", ""),
            "source": m.get("Source", ""),
            "destination": m.get("Destination", ""),
            "mode": m.get("Mode", ""),
            "rw": m.get("RW", True),
        })

    # ── Networks ──
    networks = []
    for net_name, net_info in (network_settings.get("Networks") or {}).items():
        networks.append({
            "name": net_name,
            "ip": net_info.get("IPAddress", ""),
            "gateway": net_info.get("Gateway", ""),
            "mac": net_info.get("MacAddress", ""),
        })

    # ── Resource limits ──
    nano_cpus = host_config.get("NanoCpus", 0)
    memory_limit = host_config.get("Memory", 0)
    resources = {
        "cpu_limit": f"{nano_cpus / 1e9:.1f} cores" if nano_cpus else "unlimited",
        "memory_limit": f"{memory_limit / (1024**2):.0f} MB" if memory_limit else "unlimited",
        "restart_policy": host_config.get("RestartPolicy", {}).get("Name", "no"),
        "restart_count": attrs.get("RestartCount", 0),
    }

    # ── Logs (last 80 lines) ──
    logs = ""
    try:
        logs = c.logs(tail=80, timestamps=True).decode("utf-8", errors="replace")
    except Exception as e:
        logs = f"Error fetching logs: {e}"

    # ── All labels ──
    labels = dict(c.labels)

    # ── Depends on (from compose label) ──
    depends_on_raw = c.labels.get("com.docker.compose.depends_on", "")
    depends_on = [d.split(":")[0] for d in depends_on_raw.split(",") if d] if depends_on_raw else []

    return {
        "id": c.short_id,
        "full_id": c.id,
        "name": c.name,
        "service": svc or c.name,
        "compose_project": proj,
        "image": image_name,
        "status": c.status,
        "health": health_status,
        "health_log": health_log,
        "ports": ports,
        "uptime": uptime,
        "started_at": started_at,
        "created": attrs.get("Created", ""),
        "platform": config.get("Image", ""),
        "command": " ".join(config.get("Cmd") or []) if config.get("Cmd") else config.get("Entrypoint", ""),
        "entrypoint": " ".join(config.get("Entrypoint") or []) if isinstance(config.get("Entrypoint"), list) else (config.get("Entrypoint") or ""),
        "working_dir": config.get("WorkingDir", ""),
        "env": env_vars,
        "mounts": mounts,
        "networks": networks,
        "resources": resources,
        "depends_on": depends_on,
        "labels": labels,
        "logs": logs,
    }


@app.get("/api/infra/{container_id}")
async def container_detail_api(container_id: str, request: Request):
    """Container detail - disabled in Portal"""
    return JSONResponse({
        "error": "Infrastructure management is not available in the Customer Portal",
        "hint": "Use the Admin Portal for infrastructure management"
    }, status_code=403)


@app.post("/api/infra/{container_id}/action")
async def container_action(container_id: str, request: Request):
    """Container actions - disabled in Portal"""
    return JSONResponse({
        "error": "Infrastructure management is not available in the Customer Portal",
        "hint": "Use the Admin Portal for infrastructure management"
    }, status_code=403)


@app.get("/api/infra/{container_id}/logs")
async def container_logs_api(container_id: str, request: Request, tail: int = 200):
    """Container logs - disabled in Portal"""
    return JSONResponse({
        "error": "Infrastructure management is not available in the Customer Portal",
        "hint": "Use the Admin Portal for infrastructure management"
    }, status_code=403)


@app.get("/infra/{container_id}", response_class=HTMLResponse)
async def container_detail_page(container_id: str, request: Request):
    """Container detail page - disabled in Portal"""
    return RedirectResponse("/resources/all?message=Infrastructure+management+requires+Admin+Portal", status_code=302)


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8090))
    uvicorn.run(app, host="0.0.0.0", port=port)
