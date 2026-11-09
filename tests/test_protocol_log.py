from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEventLoop, QTimer
from smftp.core.clients import FTPClient
from smftp.models.entries import Connection
from smftp.workers.session import Session
from smftp.ui.main_window import MainWindow
from test_clients import ftp_server


def test_ftp_logs_commands_and_replies_without_secret(ftp_server):
    port, _ = ftp_server
    messages = []
    client = FTPClient(messages.append)
    try:
        client.connect(Connection("127.0.0.1", "test", "FTP", port, "secret"))
        client.listdir("/")
    finally:
        client.close()
    text = "\n".join(messages)
    assert "Commande : USER test" in text
    assert "Commande : PASS [masqué]" in text
    assert "Réponse : 220" in text
    assert "Réponse : 230" in text
    assert "MLSD" in text
    assert "secret" not in text
    assert messages.index(next(m for m in messages if "220" in m)) < messages.index("Commande : USER test")


def test_ftp_failure_logged_before_failure_signal(ftp_server, tmp_path, monkeypatch):
    import smftp.workers.session as module
    monkeypatch.setattr(module, "data_dir", lambda: tmp_path)
    port, _ = ftp_server
    events = []
    session = Session()
    session.progress.connect(lambda text: events.append(("progress", text)))
    session.failed.connect(lambda error: events.append(("failed", error.summary)))
    session.connect_to(Connection("127.0.0.1", "test", "FTP", port, "incorrect-secret"))
    assert events[-1][0] == "failed"
    assert any(kind == "progress" and "Réponse : 530" in text for kind, text in events)
    assert any(kind == "progress" and text.startswith("Erreur :") for kind, text in events)
    assert "incorrect-secret" not in str(events)


def test_gui_receives_progress_while_connection_worker_is_busy(monkeypatch):
    import smftp.workers.session as module
    import threading
    app = QApplication.instance() or QApplication([])
    entered, release = threading.Event(), threading.Event()
    class WaitingFTP(FTPClient):
        def connect(self, connection):
            self.secrets = (connection.password,)
            self.report("Statut : connexion en cours…")
            entered.set()
            if not release.wait(3):
                raise TimeoutError
            return "/"
        def listdir(self, path):
            return []
    monkeypatch.setattr(module, "FTPClient", WaitingFTP)
    window = MainWindow()
    window.connection = Connection("test.local", "alice", "FTP", 21, "private")
    window.activity_tabs.setCurrentWidget(window.transfers)
    window.start_connection()
    loop = QEventLoop()
    observed = []
    def check():
        if entered.is_set() and "Statut : connexion en cours" in window.logs.toPlainText():
            observed.append(window.busy and not window.online)
            loop.quit()
    timer = QTimer()
    timer.timeout.connect(check)
    timer.start(10)
    QTimer.singleShot(2000, loop.quit)
    loop.exec()
    timer.stop()
    try:
        assert observed == [True]
        assert window.activity_tabs.currentWidget() is window.logs
        from smftp.core.errors import ErrorInfo
        window.on_session_error(ErrorInfo("Connexion refusée", "cause"))
        assert "Connexion refusée" in window.statusBar().currentMessage()
    finally:
        release.set()
        window.close()
        closing = QEventLoop()
        window.thread.finished.connect(closing.quit)
        QTimer.singleShot(4000, closing.quit)
        closing.exec()
