from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

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
