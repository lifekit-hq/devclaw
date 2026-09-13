"""Host-memory probes — the mechanism behind the queue's launch admission."""

from __future__ import annotations

from typing import Optional


def _parse_mem(text: str) -> int:
    """Docker-style memory string (``2g`` / ``512m`` / ``2048k`` / bytes) →
    bytes. An unparseable value falls back to 2 GiB rather than crashing import."""
    s = str(text).strip().lower()
    units = {"k": 1 << 10, "m": 1 << 20, "g": 1 << 30, "b": 1}
    try:
        if s and s[-1] in units:
            return int(float(s[:-1]) * units[s[-1]])
        return int(s)
    except (ValueError, TypeError):
        return 2 << 30


def host_mem_available_bytes() -> Optional[int]:
    """``/proc/meminfo`` ``MemAvailable`` in bytes; ``None`` on any failure so
    dispatch fails OPEN — an unmeasurable host is never wedged."""
    try:
        with open("/proc/meminfo", "r", encoding="ascii", errors="replace") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def host_mem_total_bytes() -> Optional[int]:
    """``/proc/meminfo`` ``MemTotal`` in bytes; ``None`` on any failure — the
    stable budget a per-project sizing override is validated against."""
    try:
        with open("/proc/meminfo", "r", encoding="ascii", errors="replace") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None
