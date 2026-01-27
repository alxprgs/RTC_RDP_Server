from __future__ import annotations

import json
from typing import Sequence, Optional

IGNORE_LINE_PREFIXES_UPPER: tuple[str, ...] = (
    "OK READY",
    "OK PINS",
    "OK CMDS",
    "OK SERVO_PWR?",
    "OK START",
)


class SerialProtocolError(RuntimeError):
    def __init__(self, sent: str, reply: str):
        super().__init__(f"Arduino replied with error. sent={sent!r} reply={reply!r}")
        self.sent = sent
        self.reply = reply


def sanitize_outgoing_line(s: str) -> str:
    s = (s or "").strip()
    s = s.lstrip("\ufeff\uFFFD")
    s = "".join(ch for ch in s if ch == " " or 33 <= ord(ch) <= 126)
    s = " ".join(s.split())
    return s


def infer_expect_prefixes_upper(cmd_line: str) -> list[str]:
    clean = (cmd_line or "").strip()
    if not clean:
        return ["OK"]
    name = clean.split()[0].strip().upper()

    if name == "PING":
        return ["OK PONG"]
    if name == "SERVOPWR":
        return ["OK SERVO_PWR"]

    if name in ("TELEM", "TELEMETRY"):
        return ["OK TELEM"]

    if name in ("SETAENGINE", "SETBENGINE", "SETALLENGINE"):
        return [f"OK {name}"]

    # --- NEW: servo multi
    if name == "SETSERVO":
        return ["OK SETSERVO"]
    if name == "SETSERVOS":
        return ["OK SETSERVOS"]
    if name in ("SERVOCENTER", "SERVO_CENTER"):
        return ["OK SERVO_CENTER"]

    # --- NEW: safety
    if name == "ESTOP":
        return ["OK ESTOP"]

    return ["OK"]



def parse_arduino_telem_reply(reply: str) -> dict:
    s = (reply or "").strip()
    up = s.upper()

    if not up.startswith("OK TELEM"):
        raise ValueError(f"Not an Arduino telemetry reply: {reply!r}")

    json_part = s[len("OK TELEM") :].strip()
    if not json_part.startswith("{"):
        raise ValueError(f"Telemetry JSON missing: {reply!r}")

    return json.loads(json_part)
