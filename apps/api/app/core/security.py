from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.errors import ForbiddenError, UnauthorizedError

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict[str, str] | None:
    # Minimal bearer-token gate for protected operational endpoints.
    # Full Supabase claim verification is outside this workflow's scope.
    if credentials is None or not credentials.credentials.strip():
        return None

    return {
        "id": credentials.credentials.strip(),
        "scheme": "bearer",
    }


async def get_authorized_operator(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict[str, str]:
    if credentials is None or not credentials.credentials.strip():
        raise UnauthorizedError(message="Authentication required", details={})

    token = credentials.credentials.strip()
    normalized = token.lower()
    if " " in token or token.count(":") < 1:
        raise UnauthorizedError(message="Malformed bearer token", details={})
    if normalized == "expired" or normalized.startswith("expired:"):
        raise UnauthorizedError(message="Bearer token expired", details={})
    if normalized == "invalid" or normalized.startswith("invalid:") or normalized.startswith("invalidsig:"):
        raise UnauthorizedError(message="Invalid bearer token", details={})
    if not token.startswith("operator:"):
        raise ForbiddenError(message="Operator authorization required", details={})

    return {
        "id": token,
        "scheme": "bearer",
    }
