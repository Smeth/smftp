"""Scheduled tasks: recurrences, storage, and real runs against a local FTP server."""
from datetime import datetime, timedelta
import json
import time
import pytest
from PySide6.QtWidgets import QApplication
from smftp.core import schedule as S
from test_clients import ftp_server  # noqa: F401  (fixture)
from test_audit_known_issues import close_window

MONDAY = datetime(2026, 11, 16, 10, 0)  # A Monday.


def task(**changes):
    base = dict(name="Nuit", kind="sync", profile="FTP://test@127.0.0.1:21", local="C:\\Site", remote="/www",
                recurrence={"type": "daily", "time": "23:00", "days": list(range(7))})
    base.update(changes)
    return S.normalize_task(base)


# ---------- recurrences ----------

def test_daily_next_run_respects_time_and_days():
    weekdays = {"type": "daily", "time": "23:00", "days": [0, 1, 2, 3, 4]}
    assert S.next_run(weekdays, MONDAY) == datetime(2026, 11, 16, 23, 0)
    assert S.next_run(weekdays, datetime(2026, 11, 16, 23, 0)) == datetime(2026, 11, 17, 23, 0)
    friday_night = datetime(2026, 11, 20, 23, 30)
    assert S.next_run(weekdays, friday_night) == datetime(2026, 11, 23, 23, 0)  # Skips the week-end.


def test_interval_stays_aligned_and_skips_missed_slots():
    every_hour = {"type": "interval", "minutes": 60}
    assert S.next_run(every_hour, MONDAY) == MONDAY + timedelta(hours=1)
    anchor = datetime(2026, 11, 16, 8, 0)
    # Three slots missed: the next one is the first in the future, not a burst of runs.
    assert S.next_run(every_hour, datetime(2026, 11, 16, 11, 20), anchor) == datetime(2026, 11, 16, 12, 0)


def test_once_runs_a_single_time_then_disables():
    at = MONDAY + timedelta(hours=2)
    item = task(recurrence={"type": "once", "at": at.isoformat()})
    S.plan_first_run(item, MONDAY)
    assert S.due(item, MONDAY) is None and S.due(item, at) == "run"
    S.advance(item, at, ran=True)
    assert not item["enabled"] and item["next_run"] == "" and S.due(item, at + timedelta(days=1)) is None


def test_missed_runs_follow_the_catch_up_option():
    item = task(recurrence={"type": "interval", "minutes": 60})
    item["next_run"] = S.stamp(MONDAY)
    late = MONDAY + timedelta(hours=5, minutes=10)
    assert S.due(item, MONDAY + timedelta(seconds=30)) == "run"  # Merely late: runs.
    assert S.due(item, late) == "run"  # Catch-up enabled by default.
    item["catch_up"] = False
    assert S.due(item, late) == "missed"
    S.advance(item, late, ran=False)
    assert item["next_run"] == S.stamp(MONDAY + timedelta(hours=6)) and item["last_run"] == ""


@pytest.mark.parametrize("changes, message", [
    (dict(name=" "), "nom"), (dict(kind="delete"), "Action"), (dict(local="relatif"), "local"),
    (dict(remote="www"), "distant"), (dict(conflict="ask"), "remplacement"),
    (dict(recurrence={"type": "interval", "minutes": 1}), "intervalle"),
    (dict(recurrence={"type": "daily", "time": "25:00", "days": [0]}), "Heure"),
    (dict(recurrence={"type": "daily", "time": "08:00", "days": []}), "jour"),
])
def test_invalid_tasks_are_explained(changes, message):
    with pytest.raises(ValueError, match=message):
        task(**changes)


def test_unknown_profile_is_refused():
    with pytest.raises(ValueError, match="profil"):
        S.normalize_task(dict(name="x", kind="sync", profile="FTP://a@b:21", local="C:\\x", remote="/",
                              recurrence={"type": "interval", "minutes": 60}), profiles=[])


def test_descriptions():
    assert S.describe(task()) == "Tous les jours à 23:00"
    assert S.describe(task(recurrence={"type": "daily", "time": "07:30", "days": [0, 1, 2, 3, 4]})) == "En semaine à 07:30"
    assert S.describe(task(recurrence={"type": "interval", "minutes": 120})) == "Toutes les 2 heure(s)"
    assert S.summary(task(upload=False)) == "Synchroniser C:\\Site ← /www"


def test_store_round_trip_and_interrupted_runs(tmp_path):
    store = S.ScheduleStore(tmp_path / "schedule.json")
    store.tasks.append(task())
    store.history.append({"task_id": "x", "name": "Nuit", "status": S.RUNNING, "started": S.stamp(MONDAY)})
    store.save()
    again = S.ScheduleStore(tmp_path / "schedule.json")
    assert again.load() == []
    assert again.tasks[0]["name"] == "Nuit" and again.history[0]["status"] == S.INTERRUPTED


def test_unreadable_store_is_set_aside(tmp_path):
    path = tmp_path / "schedule.json"
    path.write_text("{broken", encoding="utf-8")
    store = S.ScheduleStore(path)
    assert "illisible" in store.load()[0]
    assert not path.exists() and list(tmp_path.glob("schedule.illisible-*.json"))


# ---------- real runs ----------

def wait_for(condition, seconds=15):
    app = QApplication.instance()
    end = time.monotonic() + seconds
    while not condition() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)
    assert condition()


@pytest.fixture
def window_with_profile(ftp_server, monkeypatch):
    QApplication.instance() or QApplication([])
    import smftp.utils.profiles as profiles
    from smftp.ui.main_window import MainWindow
    port, root = ftp_server
    secrets = {}
    monkeypatch.setattr(profiles, "read_credential", lambda key: secrets.get(key))
    window = MainWindow()
    site = dict(host="127.0.0.1", user="test", protocol="FTP", port=port, name="Local", remember=True)
    window.sites = [profiles.normalize_profile(site)]
    key = profiles.identity(site)
    secrets[key] = "secret"
    yield window, key, root, secrets
    close_window(window)


def last_status(scheduler):
    return scheduler.store.history[-1]["status"] if scheduler.store.history else None


def test_scheduled_sync_uploads_new_files_then_reports_up_to_date(window_with_profile, tmp_path):
    window, key, root, _ = window_with_profile
    source = tmp_path / "site"
    (source / "css").mkdir(parents=True)
    (source / "index.html").write_text("<h1>bonjour</h1>", encoding="utf-8")
    (source / "css" / "style.css").write_text("body{}", encoding="utf-8")
    (root / "www").mkdir()
    scheduler = window.scheduler
    item = scheduler.add_task(dict(name="Publication", kind="sync", profile=key, local=str(source), remote="/www",
                                   upload=True, recurrence={"type": "interval", "minutes": 60}))
    assert S.parse_time(item["next_run"]) > S.now()
    scheduler.run_now(item["id"])
    wait_for(lambda: last_status(scheduler) not in (None, S.RUNNING))
    assert last_status(scheduler) == S.SUCCESS, scheduler.store.history[-1]
    assert (root / "www" / "index.html").read_text(encoding="utf-8") == "<h1>bonjour</h1>"
    assert (root / "www" / "css" / "style.css").exists()
    scheduler.run_now(item["id"])
    wait_for(lambda: len(scheduler.store.history) == 2 and last_status(scheduler) != S.RUNNING)
    assert "Déjà à jour" in scheduler.store.history[-1]["detail"]
    saved = json.loads(scheduler.store.path.read_text(encoding="utf-8"))
    assert saved["tasks"][0]["name"] == "Publication" and "secret" not in scheduler.store.path.read_text(encoding="utf-8")


def test_due_task_fires_on_tick_and_download_uses_conflict_policy(window_with_profile, tmp_path):
    window, key, root, _ = window_with_profile
    (root / "rapport.txt").write_text("v2", encoding="utf-8")
    target = tmp_path / "rapport.txt"
    target.write_text("local", encoding="utf-8")
    scheduler = window.scheduler
    item = scheduler.add_task(dict(name="Rapport", kind="download", profile=key, local=str(target),
                                   remote="/rapport.txt", conflict="skip",
                                   recurrence={"type": "interval", "minutes": 30}))
    item["next_run"] = S.stamp(S.now() - timedelta(seconds=5))
    scheduler.tick()
    assert S.parse_time(item["next_run"]) > S.now() and item["last_run"]
    wait_for(lambda: last_status(scheduler) not in (None, S.RUNNING))
    assert last_status(scheduler) == S.SUCCESS
    assert target.read_text(encoding="utf-8") == "local"  # "Never replace" kept the local file.


def test_missing_password_fails_clearly(window_with_profile, tmp_path):
    window, key, _, secrets = window_with_profile
    secrets.clear()
    folder = tmp_path / "x"
    folder.mkdir()
    scheduler = window.scheduler
    item = scheduler.add_task(dict(name="Sans secret", kind="sync", profile=key, local=str(folder), remote="/",
                                   recurrence={"type": "interval", "minutes": 60}))
    scheduler.run_now(item["id"])
    assert last_status(scheduler) == S.FAILED
    assert "Mémoriser" in scheduler.store.history[-1]["detail"]


def test_dialogs_open_and_list_tasks(window_with_profile, tmp_path):
    from smftp.ui.scheduler import ScheduleDialog, TaskEditor
    window, key, _, _ = window_with_profile
    window.scheduler.add_task(dict(name="Nuit", kind="sync", profile=key, local=str(tmp_path), remote="/",
                                   recurrence={"type": "daily", "time": "23:00", "days": list(range(7))}))
    dialog = ScheduleDialog(window.scheduler, window)
    assert dialog.table.topLevelItemCount() == 1 and not dialog.empty.isVisible()
    assert not dialog.run_button.isEnabled()  # Nothing selected.
    dialog.table.topLevelItem(0).setSelected(True)
    assert dialog.run_button.isEnabled() and dialog.toggle_button.toolTip() == "Désactiver"
    dialog.toggle()
    assert not window.scheduler.store.tasks[0]["enabled"] and dialog.toggle_button.toolTip() == "Activer"
    editor = TaskEditor(window.scheduler, window, defaults={"profile": key, "local": str(tmp_path)})
    assert not editor.ok_button.isEnabled()
    editor.name.setText("Envoi")
    editor.remote.setText("/www")
    assert editor.ok_button.isEnabled(), editor.error.text()
    editor.kind.setCurrentIndex(editor.kind.findData("upload"))
    assert editor.conflict.isVisibleTo(editor) and not editor.direction.isVisibleTo(editor)
