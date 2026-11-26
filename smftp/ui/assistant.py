"""Assistant tab: chat with Claude, which acts through confirmed SMFTP tools."""
from datetime import datetime
import json
import os
from pathlib import Path
from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot, QTimer, QSize
from PySide6.QtWidgets import (QWidget, QFrame, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit, QPushButton,
                               QToolButton, QPlainTextEdit, QScrollArea, QSizePolicy, QMessageBox, QDialog,
                               QDialogButtonBox, QRadioButton, QButtonGroup, QStackedWidget, QComboBox,
                               QCheckBox, QMenu)
from smftp.core import assistant as A
from smftp.core import schedule as S
from smftp.ui.icons import icon_button, make_icon, recolor, theme_of

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
               "Quelles tâches sont prévues cette semaine ?", "Télécharge les rapports du serveur chaque lundi à 8 h"]
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
        self.window.set_assistant_visible(True)
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

    @staticmethod
    def refuse_network_path(*paths):
        """Paths come from the model, which may follow text planted in a file name or a
        server banner: a \\\\server\\share path would make Windows contact that server
        with the user's credentials, so it is refused before any disk access."""
        from smftp.utils.paths import is_network_path
        for path in paths:
            if is_network_path(path):
                raise ValueError("Chemins réseau (\\\\serveur\\partage) refusés pour l’assistant : "
                                 "utilisez un dossier de ce PC ou un lecteur réseau mappé (Z:\\…).")

    def tool_list_local_directory(self, path):
        self.refuse_network_path(path)
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
                         "reglages": A.task_to_tool(task)})
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
        self.refuse_network_path(task.get("local", ""))
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
        self.refuse_network_path(task.get("local", ""))
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

    def tool_queue_transfer(self, profile, direction, local, remote, directory):
        from smftp.workers.transfer import Transfer
        upload = direction != "telecharger"
        key = self.profile_key(profile)
        self.refuse_network_path(local)
        if not Path(local).is_absolute() or not remote.startswith("/"):
            raise ValueError("Chemins complets attendus (local absolu, distant commençant par /).")
        connection = self.scheduler.connection_for({"profile": key})
        label = self.scheduler.profile_label(key)
        text = (f"Envoyer {local} → {remote}" if upload else f"Télécharger {remote} → {local}") + f"\nServeur : {label}"
        # The disk is read only once the user has approved the paths.
        if not self.confirm("Ajouter ce transfert à la file ?", text):
            return "L’utilisateur a refusé ce transfert."
        if upload and not Path(local).exists():
            raise ValueError(f"Chemin local introuvable : {local}")
        if not upload and not Path(local).parent.is_dir():
            raise ValueError(f"Dossier local introuvable : {Path(local).parent}")
        conflict = self.window.settings.value("conflict", "ask")
        record = self.window.transfers.add(Transfer(connection, local, remote, bool(upload),
                                                    bool(directory or (upload and Path(local).is_dir())), conflict=conflict))
        return "Transfert ajouté à la file." if record else "Ce transfert est déjà dans la file."


class AssistantWorker(QThread):
    tool_started = Signal(str)
    text_delta = Signal(str)  # Answer text as it arrives (Claude).
    progress = Signal(int)  # Characters produced so far (local models).
    done = Signal(object)  # TurnResult
    failed = Signal(str, bool)  # message, the provider settings need attention

    def __init__(self, conversation, text, context, parent=None):
        super().__init__(parent)
        self.conversation, self.text, self.context = conversation, text, context

    def run(self):
        if hasattr(self.conversation, "on_progress"):
            self.conversation.on_progress = self.progress.emit
        if hasattr(self.conversation, "on_text"):
            self.conversation.on_text = self.text_delta.emit
        try:
            result = self.conversation.send(self.text, self.context,
                                            on_tool=lambda name, _input: self.tool_started.emit(name),
                                            cancelled=self.isInterruptionRequested)
            self.done.emit(result)
        except A.ProviderError as error:
            self.failed.emit(str(error), True)
        except Exception as error:
            self.failed.emit(*self.explain(error))

    @staticmethod
    def explain(error):
        try:
            import anthropic
        except ImportError:
            return f"Erreur inattendue de l’assistant : {error}", False
        if isinstance(error, anthropic.AuthenticationError):
            return "Clé API refusée. Vérifiez-la dans les réglages de l’assistant.", True
        if isinstance(error, anthropic.PermissionDeniedError):
            return "Cette clé API n’a pas accès au modèle choisi.", True
        if isinstance(error, anthropic.NotFoundError):
            return "Modèle introuvable pour cette clé API : choisissez-en un autre dans les réglages.", True
        if isinstance(error, anthropic.RateLimitError):
            return "Trop de demandes pour le moment : réessayez dans une minute.", False
        if isinstance(error, anthropic.APIConnectionError):
            return "Impossible de joindre le service Claude : vérifiez la connexion Internet.", False
        if isinstance(error, anthropic.APIStatusError):
            return f"Le service Claude a renvoyé une erreur ({error.status_code}). Réessayez plus tard.", False
        return f"Erreur inattendue de l’assistant : {error}", False


# ------------------------------------------------------------------ settings

def provider_settings(settings):
    return {
        "provider": settings.value("assistant_provider", "claude"),
        "claude_model": settings.value("assistant_claude_model", A.MODEL),
        "ollama_url": settings.value("assistant_ollama_url", A.OLLAMA_URL),
        "ollama_model": settings.value("assistant_ollama_model", ""),
        "ollama_thinking": settings.value("assistant_ollama_thinking", False, type=bool),
        "effort": settings.value("assistant_effort", A.EFFORT),
        "fast": settings.value("assistant_fast", False, type=bool),
    }


def provider_label(config):
    if config["provider"] == "ollama":
        where = "local" if A.is_local_url(config["ollama_url"]) else "serveur distant"
        return f"Ollama · {config['ollama_model'] or 'aucun modèle'} · {where}"
    label = A.CLAUDE_MODELS.get(config["claude_model"], config["claude_model"]).split(" — ")[0]
    if config["fast"] and config["claude_model"] in A.FAST_MODELS:
        label += " · rapide"
    return label


class ModelLister(QThread):
    """Reads the installed Ollama models without freezing the settings window."""
    listed = Signal(object)
    failed = Signal(str)

    def __init__(self, url, parent=None):
        super().__init__(parent)
        self.url = url

    def run(self):
        try:
            self.listed.emit(A.ollama_models(self.url))
        except A.ProviderError as error:
            self.failed.emit(str(error))


class AssistantSettings(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.lister = None
        self.models = {}
        config = provider_settings(settings)
        self.setWindowTitle(self.tr("Réglages de l’assistant"))
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        title = QLabel(self.tr("Quel modèle fait tourner l’assistant ?"))
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        self.claude_choice = QRadioButton(self.tr("Claude (Anthropic) — en ligne, le plus fiable"))
        self.ollama_choice = QRadioButton(self.tr("Ollama — modèle local, rien ne quitte ce PC"))
        group = QButtonGroup(self)
        for button in (self.claude_choice, self.ollama_choice):
            group.addButton(button)
            layout.addWidget(button)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages)
        # Claude page.
        claude = QWidget()
        form = QFormLayout(claude)
        form.setContentsMargins(24, 4, 0, 0)
        self.claude_model = QComboBox()
        for value, label in A.CLAUDE_MODELS.items():
            self.claude_model.addItem(label, value)
        self.claude_model.setCurrentIndex(max(0, self.claude_model.findData(config["claude_model"])))
        form.addRow(self.tr("Modèle"), self.claude_model)
        self.effort = QComboBox()
        for value, label in A.EFFORTS.items():
            self.effort.addItem(self.tr(label), value)
        self.effort.setCurrentIndex(max(0, self.effort.findData(config["effort"])))
        form.addRow(self.tr("Réflexion"), self.effort)
        self.fast = QCheckBox(self.tr("Mode rapide : réponses jusqu’à 2,5 fois plus vite, facturées 2 fois plus cher "
                                      "(Opus 5.5 uniquement)"))
        self.fast.setChecked(config["fast"])
        form.addRow("", self.fast)
        self.claude_model.currentIndexChanged.connect(
            lambda _: self.fast.setEnabled(self.claude_model.currentData() in A.FAST_MODELS))
        self.fast.setEnabled(self.claude_model.currentData() in A.FAST_MODELS)
        self.api_key = QLineEdit(read_api_key() or "")
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText(self.tr("sk-ant-…"))
        form.addRow(self.tr("Clé API"), self.api_key)
        claude_note = QLabel(self.tr("Créez une clé sur <a href='https://console.anthropic.com'>console.anthropic.com</a>. "
                                     "Elle est rangée dans le coffre-fort système. L’usage est facturé par Anthropic ; "
                                     "vos demandes, les noms de fichiers consultés et des extraits du journal lui sont "
                                     "envoyés, jamais les mots de passe."))
        claude_note.setOpenExternalLinks(True)
        claude_note.setWordWrap(True)
        claude_note.setObjectName("muted")
        form.addRow(claude_note)
        self.pages.addWidget(claude)
        # Ollama page.
        ollama = QWidget()
        form = QFormLayout(ollama)
        form.setContentsMargins(24, 4, 0, 0)
        self.ollama_url = QLineEdit(config["ollama_url"])
        self.ollama_url.editingFinished.connect(self.refresh_models)
        form.addRow(self.tr("Adresse"), self.ollama_url)
        model_row = QWidget()
        row = QHBoxLayout(model_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.ollama_model = QComboBox()
        self.ollama_model.setEditable(True)
        self.ollama_model.setMinimumWidth(260)
        if config["ollama_model"]:
            self.ollama_model.addItem(config["ollama_model"], config["ollama_model"])
        self.ollama_model.currentTextChanged.connect(self.update_hint)
        row.addWidget(self.ollama_model, 1)
        row.addWidget(icon_button("retry", self.tr("Actualiser la liste des modèles"), self.refresh_models))
        form.addRow(self.tr("Modèle"), model_row)
        self.ollama_status = QLabel()
        self.ollama_status.setWordWrap(True)
        form.addRow(self.ollama_status)
        self.ollama_hint = QLabel()
        self.ollama_hint.setWordWrap(True)
        self.ollama_hint.setObjectName("muted")
        form.addRow(self.ollama_hint)
        self.pages.addWidget(ollama)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(self.tr("Enregistrer"))
        buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName("primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(self.tr("Annuler"))
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.claude_choice.toggled.connect(lambda on: self.pages.setCurrentIndex(0 if on else 1))
        self.ollama_choice.toggled.connect(lambda on: on and not self.models and self.refresh_models())
        (self.ollama_choice if config["provider"] == "ollama" else self.claude_choice).setChecked(True)
        self.pages.setCurrentIndex(1 if config["provider"] == "ollama" else 0)
        recolor(self, theme_of(parent))
        self.update_hint()

    def refresh_models(self):
        if self.lister is not None and self.lister.isRunning():
            return
        self.set_status(self.tr("Recherche des modèles installés…"), "muted")
        self.lister = ModelLister(self.ollama_url.text().strip() or A.OLLAMA_URL, self)
        self.lister.listed.connect(self.show_models)
        self.lister.failed.connect(lambda message: self.set_status("✕ " + message, "error"))
        self.lister.start()

    def show_models(self, models):
        current = self.ollama_model.currentText()
        self.models = {m["name"]: m for m in models}
        self.ollama_model.clear()
        for model in models:
            suffix = f"  ({model['size']})" if model["size"] else ""
            label = model["name"] + suffix + ("" if model["tools"] else self.tr("  — sans appel d’outils"))
            self.ollama_model.addItem(label, model["name"])
        usable = [m for m in models if m["tools"]]
        index = self.ollama_model.findData(current)
        if index < 0 and usable:
            index = self.ollama_model.findData(usable[0]["name"])
        self.ollama_model.setCurrentIndex(max(0, index))
        if not models:
            self.set_status(self.tr("✕ Ollama répond, mais aucun modèle n’est installé. Exemple : ollama pull qwen3:4b"), "error")
        else:
            self.set_status(self.tr("✓ Ollama répond : %n modèle(s) installé(s).", "", len(models)), "success")
        self.update_hint()

    def set_status(self, text, kind):
        self.ollama_status.setObjectName(kind)
        self.ollama_status.setText(text)
        self.ollama_status.style().unpolish(self.ollama_status)
        self.ollama_status.style().polish(self.ollama_status)

    def chosen_model(self):
        data = self.ollama_model.currentData()
        text = self.ollama_model.currentText().strip()
        # An edited name (not in the list) is taken as typed.
        return data if data and text.startswith(data) else text.split("  ")[0]

    def update_hint(self, *_):
        hints = []
        model = self.models.get(self.chosen_model())
        if model is not None and not model["tools"]:
            hints.append(self.tr("⚠ Ce modèle ne sait pas appeler d’outils : l’assistant ne pourra rien consulter ni planifier. "
                                 "Choisissez-en un marqué compatible (ex. qwen3, llama3.1, mistral)."))
        if not A.is_local_url(self.ollama_url.text().strip() or A.OLLAMA_URL):
            hints.append(self.tr("⚠ Cette adresse n’est pas sur ce PC : vos demandes et les noms de fichiers consultés "
                                 "partiront vers ce serveur."))
        hints.append(self.tr("Un modèle local est gratuit et privé, mais plus lent et moins fiable que Claude sur les "
                             "demandes en plusieurs étapes. Sur une petite carte graphique, comptez de 30 s à 2 min par "
                             "réponse. Chaque action reste soumise à votre confirmation."))
        self.ollama_hint.setText("\n\n".join(hints))

    def save(self):
        if self.ollama_choice.isChecked():
            model = self.chosen_model()
            if not model:
                self.set_status(self.tr("✕ Choisissez un modèle."), "error")
                return
            self.settings.setValue("assistant_provider", "ollama")
            self.settings.setValue("assistant_ollama_url", self.ollama_url.text().strip() or A.OLLAMA_URL)
            self.settings.setValue("assistant_ollama_model", model)
            self.settings.setValue("assistant_ollama_thinking", bool(self.models.get(model, {}).get("thinking")))
        else:
            self.settings.setValue("assistant_provider", "claude")
            self.settings.setValue("assistant_claude_model", self.claude_model.currentData())
            self.settings.setValue("assistant_effort", self.effort.currentData())
            self.settings.setValue("assistant_fast", self.fast.isChecked())
            try:
                store_api_key(self.api_key.text().strip())
            except Exception:
                QMessageBox.warning(self, self.tr("Assistant"), self.tr("Impossible d’enregistrer la clé dans le coffre-fort."))
                return
        self.accept()

    def done(self, result):
        if self.lister is not None and self.lister.isRunning():
            self.lister.wait(6000)
        super().done(result)


# ------------------------------------------------------------------ chat pane

class ChatInput(QPlainTextEdit):
    """Enter sends, Shift+Enter starts a new line; grows up to five lines."""
    submitted = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTabChangesFocus(True)
        self.textChanged.connect(self.fit)
        self.fit()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.submitted.emit()
            return
        super().keyPressEvent(event)

    def fit(self):
        wanted = max(1, int(self.document().size().height()))
        lines = min(5, wanted)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded if wanted > 5
                                        else Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFixedHeight(self.fontMetrics().lineSpacing() * lines + 16)

    def text(self):
        return self.toPlainText()

    def setText(self, value):
        self.setPlainText(value)
        cursor = self.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.setTextCursor(cursor)


def open_web_link(url: str) -> bool:
    """Open https:// links only; anything else is refused."""
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    link = QUrl(url)
    if link.scheme().lower() != "https" or not link.host():
        return False
    return QDesktopServices.openUrl(link)


class Bubble(QFrame):
    """One message: the user's on the right, the assistant's on the left (Markdown)."""

    def __init__(self, kind, text, parent=None):
        super().__init__(parent)
        self.setObjectName({"user": "bubbleUser", "assistant": "bubbleAssistant", "error": "bubbleError",
                            "note": "bubbleNote"}[kind])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse |
                                           Qt.TextInteractionFlag.LinksAccessibleByMouse)
        # Model answers may be influenced by file names or server messages: never open
        # file:, \\server, ms-msdt:, search-ms: or other local handlers from a link.
        self.label.setOpenExternalLinks(False)
        self.label.linkActivated.connect(open_web_link)
        if kind == "assistant":
            self.label.setTextFormat(Qt.TextFormat.MarkdownText)
        else:
            self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setText(text)
        layout.addWidget(self.label)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)


class Steps(QWidget):
    """What the assistant is doing, as a short checklist under the pending answer."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("chatRow")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 0, 0, 0)
        layout.setSpacing(2)
        self.lines = []
        self.thinking = QLabel()
        self.thinking.setObjectName("muted")
        layout.addWidget(self.thinking)
        self.dots = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.animate)
        self.timer.start(450)
        self.animate()

    def animate(self):
        self.dots = self.dots % 3 + 1
        self.thinking.setText(self.tr("L’assistant réfléchit") + "." * self.dots + self.progress_text)

    progress_text = ""

    def set_progress(self, characters):
        # Local models can think for minutes: show that work is going on.
        self.progress_text = f"   {characters:,} caractères".replace(",", " ")

    def add(self, label):
        if self.lines:
            self.lines[-1].setText("✓ " + self.lines[-1].text()[2:])
        line = QLabel("… " + label)
        line.setObjectName("muted")
        self.layout().insertWidget(len(self.lines), line)
        self.lines.append(line)

    def finish(self):
        self.timer.stop()
        self.thinking.hide()
        if self.lines:
            self.lines[-1].setText("✓ " + self.lines[-1].text()[2:])
        else:
            self.hide()


class AssistantPanel(QFrame):
    """Side pane: chat with the assistant, which acts through confirmed SMFTP tools."""

    def __init__(self, window, client_factory=None, conversation_factory=None):
        super().__init__(window)
        self.setObjectName("assistantPane")
        self.setMinimumWidth(330)
        self.window = window
        self.client_factory = client_factory
        self.conversation_factory = conversation_factory
        self.toolbox = Toolbox(window)
        self.conversation = None
        self.worker = None
        self.steps = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        # Header.
        header = QFrame()
        header.setObjectName("assistantHeader")
        top = QHBoxLayout(header)
        top.setContentsMargins(14, 10, 8, 10)
        top.setSpacing(4)
        mark = QLabel()
        mark.setPixmap(make_icon("spark", True, "#5788f3").pixmap(QSize(20, 20)))
        top.addWidget(mark)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        title = QLabel(self.tr("Assistant"))
        title.setObjectName("paneTitle")
        titles.addWidget(title)
        self.provider = QPushButton()
        self.provider.setObjectName("providerChip")
        self.provider.setToolTip(self.tr("Changer de modèle"))
        self.provider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.provider.clicked.connect(self.show_model_menu)
        titles.addWidget(self.provider, 0, Qt.AlignmentFlag.AlignLeft)
        top.addLayout(titles, 1)
        self.reset_button = icon_button("edit", self.tr("Nouvelle conversation"), self.reset)
        self.settings_button = icon_button("gear", self.tr("Réglages : fournisseur, modèle, clé API"), self.open_settings)
        self.close_button = icon_button("close", self.tr("Masquer l’assistant (Ctrl+Maj+I)"),
                                        lambda: window.set_assistant_visible(False))
        for button in (self.reset_button, self.settings_button, self.close_button):
            top.addWidget(button)
        outer.addWidget(header)
        # Conversation.
        self.scroll = QScrollArea()
        self.scroll.setObjectName("chatScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("chatBody")
        self.messages = QVBoxLayout(body)
        self.messages.setContentsMargins(14, 14, 14, 14)
        self.messages.setSpacing(10)
        self.messages.addStretch()
        self.scroll.setWidget(body)
        # A bound method (not a lambda) is disconnected when the pane is destroyed.
        self.scroll.verticalScrollBar().rangeChanged.connect(self.scroll_to_end)
        outer.addWidget(self.scroll, 1)
        self.empty = self.build_empty_state()
        self.messages.insertWidget(0, self.empty)
        # Composer.
        composer_box = QFrame()
        composer_box.setObjectName("composerArea")
        area = QVBoxLayout(composer_box)
        area.setContentsMargins(12, 8, 12, 10)
        area.setSpacing(4)
        composer = QFrame()
        composer.setObjectName("composer")
        row = QHBoxLayout(composer)
        row.setContentsMargins(10, 4, 4, 4)
        row.setSpacing(6)
        self.input = ChatInput()
        self.input.setObjectName("chatInput")
        self.input.setPlaceholderText(self.tr("Demandez à l’assistant…"))
        self.input.submitted.connect(self.send)
        self.input.textChanged.connect(self.update_buttons)
        row.addWidget(self.input, 1)
        self.send_button = QToolButton()
        self.send_button.setObjectName("sendButton")
        self.send_button.setIconSize(QSize(18, 18))
        self.send_button.setFixedSize(34, 34)
        self.send_button.clicked.connect(self.send_or_stop)
        row.addWidget(self.send_button, 0, Qt.AlignmentFlag.AlignBottom)
        area.addWidget(composer)
        hint = QLabel(self.tr("Entrée pour envoyer • Maj+Entrée : nouvelle ligne • chaque action vous est demandée"))
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        area.addWidget(hint)
        outer.addWidget(composer_box)
        recolor(self, theme_of(window))
        self.refresh_provider()
        self.update_buttons()

    # ----- layout pieces -----
    def build_empty_state(self):
        box = QWidget()
        box.setObjectName("chatRow")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(4, 18, 4, 4)
        layout.setSpacing(8)
        icon = QLabel()
        icon.setPixmap(make_icon("spark", True, "#5788f3").pixmap(QSize(36, 36)))
        layout.addWidget(icon)
        title = QLabel(self.tr("Que voulez-vous automatiser ?"))
        title.setObjectName("emptyTitle")
        title.setWordWrap(True)
        layout.addWidget(title)
        intro = QLabel(self.tr("Décrivez votre besoin en quelques mots. L’assistant consulte vos serveurs et dossiers, "
                               "puis vous propose les actions : rien n’est fait sans votre accord."))
        intro.setWordWrap(True)
        intro.setObjectName("muted")
        layout.addWidget(intro)
        layout.addSpacing(6)
        self.suggestions = QWidget()
        self.suggestions.setObjectName("chatRow")
        chips = QVBoxLayout(self.suggestions)
        chips.setContentsMargins(0, 0, 0, 0)
        chips.setSpacing(6)
        for text in SUGGESTIONS:
            chip = QPushButton(self.tr(text))
            chip.setObjectName("suggestion")
            chip.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            chip.setToolTip(self.tr(text))
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.clicked.connect(lambda checked=False, t=text: self.use_suggestion(t))
            chips.addWidget(chip)
        layout.addWidget(self.suggestions)
        layout.addSpacing(6)
        self.privacy = QLabel()
        self.privacy.setWordWrap(True)
        self.privacy.setObjectName("hint")
        layout.addWidget(self.privacy)
        return box

    def use_suggestion(self, text):
        self.input.setText(self.tr(text))
        self.input.setFocus()

    def add_widget(self, widget, align=None):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        if align == "right":
            row.addStretch(1)
            row.addWidget(widget, 4)
        elif align == "left":
            row.addWidget(widget, 6)
            row.addStretch(0)
        else:
            row.addWidget(widget)
        holder = QWidget()
        holder.setObjectName("chatRow")
        holder.setLayout(row)
        self.messages.insertWidget(self.messages.count() - 1, holder)
        return holder

    def append(self, kind, text):
        bubble = Bubble(kind, text)
        self.add_widget(bubble, "right" if kind == "user" else "left")
        return bubble

    @Slot(int, int)
    def scroll_to_end(self, _minimum, maximum):
        self.scroll.verticalScrollBar().setValue(maximum)

    def focus_input(self):
        self.input.setFocus()

    # ----- state -----
    def config(self):
        return provider_settings(self.window.settings)

    def refresh_provider(self):
        config = self.config()
        self.provider.setText(provider_label(config) + "  ▾")
        if config["provider"] == "ollama" and A.is_local_url(config["ollama_url"]):
            self.privacy.setText(self.tr("Modèle local : vos demandes restent sur ce PC."))
        elif config["provider"] == "ollama":
            self.privacy.setText(self.tr("Les demandes partent vers le serveur Ollama configuré. Les mots de passe ne "
                                         "quittent jamais votre coffre-fort."))
        else:
            self.privacy.setText(self.tr("Vos demandes, les noms de fichiers consultés et des extraits du journal sont "
                                         "envoyés à Claude (Anthropic). Les mots de passe ne quittent jamais votre coffre-fort."))

    def busy(self):
        return self.worker is not None and self.worker.isRunning()

    def update_buttons(self, *_):
        running = self.busy()
        dark = theme_of(self.window)
        self.send_button.setIcon(make_icon("stop" if running else "send", dark, "#ffffff"))
        self.send_button.setToolTip(self.tr("Arrêter après l’étape en cours") if running else self.tr("Envoyer (Entrée)"))
        self.send_button.setEnabled(running or bool(self.input.text().strip()))
        self.input.setReadOnly(running)
        self.reset_button.setEnabled(not running and self.conversation is not None and bool(self.conversation.messages))
        self.settings_button.setEnabled(not running)
        self.provider.setEnabled(not running)

    def model_menu(self):
        """Every model one click away: Claude models, the local model, then the settings."""
        config = self.config()
        menu = QMenu(self)
        title = menu.addAction(self.tr("Claude (en ligne)"))
        title.setEnabled(False)
        for value, label in A.CLAUDE_MODELS.items():
            name, _, detail = label.partition(" — ")
            action = menu.addAction(f"{name}  —  {detail}")
            action.setCheckable(True)
            action.setChecked(config["provider"] == "claude" and config["claude_model"] == value)
            action.triggered.connect(lambda checked=False, v=value: self.choose_model("claude", v))
        menu.addSeparator()
        title = menu.addAction(self.tr("Ollama (sur ce PC)"))
        title.setEnabled(False)
        if config["ollama_model"]:
            action = menu.addAction(config["ollama_model"])
            action.setCheckable(True)
            action.setChecked(config["provider"] == "ollama")
            action.triggered.connect(lambda checked=False: self.choose_model("ollama", config["ollama_model"]))
        menu.addAction(self.tr("Choisir un modèle local…")).triggered.connect(self.open_settings)
        menu.addSeparator()
        menu.addAction(self.tr("Réglages de l’assistant…")).triggered.connect(self.open_settings)
        return menu

    def show_model_menu(self):
        if not self.busy():
            self.model_menu().exec(self.provider.mapToGlobal(self.provider.rect().bottomLeft()))

    def choose_model(self, provider, model):
        settings = self.window.settings
        previous = self.config()
        settings.setValue("assistant_provider", provider)
        settings.setValue("assistant_claude_model" if provider == "claude" else "assistant_ollama_model", model)
        if provider == "claude" and previous["provider"] == "claude" and isinstance(self.conversation, A.ClaudeConversation):
            # Same provider: the conversation continues on the new model.
            self.conversation.model = model
        elif provider != previous["provider"]:
            # The history format differs between providers: start afresh.
            self.conversation = None
            self.clear_messages()
        self.refresh_provider()
        self.update_buttons()

    def open_settings(self):
        if self.busy():
            return
        dialog = AssistantSettings(self.window.settings, self.window)
        if dialog.exec():
            self.conversation = None  # The next message starts with the new provider.
            self.clear_messages()
            self.refresh_provider()
            self.update_buttons()

    # ----- conversation -----
    def consent(self):
        settings = self.window.settings
        if settings.value("assistant_consent", False, type=bool):
            return True
        answer = QMessageBox.question(self.window, self.tr("Assistant IA"), self.tr(
            "L’assistant utilise le service Claude d’Anthropic. Vos messages, ainsi que les noms de fichiers, de dossiers, "
            "de profils et les extraits du journal qu’il consulte, lui sont envoyés. Les mots de passe ne sont jamais "
            "transmis.\n\nUne clé API Anthropic est nécessaire ; son utilisation est facturée par Anthropic. "
            "Vous pouvez aussi choisir un modèle local (Ollama) dans les réglages.\n\nUtiliser Claude ?"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return False
        settings.setValue("assistant_consent", True)
        return True

    def remote_ollama_consent(self, url):
        """Asked once per address: requests and file names leave this PC, in clear text over http."""
        settings = self.window.settings
        if settings.value("assistant_ollama_consent", "") == url:
            return True
        clear = url.lower().startswith("http://")
        text = self.tr("Le serveur Ollama « %1 » n’est pas sur ce PC. Vos messages, ainsi que les noms de fichiers, "
                       "de dossiers, de profils et les extraits du journal consultés lui seront envoyés. "
                       "Les mots de passe ne sont jamais transmis.").replace("%1", url)
        if clear:
            text += "\n\n" + self.tr("L’adresse commence par http:// : ces données circuleront EN CLAIR sur le réseau. "
                                       "Préférez https:// ou un tunnel SSH.")
        text += "\n\n" + self.tr("Utiliser ce serveur ?")
        if QMessageBox.question(self.window, self.tr("Assistant IA"), text,
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return False
        settings.setValue("assistant_ollama_consent", url)
        return True

    def make_conversation(self):
        if self.conversation_factory:
            return self.conversation_factory()
        config = self.config()
        if config["provider"] == "ollama":
            if not config["ollama_model"]:
                self.append("error", self.tr("Aucun modèle local choisi : ouvrez les réglages de l’assistant."))
                self.open_settings()
                return None
            if not A.is_local_url(config["ollama_url"]) and not self.remote_ollama_consent(config["ollama_url"]):
                return None
            return A.OllamaConversation(self.toolbox.execute, model=config["ollama_model"],
                                        base_url=config["ollama_url"], thinking=config["ollama_thinking"])
        if not self.consent():
            return None
        if self.client_factory:
            client = self.client_factory()
        else:
            try:
                import anthropic
            except ImportError:
                self.append("error", self.tr("Le composant Claude manque dans cette installation de SMFTP. "
                                             "Réinstallez SMFTP, ou choisissez un modèle local (Ollama) dans les réglages."))
                return None
            key = read_api_key() or os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                self.append("error", self.tr("Aucune clé API Claude : ajoutez-la dans les réglages, ou choisissez un "
                                             "modèle local (Ollama)."))
                self.open_settings()
                key = read_api_key()
                if not key or self.config()["provider"] != "claude":
                    return None
            client = anthropic.Anthropic(api_key=key, timeout=120.0, max_retries=2)
        return A.ClaudeConversation(client, self.toolbox.execute, model=config["claude_model"],
                                    effort=config["effort"], fast=config["fast"])

    def context(self):
        now = datetime.now()
        lines = [f"Date et heure locales : {DAYS[now.weekday()]} {now:%Y-%m-%d %H:%M}"]
        window = self.window
        if window.online and window.connection:
            lines.append(f"Serveur ouvert : {window.connection.name or window.connection.host} "
                         f"(dossier distant affiché : {window.remote.path})")
        lines.append(f"Dossier local affiché : {window.local.path}")
        # Saves the model a round trip (and minutes on a local model).
        profiles = []
        for site in window.sites:
            if site.get("protocol") == "SSH":
                continue  # Terminal profiles: no file tools.
            secret = "mot de passe mémorisé" if site.get("remember") else "mot de passe non mémorisé"
            profiles.append(f"{site.get('name') or site['host']} ({site['protocol']}, {site['host']}, {secret})")
        lines.append("Profils enregistrés : " + ("; ".join(profiles) if profiles else "aucun"))
        return "\n".join(lines)

    def send_or_stop(self):
        if self.busy():
            self.stop()
        else:
            self.send()

    def send(self):
        text = self.input.text().strip()
        if not text or self.busy():
            return
        if self.conversation is None:
            self.conversation = self.make_conversation()
            if self.conversation is None:
                return
        self.empty.hide()
        self.input.clear()
        self.append("user", text)
        self.steps = Steps()
        self.add_widget(self.steps, "left")
        self.worker = AssistantWorker(self.conversation, text, self.context(), self)
        self.worker.tool_started.connect(self.on_tool)
        self.worker.progress.connect(self.on_progress)
        self.worker.text_delta.connect(self.on_text)
        self.stream_bubble = None
        self.stream_text = ""
        self.worker.done.connect(self.on_done)
        self.worker.failed.connect(self.on_failed)
        self.worker.finished.connect(self.on_finished)
        self.worker.start()
        self.update_buttons()

    def on_progress(self, characters):
        if self.steps is not None:
            self.steps.set_progress(characters)

    stream_bubble = None
    stream_text = ""

    def on_text(self, piece):
        if self.stream_bubble is None:
            # The answer starts: the "thinking" line gives way to the text itself.
            if self.steps is not None:
                self.steps.finish()
                self.steps = None
            self.stream_text = ""
            self.stream_bubble = self.append("assistant", "")
            self.flush_timer = QTimer(self)
            self.flush_timer.setSingleShot(True)
            self.flush_timer.timeout.connect(self.flush_stream)
        self.stream_text += piece
        if not self.flush_timer.isActive():
            self.flush_timer.start(60)  # Redraw a few times per second, not per word.

    def flush_stream(self):
        if self.stream_bubble is not None:
            self.stream_bubble.label.setText(self.stream_text)

    def end_stream(self):
        if self.stream_bubble is not None:
            self.flush_timer.stop()
            self.flush_stream()
            if not self.stream_text.strip():
                self.stream_bubble.parentWidget().hide()
        self.stream_bubble = None

    def on_tool(self, name):
        if self.stream_bubble is not None:
            # Text before a tool call stays as its own message; steps follow it.
            self.end_stream()
        if self.steps is None:
            self.steps = Steps()
            self.add_widget(self.steps, "left")
        self.steps.add(self.tr(TOOL_LABELS.get(name, name)).capitalize())

    def on_done(self, result):
        if self.stream_bubble is not None and result.text:
            # The final answer was streamed: show the complete text once more (full Markdown).
            self.stream_text = result.text
            self.end_stream()
        elif result.text:
            self.end_stream()
            self.append("assistant", result.text)
        else:
            self.end_stream()
        notes = {"refusal": "L’assistant a refusé cette demande. Reformulez-la.",
                 "max_tokens": "Réponse interrompue : elle était trop longue.",
                 "rounds": "L’assistant s’est arrêté après trop d’étapes. Précisez la demande.",
                 "cancelled": "Arrêté à votre demande."}
        if result.stopped in notes:
            self.append("note", self.tr(notes[result.stopped]))
        elif not result.text:
            self.append("note", self.tr("Aucune réponse du modèle. Reformulez ou essayez un autre modèle."))

    def on_failed(self, message, settings_issue):
        self.end_stream()
        self.append("error", message)
        if settings_issue:
            self.conversation = None

    def on_finished(self):
        if self.steps is not None:
            self.steps.finish()
            self.steps = None
        self.update_buttons()
        self.input.setFocus()

    def stop(self):
        if self.busy():
            self.worker.requestInterruption()
            self.send_button.setEnabled(False)
            self.send_button.setToolTip(self.tr("Arrêt après l’étape en cours…"))

    def clear_messages(self):
        while self.messages.count() > 1:
            item = self.messages.takeAt(0)
            widget = item.widget()
            if widget is not None and widget is not self.empty:
                widget.hide()  # Hidden now; deleted when control returns to the event loop.
                widget.deleteLater()
        self.messages.insertWidget(0, self.empty)
        self.empty.show()

    def reset(self):
        if self.busy():
            return
        if self.conversation is not None:
            self.conversation.messages.clear()
        self.clear_messages()
        self.update_buttons()
        self.input.setFocus()
