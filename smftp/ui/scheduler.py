"""Scheduled tasks: the runner (fires due tasks while SMFTP is open) and its windows."""
from pathlib import Path
import posixpath
import uuid
from PySide6.QtCore import QObject, QTimer, Signal, Qt, QDateTime, QTime
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QTreeWidget, QTreeWidgetItem,
                               QLabel, QLineEdit, QComboBox, QCheckBox, QSpinBox, QDateTimeEdit, QTimeEdit,
                               QDialogButtonBox, QMessageBox, QFileDialog, QWidget, QStackedWidget, QPushButton,
                               QSplitter, QAbstractItemView, QHeaderView)
from smftp.core import schedule as S
from smftp.ui.icons import icon_button, recolor, theme_of
from smftp.utils.storage import data_dir
from PySide6.QtGui import QBrush, QColor

# Readable on both themes.
STATUS_COLORS = {S.SUCCESS: "#2e9d62", S.FAILED: "#d64545", S.PARTIAL: "#d08a1c", S.INTERRUPTED: "#d08a1c"}


class Scheduler(QObject):
    """Fires due tasks and follows their transfers until each run is finished."""
    changed = Signal()
    finished_run = Signal(dict)  # history row

    CHECK_EVERY_MS = 20_000

    def __init__(self, window, path=None):
        super().__init__(window)
        self.window = window
        self.store = S.ScheduleStore(path or data_dir() / "schedule.json")
        self.runs = {}  # run id -> {"row", "task", "pending": {id(Transfer): Transfer}, "done": [...], "planner"}
        self.stopped = False
        for message in self.store.load():
            self.log("Erreur : " + message)
        moment = S.now()
        for task in self.store.tasks:
            if task["enabled"] and not task["next_run"]:
                S.plan_first_run(task, moment)
        window.transfers.outcome.connect(self.on_outcome)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(self.CHECK_EVERY_MS)
        # First check shortly after start-up, for missed runs to catch up.
        QTimer.singleShot(3000, self.tick)

    # ----- helpers -----
    def log(self, message):
        self.window.log(message)

    def profiles(self):
        from smftp.utils.profiles import identity
        result = {}
        for site in self.window.sites:
            try:
                result[identity(site)] = site
            except (ValueError, TypeError):
                continue
        return result

    def profile_label(self, key):
        site = self.profiles().get(key)
        return (site.get("name") or site.get("host")) if site else "Profil supprimé"

    def save(self):
        try:
            self.store.save()
        except OSError:
            self.log("Erreur : impossible d’enregistrer les tâches planifiées.")
        self.changed.emit()

    def running(self, task_id):
        return any(run["task"]["id"] == task_id for run in self.runs.values())

    def planning(self):
        return any(run.get("planner") is not None and run["planner"].isRunning() for run in self.runs.values())

    def enabled_count(self):
        return sum(task["enabled"] for task in self.store.tasks)

    # ----- task management (also used by the assistant) -----
    def add_task(self, data):
        task = S.normalize_task(dict(data, id=None, last_run="", next_run=""), list(self.profiles()))
        self.check_once_in_future(task)
        S.plan_first_run(task, S.now())
        self.store.tasks.append(task)
        self.save()
        return task

    def update_task(self, task_id, data):
        current = self.store.find(task_id)
        if current is None:
            raise ValueError("Tâche introuvable.")
        task = S.normalize_task(dict(current, **data, id=task_id), list(self.profiles()))
        if task["recurrence"] != current["recurrence"] or (task["enabled"] and not current["enabled"]):
            if task["recurrence"]["type"] == "once":
                self.check_once_in_future(task)
            S.plan_first_run(task, S.now())
        self.store.tasks[self.store.tasks.index(current)] = task
        self.save()
        return task

    @staticmethod
    def check_once_in_future(task):
        if task["recurrence"]["type"] == "once" and S.parse_time(task["recurrence"]["at"]) <= S.now():
            raise ValueError("La date d’exécution unique est déjà passée.")

    def remove_task(self, task_id):
        task = self.store.find(task_id)
        if task is None:
            raise ValueError("Tâche introuvable.")
        self.store.tasks.remove(task)
        self.save()

    def set_enabled(self, task_id, enabled):
        return self.update_task(task_id, {"enabled": bool(enabled)})

    def run_now(self, task_id):
        task = self.store.find(task_id)
        if task is None:
            raise ValueError("Tâche introuvable.")
        if self.running(task_id):
            raise ValueError("Cette tâche est déjà en cours.")
        task["last_run"] = S.stamp(S.now())
        self.start(task, manual=True)
        self.save()

    # ----- firing -----
    def tick(self):
        if self.stopped:
            return
        moment = S.now()
        touched = False
        for task in list(self.store.tasks):
            state = S.due(task, moment)
            if state is None:
                continue
            touched = True
            if state == "missed":
                self.add_row(task, S.SKIPPED, "Exécution manquée pendant que SMFTP était fermé (rattrapage désactivé).")
                S.advance(task, moment, ran=False)
            elif self.running(task["id"]):
                self.add_row(task, S.SKIPPED, "L’exécution précédente n’est pas terminée.")
                S.advance(task, moment, ran=False)
            else:
                S.advance(task, moment, ran=True)
                self.start(task)
        if touched:
            self.save()

    def add_row(self, task, status, detail, manual=False):
        row = {"run": uuid.uuid4().hex, "task_id": task["id"], "name": task["name"], "started": S.stamp(S.now()),
               "finished": "" if status == S.RUNNING else S.stamp(S.now()), "status": status, "detail": detail,
               "manual": manual}
        self.store.history.append(row)
        if status != S.RUNNING:
            self.log(f"Tâche planifiée « {task['name']} » : {status.lower()} — {detail}")
        return row

    def connection_for(self, task):
        from smftp.models.entries import Connection
        from smftp.utils.profiles import normalize_profile, read_credential
        site = self.profiles().get(task["profile"])
        if site is None:
            raise ValueError("Le profil de serveur de cette tâche n’existe plus.")
        profile = normalize_profile(site)
        try:
            password = read_credential(task["profile"])
        except Exception:
            password = None
        anonymous = profile["user"].lower() in ("anonymous", "ftp")
        if password is None and not anonymous and not (profile["protocol"] == "SFTP" and profile["key_file"]):
            raise ValueError("Mot de passe non mémorisé pour ce profil : modifiez le profil et cochez "
                             "« Mémoriser dans le coffre-fort système ».")
        return Connection(**profile, password=password or "")

    def start(self, task, manual=False):
        row = self.add_row(task, S.RUNNING, "Démarrage…", manual)
        run = {"row": row, "task": dict(task), "pending": {}, "done": [], "planner": None}
        self.runs[row["run"]] = run
        self.log(f"Tâche planifiée « {task['name']} » : démarrage ({S.summary(task)}).")
        try:
            connection = self.connection_for(task)
            local = Path(task["local"])
            if (task["kind"] == "upload" or task["kind"] == "sync") and not local.exists():
                raise ValueError(f"Chemin local introuvable : {local}")
            if task["kind"] == "sync" and not local.is_dir():
                raise ValueError(f"La synchronisation demande un dossier local : {local}")
            if task["kind"] == "download" and not local.parent.is_dir():
                raise ValueError(f"Dossier local introuvable : {local.parent}")
        except ValueError as error:
            self.finish(run, S.FAILED, str(error))
            return
        if task["kind"] == "sync":
            from smftp.workers.sync import SyncPlanner
            planner = SyncPlanner(connection, task["local"], task["remote"], task["upload"], self,
                                  compare_content=task["compare_content"], mirror=False)
            run["planner"] = planner
            planner.note.connect(self.log)
            planner.failed.connect(lambda message, r=run: self.finish(r, S.FAILED, message))
            planner.planned.connect(lambda plan, r=run, c=connection: self.enqueue_plan(r, c, plan))
            row["detail"] = "Comparaison des dossiers…"
            planner.start()
        else:
            from smftp.workers.transfer import Transfer
            directory = local.is_dir() if task["kind"] == "upload" else task["directory"]
            transfer = Transfer(connection, task["local"], task["remote"], task["kind"] == "upload", directory,
                                conflict=task["conflict"])
            self.enqueue(run, [transfer])
        self.changed.emit()

    def enqueue_plan(self, run, connection, plan):
        from smftp.workers.transfer import Transfer
        from smftp.workers.sync import DELETIONS
        from smftp.utils.paths import local_child
        task = run["task"]
        run["planner"] = None
        transfers = []
        for path, state, _, expected in plan:
            if state in DELETIONS:
                continue  # Scheduled runs never delete.
            destination = Path(task["local"])
            try:
                for part in path.split("/"):
                    destination = local_child(destination, part)
            except ValueError as error:
                self.log(f"Tâche planifiée « {task['name']} » : {path} ignoré ({error}).")
                continue
            # A destination changed since the comparison is skipped, never overwritten.
            transfers.append(Transfer(connection, str(destination), posixpath.join(task["remote"], path),
                                      task["upload"], conflict="skip", planned=True, expected=expected))
        if not transfers:
            self.finish(run, S.SUCCESS, "Déjà à jour : aucun fichier nouveau ou modifié.")
            return
        self.enqueue(run, transfers)

    def enqueue(self, run, transfers):
        if self.stopped:
            self.finish(run, S.INTERRUPTED, "SMFTP se ferme.")
            return
        for transfer in transfers:
            # Registered first: a transfer can end before add() returns.
            run["pending"][id(transfer)] = transfer
            if self.window.transfers.add(transfer, transient=True) is None:
                run["pending"].pop(id(transfer), None)
                run["done"].append("Ignoré")
        count = len(run["pending"])
        if not count:
            self.finish(run, S.SKIPPED, "Les transferts sont déjà dans la file.")
            return
        run["row"]["detail"] = f"{count} transfert(s) en cours…"
        self.changed.emit()

    def on_outcome(self, transfer, status):
        for run in list(self.runs.values()):
            if run["pending"].pop(id(transfer), None) is not None:
                run["done"].append(status)
                if not run["pending"]:
                    self.conclude(run)
                return

    def conclude(self, run):
        done = run["done"]
        copied = done.count("Terminé")
        failed = len(done) - copied - done.count("Ignoré")
        noun = "fichier(s)" if run["task"]["kind"] == "sync" else "transfert(s)"
        if failed == 0:
            self.finish(run, S.SUCCESS, f"{copied} {noun} traité(s).")
        elif copied:
            self.finish(run, S.PARTIAL, f"{copied} {noun} traité(s), {failed} en échec (voir l’onglet Échoués).")
        else:
            self.finish(run, S.FAILED, f"{failed} {noun} en échec (voir l’onglet Échoués et le journal).")

    def finish(self, run, status, detail):
        if self.runs.pop(run["row"]["run"], None) is None:
            return
        row = run["row"]
        row.update(status=status, detail=detail, finished=S.stamp(S.now()))
        self.log(f"Tâche planifiée « {row['name']} » : {status.lower()} — {detail}")
        if status in (S.FAILED, S.PARTIAL):
            tray = getattr(self.window, "tray", None)
            if tray is not None and tray.isVisible():
                tray.showMessage("SMFTP", f"Tâche « {row['name']} » : {status.lower()}. {detail}")
        self.save()
        self.finished_run.emit(dict(row))

    def stop(self):
        """SMFTP is closing: stop firing and interrupt comparisons."""
        self.stopped = True
        self.timer.stop()
        for run in list(self.runs.values()):
            if run.get("planner") is not None:
                run["planner"].requestInterruption()
            row = run["row"]
            row.update(status=S.INTERRUPTED, detail="SMFTP a été fermé pendant l’exécution.", finished=S.stamp(S.now()))
        self.runs.clear()
        try:
            self.store.save()
        except OSError:
            pass


class TaskEditor(QDialog):
    """Create or edit one scheduled task."""

    def __init__(self, scheduler, parent=None, task=None, defaults=None):
        super().__init__(parent)
        self.scheduler = scheduler
        self.task = task
        self.setWindowTitle(self.tr("Modifier la tâche planifiée") if task else self.tr("Nouvelle tâche planifiée"))
        self.setMinimumWidth(560)
        data = dict(defaults or {}, **(task or {}))
        form = QFormLayout(self)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        legend = QLabel(self.tr("Les champs marqués d’un * sont obligatoires."))
        legend.setObjectName("muted")
        form.addRow(legend)
        self.name = QLineEdit(data.get("name", ""))
        self.name.setPlaceholderText(self.tr("Ex. Sauvegarde du site chaque nuit"))
        self.profile = QComboBox()
        for key, site in scheduler.profiles().items():
            self.profile.addItem(site.get("name") or site.get("host"), key)
        if data.get("profile") and self.profile.findData(data["profile"]) < 0:
            self.profile.addItem(self.tr("Profil supprimé"), data["profile"])
        if data.get("profile"):
            self.profile.setCurrentIndex(self.profile.findData(data["profile"]))
        self.kind = QComboBox()
        for key, label in S.KINDS.items():
            self.kind.addItem(self.tr(label), key)
        self.kind.setCurrentIndex(max(0, self.kind.findData(data.get("kind", "sync"))))
        self.direction = QComboBox()
        self.direction.addItem(self.tr("Local → Distant"), True)
        self.direction.addItem(self.tr("Distant → Local"), False)
        self.direction.setCurrentIndex(0 if data.get("upload", True) else 1)
        self.local = QLineEdit(data.get("local", ""))
        self.local.setPlaceholderText(self.tr("Ex. C:\\Site"))
        local_row = QWidget()
        row_layout = QHBoxLayout(local_row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(4)
        row_layout.addWidget(self.local)
        self.local_picker = icon_button("folder", self.tr("Choisir…"), self.choose_local)
        row_layout.addWidget(self.local_picker)
        self.remote = QLineEdit(data.get("remote", ""))
        self.remote.setPlaceholderText(self.tr("Ex. /var/www"))
        self.directory = QCheckBox(self.tr("L’élément distant est un dossier"))
        self.directory.setChecked(bool(data.get("directory", False)))
        self.compare = QCheckBox(self.tr("Comparer le contenu par empreinte (plus lent)"))
        self.compare.setChecked(bool(data.get("compare_content", False)))
        self.conflict = QComboBox()
        for key, label in S.CONFLICTS.items():
            self.conflict.addItem(self.tr(label), key)
        self.conflict.setCurrentIndex(max(0, self.conflict.findData(data.get("conflict", "newer"))))
        # Recurrence.
        recurrence = data.get("recurrence") or {"type": "daily", "time": "23:00", "days": list(range(7))}
        self.recurrence = QComboBox()
        self.recurrence.addItem(self.tr("Chaque jour, à heure fixe"), "daily")
        self.recurrence.addItem(self.tr("À intervalle régulier"), "interval")
        self.recurrence.addItem(self.tr("Une seule fois"), "once")
        self.recurrence.setCurrentIndex(max(0, self.recurrence.findData(recurrence["type"])))
        self.pages = QStackedWidget()
        daily = QWidget()
        daily_layout = QHBoxLayout(daily)
        daily_layout.setContentsMargins(0, 0, 0, 0)
        self.time = QTimeEdit(QTime.fromString(recurrence.get("time", "23:00"), "HH:mm"))
        self.time.setDisplayFormat("HH:mm")
        daily_layout.addWidget(self.time)
        self.days = []
        for index, label in enumerate(S.DAYS):
            box = QCheckBox(label)
            box.setChecked(index in recurrence.get("days", range(7)))
            box.toggled.connect(self.validate)
            daily_layout.addWidget(box)
            self.days.append(box)
        daily_layout.addStretch()
        interval = QWidget()
        interval_layout = QHBoxLayout(interval)
        interval_layout.setContentsMargins(0, 0, 0, 0)
        self.minutes = QSpinBox()
        self.minutes.setRange(S.MIN_INTERVAL, S.MAX_INTERVAL)
        self.minutes.setSingleStep(5)
        self.minutes.setValue(int(recurrence.get("minutes", 60)))
        self.minutes.setSuffix(self.tr(" min"))
        interval_layout.addWidget(QLabel(self.tr("Toutes les")))
        interval_layout.addWidget(self.minutes)
        interval_layout.addStretch()
        once = QWidget()
        once_layout = QHBoxLayout(once)
        once_layout.setContentsMargins(0, 0, 0, 0)
        at = S.parse_time(recurrence.get("at", ""))
        self.at = QDateTimeEdit(QDateTime(at) if at else QDateTime.currentDateTime().addSecs(3600))
        self.at.setDisplayFormat("dd/MM/yyyy HH:mm")
        self.at.setCalendarPopup(True)
        once_layout.addWidget(self.at)
        once_layout.addStretch()
        for page in (daily, interval, once):
            self.pages.addWidget(page)
        self.catch_up = QCheckBox(self.tr("Rattraper une exécution manquée au lancement de SMFTP"))
        self.catch_up.setChecked(bool(data.get("catch_up", True)))
        self.enabled = QCheckBox(self.tr("Tâche active"))
        self.enabled.setChecked(bool(data.get("enabled", True)))
        self.rows = {}
        for label, widget, required in [("Nom", self.name, True), ("Profil de serveur", self.profile, True),
                                        ("Action", self.kind, True), ("Sens", self.direction, True),
                                        ("Chemin local", local_row, True), ("Chemin distant", self.remote, True),
                                        ("", self.directory, False), ("", self.compare, False),
                                        ("Si la destination existe", self.conflict, False),
                                        ("Quand", self.recurrence, True), ("", self.pages, False),
                                        ("", self.catch_up, False), ("", self.enabled, False)]:
            form.addRow(self.tr(label) + (" *" if required else ""), widget)
            self.rows[widget] = form.labelForField(widget)
        note = QLabel(self.tr("Les tâches s’exécutent tant que SMFTP est ouvert (fenêtre réduite comprise). "
                              "Elles copient uniquement : rien n’est jamais supprimé. Le mot de passe du profil "
                              "doit être mémorisé dans le coffre-fort."))
        note.setObjectName("muted")
        note.setWordWrap(True)
        form.addRow(note)
        self.warning = QLabel()
        self.warning.setObjectName("warning")
        self.warning.setWordWrap(True)
        form.addRow(self.warning)
        self.error = QLabel()
        self.error.setObjectName("error")
        self.error.setWordWrap(True)
        form.addRow(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText(self.tr("Enregistrer"))
        self.ok_button.setObjectName("primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(self.tr("Annuler"))
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        for signal in (self.name.textChanged, self.local.textChanged, self.remote.textChanged,
                       self.profile.currentIndexChanged, self.kind.currentIndexChanged,
                       self.recurrence.currentIndexChanged, self.minutes.valueChanged, self.at.dateTimeChanged):
            signal.connect(self.validate)
        self.kind.currentIndexChanged.connect(self.update_fields)
        self.recurrence.currentIndexChanged.connect(self.update_fields)
        self.update_fields()
        recolor(self, theme_of(parent))
        self.name.setFocus()

    def update_fields(self, *_):
        kind = self.kind.currentData()
        self.set_row_visible(self.direction, kind == "sync")
        self.set_row_visible(self.compare, kind == "sync")
        self.set_row_visible(self.directory, kind == "download")
        self.set_row_visible(self.conflict, kind != "sync")
        self.pages.setCurrentIndex(self.recurrence.currentIndex())
        self.catch_up.setVisible(self.recurrence.currentData() != "once")
        self.validate()

    def set_row_visible(self, widget, visible):
        widget.setVisible(visible)
        label = self.rows.get(widget)
        if label is not None:
            label.setVisible(visible)

    def choose_local(self):
        if self.kind.currentData() == "upload":
            path, _ = QFileDialog.getOpenFileName(self, self.tr("Fichier à envoyer"), self.local.text())
            if not path:
                path = QFileDialog.getExistingDirectory(self, self.tr("…ou dossier à envoyer"), self.local.text())
        else:
            path = QFileDialog.getExistingDirectory(self, self.tr("Dossier local"), self.local.text())
        if path:
            self.local.setText(str(Path(path)))

    def data(self):
        kind = self.recurrence.currentData()
        if kind == "daily":
            recurrence = {"type": "daily", "time": self.time.time().toString("HH:mm"),
                          "days": [i for i, box in enumerate(self.days) if box.isChecked()]}
        elif kind == "interval":
            recurrence = {"type": "interval", "minutes": self.minutes.value()}
        else:
            recurrence = {"type": "once", "at": self.at.dateTime().toPython().replace(second=0).isoformat()}
        return {"name": self.name.text(), "profile": self.profile.currentData() or "", "kind": self.kind.currentData(),
                "upload": bool(self.direction.currentData()), "local": self.local.text(), "remote": self.remote.text(),
                "directory": self.directory.isChecked(), "compare_content": self.compare.isChecked(),
                "conflict": self.conflict.currentData(), "recurrence": recurrence,
                "catch_up": self.catch_up.isChecked(), "enabled": self.enabled.isChecked()}

    def validate(self, *_):
        if not hasattr(self, "ok_button"):
            return
        data = self.data()
        try:
            S.normalize_task(data, list(self.scheduler.profiles()))
            if data["recurrence"]["type"] == "once" and self.at.dateTime().toPython() <= S.now():
                raise ValueError(self.tr("La date d’exécution unique est déjà passée."))
            message = ""
        except ValueError as error:
            message = str(error)
        touched = any((self.name.text(), self.local.text(), self.remote.text()))
        self.error.setText(message if touched else "")
        self.error.setVisible(bool(message) and touched)
        self.ok_button.setEnabled(not message)
        warning = ""
        site = self.scheduler.profiles().get(data["profile"])
        if site and not site.get("remember") and not (site.get("protocol") == "SFTP" and site.get("key_file")):
            warning = self.tr("⚠ Le mot de passe de ce profil n’est pas mémorisé : la tâche échouera. "
                              "Modifiez le profil et cochez « Mémoriser dans le coffre-fort système ».")
        self.warning.setText(warning)
        self.warning.setVisible(bool(warning))

    def save(self):
        try:
            if self.task:
                self.saved = self.scheduler.update_task(self.task["id"], self.data())
            else:
                self.saved = self.scheduler.add_task(self.data())
        except ValueError as error:
            self.error.setText(str(error))
            self.error.show()
            return
        self.accept()


class ScheduleDialog(QDialog):
    """List of scheduled tasks and their recent runs."""

    def __init__(self, scheduler, parent=None, defaults=None):
        super().__init__(parent)
        self.scheduler = scheduler
        self.defaults = defaults or {}
        self.setWindowTitle(self.tr("Tâches planifiées"))
        self.resize(940, 560)
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        bar.setSpacing(2)
        self.add_button = QPushButton(self.tr("＋  Nouvelle tâche"))
        self.add_button.setObjectName("primary")
        self.add_button.clicked.connect(self.add)
        bar.addWidget(self.add_button)
        bar.addSpacing(8)
        self.run_button = icon_button("play", self.tr("Exécuter maintenant"), self.run_now)
        self.toggle_button = icon_button("pause", self.tr("Désactiver"), self.toggle)
        self.edit_button = icon_button("edit", self.tr("Modifier…"), self.edit)
        self.remove_button = icon_button("trash", self.tr("Supprimer…"), self.remove)
        for button in (self.run_button, self.toggle_button, self.edit_button, self.remove_button):
            bar.addWidget(button)
        bar.addStretch()
        note = QLabel(self.tr("Exécutées tant que SMFTP est ouvert • copies uniquement, aucune suppression"))
        note.setObjectName("muted")
        bar.addWidget(note)
        layout.addLayout(bar)
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.table = QTreeWidget()
        self.table.setRootIsDecorated(False)
        self.table.setHeaderLabels([self.tr("Nom"), self.tr("Action"), self.tr("Serveur"), self.tr("Quand"),
                                    self.tr("Prochaine exécution"), self.tr("Dernier résultat")])
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.header().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column, width in enumerate((170, 240, 110, 150, 130)):
            self.table.setColumnWidth(column, width)
        self.table.itemSelectionChanged.connect(self.update_actions)
        self.table.itemDoubleClicked.connect(lambda *_: self.edit())
        splitter.addWidget(self.table)
        history_box = QWidget()
        history_layout = QVBoxLayout(history_box)
        history_layout.setContentsMargins(0, 8, 0, 0)
        title = QLabel(self.tr("HISTORIQUE DES EXÉCUTIONS"))
        title.setObjectName("eyebrow")
        history_layout.addWidget(title)
        self.history = QTreeWidget()
        self.history.setRootIsDecorated(False)
        self.history.setHeaderLabels([self.tr("Début"), self.tr("Tâche"), self.tr("Résultat"), self.tr("Détail")])
        for column, width in enumerate((140, 200, 90)):
            self.history.setColumnWidth(column, width)
        history_layout.addWidget(self.history)
        splitter.addWidget(history_box)
        splitter.setSizes([300, 220])
        layout.addWidget(splitter)
        self.empty = QLabel(self.tr("Aucune tâche planifiée. Choisissez « Nouvelle tâche » pour envoyer, télécharger "
                                    "ou synchroniser des fichiers automatiquement."))
        self.empty.setObjectName("muted")
        self.empty.setWordWrap(True)
        layout.addWidget(self.empty)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.button(QDialogButtonBox.StandardButton.Close).setText(self.tr("Fermer"))
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        scheduler.changed.connect(self.refresh)
        recolor(self, theme_of(parent))
        self.refresh()

    def selected(self):
        items = self.table.selectedItems()
        return self.scheduler.store.find(items[0].data(0, Qt.ItemDataRole.UserRole)) if items else None

    def refresh(self):
        current = self.selected()
        self.table.clear()
        latest = {}
        for row in self.scheduler.store.history:
            latest[row.get("task_id")] = row
        for task in self.scheduler.store.tasks:
            following = S.parse_time(task["next_run"])
            last = latest.get(task["id"])
            status = (S.RUNNING if self.scheduler.running(task["id"]) else
                      f"{last['status']} ({S.parse_time(last['started']):%d/%m %H:%M})" if last and S.parse_time(last.get("started", "")) else "—")
            item = QTreeWidgetItem([task["name"], S.summary(task), self.scheduler.profile_label(task["profile"]),
                                    S.describe(task),
                                    f"{following:%d/%m/%Y %H:%M}" if following and task["enabled"] else self.tr("Désactivée"),
                                    status])
            item.setData(0, Qt.ItemDataRole.UserRole, task["id"])
            item.setToolTip(1, S.summary(task))
            if last and last.get("detail"):
                item.setToolTip(5, last["detail"])
            if not task["enabled"]:
                for column in range(6):
                    item.setForeground(column, self.palette().placeholderText())
            self.table.addTopLevelItem(item)
            if current and current["id"] == task["id"]:
                item.setSelected(True)
        self.history.clear()
        for row in reversed(self.scheduler.store.history[-100:]):
            started = S.parse_time(row.get("started", ""))
            item = QTreeWidgetItem([f"{started:%d/%m/%Y %H:%M}" if started else "", row.get("name", ""),
                                    row.get("status", ""), row.get("detail", "")])
            item.setToolTip(3, row.get("detail", ""))
            color = STATUS_COLORS.get(row.get("status"))
            if color:
                item.setForeground(2, QBrush(QColor(color)))
            self.history.addTopLevelItem(item)
        self.empty.setVisible(not self.scheduler.store.tasks)
        self.update_actions()

    def update_actions(self):
        task = self.selected()
        for button in (self.run_button, self.toggle_button, self.edit_button, self.remove_button):
            button.setEnabled(task is not None)
        if task is not None:
            self.run_button.setEnabled(not self.scheduler.running(task["id"]))
            from smftp.ui.icons import set_icon
            set_icon(self.toggle_button, "pause" if task["enabled"] else "play", theme_of(self.parent()))
            tip = self.tr("Désactiver") if task["enabled"] else self.tr("Activer")
            self.toggle_button.setToolTip(tip)
            self.toggle_button.setAccessibleName(tip)

    def add(self):
        editor = TaskEditor(self.scheduler, self, defaults=self.defaults)
        editor.exec()

    def edit(self):
        task = self.selected()
        if task:
            TaskEditor(self.scheduler, self, task=task).exec()

    def remove(self):
        task = self.selected()
        if task and QMessageBox.question(self, self.tr("Supprimer la tâche"),
                self.tr("Supprimer la tâche « %s » ? Son historique est conservé.") % task["name"],
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.scheduler.remove_task(task["id"])

    def toggle(self):
        task = self.selected()
        if task:
            try:
                self.scheduler.set_enabled(task["id"], not task["enabled"])
            except ValueError as error:
                QMessageBox.warning(self, self.tr("Tâches planifiées"), str(error))

    def run_now(self):
        task = self.selected()
        if task:
            try:
                self.scheduler.run_now(task["id"])
            except ValueError as error:
                QMessageBox.warning(self, self.tr("Tâches planifiées"), str(error))
