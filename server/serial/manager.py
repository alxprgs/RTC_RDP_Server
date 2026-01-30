from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional, Sequence

import serial

from server.core.context import REQUEST_ID
from server.core.logging_runtime import LoggingRuntime
from server.serial.protocol import (
    IGNORE_LINE_PREFIXES_UPPER,
    SerialProtocolError,
    sanitize_outgoing_line,
    infer_expect_prefixes_upper,
)

log = logging.getLogger("motor-bridge")
serial_log = logging.getLogger("motor-bridge.serial")


class SerialManager:
    def __init__(
        self: "SerialManager",
        port: str,
        baudrate: int = 115200,
        timeout: float = 1.0,
        logging_runtime: LoggingRuntime | None = None,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._ser: Optional[serial.Serial] = None
        self._lock = asyncio.Lock()
        self._rx_buf = bytearray()
        self.runtime = logging_runtime or LoggingRuntime("INFO", False, False, 800, 200)
        self.last_any_actuator_ts = 0.0
        self.last_motor_ts = 0.0
        self.last_servo_ts = 0.0

    def _slog(self: "SerialManager", level: str, msg: str, *args: object) -> None:
        if not self.runtime.serial_log:
            serial_log.debug(msg, *args)
            return

        if level == "info":
            serial_log.info(msg, *args)
        elif level == "warning":
            serial_log.warning(msg, *args)
        elif level == "error":
            serial_log.error(msg, *args)
        else:
            serial_log.info(msg, *args)

    def _mark_activity_line(self: "SerialManager", line: str) -> None:
        up = (line or "").strip().upper()
        if not up:
            return

        now = time.monotonic()

        # моторы
        if up.startswith("SETAENGINE") or up.startswith("SETBENGINE") or up.startswith("SETALLENGINE"):
            self.last_any_actuator_ts = now
            self.last_motor_ts = now
            return

        # сервы (универсальные + будущие)
        if up.startswith("SETSERVO") or up.startswith("SETSERVOS") or up.startswith("SERVOCENTER"):
            self.last_any_actuator_ts = now
            self.last_servo_ts = now
            return

        # подключение/отключение тоже считаем серво-активностью
        if up.startswith("SERVOATTACH") or up.startswith("SERVODETACH"):
            self.last_any_actuator_ts = now
            self.last_servo_ts = now
            return

    def connect(self: "SerialManager") -> None:
        rid = REQUEST_ID.get()
        if self._ser and self._ser.is_open:
            return

        self._slog(
            "info",
            "CONNECT port=%s baud=%s timeout=%.2fs | rid=%s",
            self.port,
            self.baudrate,
            self.timeout,
            rid,
        )
        self._ser = serial.Serial(
            self.port,
            self.baudrate,
            timeout=0.05,
            write_timeout=self.timeout,
        )

        time.sleep(2.2)

        try:
            self._ser.reset_input_buffer()
            self._ser.reset_output_buffer()
        except Exception:
            pass

        self._rx_buf.clear()
        self._slog("info", "CONNECTED port=%s | rid=%s", self.port, rid)

    def close(self: "SerialManager") -> None:
        rid = REQUEST_ID.get()
        if self._ser:
            try:
                was_open = self._ser.is_open
                self._ser.close()
                self._slog("info", "CLOSE port=%s was_open=%s | rid=%s", self.port, was_open, rid)
            except Exception as e:
                self._slog("warning", "CLOSE FAILED port=%s | rid=%s | err=%s", self.port, rid, repr(e))
        self._ser = None
        self._rx_buf.clear()

    def _readline_buffered_sync(
        self: "SerialManager",
        deadline: float,
        max_line: int = 256,
    ) -> str:
        if not self._ser:
            raise RuntimeError("Serial not connected")

        while time.monotonic() < deadline:
            nl = self._rx_buf.find(b"\n")
            if nl != -1:
                raw = self._rx_buf[:nl]
                del self._rx_buf[: nl + 1]
                raw = raw.replace(b"\r", b"").strip()
                if not raw:
                    continue
                return raw.decode("utf-8", errors="replace")

            chunk = self._ser.read(64)
            if chunk:
                self._rx_buf.extend(chunk)
                if len(self._rx_buf) > 4096:
                    self._rx_buf = self._rx_buf[-1024:]

            if len(self._rx_buf) > max_line:
                self._rx_buf.clear()
                return "ERR LineTooLong"

        return ""

    def _drain_lines_sync(
        self: "SerialManager",
        seconds: float = 1.0,
        max_lines: int = 200,
    ) -> list[str]:
        self.connect()
        rid = REQUEST_ID.get()
        end = time.monotonic() + max(0.0, seconds)
        lines: list[str] = []
        while time.monotonic() < end and len(lines) < max_lines:
            s = self._readline_buffered_sync(deadline=time.monotonic() + 0.10)
            if s:
                lines.append(s)

        if lines:
            preview = lines[:10]
            more = len(lines) - len(preview)
            self._slog(
                "info",
                "DRAIN got=%d lines (showing %d)%s | rid=%s",
                len(lines),
                len(preview),
                f" +{more} more" if more > 0 else "",
                rid,
            )
            for i, ln in enumerate(preview, 1):
                self._slog("info", "  drain[%d]: %r | rid=%s", i, ln, rid)
        return lines

    def _wait_relevant_reply_sync(
        self: "SerialManager",
        sent_line: str,
        expect_prefixes_upper: Sequence[str],
        max_wait_s: float,
        max_lines: int,
    ) -> str:
        rid = REQUEST_ID.get()
        end = time.monotonic() + max_wait_s
        seen: list[str] = []

        while time.monotonic() < end and len(seen) < max_lines:
            s = self._readline_buffered_sync(deadline=time.monotonic() + 0.40)
            if not s:
                continue

            s_up = s.upper()

            if any(s_up.startswith(p) for p in IGNORE_LINE_PREFIXES_UPPER):
                self._slog("info", "← RX(ignore) %r | rid=%s", s, rid)
                continue

            if s_up.startswith("ERR"):
                self._slog("warning", "← RX(err) %r | rid=%s", s, rid)
                raise SerialProtocolError(sent=sent_line, reply=s)

            seen.append(s)

            if any(s_up.startswith(exp) for exp in expect_prefixes_upper):
                self._slog("info", "← RX(match) %r | rid=%s", s, rid)
                return s

            self._slog("warning", "← RX(unexpected) %r (expect=%s) | rid=%s", s, list(expect_prefixes_upper), rid)

        raise TimeoutError(
            f"Timeout waiting reply. sent={sent_line!r} expect={list(expect_prefixes_upper)!r} "
            f"seen={seen[:10]}{' ...' if len(seen) > 10 else ''}"
        )

    def _send_cmd_sync(
        self: "SerialManager",
        line: str,
        expect_prefixes_upper: Optional[Sequence[str]] = None,
        max_wait_s: float = 2.5,
        pre_drain_s: float = 0.0,
        max_lines: int = 80,
        mark_activity: bool = True,
    ) -> str:
        self.connect()
        if not self._ser:
            raise RuntimeError("Serial not connected")

        clean = sanitize_outgoing_line(line)
        if not clean:
            raise ValueError("Empty command")
        if mark_activity:
            self._mark_activity_line(clean)

        payload = (clean + "\n").encode("ascii", errors="strict")

        if expect_prefixes_upper is None:
            expect_prefixes_upper = infer_expect_prefixes_upper(clean)

        rid = REQUEST_ID.get()
        preview = (
            clean
            if len(clean) <= self.runtime.serial_max_preview
            else clean[: self.runtime.serial_max_preview] + "…(truncated)"
        )

        if pre_drain_s > 0:
            try:
                self._drain_lines_sync(seconds=pre_drain_s)
            except Exception:
                pass

        t0 = time.perf_counter()
        self._slog(
            "info",
            "→ TX %r (%d bytes) expect=%s | rid=%s",
            preview,
            len(payload),
            list(expect_prefixes_upper),
            rid,
        )

        self._ser.write(payload)
        self._ser.flush()

        reply = self._wait_relevant_reply_sync(
            sent_line=clean,
            expect_prefixes_upper=expect_prefixes_upper,
            max_wait_s=max_wait_s,
            max_lines=max_lines,
        )

        dt = (time.perf_counter() - t0) * 1000.0
        self._slog(
            "info",
            "✓ CMD OK sent=%r reply=%r | rid=%s | took=%.1fms",
            preview,
            reply,
            rid,
            dt,
        )
        return reply

    async def send_cmd(
        self: "SerialManager",
        line: str,
        expect_prefixes_upper: Optional[Sequence[str]] = None,
        max_wait_s: float = 2.5,
        pre_drain_s: float = 0.0,
        close_on_error: bool = True,
        mark_activity: bool = True,
    ) -> str:
        async with self._lock:
            try:
                return await asyncio.to_thread(
                    self._send_cmd_sync,
                    line,
                    expect_prefixes_upper,
                    max_wait_s,
                    pre_drain_s,
                    80,
                    mark_activity,
                )
            except Exception:
                if close_on_error:
                    self.close()
                raise

    async def send_cmds(
        self: "SerialManager",
        lines: Sequence[str],
        max_wait_s_each: float = 2.5,
        mark_activity: bool = True,
    ) -> list[str]:
        async with self._lock:
            replies: list[str] = []
            try:
                for line in lines:
                    if mark_activity:
                        self._mark_activity_line(line)
                    exp = infer_expect_prefixes_upper(line)
                    replies.append(
                        await asyncio.to_thread(self._send_cmd_sync, line, exp, max_wait_s_each, 0.0, 80, False)
                    )
                return replies
            except Exception:
                self.close()
                raise
