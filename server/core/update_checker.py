from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional


@dataclass
class UpdateStatus:
    ok: bool
    checked_at_utc: str
    repo: str
    latest_tag: Optional[str] = None
    latest_sha: Optional[str] = None
    latest_url: Optional[str] = None
    error: Optional[str] = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _http_get_json(url: str, timeout_s: float, token: str | None) -> dict:
    headers = {
        "User-Agent": "arduino-motor-bridge/1.0",
        "Accept": "application/vnd.github+json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        data = resp.read().decode("utf-8", errors="replace")
        return json.loads(data)


def check_github_latest(repo: str, branch: str, timeout_s: float, token: str | None) -> UpdateStatus:
    """
    1) Пытаемся latest release: /releases/latest (если нет релизов — может быть 404)
    2) Фолбэк: latest commit в branch: /commits/<branch>
    """
    base = f"https://api.github.com/repos/{repo}"

    try:
        rel = _http_get_json(f"{base}/releases/latest", timeout_s, token)
        tag = rel.get("tag_name")
        html = rel.get("html_url")
        return UpdateStatus(
            ok=True,
            checked_at_utc=_utc_now(),
            repo=repo,
            latest_tag=tag,
            latest_url=html,
        )
    except urllib.error.HTTPError as e:
        # 404 -> релизов нет, идём за коммитом
        if getattr(e, "code", None) != 404:
            return UpdateStatus(ok=False, checked_at_utc=_utc_now(), repo=repo, error=f"HTTPError: {e}")
    except Exception as e:
        return UpdateStatus(ok=False, checked_at_utc=_utc_now(), repo=repo, error=str(e))

    try:
        c = _http_get_json(f"{base}/commits/{branch}", timeout_s, token)
        sha = c.get("sha")
        html = c.get("html_url")
        return UpdateStatus(
            ok=True,
            checked_at_utc=_utc_now(),
            repo=repo,
            latest_sha=sha,
            latest_url=html,
        )
    except Exception as e:
        return UpdateStatus(ok=False, checked_at_utc=_utc_now(), repo=repo, error=str(e))


def should_refresh(last_checked_ts: float | None, interval_s: int) -> bool:
    if not last_checked_ts:
        return True
    return (time.time() - last_checked_ts) >= max(60, int(interval_s))


def status_to_dict(s: UpdateStatus) -> Dict[str, Any]:
    return {
        "ok": s.ok,
        "checked_at_utc": s.checked_at_utc,
        "repo": s.repo,
        "latest_tag": s.latest_tag,
        "latest_sha": s.latest_sha,
        "latest_url": s.latest_url,
        "error": s.error,
    }
