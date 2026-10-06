from __future__ import annotations

from typing import Any

from langgraph_sdk import Auth

from protocol_optimization_agent.runtime.access_control import AccessPolicy

auth = Auth()


@auth.authenticate
async def authenticate(authorization: str) -> dict[str, str]:
    try:
        identity = AccessPolicy().authenticate(authorization)
    except (PermissionError, OSError, ValueError, KeyError):
        raise Auth.exceptions.HTTPException(status_code=401, detail="Unauthorized") from None
    return {"identity": identity}


@auth.on
async def deny_unhandled(ctx: Auth.types.AuthContext, value: Any) -> bool:
    return False


@auth.on.threads.create
async def create_thread(ctx: Auth.types.AuthContext, value: dict) -> None:
    value.setdefault("metadata", {})["owner"] = ctx.user.identity


@auth.on.threads
async def own_threads(ctx: Auth.types.AuthContext, value: dict) -> dict:
    if "metadata" in value:
        value["metadata"]["owner"] = ctx.user.identity
    return {"owner": ctx.user.identity}


@auth.on.assistants.read
@auth.on.assistants.search
async def read_assistants(ctx: Auth.types.AuthContext, value: dict) -> bool:
    return True
