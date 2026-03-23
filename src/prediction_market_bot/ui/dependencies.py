from __future__ import annotations

from fastapi import Request

from .actions import UiOperatorActionService
from .auth import AuthenticatedUser, UiAuthService
from .read_models import UiReadModelService


def get_read_model_service(request: Request) -> UiReadModelService:
    service = getattr(request.app.state, "read_models", None)
    if not isinstance(service, UiReadModelService):
        raise RuntimeError("UI read-model service not initialized")
    return service


def get_action_service(request: Request) -> UiOperatorActionService:
    service = getattr(request.app.state, "actions", None)
    if not isinstance(service, UiOperatorActionService):
        raise RuntimeError("UI action service not initialized")
    return service


def get_auth_service(request: Request) -> UiAuthService:
    service = getattr(request.app.state, "auth", None)
    if not isinstance(service, UiAuthService):
        raise RuntimeError("UI auth service not initialized")
    return service


def require_viewer_role(request: Request) -> AuthenticatedUser:
    auth = get_auth_service(request)
    return auth.require_role(request, allowed_roles=("viewer", "operator", "admin"))


def require_operator_role(request: Request) -> AuthenticatedUser:
    auth = get_auth_service(request)
    return auth.require_role(request, allowed_roles=("operator", "admin"))


def require_admin_role(request: Request) -> AuthenticatedUser:
    auth = get_auth_service(request)
    return auth.require_role(request, allowed_roles=("admin",))
