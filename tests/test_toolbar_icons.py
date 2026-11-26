"""Icon toolbars: transfer controls follow the queue state; the journal can be cleared."""
from PySide6.QtWidgets import QApplication
from smftp.models.entries import Connection
from smftp.ui.main_window import MainWindow
from smftp.workers.transfer import Transfer
from test_audit_known_issues import close_window


def controls(transfers):
    return [transfers.pause_button, transfers.cancel_button, transfers.retry_button,
            transfers.pause_all_button, transfers.stop_all_button, transfers.clear_button]


def test_transfer_controls_disabled_when_queue_empty():
    QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        transfers = window.transfers
        assert not any(button.isEnabled() for button in controls(transfers))
        assert all(button.icon() and not button.icon().isNull() and button.toolTip() for button in controls(transfers))
        for index in (1, 2):
            transfers.tabs.setCurrentIndex(index)
            assert not transfers.clear_button.isEnabled()
    finally:
        close_window(window)


def test_transfer_controls_follow_selection_and_state(tmp_path):
    QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        transfers = window.transfers
        transfers.pause_all()  # Never contact the fake server.
        task = Transfer(Connection("audit.invalid", "audit"), str(tmp_path / "f.txt"), "/f.txt", True)
        record = transfers.add(task, transient=True)
        assert transfers.pause_all_button.isEnabled() and transfers.stop_all_button.isEnabled()
        assert transfers.pause_all_button.property("iconName") == "play"  # Queue is paused: offers resume.
        assert not transfers.cancel_button.isEnabled()  # Nothing selected yet.
        record["item"].setSelected(True)
        assert transfers.cancel_button.isEnabled() and transfers.pause_button.isEnabled()
        assert not transfers.retry_button.isEnabled()
        transfers.done(record, "Échoué")
        assert not transfers.pause_all_button.isEnabled() and not transfers.stop_all_button.isEnabled()
        transfers.tabs.setCurrentIndex(1)
        assert transfers.clear_button.isEnabled()
        assert "(1)" in transfers.tabs.tabText(1)
        record["item"].setSelected(True)
        assert transfers.retry_button.isEnabled() and not transfers.cancel_button.isEnabled()
        transfers.clear_current()
        assert transfers.tables[1].topLevelItemCount() == 0 and not transfers.clear_button.isEnabled()
    finally:
        close_window(window)


def test_journal_can_be_cleared_from_its_toolbar():
    QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        assert window.logs.toPlainText() and window.log_clear_button.isEnabled()
        window.log_clear_button.click()
        assert window.logs.toPlainText() == ""
        assert not window.log_clear_button.isEnabled() and not window.log_copy_button.isEnabled()
        window.log("Nouveau message")
        assert window.log_copy_button.isEnabled()
        window.copy_log()
        assert "Nouveau message" in QApplication.clipboard().text()
        window.toggle_theme()  # Icons are redrawn for the other theme without errors.
        assert window.log_clear_button.property("iconDark") is False or window.dark
    finally:
        close_window(window)
