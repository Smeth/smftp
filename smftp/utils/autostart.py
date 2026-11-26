"""Start SMFTP with Windows, minimized in the notification area.

The installed (Inno Setup / portable) build uses the per-user Run key. The
Microsoft Store build declares a startup task in its package instead: Windows
lets the user switch it on in Settings > Apps > Startup.
"""
import os
import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE = "SMFTP"


def supported() -> bool:
    """Only a packaged executable can be registered; the Store manages its own startup."""
    from smftp.workers.updates import distribution
    return os.name == "nt" and bool(getattr(sys, "frozen", False)) and distribution() != "store"


def command() -> str:
    return f'"{sys.executable}" --minimized'


def is_enabled() -> bool:
    if os.name != "nt":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE)
    except OSError:
        return False
    return value == command()


def set_enabled(enabled: bool) -> None:
    """Raise OSError when the registry refuses the change."""
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, VALUE, 0, winreg.REG_SZ, command())
        else:
            try:
                winreg.DeleteValue(key, VALUE)
            except FileNotFoundError:
                pass
