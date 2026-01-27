from __future__ import annotations

import os
import platform
import sys
from datetime import datetime, timezone
from typing import Any, Dict

# Можно прокинуть через env из CI/CD
APP_VERSION = os.getenv("APP_VERSION", "0.0.0")
GIT_SHA = os.getenv("GIT_SHA", "") or os.getenv("COMMIT_SHA", "")
BUILD_TIME_UTC = os.getenv("BUILD_TIME_UTC", "")  # например 2026-01-27T12:00:00Z


def server_version_payload() -> Dict[str, Any]:
    return {
        "version": APP_VERSION,
        "git_sha": GIT_SHA,
        "build_time_utc": BUILD_TIME_UTC,
        "python": sys.version.split()[0],
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "ts_utc": datetime.now(timezone.utc).isoformat(),
    }
