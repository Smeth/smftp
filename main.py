"""SMFTP desktop entry point."""
import sys
from pathlib import Path
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from smftp.ui.main_window import MainWindow
from smftp.utils.storage import configure_logging


INSTANCE_MUTEX = "SMFTP-Instance"


def create_instance_mutex():
    """Create the named mutex "SMFTP-Instance" for as long as SMFTP runs (Windows only).

    Returns its handle (closed by Windows when the process ends), or None.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        return kernel32.CreateMutexW(None, False, INSTANCE_MUTEX) or None
    except (OSError, AttributeError):
        return None


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("SMFTP")
    app.setOrganizationName("SMFTP")
    app.setWindowIcon(QIcon(str(Path(__file__).resolve().parent / "resources" / "sf-icon.png")))
    # Interface language (Preferences; "auto" follows Windows): before any window is built.
    from smftp import i18n
    app.translators = i18n.install(app, "en" if "--lang=en" in sys.argv else "fr" if "--lang=fr" in sys.argv else None)
    from PySide6.QtWidgets import QMessageBox
    from smftp.utils.storage import acquire_instance_lock
    lock, available = acquire_instance_lock()
    if not available:
        from smftp.utils.storage import ask_running_instance_to_show
        if not ask_running_instance_to_show():
            QMessageBox.information(None, "SMFTP", i18n.tr("SMFTP est déjà ouvert.\n\nUtilisez la fenêtre existante : deux instances écraseraient mutuellement les profils et la file de transferts."))
        sys.exit(0)
    # Named mutex watched by the installer (AppMutex in installer/smftp.iss): an update
    # waits for SMFTP to close instead of replacing files in use.
    app.instance_mutex = create_instance_mutex()
    logging_available = configure_logging()
    from smftp.utils.storage import clean_stale_temporary
    from PySide6.QtCore import QSettings
    import tempfile
    custom_temp = QSettings().value("temp_directory", "") or ""
    clean_stale_temporary([tempfile.gettempdir()] + ([custom_temp] if custom_temp else []))
    window = MainWindow()
    if not logging_available:
        window.log(window.tr("Le journal sur disque est indisponible. Vérifiez les droits du dossier utilisateur."))
    from PySide6.QtWidgets import QSystemTrayIcon
    # Started with Windows: wait in the notification area, scheduled tasks running.
    if "--minimized" in sys.argv and window.tray.isVisible() and QSystemTrayIcon.isSystemTrayAvailable():
        window.tray.showMessage("SMFTP", window.tr("SMFTP est prêt dans la zone de notification."))
    else:
        window.show()
    from smftp.utils.storage import listen_for_other_instances
    window.instance_server = listen_for_other_instances(window)
    if window.site_problems:
        for problem in window.site_problems:
            window.log(window.tr("Erreur : ") + problem)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: QMessageBox.warning(window, window.tr("Profils"), "\n\n".join(window.site_problems)))
    if "--smoke-test" not in sys.argv:
        # Background check, at most once a day (Preferences); never in the Store build.
        from PySide6.QtCore import QTimer
        QTimer.singleShot(4000, window.auto_check_for_updates)
    if "--smoke-test" in sys.argv:
        from PySide6.QtCore import QTimer
        def finish_smoke() -> None:
            window.grab().save("preview-packaged.png")
            try:
                # The assistant imports the SDK lazily: check here that the package is bundled.
                import anthropic
                anthropic.Anthropic(api_key="smoke-test").beta.messages
                # The SSH terminal needs pyte and wcwidth in the bundle.
                import pyte
                import wcwidth
                pyte.ByteStream(pyte.Screen(10, 2)).feed(b"ok")
            except Exception as error:
                print(f"Smoke test: SDK Claude indisponible ({error!r})", file=sys.stderr)
                window.close()
                app.exit(3)
                return
            window.close()
        QTimer.singleShot(700, finish_smoke)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
