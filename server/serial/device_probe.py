from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Sequence

from server.serial.protocol import SerialProtocolError
from server.serial.manager import SerialManager


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_ok_json(reply: str, token: str) -> dict:
    s = (reply or "").strip()
    up = s.upper()
    prefix = f"OK {token}".upper()
    if not up.startswith(prefix):
        raise ValueError(f"Expected OK {token}..., got: {reply!r}")
    tail = s[len(f"OK {token}"):].strip()
    if not tail.startswith("{"):
        raise ValueError(f"JSON missing in OK {token}: {reply!r}")
    return json.loads(tail)


def _parse_ok_text_or_json(reply: str, token: str) -> dict:
    s = (reply or "").strip()
    up = s.upper()
    prefix = f"OK {token}".upper()
    if not up.startswith(prefix):
        raise ValueError(f"Expected OK {token}..., got: {reply!r}")
    tail = s[len(f"OK {token}"):].strip()
    if tail.startswith("{"):
        return json.loads(tail)
    return {"value": tail}


async def probe_device(serial_mgr: SerialManager, timeout_s: float = 2.5) -> Dict[str, Any]:
    """
    Пытается получить:
    - CAPS (json)
    - FWVER/VERSION/VER (текст или json)
    Любая команда может отсутствовать: тогда вернём partial info.
    """
    out: Dict[str, Any] = {"ts_utc": _utc_now(), "caps": None, "fw": None, "supported_commands": None}

    # CAPS
    try:
        r = await serial_mgr.send_cmd("CAPS", expect_prefixes_upper=["OK CAPS"], max_wait_s=timeout_s, close_on_error=False)
        caps = _parse_ok_json(r, "CAPS")
        out["caps"] = caps

        # поддерживаемые команды: commands или supported_commands
        cmds = caps.get("commands") or caps.get("supported_commands")
        if isinstance(cmds, list):
            out["supported_commands"] = [str(x) for x in cmds]
    except SerialProtocolError:
        pass
    except Exception:
        pass

    # FW version (пробуем несколько команд)
    for cmd in ("FWVER", "VERSION", "VER"):
        try:
            r = await serial_mgr.send_cmd(cmd, expect_prefixes_upper=[f"OK {cmd}"], max_wait_s=timeout_s, close_on_error=False)
            out["fw"] = {"cmd": cmd, "reply": _parse_ok_text_or_json(r, cmd)}
            break
        except SerialProtocolError:
            continue
        except Exception:
            continue

    return out
