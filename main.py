"""SMFTP desktop entry point."""
import sys
from pathlib import Path
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from smftp.ui.main_window import MainWindow
from smftp.utils.storage import configure_logging


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("SMFTP")
    app.setOrganizationName("SMFTP")
    app.setWindowIcon(QIcon(str(Path(__file__).resolve().parent / "resources" / "sf-icon.png")))
    from PySide6.QtCore import QTranslator, QLibraryInfo
    translator = QTranslator(app)
    local_translations = str(Path(__file__).resolve().parent / "resources" / "translations")
    if (translator.load("qtbase_fr", local_translations) or
        translator.load("qtbase_fr", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))):
        app.installTranslator(translator)
    from PySide6.QtWidgets import QMessageBox
    from smftp.utils.storage import acquire_instance_lock
    lock, available = acquire_instance_lock()
    if not available:
        from smftp.utils.storage import ask_running_instance_to_show
        if not ask_running_instance_to_show():
            QMessageBox.information(None, "SMFTP", "SMFTP est déjà ouvert.\n\nUtilisez la fenêtre existante : deux instances écraseraient mutuellement les profils et la file de transferts.")
        sys.exit(0)
    logging_available = configure_logging()
    from smftp.utils.storage import clean_stale_temporary
    from PySide6.QtCore import QSettings
    import tempfile
    custom_temp = QSettings().value("temp_directory", "") or ""
    clean_stale_temporary([tempfile.gettempdir()] + ([custom_temp] if custom_temp else []))
    window = MainWindow()
    if not logging_available:
        window.log(window.tr("Le journal sur disque est indisponible. Vérifiez les droits du dossier utilisateur."))
    window.show()
    from smftp.utils.storage import listen_for_other_instances
    window.instance_server = listen_for_other_instances(window)
    if window.site_problems:
        for problem in window.site_problems:
            window.log(window.tr("Erreur : ") + problem)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: QMessageBox.warning(window, window.tr("Profils"), "\n\n".join(window.site_problems)))
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
