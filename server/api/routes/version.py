from __future__ import annotations

import time
from fastapi import APIRouter, Request

from server.core.build_info import server_version_payload
from server.core.update_checker import (
    check_github_latest,
    should_refresh,
    status_to_dict,
)

router = APIRouter(tags=["version"])


@router.get("/version")
async def version(request: Request):
    app = request.app
    s = app.state.settings

    payload = {"server": server_version_payload()}

    # cached update status
    st = getattr(app.state, "update_status", None)
    payload["update"] = st

    return payload


@router.post("/version/check")
async def version_check(request: Request):
    app = request.app
    s = app.state.settings

    if not s.update_check_enabled:
        return {"ok": False, "disabled": True}

    status = check_github_latest(
        repo=s.github_repo,
        branch=s.github_branch,
        timeout_s=float(s.update_check_timeout_s),
        token=s.github_token,
    )
    app.state.update_status = status_to_dict(status)
    app.state.update_last_checked_ts = time.time()
    return app.state.update_status
