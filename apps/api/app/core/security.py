from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict[str, str] | None:
    # TODO: Verify Supabase JWT and return a normalized user payload.
    # This is intentionally a stub in Phase 0 because no protected routes are implemented yet.
    _ = credentials
    return None
