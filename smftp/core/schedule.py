"""Scheduled tasks: validation, recurrences and storage (no Qt, no network).

A task copies files on its own at set times while SMFTP is open:

- ``sync``: copies new and modified files from one side to the other. Never deletes.
- ``upload`` / ``download``: sends or fetches one file or folder with a fixed
  conflict policy, since nobody is there to answer a question.

Credentials are never stored here: the task names a profile, whose secret stays
in the operating-system vault.
"""
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import tempfile
import uuid

KINDS = {"sync": "Synchroniser (nouveaux et modifiés)", "upload": "Envoyer", "download": "Télécharger"}
# Unattended conflict policies; "ask" is not possible without someone in front of the screen.
CONFLICTS = {"newer": "Remplacer si la source est plus récente", "different": "Remplacer si la taille diffère",
             "overwrite": "Toujours remplacer", "skip": "Ne jamais remplacer"}
DAYS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
MIN_INTERVAL, MAX_INTERVAL = 5, 7 * 24 * 60
HISTORY_LIMIT = 200
# A run due longer ago than this was missed (SMFTP was closed), not merely late.
MISSED_AFTER = timedelta(minutes=2)

RUNNING, SUCCESS, PARTIAL, FAILED, SKIPPED, INTERRUPTED = (
    "En cours", "Réussi", "Partiel", "Échoué", "Ignoré", "Interrompu")


def now() -> datetime:
    return datetime.now().replace(microsecond=0)


def parse_time(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(text) if text else None
    except (TypeError, ValueError):
        return None


def stamp(moment: datetime | None) -> str:
    return moment.replace(microsecond=0).isoformat(timespec="seconds") if moment else ""


def normalize_recurrence(value) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Récurrence invalide.")
    kind = value.get("type")
    if kind == "once":
        at = parse_time(value.get("at", ""))
        if at is None:
            raise ValueError("Indiquez la date et l’heure de l’exécution unique.")
        return {"type": "once", "at": stamp(at)}
    if kind == "interval":
        try:
            minutes = int(value.get("minutes", 0))
        except (TypeError, ValueError):
            raise ValueError("Intervalle invalide.") from None
        if not MIN_INTERVAL <= minutes <= MAX_INTERVAL:
            raise ValueError(f"L’intervalle doit être compris entre {MIN_INTERVAL} minutes et 7 jours.")
        return {"type": "interval", "minutes": minutes}
    if kind == "daily":
        text = str(value.get("time", ""))
        try:
            hour, minute = (int(part) for part in text.split(":"))
            if not (0 <= hour < 24 and 0 <= minute < 60):
                raise ValueError
        except ValueError:
            raise ValueError("Heure invalide : utilisez HH:MM.") from None
        days = value.get("days", range(7))
        if not isinstance(days, (list, tuple, range)):
            raise ValueError("Jours invalides.")
        days = sorted({int(d) for d in days if str(d).isdigit() and 0 <= int(d) < 7})
        if not days:
            raise ValueError("Choisissez au moins un jour.")
        return {"type": "daily", "time": f"{hour:02d}:{minute:02d}", "days": days}
    raise ValueError("Type de récurrence inconnu : once, interval ou daily.")


def normalize_task(task: dict, profiles: list[str] | None = None) -> dict:
    """Clean a task or raise ValueError with a message for the user.

    ``profiles`` lists the known profile identities; when given, the task must
    name one of them.
    """
    if not isinstance(task, dict):
        raise ValueError("La tâche doit être un objet.")
    # One line, no control characters: a name proposed by the assistant appears in
    # confirmation dialogs and must not be able to fake their content.
    name = " ".join("".join(c if c.isprintable() else " " for c in str(task.get("name", ""))).split())[:80]
    if not name:
        raise ValueError("Donnez un nom à la tâche.")
    kind = task.get("kind")
    if kind not in KINDS:
        raise ValueError("Action inconnue : sync, upload ou download.")
    profile = str(task.get("profile", "")).strip()
    if not profile:
        raise ValueError("Choisissez le profil de serveur.")
    if profiles is not None and profile not in profiles:
        raise ValueError("Ce profil de serveur n’existe plus.")
    local = str(task.get("local", "")).strip()
    remote = str(task.get("remote", "")).strip()
    if not local or not Path(local).is_absolute():
        raise ValueError("Indiquez un chemin local complet (ex. C:\\Site).")
    if not remote.startswith("/"):
        raise ValueError("Indiquez un chemin distant complet, commençant par « / ».")
    conflict = task.get("conflict", "newer")
    if conflict not in CONFLICTS:
        raise ValueError("Règle de remplacement inconnue.")
    clean = {
        "id": str(task.get("id") or uuid.uuid4().hex),
        "name": name,
        "enabled": bool(task.get("enabled", True)),
        "kind": kind,
        "profile": profile,
        "local": local,
        "remote": remote.rstrip("/") or "/",
        # Sync direction; for upload / download it follows from the kind.
        "upload": bool(task.get("upload", True)) if kind == "sync" else kind == "upload",
        "directory": bool(task.get("directory", False)),
        "compare_content": bool(task.get("compare_content", False)),
        "conflict": conflict,
        "recurrence": normalize_recurrence(task.get("recurrence")),
        "catch_up": bool(task.get("catch_up", True)),
        "last_run": stamp(parse_time(task.get("last_run", ""))),
        "next_run": stamp(parse_time(task.get("next_run", ""))),
    }
    return clean


def next_run(recurrence: dict, after: datetime, anchor: datetime | None = None) -> datetime | None:
    """First run strictly after ``after``. For intervals, ``anchor`` is the slot the
    following ones are counted from (the previous planned run)."""
    kind = recurrence["type"]
    if kind == "once":
        at = parse_time(recurrence["at"])
        return at if at and at > after else None
    if kind == "interval":
        step = timedelta(minutes=recurrence["minutes"])
        if anchor is None:
            return after + step
        moment = anchor + step
        if moment <= after:
            # Missed slots are not replayed one by one.
            missed = (after - moment) // step + 1
            moment += missed * step
        return moment
    hour, minute = (int(part) for part in recurrence["time"].split(":"))
    candidate = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= after:
        candidate += timedelta(days=1)
    for _ in range(8):
        if candidate.weekday() in recurrence["days"]:
            return candidate
        candidate += timedelta(days=1)
    return None


def describe(task: dict) -> str:
    """Recurrence in words, for tables and for the assistant."""
    recurrence = task["recurrence"]
    if recurrence["type"] == "once":
        at = parse_time(recurrence["at"])
        return f"Une fois, le {at:%d/%m/%Y à %H:%M}" if at else "Une fois"
    if recurrence["type"] == "interval":
        minutes = recurrence["minutes"]
        if minutes % 1440 == 0:
            return f"Tous les {minutes // 1440} jour(s)" if minutes > 1440 else "Toutes les 24 heures"
        if minutes % 60 == 0:
            return f"Toutes les {minutes // 60} heure(s)" if minutes > 60 else "Toutes les heures"
        return f"Toutes les {minutes} minutes"
    days = recurrence["days"]
    when = ("tous les jours" if len(days) == 7 else "en semaine" if days == [0, 1, 2, 3, 4]
            else "le week-end" if days == [5, 6] else ", ".join(DAYS[d] for d in days))
    return f"{when.capitalize()} à {recurrence['time']}"


def summary(task: dict) -> str:
    if task["kind"] == "sync":
        arrow = "→" if task["upload"] else "←"
        return f"Synchroniser {task['local']} {arrow} {task['remote']}"
    if task["kind"] == "upload":
        return f"Envoyer {task['local']} → {task['remote']}"
    return f"Télécharger {task['remote']} → {task['local']}"


def due(task: dict, moment: datetime) -> str | None:
    """"run", "missed" (catch-up disabled) or None."""
    if not task["enabled"]:
        return None
    scheduled = parse_time(task["next_run"])
    if scheduled is None or scheduled > moment:
        return None
    if moment - scheduled > MISSED_AFTER and not task["catch_up"]:
        return "missed"
    return "run"


def advance(task: dict, moment: datetime, ran: bool) -> None:
    """Record a run (or a missed slot) and plan the next one."""
    if ran:
        task["last_run"] = stamp(moment)
    recurrence = task["recurrence"]
    if recurrence["type"] == "once":
        task["enabled"] = False
        task["next_run"] = ""
        return
    # Interval slots stay aligned on the planned time, so a late run does not drift.
    anchor = parse_time(task["next_run"]) or moment
    task["next_run"] = stamp(next_run(recurrence, moment, anchor if recurrence["type"] == "interval" else None))


def plan_first_run(task: dict, moment: datetime) -> None:
    """Set the first ``next_run`` of a new or edited task."""
    recurrence = task["recurrence"]
    if recurrence["type"] == "once":
        task["next_run"] = recurrence["at"]
    else:
        task["next_run"] = stamp(next_run(recurrence, moment))


class ScheduleStore:
    """schedule.json: tasks and run history, written atomically."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.tasks: list[dict] = []
        self.history: list[dict] = []

    def load(self) -> list[str]:
        """Load what can be read; return messages about entries that were dropped."""
        problems = []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return problems
        except (OSError, ValueError):
            broken = self.path.with_name(f"schedule.illisible-{now():%Y%m%d-%H%M%S}.json")
            try:
                self.path.replace(broken)
                problems.append(f"Fichier des tâches planifiées illisible : mis de côté sous {broken.name}.")
            except OSError:
                problems.append("Fichier des tâches planifiées illisible.")
            return problems
        if not isinstance(raw, dict):
            raw = {}
            problems.append("Fichier des tâches planifiées d’un format inconnu : aucune tâche chargée.")
        tasks = raw.get("tasks", [])
        if not isinstance(tasks, list):
            tasks = []
            problems.append("Liste des tâches planifiées illisible : aucune tâche chargée.")
        for task in tasks:
            try:
                self.tasks.append(normalize_task(task))
            except (TypeError, ValueError, AttributeError) as error:
                label = str(task.get("name", "?")) if isinstance(task, dict) else "?"
                detail = error if isinstance(error, ValueError) else "format inattendu"
                problems.append(f"Tâche ignorée ({label}) : {detail}")
        history = raw.get("history", [])
        if not isinstance(history, list):
            history = []
        simple = (str, int, float, bool, type(None))
        self.history = [row for row in history if isinstance(row, dict) and
                        all(isinstance(value, simple) for value in row.values())][-HISTORY_LIMIT:]
        if problems:
            # The next save drops what could not be read: keep a copy of the original.
            import shutil
            try:
                shutil.copy2(self.path, self.path.with_name(f"schedule.illisible-{now():%Y%m%d-%H%M%S}.json"))
            except OSError:
                pass
        # Runs that were in progress when SMFTP closed.
        for row in self.history:
            if row.get("status") == RUNNING:
                row["status"] = INTERRUPTED
                row["detail"] = "SMFTP a été fermé pendant l’exécution."
        return problems

    def save(self) -> None:
        self.history = self.history[-HISTORY_LIMIT:]
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix="schedule-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({"tasks": self.tasks, "history": self.history}, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()

    def find(self, task_id: str) -> dict | None:
        return next((task for task in self.tasks if task["id"] == task_id), None)
