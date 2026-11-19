"""Assistant tab: chat with Claude, which acts through confirmed SMFTP tools."""
from datetime import datetime
import html
import json
import os
from pathlib import Path
from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTextBrowser, QLineEdit, QPushButton, QLabel,
                               QMessageBox, QInputDialog)
from smftp.core import assistant as A
from smftp.core import schedule as S
from smftp.ui.icons import icon_button

KEY_SERVICE, KEY_NAME = "SMFTP", "assistant:anthropic-api-key"
DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
TOOL_LABELS = {
    "list_profiles": "consulte les profils de serveurs", "list_local_directory": "liste un dossier local",
    "list_remote_directory": "liste un dossier distant", "list_scheduled_tasks": "consulte les tâches planifiées",
    "create_scheduled_task": "propose une nouvelle tâche planifiée", "update_scheduled_task": "propose de modifier une tâche",
    "delete_scheduled_task": "propose de supprimer une tâche", "run_scheduled_task_now": "propose de lancer une tâche",
    "queue_transfer": "propose un transfert", "get_transfer_status": "consulte la file de transferts",
    "read_journal": "lit le journal",
}
SUGGESTIONS = ["Synchronise mon site chaque soir à 23 h", "Pourquoi mes derniers transferts ont échoué ?",
               "Quelles tâches sont prévues cette semaine ?"]
LIST_LIMIT = 200


def read_api_key():
    try:
        import keyring
        return keyring.get_password(KEY_SERVICE, KEY_NAME)
    except Exception:
        return None


def store_api_key(value):
    import keyring
    if value:
        keyring.set_password(KEY_SERVICE, KEY_NAME, value)
    elif keyring.get_password(KEY_SERVICE, KEY_NAME) is not None:
        keyring.delete_password(KEY_SERVICE, KEY_NAME)


def describe_entry(name, is_dir, size, modified):
    if is_dir:
        return f"{name}/"
    date = datetime.fromtimestamp(modified).strftime("%Y-%m-%d %H:%M") if modified else "?"
    return f"{name}  ({size} o, modifié {date})"


class Toolbox(QObject):
    """Runs the assistant's tools. GUI-bound tools run on the GUI thread (blocking the worker);
    listings run on the calling thread so the window never freezes on the network."""
    gui_request = Signal(str, object, object)  # name, input, result holder

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.gui_request.connect(self.run_on_gui, Qt.ConnectionType.BlockingQueuedConnection)

    # ----- dispatch -----
    def execute(self, name, data):
        if name not in {tool["name"] for tool in A.TOOLS}:
            return f"Outil inconnu : {name}", True
        if name in ("list_local_directory", "list_remote_directory"):
            return self.run(name, data)
        if QThread.currentThread() is self.thread():
            return self.run(name, data)
        holder = {}
        self.gui_request.emit(name, data, holder)
        return holder.get("result", ("Outil interrompu.", True))

    @Slot(str, object, object)
    def run_on_gui(self, name, data, holder):
        holder["result"] = self.run(name, data)

    def run(self, name, data):
        try:
            return getattr(self, "tool_" + name)(**data), False
        except ValueError as error:
            return str(error), True
        except TypeError as error:
            return f"Paramètres invalides : {error}", True

    # ----- helpers -----
    @property
    def scheduler(self):
        return self.window.scheduler

    def profile_key(self, name):
        wanted = (name or "").strip().casefold()
        for key, site in self.scheduler.profiles().items():
            if wanted in ((site.get("name") or "").casefold(), site.get("host", "").casefold(), key.casefold()):
                return key
        names = ", ".join(site.get("name") or site.get("host") for site in self.scheduler.profiles().values())
        raise ValueError(f"Profil « {name} » introuvable. Profils existants : {names or 'aucun'}.")

    def confirm(self, title, text):
        """Nothing changes without the user's yes; "No" is the default button."""
        self.window.activity_tabs.setCurrentWidget(self.window.assistant_panel)
        return QMessageBox.question(
            self.window, self.window.tr("L’assistant propose une action"), f"{title}\n\n{text}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def task_text(self, task):
        lines = [S.summary(task), S.describe(task), f"Serveur : {self.scheduler.profile_label(task['profile'])}"]
        if task["kind"] != "sync":
            lines.append(f"Si la destination existe : {S.CONFLICTS[task['conflict']].lower()}")
        if not task["enabled"]:
            lines.append("Tâche désactivée")
        return "\n".join(lines)

    # ----- read-only tools -----
    def tool_list_profiles(self):
        rows = []
        for key, site in self.scheduler.profiles().items():
            rows.append({"nom": site.get("name") or site["host"], "protocole": site["protocol"], "hote": site["host"],
                         "port": site["port"], "utilisateur": site["user"],
                         "mot_de_passe_memorise": bool(site.get("remember")),
                         "cle_ssh": bool(site.get("key_file")), "groupe": site.get("group", ""),
                         "dossier_distant_par_defaut": site.get("remote_directory", "")})
        return json.dumps(rows, ensure_ascii=False) if rows else "Aucun profil enregistré."

    def tool_list_local_directory(self, path):
        folder = Path(path)
        if not folder.is_absolute() or not folder.is_dir():
            raise ValueError(f"Dossier local introuvable : {path}")
        entries = []
        for child in sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold()))[:LIST_LIMIT]:
            try:
                info = child.stat()
            except OSError:
                continue
            entries.append(describe_entry(child.name, child.is_dir(), info.st_size, info.st_mtime))
        total = sum(1 for _ in folder.iterdir())
        more = f"\n… {total - LIST_LIMIT} autre(s) élément(s) non listé(s)." if total > LIST_LIMIT else ""
        return (f"{path} ({total} élément(s)) :\n" + "\n".join(entries) + more) if entries else f"{path} est vide."

    def tool_list_remote_directory(self, profile, path):
        from smftp.core.clients import make_client, UnknownHostError
        from smftp.utils.storage import data_dir
        if not path.startswith("/"):
            raise ValueError("Le chemin distant doit commencer par /.")
        key = self.profile_key(profile)
        connection = self.scheduler.connection_for({"profile": key})
        client = make_client(connection.protocol, str(data_dir() / "known_hosts"))
        try:
            client.connect(connection)
            items = client.listdir(path)
        except UnknownHostError:
            raise ValueError("Clé du serveur SSH pas encore approuvée : connectez-vous une fois depuis SMFTP "
                             "pour vérifier son empreinte.") from None
        except Exception as error:
            from smftp.core.errors import describe_error
            raise ValueError(describe_error(error, connection, "Lecture du dossier distant").summary) from None
        finally:
            try:
                client.close()
            except Exception:
                pass
        items = sorted(items, key=lambda e: (not e.is_dir, e.name.casefold()))
        lines = [describe_entry(e.name, e.is_dir, e.size, e.modified) for e in items[:LIST_LIMIT]]
        more = f"\n… {len(items) - LIST_LIMIT} autre(s) élément(s) non listé(s)." if len(items) > LIST_LIMIT else ""
        return (f"{path} ({len(items)} élément(s)) :\n" + "\n".join(lines) + more) if lines else f"{path} est vide."

    def tool_list_scheduled_tasks(self):
        latest = {row.get("task_id"): row for row in self.scheduler.store.history}
        rows = []
        for task in self.scheduler.store.tasks:
            last = latest.get(task["id"])
            rows.append({"task_id": task["id"], "nom": task["name"], "active": task["enabled"],
                         "action": S.summary(task), "quand": S.describe(task),
                         "profil": self.scheduler.profile_label(task["profile"]),
                         "prochaine_execution": task["next_run"] or None,
                         "dernier_resultat": f"{last['status']} le {last['started']} : {last.get('detail', '')}" if last else None,
                         "en_cours": self.scheduler.running(task["id"]),
                         "reglages": {k: task[k] for k in ("kind", "upload", "local", "remote", "directory",
                                                           "compare_content", "conflict", "recurrence", "catch_up")}})
        return json.dumps(rows, ensure_ascii=False) if rows else "Aucune tâche planifiée."

    def tool_get_transfer_status(self):
        groups = {}
        for record in self.window.transfers.records:
            groups.setdefault(record["state"], []).append(record)
        def line(record):
            task = record["task"]
            reason = record["item"].toolTip(5).split("\n")[0]
            arrow = "↑" if task.upload else "↓"
            return f"{arrow} {Path(task.local).name} ({task.remote})" + (f" — {reason}" if reason else "")
        parts = []
        for state, label in (("active", "En cours"), ("pending", "En attente"), ("paused_pending", "En pause"),
                             ("Échoué", "Échoués"), ("Annulé", "Annulés"), ("Terminé", "Terminés")):
            records = groups.get(state, [])
            if records:
                shown = records[-20:]
                parts.append(f"{label} ({len(records)}) :\n" + "\n".join(line(r) for r in shown))
        return "\n\n".join(parts) if parts else "La file de transferts est vide."

    def tool_read_journal(self, lines):
        count = max(20, min(300, int(lines)))
        text = self.window.logs.toPlainText().splitlines()[-count:]
        return "\n".join(text) if text else "Le journal est vide."

    # ----- actions (confirmed) -----
    def tool_create_scheduled_task(self, **data):
        task = A.task_from_tool(data)
        task["profile"] = self.profile_key(data.get("profile", ""))
        clean = S.normalize_task(task, list(self.scheduler.profiles()))
        self.scheduler.check_once_in_future(clean)
        if not self.confirm(f"Créer la tâche planifiée « {clean['name']} » ?", self.task_text(clean)):
            return "L’utilisateur a refusé de créer cette tâche."
        created = self.scheduler.add_task(task)
        return (f"Tâche créée (task_id {created['id']}). Prochaine exécution : {created['next_run'] or 'aucune'}. "
                + ("Attention : le mot de passe de ce profil n’est pas mémorisé, la tâche échouera."
                   if not self.scheduler.profiles()[created["profile"]].get("remember") else ""))

    def tool_update_scheduled_task(self, task_id, **data):
        current = self.scheduler.store.find(task_id)
        if current is None:
            raise ValueError("Tâche introuvable : appelez list_scheduled_tasks pour obtenir les identifiants.")
        task = A.task_from_tool(data)
        task["profile"] = self.profile_key(data.get("profile", ""))
        clean = S.normalize_task(dict(task, id=task_id), list(self.scheduler.profiles()))
        if not self.confirm(f"Modifier la tâche « {current['name']} » ?",
                            "Avant :\n" + self.task_text(current) + "\n\nAprès :\n" + self.task_text(clean)):
            return "L’utilisateur a refusé cette modification."
        updated = self.scheduler.update_task(task_id, task)
        return f"Tâche modifiée. Prochaine exécution : {updated['next_run'] or 'aucune'}."

    def tool_delete_scheduled_task(self, task_id):
        task = self.scheduler.store.find(task_id)
        if task is None:
            raise ValueError("Tâche introuvable.")
        if not self.confirm(f"Supprimer la tâche « {task['name']} » ?",
                            self.task_text(task) + "\n\nAucun fichier n’est supprimé."):
            return "L’utilisateur a refusé la suppression."
        self.scheduler.remove_task(task_id)
        return "Tâche supprimée."

    def tool_run_scheduled_task_now(self, task_id):
        task = self.scheduler.store.find(task_id)
        if task is None:
            raise ValueError("Tâche introuvable.")
        if not self.confirm(f"Lancer maintenant la tâche « {task['name']} » ?", self.task_text(task)):
            return "L’utilisateur a refusé de lancer la tâche."
        self.scheduler.run_now(task_id)
        row = self.scheduler.store.history[-1]
        return f"Tâche lancée : {row['status']} — {row.get('detail', '')}. Le résultat final apparaîtra dans l’historique."

    def tool_queue_transfer(self, profile, upload, local, remote, directory):
        from smftp.workers.transfer import Transfer
        key = self.profile_key(profile)
        if not Path(local).is_absolute() or not remote.startswith("/"):
            raise ValueError("Chemins complets attendus (local absolu, distant commençant par /).")
        if upload and not Path(local).exists():
            raise ValueError(f"Chemin local introuvable : {local}")
        if not upload and not Path(local).parent.is_dir():
            raise ValueError(f"Dossier local introuvable : {Path(local).parent}")
        connection = self.scheduler.connection_for({"profile": key})
        label = self.scheduler.profile_label(key)
        text = (f"Envoyer {local} → {remote}" if upload else f"Télécharger {remote} → {local}") + f"\nServeur : {label}"
        if not self.confirm("Ajouter ce transfert à la file ?", text):
            return "L’utilisateur a refusé ce transfert."
        conflict = self.window.settings.value("conflict", "ask")
        record = self.window.transfers.add(Transfer(connection, local, remote, bool(upload),
                                                    bool(directory or (upload and Path(local).is_dir())), conflict=conflict))
        return "Transfert ajouté à la file." if record else "Ce transfert est déjà dans la file."


class AssistantWorker(QThread):
    tool_started = Signal(str)
    done = Signal(object)  # TurnResult
    failed = Signal(str, bool)  # message, the API key was rejected

    def __init__(self, conversation, text, context, parent=None):
        super().__init__(parent)
        self.conversation, self.text, self.context = conversation, text, context

    def run(self):
        import anthropic
        try:
            result = self.conversation.send(self.text, self.context,
                                            on_tool=lambda name, _input: self.tool_started.emit(name),
                                            cancelled=self.isInterruptionRequested)
            self.done.emit(result)
        except anthropic.AuthenticationError:
            self.failed.emit("Clé API refusée. Vérifiez-la dans « Clé API… ».", True)
        except anthropic.PermissionDeniedError:
            self.failed.emit("Cette clé API n’a pas accès au modèle demandé.", False)
        except anthropic.RateLimitError:
            self.failed.emit("Trop de demandes pour le moment : réessayez dans une minute.", False)
        except anthropic.APIConnectionError:
            self.failed.emit("Impossible de joindre le service d’IA : vérifiez la connexion Internet.", False)
        except anthropic.APIStatusError as error:
            self.failed.emit(f"Le service d’IA a renvoyé une erreur ({error.status_code}). Réessayez plus tard.", False)
        except Exception as error:
            self.failed.emit(f"Erreur inattendue de l’assistant : {error}", False)


class AssistantPanel(QWidget):
    """Third tab of the activity area."""

    def __init__(self, window, client_factory=None):
        super().__init__(window)
        self.window = window
        self.client_factory = client_factory
        self.toolbox = Toolbox(window)
        self.conversation = None
        self.worker = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        bar = QHBoxLayout()
        bar.setSpacing(2)
        self.status = QLabel(self.tr("Assistant IA (Claude) — chaque action est confirmée avant d’être appliquée."))
        self.status.setObjectName("muted")
        bar.addWidget(self.status, 1)
        self.key_button = QPushButton(self.tr("Clé API…"))
        self.key_button.clicked.connect(self.ask_key)
        bar.addWidget(self.key_button)
        self.reset_button = icon_button("trash", self.tr("Nouvelle conversation"), self.reset)
        bar.addWidget(self.reset_button)
        layout.addLayout(bar)
        self.transcript = QTextBrowser()
        self.transcript.setOpenExternalLinks(True)
        layout.addWidget(self.transcript, 1)
        self.suggestions = QWidget()
        chips = QHBoxLayout(self.suggestions)
        chips.setContentsMargins(0, 0, 0, 0)
        for text in SUGGESTIONS:
            chip = QPushButton(self.tr(text))
            chip.setObjectName("breadcrumb")
            chip.clicked.connect(lambda checked=False, t=text: (self.input.setText(t), self.input.setFocus()))
            chips.addWidget(chip)
        chips.addStretch()
        layout.addWidget(self.suggestions)
        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText(self.tr("Ex. « Envoie le dossier C:\\Site vers /www chaque soir à 23 h »"))
        self.input.returnPressed.connect(self.send)
        self.input.textChanged.connect(self.update_buttons)
        row.addWidget(self.input, 1)
        self.send_button = QPushButton(self.tr("Envoyer"))
        self.send_button.setObjectName("primary")
        self.send_button.clicked.connect(self.send)
        row.addWidget(self.send_button)
        self.stop_button = QPushButton(self.tr("Arrêter"))
        self.stop_button.clicked.connect(self.stop)
        self.stop_button.hide()
        row.addWidget(self.stop_button)
        layout.addLayout(row)
        self.show_welcome()
        self.update_buttons()

    # ----- state -----
    def busy(self):
        return self.worker is not None and self.worker.isRunning()

    def update_buttons(self, *_):
        running = self.busy()
        self.send_button.setVisible(not running)
        self.stop_button.setVisible(running)
        self.send_button.setEnabled(bool(self.input.text().strip()))
        self.input.setEnabled(not running)
        self.reset_button.setEnabled(not running and self.conversation is not None and bool(self.conversation.messages))

    def show_welcome(self):
        self.transcript.setHtml(
            "<p><b>" + html.escape(self.tr("Bonjour !")) + "</b> " + html.escape(self.tr(
                "Décrivez ce que vous voulez faire : planifier une synchronisation, envoyer un dossier chaque nuit, "
                "comprendre une erreur de transfert…")) + "</p><p style='color:#8794a9'>" + html.escape(self.tr(
                "Vos demandes, les noms de fichiers et de dossiers consultés et des extraits du journal sont envoyés "
                "au service Claude d’Anthropic. Les mots de passe ne quittent jamais votre coffre-fort.")) + "</p>")
        self.welcome = True
        self.suggestions.show()

    def append(self, who, text, muted=False):
        color = "#8794a9" if muted else None
        body = html.escape(text).replace("\n", "<br>")
        style = f" style='color:{color}'" if color else ""
        prefix = f"<b>{html.escape(who)}</b><br>" if who else ""
        self.transcript.append(f"<p{style}>{prefix}{body}</p>")
        self.transcript.verticalScrollBar().setValue(self.transcript.verticalScrollBar().maximum())

    # ----- client -----
    def consent(self):
        settings = self.window.settings
        if settings.value("assistant_consent", False, type=bool):
            return True
        answer = QMessageBox.question(self, self.tr("Assistant IA"), self.tr(
            "L’assistant utilise le service Claude d’Anthropic. Vos messages, ainsi que les noms de fichiers, de dossiers, "
            "de profils et les extraits du journal qu’il consulte, lui sont envoyés. Les mots de passe ne sont jamais "
            "transmis.\n\nUne clé API Anthropic est nécessaire (console.anthropic.com) ; son utilisation est facturée "
            "par Anthropic.\n\nActiver l’assistant ?"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return False
        settings.setValue("assistant_consent", True)
        return True

    def ask_key(self):
        value, ok = QInputDialog.getText(self, self.tr("Clé API Anthropic"), self.tr(
            "Collez votre clé API (elle est rangée dans le coffre-fort système).\nLaissez vide pour l’effacer."),
            QLineEdit.EchoMode.Password)
        if not ok:
            return False
        try:
            store_api_key(value.strip())
        except Exception:
            QMessageBox.warning(self, self.tr("Assistant IA"), self.tr("Impossible d’enregistrer la clé dans le coffre-fort."))
            return False
        self.conversation = None  # Next message uses the new key.
        return bool(value.strip())

    def make_client(self):
        if self.client_factory:
            return self.client_factory()
        import anthropic
        key = read_api_key() or os.environ.get("ANTHROPIC_API_KEY")
        if not key and not self.ask_key():
            return None
        key = read_api_key() or key
        return anthropic.Anthropic(api_key=key, timeout=120.0, max_retries=2)

    # ----- chat -----
    def context(self):
        now = datetime.now()
        lines = [f"Date et heure locales : {DAYS[now.weekday()]} {now:%Y-%m-%d %H:%M}"]
        window = self.window
        if window.online and window.connection:
            lines.append(f"Serveur ouvert : {window.connection.name or window.connection.host} "
                         f"(dossier distant affiché : {window.remote.path})")
        lines.append(f"Dossier local affiché : {window.local.path}")
        return "\n".join(lines)

    def send(self):
        text = self.input.text().strip()
        if not text or self.busy():
            return
        if self.conversation is None:
            if not self.consent():
                return
            try:
                client = self.make_client()
            except ImportError:
                self.append("", self.tr("Le module « anthropic » n’est pas installé : pip install anthropic."), muted=True)
                return
            if client is None:
                return
            self.conversation = A.Conversation(client, self.toolbox.execute)
        if self.suggestions.isVisible() or self.welcome:
            self.transcript.clear()  # Replace the welcome text with the conversation.
            self.welcome = False
        self.suggestions.hide()
        self.input.clear()
        self.append(self.tr("Vous"), text)
        self.status.setText(self.tr("L’assistant réfléchit…"))
        self.worker = AssistantWorker(self.conversation, text, self.context(), self)
        self.worker.tool_started.connect(self.on_tool)
        self.worker.done.connect(self.on_done)
        self.worker.failed.connect(self.on_failed)
        self.worker.finished.connect(self.on_finished)
        self.worker.start()
        self.update_buttons()

    def on_tool(self, name):
        label = TOOL_LABELS.get(name, name)
        self.status.setText(self.tr("L’assistant %s…") % label)
        self.append("", "→ " + self.tr("L’assistant %s.") % label, muted=True)

    def on_done(self, result):
        if result.text:
            self.append(self.tr("Assistant"), result.text)
        notes = {"refusal": "L’assistant a refusé cette demande. Reformulez-la.",
                 "max_tokens": "Réponse interrompue (trop longue).",
                 "rounds": "L’assistant s’est arrêté après trop d’étapes. Précisez la demande.",
                 "cancelled": "Arrêté."}
        if result.stopped in notes:
            self.append("", self.tr(notes[result.stopped]), muted=True)

    def on_failed(self, message, bad_key):
        self.append("", message, muted=True)
        if bad_key:
            self.conversation = None

    def on_finished(self):
        self.status.setText(self.tr("Assistant IA (Claude) — chaque action est confirmée avant d’être appliquée."))
        self.update_buttons()
        self.input.setFocus()

    def stop(self):
        if self.busy():
            self.worker.requestInterruption()
            self.status.setText(self.tr("Arrêt après l’étape en cours…"))

    def reset(self):
        if self.busy():
            return
        if self.conversation is not None:
            self.conversation.messages.clear()
        self.show_welcome()
        self.update_buttons()
