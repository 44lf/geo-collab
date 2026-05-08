import os
import hmac

from fastapi import HTTPException, Request


async def require_local_token(request: Request) -> None:
    token = os.environ.get("GEO_LOCAL_API_TOKEN")
    if not token:
        return

    received = request.headers.get("X-Geo-Token")
    if not received:
        raise HTTPException(status_code=401, detail="Missing X-Geo-Token header")

    if not hmac.compare_digest(token, received):
        raise HTTPException(status_code=401, detail="Invalid token")
