from __future__ import annotations

import logging
import os
import sys
from typing import Any, Optional, Sequence

from serial.tools import list_ports

log = logging.getLogger("motor-bridge")


def _looks_like_arduino(p: Any) -> bool:
    text = " ".join(
        [
            str(getattr(p, "description", "") or ""),
            str(getattr(p, "manufacturer", "") or ""),
            str(getattr(p, "product", "") or ""),
            str(getattr(p, "hwid", "") or ""),
        ]
    ).lower()

    keywords = [
        "arduino",
        "ch340",
        "wch",
        "cp210",
        "silicon labs",
        "ftdi",
        "usb serial",
        "usb-serial",
        "acm",
        "serial",
    ]
    return any(k in text for k in keywords)


def find_arduino_port(prefer_vid_pid: Optional[Sequence[tuple[int, int]]] = None) -> str:
    env_port = os.getenv("ARDUINO_PORT")
    if env_port:
        return env_port

    ports = list(list_ports.comports())
    if not ports:
        raise RuntimeError("Serial порты не найдены. Проверь подключение Arduino/драйверы/права доступа.")

    for p in ports:
        log.info(
            "Serial port found: device=%s desc=%s manuf=%s hwid=%s vid=%s pid=%s",
            p.device,
            p.description,
            p.manufacturer,
            p.hwid,
            p.vid,
            p.pid,
        )

    if prefer_vid_pid:
        for p in ports:
            if p.vid is None or p.pid is None:
                continue
            for (vid, pid) in prefer_vid_pid:
                if p.vid == vid and p.pid == pid:
                    return p.device

    scored = []
    for p in ports:
        score = 0
        if _looks_like_arduino(p):
            score += 10
        if sys.platform.startswith("linux"):
            if (p.device or "").startswith("/dev/ttyACM"):
                score += 5
            if (p.device or "").startswith("/dev/ttyUSB"):
                score += 3
        scored.append((score, p))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1].device


def find_uart_port(prefer_vid_pid: Optional[Sequence[tuple[int, int]]] = None) -> str:
    env_port = os.getenv("UART_PORT")
    if env_port:
        return env_port

    ports = list(list_ports.comports())
    if not ports:
        raise RuntimeError("UART порты не найдены. Проверь подключение устройств.")

    for p in ports:
        log.info(
            "UART port found: device=%s desc=%s manuf=%s hwid=%s vid=%s pid=%s",
            p.device,
            p.description,
            p.manufacturer,
            p.hwid,
            p.vid,
            p.pid,
        )

    if prefer_vid_pid:
        for p in ports:
            if p.vid is None or p.pid is None:
                continue
            for (vid, pid) in prefer_vid_pid:
                if p.vid == vid and p.pid == pid:
                    return p.device

    scored = []
    for p in ports:
        score = 0
        if _looks_like_arduino(p):
            score += 10
        if sys.platform.startswith("linux"):
            if (p.device or "").startswith("/dev/ttyACM"):
                score += 5
            if (p.device or "").startswith("/dev/ttyUSB"):
                score += 3
        scored.append((score, p))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1].device
