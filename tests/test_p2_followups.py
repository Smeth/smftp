"""Retry keeps follow-up actions; disconnect status; single instance; queue quarantine."""
import json
from PySide6.QtWidgets import QApplication, QMessageBox
from smftp.models.entries import Connection
from smftp.ui.main_window import MainWindow
from smftp.workers.transfer import Transfer
from test_audit_known_issues import close_window


def test_retry_moves_follow_up_to_new_task(tmp_path):
    QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        window.transfers.pause_all()
        connection = Connection("audit.invalid", "audit")
        task = Transfer(connection, str(tmp_path / "f.txt"), "/f.txt", True)
        record = dict(remote="/f.txt", local=task.local, connection=connection, baseline=None)
        window.transfers.add(task, transient=True)
        window.edit_uploads[id(task)] = record
        window.transfers.done(window.transfers.records[0], "Échoué")
        assert id(task) not in window.edit_uploads and id(task) in window.parked
        window.transfers.tables[1].topLevelItem(0).setSelected(True)
        window.transfers.tabs.setCurrentIndex(1)
        window.transfers.retry()
        new = window.transfers.records[-1]["task"]
        assert new is not task and window.edit_uploads.get(id(new)) is record
        assert not window.parked
    finally:
        close_window(window)


def test_clearing_failed_jobs_drops_parked_follow_ups(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    window = MainWindow()
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    try:
        window.transfers.pause_all()
        task = Transfer(Connection("audit.invalid", "audit"), str(tmp_path / "f"), "/f", False)
        window.transfers.add(task, transient=True)
        window.drag_downloads[id(task)] = ("id", "/f", task.local, (1, 1))
        window.transfers.done(window.transfers.records[0], "Échoué")
        window.transfers.clear_failed()
        assert not window.parked and not window.transfers.records
    finally:
        close_window(window)


def test_disconnected_status_mentions_running_transfers(tmp_path):
    QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        window.transfers.pause_all()
        window.transfers.add(Transfer(Connection("audit.invalid", "audit"), str(tmp_path / "f"), "/f", True))
        window.transfers.records[0]["state"] = "pending"
        window.on_disconnected()
        assert "1 transfert" in window.statusBar().currentMessage()
    finally:
        window.transfers.records[0]["state"] = "paused_pending"
        close_window(window)


def test_second_instance_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("SMFTP_DATA_DIR", str(tmp_path))
    from smftp.utils.storage import acquire_instance_lock
    first, ok = acquire_instance_lock()
    assert ok and first is not None
    second, ok2 = acquire_instance_lock()
    assert not ok2 and second is None
    first.unlock()
    third, ok3 = acquire_instance_lock()
    assert ok3
    third.unlock()


def test_unreadable_queue_is_set_aside(tmp_path, monkeypatch):
    monkeypatch.setenv("SMFTP_DATA_DIR", str(tmp_path))
    QApplication.instance() or QApplication([])
    from smftp.ui.transfers import Transfers
    (tmp_path / "queue.json").write_text("{not json", encoding="utf-8")
    messages = []
    transfers = Transfers()
    transfers.activity.connect(messages.append)
    transfers.restore()
    kept = list(tmp_path.glob("queue.illisible-*.json"))
    assert len(kept) == 1 and kept[0].read_text(encoding="utf-8") == "{not json"
    assert not (tmp_path / "queue.json").exists()
    assert "mis de côté" in messages[-1]
