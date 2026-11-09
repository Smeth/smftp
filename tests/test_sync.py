from smftp.workers.sync import SyncPlanner
from smftp.models.entries import Connection
from test_clients import ftp_server


def test_recursive_sync_plan(ftp_server, tmp_path):
    port, root = ftp_server
    local = tmp_path / "source"
    local.mkdir()
    (local / "new.txt").write_text("new")
    (local / "nested").mkdir()
    (local / "nested" / "child.txt").write_text("child")
    plans = []
    worker = SyncPlanner(Connection("127.0.0.1", "test", "FTP", port, "secret"), str(local), "/", True)
    worker.planned.connect(plans.append)
    worker.run()
    assert {row[0] for row in plans[0]} == {"new.txt", "nested/child.txt"}
