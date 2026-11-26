"""Persistent user preferences."""
from pathlib import Path
from PySide6.QtWidgets import (QDialog, QFormLayout, QLineEdit, QComboBox, QFileDialog,
                               QSpinBox, QDialogButtonBox, QCheckBox, QWidget, QHBoxLayout)
from smftp.ui.icons import icon_button, recolor, theme_of


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Préférences"))
        self.setMinimumWidth(500)
        layout = QFormLayout(self)
        self.local = QLineEdit(settings.value("local_directory", ""))
        self.temp = QLineEdit(settings.value("temp_directory", ""))
        self.temp.setPlaceholderText(self.tr("Dossier temporaire système"))
        self.count = QSpinBox()
        self.count.setRange(1, 5)
        self.count.setValue(settings.value("concurrency", 2, type=int))
        self.conflict = QComboBox()
        for label, value in [("Demander", "ask"), ("Écraser", "overwrite"), ("Ignorer", "skip"), ("Renommer", "rename"),
                             ("Écraser si plus récent", "newer"), ("Écraser si la taille diffère", "different")]:
            self.conflict.addItem(self.tr(label), value)
        self.conflict.setCurrentIndex(max(0, self.conflict.findData(settings.value("conflict", "ask"))))
        self.local_picker = icon_button("folder", self.tr("Choisir le dossier local au démarrage…"),
                                        lambda: self.choose_folder(self.local, self.tr("Dossier local au démarrage")))
        self.temp_picker = icon_button("folder", self.tr("Choisir le dossier temporaire…"),
                                       lambda: self.choose_folder(self.temp, self.tr("Dossier temporaire")))
        # Language names are written in their own language: whoever opens this finds theirs.
        from smftp.i18n import LANGUAGES
        self.language = QComboBox()
        self.language.addItem(self.tr("Automatique (langue de Windows)"), "auto")
        for code, name in LANGUAGES.items():
            self.language.addItem(name, code)
        self.language.setCurrentIndex(max(0, self.language.findData(settings.value("language", "auto"))))
        self.language.setAccessibleName("Langue / Language")
        layout.addRow("Langue / Language", self.language)
        for title, widget in [("Dossier local au démarrage", self.with_button(self.local, self.local_picker)),
                              ("Dossier temporaire", self.with_button(self.temp, self.temp_picker)),
                              ("Transferts simultanés", self.count), ("Fichier existant", self.conflict)]:
            layout.addRow(self.tr(title), widget)
        self.reconnect = QCheckBox(self.tr("Reconnexion automatique après erreur réseau (3 essais maximum)"))
        self.reconnect.setChecked(settings.value("auto_reconnect", False, type=bool))
        layout.addRow(self.reconnect)
        self.verify = QCheckBox(self.tr("Vérifier l’empreinte (SHA-256 si possible) après chaque transfert"))
        self.verify.setToolTip(self.tr("Le serveur calcule l’empreinte du fichier, comparée à la copie locale. "
                                       "Sans prise en charge par le serveur, seule la taille est vérifiée."))
        self.verify.setChecked(settings.value("verify_checksum", True, type=bool))
        layout.addRow(self.verify)
        self.mark_downloads = QCheckBox(self.tr("Marquer les fichiers téléchargés comme provenant d’Internet"))
        self.mark_downloads.setToolTip(self.tr("Windows SmartScreen et le mode protégé d’Office traitent alors ces fichiers "
                                               "comme ceux téléchargés par un navigateur."))
        self.mark_downloads.setChecked(settings.value("mark_downloads", True, type=bool))
        layout.addRow(self.mark_downloads)
        self.notifications = QCheckBox(self.tr("Notifications système de fin de transfert"))
        self.notifications.setChecked(settings.value("notifications", True, type=bool))
        layout.addRow(self.notifications)
        self.tray = QCheckBox(self.tr("Garder SMFTP dans la zone de notification à la fermeture"))
        self.tray.setToolTip(self.tr("Fermer la fenêtre ne quitte pas SMFTP : les tâches planifiées continuent. "
                                     "Clic droit sur l’icône → Quitter pour le fermer."))
        self.tray.setChecked(settings.value("tray_on_close", False, type=bool))
        layout.addRow(self.tray)
        from smftp.utils import autostart
        from smftp.workers.updates import distribution
        self.autostart = QCheckBox(self.tr("Lancer SMFTP au démarrage de Windows, réduit dans la zone de notification"))
        if autostart.supported():
            self.autostart.setChecked(autostart.is_enabled())
        else:
            self.autostart.setEnabled(False)
            self.autostart.setToolTip(
                self.tr("Version Microsoft Store : activez SMFTP dans Paramètres Windows → Applications → Démarrage.")
                if distribution() == "store" else
                self.tr("Disponible dans la version installée de SMFTP."))
        layout.addRow(self.autostart)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)
        recolor(self, theme_of(parent))

    @staticmethod
    def with_button(field, button):
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(4)
        line.addWidget(field)
        line.addWidget(button)
        return row

    def choose_folder(self, field, title):
        """Fill ``field`` from the system folder dialog; cancelling leaves it unchanged."""
        start = field.text() if field.text() and Path(field.text()).is_dir() else str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, title, start)
        if folder:
            field.setText(str(Path(folder)))

    def persist(self, settings):
        previous = settings.value("language", "auto")
        settings.setValue("language", self.language.currentData())
        if self.language.currentData() != previous:
            from PySide6.QtWidgets import QMessageBox
            # Shown in both languages: the new one is not loaded until the restart.
            QMessageBox.information(self, "SMFTP", "Redémarrez SMFTP pour changer de langue.\n"
                                                   "Restart SMFTP to change the language.")
        settings.setValue("local_directory", self.local.text())
        settings.setValue("temp_directory", self.temp.text())
        settings.setValue("concurrency", self.count.value())
        settings.setValue("conflict", self.conflict.currentData())
        settings.setValue("auto_reconnect", self.reconnect.isChecked())
        settings.setValue("notifications", self.notifications.isChecked())
        settings.setValue("tray_on_close", self.tray.isChecked())
        from smftp.utils import autostart
        if autostart.supported() and self.autostart.isChecked() != autostart.is_enabled():
            try:
                autostart.set_enabled(self.autostart.isChecked())
            except OSError:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(self, self.tr("Démarrage avec Windows"),
                                    self.tr("Windows a refusé ce réglage. Vérifiez les droits de votre compte."))
        settings.setValue("verify_checksum", self.verify.isChecked())
        settings.setValue("mark_downloads", self.mark_downloads.isChecked())
