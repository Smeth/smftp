"""Transfer queue and controls."""
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTreeWidget, QFrame,
                               QTreeWidgetItem, QProgressBar, QTabWidget, QMessageBox,
                               QSpinBox, QLabel, QAbstractItemView)
from PySide6.QtCore import Signal
from smftp.workers.transfer import TransferWorker, RateLimiter
from smftp.ui.explorer import size_label
from smftp.ui.icons import icon_button, set_icon
from smftp.i18n import tr


def duration_label(seconds: float) -> str:
    """Remaining time as "45 s", "3 min 05 s" or "1 h 02 min"."""
    seconds = int(round(seconds))
    if seconds < 60:
        return tr("{seconds} s").format(seconds=seconds)
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return tr("{minutes} min {seconds} s").format(minutes=minutes, seconds=f"{seconds:02d}")
    hours, minutes = divmod(minutes, 60)
    return tr("{hours} h {minutes} min").format(hours=hours, minutes=f"{minutes:02d}")


class Transfers(QWidget):
    activity = Signal(str)
    completed = Signal(object)
    changed = Signal()
    outcome = Signal(object, str)
    speed_changed = Signal(float)
    diagnostic = Signal(object)
    retried = Signal(object, object)  # failed task, new task
    discarded = Signal(object)  # list of tasks removed with their retry data

    def __init__(self, parent=None):
        super().__init__(parent)
        self.records = []
        self.queued = {}  # task key -> latest record, for the duplicate check
        self.batch_depth = 0
        self.batch_dirty = False
        self.limit = 2
        self.stopping = False
        self.paused = False
        self.limiter = RateLimiter()
        self.verify = True  # Fingerprint check after each file (preference).
        self.mark_downloads = True  # "Mark of the Web" on downloaded files (preference).
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        bar = QHBoxLayout()
        bar.setSpacing(2)
        # Selection actions, then whole-queue actions. Each is enabled only when it can act.
        self.pause_button = icon_button("pause", self.tr("Mettre en pause / reprendre la sélection"), self.pause)
        self.cancel_button = icon_button("cancel", self.tr("Annuler la sélection"), self.cancel)
        self.retry_button = icon_button("retry", self.tr("Relancer la sélection"), self.retry)
        self.pause_all_button = icon_button("pause_play", self.tr("Tout mettre en pause"), self.toggle_all)
        self.stop_all_button = icon_button("stop", self.tr("Annuler tous les transferts"), self.confirm_cancel_all)
        self.clear_button = icon_button("trash", self.tr("Effacer la liste"), self.clear_current)
        for widget in (self.pause_button, self.cancel_button, self.retry_button):
            bar.addWidget(widget)
        bar.addWidget(self._separator())
        bar.addWidget(self.pause_all_button)
        bar.addWidget(self.stop_all_button)
        bar.addWidget(self._separator())
        bar.addWidget(self.clear_button)
        bar.addStretch()
        bar.addWidget(QLabel(self.tr("Simultanés")))
        self.concurrency = QSpinBox()
        self.concurrency.setRange(1, 5)
        self.concurrency.setValue(2)
        self.concurrency.valueChanged.connect(self.set_limit)
        bar.addWidget(self.concurrency)
        bar.addWidget(QLabel(self.tr("Limite totale Ko/s")))
        self.speed = QSpinBox()
        self.speed.setRange(0, 1_000_000)
        self.speed.setSpecialValueText(self.tr("Illimitée"))
        self.speed.setToolTip(self.tr("Débit partagé par tous les transferts de la file. 0 = illimité."))
        self.speed.valueChanged.connect(lambda value: self.limiter.set_rate(value * 1024))
        bar.addWidget(self.speed)
        layout.addLayout(bar)
        self.tabs = QTabWidget()
        self.tables = []
        for title in ["En cours", "Échoués", "Terminés"]:
            table = QTreeWidget()
            table.setHeaderLabels([self.tr(label) for label in
                                   ["Fichier", "Direction", "Progression", "Vitesse", "Restant", "Statut", "Taille", "Destination"]])
            table.setColumnWidth(0, 240)
            table.setColumnWidth(5, 120)
            table.setColumnWidth(6, 90)
            table.setColumnWidth(2, 180)
            table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
            table.itemSelectionChanged.connect(self.update_actions)
            self.tabs.addTab(table, self.tr(title))
            self.tables.append(table)
        self.tab_titles = ["En cours", "Échoués", "Terminés"]
        self.tabs.currentChanged.connect(self.update_actions)
        layout.addWidget(self.tabs)
        self.update_actions()

    @staticmethod
    def _separator():
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setObjectName("toolSeparator")
        line.setFixedHeight(22)
        return line

    def update_actions(self, *_):
        """Enable each control only when it has something to act on."""
        if not hasattr(self, "clear_button"):
            return
        selected = self.selected_records()
        live = ("pending", "paused_pending", "active")
        movable = [r for r in selected if r["state"] in live]
        held = movable and all(r["state"] == "paused_pending" or
                               (r["state"] == "active" and not r["worker"].running.is_set()) for r in movable)
        set_icon(self.pause_button, "play" if held else "pause")
        tip = self.tr("Reprendre la sélection") if held else self.tr("Mettre en pause la sélection")
        self.pause_button.setToolTip(tip)
        self.pause_button.setAccessibleName(tip)
        self.pause_button.setEnabled(bool(movable))
        self.cancel_button.setEnabled(any(r["state"] in live for r in selected))
        self.retry_button.setEnabled(any(r["state"] in ("Échoué", "Annulé") and not r["worker"].isRunning()
                                         for r in selected))
        queued = any(r["state"] in live for r in self.records)
        resumable = any(r["state"] == "paused_pending" and not r["restored"] for r in self.records)
        resume = self.paused or (resumable and not any(r["state"] in ("pending", "active") for r in self.records))
        set_icon(self.pause_all_button, "play" if resume else "pause_play")
        tip = self.tr("Tout reprendre") if resume else self.tr("Tout mettre en pause")
        self.pause_all_button.setToolTip(tip)
        self.pause_all_button.setAccessibleName(tip)
        self.pause_all_button.setEnabled(queued)
        self.stop_all_button.setEnabled(queued)
        index = self.tabs.currentIndex()
        self.clear_button.setEnabled(index in (1, 2) and self.tables[index].topLevelItemCount() > 0)
        tip = {1: self.tr("Effacer les tâches échouées ou annulées…"),
               2: self.tr("Effacer les tâches terminées")}.get(index, self.tr("Effacer la liste"))
        self.clear_button.setToolTip(tip)
        self.clear_button.setAccessibleName(tip)
        for i, title in enumerate(self.tab_titles):
            count = self.tables[i].topLevelItemCount()
            self.tabs.setTabText(i, self.tr(title) + (f"  ({count})" if count else ""))

    def toggle_all(self):
        if self.pause_all_button.property("iconName") == "play":
            self.resume_all()
        else:
            self.pause_all()

    def confirm_cancel_all(self):
        if QMessageBox.question(self, self.tr("Annuler les transferts"),
                                self.tr("Annuler tous les transferts en cours et en attente ?"),
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.cancel_all()

    def clear_current(self):
        index = self.tabs.currentIndex()
        if index == 1:
            self.clear_failed()
        elif index == 2:
            self.clear_finished()

    def set_limit(self, value):
        self.limit = value
        self.schedule()

    def batch(self):
        """Add many tasks (synchronization plan, scheduled task) with one queue save at the end.

        Saving the whole queue after each of thousands of additions froze the window.
        """
        from contextlib import contextmanager

        @contextmanager
        def grouped():
            self.batch_depth += 1
            try:
                yield
            finally:
                self.batch_depth -= 1
                if not self.batch_depth and self.batch_dirty:
                    self.batch_dirty = False
                    self.persist()
        return grouped()

    @staticmethod
    def task_key(task):
        return (task.connection.identity, task.local, task.remote, task.upload)

    def add(self, task, *, restored=False, transient=False):
        known = self.queued.get(self.task_key(task))
        if known is not None and known["state"] in ("pending", "paused_pending", "active"):
            self.activity.emit(self.tr("Statut : ce transfert est déjà dans la file."))
            return None
        from pathlib import Path
        server = f"{task.connection.user}@{task.connection.host}" if task.connection.host else ""
        destination = f"{server}:{task.remote}" if task.upload else task.local
        # Column 5 shows the state; the internal states ("Terminé", "Échoué"…) stay in French.
        item = QTreeWidgetItem([Path(task.local).name, self.tr("↑ Envoi") if task.upload else self.tr("↓ Réception"),
                                "", "—", "—", self.tr("En attente"), "—", destination])
        item.setToolTip(7, (f"{task.local}  →  {server}:{task.remote}" if task.upload
                            else f"{server}:{task.remote}  →  {task.local}"))
        self.tables[0].addTopLevelItem(item)
        progress = QProgressBar()
        progress.setRange(0, 100)
        progress.setMaximumHeight(16)  # Fits the row; taller bars spilled over the next one.
        self.tables[0].setItemWidget(item, 2, progress)
        worker = TransferWorker(task, self)
        worker.limiter = self.limiter
        worker.verify = self.verify
        worker.mark_downloads = self.mark_downloads
        record = {"task": task, "item": item, "bar": progress, "worker": worker,
                  "state": "paused_pending" if restored else "pending", "speed": 0,
                  "restored": restored, "transient": transient}
        if restored:
            item.setText(5, self.tr("Restauré — en pause"))
        worker.updated.connect(lambda done, total, speed, r=record: self.update_progress(r, done, total, speed))
        worker.activity.connect(self.activity.emit)
        worker.conflict_found.connect(lambda name, r=record: self.resolve_conflict(r, name))
        worker.result.connect(lambda status, r=record: self.done(r, status))
        worker.failed.connect(lambda error, r=record: self.report_failure(r, error))
        worker.finished.connect(self.schedule)
        self.records.append(record)
        self.queued[self.task_key(task)] = record
        self.persist()
        self.schedule()
        return record

    def persist(self):
        if self.stopping:
            return
        if self.batch_depth:
            self.batch_dirty = True
            return
        try:
            from smftp.utils.queue_store import write_queue
            write_queue([r["task"] for r in self.records if not r["transient"] and r["state"] in
                         ("pending", "paused_pending", "active", "Échoué")])
        except (OSError, ValueError):
            self.activity.emit(self.tr("Erreur : impossible de conserver la file de transferts sur disque."))

    def restore(self):
        if self.stopping:
            return
        from smftp.utils.queue_store import read_queue
        try:
            tasks = read_queue()
            with self.batch():
                for task in tasks:
                    self.add(task, restored=True)
            if tasks:
                self.activity.emit(self.tr("Statut : file restaurée en pause. Connectez le profil puis choisissez Reprendre tout."))
        except (OSError, ValueError, KeyError, TypeError):
            from smftp.utils.queue_store import quarantine_queue
            kept = quarantine_queue()
            if kept:
                self.activity.emit(self.tr("Erreur : la file sauvegardée est illisible. Aucune tâche n’a été lancée. Le fichier a été mis de côté sous {path} pour ne pas être écrasé.").format(path=kept))
            else:
                self.activity.emit(self.tr("Erreur : la file sauvegardée est illisible et n’a pas pu être mise de côté. Aucune tâche n’a été lancée."))

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
                r["item"].setText(5, self.tr("En pause"))
        self.persist()
        self.speed_changed.emit(0)
        self.update_actions()

    def resume_all(self):
        self.paused = False
        for r in self.records:
            if r["state"] == "paused_pending" and not r["restored"]:
                r["state"] = "pending"
            if r["state"] == "active":
                r["worker"].running.set()
                r["item"].setText(5, self.tr("En cours"))
        self.schedule()

    def cancel_all(self):
        self.paused = True
        for r in self.records:
            if r["state"] in ("pending", "paused_pending", "active"):
                r["worker"].cancel()
                if r["state"] != "active":
                    self.done(r, "Annulé")
        self.paused = False
        self.update_actions()

    def clear_finished(self):
        """Remove successful jobs only; failed jobs keep their retry data."""
        self.tables[2].clear()
        kept = [r for r in self.records if r["state"] != "Terminé" or r["worker"].isRunning()]
        self.release([r for r in self.records if not any(r is k for k in kept)])
        self.records = kept
        self.persist()
        self.update_actions()

    def clear_failed(self):
        """Explicitly discard failed and cancelled jobs, including their retry data."""
        if QMessageBox.question(self, self.tr("Effacer les échecs"), self.tr("Effacer les tâches échouées ou annulées ? Elles ne pourront plus être relancées."), QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.tables[1].clear()
        removed = [r for r in self.records if r["state"] in ("Échoué", "Annulé", "Relancé") and not r["worker"].isRunning()]
        self.records = [r for r in self.records if not any(r is x for x in removed)]
        self.release(removed)
        self.persist()
        self.update_actions()
        self.discarded.emit([r["task"] for r in removed])

    def release(self, records):
        """Free the threads of jobs removed from the list (a long session keeps no leftovers)."""
        for record in records:
            if not record["worker"].isRunning():
                record["worker"].deleteLater()
            if self.queued.get(self.task_key(record["task"])) is record:
                del self.queued[self.task_key(record["task"])]

    def report_failure(self, record, error):
        record["item"].setToolTip(5, error.summary + "\n" + error.details)
        self.diagnostic.emit(error)

    def schedule(self):
        if self.stopping:
            return
        if self.paused:
            self.update_actions()
            return
        active = sum(r["worker"].isRunning() for r in self.records)
        for record in self.records:
            if active >= self.limit:
                break
            if record["state"] == "pending" and not record["restored"]:
                record["state"] = "active"
                record["item"].setText(5, self.tr("En cours"))
                record["worker"].start()
                active += 1
        self.update_actions()

    def update_progress(self, record, done, total, speed):
        record["speed"] = speed
        self.speed_changed.emit(sum(r["speed"] for r in self.records if r["state"] == "active"))
        record["bar"].setValue(int(done / total * 100) if total else 0)
        record["item"].setText(3, f"{size_label(int(speed))}/s")
        record["item"].setText(4, duration_label(max(total - done, 0) / speed) if speed else "—")
        if total:
            record["item"].setText(6, size_label(int(total)))

    def resolve_conflict(self, record, name):
        box = QMessageBox(self)
        box.setWindowTitle(self.tr("Fichier existant"))
        box.setText(self.tr("« %s » existe déjà à la destination. Que faire ?") % name)
        details = getattr(record["worker"], "conflict_details", {}) or {}
        if details:
            from datetime import datetime

            def describe(meta):
                size, modified = meta
                when = (datetime.fromtimestamp(modified).strftime(self.tr("%d/%m/%Y %H:%M")) if modified
                        else self.tr("date inconnue"))
                return f"{size_label(int(size))} — {when}"
            upload = details.get("upload")
            source_place, target_place = (self.tr("sur ce PC"), self.tr("sur le serveur")) if upload else \
                (self.tr("sur le serveur"), self.tr("sur ce PC"))
            newer = details["source"][1] > details["target"][1] + 2
            box.setInformativeText(
                self.tr("Fichier à copier (%1) : %2").replace("%1", source_place).replace("%2", describe(details["source"]))
                + "\n" + self.tr("Fichier existant (%1) : %2").replace("%1", target_place).replace("%2", describe(details["target"]))
                + "\n\n" + (self.tr("Le fichier à copier est le plus récent.") if newer
                             else self.tr("Le fichier existant n’est pas plus ancien.")))
            if details.get("destination"):
                box.setInformativeText(box.informativeText() + "\n" + self.tr("Destination : {path}").format(
                    path=details["destination"]))
        choices = {}
        for label, value, tip in [
                ("Écraser", "overwrite", "Remplacer le fichier existant."),
                ("Ignorer", "skip", "Garder le fichier existant et passer au suivant."),
                ("Renommer", "rename", "Copier sous un autre nom ; le fichier existant est gardé."),
                ("Reprendre", "resume", "Continuer une copie interrompue de ce même fichier.")]:
            button = box.addButton(self.tr(label), QMessageBox.ButtonRole.ActionRole)
            button.setToolTip(self.tr(tip))
            choices[button] = value
        # The conditional choices share one button with a menu: the box stays narrow
        # (seven buttons did not fit at 150 % in English).
        from PySide6.QtWidgets import QCheckBox, QMenu, QPushButton
        picked = []
        conditional = QPushButton(self.tr("Écraser si…"))
        conditional.setToolTip(self.tr("Remplacer seulement sous condition : fichier plus récent ou taille différente."))
        menu = QMenu(conditional)
        for label, value, tip in [
                ("Si le fichier à copier est plus récent", "newer", "Remplacer seulement si le fichier à copier est plus récent."),
                ("Si la taille diffère", "different", "Remplacer seulement si les tailles sont différentes.")]:
            action = menu.addAction(self.tr(label))
            action.setToolTip(self.tr(tip))
            action.setData(value)
            action.triggered.connect(lambda checked=False, v=value: (picked.append(v), box.done(0)))
        menu.setToolTipsVisible(True)
        conditional.setMenu(menu)
        box.addButton(conditional, QMessageBox.ButtonRole.ActionRole)
        self.conflict_menu = menu  # Reached by the tests.
        all_files = QCheckBox(self.tr("Appliquer aux autres conflits de ce transfert"))
        box.setCheckBox(all_files)
        box.exec()
        worker = record["worker"]
        worker.choice = picked[-1] if picked else choices.get(box.clickedButton(), "skip")
        # A single-file task has only this conflict: keep the answer so that a
        # resumed or restored copy does not ask it again.
        if all_files.isChecked() or not record["task"].directory:
            record["task"].conflict = worker.choice
            self.persist()
        worker.answer.set()

    def done(self, record, status):
        record["state"] = status
        record["speed"] = 0
        self.speed_changed.emit(sum(r["speed"] for r in self.records if r["state"] == "active"))
        record["item"].setText(5, self.tr(status))  # status stays French: compared here and elsewhere
        if status == "Terminé":
            record["bar"].setValue(100)
            self.completed.emit(record["task"])
        table = self.tables[0]
        index = table.indexOfTopLevelItem(record["item"])
        if index >= 0:
            item = table.takeTopLevelItem(index)
            self.tables[2 if status == "Terminé" else 1].addTopLevelItem(item)
            item.setText(2, self.tr("100 %") if status == "Terminé" else self.tr("Interrompu"))
        self.changed.emit()
        self.outcome.emit(record["task"], status)
        self.persist()
        self.update_actions()

    def selected_records(self):
        items = self.tables[self.tabs.currentIndex()].selectedItems()
        return [r for r in self.records if r["item"] in items]

    def pause(self):
        for record in self.selected_records():
            worker = record["worker"]
            if record["state"] == "pending":
                record["state"] = "paused_pending"
                record["item"].setText(5, self.tr("En pause"))
            elif record["state"] == "paused_pending":
                record["state"] = "pending"
                record["item"].setText(5, self.tr("En attente"))
            if record["state"] == "active":
                if worker.running.is_set():
                    worker.running.clear()
                    record["item"].setText(5, self.tr("En pause"))
                else:
                    worker.running.set()
                    record["item"].setText(5, self.tr("En cours"))
        self.schedule()

    def cancel(self):
        for record in self.selected_records():
            record["worker"].cancel()
            if record["state"] in ("pending", "paused_pending"):
                self.done(record, "Annulé")
        self.update_actions()

    def retry(self):
        for record in self.selected_records():
            if not record["worker"].isRunning() and record["state"] in ("Échoué", "Annulé"):
                from dataclasses import replace
                task = replace(record["task"], resume=True)
                if self.add(task, transient=record["transient"]) is not None:
                    record["state"] = "Relancé"
                    # Lets the window move follow-up actions (edit, copy, drag) to the new task.
                    self.retried.emit(record["task"], task)
        self.update_actions()

    def stop_all(self):
        self.persist()
        self.stopping = True
        for record in self.records:
            record["worker"].cancel()
            if record["state"] in ("pending", "paused_pending"):
                record["state"] = "Annulé"

    def pending_count(self):
        """Jobs running or waiting; they use their own connections, not the browsing session."""
        return sum(r["state"] in ("pending", "active") for r in self.records)

    def active(self):
        return any(r["worker"].isRunning() for r in self.records)
