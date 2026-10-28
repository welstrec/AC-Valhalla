"""Application identity and artwork shared by source and frozen launches."""
from __future__ import annotations

import ctypes
import logging
import sys

from .paths import resource_path

APP_USER_MODEL_ID = "ValhallaCompanion.Desktop"
ICON_SIZES = (16, 32, 48, 64, 128, 256)
LOG = logging.getLogger(__name__)


def configure_windows_identity() -> bool:
    """Give Windows a stable application group before creating any window.

    This affects only this process. It neither installs a shortcut nor modifies
    Explorer's icon cache or the user's existing taskbar pins.
    """
    if sys.platform != "win32":
        return False
    try:
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        configure = shell.SetCurrentProcessExplicitAppUserModelID
        configure.argtypes = [ctypes.c_wchar_p]
        configure.restype = ctypes.c_long
        status = configure(APP_USER_MODEL_ID)
        if status < 0:
            LOG.warning("Windows application identity failed: HRESULT 0x%08X", status & 0xFFFFFFFF)
            return False
        return True
    except OSError:
        LOG.exception("Windows application identity is unavailable")
        return False


def application_icon():
    from PySide6.QtGui import QIcon
    return QIcon(str(resource_path("assets/valhalla.ico")))


def install_window_icon(window) -> bool:
    """Set the actual top-level window icon, independent of Qt's app default."""
    icon = application_icon()
    if icon.isNull():
        LOG.error("The application icon could not be loaded")
        return False
    window.setWindowIcon(icon)
    return True
