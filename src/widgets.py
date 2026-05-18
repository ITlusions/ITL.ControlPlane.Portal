"""
ITL Control Plane Portal - Widget System

Customer-facing widget system for the self-service portal.
Admin-only widgets (infrastructure, service status) are removed for security.
"""
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Callable
from enum import Enum


class WidgetRole(str, Enum):
    """Roles that can view widgets"""
    ANONYMOUS = "anonymous"           # Anyone, no login required
    AUTHENTICATED = "authenticated"   # Any logged-in user
    TENANT_ADMIN = "tenant_admin"     # Tenant-level administration
    SUBSCRIPTION_ADMIN = "subscription_admin"
    READER = "reader"
    # NOTE: No PLATFORM_ADMIN role in Portal - that's Admin Portal only


class WidgetSize(str, Enum):
    """Widget size classes"""
    SMALL = "col-md-3"    # 25% width
    MEDIUM = "col-md-4"   # 33% width
    LARGE = "col-md-6"    # 50% width
    FULL = "col-12"       # Full width


@dataclass
class Widget:
    """Dashboard widget definition"""
    id: str
    title: str
    icon: str  # Bootstrap icon class
    template: str  # Template snippet name (e.g., "widgets/stats.html")
    
    # Visibility
    roles: List[WidgetRole] = field(default_factory=lambda: [WidgetRole.AUTHENTICATED])
    order: int = 100  # Lower = higher position
    
    # Layout
    size: WidgetSize = WidgetSize.MEDIUM
    
    # Data
    data_key: Optional[str] = None  # Key in context for widget data
    refresh_interval: int = 0  # Auto-refresh in seconds (0 = no refresh)
    
    # Features
    collapsible: bool = True
    closable: bool = False  # User can hide the widget
    
    def can_view(self, user_roles: List[str]) -> bool:
        """Check if user can view this widget based on roles"""
        # Anonymous widgets visible to all
        if WidgetRole.ANONYMOUS in self.roles:
            return True
        
        # Authenticated check for logged-in users
        if WidgetRole.AUTHENTICATED in self.roles and user_roles:
            return True
        
        # Check specific roles
        for role in self.roles:
            if role.value in user_roles:
                return True
        
        return False


# ═══════════════════════════════════════════════════════════════════════════
# Widget Registry - Portal Edition (No Admin Widgets)
# ═══════════════════════════════════════════════════════════════════════════

DASHBOARD_WIDGETS: List[Widget] = [
    # Platform Stats - visible to all
    Widget(
        id="platform_stats",
        title="Platform Overview",
        icon="bi-bar-chart",
        template="widgets/stats.html",
        roles=[WidgetRole.ANONYMOUS],
        order=10,
        size=WidgetSize.FULL,
        data_key="stats",
    ),
    
    # NOTE: service_status and container_stack widgets removed - Admin Portal only
    
    # Quick Links - authenticated users
    Widget(
        id="quick_links",
        title="Quick Links",
        icon="bi-collection",
        template="widgets/quick_links.html",
        roles=[WidgetRole.ANONYMOUS],
        order=30,
        size=WidgetSize.LARGE,
    ),
    
    # NOTE: infra_links widget removed - Admin Portal only
    
    # Recent Activity - authenticated users
    Widget(
        id="recent_activity",
        title="Recent Activity",
        icon="bi-clock-history",
        template="widgets/recent_activity.html",
        roles=[WidgetRole.AUTHENTICATED],
        order=50,
        size=WidgetSize.FULL,
        data_key="recent_activity",
        refresh_interval=60,
    ),
]


def get_widgets_for_user(user_roles: List[str]) -> List[Widget]:
    """Get list of widgets user can view, sorted by order"""
    visible = [w for w in DASHBOARD_WIDGETS if w.can_view(user_roles)]
    return sorted(visible, key=lambda w: w.order)


def get_user_roles(user: Optional[Any]) -> List[str]:
    """
    Extract roles from user session.
    Portal users get tenant/subscription admin roles, never platform_admin.
    """
    if not user:
        return []
    
    roles = ["authenticated"]
    
    # Extract tenant/subscription roles from Keycloak token
    # TODO: Implement proper role extraction from access_token claims
    email = getattr(user, "email", "") or ""
    if email:
        # Portal users can have tenant_admin or subscription_admin
        # Never platform_admin - that requires Admin Portal
        roles.append("tenant_admin")
    
    return roles
