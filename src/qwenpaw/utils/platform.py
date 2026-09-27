# -*- coding: utf-8 -*-
"""Platform-specific utility helpers."""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)


def is_windows_admin() -> bool:
    """Return True if the current Windows process has admin privileges.

    On non-Windows platforms, returns True (not relevant, guard is a no-op).
    When admin detection fails, returns False (conservative: assume not admin).
    """
    if sys.platform != "win32":
        return True  # non-Windows: not relevant
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False
