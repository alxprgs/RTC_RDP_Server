from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import psutil

from server.core.config import Settings


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read().strip()
    except Exception:
        return None


def _run_cmd(cmd: List[str]) -> Optional[str]:
    try:
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT).strip()
        return out
    except Exception:
        return None


def _is_raspberry_pi() -> bool:
    model = _read_text("/proc/device-tree/model")
    if model and "Raspberry Pi" in model:
        return True
    return os.path.exists("/sys/firmware/devicetree/base/model")


def _bytes(n: Optional[int]) -> Optional[Dict[str, Any]]:
    if n is None:
        return None
    return {"bytes": int(n)}


def _get_ip_addresses() -> Dict[str, List[str]]:
    ips: Dict[str, List[str]] = {}
    try:
        addrs = psutil.net_if_addrs()
        for iface, items in addrs.items():
            iface_ips = []
            for a in items:
                if getattr(a, "family", None) in (socket.AF_INET, socket.AF_INET6):
                    iface_ips.append(a.address)
            if iface_ips:
                ips[iface] = iface_ips
    except Exception:
        pass
    return ips


def get_system_snapshot(
    settings: Settings,
    include_disk: bool = True,
    include_network: bool = True,
    include_sensors: bool = True,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()

    system = platform.system().lower()
    is_linux = system == "linux"
    is_windows = system == "windows"
    is_rpi = is_linux and _is_raspberry_pi()

    info: Dict[str, Any] = {
        "ts_utc": now,
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "architecture": platform.architecture()[0],
            "python": sys.version.split()[0],
            "hostname": platform.node(),
            "is_windows": is_windows,
            "is_linux": is_linux,
            "is_raspberry_pi": is_rpi,
        },
    }

    if is_rpi:
        info["platform"]["rpi_model"] = (
            _read_text("/proc/device-tree/model")
            or _read_text("/sys/firmware/devicetree/base/model")
        )

    # время работы
    try:
        boot = psutil.boot_time()
        info["uptime"] = {
            "boot_time_utc": datetime.fromtimestamp(boot, tz=timezone.utc).isoformat(),
            "seconds": int(time.time() - boot),
        }
    except Exception:
        info["uptime"] = None

    # процессор
    try:
        cpu_freq = psutil.cpu_freq()
        info["cpu"] = {
            "physical_cores": psutil.cpu_count(logical=False),
            "logical_cores": psutil.cpu_count(logical=True),
            "percent_total": psutil.cpu_percent(interval=float(settings.cpu_percent_interval)),
            "percent_per_core": psutil.cpu_percent(interval=0.0, percpu=True),
            "freq_mhz": {
                "current": cpu_freq.current if cpu_freq else None,
                "min": cpu_freq.min if cpu_freq else None,
                "max": cpu_freq.max if cpu_freq else None,
            },
        }
        if is_linux:
            try:
                la = os.getloadavg()
                info["cpu"]["loadavg"] = {"1m": la[0], "5m": la[1], "15m": la[2]}
            except Exception:
                info["cpu"]["loadavg"] = None
    except Exception:
        info["cpu"] = None

    # память / своп
    try:
        vm = psutil.virtual_memory()
        sm = psutil.swap_memory()
        info["memory"] = {
            "ram": {
                "total": _bytes(vm.total),
                "available": _bytes(vm.available),
                "used": _bytes(vm.used),
                "percent": vm.percent,
            },
            "swap": {
                "total": _bytes(sm.total),
                "used": _bytes(sm.used),
                "free": _bytes(sm.free),
                "percent": sm.percent,
            },
        }
    except Exception:
        info["memory"] = None

    # диск (опционально)
    if include_disk:
        try:
            parts = []
            for p in psutil.disk_partitions(all=False):
                try:
                    usage = psutil.disk_usage(p.mountpoint)
                except Exception:
                    usage = None
                parts.append(
                    {
                        "device": p.device,
                        "mountpoint": p.mountpoint,
                        "fstype": p.fstype,
                        "opts": p.opts,
                        "usage": None
                        if usage is None
                        else {
                            "total": _bytes(usage.total),
                            "used": _bytes(usage.used),
                            "free": _bytes(usage.free),
                            "percent": usage.percent,
                        },
                    }
                )

            io = psutil.disk_io_counters()
            info["disk"] = {
                "partitions": parts,
                "io": None
                if not io
                else {
                    "read_bytes": _bytes(io.read_bytes),
                    "write_bytes": _bytes(io.write_bytes),
                    "read_count": io.read_count,
                    "write_count": io.write_count,
                },
            }
        except Exception:
            info["disk"] = None

    # сеть (опционально)
    if include_network:
        try:
            netio = psutil.net_io_counters()
            info["network"] = {
                "ips": _get_ip_addresses(),
                "io": None
                if not netio
                else {
                    "bytes_sent": _bytes(netio.bytes_sent),
                    "bytes_recv": _bytes(netio.bytes_recv),
                    "packets_sent": netio.packets_sent,
                    "packets_recv": netio.packets_recv,
                },
            }
        except Exception:
            info["network"] = None

    # датчики (опционально)
    if include_sensors:
        sensors: Dict[str, Any] = {}

        try:
            temps = psutil.sensors_temperatures(fahrenheit=False)
            sensors["temperatures"] = (
                {
                    group: [
                        {
                            "label": t.label,
                            "current": t.current,
                            "high": t.high,
                            "critical": t.critical,
                        }
                        for t in items
                    ]
                    for group, items in temps.items()
                }
                if temps
                else None
            )
        except Exception:
            sensors["temperatures"] = None

        try:
            fans = psutil.sensors_fans()
            sensors["fans"] = (
                {
                    group: [{"label": f.label, "current": f.current} for f in items]
                    for group, items in fans.items()
                }
                if fans
                else None
            )
        except Exception:
            sensors["fans"] = None

        try:
            bat = psutil.sensors_battery()
            sensors["battery"] = (
                None
                if not bat
                else {
                    "percent": bat.percent,
                    "secs_left": bat.secsleft,
                    "power_plugged": bat.power_plugged,
                }
            )
        except Exception:
            sensors["battery"] = None

        info["sensors"] = sensors

    # дополнительные данные Raspberry Pi
    if is_rpi:
        rpi: Dict[str, Any] = {}
        t_raw = _read_text("/sys/class/thermal/thermal_zone0/temp")
        rpi["cpu_temp_c"] = (int(t_raw) / 1000.0) if (t_raw and t_raw.isdigit()) else None

        vc = shutil.which("vcgencmd")
        rpi["vcgencmd_available"] = bool(vc)

        if vc:
            out = _run_cmd(["vcgencmd", "get_throttled"])
            rpi["throttled_raw"] = out

            if out and "=" in out:
                try:
                    hex_str = out.split("=")[1].strip()
                    value = int(hex_str, 16)
                    rpi["throttled_flags"] = {
                        "undervoltage_now": bool(value & (1 << 0)),
                        "throttling_now": bool(value & (1 << 1)),
                        "freq_capped_now": bool(value & (1 << 2)),
                        "temp_limit_now": bool(value & (1 << 3)),
                        "undervoltage_occurred": bool(value & (1 << 16)),
                        "throttling_occurred": bool(value & (1 << 17)),
                        "freq_capped_occurred": bool(value & (1 << 18)),
                        "temp_limit_occurred": bool(value & (1 << 19)),
                        "raw_hex": hex_str,
                        "raw_int": value,
                    }
                except Exception:
                    rpi["throttled_flags"] = None
            else:
                rpi["throttled_flags"] = None

            rpi["volts"] = {
                "core": _run_cmd(["vcgencmd", "measure_volts", "core"]),
                "sdram_c": _run_cmd(["vcgencmd", "measure_volts", "sdram_c"]),
                "sdram_i": _run_cmd(["vcgencmd", "measure_volts", "sdram_i"]),
                "sdram_p": _run_cmd(["vcgencmd", "measure_volts", "sdram_p"]),
            }
            rpi["clocks"] = {
                "arm": _run_cmd(["vcgencmd", "measure_clock", "arm"]),
                "core": _run_cmd(["vcgencmd", "measure_clock", "core"]),
            }

        info["rpi"] = rpi

    return info
