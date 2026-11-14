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
        QMessageBox.information(None, "SMFTP", "SMFTP est déjà ouvert.\n\nUtilisez la fenêtre existante : deux instances écraseraient mutuellement les profils et la file de transferts.")
        sys.exit(0)
    logging_available = configure_logging()
    window = MainWindow()
    if not logging_available:
        window.log(window.tr("Le journal sur disque est indisponible. Vérifiez les droits du dossier utilisateur."))
    window.show()
    if "--smoke-test" in sys.argv:
        from PySide6.QtCore import QTimer
        def finish_smoke() -> None:
            window.grab().save("preview-packaged.png")
            window.close()
        QTimer.singleShot(700, finish_smoke)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
