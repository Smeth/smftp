"""Persistent user preferences."""
from PySide6.QtWidgets import (QDialog, QFormLayout, QLineEdit, QComboBox,
                               QSpinBox, QDialogButtonBox, QCheckBox)


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
        for label, value in [("Demander", "ask"), ("Ignorer", "skip"), ("Renommer", "rename"),
                             ("Source plus récente", "newer"), ("Taille différente", "different")]:
            self.conflict.addItem(self.tr(label), value)
        self.conflict.setCurrentIndex(max(0, self.conflict.findData(settings.value("conflict", "ask"))))
        for title, widget in [("Dossier local au démarrage", self.local), ("Dossier temporaire", self.temp), ("Transferts simultanés", self.count), ("Fichier existant", self.conflict)]:
            layout.addRow(self.tr(title), widget)
        self.reconnect = QCheckBox(self.tr("Reconnexion automatique après erreur réseau (3 essais maximum)"))
        self.reconnect.setChecked(settings.value("auto_reconnect", False, type=bool))
        layout.addRow(self.reconnect)
        self.verify = QCheckBox(self.tr("Vérifier l’empreinte (SHA-256 si possible) après chaque transfert"))
        self.verify.setToolTip(self.tr("Le serveur calcule l’empreinte du fichier, comparée à la copie locale. "
                                       "Sans prise en charge par le serveur, seule la taille est vérifiée."))
        self.verify.setChecked(settings.value("verify_checksum", True, type=bool))
        layout.addRow(self.verify)
        self.notifications = QCheckBox(self.tr("Notifications système de fin de transfert"))
        self.notifications.setChecked(settings.value("notifications", True, type=bool))
        layout.addRow(self.notifications)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def persist(self, settings):
        settings.setValue("local_directory", self.local.text())
        settings.setValue("temp_directory", self.temp.text())
        settings.setValue("concurrency", self.count.value())
        settings.setValue("conflict", self.conflict.currentData())
        settings.setValue("auto_reconnect", self.reconnect.isChecked())
        settings.setValue("notifications", self.notifications.isChecked())
        settings.setValue("verify_checksum", self.verify.isChecked())
