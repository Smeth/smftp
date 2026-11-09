"""SMFTP desktop entry point."""
import sys
from PySide6.QtWidgets import QApplication
from smftp.ui.main_window import MainWindow
from smftp.utils.storage import configure_logging


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("SMFTP")
    app.setOrganizationName("SMFTP")
    from PySide6.QtCore import QTranslator, QLibraryInfo
    translator = QTranslator(app)
    from pathlib import Path
    local_translations = str(Path(__file__).resolve().parent / "resources" / "translations")
    if (translator.load("qtbase_fr", local_translations) or
        translator.load("qtbase_fr", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))):
        app.installTranslator(translator)
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
