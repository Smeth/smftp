"""Transfer queue and controls."""
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTreeWidget,
                               QTreeWidgetItem, QProgressBar, QTabWidget, QMessageBox,
                               QSpinBox, QLabel)
from PySide6.QtCore import Signal
from smftp.workers.transfer import TransferWorker
from smftp.ui.explorer import size_label


class Transfers(QWidget):
    activity = Signal(str)
    completed = Signal(object)
    changed = Signal()
    outcome = Signal(object, str)
    speed_changed = Signal(float)
    diagnostic = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.records = []
        self.limit = 2
        self.stopping = False
        self.paused = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        bar = QHBoxLayout()
        for label, callback in [("Pause / Reprendre", self.pause), ("Annuler", self.cancel), ("Relancer", self.retry)]:
            button = QPushButton(self.tr(label))
            button.clicked.connect(callback)
            bar.addWidget(button)
        bar.addStretch()
        bar.addWidget(QLabel(self.tr("Simultanés")))
        self.concurrency = QSpinBox()
        self.concurrency.setRange(1, 5)
        self.concurrency.setValue(2)
        self.concurrency.valueChanged.connect(self.set_limit)
        bar.addWidget(self.concurrency)
        bar.addWidget(QLabel(self.tr("Limite Ko/s")))
        self.speed = QSpinBox()
        self.speed.setRange(0, 1_000_000)
        self.speed.setSpecialValueText(self.tr("Illimitée"))
        bar.addWidget(self.speed)
        layout.addLayout(bar)
        self.tabs = QTabWidget()
        self.tables = []
        for title in ["En cours", "Échoués", "Terminés"]:
            table = QTreeWidget()
            table.setHeaderLabels(["Fichier", "Direction", "Progression", "Vitesse", "Restant", "Statut"])
            table.setColumnWidth(0, 240)
            table.setColumnWidth(2, 180)
            self.tabs.addTab(table, self.tr(title))
            self.tables.append(table)
        layout.addWidget(self.tabs)

    def set_limit(self, value):
        self.limit = value
        self.schedule()

    def add(self, task, *, restored=False, transient=False):
        if any(r["state"] in ("pending", "paused_pending", "active") and
            r["task"].connection.identity == task.connection.identity and
            r["task"].local == task.local and r["task"].remote == task.remote and
            r["task"].upload == task.upload for r in self.records):
            self.activity.emit("Statut : ce transfert est déjà dans la file.")
            return
        from pathlib import Path
        item = QTreeWidgetItem([Path(task.local).name, "↑ Envoi" if task.upload else "↓ Réception", "", "—", "—", "En attente"])
        self.tables[0].addTopLevelItem(item)
        progress = QProgressBar()
        progress.setRange(0, 100)
        self.tables[0].setItemWidget(item, 2, progress)
        worker = TransferWorker(task, self)
        record = {"task": task, "item": item, "bar": progress, "worker": worker,
                  "state": "paused_pending" if restored else "pending", "speed": 0,
                  "restored": restored, "transient": transient}
        if restored:
            item.setText(5, "Restauré — en pause")
        worker.updated.connect(lambda done, total, speed, r=record: self.update_progress(r, done, total, speed))
        worker.activity.connect(self.activity.emit)
        worker.conflict_found.connect(lambda name, r=record: self.resolve_conflict(r, name))
        worker.result.connect(lambda status, r=record: self.done(r, status))
        worker.failed.connect(lambda error, r=record: self.report_failure(r, error))
        worker.finished.connect(self.schedule)
        self.records.append(record)
        self.persist()
        self.schedule()

    def persist(self):
        if self.stopping:
            return
        try:
            from smftp.utils.queue_store import write_queue
            write_queue([r["task"] for r in self.records if not r["transient"] and r["state"] in
                         ("pending", "paused_pending", "active", "Échoué")])
        except (OSError, ValueError):
            self.activity.emit("Erreur : impossible de conserver la file de transferts sur disque.")

    def restore(self):
        if self.stopping:
            return
        from smftp.utils.queue_store import read_queue
        try:
            tasks = read_queue()
            for task in tasks:
                self.add(task, restored=True)
            if tasks:
                self.activity.emit("Statut : file restaurée en pause. Connectez le profil puis choisissez Reprendre tout.")
        except (OSError, ValueError, KeyError, TypeError):
            self.activity.emit("Erreur : la file sauvegardée est illisible. Aucune tâche n’a été lancée.")

    def authorize(self, connection):
        for record in self.records:
            if record["task"].connection.identity == connection.identity and record["restored"]:
                record["task"].connection = connection
                record["restored"] = False

    def pause_all(self):
        self.paused = True
        for r in self.records:
            if r["state"] == "active":
                r["worker"].running.clear()
                r["speed"] = 0
                r["item"].setText(5, "En pause")
        self.persist()
        self.speed_changed.emit(0)

    def resume_all(self):
        self.paused = False
        for r in self.records:
            if r["state"] == "paused_pending" and not r["restored"]:
                r["state"] = "pending"
            if r["state"] == "active":
                r["worker"].running.set()
                r["item"].setText(5, "En cours")
        self.schedule()

    def cancel_all(self):
        self.paused = True
        for r in self.records:
            if r["state"] in ("pending", "paused_pending", "active"):
                r["worker"].cancel()
                if r["state"] != "active":
                    self.done(r, "Annulé")
        self.paused = False

    def clear_finished(self):
        self.tables[1].clear()
        self.tables[2].clear()
        self.records = [r for r in self.records if r["state"] in ("pending", "paused_pending", "active") or r["worker"].isRunning()]
        self.persist()

    def report_failure(self, record, error):
        record["item"].setToolTip(5, error.summary + "\n" + error.details)
        self.diagnostic.emit(error)

    def schedule(self):
        if self.stopping or self.paused:
            return
        active = sum(r["worker"].isRunning() for r in self.records)
        for record in self.records:
            if active >= self.limit:
                break
            if record["state"] == "pending" and not record["restored"]:
                record["state"] = "active"
                record["item"].setText(5, "En cours")
                record["worker"].speed_limit = self.speed.value() * 1024
                record["worker"].start()
                active += 1

    def update_progress(self, record, done, total, speed):
        record["speed"] = speed
        self.speed_changed.emit(sum(r["speed"] for r in self.records if r["state"] == "active"))
        record["bar"].setValue(int(done / total * 100) if total else 0)
        record["item"].setText(3, f"{size_label(int(speed))}/s")
        record["item"].setText(4, f"{int(max(total - done, 0) / speed)} s" if speed else "—")

    def resolve_conflict(self, record, name):
        box = QMessageBox(self)
        box.setWindowTitle(self.tr("Fichier existant"))
        box.setText(self.tr("%s existe déjà. Quelle action souhaitez-vous ?") % name)
        choices = {}
        for label, value in [("Écraser", "overwrite"), ("Ignorer", "skip"), ("Renommer", "rename"), ("Reprendre", "resume"),
                             ("Source plus récente", "newer"), ("Taille différente", "different")]:
            choices[box.addButton(self.tr(label), QMessageBox.ButtonRole.ActionRole)] = value
        from PySide6.QtWidgets import QCheckBox
        all_files = QCheckBox(self.tr("Appliquer aux autres conflits de ce transfert"))
        box.setCheckBox(all_files)
        box.exec()
        worker = record["worker"]
        worker.choice = choices.get(box.clickedButton(), "skip")
        if all_files.isChecked():
            record["task"].conflict = worker.choice
        worker.answer.set()

    def done(self, record, status):
        record["state"] = status
        record["speed"] = 0
        self.speed_changed.emit(sum(r["speed"] for r in self.records if r["state"] == "active"))
        record["item"].setText(5, status)
        if status == "Terminé":
            record["bar"].setValue(100)
            self.completed.emit(record["task"])
        table = self.tables[0]
        index = table.indexOfTopLevelItem(record["item"])
        if index >= 0:
            item = table.takeTopLevelItem(index)
            self.tables[2 if status == "Terminé" else 1].addTopLevelItem(item)
            item.setText(2, "100 %" if status == "Terminé" else "Interrompu")
        self.changed.emit()
        self.outcome.emit(record["task"], status)
        self.persist()

    def selected_records(self):
        items = self.tables[self.tabs.currentIndex()].selectedItems()
        return [r for r in self.records if r["item"] in items]

    def pause(self):
        for record in self.selected_records():
            worker = record["worker"]
            if record["state"] == "pending":
                record["state"] = "paused_pending"
                record["item"].setText(5, "En pause")
            elif record["state"] == "paused_pending":
                record["state"] = "pending"
                record["item"].setText(5, "En attente")
            if record["state"] == "active":
                if worker.running.is_set():
                    worker.running.clear()
                    record["item"].setText(5, "En pause")
                else:
                    worker.running.set()
                    record["item"].setText(5, "En cours")
        self.schedule()

    def cancel(self):
        for record in self.selected_records():
            record["worker"].cancel()
            if record["state"] in ("pending", "paused_pending"):
                self.done(record, "Annulé")

    def retry(self):
        for record in self.selected_records():
            if not record["worker"].isRunning() and record["state"] in ("Échoué", "Annulé"):
                from dataclasses import replace
                record["state"] = "Relancé"
                self.add(replace(record["task"], resume=True), transient=record["transient"])

    def stop_all(self):
        self.persist()
        self.stopping = True
        for record in self.records:
            record["worker"].cancel()
            if record["state"] in ("pending", "paused_pending"):
                record["state"] = "Annulé"

    def active(self):
        return any(r["worker"].isRunning() for r in self.records)
